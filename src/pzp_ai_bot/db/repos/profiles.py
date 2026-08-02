from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import (
    MemoryProfile,
    MemoryProfileScope,
    MemoryReviewItem,
    MemoryScopePermission,
)
from pzp_ai_bot.memory.scope import Scope

from .shared import (
    MemoryProfileView,
    _nullable_eq,
    dedupe_scopes,
    normalize_profile_name,
    scope_filter,
    utc_now,
)


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

    async def pending_page(
        self, scope: Scope | None = None, *, after_id: int = 0, limit: int
    ) -> list[MemoryReviewItem]:
        if limit <= 0:
            raise ValueError("review page limit must be positive")
        stmt = (
            select(MemoryReviewItem)
            .where(MemoryReviewItem.status == "pending", MemoryReviewItem.id > after_id)
            .order_by(MemoryReviewItem.id.asc())
            .limit(limit)
        )
        if scope is not None:
            stmt = stmt.where(scope_filter(MemoryReviewItem, scope))
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

    async def count_pending(self, scope: Scope | None = None) -> int:
        stmt = select(func.count(MemoryReviewItem.id)).where(MemoryReviewItem.status == "pending")
        if scope is not None:
            stmt = stmt.where(scope_filter(MemoryReviewItem, scope))
        return int(await self.session.scalar(stmt) or 0)


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
                updated_by=updated_by,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row = rows[0]
            if can_query is not None:
                row.can_query = can_query
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
