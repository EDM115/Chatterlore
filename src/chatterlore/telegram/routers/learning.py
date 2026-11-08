from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from chatterlore.app_context import AppContext
from chatterlore.memory.scope import Scope
from chatterlore.telegram.routers.shared import RouterGuards, parse_on_off
from chatterlore.telegram.safe_api import safe_answer
from chatterlore.telegram.utils import message_user_id, scope_from_message


def register_learning_status_handlers(
    router: Router, context: AppContext, guards: RouterGuards
) -> None:
    @router.message(Command("learn_on"))
    async def learn_on(message: Message) -> None:
        if not await guards.require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await safe_answer(message, f"Learning enabled for {scope.key}.")

    @router.message(Command("learn_off"))
    async def learn_off(message: Message) -> None:
        if not await guards.require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, False, message_user_id(message))
        await safe_answer(message, f"Learning disabled for {scope.key}.")

    @router.message(Command("learn_status"))
    async def learn_status(message: Message) -> None:
        status = await context.memory.learn_status(scope_from_message(message))
        await safe_answer(message, "\n".join(f"{key}: {value}" for key, value in status.items()))


def register_learning_policy_handlers(
    router: Router, context: AppContext, guards: RouterGuards
) -> None:
    @router.message(Command("allow_chat"))
    async def allow_chat(message: Message) -> None:
        if not await guards.require_admin(message):
            return
        scope = Scope(int(message.chat.id), None)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await safe_answer(message, f"Chat allowed: {scope.key}")

    @router.message(Command("deny_chat"))
    async def deny_chat(message: Message) -> None:
        if not await guards.require_admin(message):
            return
        scope = Scope(int(message.chat.id), None)
        await context.memory.set_learning(scope, False, message_user_id(message))
        await safe_answer(message, f"Chat denied: {scope.key}")

    @router.message(Command("allow_topic"))
    async def allow_topic(message: Message) -> None:
        if not await guards.require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await safe_answer(message, f"Topic allowed: {scope.key}")

    @router.message(Command("deny_topic"))
    async def deny_topic(message: Message) -> None:
        if not await guards.require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(
            scope_from_message(message), False, message_user_id(message)
        )
        await safe_answer(message, f"Topic denied: {scope.key}")

    @router.message(Command("allow_user"))
    async def allow_user(message: Message, command: CommandObject) -> None:
        await _set_user_policy(context, message, command, allowed=True)

    @router.message(Command("deny_user"))
    async def deny_user(message: Message, command: CommandObject) -> None:
        await _set_user_policy(context, message, command, allowed=False)

    @router.message(Command("allow_admins"))
    async def allow_admins(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        try:
            enabled = parse_on_off(command.args or "")
        except ValueError:
            await safe_answer(message, "Usage: /allow_admins on|off")
            return
        await context.memory.set_allow_admins(
            scope_from_message(message), enabled, message_user_id(message)
        )
        await safe_answer(
            message, f"Admin learning allowance is {'on' if enabled else 'off'} for this scope."
        )


async def _set_user_policy(
    context: AppContext, message: Message, command: CommandObject, *, allowed: bool
) -> None:
    user_id = message_user_id(message)
    if not await context.admins.can_administer(message.bot, int(message.chat.id), user_id):
        await safe_answer(message, "This command requires a bot admin or Telegram chat admin.")
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
        await safe_answer(
            message, "Usage: reply with /allow_user or pass /allow_user <telegram_user_id>"
        )
        return
    await context.memory.set_user_policy(scope_from_message(message), target_id, allowed, user_id)
    await safe_answer(
        message, f"User {target_id} {'allowed' if allowed else 'denied'} for this scope."
    )


__all__ = ["register_learning_policy_handlers", "register_learning_status_handlers"]
