from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Integer, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import (
    AdminAlert,
    AIProviderCredential,
    AIUsage,
    BotEvent,
    ChatAdminGrant,
    Embedding,
    KnowledgeItem,
    ManagedChat,
    MemoryReviewItem,
    Message,
)
from pzp_ai_bot.memory.scope import Scope

from .shared import scope_filter


@dataclass(frozen=True, slots=True)
class ScopedOverview:
    messages: int
    learned: int
    skipped: int
    knowledge_items: int
    embeddings: int
    pending_reviews: int
    audit_events: int
    ai_total: int
    ai_errors: int
    ai_average_latency_ms: int | None


@dataclass(frozen=True, slots=True)
class TimeseriesPoint:
    day: date
    messages: int = 0
    learned: int = 0
    skipped: int = 0
    knowledge_items: int = 0
    ai_calls: int = 0
    ai_errors: int = 0


class InsightRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def scoped_overview(self, scope: Scope | None) -> ScopedOverview:
        def criteria(model, *extra):
            values = list(extra)
            if scope is not None:
                values.append(scope_filter(model, scope))
            return values

        stmt = select(
            select(func.count(Message.id))
            .where(*criteria(Message))
            .scalar_subquery()
            .label("messages"),
            select(func.count(Message.id))
            .where(*criteria(Message, Message.learned.is_(True)))
            .scalar_subquery()
            .label("learned"),
            select(func.count(Message.id))
            .where(*criteria(Message, Message.learned.is_(False)))
            .scalar_subquery()
            .label("skipped"),
            select(func.count(KnowledgeItem.id))
            .where(*criteria(KnowledgeItem))
            .scalar_subquery()
            .label("knowledge_items"),
            select(func.count(Embedding.id))
            .where(*criteria(Embedding))
            .scalar_subquery()
            .label("embeddings"),
            select(func.count(MemoryReviewItem.id))
            .where(*criteria(MemoryReviewItem, MemoryReviewItem.status == "pending"))
            .scalar_subquery()
            .label("pending_reviews"),
            select(func.count(BotEvent.id))
            .where(*criteria(BotEvent))
            .scalar_subquery()
            .label("audit_events"),
            select(func.count(AIUsage.id))
            .where(*criteria(AIUsage))
            .scalar_subquery()
            .label("ai_total"),
            select(func.count(AIUsage.id))
            .where(*criteria(AIUsage, AIUsage.error.is_not(None)))
            .scalar_subquery()
            .label("ai_errors"),
            select(func.avg(AIUsage.latency_ms))
            .where(*criteria(AIUsage))
            .scalar_subquery()
            .label("ai_average_latency_ms"),
        )
        row = (await self.session.execute(stmt)).one()
        return ScopedOverview(
            messages=int(row.messages or 0),
            learned=int(row.learned or 0),
            skipped=int(row.skipped or 0),
            knowledge_items=int(row.knowledge_items or 0),
            embeddings=int(row.embeddings or 0),
            pending_reviews=int(row.pending_reviews or 0),
            audit_events=int(row.audit_events or 0),
            ai_total=int(row.ai_total or 0),
            ai_errors=int(row.ai_errors or 0),
            ai_average_latency_ms=(
                int(row.ai_average_latency_ms) if row.ai_average_latency_ms is not None else None
            ),
        )

    async def timeseries(
        self, scope: Scope | None, *, days: int, now: datetime | None = None
    ) -> list[TimeseriesPoint]:
        current = (now or datetime.now(UTC)).date()
        first_day = current - timedelta(days=days - 1)
        cutoff = datetime.combine(first_day, datetime.min.time(), tzinfo=UTC)
        points = {
            first_day + timedelta(days=index): TimeseriesPoint(
                day=first_day + timedelta(days=index)
            )
            for index in range(days)
        }

        message_stmt = (
            select(
                func.date(Message.created_at),
                func.count(Message.id),
                func.sum(cast(Message.learned, Integer())),
            )
            .where(Message.created_at >= cutoff)
            .group_by(func.date(Message.created_at))
        )
        knowledge_stmt = (
            select(func.date(KnowledgeItem.created_at), func.count(KnowledgeItem.id))
            .where(KnowledgeItem.created_at >= cutoff)
            .group_by(func.date(KnowledgeItem.created_at))
        )
        usage_stmt = (
            select(
                func.date(AIUsage.created_at),
                func.count(AIUsage.id),
                func.sum(case((AIUsage.error.is_not(None), 1), else_=0)),
            )
            .where(AIUsage.created_at >= cutoff)
            .group_by(func.date(AIUsage.created_at))
        )
        if scope is not None:
            message_stmt = message_stmt.where(scope_filter(Message, scope))
            knowledge_stmt = knowledge_stmt.where(scope_filter(KnowledgeItem, scope))
            usage_stmt = usage_stmt.where(scope_filter(AIUsage, scope))

        for raw_day, total, learned in (await self.session.execute(message_stmt)).all():
            day = date.fromisoformat(str(raw_day))
            if day in points:
                learned_count = int(learned or 0)
                points[day] = TimeseriesPoint(
                    day=day,
                    messages=int(total or 0),
                    learned=learned_count,
                    skipped=int(total or 0) - learned_count,
                )
        for raw_day, total in (await self.session.execute(knowledge_stmt)).all():
            day = date.fromisoformat(str(raw_day))
            if day in points:
                point = points[day]
                points[day] = replace(point, knowledge_items=int(total or 0))
        for raw_day, total, errors in (await self.session.execute(usage_stmt)).all():
            day = date.fromisoformat(str(raw_day))
            if day in points:
                point = points[day]
                points[day] = replace(point, ai_calls=int(total or 0), ai_errors=int(errors or 0))
        return [points[day] for day in sorted(points)]

    async def system_counts(self, *, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now(UTC)
        managed_chats = {
            str(status): int(count or 0)
            for status, count in (
                await self.session.execute(
                    select(ManagedChat.status, func.count(ManagedChat.chat_id)).group_by(
                        ManagedChat.status
                    )
                )
            ).all()
        }
        delegated_admins = int(
            await self.session.scalar(
                select(func.count(ChatAdminGrant.id)).where(ChatAdminGrant.revoked_at.is_(None))
            )
            or 0
        )
        credential_total, credential_eligible = (
            await self.session.execute(
                select(
                    func.count(AIProviderCredential.id),
                    func.sum(
                        case(
                            (
                                (
                                    (AIProviderCredential.lifecycle_state == "enabled")
                                    & (AIProviderCredential.health_state != "disabled")
                                    & (
                                        AIProviderCredential.cooldown_until.is_(None)
                                        | (AIProviderCredential.cooldown_until <= current)
                                    )
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                ).where(AIProviderCredential.lifecycle_state != "deleted")
            )
        ).one()
        alerts = {
            str(state): int(count or 0)
            for state, count in (
                await self.session.execute(
                    select(AdminAlert.state, func.count(AdminAlert.id)).group_by(AdminAlert.state)
                )
            ).all()
        }
        revision = await self.session.scalar(text("select version_num from alembic_version"))
        return {
            "managed_chats": managed_chats,
            "delegated_chat_admins": delegated_admins,
            "credentials": {
                "eligible": int(credential_eligible or 0),
                "total": int(credential_total or 0),
            },
            "alerts": alerts,
            "schema_revision": str(revision or "unknown"),
        }
