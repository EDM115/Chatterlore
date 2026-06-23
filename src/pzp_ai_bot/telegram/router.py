from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.service import (
    IncomingMessage,
    format_results,
    format_review_previews,
    format_scope_settings,
    parse_memory_export_format,
    parse_scope_settings_args,
)
from pzp_ai_bot.telegram.commands import command_help_text
from pzp_ai_bot.telegram.utils import (
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


def build_router(context: AppContext) -> Router:
    router = Router(name="pzp-memory")

    async def require_admin(message: Message) -> bool:
        user_id = message_user_id(message)
        ok = await context.admins.can_administer(message.bot, int(message.chat.id), user_id)
        if not ok:
            await message.answer("This command requires a bot admin or Telegram chat admin.")
        return ok

    @router.message(Command("ask"))
    async def ask(message: Message, command: CommandObject) -> None:
        question = (command.args or "").strip()
        if not question:
            await message.answer("Usage: /ask <question>")
            return
        answer = await context.memory.ask(scope_from_message(message), question)
        await message.answer(answer[:3900])

    @router.message(Command("memory_search"))
    async def memory_search(message: Message, command: CommandObject) -> None:
        query = (command.args or "").strip()
        if not query:
            await message.answer("Usage: /memory_search <query>")
            return
        results = await context.memory.search(scope_from_message(message), query)
        await message.answer(format_results(results)[:3900])

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(command_help_text()[:3900])

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
                await message.answer("Usage: /memory_export [md|jsonl] [scope=...]")
                return
        fmt = parse_memory_export_format(raw_format)
        try:
            scope = parse_scope(raw_scope, current=scope_from_message(message))
        except ValueError as exc:
            await message.answer(str(exc))
            return
        if scope.is_global and not context.admins.is_bot_admin(message_user_id(message)):
            await message.answer("Only configured bot admins can export all memory.")
            return
        payload = await context.memory.export_memory(
            scope if not scope.is_global else None, fmt=fmt
        )
        suffix = "jsonl" if fmt == "jsonl" else "md"
        document = BufferedInputFile(
            payload.encode("utf-8"), filename=f"memory_{scope.filename_stem}.{suffix}"
        )
        await message.answer_document(document=document, caption=f"Memory export for {scope.key}")

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        counts = await context.memory.stats()
        lines = ["Memory stats:"]
        for key, value in counts.items():
            lines.append(f"- {key}: {value}")
        await message.answer("\n".join(lines))

    @router.message(Command("learn_on"))
    async def learn_on(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await message.answer(f"Learning enabled for {scope.key}.")

    @router.message(Command("learn_off"))
    async def learn_off(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, False, message_user_id(message))
        await message.answer(f"Learning disabled for {scope.key}.")

    @router.message(Command("learn_status"))
    async def learn_status(message: Message) -> None:
        status = await context.memory.learn_status(scope_from_message(message))
        await message.answer("\n".join(f"{key}: {value}" for key, value in status.items()))

    @router.message(Command("memory_settings"))
    async def memory_settings(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
            updates = parse_scope_settings_args(args)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        if not updates:
            settings = await context.memory.get_scope_settings(scope)
            await message.answer(format_scope_settings(settings))
            return
        settings = await context.memory.set_scope_settings(
            scope, updates, updated_by=message_user_id(message)
        )
        await message.answer("Updated memory settings:\n" + format_scope_settings(settings))

    @router.message(Command("memory_prune"))
    async def memory_prune(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        result = await context.memory.prune_retention(scope)
        await message.answer(
            "Pruned memory retention:\n"
            + "\n".join(f"{key}: {value}" for key, value in result.items())
        )

    @router.message(Command("allow_chat"))
    async def allow_chat(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = Scope(int(message.chat.id), None)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await message.answer(f"Chat allowed: {scope.key}")

    @router.message(Command("deny_chat"))
    async def deny_chat(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = Scope(int(message.chat.id), None)
        await context.memory.set_learning(scope, False, message_user_id(message))
        await message.answer(f"Chat denied: {scope.key}")

    @router.message(Command("allow_topic"))
    async def allow_topic(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await message.answer(f"Topic allowed: {scope.key}")

    @router.message(Command("deny_topic"))
    async def deny_topic(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(
            scope_from_message(message), False, message_user_id(message)
        )
        await message.answer(f"Topic denied: {scope.key}")

    @router.message(Command("allow_user"))
    async def allow_user(message: Message, command: CommandObject) -> None:
        await _set_user_policy(context, message, command, allowed=True)

    @router.message(Command("deny_user"))
    async def deny_user(message: Message, command: CommandObject) -> None:
        await _set_user_policy(context, message, command, allowed=False)

    @router.message(Command("allow_admins"))
    async def allow_admins(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        arg = (command.args or "").strip().lower()
        enabled = arg in {"on", "true", "1", "yes"}
        await context.memory.set_allow_admins(
            scope_from_message(message), enabled, message_user_id(message)
        )
        await message.answer(
            f"Admin learning allowance is {'on' if enabled else 'off'} for this scope."
        )

    @router.message(Command("remember"))
    async def remember(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        text = (command.args or "").strip()
        if not text:
            await message.answer("Usage: /remember <text>")
            return
        item_id = await context.memory.remember(
            scope_from_message(message), text, message.message_id
        )
        await message.answer(f"Remembered note #{item_id}.")

    @router.message(Command("summarize_now"))
    async def summarize_now(message: Message) -> None:
        if not await require_admin(message):
            return
        count = await context.memory.summarize_now(scope_from_message(message))
        await message.answer(f"Summarized {count} pending messages.")

    @router.message(Command("memory_reviews"))
    async def memory_reviews(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        reviews = await context.memory.pending_reviews(scope)
        await message.answer(format_review_previews(reviews)[:3900])

    @router.message(Command("memory_approve"))
    async def memory_approve(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        try:
            review_id = int((command.args or "").strip().split()[0])
            item_id = await context.memory.approve_review(
                review_id, reviewed_by=message_user_id(message)
            )
        except (IndexError, ValueError) as exc:
            await message.answer(str(exc) if str(exc) else "Usage: /memory_approve <review_id>")
            return
        await message.answer(f"Approved memory review #{review_id} as item #{item_id}.")

    @router.message(Command("memory_reject"))
    async def memory_reject(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        try:
            review_id = int((command.args or "").strip().split()[0])
            await context.memory.reject_review(review_id, reviewed_by=message_user_id(message))
        except (IndexError, ValueError) as exc:
            await message.answer(str(exc) if str(exc) else "Usage: /memory_reject <review_id>")
            return
        await message.answer(f"Rejected memory review #{review_id}.")

    @router.message(Command("forget"))
    async def forget(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        if scope.is_global and not context.admins.is_bot_admin(message_user_id(message)):
            await message.answer("Only configured bot admins can forget all memory.")
            return
        _ok, response = await context.memory.forget(
            scope, message_user_id(message) or 0, args.get("confirm")
        )
        await message.answer(response)

    @router.message(F.text | F.caption)
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

    return router


async def _set_user_policy(
    context: AppContext, message: Message, command: CommandObject, *, allowed: bool
) -> None:
    user_id = message_user_id(message)
    if not await context.admins.can_administer(message.bot, int(message.chat.id), user_id):
        await message.answer("This command requires a bot admin or Telegram chat admin.")
        return
    target_id: int | None = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = int(message.reply_to_message.from_user.id)
    elif command.args:
        try:
            target_id = int(command.args.strip().split()[0])
        except ValueError:
            target_id = None
    if target_id is None:
        await message.answer("Usage: reply with /allow_user or pass /allow_user <telegram_user_id>")
        return
    await context.memory.set_user_policy(scope_from_message(message), target_id, allowed, user_id)
    await message.answer(f"User {target_id} {'allowed' if allowed else 'denied'} for this scope.")
