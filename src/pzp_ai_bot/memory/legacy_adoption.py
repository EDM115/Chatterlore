from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingIndexRepository, space_view
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.legacy_adoption import LegacyAdoptionRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.embedding_adoption import LegacyAdoptionManifest
from pzp_ai_bot.embedding_identity import embedding_space_for_profile
from pzp_ai_bot.memory.scope import parse_scope

if TYPE_CHECKING:
    from pzp_ai_bot.memory.embedding_indexes import EmbeddingIndexService


async def _prepare(
    service: EmbeddingIndexService,
    session: AsyncSession,
    manifest: LegacyAdoptionManifest,
    actor_user_id: int | None,
) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    scope = parse_scope(manifest.scope)
    model, configuration = service._configuration(manifest.provider_profile, manifest.model)
    if configuration["config_hash"] != manifest.original_config_hash:
        raise ValueError("embedding_adoption_provenance_invalid")
    profile = service.registry.profile_by_name(manifest.provider_profile)
    known_dimension = (
        profile.fake_embedding_dimensions
        if profile.provider == "fake"
        else profile.local_embedding_options.get("truncate_dim")
        if profile.provider == "local"
        else None
    )
    if known_dimension is not None and known_dimension != manifest.dimension:
        raise ValueError("embedding_adoption_provenance_invalid")
    metadata = embedding_space_for_profile(
        profile,
        model,
        manifest.dimension,
        managed_profile=service.registry.is_managed(profile.name),
    )
    indexes = EmbeddingIndexRepository(session)
    await indexes.validate_profile(metadata)
    repo = LegacyAdoptionRepository(session)
    candidates = await repo.inspect(
        manifest, scope, space_key=metadata["key"], provider_kind=profile.provider
    )
    epoch = await indexes.current_epoch()
    snapshot = {
        "manifest": {
            **manifest.model_dump(mode="json"),
            "entries": [
                entry.model_dump()
                for entry in sorted(manifest.entries, key=lambda item: item.embedding_id)
            ],
        },
        "actor_user_id": actor_user_id,
        "scope": scope.key,
        "space_key": metadata["key"],
        "route_epoch": epoch,
        "targets": [
            (item.row.id, item.row.target_type, item.row.target_id, item.source.scope.key)
            for item in candidates
        ],
    }
    digest = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        {
            "preview_hash": digest,
            "count": len(candidates),
            "scope": scope.key,
            "provider_profile": profile.name,
            "model": model,
            "dimension": manifest.dimension,
            "space_key": metadata["key"],
            "configuration_fingerprint": metadata["config_hash"],
            "route_epoch": epoch,
        },
        metadata,
        candidates,
    )


async def preview_legacy_adoption(
    service: EmbeddingIndexService, value: Any, *, actor_user_id: int | None = None
) -> dict[str, Any]:
    manifest = LegacyAdoptionManifest.parse(value)
    async with session_scope(service.session_factory) as session:
        preview, _, _ = await _prepare(service, session, manifest, actor_user_id)
        return preview


async def adopt_legacy(
    service: EmbeddingIndexService,
    value: Any,
    *,
    preview_hash: str,
    confirmation: str,
    provenance_attested: bool,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    if provenance_attested is not True or confirmation != f"ADOPT {preview_hash}":
        raise ValueError("embedding_adoption_confirmation_required")
    manifest = LegacyAdoptionManifest.parse(value)
    async with session_scope(service.session_factory) as session:
        indexes = EmbeddingIndexRepository(session)
        await indexes.guard_write()
        preview, metadata, candidates = await _prepare(service, session, manifest, actor_user_id)
        if preview_hash != preview["preview_hash"]:
            raise ValueError("embedding_adoption_preview_changed")
        space = await indexes.register_space(metadata)
        await LegacyAdoptionRepository(session).adopt(candidates, space)
        await EventRepository(session).add(
            event_type="embedding_index.legacy_adopted",
            scope=parse_scope(manifest.scope),
            user_id=actor_user_id,
            payload={
                "count": len(candidates),
                "embedding_ids": [item.row.id for item in candidates],
                "space_key": space.key,
                "configuration_fingerprint": metadata["config_hash"],
                "preview_hash": preview_hash,
                "actor_kind": "telegram_user" if actor_user_id is not None else "local_operator",
            },
        )
        return {
            "adopted": len(candidates),
            "preview_hash": preview_hash,
            "space": space_view(space),
        }
