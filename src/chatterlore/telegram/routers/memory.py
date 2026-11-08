from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from chatterlore.app_context import AppContext
from chatterlore.memory.scope import parse_scope
from chatterlore.memory.service import (
    IncomingMessage,
    format_memory_item_refresh,
    format_memory_items,
    format_note_previews,
    format_review_previews,
    parse_memory_item_mode,
    parse_note_create_args,
    parse_note_edit_args,
    parse_optional_limit,
)
from chatterlore.telegram.routers.shared import RouterGuards
from chatterlore.telegram.safe_api import safe_answer
from chatterlore.telegram.utils import (
    is_command_text,
    message_chat_title,
    message_chat_username,
    message_created_at,
    message_permalink,
    message_topic_title,
    message_user_id,
    message_username,
    parse_key_values,
    reply_to_message_id,
    scope_from_message,
    text_from_message,
)


def register_memory_item_handlers(
    router: Router, context: AppContext, guards: RouterGuards
) -> None:
    @router.message(Command("remember"))
    async def remember(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        try:
            options, text = parse_note_create_args(command.args)
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        item_id = await context.memory.remember(
            scope_from_message(message),
            text,
            message.message_id,
            tags=options.get("tags"),
            priority=options.get("priority"),
            source=options.get("source"),
        )
        await safe_answer(message, f"Remembered note #{item_id}.")

    @router.message(Command("memory_items"))
    async def memory_items(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
            limit = parse_optional_limit(args.get("limit"), default=20, maximum=50)
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        items = await context.memory.browse_items(
            None if scope.is_global else scope, kind=args.get("kind"), limit=limit
        )
        await safe_answer(message, format_memory_items(items)[:3900])

    @router.message(Command("memory_item_delete"))
    async def memory_item_delete(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        parts = (command.args or "").strip().split(None, 1)
        if not parts:
            await safe_answer(message, "Usage: /memory_item_delete <id> [scope=...]")
            return
        args = parse_key_values(parts[1] if len(parts) > 1 else None)
        try:
            item_id = int(parts[0])
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        try:
            deleted = await context.memory.delete_memory_item(
                scope, item_id, deleted_by=message_user_id(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(
            message, f"Deleted memory item #{deleted.id} ({deleted.kind}) from {deleted.scope.key}."
        )

    @router.message(Command("memory_item_refresh"))
    async def memory_item_refresh(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        parts = (command.args or "").strip().split(None, 1)
        if not parts:
            await safe_answer(
                message, "Usage: /memory_item_refresh <id> [mode=embed|summarize|both] [scope=...]"
            )
            return
        args = parse_key_values(parts[1] if len(parts) > 1 else None)
        try:
            item_id = int(parts[0])
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
            mode = parse_memory_item_mode(args.get("mode"))
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        try:
            result = await context.memory.refresh_memory_item(
                scope, item_id, mode=mode, updated_by=message_user_id(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_memory_item_refresh(result)[:3900])

    @router.message(Command("memory_notes"))
    async def memory_notes(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        notes = await context.memory.list_notes(scope, tag=args.get("tag"))
        await safe_answer(message, format_note_previews(notes)[:3900])

    @router.message(Command("memory_note_edit"))
    async def memory_note_edit(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        try:
            item_id, updates, content = parse_note_edit_args(command.args)
            note = await context.memory.edit_note(
                scope_from_message(message),
                item_id,
                content=content,
                tags=updates.get("tags"),
                priority=updates.get("priority"),
                source=updates.get("source"),
                clear_priority=bool(updates.get("clear_priority")),
                clear_source=bool(updates.get("clear_source")),
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, f"Updated manual note #{note.id}.")

    @router.message(Command("memory_note_delete"))
    async def memory_note_delete(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        try:
            item_id = int((command.args or "").strip().split()[0])
            await context.memory.delete_note(scope_from_message(message), item_id)
        except (IndexError, ValueError) as exc:
            await safe_answer(message, str(exc) if str(exc) else "Usage: /memory_note_delete <id>")
            return
        await safe_answer(message, f"Deleted manual note #{item_id}.")


def register_review_handlers(router: Router, context: AppContext, guards: RouterGuards) -> None:
    @router.message(Command("memory_reviews"))
    async def memory_reviews(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        reviews = await context.memory.pending_reviews(scope)
        await safe_answer(message, format_review_previews(reviews)[:3900])

    @router.message(Command("memory_approve"))
    async def memory_approve(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        try:
            review_id = int((command.args or "").strip().split()[0])
            review_scope = await context.memory.pending_review_scope(review_id)
            if not await guards.require_scope_admin(message, review_scope):
                return
            item_id = await context.memory.approve_review(
                review_id, reviewed_by=message_user_id(message)
            )
        except (IndexError, ValueError) as exc:
            await safe_answer(
                message, str(exc) if str(exc) else "Usage: /memory_approve <review_id>"
            )
            return
        await safe_answer(message, f"Approved memory review #{review_id} as item #{item_id}.")

    @router.message(Command("memory_reject"))
    async def memory_reject(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        try:
            review_id = int((command.args or "").strip().split()[0])
            review_scope = await context.memory.pending_review_scope(review_id)
            if not await guards.require_scope_admin(message, review_scope):
                return
            await context.memory.reject_review(review_id, reviewed_by=message_user_id(message))
        except (IndexError, ValueError) as exc:
            await safe_answer(
                message, str(exc) if str(exc) else "Usage: /memory_reject <review_id>"
            )
            return
        await safe_answer(message, f"Rejected memory review #{review_id}.")


def register_forget_handler(router: Router, context: AppContext, guards: RouterGuards) -> None:
    @router.message(Command("forget"))
    async def forget(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        _ok, response = await context.memory.forget(
            scope, message_user_id(message) or 0, args.get("confirm")
        )
        await safe_answer(message, response)


def register_ingest_handler(router: Router, context: AppContext) -> None:
    @router.message(F.text | F.caption | F.document)
    async def ingest(message: Message) -> None:
        text = text_from_message(message)
        user_id = message_user_id(message)
        is_chat_admin = await context.admins.is_chat_admin(
            message.bot, int(message.chat.id), user_id
        )
        await context.memory.ingest(
            IncomingMessage(
                telegram_message_id=message.message_id,
                scope=scope_from_message(message),
                user_id=user_id,
                username=message_username(message),
                text=text,
                created_at=message_created_at(message),
                reply_to_message_id=reply_to_message_id(message),
                is_bot=bool(getattr(getattr(message, "from_user", None), "is_bot", False)),
                is_command=is_command_text(text),
                is_chat_admin=is_chat_admin,
                chat_title=message_chat_title(message),
                chat_username=message_chat_username(message),
                topic_title=message_topic_title(message),
                message_permalink=message_permalink(message),
            )
        )


__all__ = [
    "register_forget_handler",
    "register_ingest_handler",
    "register_memory_item_handlers",
    "register_review_handlers",
]
