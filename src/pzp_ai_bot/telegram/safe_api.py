from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, Message

logger = logging.getLogger(__name__)


async def safe_answer(message: Message, text: str, **kwargs: Any) -> bool:
    try:
        await message.answer(text, **kwargs)
        return True
    except TelegramRetryAfter as exc:
        _log_telegram_error("telegram_rate_limited", exc, message, retry_after=exc.retry_after)
        return False
    except TelegramAPIError as exc:
        _log_telegram_error("telegram_api_error", exc, message)
        return False


async def safe_answer_document(
    message: Message, *, document: BufferedInputFile, caption: str | None = None, **kwargs: Any
) -> bool:
    try:
        await message.answer_document(document=document, caption=caption, **kwargs)
        return True
    except TelegramRetryAfter as exc:
        _log_telegram_error("telegram_rate_limited", exc, message, retry_after=exc.retry_after)
        return False
    except TelegramAPIError as exc:
        _log_telegram_error("telegram_api_error", exc, message)
        return False


def _log_telegram_error(
    event: str, exc: TelegramAPIError, message: Message, *, retry_after: int | None = None
) -> None:
    logger.warning(
        event,
        extra={
            "chat_id": int(message.chat.id),
            "thread_id": getattr(message, "message_thread_id", None),
            "message_id": getattr(message, "message_id", None),
            "method": type(getattr(exc, "method", None)).__name__,
            "retry_after": retry_after,
            "error": str(exc),
        },
    )
