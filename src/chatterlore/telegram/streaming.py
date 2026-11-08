from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable

import structlog
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import InputRichMessage, LinkPreviewOptions, Message, MessageGenerationStopped

from chatterlore.telegram.presentation import (
    PLAIN_TEXT_UNITS,
    AnswerChunk,
    answer_chunks,
    markdown_has_text,
    rich_payload,
    split_text,
)
from chatterlore.telegram.safe_api import (
    _log_telegram_error,
    is_rich_rejection,
    safe_answer,
    send_answer_piece,
    send_literal,
)

logger = structlog.get_logger(__name__)


class _UseLiteralError(Exception):
    pass


class AnswerDelivery:
    def __init__(
        self,
        message: Message,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.message = message
        bot = message.bot
        if bot is None:
            raise ValueError("Telegram reply requires a bound bot.")
        self.bot = bot
        self.clock = clock
        self.sleep = sleep
        self.private = message.chat.type == "private"
        self.draft_id = secrets.randbits(63) or 1
        self.task = asyncio.current_task()
        self.stopped = False
        self.superseded = False
        self.closed = False
        self._message_id: int | None = None
        self._plain = False
        self._next_update = 0.0
        self._retry_at = 0.0
        self._last_text = ""
        self._preview_disabled = False
        self._uncertain_send = False

    async def update(self, text: str) -> None:
        if self.stopped:
            raise asyncio.CancelledError
        if self.closed or self._preview_disabled or not text or text == self._last_text:
            return
        now = self.clock()
        if now < max(self._next_update, self._retry_at):
            return
        preview = next(split_text(text, limit=3000, utf16=True))
        if not markdown_has_text(preview):
            return
        self._next_update = now + (1.0 if self.private else 3.0)
        try:
            if await self._attempt(preview):
                self._last_text = text
        except _UseLiteralError:
            await self._attempt(preview)

    async def finish(self, text: str, *, markdown: bool = True) -> bool:
        if self.closed or self._uncertain_send:
            return False
        wait = self._retry_at - self.clock()
        if wait > 10:
            logger.warning("telegram_final_rate_limited", chat_id=self.message.chat.id)
            return False
        if wait > 0:
            await self.sleep(wait)
        if self._message_id is None:
            return await safe_answer(self.message, text, markdown=markdown)
        for _ in range(2):
            parts = list(
                (
                    AnswerChunk(part, rich_payload(part))
                    for part in split_text(text, limit=PLAIN_TEXT_UNITS, utf16=True)
                )
                if self._plain
                else answer_chunks(text, markdown=markdown)
            )
            if not parts:
                return False
            try:
                if not await self._attempt(
                    parts[0].text, final=True, markdown=markdown, payload=parts[0].payload
                ):
                    return False
            except _UseLiteralError:
                continue
            for part in parts[1:]:
                if self._plain:
                    if await send_literal(self.message, part.text) is None:
                        return False
                elif await send_answer_piece(self.message, part.text, payload=part.payload) is None:
                    return False
            return True
        return False

    async def _attempt(
        self,
        text: str,
        *,
        final: bool = False,
        markdown: bool = True,
        payload: InputRichMessage | None = None,
    ) -> bool:
        try:
            async with asyncio.timeout(4):
                await self._publish(text, final=final, markdown=markdown, payload=payload)
            return True
        except TelegramRetryAfter as exc:
            self._retry_at = self.clock() + max(0, exc.retry_after)
            _log_telegram_error(
                "telegram_rate_limited", exc, self.message, retry_after=exc.retry_after
            )
        except (TelegramBadRequest, TelegramNotFound) as exc:
            if "message is not modified" in exc.message.casefold():
                return True
            _log_telegram_error("telegram_api_error", exc, self.message)
            if is_rich_rejection(exc) and not self._plain:
                if self.private and not final:
                    self._preview_disabled = True
                else:
                    self._plain = True
                    raise _UseLiteralError from None
            self._preview_disabled = True
        except TelegramAPIError as exc:
            _log_telegram_error("telegram_api_error", exc, self.message)
            self._preview_disabled = True
            if not self.private and self._message_id is None:
                self._uncertain_send = isinstance(exc, TelegramNetworkError)
        except TimeoutError:
            logger.warning("telegram_delivery_timeout", chat_id=self.message.chat.id)
            self._preview_disabled = True
            if not self.private and self._message_id is None:
                self._uncertain_send = True
        return False

    async def _publish(
        self, text: str, *, final: bool, markdown: bool, payload: InputRichMessage | None = None
    ) -> None:
        message = self.message
        payload = payload or rich_payload(text, markdown=markdown)
        if self.private and not final:
            await self.bot.send_rich_message_draft(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id if message.is_topic_message else None,
                draft_id=self.draft_id,
                rich_message=payload,
                can_stop=True,
                keep_on_stop=False,
            )
            return
        if not final:
            text = "Generating…\n" + text
            payload = payload.model_copy(
                update={"html": "<p><em>Generating…</em></p>\n" + (payload.html or "")}
            )
        if self._message_id is not None:
            if self._plain:
                await self.bot.edit_message_text(
                    text=text,
                    business_connection_id=message.business_connection_id,
                    chat_id=message.chat.id,
                    message_id=self._message_id,
                    parse_mode=None,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            else:
                await self.bot.edit_message_text(
                    rich_message=payload,
                    business_connection_id=message.business_connection_id,
                    chat_id=message.chat.id,
                    message_id=self._message_id,
                    parse_mode=None,
                )
        else:
            if self._plain:
                sent = await message.answer(
                    text, parse_mode=None, link_preview_options=LinkPreviewOptions(is_disabled=True)
                )
            else:
                sent = await message.answer_rich(payload)
            self._message_id = sent.message_id


class AnswerRegistry:
    def __init__(self) -> None:
        self._active: dict[tuple[int, int, int | None, int], AnswerDelivery] = {}
        self._closed = False

    def _key(self, message: Message) -> tuple[int, int, int | None, int]:
        bot = message.bot
        if bot is None:
            raise ValueError("Telegram reply requires a bound bot.")
        return (
            bot.id,
            message.chat.id,
            message.message_thread_id if message.is_topic_message else None,
            0
            if message.chat.type == "private"
            else (message.from_user.id if message.from_user else 0),
        )

    def start(self, message: Message) -> AnswerDelivery:
        if self._closed:
            raise ValueError("Reply generation is shutting down.")
        key = self._key(message)
        old = self._active.get(key)
        if old is None and len(self._active) >= 128:
            raise ValueError("Too many answer requests are already running. Try again shortly.")
        if old is not None and old.task is not None and old.task is not asyncio.current_task():
            old.stopped = old.superseded = True
            old.task.cancel()
        current = AnswerDelivery(message)
        self._active[key] = current
        return current

    def discard(self, delivery: AnswerDelivery) -> None:
        key = self._key(delivery.message)
        if self._active.get(key) is delivery:
            self._active.pop(key)
        delivery.closed = True

    def stop(self, bot_id: int, event: MessageGenerationStopped) -> bool:
        if event.chat.type != "private":
            return False
        delivery = self._active.get((bot_id, event.chat.id, event.message_thread_id, 0))
        if delivery is None or delivery.draft_id != event.draft_id or delivery.task is None:
            return False
        delivery.stopped = True
        delivery.task.cancel()
        return True

    async def aclose(self, **_kwargs: object) -> None:
        self._closed = True
        tasks = {
            delivery.task
            for delivery in self._active.values()
            if delivery.task is not None and delivery.task is not asyncio.current_task()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()
