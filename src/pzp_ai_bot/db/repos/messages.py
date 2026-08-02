from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, cast, delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import Message, ScopeMetadata
from pzp_ai_bot.memory.scope import Scope

from .shared import (
    MessageScopeCounts,
    SourceReference,
    build_telegram_message_url,
    exact_scope_filter,
    scope_filter,
    utc_now,
)


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_logical(self, scope: Scope, telegram_message_id: int) -> Message | None:
        chat_id = int(scope.chat_id or 0)
        return await self.session.scalar(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.message_thread_id.is_(scope.thread_id)
                if scope.thread_id is None
                else Message.message_thread_id == scope.thread_id,
                Message.telegram_message_id == telegram_message_id,
            )
        )

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
        chat_id = int(scope.chat_id or 0)
        statement = (
            sqlite_insert(Message)
            .values(
                telegram_message_id=telegram_message_id,
                chat_id=chat_id,
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
            .on_conflict_do_nothing()
            .returning(Message)
        )
        inserted = await self.session.scalar(statement)
        if inserted is not None:
            return inserted, True
        existing = await self.get_logical(scope, telegram_message_id)
        if existing is None:
            raise RuntimeError("message insert conflicted without a logical existing row")
        return existing, False

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

    async def claim_for_summary(
        self,
        scope: Scope,
        *,
        limit: int,
        token: str,
        now: datetime,
        stale_before: datetime,
        created_before: datetime | None = None,
    ) -> list[Message]:
        if limit <= 0:
            raise ValueError("summary claim limit must be positive")
        eligible = [
            Message.learned.is_(True),
            Message.summarized_at.is_(None),
            scope_filter(Message, scope),
            or_(
                Message.summary_claim_token.is_(None),
                Message.summary_claimed_at.is_(None),
                Message.summary_claimed_at < stale_before,
            ),
        ]
        if created_before is not None:
            eligible.append(Message.created_at <= created_before)
        candidate_ids = (
            select(Message.id)
            .where(*eligible)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        )
        await self.session.execute(
            update(Message)
            .where(Message.id.in_(candidate_ids), *eligible)
            .values(summary_claim_token=token, summary_claimed_at=now)
        )
        return list(
            (
                await self.session.scalars(
                    select(Message)
                    .where(Message.summary_claim_token == token, Message.summarized_at.is_(None))
                    .order_by(Message.created_at.asc(), Message.id.asc())
                )
            ).all()
        )

    async def claimed_by_token(self, token: str) -> list[Message]:
        return list(
            (
                await self.session.scalars(
                    select(Message)
                    .where(Message.summary_claim_token == token, Message.summarized_at.is_(None))
                    .order_by(Message.created_at.asc(), Message.id.asc())
                )
            ).all()
        )

    async def stale_summary_claim_ids(
        self, scope: Scope, *, stale_before: datetime, created_before: datetime | None = None
    ) -> set[int]:
        criteria = [
            Message.learned.is_(True),
            Message.summarized_at.is_(None),
            scope_filter(Message, scope),
            Message.summary_claim_token.is_not(None),
            Message.summary_claimed_at.is_not(None),
            Message.summary_claimed_at < stale_before,
        ]
        if created_before is not None:
            criteria.append(Message.created_at <= created_before)
        return set(await self.session.scalars(select(Message.id).where(*criteria)))

    async def release_summary_claim(self, token: str) -> int:
        result = await self.session.execute(
            update(Message)
            .where(Message.summary_claim_token == token, Message.summarized_at.is_(None))
            .values(summary_claim_token=None, summary_claimed_at=None)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def finalize_summary_claim(
        self, token: str, message_ids: list[int], *, summarized_at: datetime
    ) -> int:
        if not message_ids:
            return 0
        result = await self.session.execute(
            update(Message)
            .where(
                Message.summary_claim_token == token,
                Message.summarized_at.is_(None),
                Message.id.in_(message_ids),
            )
            .values(summarized_at=summarized_at, summary_claim_token=None, summary_claimed_at=None)
        )
        return int(getattr(result, "rowcount", 0) or 0)

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

    async def get_many(self, message_ids: list[int]) -> dict[int, Message]:
        if not message_ids:
            return {}
        rows = list(
            (
                await self.session.scalars(
                    select(Message).where(Message.id.in_(sorted(set(message_ids))))
                )
            ).all()
        )
        return {int(row.id): row for row in rows}

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

    async def by_telegram_ids(self, scope: Scope, telegram_message_ids: list[int]) -> list[Message]:
        if not telegram_message_ids:
            return []
        stmt = (
            select(Message)
            .where(
                scope_filter(Message, scope),
                Message.telegram_message_id.in_(sorted(set(telegram_message_ids))),
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def by_telegram_ids_exact(
        self, scope: Scope, telegram_message_ids: list[int], *, limit: int
    ) -> list[Message]:
        if not telegram_message_ids or limit <= 0:
            return []
        stmt = (
            select(Message)
            .where(
                exact_scope_filter(Message, scope),
                Message.telegram_message_id.in_(sorted(set(telegram_message_ids))),
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def existing_logical_ids(self, scope: Scope, telegram_message_ids: list[int]) -> set[int]:
        if not telegram_message_ids:
            return set()
        stmt = select(Message.telegram_message_id).where(
            exact_scope_filter(Message, scope),
            Message.telegram_message_id.in_(sorted(set(telegram_message_ids))),
        )
        return {int(value) for value in (await self.session.scalars(stmt)).all()}

    async def skipped(
        self, scope: Scope, *, limit: int = 10, telegram_message_id: int | None = None
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(scope_filter(Message, scope), Message.learned.is_(False))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        if telegram_message_id is not None:
            stmt = stmt.where(Message.telegram_message_id == telegram_message_id)
        rows = list((await self.session.scalars(stmt)).all())
        return list(reversed(rows))

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

    async def counts(self, scope: Scope | None = None) -> dict[str, int]:
        filters = [] if scope is None else [scope_filter(Message, scope)]
        total = await self.session.scalar(select(func.count(Message.id)).where(*filters))
        learned = await self.session.scalar(
            select(func.count(Message.id)).where(*filters, Message.learned.is_(True))
        )
        skipped = await self.session.scalar(
            select(func.count(Message.id)).where(*filters, Message.learned.is_(False))
        )
        return {
            "messages": int(total or 0),
            "learned": int(learned or 0),
            "skipped": int(skipped or 0),
        }

    async def scope_counts(self) -> list[MessageScopeCounts]:
        stmt = (
            select(
                Message.chat_id,
                Message.message_thread_id,
                func.count(Message.id),
                func.sum(cast(Message.learned, Integer)),
                func.max(Message.created_at),
            )
            .group_by(Message.chat_id, Message.message_thread_id)
            .order_by(Message.chat_id.asc(), Message.message_thread_id.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        counts: list[MessageScopeCounts] = []
        for chat_id, thread_id, total, learned, last_message_at in rows:
            learned_count = int(learned or 0)
            total_count = int(total or 0)
            counts.append(
                MessageScopeCounts(
                    scope=Scope(int(chat_id), thread_id),
                    messages=total_count,
                    learned=learned_count,
                    skipped=total_count - learned_count,
                    last_message_at=last_message_at,
                )
            )
        return counts

    async def delete_scope(self, scope: Scope) -> int:
        count = await self.session.scalar(
            select(func.count(Message.id)).where(scope_filter(Message, scope))
        )
        await self.session.execute(delete(Message).where(scope_filter(Message, scope)))
        return int(count or 0)
