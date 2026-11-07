from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import delete, select

from pzp_ai_bot.db.models import Embedding, KnowledgeItem
from pzp_ai_bot.db.repos.embeddings import EmbeddingRepository
from pzp_ai_bot.db.repos.shared import utc_now
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.scope import Scope


def space(selection, dimension=64):
    from pzp_ai_bot.embedding_identity import embedding_space_metadata

    return embedding_space_metadata(selection, dimension)


async def add_note(context, text="durable private source", scope=Scope(-100555)):
    async with session_scope(context.session_factory) as session:
        row = KnowledgeItem(
            chat_id=scope.chat_id,
            message_thread_id=scope.thread_id,
            kind="note",
            content=text,
            source_message_ids=[],
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(row)
        await session.flush()
        return row.id


def test_space_identity_separates_endpoint_model_and_preprocessing(test_settings):
    from pzp_ai_bot.ai.factory import ProviderRegistry

    selection = ProviderRegistry(test_settings).select()
    base = space(selection)
    profile = replace(
        selection.profile,
        provider="openai_compatible",
        openai_base_url="https://ONE.example:443/v1/",
    )
    one = space(replace(selection, profile=profile))
    same = space(
        replace(
            selection,
            profile=replace(
                profile,
                name="renamed",
                openai_base_url="https://one.example/v1",
                openai_api_key="rotation",
            ),
        )
    )
    two = space(
        replace(selection, profile=replace(profile, openai_base_url="https://two.example/v1"))
    )
    assert one["key"] == same["key"]
    assert one["key"] != two["key"]
    assert base["key"] != space(replace(selection, embedding_model="unrelated-model"))["key"]
    assert (
        base["key"]
        != space(replace(selection, profile=replace(selection.profile, redact_before_ai=False)))[
            "key"
        ]
    )
    assert "rotation" not in str(same)


async def test_repository_preserves_spaces_updates_idempotently_and_filters_before_quota(context):
    from pzp_ai_bot.embedding_identity import content_hash

    scope = Scope(-100555)
    target_id = await add_note(context)
    selection = context.memory.provider_registry.select()
    first = space(selection)
    second = space(replace(selection, embedding_model="other"))
    async with session_scope(context.session_factory) as session:
        repo = EmbeddingRepository(session)
        one = await repo.add(
            target_type="knowledge_item",
            target_id=target_id,
            scope=scope,
            provider="fake",
            model=selection.embedding_model,
            vector=[1.0] * 64,
            space=first,
            source_hash=content_hash("durable private source"),
            route_epoch=0,
        )
        await repo.add(
            target_type="knowledge_item",
            target_id=target_id,
            scope=scope,
            provider="fake",
            model="other",
            vector=[2.0] * 64,
            space=second,
            source_hash=content_hash("durable private source"),
            route_epoch=0,
        )
        updated = await repo.add(
            target_type="knowledge_item",
            target_id=target_id,
            scope=scope,
            provider="fake",
            model=selection.embedding_model,
            vector=[3.0] * 64,
            space=first,
            source_hash=content_hash("durable private source"),
            route_epoch=0,
        )
        assert updated.id == one.id
        assert await repo.count(scope) == 2
        retrieved = await repo.list_for_retrieval(scope, limit=1, space_id=one.space_id)
        assert len(retrieved) == 1
        assert retrieved[0][1] == [3.0] * 64
        note = await session.get(KnowledgeItem, target_id)
        assert note is not None
        note.content = "edited source"
        await session.flush()
        assert await repo.list_for_retrieval(scope, space_id=one.space_id) == []


async def test_legacy_vectors_are_not_silently_adopted(context):
    scope = Scope(-100555)
    target_id = await add_note(context)
    async with session_scope(context.session_factory) as session:
        repo = EmbeddingRepository(session)
        row = await repo.add(
            target_type="knowledge_item",
            target_id=target_id,
            scope=scope,
            provider="fake",
            model="fake-embedding",
            vector=[1.0] * 64,
        )
        assert row.space_id is None
        assert row.provenance_status == "legacy_unverified"
        assert len(await repo.list_for_retrieval(scope)) == 1
        assert await repo.list_for_retrieval(scope, space_id=999) == []


async def test_epoch_guard_rolls_back_old_route_source_writes(context):
    from pzp_ai_bot.db.repos.embedding_indexes import (
        EmbeddingIndexRepository,
        EmbeddingRouteChanged,
    )

    async with session_scope(context.session_factory) as session:
        repo = EmbeddingIndexRepository(session)
        assert await repo.current_epoch() == 0
        await repo.advance_epoch(0)
    with pytest.raises(EmbeddingRouteChanged):
        async with session_scope(context.session_factory) as session:
            await EmbeddingIndexRepository(session).guard_write(0)
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingIndexRepository(session).current_epoch() == 1


def index_service(context):
    from pzp_ai_bot.memory.embedding_indexes import EmbeddingIndexService

    return EmbeddingIndexService(
        context.settings,
        context.session_factory,
        context.memory.provider_registry,
        context.memory._settings_profiles,
        context.memory._provider_operations,
    )


async def test_job_builds_without_activation_and_activation_fences_writes(context):
    from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingIndexRepository

    scope = Scope(-100555)
    await add_note(context)
    service = index_service(context)
    preview = await service.preview(scope, "default")
    assert preview["eligible"] == 1
    assert preview["missing"] == 1
    job = await service.create_job(scope, "default", actor_user_id=111)
    ready = await service.run_job(job["id"])
    assert ready["status"] == "ready"
    assert (await service.status(scope))["active"] is not None
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingIndexRepository(session).current_epoch() == 0
    activated = await service.activate_job(job["id"], ready["version"], True, actor_user_id=111)
    assert activated["status"] == "activated"
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingIndexRepository(session).current_epoch() == 1


async def test_job_rechecks_edited_and_deleted_sources_after_provider_call(context, monkeypatch):
    scope = Scope(-100555)
    edited_id = await add_note(context, "old text")
    deleted_id = await add_note(context, "to forget")
    service = index_service(context)
    selection = context.memory.provider_registry.select()
    original = selection.embedding_provider.embed

    async def edit_during_generation(texts):
        async with session_scope(context.session_factory) as session:
            note = await session.get(KnowledgeItem, edited_id)
            assert note is not None
            note.content = "new text"
            await session.execute(delete(KnowledgeItem).where(KnowledgeItem.id == deleted_id))
        return await original(texts)

    monkeypatch.setattr(selection.embedding_provider, "embed", edit_during_generation)
    job = await service.create_job(scope, "default", actor_user_id=111)
    result = await service.run_job(job["id"])
    assert result["status"] != "ready"
    async with session_scope(context.session_factory) as session:
        assert list((await session.scalars(select(Embedding))).all()) == []
    monkeypatch.setattr(selection.embedding_provider, "embed", original)
    ready = await service.run_job(job["id"])
    assert ready["status"] == "ready"
    assert ready["total"] == 1


async def test_failed_job_resumes_and_cancellation_is_terminal(context, monkeypatch):
    scope = Scope(-100555)
    await add_note(context)
    service = index_service(context)
    selection = context.memory.provider_registry.select()
    original = selection.embedding_provider.embed

    async def fail(texts):
        raise RuntimeError("secret raw source must not reach jobs")

    monkeypatch.setattr(selection.embedding_provider, "embed", fail)
    job = await service.create_job(scope, "default", actor_user_id=111)
    failed = await service.run_job(job["id"])
    assert failed["status"] == "failed"
    assert "secret" not in str(failed)
    monkeypatch.setattr(selection.embedding_provider, "embed", original)
    resumed = await service.resume_job(job["id"], failed["version"], actor_user_id=111)
    ready = await service.run_job(resumed["id"])
    assert ready["status"] == "ready"
    cancelled = await service.cancel_job(job["id"], ready["version"], actor_user_id=111)
    with pytest.raises(ValueError, match="cancelled"):
        await service.activate_job(job["id"], cancelled["version"], True, actor_user_id=111)


async def test_duplicate_worker_cannot_process_or_activate_stale_coverage(context, monkeypatch):
    scope = Scope(-100555)
    target_id = await add_note(context)
    service = index_service(context)
    selection = context.memory.provider_registry.select()
    original = selection.embedding_provider.embed
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused(texts):
        entered.set()
        await release.wait()
        return await original(texts)

    monkeypatch.setattr(selection.embedding_provider, "embed", paused)
    job = await service.create_job(scope, "default", actor_user_id=111)
    running = asyncio.create_task(service.run_job(job["id"]))
    await entered.wait()
    duplicate = await service.run_job(job["id"])
    assert duplicate["status"] == "running"
    release.set()
    ready = await running
    async with session_scope(context.session_factory) as session:
        note = await session.get(KnowledgeItem, target_id)
        assert note is not None
        note.content = "changed after ready"
    with pytest.raises(ValueError, match="coverage"):
        await service.activate_job(job["id"], ready["version"], True, actor_user_id=111)


async def test_activation_requires_exact_confirmation_phrase(context):
    service = index_service(context)
    job = await service.create_job(Scope(-100555), "default", actor_user_id=111)
    ready = await service.run_job(job["id"])
    assert ready["status"] == "ready"
    activated = await service.activate_job(
        job["id"], ready["version"], f"ACTIVATE {job['id']}", actor_user_id=111
    )
    assert activated["status"] == "activated"


async def test_activation_rejects_changed_settings_and_profile_without_mutation(context):
    from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingIndexRepository
    from pzp_ai_bot.db.repos.settings import ScopeSettingsRepository

    scope = Scope(-100555)
    await add_note(context)
    service = index_service(context)
    job = await service.create_job(scope, "default", actor_user_id=111)
    ready = await service.run_job(job["id"])
    async with session_scope(context.session_factory) as session:
        await ScopeSettingsRepository(session).set_settings(
            Scope.global_scope(), {"summary_batch_size": 3}, 111
        )
    with pytest.raises(ValueError, match="embedding_settings_changed"):
        await service.activate_job(job["id"], ready["version"], True, actor_user_id=111)
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingIndexRepository(session).current_epoch() == 0
        assert await ScopeSettingsRepository(session).get(scope) is None
    replacement = replace(
        context.memory.provider_registry.profile_by_name("default"), embedding_model="changed"
    )
    context.memory.provider_registry.replace_managed_profiles({"default": replacement})
    with pytest.raises(ValueError, match="embedding_profile_changed"):
        await service.activate_job(job["id"], ready["version"], True, actor_user_id=111)


async def test_failed_second_batch_preserves_first_and_resumes_without_repeating(
    context, monkeypatch
):
    scope = Scope(-100555)
    for value in range(3):
        await add_note(context, f"private note {value}")
    service = index_service(context)
    service.batch_size = 2
    selection = context.memory.provider_registry.select()
    original = selection.embedding_provider.embed
    requests = []

    async def fail_second(texts):
        requests.append(tuple(texts))
        if len(requests) == 2:
            raise RuntimeError("fail only the second batch")
        return await original(texts)

    monkeypatch.setattr(selection.embedding_provider, "embed", fail_second)
    job = await service.create_job(scope, "default", actor_user_id=111)
    failed = await service.run_job(job["id"], max_batches=2)
    assert failed["status"] == "failed"
    assert failed["processed"] == 2
    resumed = await service.resume_job(job["id"], failed["version"], actor_user_id=111)
    ready = await service.run_job(resumed["id"])
    assert ready["status"] == "ready"
    assert ready["processed"] == 3
    assert requests == [
        ("private note 0", "private note 1"),
        ("private note 2",),
        ("private note 2",),
    ]


async def test_cancelled_inflight_job_cannot_resurrect_or_overwrite_status(context, monkeypatch):
    scope = Scope(-100555)
    await add_note(context)
    service = index_service(context)
    selection = context.memory.provider_registry.select()
    original = selection.embedding_provider.embed
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(texts):
        entered.set()
        await release.wait()
        return await original(texts)

    monkeypatch.setattr(selection.embedding_provider, "embed", pause)
    job = await service.create_job(scope, "default", actor_user_id=111)
    running = asyncio.create_task(service.run_job(job["id"]))
    await entered.wait()
    active = await service.get_job(job["id"])
    cancelled = await service.cancel_job(job["id"], active["version"], actor_user_id=111)
    release.set()
    result = await running
    assert result["status"] == "cancelled"
    assert result["version"] == cancelled["version"]
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingRepository(session).count(scope) == 0


async def test_expired_worker_lease_is_reclaimed_and_fenced(context):
    from datetime import timedelta

    from pzp_ai_bot.db.models import EmbeddingIndexJob
    from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingIndexRepository

    service = index_service(context)
    job = await service.create_job(Scope(-100555), "default", actor_user_id=111)
    async with session_scope(context.session_factory) as session:
        row = await EmbeddingIndexRepository(session).claim_job(job["id"], "dead-worker", 300)
        assert row is not None
        old_fence = row.lease_fence
        row.lease_expires_at = utc_now() - timedelta(seconds=1)
    ready = await service.run_job(job["id"])
    assert ready["status"] == "ready"
    with pytest.raises(ValueError, match="embedding_job_lease_lost"):
        async with session_scope(context.session_factory) as session:
            await EmbeddingIndexRepository(session).guarded_job(
                job["id"], "dead-worker", old_fence, 300
            )
    async with session_scope(context.session_factory) as session:
        row = await session.get(EmbeddingIndexJob, job["id"])
        assert row is not None
        assert row.lease_fence == old_fence + 1


def test_schema_upgrade_preserves_legacy_vectors_and_matches_metadata(test_settings):
    import hashlib

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine, text

    from pzp_ai_bot.db.models import Base
    from pzp_ai_bot.db.session import upgrade_schema

    upgrade_schema(test_settings, "0018_provider_operation_profiles")
    engine = create_engine(test_settings.db_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO knowledge_items (id,chat_id,kind,content,source_message_ids,created_at,updated_at) VALUES (:id,-100555,'note',:content,'[]','2026-11-07','2026-11-07')"  # noqa: E501
            ),
            [{"id": value + 1, "content": f"source {value}"} for value in range(300)],
        )
        connection.execute(
            text(
                "INSERT INTO messages (telegram_message_id,chat_id,text,normalized_text,learned,created_at,ingested_at) VALUES (1,-100555,'RAW source','normalized source',1,'2026-11-07','2026-11-07')"  # noqa: E501
            )
        )
        connection.execute(
            text(
                "INSERT INTO embeddings (target_type,target_id,chat_id,provider,model,dimension,vector,created_at) VALUES ('knowledge_item',99,-100555,'local','old',2,X'0000803f00000000','2026-11-07')"  # noqa: E501
            )
        )
    upgrade_schema(test_settings)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT space_id,source_hash,provenance_status,vector FROM embeddings")
        ).one()
        assert row[:3] == (None, None, "legacy_unverified")
        assert row.vector == b"\x00\x00\x80?\x00\x00\x00\x00"
        backfilled = dict(
            connection.execute(
                text("SELECT id,embedding_source_hash FROM knowledge_items WHERE id IN (1,256,300)")
            ).all()
        )
        assert backfilled == {
            value + 1: hashlib.sha256(f"source {value}".encode()).hexdigest()
            for value in (0, 255, 299)
        }
        assert (
            connection.scalar(text("SELECT embedding_source_hash FROM messages"))
            == hashlib.sha256(b"normalized source").hexdigest()
        )
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()


async def test_managed_profile_missing_or_changed_in_database_cannot_bless_stale_vectors(context):
    from pzp_ai_bot.db.models import AIProviderProfile
    from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingRouteChanged
    from pzp_ai_bot.embedding_identity import content_hash

    scope = Scope(-100555)
    target_id = await add_note(context)
    selection = replace(
        context.memory.provider_registry.select(), managed_profile=True, profile_revision=1
    )
    metadata = space(selection)
    with pytest.raises(EmbeddingRouteChanged):
        async with session_scope(context.session_factory) as session:
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=target_id,
                scope=scope,
                provider="fake",
                model=selection.embedding_model,
                vector=[1.0] * 64,
                space=metadata,
                source_hash=content_hash("durable private source"),
                route_epoch=0,
            )
    async with session_scope(context.session_factory) as session:
        session.add(
            AIProviderProfile(
                name="default",
                provider_kind="fake",
                base_url=selection.profile.openai_base_url,
                chat_model="fake-chat",
                embedding_model="fake-embedding",
                chat_capable=True,
                embedding_capable=True,
                reasoning_capable=False,
                local=False,
                redact_before_ai=False,
                redaction_patterns=[],
                fake_embedding_dimensions=64,
                enabled=True,
                source_kind="managed",
                created_by=111,
                updated_by=111,
                created_at=utc_now(),
                updated_at=utc_now(),
                version=2,
            )
        )
    with pytest.raises(EmbeddingRouteChanged):
        async with session_scope(context.session_factory) as session:
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=target_id,
                scope=scope,
                provider="fake",
                model=selection.embedding_model,
                vector=[1.0] * 64,
                space=metadata,
                source_hash=content_hash("durable private source"),
                route_epoch=0,
            )
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingRepository(session).count(scope) == 0


async def test_topic_coverage_uses_one_space_and_activation_preserves_descendant_override(context):
    from pzp_ai_bot.db.repos.settings import ScopeSettingsRepository

    scope, topic = Scope(-100555), Scope(-100555, 7)
    await add_note(context, "main note", scope)
    await add_note(context, "topic note", topic)
    async with session_scope(context.session_factory) as session:
        await ScopeSettingsRepository(session).set_settings(
            topic, {"embedding_model": "topic-specific"}, 111
        )
    service = index_service(context)
    job = await service.create_job(scope, "default", model="query-space", actor_user_id=111)
    ready = await service.run_job(job["id"])
    assert ready["total"] == 2
    activated = await service.activate_job(
        job["id"], ready["version"], f"ACTIVATE {job['id']}", actor_user_id=111
    )
    assert activated["status"] == "activated"
    async with session_scope(context.session_factory) as session:
        settings = await ScopeSettingsRepository(session).get(topic)
        assert settings is not None
        assert settings.embedding_model == "topic-specific"
    selected_space = next(
        row for row in (await service.status(scope))["spaces"] if row["id"] == ready["space_id"]
    )
    assert (await service.coverage(topic, selected_space["key"]))["indexed"] == 1


async def test_rollback_requires_rebuilding_missing_sources_and_preserves_new_space(context):
    scope = Scope(-100555)
    await add_note(context, "original")
    service = index_service(context)
    first = await service.create_job(scope, "default", actor_user_id=111)
    first = await service.run_job(first["id"])
    first = await service.activate_job(first["id"], first["version"], True, actor_user_id=111)
    second = await service.create_job(scope, "default", model="new-space", actor_user_id=111)
    second = await service.run_job(second["id"])
    second = await service.activate_job(second["id"], second["version"], True, actor_user_id=111)
    await add_note(context, "new source since activation")
    rollback = await service.create_job(scope, "default", model="fake-embedding", actor_user_id=111)
    assert rollback["processed"] == 1
    assert rollback["total"] == 2
    with pytest.raises(ValueError, match="not_ready"):
        await service.activate_job(rollback["id"], rollback["version"], True, actor_user_id=111)
    rollback = await service.run_job(rollback["id"])
    rollback = await service.activate_job(
        rollback["id"], rollback["version"], True, actor_user_id=111
    )
    assert rollback["space_id"] == first["space_id"]
    assert rollback["space_id"] != second["space_id"]
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingRepository(session).count(scope) == 3


async def test_route_change_inside_embedding_add_rolls_back_the_source_transaction(context):
    from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingRouteChanged
    from pzp_ai_bot.embedding_identity import content_hash

    scope = Scope(-100555)
    await add_note(context, "original")
    service = index_service(context)
    old_selection = context.memory.provider_registry.select()
    job = await service.create_job(scope, "default", model="replacement", actor_user_id=111)
    ready = await service.run_job(job["id"])
    await service.activate_job(job["id"], ready["version"], True, actor_user_id=111)
    with pytest.raises(EmbeddingRouteChanged):
        async with session_scope(context.session_factory) as session:
            note = KnowledgeItem(
                chat_id=scope.chat_id,
                kind="note",
                content="old in-flight content",
                source_message_ids=[],
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(note)
            await session.flush()
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=note.id,
                scope=scope,
                provider="fake",
                model=old_selection.embedding_model,
                vector=[1.0] * 64,
                space=space(old_selection),
                source_hash=content_hash(note.content),
                route_epoch=0,
            )
    async with session_scope(context.session_factory) as session:
        assert list((await session.scalars(select(KnowledgeItem.content))).all()) == ["original"]


async def test_activation_sees_old_route_writer_committed_before_coverage_check(context):
    from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingIndexRepository
    from pzp_ai_bot.embedding_identity import content_hash

    scope = Scope(-100555)
    await add_note(context, "original")
    service = index_service(context)
    old_selection = context.memory.provider_registry.select()
    job = await service.create_job(scope, "default", model="replacement", actor_user_id=111)
    ready = await service.run_job(job["id"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def old_writer():
        async with session_scope(context.session_factory) as session:
            await EmbeddingIndexRepository(session).guard_write(0)
            note = KnowledgeItem(
                chat_id=scope.chat_id,
                kind="note",
                content="committed before activation",
                source_message_ids=[],
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(note)
            await session.flush()
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=note.id,
                scope=scope,
                provider="fake",
                model=old_selection.embedding_model,
                vector=[1.0] * 64,
                space=space(old_selection),
                source_hash=content_hash(note.content),
                route_epoch=0,
            )
            entered.set()
            await release.wait()

    writer = asyncio.create_task(old_writer())
    await entered.wait()
    activation = asyncio.create_task(
        service.activate_job(job["id"], ready["version"], True, actor_user_id=111)
    )
    release.set()
    await writer
    with pytest.raises(ValueError, match="embedding_coverage_required"):
        await activation
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingIndexRepository(session).current_epoch() == 0


async def test_deleted_managed_job_profile_does_not_fall_back_to_environment_profile(context):
    from pzp_ai_bot.db.models import AIProviderProfile

    scope = Scope(-100555)
    await add_note(context)
    profile = context.memory.provider_registry.profile_by_name("default")
    async with session_scope(context.session_factory) as session:
        session.add(
            AIProviderProfile(
                name=profile.name,
                provider_kind=profile.provider,
                base_url=profile.openai_base_url,
                chat_model=profile.chat_model,
                embedding_model=profile.embedding_model,
                chat_capable=True,
                embedding_capable=True,
                reasoning_capable=False,
                local=False,
                redact_before_ai=profile.redact_before_ai,
                redaction_patterns=list(profile.redaction_custom_patterns),
                fake_embedding_dimensions=profile.fake_embedding_dimensions,
                enabled=True,
                source_kind="managed",
                created_by=111,
                updated_by=111,
                created_at=utc_now(),
                updated_at=utc_now(),
                version=1,
            )
        )
    context.memory.provider_registry.replace_managed_profiles(
        {"default": profile}, revisions={"default": 1}
    )
    service = index_service(context)
    job = await service.create_job(scope, "default", actor_user_id=111)
    async with session_scope(context.session_factory) as session:
        await session.execute(delete(AIProviderProfile).where(AIProviderProfile.name == "default"))
    context.memory.provider_registry.replace_managed_profiles({})
    failed = await service.run_job(job["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "embedding_profile_changed"


async def test_activation_classifies_database_profile_drift_as_actionable_conflict(context):
    from pzp_ai_bot.db.models import AIProviderProfile

    scope = Scope(-100555)
    await add_note(context)
    profile = context.memory.provider_registry.profile_by_name("default")
    async with session_scope(context.session_factory) as session:
        row = AIProviderProfile(
            name=profile.name,
            provider_kind=profile.provider,
            base_url=profile.openai_base_url,
            chat_model=profile.chat_model,
            embedding_model=profile.embedding_model,
            chat_capable=True,
            embedding_capable=True,
            reasoning_capable=False,
            local=False,
            redact_before_ai=profile.redact_before_ai,
            redaction_patterns=list(profile.redaction_custom_patterns),
            fake_embedding_dimensions=profile.fake_embedding_dimensions,
            enabled=True,
            source_kind="managed",
            created_by=111,
            updated_by=111,
            created_at=utc_now(),
            updated_at=utc_now(),
            version=1,
        )
        session.add(row)
    context.memory.provider_registry.replace_managed_profiles(
        {"default": profile}, revisions={"default": 1}
    )
    service = index_service(context)
    job = await service.create_job(scope, "default", actor_user_id=111)
    ready = await service.run_job(job["id"])
    assert ready["status"] == "ready"
    async with session_scope(context.session_factory) as session:
        row = await session.scalar(
            select(AIProviderProfile).where(AIProviderProfile.name == "default")
        )
        assert row is not None
        row.redact_before_ai = not profile.redact_before_ai
    with pytest.raises(ValueError, match="embedding_profile_changed"):
        await service.activate_job(job["id"], ready["version"], True, actor_user_id=111)


async def test_source_repositories_persist_fingerprints_and_raw_edits_invalidate_them(context):
    from sqlalchemy import text

    from pzp_ai_bot.db.repos.knowledge import KnowledgeRepository
    from pzp_ai_bot.db.repos.messages import MessageRepository
    from pzp_ai_bot.embedding_identity import content_hash

    scope = Scope(-100555)
    async with session_scope(context.session_factory) as session:
        repo = KnowledgeRepository(session)
        row = await repo.add(
            scope=scope, kind="manual_note", content="initial", source_message_ids=[]
        )
        assert row.embedding_source_hash == content_hash("initial")
        updated = await repo.update_manual_note(row.id, scope=scope, content="repository edit")
        assert updated is not None
        assert updated.embedding_source_hash == content_hash("repository edit")
        await session.execute(
            text(
                "UPDATE knowledge_items SET content=:content, embedding_source_hash=:attempted_hash WHERE id=:id"  # noqa: E501
            ),
            {
                "content": "raw SQL edit",
                "attempted_hash": content_hash("raw SQL edit"),
                "id": row.id,
            },
        )
        stored = await session.scalar(
            select(KnowledgeItem.embedding_source_hash).where(KnowledgeItem.id == row.id)
        )
        assert stored is None
        message, created = await MessageRepository(session).add(
            telegram_message_id=1,
            scope=scope,
            user_id=111,
            username=None,
            text="Raw source",
            normalized_text="normalized source",
            learned=True,
            skip_reason=None,
            reply_to_message_id=None,
            created_at=utc_now(),
        )
        assert created
        assert message.embedding_source_hash == content_hash("normalized source")
        await session.execute(
            text("UPDATE messages SET normalized_text='raw SQL message edit' WHERE id=:id"),
            {"id": message.id},
        )
        from pzp_ai_bot.db.models import Message

        assert (
            await session.scalar(
                select(Message.embedding_source_hash).where(Message.id == message.id)
            )
            is None
        )


async def test_coverage_uses_two_aggregate_queries_without_hashing_source_payloads(
    context, monkeypatch
):
    from sqlalchemy import event

    import pzp_ai_bot.db.repos.embedding_indexes as module

    scope = Scope(-100555)
    await add_note(context)
    service = index_service(context)
    job = await service.create_job(scope, "default", actor_user_id=111)
    ready = await service.run_job(job["id"])
    statements = []

    def record(connection, cursor, statement, parameters, execution_context, executemany):
        statements.append(statement)

    def no_payload_hashing(value):
        raise AssertionError("coverage must not load/hash source payloads")

    monkeypatch.setattr(module, "content_hash", no_payload_hashing, raising=False)
    engine = context.session_factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        async with session_scope(context.session_factory) as session:
            coverage = await module.EmbeddingIndexRepository(session).coverage(
                scope, ready["space_id"]
            )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert coverage == {"eligible": 1, "indexed": 1, "missing": 0, "stale": 0}
    assert len(statements) == 2
    assert all("count(" in statement.lower() for statement in statements)
    assert all(
        "knowledge_items.content" not in statement and "messages.normalized_text" not in statement
        for statement in statements
    )


async def test_missing_source_payload_hydration_is_bounded_before_hashing(context, monkeypatch):
    import pzp_ai_bot.db.repos.embedding_indexes as module

    scope = Scope(-100555)
    for value in range(12):
        await add_note(context, f"note {value}")
    service = index_service(context)
    service.batch_size = 2
    job = await service.create_job(scope, "default", actor_user_id=111)
    running = await service.run_job(job["id"])
    calls = []
    original_hash = module.content_hash

    def selected_only(value):
        calls.append(value)
        return original_hash(value)

    monkeypatch.setattr(module, "content_hash", selected_only)
    async with session_scope(context.session_factory) as session:
        sources = await module.EmbeddingIndexRepository(session).missing_sources(
            scope, running["space_id"], limit=3
        )
    assert [row.text for row in sources] == ["note 2", "note 3", "note 4"]
    assert calls == ["note 2", "note 3", "note 4"]


async def test_verified_retrieval_applies_sql_freshness_before_candidate_limit(
    context, monkeypatch
):
    from sqlalchemy import text

    import pzp_ai_bot.db.repos.embeddings as module

    scope = Scope(-100555)
    older = await add_note(context, "older fresh note")
    newer = await add_note(context, "newer stale note")
    service = index_service(context)
    job = await service.create_job(scope, "default", actor_user_id=111)
    ready = await service.run_job(job["id"])
    async with session_scope(context.session_factory) as session:
        await session.execute(
            text("UPDATE knowledge_items SET content='changed outside repository' WHERE id=:id"),
            {"id": newer},
        )

    def no_payload_hashing(value):
        raise AssertionError("retrieval freshness belongs in SQL before LIMIT")

    monkeypatch.setattr(module, "content_hash", no_payload_hashing, raising=False)
    async with session_scope(context.session_factory) as session:
        rows = await module.EmbeddingRepository(session).list_for_retrieval(
            scope, space_id=ready["space_id"], limit=1
        )
    assert [row.target_id for row, _ in rows] == [older]


async def test_deliberately_owned_create_all_schema_installs_freshness_triggers(test_settings):
    from sqlalchemy import text

    from pzp_ai_bot.db.repos.knowledge import KnowledgeRepository
    from pzp_ai_bot.db.session import create_schema, create_session_factory

    await create_schema(test_settings)
    factory = create_session_factory(test_settings)
    try:
        async with session_scope(factory) as session:
            note = await KnowledgeRepository(session).add(
                scope=Scope(-100555), kind="note", content="fresh source", source_message_ids=[]
            )
            assert note.embedding_source_hash is not None
            await session.execute(
                text("UPDATE knowledge_items SET content='changed' WHERE id=:id"), {"id": note.id}
            )
            assert (
                await session.scalar(
                    select(KnowledgeItem.embedding_source_hash).where(KnowledgeItem.id == note.id)
                )
                is None
            )
    finally:
        await factory.kw["bind"].dispose()
