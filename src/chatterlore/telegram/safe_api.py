from __future__ import annotations

from typing import Any

import structlog
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile, InputRichMessage, LinkPreviewOptions, Message

from chatterlore.telegram.presentation import (
    PLAIN_TEXT_UNITS,
    answer_chunks,
    rich_payload,
    split_text,
)

logger = structlog.get_logger(__name__)


async def safe_answer(
    message: Message, text: str, *, markdown: bool = False, **kwargs: Any
) -> bool:
    for part in answer_chunks(text, markdown=markdown):
        if await send_answer_piece(message, part.text, payload=part.payload, **kwargs) is None:
            return False
    return True


async def send_answer_piece(
    message: Message,
    text: str,
    *,
    markdown: bool = False,
    payload: InputRichMessage | None = None,
    **kwargs: Any,
) -> Message | None:
    try:
        return await message.answer_rich(payload or rich_payload(text, markdown=markdown), **kwargs)
    except TelegramRetryAfter as exc:
        _log_telegram_error("telegram_rate_limited", exc, message, retry_after=exc.retry_after)
    except (TelegramBadRequest, TelegramNotFound) as exc:
        _log_telegram_error("telegram_api_error", exc, message)
        if is_rich_rejection(exc):
            return await send_literal(message, text, **kwargs)
    except TelegramAPIError as exc:
        _log_telegram_error("telegram_api_error", exc, message)
    return None


def is_rich_rejection(exc: TelegramAPIError) -> bool:
    detail = exc.message.casefold()
    return any(
        marker in detail
        for marker in (
            "parse",
            "rich message",
            "unsupported",
            "method not found",
            "too many blocks",
            "too long",
        )
    )


async def send_literal(message: Message, text: str, **kwargs: Any) -> Message | None:
    kwargs["parse_mode"] = None
    kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
    sent: Message | None = None
    try:
        for part in split_text(text, limit=PLAIN_TEXT_UNITS, utf16=True):
            sent = await message.answer(part, **kwargs)
    except TelegramRetryAfter as exc:
        _log_telegram_error("telegram_rate_limited", exc, message, retry_after=exc.retry_after)
        return None
    except TelegramAPIError as exc:
        _log_telegram_error("telegram_api_error", exc, message)
        return None
    return sent


async def safe_answer_document(
    message: Message, *, document: BufferedInputFile, caption: str | None = None, **kwargs: Any
) -> bool:
    try:
        kwargs["parse_mode"] = None
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
        event_type=event,
        chat_id=int(message.chat.id),
        thread_id=getattr(message, "message_thread_id", None),
        message_id=getattr(message, "message_id", None),
        method=type(getattr(exc, "method", None)).__name__,
        retry_after=retry_after,
        error_type=type(exc).__name__,
    )
