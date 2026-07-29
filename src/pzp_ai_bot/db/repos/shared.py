from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_

from pzp_ai_bot.memory.canonical import CanonicalMemoryEntry
from pzp_ai_bot.memory.scope import Scope


def utc_now() -> datetime:
    return datetime.now(UTC)


def scope_filter(model, scope: Scope):
    if scope.chat_id is None:
        return True
    if scope.thread_id is None:
        return model.chat_id == scope.chat_id
    return and_(model.chat_id == scope.chat_id, model.message_thread_id == scope.thread_id)


def exact_scope_filter(model, scope: Scope):
    if scope.chat_id is None:
        return and_(model.chat_id.is_(None), model.message_thread_id.is_(None))
    if scope.thread_id is None:
        return and_(model.chat_id == scope.chat_id, model.message_thread_id.is_(None))
    return and_(model.chat_id == scope.chat_id, model.message_thread_id == scope.thread_id)


@dataclass(frozen=True)
class RetrievedItem:
    target_type: str
    target_id: int
    content: str
    score: float
    source_message_ids: list[int]
    scope: Scope


@dataclass(frozen=True, slots=True)
class EffectiveScopeSettings:
    retrieval_top_k: int
    summary_batch_size: int
    summary_model: str
    system_prompt_template: str
    provider_profile: str
    chat_model: str
    embedding_model: str
    reasoning_level: str
    raw_message_retention_days: int | None
    knowledge_retention_days: int | None
    review_before_compact: bool

    def as_dict(self) -> dict[str, int | str | bool | None]:
        return {
            "retrieval_top_k": self.retrieval_top_k,
            "summary_batch_size": self.summary_batch_size,
            "summary_model": self.summary_model,
            "system_prompt_template": self.system_prompt_template,
            "provider_profile": self.provider_profile,
            "chat_model": self.chat_model,
            "embedding_model": self.embedding_model,
            "reasoning_level": self.reasoning_level,
            "raw_message_retention_days": self.raw_message_retention_days,
            "knowledge_retention_days": self.knowledge_retention_days,
            "review_before_compact": self.review_before_compact,
        }


@dataclass(frozen=True, slots=True)
class SourceReference:
    message_id: int
    label: str
    url: str | None


@dataclass(frozen=True, slots=True)
class CanonicalScopeSnapshotRecord:
    scope: Scope
    item_fingerprint: str
    entries: tuple[CanonicalMemoryEntry, ...]
    provider: str
    provider_profile: str
    model: str
    reasoning_level: str
    prompt_version: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MessageScopeCounts:
    scope: Scope
    messages: int
    learned: int
    skipped: int
    last_message_at: datetime | None


@dataclass(frozen=True, slots=True)
class KnowledgeScopeCounts:
    scope: Scope
    knowledge_items: int


@dataclass(frozen=True, slots=True)
class AIUsageStats:
    total: int
    errors: int
    average_latency_ms: int | None
    operations: dict[str, int]


@dataclass(frozen=True, slots=True)
class MemoryProfileView:
    name: str
    description: str | None
    source_scopes: list[Scope]


def _setting_payload_value(payload: dict[str, Any] | None) -> Any:
    if payload is None:
        return None
    return payload.get("value")


def build_telegram_message_url(
    scope: Scope, telegram_message_id: int, chat_username: str | None = None
) -> str | None:
    if scope.chat_id is None:
        return None
    if chat_username:
        username = chat_username.removeprefix("@")
        return f"https://t.me/{username}/{telegram_message_id}"
    chat_id = str(scope.chat_id)
    if chat_id.startswith("-100"):
        internal_id = chat_id.removeprefix("-100")
        if scope.thread_id is not None:
            return f"https://t.me/c/{internal_id}/{scope.thread_id}/{telegram_message_id}"
        return f"https://t.me/c/{internal_id}/{telegram_message_id}"
    return None


def _settings_resolution_order(scope: Scope) -> list[Scope]:
    scopes = [Scope.global_scope()]
    if scope.chat_id is not None:
        scopes.append(Scope(scope.chat_id, None))
        if scope.thread_id is not None:
            scopes.append(scope)
    return scopes


def _required_int(value: int | str | bool | None) -> int:
    if value is None:
        raise ValueError("required integer setting is missing")
    return int(value)


def _required_str(value: int | str | bool | None) -> str:
    if value is None:
        raise ValueError("required string setting is missing")
    return str(value)


def _optional_int(value: int | str | bool | None) -> int | None:
    if value is None:
        return None
    return int(value)


def _nullable_eq(column, value):
    if value is None:
        return column.is_(None)
    return column == value


def normalize_profile_name(name: str) -> str:
    clean = name.strip().lower()
    if not clean or len(clean) > 64:
        raise ValueError("name must be 1-64 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    if any(char not in allowed for char in clean):
        raise ValueError("name may contain only letters, numbers, underscore and dash")
    return clean


def dedupe_scopes(scopes: list[Scope]) -> list[Scope]:
    seen: set[str] = set()
    out: list[Scope] = []
    for scope in scopes:
        if scope.key in seen:
            continue
        seen.add(scope.key)
        out.append(scope)
    return out
