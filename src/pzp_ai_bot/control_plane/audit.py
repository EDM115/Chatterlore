from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.scope import Scope

SENSITIVE_AUDIT_KEY_PARTS = (
    "api_key",
    "authorization",
    "content",
    "init_data",
    "password",
    "prompt",
    "secret",
    "text",
    "token",
)


class ControlPlaneAuditService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def events(
        self, *, scope: Scope | None, event_type: str | None, before_id: int | None, limit: int
    ) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            rows = await EventRepository(session).list_events_page(
                scope=scope, event_type=event_type, before_id=before_id, limit=limit + 1
            )
        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "items": [
                {
                    "id": row.id,
                    "event_type": row.event_type,
                    "scope": (
                        Scope(row.chat_id, row.message_thread_id).key
                        if row.chat_id is not None
                        else None
                    ),
                    "user_id": row.user_id,
                    "payload": safe_audit_value(row.payload or {}),
                    "created_at": _as_utc(row.created_at),
                }
                for row in page
            ],
            "next_cursor": page[-1].id if has_more and page else None,
            "has_more": has_more,
        }

    async def ai_usage(
        self, *, scope: Scope | None, before_id: int | None, limit: int
    ) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            rows = await EventRepository(session).list_ai_usage_page(
                scope=scope, before_id=before_id, limit=limit + 1
            )
        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "items": [
                {
                    "id": row.id,
                    "scope": (
                        Scope(row.chat_id, row.message_thread_id).key
                        if row.chat_id is not None
                        else None
                    ),
                    "provider": row.provider,
                    "model": row.model,
                    "operation": row.operation,
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "latency_ms": row.latency_ms,
                    "error": row.error,
                    "created_at": _as_utc(row.created_at),
                }
                for row in page
            ],
            "next_cursor": page[-1].id if has_more and page else None,
            "has_more": has_more,
        }


def safe_audit_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    normalized_key = key.strip().lower()
    if any(part in normalized_key for part in SENSITIVE_AUDIT_KEY_PARTS):
        return "[redacted]"
    if depth >= 3:
        return "[truncated]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, dict):
        return {
            str(item_key)[:128]: safe_audit_value(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in list(value.items())[:50]
        }
    if isinstance(value, list | tuple):
        return [safe_audit_value(item, depth=depth + 1) for item in value[:50]]
    return str(value)[:512]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
