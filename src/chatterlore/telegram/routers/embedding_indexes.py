from __future__ import annotations

import json
import shlex

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from chatterlore.app_context import AppContext
from chatterlore.memory.embedding_control_errors import control_error_code
from chatterlore.memory.scope import parse_scope
from chatterlore.telegram.routers.shared import RouterGuards
from chatterlore.telegram.safe_api import safe_answer
from chatterlore.telegram.utils import message_user_id, scope_from_message

_USAGE = "\n".join(
    [
        "/embedding_index status [scope=all] [id=N]",
        "/embedding_index preview|start profile=name [scope=...] [model=...]",
        "/embedding_index run id=N [batches=1..10] | resume|cancel id=N version=N",
        "/embedding_index activate id=N version=N confirm='ACTIVATE N'",
    ]
)


def _arguments(raw: str | None) -> tuple[str, dict[str, str]]:
    tokens = shlex.split(raw or "status")
    if not tokens:
        return "status", {}
    action, *pairs = tokens
    options: dict[str, str] = {}
    allowed = {
        "status": {"scope", "id"},
        "preview": {"scope", "profile", "model"},
        "start": {"scope", "profile", "model"},
        "run": {"id", "batches"},
        "resume": {"id", "version"},
        "cancel": {"id", "version"},
        "activate": {"id", "version", "confirm"},
    }
    if action not in allowed:
        raise ValueError("embedding_command_invalid")
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not value or key not in allowed[action] or key in options:
            raise ValueError("embedding_command_invalid")
        options[key] = value
    return action, options


def _integer(
    options: dict[str, str], name: str, *, default: str | None = None, maximum: int | None = None
) -> int:
    value = options.get(name, default)
    if value is None or not value.isascii() or not value.isdigit():
        raise ValueError("embedding_command_invalid")
    number = int(value)
    if number < 1 or (maximum is not None and number > maximum):
        raise ValueError("embedding_command_invalid")
    return number


def register_embedding_index_handlers(
    router: Router, context: AppContext, guards: RouterGuards
) -> None:
    @router.message(Command("embedding_index"))
    async def embedding_index(message: Message, command: CommandObject) -> None:
        if not await guards.require_bot_admin(message):
            return
        try:
            action, options = _arguments(command.args)
            service = context.memory.embedding_indexes
            actor = message_user_id(message)
            if action in {"preview", "start"} and "profile" not in options:
                raise ValueError("embedding_command_invalid")
            if action in {"status", "preview", "start"}:
                scope = parse_scope(
                    options.get("scope", "this_topic"), current=scope_from_message(message)
                )
                if action == "status":
                    result = (
                        await service.get_job(_integer(options, "id"))
                        if "id" in options
                        else await service.status(scope)
                    )
                elif action == "preview":
                    result = await service.preview(
                        scope, options["profile"], model=options.get("model")
                    )
                else:
                    result = await service.create_job(
                        scope, options["profile"], model=options.get("model"), actor_user_id=actor
                    )
            else:
                job_id = _integer(options, "id")
                if action == "run":
                    result = await service.run_job(
                        job_id, max_batches=_integer(options, "batches", default="1", maximum=10)
                    )
                else:
                    version = _integer(options, "version")
                    if action == "activate":
                        if options.get("confirm") != f"ACTIVATE {job_id}":
                            raise ValueError("embedding_confirmation_required")
                        result = await service.activate_job(
                            job_id,
                            version=version,
                            confirmation=options["confirm"],
                            actor_user_id=actor,
                        )
                    elif action == "resume":
                        result = await service.resume_job(
                            job_id, version=version, actor_user_id=actor
                        )
                    else:
                        result = await service.cancel_job(
                            job_id, version=version, actor_user_id=actor
                        )
        except (ValueError, RuntimeError) as exc:
            await safe_answer(
                message, f"Index request rejected ({control_error_code(exc)}).\n{_USAGE}"[:3900]
            )
            return
        await safe_answer(message, json.dumps(result, ensure_ascii=False, default=str)[:3900])
