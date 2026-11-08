from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import pytest
from sqlalchemy import select

from chatterlore.db.models import BotEvent, Embedding, KnowledgeItem, Message
from chatterlore.db.repos.embedding_indexes import EmbeddingIndexRepository
from chatterlore.db.repos.embeddings import EmbeddingRepository
from chatterlore.db.repos.shared import utc_now
from chatterlore.db.session import session_scope
from chatterlore.embedding_adoption import LegacyAdoptionManifest
from chatterlore.embedding_identity import embedding_configuration, embedding_space_metadata
from chatterlore.memory.scope import Scope


async def legacy_manifest(context):
    profile = context.memory.provider_registry.profile_by_name("default")
    manifest = {
        "scope": "-100555:3",
        "provider_profile": "default",
        "model": profile.embedding_model,
        "dimension": 64,
        "original_config_hash": embedding_configuration(profile, profile.embedding_model)[
            "config_hash"
        ],
        "entries": [],
    }
    async with session_scope(context.session_factory) as db:
        for text, value in [("PRIVATE_ALPHA_SOURCE", 0.25), ("PRIVATE_BETA_SOURCE", 0.75)]:
            source = KnowledgeItem(
                chat_id=-100555,
                message_thread_id=3,
                kind="note",
                content=text,
                source_message_ids=[],
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(source)
            await db.flush()
            row = await EmbeddingRepository(db).add(
                target_type="knowledge_item",
                target_id=source.id,
                scope=Scope(-100555, 3),
                provider="fake",
                model=profile.embedding_model,
                vector=[value] * 64,
            )
            manifest["entries"].append(
                {
                    "embedding_id": row.id,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "vector_sha256": hashlib.sha256(row.vector).hexdigest(),
                }
            )
    return manifest


async def assert_unadopted(context):
    async with session_scope(context.session_factory) as db:
        rows = list((await db.scalars(select(Embedding))).all())
        assert all(
            row.space_id is None and row.provenance_status == "legacy_unverified" for row in rows
        )
        events = list(
            (
                await db.scalars(
                    select(BotEvent).where(BotEvent.event_type == "embedding_index.legacy_adopted")
                )
            ).all()
        )
        assert events == []


async def test_explicit_adoption_preserves_bytes_and_establishes_current_coverage_without_inference(
    context, monkeypatch
):
    manifest = await legacy_manifest(context)
    service = context.memory.embedding_indexes

    def no_provider(*args, **kwargs):
        raise AssertionError("adoption must not construct or call a provider")

    monkeypatch.setattr(service.registry, "select", no_provider)
    preview = await service.preview_legacy_adoption(manifest, actor_user_id=111)
    assert preview["count"] == 2
    await assert_unadopted(context)
    result = await service.adopt_legacy(
        manifest,
        preview_hash=preview["preview_hash"],
        confirmation=f"ADOPT {preview['preview_hash']}",
        provenance_attested=True,
        actor_user_id=111,
    )
    assert result["adopted"] == 2
    async with session_scope(context.session_factory) as db:
        rows = list((await db.scalars(select(Embedding).order_by(Embedding.id))).all())
        assert [hashlib.sha256(row.vector).hexdigest() for row in rows] == [
            entry["vector_sha256"] for entry in manifest["entries"]
        ]
        assert all(
            row.provenance_status == "verified" and row.space_id == result["space"]["id"]
            for row in rows
        )
        assert (
            len(
                await EmbeddingRepository(db).list_for_retrieval(
                    Scope(-100555, 3), space_id=result["space"]["id"]
                )
            )
            == 2
        )
        coverage = await EmbeddingIndexRepository(db).coverage(
            Scope(-100555, 3), result["space"]["id"]
        )
        assert (coverage["eligible"], coverage["indexed"], coverage["missing"]) == (2, 2, 0)
        [event] = list(
            (
                await db.scalars(
                    select(BotEvent).where(BotEvent.event_type == "embedding_index.legacy_adopted")
                )
            ).all()
        )
        assert event.user_id == 111 and event.payload["count"] == 2
        assert "PRIVATE_" not in str(event.payload)


@pytest.mark.parametrize("change", ["source", "vector", "delete", "epoch", "profile"])
async def test_adoption_revalidates_preview_and_rolls_back_the_whole_batch(
    context, change, monkeypatch
):
    manifest = await legacy_manifest(context)
    service = context.memory.embedding_indexes
    preview = await service.preview_legacy_adoption(manifest, actor_user_id=111)
    async with session_scope(context.session_factory) as db:
        second = await db.get(Embedding, manifest["entries"][1]["embedding_id"])
        assert second is not None
        source = await db.get(KnowledgeItem, second.target_id)
        assert source is not None
        if change == "source":
            source.content = "EDITED_PRIVATE_SOURCE"
        elif change == "vector":
            second.vector = second.vector[:-4] + b"\0\0\0\0"
        elif change == "delete":
            await db.delete(source)
        elif change == "epoch":
            await EmbeddingIndexRepository(db).advance_epoch()
    if change == "profile":
        profile = service.registry.profile_by_name("default")
        monkey_profile = replace(profile, redact_before_ai=not profile.redact_before_ai)
        monkeypatch.setattr(service.registry, "profile_by_name", lambda _name: monkey_profile)
    with pytest.raises(ValueError):
        await service.adopt_legacy(
            manifest,
            preview_hash=preview["preview_hash"],
            confirmation=f"ADOPT {preview['preview_hash']}",
            provenance_attested=True,
            actor_user_id=111,
        )
    await assert_unadopted(context)


@pytest.mark.parametrize("change", ["config", "source", "vector", "scope", "duplicate", "oversize"])
async def test_adoption_does_not_infer_missing_or_incompatible_original_provenance(context, change):
    manifest = await legacy_manifest(context)
    if change == "config":
        manifest["original_config_hash"] = "0" * 64
    elif change == "source":
        manifest["entries"][1]["source_sha256"] = "0" * 64
    elif change == "vector":
        manifest["entries"][1]["vector_sha256"] = "0" * 64
    elif change == "scope":
        manifest["scope"] = "-100555:5"
    elif change == "duplicate":
        manifest["entries"][1] = copy.deepcopy(manifest["entries"][0])
    else:
        manifest["entries"] *= 51
    with pytest.raises(ValueError):
        await context.memory.embedding_indexes.preview_legacy_adoption(manifest, actor_user_id=111)
    await assert_unadopted(context)


@pytest.mark.parametrize(
    "attested,actor,confirmation",
    [(False, 111, "correct"), (True, 222, "correct"), (True, 111, "ADOPT all")],
)
async def test_adoption_requires_attestation_exact_confirmation_and_the_preview_actor(
    context, attested, actor, confirmation
):
    manifest = await legacy_manifest(context)
    service = context.memory.embedding_indexes
    preview = await service.preview_legacy_adoption(manifest, actor_user_id=111)
    with pytest.raises(ValueError):
        await service.adopt_legacy(
            manifest,
            preview_hash=preview["preview_hash"],
            confirmation=f"ADOPT {preview['preview_hash']}"
            if confirmation == "correct"
            else confirmation,
            provenance_attested=attested,
            actor_user_id=actor,
        )
    await assert_unadopted(context)


async def test_adoption_never_overwrites_an_existing_verified_target(context):
    manifest = await legacy_manifest(context)
    selection = context.memory.provider_registry.select()
    async with session_scope(context.session_factory) as db:
        legacy = await db.get(Embedding, manifest["entries"][0]["embedding_id"])
        assert legacy is not None
        current = await EmbeddingRepository(db).add(
            target_type=legacy.target_type,
            target_id=legacy.target_id,
            scope=Scope(-100555, 3),
            provider="fake",
            model=selection.embedding_model,
            vector=[9.0] * 64,
            space=embedding_space_metadata(selection, 64),
            source_hash=manifest["entries"][0]["source_sha256"],
        )
        current_id, original_bytes = current.id, current.vector
    with pytest.raises(ValueError):
        await context.memory.embedding_indexes.preview_legacy_adoption(manifest, actor_user_id=111)
    async with session_scope(context.session_factory) as db:
        current = await db.get(Embedding, current_id)
        assert current is not None and current.vector == original_bytes


async def test_adoption_rejects_dimensions_inconsistent_with_known_original_configuration(context):
    manifest = await legacy_manifest(context)
    manifest["dimension"] = 63
    async with session_scope(context.session_factory) as db:
        for evidence in manifest["entries"]:
            row = await db.get(Embedding, evidence["embedding_id"])
            assert row is not None
            row.vector = row.vector[:-4]
            row.dimension = 63
            evidence["vector_sha256"] = hashlib.sha256(row.vector).hexdigest()
    with pytest.raises(ValueError, match="embedding_adoption_provenance_invalid"):
        await context.memory.embedding_indexes.preview_legacy_adoption(manifest)
    await assert_unadopted(context)


@pytest.mark.parametrize("learned", [True, False])
async def test_adoption_uses_normalized_provenance_and_requires_learned_messages(context, learned):
    profile = context.memory.provider_registry.profile_by_name("default")
    normalized = "Normalized source with a distinct fingerprint"
    async with session_scope(context.session_factory) as db:
        message = Message(
            telegram_message_id=19,
            chat_id=-100555,
            message_thread_id=3,
            text="Different display text",
            normalized_text=normalized,
            learned=learned,
            created_at=utc_now(),
            ingested_at=utc_now(),
        )
        db.add(message)
        await db.flush()
        row = await EmbeddingRepository(db).add(
            target_type="message",
            target_id=message.id,
            scope=Scope(-100555, 3),
            provider="fake",
            model=profile.embedding_model,
            vector=[0.5] * 64,
        )
        manifest = {
            "scope": "-100555:3",
            "provider_profile": "default",
            "model": profile.embedding_model,
            "dimension": 64,
            "original_config_hash": embedding_configuration(profile, profile.embedding_model)[
                "config_hash"
            ],
            "entries": [
                {
                    "embedding_id": row.id,
                    "source_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
                    "vector_sha256": hashlib.sha256(row.vector).hexdigest(),
                }
            ],
        }
    service = context.memory.embedding_indexes
    if not learned:
        with pytest.raises(ValueError, match="embedding_adoption_source_changed"):
            await service.preview_legacy_adoption(manifest)
        return
    preview = await service.preview_legacy_adoption(manifest)
    assert (
        await service.adopt_legacy(
            manifest,
            preview_hash=preview["preview_hash"],
            confirmation=f"ADOPT {preview['preview_hash']}",
            provenance_attested=True,
        )
    )["adopted"] == 1


@pytest.mark.parametrize("bad_bytes", [b"bad", b"\x00\x00\xc0\x7f"])
async def test_adoption_rejects_malformed_or_nonfinite_vectors_despite_matching_byte_evidence(
    context, bad_bytes
):
    manifest = await legacy_manifest(context)
    async with session_scope(context.session_factory) as db:
        row = await db.get(Embedding, manifest["entries"][0]["embedding_id"])
        assert row is not None
        row.vector = bad_bytes if len(bad_bytes) == 3 else bad_bytes + row.vector[4:]
        manifest["entries"][0]["vector_sha256"] = hashlib.sha256(row.vector).hexdigest()
    with pytest.raises(ValueError, match="embedding_adoption_vector_invalid"):
        await context.memory.embedding_indexes.preview_legacy_adoption(manifest)
    await assert_unadopted(context)


def test_manifest_mapping_bound_is_independent_of_duplicate_ids():
    base = {
        "scope": "all",
        "provider_profile": "default",
        "model": "known-model",
        "dimension": 64,
        "original_config_hash": "a" * 64,
    }
    entries = [
        {"embedding_id": index, "source_sha256": "b" * 64, "vector_sha256": "c" * 64}
        for index in range(1, 102)
    ]
    assert len(LegacyAdoptionManifest.parse({**base, "entries": entries[:100]}).entries) == 100
    for invalid in [entries, [], [{**entries[0], "embedding_id": True}]]:
        with pytest.raises(ValueError):
            LegacyAdoptionManifest.parse({**base, "entries": invalid})
