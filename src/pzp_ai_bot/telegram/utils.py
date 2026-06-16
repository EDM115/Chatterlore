from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pzp_ai_bot.memory.scope import Scope


def scope_from_message(message: Any) -> Scope:
    chat_id = int(message.chat.id)
    is_topic = bool(getattr(message, "is_topic_message", False))
    thread_id = getattr(message, "message_thread_id", None) if is_topic else None
    return Scope(chat_id=chat_id, thread_id=thread_id)


def text_from_message(message: Any) -> str:
    return (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()


def message_user_id(message: Any) -> int | None:
    user = getattr(message, "from_user", None)
    if user is None:
        return None
    return int(user.id)


def message_username(message: Any) -> str | None:
    user = getattr(message, "from_user", None)
    if user is None:
        return None
    return getattr(user, "username", None)


def message_created_at(message: Any) -> datetime:
    value = getattr(message, "date", None)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.now(UTC)


def reply_to_message_id(message: Any) -> int | None:
    reply = getattr(message, "reply_to_message", None)
    if reply is None:
        return None
    return getattr(reply, "message_id", None)


def is_command_text(text: str) -> bool:
    return text.lstrip().startswith("/")


def parse_key_values(raw: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not raw:
        return out
    for token in raw.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        out[key.strip()] = value.strip()
    return out
