from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Select, and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import (
    AIUsage,
    BotEvent,
    Embedding,
    KnowledgeItem,
    LearningPolicy,
    MemoryProfile,
    MemoryProfileScope,
    MemoryReviewItem,
    MemoryScopePermission,
    Message,
    ScopeMetadata,
    ScopeSettings,
    SummaryTemplate,
    SystemPromptTemplate,
)
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.vector import decode_vector, encode_vector


def utc_now() -> datetime:
    return datetime.now(UTC)


def scope_filter(model, scope: Scope):
    if scope.chat_id is None:
        return True
    if scope.thread_id is None:
        return model.chat_id == scope.chat_id
    return and_(model.chat_id == scope.chat_id, model.message_thread_id == scope.thread_id)


@dataclass(frozen=True)
class RetrievedItem:
    target_type: str
    target_id: int
    content: str
    score: float
    source_message_ids: list[int]
    scope: Scope


@dataclass(frozen=True, slots=True)
class EffectiveScopeSettings:
    retrieval_top_k: int
    summary_batch_size: int
    summary_model: str
    system_prompt_template: str
    provider_profile: str
    chat_model: str
    embedding_model: str
    reasoning_level: str
    raw_message_retention_days: int | None
    knowledge_retention_days: int | None
    review_before_compact: bool

    def as_dict(self) -> dict[str, int | str | bool | None]:
        return {
            "retrieval_top_k": self.retrieval_top_k,
            "summary_batch_size": self.summary_batch_size,
            "summary_model": self.summary_model,
            "system_prompt_template": self.system_prompt_template,
            "provider_profile": self.provider_profile,
            "chat_model": self.chat_model,
            "embedding_model": self.embedding_model,
            "reasoning_level": self.reasoning_level,
            "raw_message_retention_days": self.raw_message_retention_days,
            "knowledge_retention_days": self.knowledge_retention_days,
            "review_before_compact": self.review_before_compact,
        }


@dataclass(frozen=True, slots=True)
class SourceReference:
    message_id: int
    label: str
    url: str | None


@dataclass(frozen=True, slots=True)
class MemoryProfileView:
    name: str
    description: str | None
    source_scopes: list[Scope]


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        telegram_message_id: int,
        scope: Scope,
        user_id: int | None,
        username: str | None,
        text: str,
        normalized_text: str,
        learned: bool,
        skip_reason: str | None,
        reply_to_message_id: int | None,
        created_at: datetime,
        chat_title: str | None = None,
        chat_username: str | None = None,
        topic_title: str | None = None,
        message_permalink: str | None = None,
    ) -> tuple[Message, bool]:
        existing = await self.session.scalar(
            select(Message).where(
                Message.chat_id == scope.chat_id,
                Message.message_thread_id.is_(scope.thread_id)
                if scope.thread_id is None
                else Message.message_thread_id == scope.thread_id,
                Message.telegram_message_id == telegram_message_id,
            )
        )
        if existing is not None:
            return existing, False
        message = Message(
            telegram_message_id=telegram_message_id,
            chat_id=int(scope.chat_id or 0),
            message_thread_id=scope.thread_id,
            user_id=user_id,
            username=username,
            chat_title=chat_title,
            chat_username=chat_username,
            topic_title=topic_title,
            message_permalink=message_permalink,
            text=text,
            normalized_text=normalized_text,
            learned=learned,
            skip_reason=skip_reason,
            reply_to_message_id=reply_to_message_id,
            created_at=created_at,
            ingested_at=utc_now(),
        )
        self.session.add(message)
        await self.session.flush()
        return message, True

    async def pending_for_summary(self, scope: Scope, limit: int | None = None) -> list[Message]:
        stmt = (
            select(Message)
            .where(
                Message.learned.is_(True),
                Message.summarized_at.is_(None),
                scope_filter(Message, scope),
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.session.scalars(stmt)).all())

    async def pending_scopes(self, *, idle_before: datetime | None = None) -> list[Scope]:
        stmt = (
            select(Message.chat_id, Message.message_thread_id)
            .where(Message.learned.is_(True), Message.summarized_at.is_(None))
            .group_by(Message.chat_id, Message.message_thread_id)
            .order_by(Message.chat_id, Message.message_thread_id)
        )
        if idle_before is not None:
            stmt = stmt.having(func.max(Message.ingested_at) <= idle_before)
        rows = (await self.session.execute(stmt)).all()
        return [Scope(int(chat_id), thread_id) for chat_id, thread_id in rows]

    async def mark_summarized(self, ids: list[int]) -> None:
        if not ids:
            return
        await self.session.execute(
            update(Message).where(Message.id.in_(ids)).values(summarized_at=utc_now())
        )

    async def get(self, message_id: int) -> Message | None:
        return await self.session.get(Message, message_id)

    async def get_by_telegram_id(self, scope: Scope, telegram_message_id: int) -> Message | None:
        return await self.session.scalar(
            select(Message).where(
                scope_filter(Message, scope), Message.telegram_message_id == telegram_message_id
            )
        )

    async def learned_between_dates(
        self, scope: Scope, start: datetime, end: datetime
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(
                scope_filter(Message, scope),
                Message.learned.is_(True),
                Message.created_at >= start,
                Message.created_at <= end,
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def recent(
        self, scope: Scope, limit: int = 10, *, learned_only: bool = False
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(scope_filter(Message, scope))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        if learned_only:
            stmt = stmt.where(Message.learned.is_(True))
        rows = list((await self.session.scalars(stmt)).all())
        return list(reversed(rows))

    async def source_references(
        self, scope: Scope, message_ids: list[int], metadata: ScopeMetadata | None = None
    ) -> dict[int, SourceReference]:
        if not message_ids or scope.chat_id is None:
            return {}
        unique_ids = sorted(set(message_ids))
        stmt = select(Message).where(
            scope_filter(Message, scope), Message.telegram_message_id.in_(unique_ids)
        )
        rows = list((await self.session.scalars(stmt)).all())
        by_message_id: dict[int, SourceReference] = {}
        fallback_username = metadata.chat_username if metadata else None
        for row in rows:
            username = row.chat_username or fallback_username
            by_message_id[row.telegram_message_id] = SourceReference(
                message_id=row.telegram_message_id,
                label=f"#{row.telegram_message_id}",
                url=row.message_permalink
                or build_telegram_message_url(
                    Scope(row.chat_id, row.message_thread_id), row.telegram_message_id, username
                ),
            )
        for message_id in unique_ids:
            if message_id in by_message_id:
                continue
            by_message_id[message_id] = SourceReference(
                message_id=message_id,
                label=f"#{message_id}",
                url=build_telegram_message_url(scope, message_id, fallback_username),
            )
        return by_message_id

    async def ids_older_than(self, scope: Scope, older_than: datetime) -> list[int]:
        stmt = select(Message.id).where(
            scope_filter(Message, scope), Message.created_at < older_than
        )
        return [int(value) for value in (await self.session.scalars(stmt)).all()]

    async def delete_ids(self, ids: list[int]) -> int:
        if not ids:
            return 0
        await self.session.execute(delete(Message).where(Message.id.in_(ids)))
        return len(ids)

    async def counts(self) -> dict[str, int]:
        total = await self.session.scalar(select(func.count(Message.id)))
        learned = await self.session.scalar(
            select(func.count(Message.id)).where(Message.learned.is_(True))
        )
        skipped = await self.session.scalar(
            select(func.count(Message.id)).where(Message.learned.is_(False))
        )
        return {
            "messages": int(total or 0),
            "learned": int(learned or 0),
            "skipped": int(skipped or 0),
        }

    async def delete_scope(self, scope: Scope) -> int:
        count = await self.session.scalar(
            select(func.count(Message.id)).where(scope_filter(Message, scope))
        )
        await self.session.execute(delete(Message).where(scope_filter(Message, scope)))
        return int(count or 0)


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        scope: Scope,
        kind: str,
        content: str,
        source_message_ids: list[int],
        metadata_json: dict[str, Any] | None = None,
    ) -> KnowledgeItem:
        now = utc_now()
        item = KnowledgeItem(
            chat_id=scope.chat_id,
            message_thread_id=scope.thread_id,
            kind=kind,
            content=content,
            source_message_ids=source_message_ids,
            metadata_json=metadata_json,
            created_at=now,
            updated_at=now,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_scope(self, scope: Scope | None = None) -> list[KnowledgeItem]:
        stmt = select(KnowledgeItem).order_by(
            KnowledgeItem.chat_id, KnowledgeItem.message_thread_id, KnowledgeItem.created_at
        )
        if scope is not None:
            stmt = stmt.where(scope_filter(KnowledgeItem, scope))
        return list((await self.session.scalars(stmt)).all())

    async def get(self, item_id: int) -> KnowledgeItem | None:
        return await self.session.get(KnowledgeItem, item_id)

    async def list_manual_notes(
        self, scope: Scope | None = None, tag: str | None = None
    ) -> list[KnowledgeItem]:
        stmt = (
            select(KnowledgeItem)
            .where(KnowledgeItem.kind == "manual_note")
            .order_by(KnowledgeItem.created_at.asc(), KnowledgeItem.id.asc())
        )
        if scope is not None:
            stmt = stmt.where(scope_filter(KnowledgeItem, scope))
        rows = list((await self.session.scalars(stmt)).all())
        if tag is None:
            return rows
        normalized = tag.strip().lower()
        return [
            row
            for row in rows
            if normalized
            in {str(value).strip().lower() for value in (row.metadata_json or {}).get("tags", [])}
        ]

    async def update_manual_note(
        self,
        item_id: int,
        *,
        scope: Scope,
        content: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> KnowledgeItem | None:
        item = await self.session.scalar(
            select(KnowledgeItem).where(
                KnowledgeItem.id == item_id,
                KnowledgeItem.kind == "manual_note",
                scope_filter(KnowledgeItem, scope),
            )
        )
        if item is None:
            return None
        if content is not None:
            item.content = content
        metadata = dict(item.metadata_json or {})
        if metadata_updates:
            for key, value in metadata_updates.items():
                if value is None:
                    metadata.pop(key, None)
                else:
                    metadata[key] = value
        item.metadata_json = metadata
        item.updated_at = utc_now()
        await self.session.flush()
        return item

    async def delete_manual_note(self, item_id: int, *, scope: Scope) -> bool:
        item = await self.session.scalar(
            select(KnowledgeItem).where(
                KnowledgeItem.id == item_id,
                KnowledgeItem.kind == "manual_note",
                scope_filter(KnowledgeItem, scope),
            )
        )
        if item is None:
            return False
        await self.session.delete(item)
        await self.session.flush()
        return True

    async def ids_older_than(self, scope: Scope, older_than: datetime) -> list[int]:
        stmt = select(KnowledgeItem.id).where(
            scope_filter(KnowledgeItem, scope), KnowledgeItem.created_at < older_than
        )
        return [int(value) for value in (await self.session.scalars(stmt)).all()]

    async def delete_ids(self, ids: list[int]) -> int:
        if not ids:
            return 0
        await self.session.execute(delete(KnowledgeItem).where(KnowledgeItem.id.in_(ids)))
        return len(ids)

    async def count(self) -> int:
        return int(await self.session.scalar(select(func.count(KnowledgeItem.id))) or 0)

    async def delete_scope(self, scope: Scope) -> int:
        count = await self.session.scalar(
            select(func.count(KnowledgeItem.id)).where(scope_filter(KnowledgeItem, scope))
        )
        await self.session.execute(delete(KnowledgeItem).where(scope_filter(KnowledgeItem, scope)))
        return int(count or 0)


class EmbeddingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        target_type: str,
        target_id: int,
        scope: Scope,
        provider: str,
        model: str,
        vector: list[float],
    ) -> Embedding:
        existing = await self.session.scalar(
            select(Embedding).where(
                Embedding.target_type == target_type,
                Embedding.target_id == target_id,
                Embedding.provider == provider,
                Embedding.model == model,
            )
        )
        if existing is not None:
            return existing
        embedding = Embedding(
            target_type=target_type,
            target_id=target_id,
            chat_id=scope.chat_id,
            message_thread_id=scope.thread_id,
            provider=provider,
            model=model,
            dimension=len(vector),
            vector=encode_vector(vector),
            created_at=utc_now(),
        )
        self.session.add(embedding)
        await self.session.flush()
        return embedding

    async def list_for_retrieval(self, scope: Scope) -> list[tuple[Embedding, list[float]]]:
        stmt: Select[tuple[Embedding]] = select(Embedding).where(scope_filter(Embedding, scope))
        rows = list((await self.session.scalars(stmt)).all())
        return [(row, decode_vector(row.vector)) for row in rows]

    async def count(self) -> int:
        return int(await self.session.scalar(select(func.count(Embedding.id))) or 0)

    async def delete_targets(self, *, target_type: str, target_ids: list[int]) -> int:
        if not target_ids:
            return 0
        count = await self.session.scalar(
            select(func.count(Embedding.id)).where(
                Embedding.target_type == target_type, Embedding.target_id.in_(target_ids)
            )
        )
        await self.session.execute(
            delete(Embedding).where(
                Embedding.target_type == target_type, Embedding.target_id.in_(target_ids)
            )
        )
        return int(count or 0)

    async def delete_scope(self, scope: Scope) -> int:
        count = await self.session.scalar(
            select(func.count(Embedding.id)).where(scope_filter(Embedding, scope))
        )
        await self.session.execute(delete(Embedding).where(scope_filter(Embedding, scope)))
        return int(count or 0)


class PolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set_policy(
        self,
        *,
        policy_type: str,
        enabled: bool,
        scope: Scope | None = None,
        user_id: int | None = None,
        key: str | None = None,
        value: dict[str, Any] | None = None,
        updated_by: int | None = None,
    ) -> LearningPolicy:
        chat_id = scope.chat_id if scope else None
        thread_id = scope.thread_id if scope else None
        stmt = select(LearningPolicy).where(
            LearningPolicy.policy_type == policy_type,
            _nullable_eq(LearningPolicy.chat_id, chat_id),
            _nullable_eq(LearningPolicy.message_thread_id, thread_id),
            _nullable_eq(LearningPolicy.user_id, user_id),
            _nullable_eq(LearningPolicy.key, key),
        )
        rows = list((await self.session.scalars(stmt.order_by(LearningPolicy.id.asc()))).all())
        if not rows:
            row = LearningPolicy(
                policy_type=policy_type,
                chat_id=chat_id,
                message_thread_id=thread_id,
                user_id=user_id,
                key=key,
                enabled=enabled,
                value=value,
                updated_by=updated_by,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row = rows[0]
            row.enabled = enabled
            row.value = value
            row.updated_by = updated_by
            row.updated_at = utc_now()
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def list_policies(self) -> list[LearningPolicy]:
        return list((await self.session.scalars(select(LearningPolicy))).all())

    async def get_policy(
        self,
        *,
        policy_type: str,
        scope: Scope | None = None,
        user_id: int | None = None,
        key: str | None = None,
    ) -> LearningPolicy | None:
        chat_id = scope.chat_id if scope else None
        thread_id = scope.thread_id if scope else None
        return await self.session.scalar(
            select(LearningPolicy).where(
                LearningPolicy.policy_type == policy_type,
                _nullable_eq(LearningPolicy.chat_id, chat_id),
                _nullable_eq(LearningPolicy.message_thread_id, thread_id),
                _nullable_eq(LearningPolicy.user_id, user_id),
                _nullable_eq(LearningPolicy.key, key),
            )
        )


class ScopeMetadataRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        scope: Scope,
        *,
        chat_title: str | None = None,
        topic_title: str | None = None,
        chat_username: str | None = None,
    ) -> ScopeMetadata | None:
        if scope.chat_id is None:
            return None
        rows = await self._rows(scope)
        if not rows:
            row = ScopeMetadata(
                chat_id=scope.chat_id,
                message_thread_id=scope.thread_id,
                chat_title=chat_title,
                topic_title=topic_title,
                chat_username=chat_username,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row = rows[0]
            if chat_title:
                row.chat_title = chat_title
            if topic_title:
                row.topic_title = topic_title
            if chat_username:
                row.chat_username = chat_username
            row.updated_at = utc_now()
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def get(self, scope: Scope) -> ScopeMetadata | None:
        rows = await self._rows(scope)
        return rows[0] if rows else None

    async def get_effective(self, scope: Scope) -> ScopeMetadata | None:
        exact = await self.get(scope)
        if exact is not None or scope.chat_id is None or scope.thread_id is None:
            return exact
        return await self.get(Scope(scope.chat_id, None))

    async def list_all(self) -> dict[str, ScopeMetadata]:
        rows = list((await self.session.scalars(select(ScopeMetadata))).all())
        return {Scope(row.chat_id, row.message_thread_id).key: row for row in rows}

    async def _rows(self, scope: Scope) -> list[ScopeMetadata]:
        if scope.chat_id is None:
            return []
        stmt = select(ScopeMetadata).where(
            ScopeMetadata.chat_id == scope.chat_id,
            _nullable_eq(ScopeMetadata.message_thread_id, scope.thread_id),
        )
        return list((await self.session.scalars(stmt.order_by(ScopeMetadata.id.asc()))).all())


class ScopeSettingsRepository:
    SETTING_FIELDS: ClassVar[set[str]] = {
        "retrieval_top_k",
        "summary_batch_size",
        "summary_model",
        "system_prompt_template",
        "provider_profile",
        "chat_model",
        "embedding_model",
        "reasoning_level",
        "raw_message_retention_days",
        "knowledge_retention_days",
        "review_before_compact",
    }

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set_settings(
        self, scope: Scope, updates: dict[str, int | str | bool | None], updated_by: int | None
    ) -> ScopeSettings:
        unknown = set(updates) - self.SETTING_FIELDS
        if unknown:
            raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
        rows = await self._rows(scope)
        if not rows:
            row = ScopeSettings(
                chat_id=scope.chat_id,
                message_thread_id=scope.thread_id,
                updated_by=updated_by,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row = rows[0]
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        for key, value in updates.items():
            setattr(row, key, value)
        row.updated_by = updated_by
        row.updated_at = utc_now()
        await self.session.flush()
        return row

    async def effective(
        self, scope: Scope, defaults: EffectiveScopeSettings
    ) -> EffectiveScopeSettings:
        values = defaults.as_dict()
        for candidate in _settings_resolution_order(scope):
            row = await self.get(candidate)
            if row is None:
                continue
            for key in self.SETTING_FIELDS:
                value = getattr(row, key)
                if value is not None:
                    values[key] = value
        return EffectiveScopeSettings(
            retrieval_top_k=_required_int(values["retrieval_top_k"]),
            summary_batch_size=_required_int(values["summary_batch_size"]),
            summary_model=_required_str(values["summary_model"]),
            system_prompt_template=_required_str(values["system_prompt_template"]),
            provider_profile=_required_str(values["provider_profile"]),
            chat_model=_required_str(values["chat_model"]),
            embedding_model=_required_str(values["embedding_model"]),
            reasoning_level=_required_str(values["reasoning_level"]),
            raw_message_retention_days=_optional_int(values["raw_message_retention_days"]),
            knowledge_retention_days=_optional_int(values["knowledge_retention_days"]),
            review_before_compact=bool(values["review_before_compact"]),
        )

    async def get(self, scope: Scope) -> ScopeSettings | None:
        rows = await self._rows(scope)
        return rows[0] if rows else None

    async def _rows(self, scope: Scope) -> list[ScopeSettings]:
        stmt = select(ScopeSettings).where(
            _nullable_eq(ScopeSettings.chat_id, scope.chat_id),
            _nullable_eq(ScopeSettings.message_thread_id, scope.thread_id),
        )
        return list((await self.session.scalars(stmt.order_by(ScopeSettings.id.asc()))).all())


class MemoryReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self, *, scope: Scope, kind: str, content: str, source_message_ids: list[int]
    ) -> MemoryReviewItem:
        if scope.chat_id is None:
            raise ValueError("review items require a concrete scope")
        item = MemoryReviewItem(
            chat_id=scope.chat_id,
            message_thread_id=scope.thread_id,
            kind=kind,
            content=content,
            source_message_ids=source_message_ids,
            status="pending",
            created_at=utc_now(),
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def pending(self, scope: Scope | None = None) -> list[MemoryReviewItem]:
        stmt = select(MemoryReviewItem).where(MemoryReviewItem.status == "pending")
        if scope is not None:
            stmt = stmt.where(scope_filter(MemoryReviewItem, scope))
        stmt = stmt.order_by(MemoryReviewItem.created_at.asc(), MemoryReviewItem.id.asc())
        return list((await self.session.scalars(stmt)).all())

    async def get_pending(self, review_id: int) -> MemoryReviewItem | None:
        return await self.session.scalar(
            select(MemoryReviewItem).where(
                MemoryReviewItem.id == review_id, MemoryReviewItem.status == "pending"
            )
        )

    async def set_status(
        self, item: MemoryReviewItem, *, status: str, reviewed_by: int | None
    ) -> None:
        item.status = status
        item.reviewed_by = reviewed_by
        item.reviewed_at = utc_now()
        await self.session.flush()

    async def count_pending(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count(MemoryReviewItem.id)).where(MemoryReviewItem.status == "pending")
            )
            or 0
        )


class MemoryProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_profile(
        self,
        *,
        name: str,
        source_scopes: list[Scope],
        description: str | None = None,
        updated_by: int | None = None,
    ) -> MemoryProfileView:
        clean_name = normalize_profile_name(name)
        now = utc_now()
        rows = list(
            (
                await self.session.scalars(
                    select(MemoryProfile)
                    .where(MemoryProfile.name == clean_name)
                    .order_by(MemoryProfile.id.asc())
                )
            ).all()
        )
        if not rows:
            profile = MemoryProfile(
                name=clean_name,
                description=description,
                created_by=updated_by,
                created_at=now,
                updated_at=now,
            )
            self.session.add(profile)
            await self.session.flush()
        else:
            profile = rows[0]
            profile.description = description if description is not None else profile.description
            profile.updated_at = now
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.execute(
            delete(MemoryProfileScope).where(MemoryProfileScope.profile_id == profile.id)
        )
        for source_scope in dedupe_scopes(source_scopes):
            if source_scope.chat_id is None:
                raise ValueError("profile source scopes must be concrete")
            self.session.add(
                MemoryProfileScope(
                    profile_id=profile.id,
                    chat_id=source_scope.chat_id,
                    message_thread_id=source_scope.thread_id,
                )
            )
        await self.session.flush()
        return await self.get_profile(clean_name) or MemoryProfileView(clean_name, description, [])

    async def get_profile(self, name: str) -> MemoryProfileView | None:
        clean_name = normalize_profile_name(name)
        profile = await self.session.scalar(
            select(MemoryProfile).where(MemoryProfile.name == clean_name)
        )
        if profile is None:
            return None
        rows = list(
            (
                await self.session.scalars(
                    select(MemoryProfileScope)
                    .where(MemoryProfileScope.profile_id == profile.id)
                    .order_by(
                        MemoryProfileScope.chat_id.asc(), MemoryProfileScope.message_thread_id.asc()
                    )
                )
            ).all()
        )
        return MemoryProfileView(
            name=profile.name,
            description=profile.description,
            source_scopes=[Scope(row.chat_id, row.message_thread_id) for row in rows],
        )

    async def list_profiles(self) -> list[MemoryProfileView]:
        profiles = list(
            (
                await self.session.scalars(select(MemoryProfile).order_by(MemoryProfile.name.asc()))
            ).all()
        )
        views: list[MemoryProfileView] = []
        for profile in profiles:
            view = await self.get_profile(profile.name)
            if view is not None:
                views.append(view)
        return views

    async def set_permission(
        self,
        *,
        destination: Scope,
        source: Scope,
        can_query: bool | None = None,
        can_learn: bool | None = None,
        updated_by: int | None = None,
    ) -> MemoryScopePermission:
        if destination.chat_id is None or source.chat_id is None:
            raise ValueError(
                "cross-scope permissions require concrete source and destination scopes"
            )
        rows = list(
            (
                await self.session.scalars(
                    select(MemoryScopePermission)
                    .where(
                        MemoryScopePermission.destination_chat_id == destination.chat_id,
                        _nullable_eq(
                            MemoryScopePermission.destination_thread_id, destination.thread_id
                        ),
                        MemoryScopePermission.source_chat_id == source.chat_id,
                        _nullable_eq(MemoryScopePermission.source_thread_id, source.thread_id),
                    )
                    .order_by(MemoryScopePermission.id.asc())
                )
            ).all()
        )
        if not rows:
            row = MemoryScopePermission(
                destination_chat_id=destination.chat_id,
                destination_thread_id=destination.thread_id,
                source_chat_id=source.chat_id,
                source_thread_id=source.thread_id,
                can_query=bool(can_query),
                can_learn=bool(can_learn),
                updated_by=updated_by,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row = rows[0]
            if can_query is not None:
                row.can_query = can_query
            if can_learn is not None:
                row.can_learn = can_learn
            row.updated_by = updated_by
            row.updated_at = utc_now()
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def can_query(self, *, destination: Scope, source: Scope) -> bool:
        if destination.chat_id is None or source.chat_id is None:
            return False
        row = await self.session.scalar(
            select(MemoryScopePermission).where(
                MemoryScopePermission.destination_chat_id == destination.chat_id,
                _nullable_eq(MemoryScopePermission.destination_thread_id, destination.thread_id),
                MemoryScopePermission.source_chat_id == source.chat_id,
                _nullable_eq(MemoryScopePermission.source_thread_id, source.thread_id),
                MemoryScopePermission.can_query.is_(True),
            )
        )
        return row is not None


class SummaryTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self, *, name: str, prompt: str, updated_by: int | None = None
    ) -> SummaryTemplate:
        clean_name = normalize_profile_name(name)
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("summary template prompt cannot be empty")
        rows = list(
            (
                await self.session.scalars(
                    select(SummaryTemplate)
                    .where(SummaryTemplate.name == clean_name)
                    .order_by(SummaryTemplate.id.asc())
                )
            ).all()
        )
        now = utc_now()
        if not rows:
            row = SummaryTemplate(
                name=clean_name,
                prompt=clean_prompt,
                created_by=updated_by,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row = rows[0]
            row.prompt = clean_prompt
            row.updated_at = now
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def get_prompt(self, name: str) -> str | None:
        clean_name = normalize_profile_name(name)
        row = await self.session.scalar(
            select(SummaryTemplate).where(SummaryTemplate.name == clean_name)
        )
        return row.prompt if row is not None else None

    async def list_templates(self) -> list[SummaryTemplate]:
        return list(
            (
                await self.session.scalars(
                    select(SummaryTemplate).order_by(SummaryTemplate.name.asc())
                )
            ).all()
        )


class SystemPromptTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self, *, name: str, prompt: str, version: str, updated_by: int | None = None
    ) -> SystemPromptTemplate:
        clean_name = normalize_profile_name(name)
        clean_prompt = prompt.strip()
        clean_version = version.strip()
        if not clean_prompt:
            raise ValueError("system prompt template cannot be empty")
        if not clean_version:
            raise ValueError("system prompt version cannot be empty")
        rows = list(
            (
                await self.session.scalars(
                    select(SystemPromptTemplate)
                    .where(SystemPromptTemplate.name == clean_name)
                    .order_by(SystemPromptTemplate.id.asc())
                )
            ).all()
        )
        now = utc_now()
        if not rows:
            row = SystemPromptTemplate(
                name=clean_name,
                prompt=clean_prompt,
                version=clean_version,
                created_by=updated_by,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row = rows[0]
            row.prompt = clean_prompt
            row.version = clean_version
            row.updated_at = now
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def get(self, name: str) -> SystemPromptTemplate | None:
        clean_name = normalize_profile_name(name)
        return await self.session.scalar(
            select(SystemPromptTemplate).where(SystemPromptTemplate.name == clean_name)
        )

    async def list_templates(self) -> list[SystemPromptTemplate]:
        return list(
            (
                await self.session.scalars(
                    select(SystemPromptTemplate).order_by(SystemPromptTemplate.name.asc())
                )
            ).all()
        )


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        event_type: str,
        scope: Scope | None = None,
        user_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> BotEvent:
        event = BotEvent(
            event_type=event_type,
            chat_id=scope.chat_id if scope else None,
            message_thread_id=scope.thread_id if scope else None,
            user_id=user_id,
            payload=payload or {},
            created_at=utc_now(),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def add_ai_usage(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> AIUsage:
        usage = AIUsage(
            provider=provider,
            model=model,
            operation=operation,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            error=error,
            metadata_json=metadata_json,
            created_at=utc_now(),
        )
        self.session.add(usage)
        await self.session.flush()
        return usage

    async def count_events(self) -> int:
        return int(await self.session.scalar(select(func.count(BotEvent.id))) or 0)


def build_telegram_message_url(
    scope: Scope, telegram_message_id: int, chat_username: str | None = None
) -> str | None:
    if scope.chat_id is None:
        return None
    if chat_username:
        username = chat_username.removeprefix("@")
        return f"https://t.me/{username}/{telegram_message_id}"
    chat_id = str(scope.chat_id)
    if chat_id.startswith("-100"):
        internal_id = chat_id.removeprefix("-100")
        if scope.thread_id is not None:
            return f"https://t.me/c/{internal_id}/{scope.thread_id}/{telegram_message_id}"
        return f"https://t.me/c/{internal_id}/{telegram_message_id}"
    return None


def _settings_resolution_order(scope: Scope) -> list[Scope]:
    scopes = [Scope.global_scope()]
    if scope.chat_id is not None:
        scopes.append(Scope(scope.chat_id, None))
        if scope.thread_id is not None:
            scopes.append(scope)
    return scopes


def _required_int(value: int | str | bool | None) -> int:
    if value is None:
        raise ValueError("required integer setting is missing")
    return int(value)


def _required_str(value: int | str | bool | None) -> str:
    if value is None:
        raise ValueError("required string setting is missing")
    return str(value)


def _optional_int(value: int | str | bool | None) -> int | None:
    if value is None:
        return None
    return int(value)


def _nullable_eq(column, value):
    if value is None:
        return column.is_(None)
    return column == value


def normalize_profile_name(name: str) -> str:
    clean = name.strip().lower()
    if not clean or len(clean) > 64:
        raise ValueError("name must be 1-64 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    if any(char not in allowed for char in clean):
        raise ValueError("name may contain only letters, numbers, underscore and dash")
    return clean


def dedupe_scopes(scopes: list[Scope]) -> list[Scope]:
    seen: set[str] = set()
    out: list[Scope] = []
    for scope in scopes:
        if scope.key in seen:
            continue
        seen.add(scope.key)
        out.append(scope)
    return out
