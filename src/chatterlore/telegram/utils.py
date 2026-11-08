from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from chatterlore.memory.scope import Scope


def scope_from_message(message: Any) -> Scope:
    chat_id = int(message.chat.id)
    is_topic = bool(getattr(message, "is_topic_message", False))
    thread_id = getattr(message, "message_thread_id", None) if is_topic else None
    if thread_id is None and bool(getattr(message.chat, "is_forum", False)):
        # General has no topic flag in Bot API updates, but must not become an aggregate scope
        thread_id = 1
    return Scope(chat_id=chat_id, thread_id=thread_id)


def text_from_message(message: Any) -> str:
    body = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    parts = [body] if body else []
    links = message_links(message, body)
    if links:
        parts.append("Links: " + ", ".join(links[:5]))
    document_name = message_document_name(message)
    if document_name:
        parts.append(f"Attachment: {document_name}")
    return "\n".join(parts).strip()


def message_links(message: Any, body: str) -> list[str]:
    entities = (
        getattr(message, "entities", None) or getattr(message, "caption_entities", None) or []
    )
    out: list[str] = []
    seen: set[str] = set()
    for entity in entities:
        raw_type = getattr(entity, "type", "")
        entity_type = str(getattr(raw_type, "value", raw_type))
        candidate = ""
        if entity_type in {"url", "link"}:
            offset = int(getattr(entity, "offset", 0) or 0)
            length = int(getattr(entity, "length", 0) or 0)
            candidate = body[offset : offset + length]
        elif entity_type == "text_link":
            candidate = str(getattr(entity, "url", "") or "")
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def message_document_name(message: Any) -> str | None:
    document = getattr(message, "document", None)
    if document is None:
        return None
    file_name = (
        str(getattr(document, "file_name", "") or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    )
    return file_name[:160] if file_name else None


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


def message_chat_title(message: Any) -> str | None:
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    return getattr(chat, "title", None)


def message_chat_username(message: Any) -> str | None:
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    return getattr(chat, "username", None)


def message_topic_title(message: Any) -> str | None:
    topic = getattr(message, "forum_topic_created", None)
    if topic is not None:
        return getattr(topic, "name", None)
    return None


def message_permalink(message: Any) -> str | None:
    username = message_chat_username(message)
    message_id = getattr(message, "message_id", None)
    if message_id is None:
        return None
    if username:
        return f"https://t.me/{str(username).removeprefix('@')}/{message_id}"
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    chat_id = str(getattr(chat, "id", ""))
    if not chat_id.startswith("-100"):
        return None
    internal_id = chat_id.removeprefix("-100")
    scope = scope_from_message(message)
    if scope.thread_id is not None:
        return f"https://t.me/c/{internal_id}/{scope.thread_id}/{message_id}"
    return f"https://t.me/c/{internal_id}/{message_id}"


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
