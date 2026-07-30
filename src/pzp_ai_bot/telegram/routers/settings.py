from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import parse_scope
from pzp_ai_bot.memory.service import (
    format_memory_profiles,
    format_scope_settings,
    format_settings_change_preview,
    format_summary_templates,
    format_system_prompt_templates,
    parse_profile_sources,
    parse_scope_settings_args,
)
from pzp_ai_bot.telegram.routers.shared import RouterGuards, parse_on_off
from pzp_ai_bot.telegram.safe_api import safe_answer
from pzp_ai_bot.telegram.utils import message_user_id, parse_key_values, scope_from_message


def register_scope_settings_handlers(
    router: Router, context: AppContext, guards: RouterGuards
) -> None:
    @router.message(Command("memory_settings"))
    async def memory_settings(message: Message, command: CommandObject) -> None:
        await _scope_settings_command(context, message, command, require_confirmation=False)

    @router.message(Command("settings"))
    async def settings(message: Message, command: CommandObject) -> None:
        await _scope_settings_command(context, message, command, require_confirmation=True)

    @router.message(Command("memory_prune"))
    async def memory_prune(message: Message, command: CommandObject) -> None:
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
        result = await context.memory.prune_retention(scope)
        await safe_answer(
            message,
            "Pruned memory retention:\n"
            + "\n".join(f"{key}: {value}" for key, value in result.items()),
        )


def register_profile_template_handlers(
    router: Router, context: AppContext, guards: RouterGuards
) -> None:
    @router.message(Command("memory_profiles"))
    async def memory_profiles(message: Message) -> None:
        if not await guards.require_bot_admin(message):
            return
        profiles = await context.memory.list_memory_profiles()
        await safe_answer(message, format_memory_profiles(profiles)[:3900])

    @router.message(Command("memory_profile_set"))
    async def memory_profile_set(message: Message, command: CommandObject) -> None:
        if not await guards.require_bot_admin(message):
            return
        parts = (command.args or "").strip().split(None, 1)
        if len(parts) < 2:
            await safe_answer(
                message,
                "Usage: /memory_profile_set <name> sources=<scope,scope> [description=text]",
            )
            return
        name, rest = parts
        args = parse_key_values(rest)
        if "sources" not in args:
            await safe_answer(
                message,
                "Usage: /memory_profile_set <name> sources=<scope,scope> [description=text]",
            )
            return
        try:
            sources = parse_profile_sources(args["sources"], current=scope_from_message(message))
            profile = await context.memory.set_memory_profile(
                name,
                sources,
                description=args.get("description"),
                updated_by=message_user_id(message),
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_memory_profiles([profile])[:3900])

    @router.message(Command("memory_profile_allow"))
    async def memory_profile_allow(message: Message, command: CommandObject) -> None:
        if not await guards.require_bot_admin(message):
            return
        parts = (command.args or "").strip().split(None, 1)
        if not parts or not parts[0]:
            await safe_answer(
                message, "Usage: /memory_profile_allow <name> [scope=...] [enabled=on|off]"
            )
            return
        name = parts[0]
        args = parse_key_values(parts[1] if len(parts) > 1 else None)
        try:
            unsupported = sorted(set(args) - {"scope", "enabled"})
            if unsupported:
                raise ValueError(f"unsupported argument(s): {', '.join(unsupported)}")
            destination = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
            enabled = parse_on_off(args.get("enabled", "on"))
            profile = await context.memory.allow_memory_profile(
                destination=destination,
                profile_name=name,
                can_query=enabled,
                updated_by=message_user_id(message),
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(
            message,
            f"Profile {profile.name} query permission is {'on' if enabled else 'off'} "
            f"for {destination.key}.",
        )

    @router.message(Command("memory_templates"))
    async def memory_templates(message: Message) -> None:
        if not await guards.require_admin(message):
            return
        templates = await context.memory.list_summary_templates()
        await safe_answer(message, format_summary_templates(templates)[:3900])

    @router.message(Command("memory_template_set"))
    async def memory_template_set(message: Message, command: CommandObject) -> None:
        if not await guards.require_bot_admin(message):
            return
        parts = (command.args or "").strip().split(None, 1)
        if len(parts) < 2:
            await safe_answer(message, "Usage: /memory_template_set <name> <prompt>")
            return
        try:
            template = await context.memory.set_summary_template(
                parts[0], parts[1], updated_by=message_user_id(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_summary_templates([template])[:3900])

    @router.message(Command("system_prompts"))
    async def system_prompts(message: Message) -> None:
        if not await guards.require_admin(message):
            return
        templates = await context.memory.list_system_prompt_templates()
        await safe_answer(message, format_system_prompt_templates(templates)[:3900])

    @router.message(Command("system_prompt_set"))
    async def system_prompt_set(message: Message, command: CommandObject) -> None:
        if not await guards.require_bot_admin(message):
            return
        parts = (command.args or "").strip().split(None, 1)
        if len(parts) < 2:
            await safe_answer(message, "Usage: /system_prompt_set <name> <prompt>")
            return
        try:
            template = await context.memory.set_system_prompt_template(
                parts[0], parts[1], updated_by=message_user_id(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_system_prompt_templates([template])[:3900])


async def _scope_settings_command(
    context: AppContext, message: Message, command: CommandObject, *, require_confirmation: bool
) -> None:
    user_id = message_user_id(message)
    if not await context.admins.can_administer(message.bot, int(message.chat.id), user_id):
        await safe_answer(message, "This command requires a bot admin or Telegram chat admin.")
        return
    args = parse_key_values(command.args)
    confirm = args.pop("confirm", None)
    try:
        scope = parse_scope(args.get("scope", "this_topic"), current=scope_from_message(message))
        updates = parse_scope_settings_args(args)
    except ValueError as exc:
        await safe_answer(message, str(exc))
        return
    if not context.admins.is_bot_admin(user_id):
        if scope.is_global:
            await safe_answer(message, "Only configured bot admins can target all scopes.")
            return
        if scope.chat_id != int(message.chat.id):
            await safe_answer(message, "Only configured bot admins can target another chat scope.")
            return
    if confirm:
        try:
            preview = await context.memory.confirm_scope_settings(
                scope, user_id=user_id or 0, confirm=confirm
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_settings_change_preview(preview)[:3900])
        return
    if not updates:
        settings = await context.memory.get_scope_settings(scope)
        await safe_answer(message, format_scope_settings(settings))
        return
    if require_confirmation:
        preview = await context.memory.preview_scope_settings(scope, updates, user_id=user_id or 0)
        await safe_answer(message, format_settings_change_preview(preview)[:3900])
        return
    settings = await context.memory.set_scope_settings(scope, updates, updated_by=user_id)
    await safe_answer(message, "Updated memory settings:\n" + format_scope_settings(settings))


__all__ = ["register_profile_template_handlers", "register_scope_settings_handlers"]
