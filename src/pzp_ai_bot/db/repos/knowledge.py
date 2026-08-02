from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import KnowledgeItem
from pzp_ai_bot.memory.scope import Scope

from .shared import KnowledgeScopeCounts, exact_scope_filter, scope_filter, utc_now


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

    async def list_page(
        self, scope: Scope | None = None, *, after_id: int = 0, kind: str | None = None, limit: int
    ) -> list[KnowledgeItem]:
        if limit <= 0:
            raise ValueError("knowledge page limit must be positive")
        stmt = (
            select(KnowledgeItem)
            .where(KnowledgeItem.id > after_id)
            .order_by(KnowledgeItem.id.asc())
            .limit(limit)
        )
        if scope is not None:
            stmt = stmt.where(scope_filter(KnowledgeItem, scope))
        if kind is not None:
            stmt = stmt.where(KnowledgeItem.kind == kind)
        return list((await self.session.scalars(stmt)).all())

    async def list_exact_scope(self, scope: Scope) -> list[KnowledgeItem]:
        stmt = (
            select(KnowledgeItem)
            .where(exact_scope_filter(KnowledgeItem, scope))
            .order_by(KnowledgeItem.created_at.asc(), KnowledgeItem.id.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def list_exact_content(self, scope: Scope, contents: list[str]) -> list[KnowledgeItem]:
        if not contents:
            return []
        stmt = select(KnowledgeItem).where(
            exact_scope_filter(KnowledgeItem, scope),
            KnowledgeItem.content.in_(sorted(set(contents))),
        )
        return list((await self.session.scalars(stmt)).all())

    async def get(self, item_id: int) -> KnowledgeItem | None:
        return await self.session.get(KnowledgeItem, item_id)

    async def get_many(self, item_ids: list[int]) -> dict[int, KnowledgeItem]:
        if not item_ids:
            return {}
        rows = list(
            (
                await self.session.scalars(
                    select(KnowledgeItem).where(KnowledgeItem.id.in_(sorted(set(item_ids))))
                )
            ).all()
        )
        return {int(row.id): row for row in rows}

    async def list_recent(
        self, scope: Scope | None = None, *, kind: str | None = None, limit: int = 20
    ) -> list[KnowledgeItem]:
        stmt = select(KnowledgeItem).order_by(
            KnowledgeItem.created_at.desc(), KnowledgeItem.id.desc()
        )
        if scope is not None:
            stmt = stmt.where(scope_filter(KnowledgeItem, scope))
        if kind is not None:
            stmt = stmt.where(KnowledgeItem.kind == kind)
        return list((await self.session.scalars(stmt.limit(limit))).all())

    async def update_item(
        self,
        item_id: int,
        *,
        scope: Scope,
        content: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> KnowledgeItem | None:
        item = await self.session.scalar(
            select(KnowledgeItem).where(
                KnowledgeItem.id == item_id, scope_filter(KnowledgeItem, scope)
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

    async def delete_item(self, item_id: int, *, scope: Scope) -> KnowledgeItem | None:
        item = await self.session.scalar(
            select(KnowledgeItem).where(
                KnowledgeItem.id == item_id, scope_filter(KnowledgeItem, scope)
            )
        )
        if item is None:
            return None
        await self.session.delete(item)
        await self.session.flush()
        return item

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

    async def count(self, scope: Scope | None = None) -> int:
        stmt = select(func.count(KnowledgeItem.id))
        if scope is not None:
            stmt = stmt.where(scope_filter(KnowledgeItem, scope))
        return int(await self.session.scalar(stmt) or 0)

    async def scope_counts(self) -> list[KnowledgeScopeCounts]:
        stmt = (
            select(
                KnowledgeItem.chat_id, KnowledgeItem.message_thread_id, func.count(KnowledgeItem.id)
            )
            .group_by(KnowledgeItem.chat_id, KnowledgeItem.message_thread_id)
            .order_by(KnowledgeItem.chat_id.asc(), KnowledgeItem.message_thread_id.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            KnowledgeScopeCounts(scope=Scope(chat_id, thread_id), knowledge_items=int(count or 0))
            for chat_id, thread_id, count in rows
        ]

    async def delete_scope(self, scope: Scope) -> int:
        count = await self.session.scalar(
            select(func.count(KnowledgeItem.id)).where(scope_filter(KnowledgeItem, scope))
        )
        await self.session.execute(delete(KnowledgeItem).where(scope_filter(KnowledgeItem, scope)))
        return int(count or 0)
