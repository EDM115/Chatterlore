from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from pzp_ai_bot.telegram.utils import message_user_id, scope_from_message

logger = structlog.get_logger(__name__)


class StructuredLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        error: str | None = None
        try:
            return await handler(event, data)
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            metadata = _telegram_event_metadata(event)
            logger.info(
                "telegram_update",
                event_type="telegram_update",
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=error,
                **metadata,
            )


def _telegram_event_metadata(event: TelegramObject) -> dict[str, Any]:
    if isinstance(event, Message):
        scope = scope_from_message(event)
        return {
            "chat_id": scope.chat_id,
            "thread_id": scope.thread_id,
            "user_id": message_user_id(event),
            "message_id": event.message_id,
            "telegram_event": "message",
        }
    return {"telegram_event": type(event).__name__}
