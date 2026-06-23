from __future__ import annotations

import shlex

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.service import (
    AIRequestOptions,
    IncomingMessage,
    format_audit_events,
    format_memory_profiles,
    format_note_previews,
    format_range_summary,
    format_results,
    format_review_previews,
    format_runtime_settings,
    format_scope_settings,
    format_summary_templates,
    format_system_prompt_templates,
    parse_bool,
    parse_memory_export_format,
    parse_note_create_args,
    parse_note_edit_args,
    parse_profile_sources,
    parse_runtime_settings_args,
    parse_scope_settings_args,
)
from pzp_ai_bot.telegram.commands import command_help_text
from pzp_ai_bot.telegram.safe_api import safe_answer, safe_answer_document
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

TELEGRAM_DOCUMENT_MAX_BYTES = 50 * 1024 * 1024
EXPORT_DOCUMENT_CHUNK_BYTES = 45 * 1024 * 1024


def build_router(context: AppContext) -> Router:
    router = Router(name="pzp-memory")

    async def require_admin(message: Message) -> bool:
        user_id = message_user_id(message)
        ok = await context.admins.can_administer(message.bot, int(message.chat.id), user_id)
        if not ok:
            await safe_answer(message, "This command requires a bot admin or Telegram chat admin.")
        return ok

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
        if admin_override and not await require_admin(message):
            return
        try:
            answer = await context.memory.ask(
                scope_from_message(message),
                question,
                profile_name=profile_name,
                ai_options=ai_options,
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, answer[:3900])

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
        if admin_override and not await require_admin(message):
            return
        try:
            results = await context.memory.search(
                scope_from_message(message), query, profile_name=profile_name, ai_options=ai_options
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        await safe_answer(message, format_results(results)[:3900])

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await safe_answer(message, command_help_text()[:3900])

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
        if scope.is_global and not context.admins.is_bot_admin(message_user_id(message)):
            await safe_answer(message, "Only configured bot admins can export all memory.")
            return
        payload = await context.memory.export_memory(
            scope if not scope.is_global else None, fmt=fmt
        )
        suffix = "jsonl" if fmt == "jsonl" else "md"
        documents = export_documents(payload, filename=f"memory_{scope.filename_stem}.{suffix}")
        for index, document in enumerate(documents, start=1):
            caption = f"Memory export for {scope.key}"
            if len(documents) > 1:
                caption = f"{caption} ({index}/{len(documents)})"
            if not await safe_answer_document(message, document=document, caption=caption):
                await safe_answer(
                    message,
                    "Memory export could not be sent through Telegram. Try a narrower scope "
                    "or run the CLI export locally.",
                )
                return

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        counts = await context.memory.stats()
        lines = ["Memory stats:"]
        for key, value in counts.items():
            lines.append(f"- {key}: {value}")
        await safe_answer(message, "\n".join(lines))

    @router.message(Command("audit_events"))
    async def audit_events(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
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
        if scope.is_global and not context.admins.is_bot_admin(message_user_id(message)):
            await safe_answer(message, "Only configured bot admins can audit all memory events.")
            return
        events = await context.memory.audit_events(
            limit=limit, event_type=args.get("event_type"), scope=None if scope.is_global else scope
        )
        await safe_answer(message, format_audit_events(events)[:3900])

    @router.message(Command("runtime_settings"))
    async def runtime_settings(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            reload_requested = parse_bool(args.pop("reload")) if "reload" in args else False
            updates = parse_runtime_settings_args(args)
            unknown = sorted(
                set(args)
                - set(updates)
                - {"top_k", "provider", "system_prompt", "raw_retention_days"}
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

    @router.message(Command("learn_on"))
    async def learn_on(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await safe_answer(message, f"Learning enabled for {scope.key}.")

    @router.message(Command("learn_off"))
    async def learn_off(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, False, message_user_id(message))
        await safe_answer(message, f"Learning disabled for {scope.key}.")

    @router.message(Command("learn_status"))
    async def learn_status(message: Message) -> None:
        status = await context.memory.learn_status(scope_from_message(message))
        await safe_answer(message, "\n".join(f"{key}: {value}" for key, value in status.items()))

    @router.message(Command("memory_settings"))
    async def memory_settings(message: Message, command: CommandObject) -> None:
        await _scope_settings_command(context, message, command)

    @router.message(Command("settings"))
    async def settings(message: Message, command: CommandObject) -> None:
        await _scope_settings_command(context, message, command)

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
            await safe_answer(message, str(exc))
            return
        result = await context.memory.prune_retention(scope)
        await safe_answer(
            message,
            "Pruned memory retention:\n"
            + "\n".join(f"{key}: {value}" for key, value in result.items()),
        )

    @router.message(Command("allow_chat"))
    async def allow_chat(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = Scope(int(message.chat.id), None)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await safe_answer(message, f"Chat allowed: {scope.key}")

    @router.message(Command("deny_chat"))
    async def deny_chat(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = Scope(int(message.chat.id), None)
        await context.memory.set_learning(scope, False, message_user_id(message))
        await safe_answer(message, f"Chat denied: {scope.key}")

    @router.message(Command("allow_topic"))
    async def allow_topic(message: Message) -> None:
        if not await require_admin(message):
            return
        scope = scope_from_message(message)
        await context.memory.set_learning(scope, True, message_user_id(message))
        await safe_answer(message, f"Topic allowed: {scope.key}")

    @router.message(Command("deny_topic"))
    async def deny_topic(message: Message) -> None:
        if not await require_admin(message):
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
        if not await require_admin(message):
            return
        arg = (command.args or "").strip().lower()
        enabled = arg in {"on", "true", "1", "yes"}
        await context.memory.set_allow_admins(
            scope_from_message(message), enabled, message_user_id(message)
        )
        await safe_answer(
            message, f"Admin learning allowance is {'on' if enabled else 'off'} for this scope."
        )

    @router.message(Command("remember"))
    async def remember(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
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

    @router.message(Command("memory_notes"))
    async def memory_notes(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        args = parse_key_values(command.args)
        try:
            scope = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
        except ValueError as exc:
            await safe_answer(message, str(exc))
            return
        notes = await context.memory.list_notes(scope, tag=args.get("tag"))
        await safe_answer(message, format_note_previews(notes)[:3900])

    @router.message(Command("memory_note_edit"))
    async def memory_note_edit(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
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
        if not await require_admin(message):
            return
        try:
            item_id = int((command.args or "").strip().split()[0])
            await context.memory.delete_note(scope_from_message(message), item_id)
        except (IndexError, ValueError) as exc:
            await safe_answer(message, str(exc) if str(exc) else "Usage: /memory_note_delete <id>")
            return
        await safe_answer(message, f"Deleted manual note #{item_id}.")

    @router.message(Command("summarize_now"))
    async def summarize_now(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
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
            await safe_answer(message, str(exc))
            return
        reviews = await context.memory.pending_reviews(scope)
        await safe_answer(message, format_review_previews(reviews)[:3900])

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
            await safe_answer(
                message, str(exc) if str(exc) else "Usage: /memory_approve <review_id>"
            )
            return
        await safe_answer(message, f"Approved memory review #{review_id} as item #{item_id}.")

    @router.message(Command("memory_reject"))
    async def memory_reject(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        try:
            review_id = int((command.args or "").strip().split()[0])
            await context.memory.reject_review(review_id, reviewed_by=message_user_id(message))
        except (IndexError, ValueError) as exc:
            await safe_answer(
                message, str(exc) if str(exc) else "Usage: /memory_reject <review_id>"
            )
            return
        await safe_answer(message, f"Rejected memory review #{review_id}.")

    @router.message(Command("memory_profiles"))
    async def memory_profiles(message: Message) -> None:
        if not await require_admin(message):
            return
        profiles = await context.memory.list_memory_profiles()
        await safe_answer(message, format_memory_profiles(profiles)[:3900])

    @router.message(Command("memory_profile_set"))
    async def memory_profile_set(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
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
        if not await require_admin(message):
            return
        parts = (command.args or "").strip().split(None, 1)
        if not parts or not parts[0]:
            await safe_answer(
                message,
                "Usage: /memory_profile_allow <name> [scope=...] [enabled=on|off] [learn=on|off]",
            )
            return
        name = parts[0]
        args = parse_key_values(parts[1] if len(parts) > 1 else None)
        try:
            destination = parse_scope(
                args.get("scope", "this_topic"), current=scope_from_message(message)
            )
            enabled = _parse_on_off(args.get("enabled", "on"))
            can_learn = _parse_on_off(args.get("learn", "off"))
            profile = await context.memory.allow_memory_profile(
                destination=destination,
                profile_name=name,
                can_query=enabled,
                can_learn=can_learn,
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
        if not await require_admin(message):
            return
        templates = await context.memory.list_summary_templates()
        await safe_answer(message, format_summary_templates(templates)[:3900])

    @router.message(Command("memory_template_set"))
    async def memory_template_set(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
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
        if not await require_admin(message):
            return
        templates = await context.memory.list_system_prompt_templates()
        await safe_answer(message, format_system_prompt_templates(templates)[:3900])

    @router.message(Command("system_prompt_set"))
    async def system_prompt_set(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
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

    @router.message(Command("summarize_range"))
    async def summarize_range(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
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
        await safe_answer(message, format_range_summary(result)[:3900])

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
            await safe_answer(message, str(exc))
            return
        if scope.is_global and not context.admins.is_bot_admin(message_user_id(message)):
            await safe_answer(message, "Only configured bot admins can forget all memory.")
            return
        _ok, response = await context.memory.forget(
            scope, message_user_id(message) or 0, args.get("confirm")
        )
        await safe_answer(message, response)

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


async def _scope_settings_command(
    context: AppContext, message: Message, command: CommandObject
) -> None:
    user_id = message_user_id(message)
    if not await context.admins.can_administer(message.bot, int(message.chat.id), user_id):
        await safe_answer(message, "This command requires a bot admin or Telegram chat admin.")
        return
    args = parse_key_values(command.args)
    try:
        scope = parse_scope(args.get("scope", "this_topic"), current=scope_from_message(message))
        updates = parse_scope_settings_args(args)
    except ValueError as exc:
        await safe_answer(message, str(exc))
        return
    if not updates:
        settings = await context.memory.get_scope_settings(scope)
        await safe_answer(message, format_scope_settings(settings))
        return
    settings = await context.memory.set_scope_settings(
        scope, updates, updated_by=message_user_id(message)
    )
    await safe_answer(message, "Updated memory settings:\n" + format_scope_settings(settings))


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
        return shlex.split(raw or "")
    except ValueError as exc:
        raise ValueError(f"invalid quoted arguments: {exc}") from exc


def _extract_profile_arg(raw: str | None) -> tuple[str | None, str]:
    profile_name: str | None = None
    kept: list[str] = []
    for token in (raw or "").strip().split():
        if token.startswith("profile="):
            profile_name = token.removeprefix("profile=").strip() or None
        else:
            kept.append(token)
    return profile_name, " ".join(kept).strip()


def _parse_on_off(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean: {raw}")


def export_documents(
    payload: str, *, filename: str, max_bytes: int = EXPORT_DOCUMENT_CHUNK_BYTES
) -> list[BufferedInputFile]:
    if max_bytes <= 0 or max_bytes > TELEGRAM_DOCUMENT_MAX_BYTES:
        raise ValueError("max_bytes must be between 1 and the Telegram document upload limit")
    data = payload.encode("utf-8")
    if len(data) <= max_bytes:
        return [BufferedInputFile(data, filename=filename)]
    stem, dot, suffix = filename.rpartition(".")
    if not dot:
        stem = filename
        suffix = "txt"
    total = (len(data) + max_bytes - 1) // max_bytes
    documents: list[BufferedInputFile] = []
    for index, start in enumerate(range(0, len(data), max_bytes), start=1):
        chunk_name = f"{stem}.part{index:03d}of{total:03d}.{suffix}"
        documents.append(BufferedInputFile(data[start : start + max_bytes], filename=chunk_name))
    return documents
