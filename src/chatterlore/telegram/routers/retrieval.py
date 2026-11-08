from __future__ import annotations

import asyncio
import shlex
from collections.abc import AsyncIterator

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message, MessageGenerationStopped

from chatterlore.app_context import AppContext
from chatterlore.memory.scope import parse_scope
from chatterlore.memory.service import (
    AIRequestOptions,
    MemoryExportChunk,
    format_range_summary,
    format_results,
    parse_memory_export_format,
)
from chatterlore.telegram.commands import command_help_text
from chatterlore.telegram.routers.shared import RouterGuards
from chatterlore.telegram.safe_api import safe_answer, safe_answer_document
from chatterlore.telegram.streaming import AnswerRegistry
from chatterlore.telegram.utils import scope_from_message

TELEGRAM_DOCUMENT_MAX_BYTES = 50 * 1024 * 1024
EXPORT_DOCUMENT_CHUNK_BYTES = 45 * 1024 * 1024


def register_query_handlers(router: Router, context: AppContext, guards: RouterGuards) -> None:
    answers = AnswerRegistry()
    router.shutdown.register(answers.aclose)

    @router.stopped_message_generation()
    async def generation_stopped(event: MessageGenerationStopped, bot: Bot) -> None:
        answers.stop(bot.id, event)

    @router.message(Command("ask"))
    async def ask(message: Message, command: CommandObject) -> None:
        try:
            profile_name, ai_options, question, admin_override = _extract_ai_query_args(
                command.args, prompt_target="system"
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not question:
            await safe_answer(
                message,
                "Usage: /ask [profile=name] [provider=name] [system=name] [model=name] "
                "[reasoning=low|medium|high] [prompt='one-shot system prompt'] <question>",
            )
            return
        if admin_override and not await guards.require_admin(message):
            return
        try:
            delivery = answers.start(message)
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        try:
            answer = await context.memory.ask(
                scope_from_message(message),
                question,
                profile_name=profile_name,
                ai_options=ai_options,
                on_text=delivery.update,
            )
        except ValueError as exc:
            await delivery.finish(str(exc), markdown=False)
        except asyncio.CancelledError:
            if not delivery.stopped:
                raise
            if not delivery.superseded or not delivery.private:
                status = (
                    "Superseded by a newer request."
                    if delivery.superseded
                    else "Generation stopped."
                )
                await delivery.finish(status, markdown=False)
        else:
            await delivery.finish(answer)
        finally:
            answers.discard(delivery)

    @router.message(Command("memory_search"))
    async def memory_search(message: Message, command: CommandObject) -> None:
        try:
            profile_name, ai_options, query, admin_override = _extract_ai_query_args(
                command.args, prompt_target="none"
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not query:
            await safe_answer(
                message, "Usage: /memory_search [profile=name] [provider=name] <query>"
            )
            return
        if admin_override and not await guards.require_admin(message):
            return
        try:
            results = await context.memory.search(
                scope_from_message(message), query, profile_name=profile_name, ai_options=ai_options
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_results(results), markdown=True)

    @router.message(Command("start", "help"))
    async def help_command(message: Message) -> None:
        await safe_answer(message, command_help_text())

    @router.message(Command("memory_export"))
    async def memory_export(message: Message, command: CommandObject) -> None:
        args = (command.args or "").strip()
        raw_format = "md"
        raw_scope = "current"
        for token in args.split():
            if token in {"md", "jsonl"}:
                raw_format = token
            elif token.startswith("scope="):
                raw_scope = token.removeprefix("scope=")
            else:
                await safe_answer(message, "Usage: /memory_export [md|jsonl] [scope=...]")
                return
        fmt = parse_memory_export_format(raw_format)
        try:
            scope = parse_scope(raw_scope, current=scope_from_message(message))
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        suffix = "jsonl" if fmt == "jsonl" else "md"
        filename = f"memory_{scope.filename_stem}.{suffix}"
        chunks = context.memory.export_memory_chunks(
            scope if not scope.is_global else None, fmt=fmt
        )
        index = 0
        async for document in export_document_stream(chunks, filename=filename):
            index += 1
            caption = f"Memory export for {scope.key}"
            if document.filename != filename:
                caption = f"{caption} (part {index})"
            if not await safe_answer_document(message, document=document, caption=caption):
                await safe_answer(
                    message,
                    "Memory export could not be sent through Telegram. Try a narrower scope "
                    "or run the CLI export locally.",
                )
                return


def register_summarization_handlers(
    router: Router, context: AppContext, guards: RouterGuards
) -> None:
    @router.message(Command("summarize_now"))
    async def summarize_now(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        try:
            args, ai_options = _extract_summary_args(command.args)
            count = await context.memory.summarize_now(
                scope_from_message(message),
                template_name=args.get("template"),
                ai_options=ai_options,
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, f"Summarized {count} pending messages.")


def register_range_summary_handler(
    router: Router, context: AppContext, guards: RouterGuards
) -> None:
    @router.message(Command("summarize_range"))
    async def summarize_range(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        try:
            args, ai_options = _extract_summary_args(command.args)
            start = int(args["start"])
            end = int(args["end"])
            result = await context.memory.summarize_range(
                scope_from_message(message),
                start_message_id=start,
                end_message_id=end,
                template_name=args.get("template", "general"),
                ai_options=ai_options,
            )
        except KeyError:
            await safe_answer(
                message,
                "Usage: /summarize_range start=<message_id> end=<message_id> [template=name]",
            )
            return
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_range_summary(result), markdown=True)


def _extract_ai_query_args(
    raw: str | None, *, prompt_target: str
) -> tuple[str | None, AIRequestOptions, str, bool]:
    tokens = _shlex_tokens(raw)
    kept: list[str] = []
    memory_profile: str | None = None
    options: dict[str, str] = {}
    admin_override = False
    for token in tokens:
        if "=" not in token:
            kept.append(token)
            continue
        key, value = token.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "profile":
            memory_profile = value or None
        elif key in {"provider", "provider_profile"}:
            options["provider_profile"] = value
            admin_override = True
        elif key in {"model", "chat_model"}:
            options["chat_model"] = value
            admin_override = True
        elif key == "embedding_model":
            options["embedding_model"] = value
            admin_override = True
        elif key in {"reasoning", "reasoning_level"}:
            options["reasoning_level"] = value
            admin_override = True
        elif key in {"system", "system_prompt", "system_prompt_template"}:
            options["system_prompt_template"] = value
            admin_override = True
        elif key in {"prompt", "override"}:
            if prompt_target == "system":
                options["system_prompt_override"] = value
                admin_override = True
            elif prompt_target == "summary":
                options["summary_template_override"] = value
                admin_override = True
            else:
                raise ValueError(f"{key}= is not supported for this command")
        else:
            kept.append(token)
    return memory_profile, AIRequestOptions(**options), " ".join(kept).strip(), admin_override


def _extract_summary_args(raw: str | None) -> tuple[dict[str, str], AIRequestOptions]:
    tokens = _shlex_tokens(raw)
    args: dict[str, str] = {}
    options: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            raise ValueError("summary options must use key=value form")
        key, value = token.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in {"provider", "provider_profile"}:
            options["provider_profile"] = value
        elif key in {"model", "chat_model"}:
            options["chat_model"] = value
        elif key == "embedding_model":
            options["embedding_model"] = value
        elif key in {"reasoning", "reasoning_level"}:
            options["reasoning_level"] = value
        elif key in {"prompt", "override"}:
            options["summary_template_override"] = value
        else:
            args[key] = value
    return args, AIRequestOptions(**options)


def _shlex_tokens(raw: str | None) -> list[str]:
    try:
        return shlex.split(_escape_word_apostrophes(raw or ""))
    except ValueError as exc:
        raise ValueError(f"invalid quoted arguments: {exc}") from exc


def _escape_word_apostrophes(raw: str) -> str:
    escaped: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and quote != "'" and index + 1 < len(raw):
            escaped.extend((char, raw[index + 1]))
            index += 2
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                previous = raw[index - 1] if index else ""
                if char == "'" and previous and not previous.isspace() and previous != "=":
                    escaped.append("\\")
                else:
                    quote = char
        escaped.append(char)
        index += 1
    return "".join(escaped)


def export_documents(
    payload: str, *, filename: str, max_bytes: int = EXPORT_DOCUMENT_CHUNK_BYTES
) -> list[BufferedInputFile]:
    _validate_export_max_bytes(max_bytes)
    data = payload.encode("utf-8")
    if len(data) <= max_bytes:
        return [BufferedInputFile(data, filename=filename)]
    chunks = _utf8_chunks(data, max_bytes)
    return [
        BufferedInputFile(
            chunk, filename=_export_part_filename(filename, index=index, total=len(chunks))
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


async def export_document_stream(
    chunks: AsyncIterator[MemoryExportChunk],
    *,
    filename: str,
    max_bytes: int = EXPORT_DOCUMENT_CHUNK_BYTES,
) -> AsyncIterator[BufferedInputFile]:
    _validate_export_max_bytes(max_bytes)
    buffer = bytearray()
    part_index = 0
    async for chunk in chunks:
        buffer.extend(chunk.text.encode("utf-8"))
        while len(buffer) > max_bytes:
            end = _utf8_prefix_end(buffer, max_bytes)
            part_index += 1
            yield BufferedInputFile(
                bytes(buffer[:end]), filename=_export_part_filename(filename, index=part_index)
            )
            del buffer[:end]
    if part_index == 0:
        yield BufferedInputFile(bytes(buffer), filename=filename)
        return
    if buffer:
        part_index += 1
        yield BufferedInputFile(
            bytes(buffer), filename=_export_part_filename(filename, index=part_index)
        )


def _validate_export_max_bytes(max_bytes: int) -> None:
    if max_bytes <= 0 or max_bytes > TELEGRAM_DOCUMENT_MAX_BYTES:
        raise ValueError("max_bytes must be between 1 and the Telegram document upload limit")


def _utf8_chunks(data: bytes, max_bytes: int) -> list[bytes]:
    buffer = bytearray(data)
    chunks: list[bytes] = []
    while len(buffer) > max_bytes:
        end = _utf8_prefix_end(buffer, max_bytes)
        chunks.append(bytes(buffer[:end]))
        del buffer[:end]
    if buffer or not chunks:
        chunks.append(bytes(buffer))
    return chunks


def _utf8_prefix_end(data: bytearray, max_bytes: int) -> int:
    end = min(len(data), max_bytes)
    if end == len(data):
        return end
    while end > 0 and data[end] & 0b1100_0000 == 0b1000_0000:
        end -= 1
    if end == 0:
        raise ValueError("max_bytes is too small to preserve UTF-8 character boundaries")
    return end


def _export_part_filename(filename: str, *, index: int, total: int | None = None) -> str:
    stem, dot, suffix = filename.rpartition(".")
    if not dot:
        stem = filename
        suffix = "txt"
    if total is None:
        return f"{stem}.part{index:03d}.{suffix}"
    return f"{stem}.part{index:03d}of{total:03d}.{suffix}"


__all__ = [
    "export_document_stream",
    "export_documents",
    "register_query_handlers",
    "register_range_summary_handler",
    "register_summarization_handlers",
]
