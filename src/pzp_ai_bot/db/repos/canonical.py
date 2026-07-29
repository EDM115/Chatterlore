from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import CanonicalScopeSnapshot
from pzp_ai_bot.memory.canonical import (
    CanonicalMemoryEntry,
    canonical_entries_json,
    parse_canonical_entries_json,
)
from pzp_ai_bot.memory.scope import Scope

from .shared import CanonicalScopeSnapshotRecord, utc_now


class CanonicalScopeSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, scope: Scope) -> CanonicalScopeSnapshotRecord | None:
        row = await self.session.scalar(
            select(CanonicalScopeSnapshot).where(CanonicalScopeSnapshot.scope_key == scope.key)
        )
        return self._record(row) if row is not None else None

    async def list(self) -> dict[Scope, CanonicalScopeSnapshotRecord]:
        rows = list(
            (
                await self.session.scalars(
                    select(CanonicalScopeSnapshot).order_by(CanonicalScopeSnapshot.scope_key.asc())
                )
            ).all()
        )
        records: dict[Scope, CanonicalScopeSnapshotRecord] = {}
        for row in rows:
            record = self._record(row)
            if record is not None:
                records[record.scope] = record
        return records

    async def upsert(
        self,
        *,
        scope: Scope,
        item_fingerprint: str,
        entries: Sequence[CanonicalMemoryEntry],
        provider: str,
        provider_profile: str,
        model: str,
        reasoning_level: str,
        prompt_version: str,
    ) -> CanonicalScopeSnapshotRecord:
        row = await self.session.scalar(
            select(CanonicalScopeSnapshot).where(CanonicalScopeSnapshot.scope_key == scope.key)
        )
        values = {
            "scope_key": scope.key,
            "chat_id": scope.chat_id,
            "message_thread_id": scope.thread_id,
            "item_fingerprint": item_fingerprint,
            "entries_json": canonical_entries_json(entries),
            "provider": provider,
            "provider_profile": provider_profile,
            "model": model,
            "reasoning_level": reasoning_level,
            "prompt_version": prompt_version,
            "updated_at": utc_now(),
        }
        if row is None:
            row = CanonicalScopeSnapshot(**values)
            self.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await self.session.flush()
        record = self._record(row)
        if record is None:
            raise RuntimeError("canonical snapshot failed validation after persistence")
        return record

    async def delete_scope(self, scope: Scope) -> bool:
        result = await self.session.execute(
            delete(CanonicalScopeSnapshot).where(CanonicalScopeSnapshot.scope_key == scope.key)
        )
        return bool(getattr(result, "rowcount", 0))

    async def delete_missing(self, current_scope_keys: set[str]) -> int:
        stmt = delete(CanonicalScopeSnapshot)
        if current_scope_keys:
            stmt = stmt.where(CanonicalScopeSnapshot.scope_key.not_in(current_scope_keys))
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    def _record(row: CanonicalScopeSnapshot) -> CanonicalScopeSnapshotRecord | None:
        scope = Scope(row.chat_id, row.message_thread_id)
        entries = parse_canonical_entries_json(row.entries_json)
        if row.scope_key != scope.key or entries is None:
            return None
        return CanonicalScopeSnapshotRecord(
            scope=scope,
            item_fingerprint=row.item_fingerprint,
            entries=entries,
            provider=row.provider,
            provider_profile=row.provider_profile,
            model=row.model,
            reasoning_level=row.reasoning_level,
            prompt_version=row.prompt_version,
            updated_at=row.updated_at,
        )
