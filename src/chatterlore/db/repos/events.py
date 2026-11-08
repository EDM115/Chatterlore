from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatterlore.db.models import AIUsage, BotEvent
from chatterlore.memory.scope import Scope

from .shared import AIUsageStats, scope_filter, utc_now


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
        scope: Scope | None,
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
            chat_id=scope.chat_id if scope else None,
            message_thread_id=scope.thread_id if scope else None,
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

    async def list_events(
        self, *, limit: int = 50, event_type: str | None = None, scope: Scope | None = None
    ) -> list[BotEvent]:
        stmt = (
            select(BotEvent).order_by(BotEvent.created_at.desc(), BotEvent.id.desc()).limit(limit)
        )
        if event_type is not None:
            stmt = stmt.where(BotEvent.event_type == event_type)
        if scope is not None:
            stmt = stmt.where(scope_filter(BotEvent, scope))
        return list((await self.session.scalars(stmt)).all())

    async def list_events_page(
        self,
        *,
        limit: int,
        before_id: int | None = None,
        event_type: str | None = None,
        scope: Scope | None = None,
    ) -> list[BotEvent]:
        if limit <= 0:
            raise ValueError("event page limit must be positive")
        stmt = select(BotEvent).order_by(BotEvent.id.desc()).limit(limit)
        if before_id is not None:
            stmt = stmt.where(BotEvent.id < before_id)
        if event_type is not None:
            stmt = stmt.where(BotEvent.event_type == event_type)
        if scope is not None:
            stmt = stmt.where(scope_filter(BotEvent, scope))
        return list((await self.session.scalars(stmt)).all())

    async def list_ai_usage_page(
        self, *, limit: int, before_id: int | None = None, scope: Scope | None = None
    ) -> list[AIUsage]:
        if limit <= 0:
            raise ValueError("AI usage page limit must be positive")
        stmt = select(AIUsage).order_by(AIUsage.id.desc()).limit(limit)
        if before_id is not None:
            stmt = stmt.where(AIUsage.id < before_id)
        if scope is not None:
            stmt = stmt.where(scope_filter(AIUsage, scope))
        return list((await self.session.scalars(stmt)).all())

    async def count_events(self, scope: Scope | None = None) -> int:
        stmt = select(func.count(BotEvent.id))
        if scope is not None:
            stmt = stmt.where(scope_filter(BotEvent, scope))
        return int(await self.session.scalar(stmt) or 0)

    async def ai_usage_stats(self, scope: Scope | None = None) -> AIUsageStats:
        aggregate_stmt = select(
            func.count(AIUsage.id),
            func.sum(case((AIUsage.error.is_not(None), 1), else_=0)),
            func.avg(AIUsage.latency_ms),
        )
        operations_stmt = (
            select(AIUsage.operation, func.count(AIUsage.id))
            .group_by(AIUsage.operation)
            .order_by(AIUsage.operation.asc())
        )
        if scope is not None:
            criterion = scope_filter(AIUsage, scope)
            aggregate_stmt = aggregate_stmt.where(criterion)
            operations_stmt = operations_stmt.where(criterion)
        total, errors, average = (await self.session.execute(aggregate_stmt)).one()
        operation_rows = (await self.session.execute(operations_stmt)).all()
        return AIUsageStats(
            total=int(total or 0),
            errors=int(errors or 0),
            average_latency_ms=int(average) if average is not None else None,
            operations={str(operation): int(count or 0) for operation, count in operation_rows},
        )
