from __future__ import annotations

from typing import Any

from aiogram.types import BotCommand

COMMAND_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("ask", "Answer from current chat/topic memory"),
    ("memory_search", "Search scoped memory"),
    ("memory_export", "Export memory as Markdown or JSONL"),
    ("stats", "Show memory counts"),
    ("learn_on", "Enable learning for this scope"),
    ("learn_off", "Disable learning for this scope"),
    ("learn_status", "Show learning policy for this scope"),
    ("allow_chat", "Allow learning for the whole chat"),
    ("deny_chat", "Deny learning for the whole chat"),
    ("allow_topic", "Allow learning for this topic"),
    ("deny_topic", "Deny learning for this topic"),
    ("allow_user", "Allow a user by ID or reply"),
    ("deny_user", "Deny a user by ID or reply"),
    ("allow_admins", "Allow chat admins as learnable authors"),
    ("remember", "Store a durable manual note"),
    ("summarize_now", "Summarize pending learned messages"),
    ("forget", "Delete memory after confirmation"),
    ("help", "Show command help"),
)

BOT_COMMANDS = [
    BotCommand(command=command, description=description)
    for command, description in COMMAND_DESCRIPTIONS
]


def command_help_text() -> str:
    lines = ["Commands:"]
    for command, description in COMMAND_DESCRIPTIONS:
        lines.append(f"/{command} - {description}")
    lines.extend(
        [
            "",
            "Useful forms:",
            "/ask <question>",
            "/memory_export [md|jsonl] [scope=this_topic|this_chat|all|<chat_id>:<thread_id>]",
            "/forget scope=this_topic|this_chat|all confirm=<token>",
        ]
    )
    return "\n".join(lines)


async def register_bot_commands(bot: Any) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
