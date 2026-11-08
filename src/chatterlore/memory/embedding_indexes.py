from __future__ import annotations

import math
from secrets import token_hex
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chatterlore.ai.factory import ProviderRegistry, ProviderSelection
from chatterlore.config import Settings
from chatterlore.db.models import Embedding, EmbeddingIndexJob, EmbeddingSpace
from chatterlore.db.repos.embedding_indexes import (
    EmbeddingIndexRepository,
    EmbeddingRouteChanged,
    is_expired,
    job_view,
    space_view,
)
from chatterlore.db.repos.embeddings import EmbeddingRepository
from chatterlore.db.repos.events import EventRepository
from chatterlore.db.repos.settings import ScopeSettingsRepository
from chatterlore.db.repos.shared import scope_filter, utc_now
from chatterlore.db.session import session_scope
from chatterlore.embedding_identity import embedding_configuration, embedding_space_metadata
from chatterlore.memory.provider_operations import ProviderOperations
from chatterlore.memory.scope import Scope
from chatterlore.memory.settings_profiles import MemorySettingsService


class EmbeddingIndexService:
    batch_size = 32
    lease_seconds = 300

    async def preview_legacy_adoption(
        self, manifest: Any, *, actor_user_id: int | None = None
    ) -> dict[str, Any]:
        from .legacy_adoption import preview_legacy_adoption

        return await preview_legacy_adoption(self, manifest, actor_user_id=actor_user_id)

    async def adopt_legacy(
        self,
        manifest: Any,
        *,
        preview_hash: str,
        confirmation: str,
        provenance_attested: bool,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        from .legacy_adoption import adopt_legacy

        return await adopt_legacy(
            self,
            manifest,
            preview_hash=preview_hash,
            confirmation=confirmation,
            provenance_attested=provenance_attested,
            actor_user_id=actor_user_id,
        )

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        settings_profiles: MemorySettingsService,
        provider_operations: ProviderOperations,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.registry = registry
        self.settings_profiles = settings_profiles
        self.provider_operations = provider_operations

    def _configuration(
        self, provider_profile: str, model: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        profile = self.registry.profile_by_name(provider_profile)
        if "embedding" not in self.registry.profile_capabilities(profile.name):
            raise ValueError("embedding_profile_unavailable")
        chosen_model = self.settings.validate_embedding_model(model or profile.embedding_model)
        return chosen_model, embedding_configuration(profile, chosen_model)

    def _selection(self, row: EmbeddingIndexJob) -> ProviderSelection:
        model, config = self._configuration(row.provider_profile, row.model)
        if (
            config["config_hash"] != row.config_hash
            or row.config_json.get("_profile_embedding_model")
            != self.registry.profile_by_name(row.provider_profile).embedding_model
            or bool(row.config_json.get("_managed_profile"))
            != self.registry.is_managed(row.provider_profile)
        ):
            raise ValueError("embedding_profile_changed")
        return self.registry.select(row.provider_profile, embedding_model=model)

    async def status(self, scope: Scope) -> dict[str, Any]:
        effective = await self.settings_profiles.get_scope_settings(scope)
        active_hash: str | None = None
        try:
            _, active_configuration = self._configuration(
                effective.embedding_provider_profile or effective.provider_profile,
                effective.embedding_model,
            )
            active_hash = active_configuration["config_hash"]
        except ValueError:
            pass
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            manifests = await repo.list_spaces()
            spaces = [
                {**space_view(manifest), **await repo.coverage(scope, manifest.id)}
                for manifest in manifests
            ]
            active = next(
                (
                    space_view(manifest)
                    for manifest in manifests
                    if manifest.config_hash == active_hash
                ),
                None,
            )
            legacy = int(
                await session.scalar(
                    select(func.count(Embedding.id)).where(
                        scope_filter(Embedding, scope), Embedding.space_id.is_(None)
                    )
                )
                or 0
            )
            return {
                "scope": scope.key,
                "active": active,
                "spaces": spaces,
                "jobs": [job_view(row) for row in await repo.list_jobs(scope)],
                "legacy_unverified": legacy,
            }

    async def preview(
        self, scope: Scope, provider_profile: str, model: str | None = None
    ) -> dict[str, Any]:
        chosen_model, config = self._configuration(provider_profile, model)
        profile = self.registry.profile_by_name(provider_profile)
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            manifest = await repo.find_space(config["config_hash"])
            coverage = await repo.coverage(scope, manifest.id if manifest else None)
        return {
            "scope": scope.key,
            "provider_profile": profile.name,
            "model": chosen_model,
            **coverage,
            "expected_batches": (coverage["missing"] + self.batch_size - 1) // self.batch_size,
            "cost_warning": "This explicit indexing job may incur provider usage charges."
            if profile.provider == "openai_compatible" and not profile.local
            else None,
            "local_preparation_required": profile.provider == "local" and manifest is None,
        }

    async def coverage(self, scope: Scope, space_key: str) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            manifest = await repo.get_space(space_key)
            return {
                "scope": scope.key,
                "space_key": space_key,
                "space_id": manifest.id if manifest else None,
                **await repo.coverage(scope, manifest.id if manifest else None),
            }

    async def create_job(
        self,
        scope: Scope,
        provider_profile: str,
        model: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        chosen_model, config = self._configuration(provider_profile, model)
        profile = self.registry.profile_by_name(provider_profile)
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            await repo.guard_write()
            manifest = await repo.find_space(config["config_hash"])
            coverage = await repo.coverage(scope, manifest.id if manifest else None)
            now = utc_now()
            row = EmbeddingIndexJob(
                chat_id=scope.chat_id,
                message_thread_id=scope.thread_id,
                provider_profile=profile.name,
                model=chosen_model,
                space_id=manifest.id if manifest else None,
                config_hash=config["config_hash"],
                config_json={
                    **config["config_json"],
                    "_profile_embedding_model": profile.embedding_model,
                    "_managed_profile": self.registry.is_managed(profile.name),
                },
                settings_snapshot=await repo.settings_snapshot(scope),
                status="queued",
                version=1,
                processed=coverage["indexed"],
                total=coverage["eligible"],
                lease_fence=0,
                actor_user_id=actor_user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
            await self._audit(session, row, "created", actor_user_id)
            return job_view(row)

    async def get_job(self, job_id: int) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            return job_view(await EmbeddingIndexRepository(session).get_job(job_id))

    async def run_job(self, job_id: int, max_batches: int = 1) -> dict[str, Any]:
        if (
            isinstance(max_batches, bool)
            or not isinstance(max_batches, int)
            or not 1 <= max_batches <= 64
        ):
            raise ValueError("embedding_batch_limit_invalid")
        token = token_hex(24)
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            row = await repo.claim_job(job_id, token, self.lease_seconds)
            if row is None:
                return job_view(await repo.get_job(job_id))
            fence = row.lease_fence
        try:
            for batch_number in range(max_batches):
                async with session_scope(self.session_factory) as session:
                    repo = EmbeddingIndexRepository(session)
                    row = await repo.guarded_job(job_id, token, fence, self.lease_seconds)
                    selection = self._selection(row)
                    scope = Scope(row.chat_id, row.message_thread_id)
                    sources = await repo.missing_sources(scope, row.space_id, self.batch_size)
                    if not sources and row.space_id is not None:
                        coverage = await repo.coverage(scope, row.space_id)
                        self._progress(row, coverage, release=True)
                        await self._audit(session, row, "ready", None)
                        return job_view(row)
                # Explicit empty-scope preparation uses one synthetic dimension probe
                texts = [source.text for source in sources] or [
                    "chatterlore embedding space dimension preparation"
                ]
                generation = await self.provider_operations.generate_embeddings(
                    selection,
                    texts,
                    operation="embedding_reindex",
                    metadata={"scope": scope.key, "job_id": job_id},
                )
                if generation.failure is not None:
                    return await self._fail(
                        job_id, token, fence, "embedding_provider_failure", generation.telemetry
                    )
                vectors = generation.vectors
                if (
                    len(vectors) != len(texts)
                    or not vectors
                    or not vectors[0]
                    or any(
                        len(vector) != len(vectors[0])
                        or any(
                            isinstance(value, bool)
                            or not isinstance(value, (int, float))
                            or not math.isfinite(value)
                            for value in vector
                        )
                        for vector in vectors
                    )
                ):
                    return await self._fail(
                        job_id, token, fence, "embedding_vector_invalid", generation.telemetry
                    )
                metadata = generation.space or embedding_space_metadata(selection, len(vectors[0]))
                async with session_scope(self.session_factory) as session:
                    repo = EmbeddingIndexRepository(session)
                    row = await repo.guarded_job(job_id, token, fence, self.lease_seconds)
                    self._selection(row)
                    if metadata["config_hash"] != row.config_hash:
                        raise ValueError("embedding_profile_changed")
                    epoch = await repo.guard_write()
                    await repo.validate_profile(metadata)
                    manifest = await repo.register_space(metadata)
                    if row.space_id is not None and row.space_id != manifest.id:
                        raise ValueError("embedding_space_dimension_changed")
                    row.space_id = manifest.id
                    for source, vector in zip(sources, vectors, strict=bool(sources)):
                        current = await repo.source(source.target_type, source.target_id)
                        if (
                            current is None
                            or current.source_hash != source.source_hash
                            or current.scope != source.scope
                        ):
                            continue
                        await EmbeddingRepository(session).add(
                            target_type=source.target_type,
                            target_id=source.target_id,
                            scope=source.scope,
                            provider=selection.embedding_provider.name,
                            model=selection.embedding_model,
                            vector=vector,
                            space=metadata,
                            source_hash=source.source_hash,
                            route_epoch=epoch,
                        )
                    await self.provider_operations.add_usage(session, generation.telemetry)
                    coverage = await repo.coverage(scope, manifest.id)
                    self._progress(
                        row,
                        coverage,
                        release=batch_number + 1 == max_batches or coverage["missing"] == 0,
                    )
                    if row.status == "ready":
                        await self._audit(session, row, "ready", None)
                    result = job_view(row)
                    if row.status == "ready":
                        return result
            return result
        except EmbeddingRouteChanged:
            return await self._fail(job_id, token, fence, "embedding_profile_changed")
        except ValueError as error:
            code = str(error).split(":", 1)[0]
            if code == "embedding_job_lease_lost":
                return await self.get_job(job_id)
            safe_code = (
                code
                if code
                in {
                    "embedding_profile_changed",
                    "embedding_profile_unavailable",
                    "embedding_space_dimension_changed",
                    "embedding_space_conflict",
                    "embedding_dimension_invalid",
                    "embedding_endpoint_invalid",
                }
                else "embedding_job_failed"
            )
            return await self._fail(job_id, token, fence, safe_code)
        except Exception:
            return await self._fail(job_id, token, fence, "embedding_job_failed")

    async def _fail(
        self, job_id: int, token: str, fence: int, code: str, telemetry: Any = None
    ) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            try:
                row = await repo.guarded_job(job_id, token, fence, self.lease_seconds)
            except ValueError:
                return job_view(await repo.get_job(job_id))
            row.status, row.error_code = "failed", code
            row.lease_token = row.lease_expires_at = None
            row.version += 1
            row.updated_at = utc_now()
            if telemetry is not None:
                await self.provider_operations.add_usage(session, telemetry)
            await self._audit(session, row, "failed", None)
            return job_view(row)

    @staticmethod
    def _progress(row: EmbeddingIndexJob, coverage: dict[str, int], *, release: bool) -> None:
        row.processed, row.total = coverage["indexed"], coverage["eligible"]
        row.status = "ready" if coverage["missing"] == 0 else "running"
        row.error_code = None
        row.version += 1
        row.updated_at = utc_now()
        if release:
            row.lease_token = row.lease_expires_at = None

    async def resume_job(
        self, job_id: int, version: int, actor_user_id: int | None = None
    ) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            await repo.guard_write()
            row = await repo.get_job(job_id)
            self._version(row, version)
            if row.status in {"cancelled", "activated"}:
                raise ValueError(f"embedding_job_{row.status}")
            if row.lease_token is not None and not is_expired(row.lease_expires_at):
                raise ValueError("embedding_job_busy")
            self._selection(row)
            row.status, row.error_code = "queued", None
            row.lease_token = row.lease_expires_at = None
            row.lease_fence += 1
            row.version += 1
            row.updated_at = utc_now()
            await self._audit(session, row, "resumed", actor_user_id)
            return job_view(row)

    async def cancel_job(
        self, job_id: int, version: int, actor_user_id: int | None = None
    ) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            await repo.guard_write()
            row = await repo.get_job(job_id)
            self._version(row, version)
            if row.status == "activated":
                raise ValueError("embedding_job_activated")
            if row.status == "cancelled":
                return job_view(row)
            row.status = "cancelled"
            row.lease_token = row.lease_expires_at = None
            row.lease_fence += 1
            row.version += 1
            row.updated_at = utc_now()
            await self._audit(session, row, "cancelled", actor_user_id)
            return job_view(row)

    async def activate_job(
        self, job_id: int, version: int, confirmation: bool | str, actor_user_id: int | None = None
    ) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            repo = EmbeddingIndexRepository(session)
            epoch = await repo.guard_write()
            row = await repo.get_job(job_id)
            self._version(row, version)
            if row.status == "cancelled":
                raise ValueError("embedding_job_cancelled")
            if row.status != "ready" or row.space_id is None or row.lease_token is not None:
                raise ValueError("embedding_job_not_ready")
            if confirmation is not True and str(confirmation).strip().lower() not in {
                str(job_id),
                "activate",
                f"activate {job_id}",
            }:
                raise ValueError("embedding_confirmation_required")
            self._selection(row)
            scope = Scope(row.chat_id, row.message_thread_id)
            if await repo.settings_snapshot(scope) != row.settings_snapshot:
                raise ValueError("embedding_settings_changed")
            manifest = await session.get(EmbeddingSpace, row.space_id)
            if manifest is None or manifest.config_hash != row.config_hash:
                raise ValueError("embedding_space_conflict")
            try:
                await repo.validate_profile(
                    embedding_space_metadata(self._selection(row), manifest.dimension)
                )
            except EmbeddingRouteChanged:
                raise ValueError("embedding_profile_changed") from None
            coverage = await repo.coverage(scope, row.space_id)
            if coverage["missing"]:
                raise ValueError("embedding_coverage_required")
            settings_repo = ScopeSettingsRepository(session)
            current_version = await settings_repo.current_version(scope)
            binding = await settings_repo.set_settings_versioned(
                scope,
                {"embedding_provider_profile": row.provider_profile, "embedding_model": row.model},
                actor_user_id,
                expected_version=current_version,
            )
            if binding is None:
                raise ValueError("embedding_settings_changed")
            await repo.advance_epoch(epoch)
            row.status = "activated"
            row.version += 1
            row.processed, row.total = coverage["indexed"], coverage["eligible"]
            row.updated_at = utc_now()
            await self._audit(session, row, "activated", actor_user_id)
            return job_view(row)

    async def worker_tick(self) -> dict[str, Any] | None:
        async with session_scope(self.session_factory) as session:
            job_id = await session.scalar(
                select(EmbeddingIndexJob.id)
                .where(
                    EmbeddingIndexJob.status.in_(["queued", "running"]),
                    or_(
                        EmbeddingIndexJob.lease_token.is_(None),
                        EmbeddingIndexJob.lease_expires_at <= utc_now(),
                    ),
                )
                .order_by(EmbeddingIndexJob.id)
                .limit(1)
            )
        return await self.run_job(job_id) if job_id is not None else None

    @staticmethod
    def _version(row: EmbeddingIndexJob, version: int) -> None:
        if row.version != version:
            raise ValueError("embedding_job_version_conflict")

    @staticmethod
    async def _audit(
        session: AsyncSession, row: EmbeddingIndexJob, action: str, actor_user_id: int | None
    ) -> None:
        await EventRepository(session).add(
            event_type=f"embedding_index.{action}",
            scope=Scope(row.chat_id, row.message_thread_id),
            user_id=actor_user_id,
            payload={
                "job_id": row.id,
                "space_id": row.space_id,
                "config_hash": row.config_hash,
                "provider_profile": row.provider_profile,
                "model": row.model,
                "processed": row.processed,
                "total": row.total,
                "version": row.version,
                "error_code": row.error_code,
            },
        )
