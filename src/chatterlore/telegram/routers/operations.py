from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from chatterlore.app_context import AppContext
from chatterlore.memory.scope import parse_scope
from chatterlore.memory.service import (
    format_audit_events,
    format_memory_status,
    format_runtime_settings,
    format_scope_statuses,
    format_skipped_messages,
    parse_bool,
    parse_optional_limit,
    parse_runtime_settings_args,
)
from chatterlore.telegram.routers.shared import RouterGuards
from chatterlore.telegram.safe_api import safe_answer
from chatterlore.telegram.utils import message_user_id, parse_key_values, scope_from_message


def register_operations_handlers(router: Router, context: AppContext, guards: RouterGuards) -> None:
    @router.message(Command("stats"))
    async def stats(message: Message, command: CommandObject) -> None:
        args = parse_key_values(command.args)
        if not args:
            counts = await context.memory.stats()
            lines = ["Memory stats:"]
            for key, value in counts.items():
                lines.append(f"- {key}: {value}")
            await safe_answer(message, "\n".join(lines))
            return
        if not await guards.require_admin(message):
            return
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        status = await context.memory.memory_status(None if scope.is_global else scope)
        await safe_answer(message, format_memory_status(status)[:3900])

    @router.message(Command("memory_status"))
    async def memory_status(message: Message, command: CommandObject) -> None:
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
        status = await context.memory.memory_status(None if scope.is_global else scope)
        await safe_answer(message, format_memory_status(status)[:3900])

    @router.message(Command("memory_scopes"))
    async def memory_scopes(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(args.get("scope", "this_chat"), current=scope_from_message(message))
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        scopes = await context.memory.known_scopes(None if scope.is_global else scope)
        await safe_answer(message, format_scope_statuses(scopes)[:3900])

    @router.message(Command("memory_debug"))
    async def memory_debug(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
            limit = parse_optional_limit(args.get("limit"), default=10, maximum=30)
            message_id = int(args["message_id"]) if "message_id" in args else None
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        skipped = await context.memory.skipped_messages(
            scope, limit=limit, telegram_message_id=message_id
        )
        await safe_answer(message, format_skipped_messages(skipped)[:3900])

    @router.message(Command("audit_events"))
    async def audit_events(message: Message, command: CommandObject) -> None:
        if not await guards.require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            limit = int(args.get("limit", "20"))
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        if not await guards.require_scope_admin(message, scope):
            return
        events = await context.memory.audit_events(
            limit=limit, event_type=args.get("event_type"), scope=None if scope.is_global else scope
        )
        await safe_answer(message, format_audit_events(events)[:3900])

    @router.message(Command("runtime_settings"))
    async def runtime_settings(message: Message, command: CommandObject) -> None:
        if not await guards.require_bot_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            reload_requested = parse_bool(args.pop("reload")) if "reload" in args else False
            updates = parse_runtime_settings_args(args)
            unknown = sorted(
                set(args)
                - set(updates)
                - {"candidate_limit", "top_k", "provider", "system_prompt", "raw_retention_days"}
            )
            if unknown:
                raise ValueError(f"unknown runtime setting: {unknown[0]}")
            if reload_requested:
                await context.memory.refresh_runtime_settings()
            if updates:
                await context.memory.set_runtime_settings(
                    updates, updated_by=message_user_id(message)
                )
            settings = await context.memory.list_runtime_settings()
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_runtime_settings(settings)[:3900])


__all__ = ["register_operations_handlers"]
