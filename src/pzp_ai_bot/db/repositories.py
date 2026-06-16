from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import (
    AIUsage,
    BotEvent,
    Embedding,
    KnowledgeItem,
    LearningPolicy,
    Message,
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
    ) -> Message:
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
            return existing
        message = Message(
            telegram_message_id=telegram_message_id,
            chat_id=int(scope.chat_id or 0),
            message_thread_id=scope.thread_id,
            user_id=user_id,
            username=username,
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
        return message

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

    async def mark_summarized(self, ids: list[int]) -> None:
        if not ids:
            return
        await self.session.execute(
            update(Message).where(Message.id.in_(ids)).values(summarized_at=utc_now())
        )

    async def recent(self, scope: Scope, limit: int = 10) -> list[Message]:
        stmt = (
            select(Message)
            .where(scope_filter(Message, scope))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.scalars(stmt)).all())
        return list(reversed(rows))

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
        self, *, scope: Scope, kind: str, content: str, source_message_ids: list[int]
    ) -> KnowledgeItem:
        now = utc_now()
        item = KnowledgeItem(
            chat_id=scope.chat_id,
            message_thread_id=scope.thread_id,
            kind=kind,
            content=content,
            source_message_ids=source_message_ids,
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
        row = await self.session.scalar(stmt)
        if row is None:
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
            row.enabled = enabled
            row.value = value
            row.updated_by = updated_by
            row.updated_at = utc_now()
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
    ) -> AIUsage:
        usage = AIUsage(
            provider=provider,
            model=model,
            operation=operation,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            error=error,
            created_at=utc_now(),
        )
        self.session.add(usage)
        await self.session.flush()
        return usage

    async def count_events(self) -> int:
        return int(await self.session.scalar(select(func.count(BotEvent.id))) or 0)


def _nullable_eq(column, value):
    if value is None:
        return column.is_(None)
    return column == value
