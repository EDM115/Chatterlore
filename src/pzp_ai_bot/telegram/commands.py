from __future__ import annotations

from typing import Any

from aiogram.types import BotCommand

COMMAND_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("ask", "Answer from current chat/topic memory"),
    ("memory_search", "Search scoped memory"),
    ("memory_export", "Export memory as Markdown or JSONL"),
    ("memory_status", "Show scoped memory and AI usage status"),
    ("memory_scopes", "List known chat/topic memory scopes"),
    ("memory_debug", "Explain skipped-message reasons"),
    ("memory_items", "Browse recent memory item IDs"),
    ("memory_item_delete", "Delete one memory item"),
    ("memory_item_refresh", "Refresh one memory item"),
    ("memory_settings", "View or edit scoped memory settings"),
    ("settings", "View or edit scoped AI and memory settings"),
    ("memory_prune", "Apply retention pruning for this scope"),
    ("memory_reviews", "List pending memory review proposals"),
    ("memory_approve", "Approve a memory review proposal"),
    ("memory_reject", "Reject a memory review proposal"),
    ("memory_notes", "List manual memory notes"),
    ("memory_note_edit", "Edit a manual memory note"),
    ("memory_note_delete", "Delete a manual memory note"),
    ("memory_profiles", "List cross-chat memory profiles"),
    ("memory_profile_set", "Create or replace a memory profile"),
    ("memory_profile_allow", "Allow profile retrieval for a scope"),
    ("memory_templates", "List summary templates"),
    ("memory_template_set", "Create or update a summary template"),
    ("system_prompts", "List system prompt templates"),
    ("system_prompt_set", "Create or update a system prompt"),
    ("summarize_range", "Summarize a bounded message range"),
    ("stats", "Show memory counts"),
    ("audit_events", "Show recent bot audit events"),
    ("runtime_settings", "View or edit safe runtime settings"),
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
            "/ask [profile=name] [provider=name] [system=name] [model=name] "
            "[reasoning=low|medium|high] <question>",
            "/memory_export [md|jsonl] [scope=this_topic|this_chat|all|<chat_id>:<thread_id>]",
            "/memory_status [scope=this_topic|this_chat|all|<chat_id>:<thread_id>]",
            "/memory_scopes [scope=this_chat|all|<chat_id>:<thread_id>]",
            "/memory_debug [scope=this_topic|this_chat|<chat_id>:<thread_id>] "
            "[message_id=N] [limit=N]",
            "/memory_items [scope=this_topic|this_chat|all|<chat_id>:<thread_id>] "
            "[kind=name] [limit=N]",
            "/memory_item_delete <id> [scope=this_topic|this_chat|all|<chat_id>:<thread_id>]",
            "/memory_item_refresh <id> [mode=embed|summarize|both] "
            "[scope=this_topic|this_chat|all|<chat_id>:<thread_id>]",
            "/memory_settings [top_k=N] [summary_batch_size=N] [summary_model=name] "
            "[provider=name] [chat_model=name] [embedding_model=name] "
            "[reasoning=low|medium|high] [system_prompt=name] "
            "[raw_retention_days=N|off] [knowledge_retention_days=N|off] "
            "[review_before_compact=on|off]",
            "/memory_reviews [scope=this_topic|this_chat|all|<chat_id>:<thread_id>]",
            "/memory_approve <review_id>",
            "/memory_reject <review_id>",
            "/remember [tags=a,b] [priority=N] [source=url] <text>",
            "/memory_notes [scope=this_topic|this_chat|all|<chat_id>:<thread_id>] [tag=name]",
            "/memory_note_edit <id> [tags=a,b] [priority=N|off] [source=url|off] [text]",
            "/memory_note_delete <id>",
            "/memory_profile_set <name> sources=<scope,scope>",
            "/memory_profile_allow <name> "
            "[scope=this_topic|this_chat|<chat_id>:<thread_id>] [enabled=on|off]",
            "/ask [profile=name] <question>",
            "/memory_search [profile=name] <query>",
            "/memory_templates",
            "/memory_template_set <name> <prompt>",
            "/system_prompts",
            "/system_prompt_set <name> <prompt>",
            "/summarize_now [template=name] [provider=name] [model=name] "
            "[reasoning=low|medium|high] [prompt='one-shot instruction']",
            "/summarize_range start=<message_id> end=<message_id> [template=name] "
            "[provider=name] [model=name] [prompt='one-shot instruction']",
            "/audit_events [scope=this_topic|this_chat|all] [event_type=name] [limit=N]",
            "/runtime_settings [reload=on] [key=value ...]",
            "/settings [key=value ...] then /settings confirm=<token>",
            "/forget scope=this_topic|this_chat|all confirm=<token>",
        ]
    )
    return "\n".join(lines)


async def register_bot_commands(bot: Any) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
