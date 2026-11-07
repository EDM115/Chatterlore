from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, case, func, not_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.config import ProviderProfile
from pzp_ai_bot.db.models import (
    AIProviderProfile,
    Embedding,
    EmbeddingIndexJob,
    EmbeddingRoutingState,
    EmbeddingSpace,
    KnowledgeItem,
    Message,
    RuntimeSetting,
    ScopeSettings,
)
from pzp_ai_bot.embedding_identity import content_hash, embedding_configuration
from pzp_ai_bot.memory.scope import Scope

from .shared import exact_scope_filter, scope_filter, utc_now


class EmbeddingRouteChanged(RuntimeError):  # noqa: N818
    def __init__(self) -> None:
        self.telemetry: Any | None = None
        super().__init__(
            "embedding_route_changed: resolve the embedding route and retry generation"
        )


class EmbeddingSourceChanged(ValueError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__(
            "embedding_source_changed: source is missing or no longer matches the generated vector"
        )


@dataclass(frozen=True, slots=True)
class EmbeddingSource:
    target_type: str
    target_id: int
    scope: Scope
    text: str
    source_hash: str
    persisted_hash: str | None = None


def source_view(row: KnowledgeItem | Message) -> EmbeddingSource:
    text = row.normalized_text if isinstance(row, Message) else row.content
    return EmbeddingSource(
        "message" if isinstance(row, Message) else "knowledge_item",
        row.id,
        Scope(row.chat_id, row.message_thread_id),
        text,
        content_hash(text),
        row.embedding_source_hash,
    )


def space_view(row: EmbeddingSpace) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": row.key,
        "provider_profile": row.provider_profile,
        "model": row.model,
        "dimension": row.dimension,
        "created_at": row.created_at.isoformat(),
    }


def job_view(row: EmbeddingIndexJob) -> dict[str, Any]:
    return {
        "id": row.id,
        "scope": Scope(row.chat_id, row.message_thread_id).key,
        "provider_profile": row.provider_profile,
        "model": row.model,
        "space_id": row.space_id,
        "status": row.status,
        "version": row.version,
        "processed": row.processed,
        "total": row.total,
        "error_code": row.error_code,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


class EmbeddingIndexRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current_epoch(self) -> int:
        value = await self.session.scalar(
            select(EmbeddingRoutingState.epoch).where(EmbeddingRoutingState.id == 1)
        )
        return int(value or 0)

    async def guard_write(self, expected_epoch: int | None = None) -> int:
        await self.session.execute(
            sqlite_insert(EmbeddingRoutingState)
            .values(id=1, epoch=0, content_revision=0)
            .on_conflict_do_nothing()
        )
        statement = update(EmbeddingRoutingState).where(EmbeddingRoutingState.id == 1)
        if expected_epoch is not None:
            statement = statement.where(EmbeddingRoutingState.epoch == expected_epoch)
        epoch = await self.session.scalar(
            statement.values(content_revision=EmbeddingRoutingState.content_revision + 1)
            .returning(EmbeddingRoutingState.epoch)
            .execution_options(synchronize_session=False)
        )
        if epoch is None:
            raise EmbeddingRouteChanged()
        return int(epoch)

    async def advance_epoch(self, expected_epoch: int | None = None) -> int:
        epoch = await self.guard_write(expected_epoch)
        await self.session.execute(
            update(EmbeddingRoutingState)
            .where(EmbeddingRoutingState.id == 1, EmbeddingRoutingState.epoch == epoch)
            .values(epoch=epoch + 1)
            .execution_options(synchronize_session=False)
        )
        return epoch + 1

    async def get_space(self, key: str) -> EmbeddingSpace | None:
        return await self.session.scalar(select(EmbeddingSpace).where(EmbeddingSpace.key == key))

    async def find_space(self, config_hash: str) -> EmbeddingSpace | None:
        return await self.session.scalar(
            select(EmbeddingSpace)
            .where(EmbeddingSpace.config_hash == config_hash)
            .order_by(EmbeddingSpace.id.desc())
        )

    async def register_space(self, metadata: dict[str, Any]) -> EmbeddingSpace:
        dimension = metadata.get("dimension")
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
            raise ValueError("embedding_dimension_invalid")
        existing = await self.get_space(str(metadata["key"]))
        if existing is not None:
            if (
                existing.dimension != dimension
                or existing.config_hash != metadata["config_hash"]
                or existing.model != metadata["model"]
            ):
                raise ValueError("embedding_space_conflict")
            return existing
        row = EmbeddingSpace(
            key=str(metadata["key"]),
            provider_profile=str(metadata["provider_profile"]),
            provider_kind=str(metadata["provider_kind"]),
            model=str(metadata["model"]),
            dimension=dimension,
            config_hash=str(metadata["config_hash"]),
            config_json=dict(metadata["config_json"]),
            created_at=utc_now(),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def validate_profile(self, metadata: dict[str, Any]) -> None:
        row = await self.session.scalar(
            select(AIProviderProfile).where(AIProviderProfile.name == metadata["provider_profile"])
        )
        if row is None:
            if metadata.get("managed_profile"):
                raise EmbeddingRouteChanged()
            return
        if not row.enabled or not row.embedding_capable:
            raise EmbeddingRouteChanged()
        profile = ProviderProfile(
            name=row.name,
            provider=cast(Any, row.provider_kind),
            chat_model=row.chat_model,
            embedding_model=row.embedding_model,
            openai_api_key="",
            openai_api_key_env=None,
            openai_base_url=row.base_url,
            redact_before_ai=row.redact_before_ai,
            redaction_custom_patterns=tuple(row.redaction_patterns),
            fake_embedding_dimensions=row.fake_embedding_dimensions,
            local=row.local,
            local_embedding_options=row.local_embedding_options,
        )
        current = embedding_configuration(profile, metadata["model"])
        if (
            current["config_hash"] != metadata["config_hash"]
            or metadata.get("profile_embedding_model", row.embedding_model) != row.embedding_model
        ):
            raise EmbeddingRouteChanged()

    async def list_spaces(self) -> list[EmbeddingSpace]:
        return list(
            (
                await self.session.scalars(
                    select(EmbeddingSpace).order_by(EmbeddingSpace.id.desc())
                )
            ).all()
        )

    async def source(self, target_type: str, target_id: int) -> EmbeddingSource | None:
        if target_type == "knowledge_item":
            row = await self.session.get(KnowledgeItem, target_id, populate_existing=True)
        elif target_type == "message":
            row = await self.session.get(Message, target_id, populate_existing=True)
            if row is not None and not row.learned:
                return None
        else:
            raise ValueError("embedding_target_invalid")
        return source_view(row) if row is not None else None

    async def sources(self, scope: Scope) -> AsyncIterator[EmbeddingSource]:
        for model in (KnowledgeItem, Message):
            statement = select(model).where(scope_filter(model, scope)).order_by(model.id)
            if model is Message:
                statement = statement.where(Message.learned.is_(True))
            rows = await self.session.stream_scalars(statement.execution_options(yield_per=256))
            async for row in rows:
                yield source_view(row)

    async def repair_source_hash(self, source: EmbeddingSource) -> None:
        if source.persisted_hash == source.source_hash:
            return
        model = KnowledgeItem if source.target_type == "knowledge_item" else Message
        text_column = model.content if model is KnowledgeItem else model.normalized_text
        statement = update(model).where(
            model.id == source.target_id,
            exact_scope_filter(model, source.scope),
            text_column == source.text,
        )
        if model is Message:
            statement = statement.where(Message.learned.is_(True))
        repaired = await self.session.scalar(
            statement.values(embedding_source_hash=source.source_hash)
            .returning(model.id)
            .execution_options(synchronize_session=False)
        )
        if repaired is None:
            raise EmbeddingSourceChanged()

    @staticmethod
    def _current_source(model):
        return and_(
            Embedding.id.is_not(None),
            Embedding.provenance_status == "verified",
            Embedding.source_hash.is_not(None),
            model.embedding_source_hash.is_not(None),
            Embedding.source_hash == model.embedding_source_hash,
        )

    async def coverage(self, scope: Scope, space_id: int | None) -> dict[str, int]:
        eligible = indexed = stale = 0
        for model, target_type in ((KnowledgeItem, "knowledge_item"), (Message, "message")):
            current = self._current_source(model)
            statement = (
                select(
                    func.count(model.id),
                    func.coalesce(func.sum(case((current, 1), else_=0)), 0),
                    func.coalesce(
                        func.sum(
                            case((and_(Embedding.id.is_not(None), not_(current)), 1), else_=0)
                        ),
                        0,
                    ),
                )
                .select_from(model)
                .outerjoin(
                    Embedding,
                    (Embedding.target_id == model.id)
                    & (Embedding.target_type == target_type)
                    & (
                        Embedding.space_id == space_id
                        if space_id is not None
                        else Embedding.id == -1
                    ),
                )
                .where(scope_filter(model, scope))
            )
            if model is Message:
                statement = statement.where(Message.learned.is_(True))
            count, fresh, outdated = (await self.session.execute(statement)).one()
            eligible += int(count)
            indexed += int(fresh)
            stale += int(outdated)
        return {
            "eligible": eligible,
            "indexed": indexed,
            "missing": eligible - indexed,
            "stale": stale,
        }

    async def missing_sources(
        self, scope: Scope, space_id: int | None, limit: int = 32
    ) -> list[EmbeddingSource]:
        if limit < 1:
            raise ValueError("embedding_batch_limit_invalid")
        result: list[EmbeddingSource] = []
        for model, target_type in ((KnowledgeItem, "knowledge_item"), (Message, "message")):
            statement = (
                select(model)
                .outerjoin(
                    Embedding,
                    (Embedding.target_id == model.id)
                    & (Embedding.target_type == target_type)
                    & (
                        Embedding.space_id == space_id
                        if space_id is not None
                        else Embedding.id == -1
                    ),
                )
                .where(scope_filter(model, scope), not_(self._current_source(model)))
                .order_by(model.id)
                .limit(limit - len(result))
            )
            if model is Message:
                statement = statement.where(Message.learned.is_(True))
            rows = (await self.session.scalars(statement)).all()
            result.extend(source_view(row) for row in rows)
            if len(result) >= limit:
                return result
        return result

    async def settings_snapshot(self, scope: Scope) -> str:
        rows = list(
            (await self.session.scalars(select(ScopeSettings).order_by(ScopeSettings.id))).all()
        )
        values: list[Any] = [
            ("scope", row.id, row.version)
            for row in rows
            if scope.contains(Scope(row.chat_id, row.message_thread_id))
            or Scope(row.chat_id, row.message_thread_id).contains(scope)
        ]
        runtime = (
            await self.session.execute(
                select(RuntimeSetting.key, RuntimeSetting.version).order_by(RuntimeSetting.key)
            )
        ).all()
        values.extend(("runtime", key, version) for key, version in runtime)
        return content_hash(json.dumps(values, separators=(",", ":")))

    async def get_job(self, job_id: int) -> EmbeddingIndexJob:
        row = await self.session.get(EmbeddingIndexJob, job_id, populate_existing=True)
        if row is None:
            raise ValueError("embedding_job_not_found")
        return row

    async def list_jobs(self, scope: Scope, limit: int = 50) -> list[EmbeddingIndexJob]:
        return list(
            (
                await self.session.scalars(
                    select(EmbeddingIndexJob)
                    .where(scope_filter(EmbeddingIndexJob, scope))
                    .order_by(EmbeddingIndexJob.id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def claim_job(
        self, job_id: int, token: str, lease_seconds: int
    ) -> EmbeddingIndexJob | None:
        now = utc_now()
        # The singleton write guard also serializes global worker admission across processes
        await self.guard_write()
        busy = await self.session.scalar(
            select(EmbeddingIndexJob.id)
            .where(
                EmbeddingIndexJob.id != job_id,
                EmbeddingIndexJob.status == "running",
                EmbeddingIndexJob.lease_token.is_not(None),
                EmbeddingIndexJob.lease_expires_at > now,
            )
            .limit(1)
        )
        if busy is not None:
            return None
        result = await self.session.scalar(
            update(EmbeddingIndexJob)
            .where(
                EmbeddingIndexJob.id == job_id,
                EmbeddingIndexJob.status.in_(["queued", "running"]),
                or_(
                    EmbeddingIndexJob.lease_token.is_(None),
                    EmbeddingIndexJob.lease_expires_at <= now,
                ),
            )
            .values(
                status="running",
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                lease_fence=EmbeddingIndexJob.lease_fence + 1,
                version=EmbeddingIndexJob.version + 1,
                updated_at=now,
            )
            .returning(EmbeddingIndexJob.id)
            .execution_options(synchronize_session=False)
        )
        return await self.get_job(job_id) if result is not None else None

    async def guarded_job(
        self, job_id: int, token: str, fence: int, lease_seconds: int
    ) -> EmbeddingIndexJob:
        now = utc_now()
        result = await self.session.scalar(
            update(EmbeddingIndexJob)
            .where(
                EmbeddingIndexJob.id == job_id,
                EmbeddingIndexJob.status == "running",
                EmbeddingIndexJob.lease_token == token,
                EmbeddingIndexJob.lease_fence == fence,
                EmbeddingIndexJob.lease_expires_at > now,
            )
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
            .returning(EmbeddingIndexJob.id)
            .execution_options(synchronize_session=False)
        )
        if result is None:
            raise ValueError("embedding_job_lease_lost")
        return await self.get_job(job_id)


def is_expired(value: datetime | None) -> bool:
    return value is None or value.replace(tzinfo=utc_now().tzinfo) <= utc_now()
