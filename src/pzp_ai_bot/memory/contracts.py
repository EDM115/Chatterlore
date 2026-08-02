from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pzp_ai_bot.ai.factory import ProviderSelection
from pzp_ai_bot.ai.protocols import ChatResult
from pzp_ai_bot.db.models import KnowledgeItem
from pzp_ai_bot.db.repos.shared import EffectiveScopeSettings
from pzp_ai_bot.memory.canonical import CanonicalMemoryEntry
from pzp_ai_bot.memory.scope import Scope


@dataclass
class IncomingMessage:
    telegram_message_id: int
    scope: Scope
    user_id: int | None
    username: str | None
    text: str
    created_at: datetime
    reply_to_message_id: int | None = None
    is_bot: bool = False
    is_command: bool = False
    is_chat_admin: bool = False
    chat_title: str | None = None
    chat_username: str | None = None
    topic_title: str | None = None
    message_permalink: str | None = None


@dataclass
class IngestResult:
    learned: bool
    reason: str
    message_db_id: int | None = None
    summarized: bool = False


@dataclass
class MemoryReviewPreview:
    id: int
    scope: Scope
    kind: str
    content: str
    source_message_ids: list[int]
    created_at: datetime


@dataclass
class MemoryNotePreview:
    id: int
    scope: Scope
    content: str
    tags: list[str]
    priority: int | None
    source: str | None
    source_message_ids: list[int]


@dataclass
class RangeSummaryResult:
    knowledge_item_id: int
    source_message_ids: list[int]
    started_at: datetime
    ended_at: datetime
    template_name: str


@dataclass
class SummaryTemplatePreview:
    name: str
    prompt: str
    builtin: bool


@dataclass
class SystemPromptTemplatePreview:
    name: str
    version: str
    prompt: str
    builtin: bool


@dataclass
class AIRequestOptions:
    provider_profile: str | None = None
    retrieval_provider_profile: str | None = None
    chat_provider_profile: str | None = None
    chat_model: str | None = None
    embedding_model: str | None = None
    reasoning_level: str | None = None
    system_prompt_template: str | None = None
    system_prompt_override: str | None = None
    summary_template_override: str | None = None


class ProviderOperationError(RuntimeError):
    def __init__(
        self, *, error_type: str, safe_summary: str, original_error: Exception | None = None
    ) -> None:
        super().__init__(safe_summary)
        self.error_type = error_type
        self.safe_summary = safe_summary
        self.original_error = original_error

    @classmethod
    def from_exception(cls, error: Exception) -> ProviderOperationError:
        error_type = type(error).__name__
        return cls(
            error_type=error_type,
            safe_summary=f"{error_type}: provider operation failed",
            original_error=error,
        )


@dataclass
class ProviderCallTelemetry:
    provider: str
    model: str
    operation: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    error_type: str | None
    error_summary: str | None
    metadata: tuple[tuple[str, Any], ...]


@dataclass
class CanonicalScopeInput:
    scope: Scope
    items: tuple[KnowledgeItem, ...]
    scope_settings: EffectiveScopeSettings
    selection: ProviderSelection
    reasoning_level: str
    item_fingerprint: str


@dataclass
class CanonicalScopeRefresh:
    entries: tuple[CanonicalMemoryEntry, ...]
    provider_calls: int
    snapshot_persisted: bool


@dataclass
class ChatProviderExecution:
    result: ChatResult | None
    failure: ProviderOperationError | None
    telemetry: ProviderCallTelemetry


@dataclass
class SummaryGenerationResult:
    content: str | None
    chat_result: ChatResult | None
    failure: ProviderOperationError | None
    telemetry: ProviderCallTelemetry
    selection: ProviderSelection
    reasoning_level: str
    template_version: str

    @property
    def error_type(self) -> str | None:
        return self.failure.error_type if self.failure is not None else None

    @property
    def latency_ms(self) -> int:
        return self.telemetry.latency_ms


@dataclass
class PendingSummaryOutcome:
    completed_messages: int
    needs_compact: bool


@dataclass
class SummaryClaim:
    token: str
    scope: Scope
    message_ids: tuple[int, ...]
    source_telegram_ids: tuple[int, ...]
    normalized_texts: tuple[str, ...]
    scope_settings: EffectiveScopeSettings
    template_name: str
    template_instruction: str
    template_version: str
    recovered_stale_claims: int


@dataclass
class SummaryBatchOutcome:
    claimed_messages: int
    completed_messages: int
    needs_compact: bool


@dataclass
class EmbeddingGenerationResult:
    vectors: list[list[float]]
    dimensions: int | None
    failure: ProviderOperationError | None
    telemetry: ProviderCallTelemetry

    @property
    def error(self) -> Exception | None:
        return self.failure.original_error if self.failure is not None else None

    @property
    def latency_ms(self) -> int:
        return self.telemetry.latency_ms


@dataclass
class AuditEventPreview:
    id: int
    event_type: str
    scope: Scope | None
    user_id: int | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass
class RuntimeSettingPreview:
    key: str
    value: Any
    default_value: Any
    previous_value: Any
    updated_by: int | None
    updated_at: datetime | None
    version: int | None


@dataclass
class MemoryStatusPreview:
    scope: Scope | None
    scope_label: str
    messages: int
    learned: int
    skipped: int
    knowledge_items: int
    embeddings: int
    events: int
    pending_reviews: int
    ai_usage_total: int
    ai_usage_errors: int
    ai_usage_average_latency_ms: int | None
    ai_usage_operations: dict[str, int]


@dataclass
class ScopeStatusPreview:
    scope: Scope
    label: str
    learning_enabled: bool | None
    allow_admins: bool | None
    messages: int
    learned: int
    skipped: int
    knowledge_items: int
    last_message_at: datetime | None


@dataclass
class SkippedMessagePreview:
    scope: Scope
    telegram_message_id: int
    reason: str
    explanation: str
    created_at: datetime


@dataclass
class MemoryItemPreview:
    id: int
    scope: Scope
    kind: str
    content: str
    source_message_ids: list[int]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass
class MemorySourcePreview:
    telegram_message_id: int
    user_id: int | None
    username: str | None
    text: str
    created_at: datetime
    message_permalink: str | None


@dataclass
class MemoryItemRefreshResult:
    id: int
    scope: Scope
    kind: str
    mode: str
    summarized: bool
    embeddings_refreshed: int


@dataclass
class SettingsChangePreview:
    scope: Scope
    updates: dict[str, int | str | bool | None]
    current_values: dict[str, int | str | bool | None]
    token: str | None
    expires_at: datetime | None
    applied: bool


__all__ = [
    "AIRequestOptions",
    "AuditEventPreview",
    "CanonicalScopeInput",
    "CanonicalScopeRefresh",
    "ChatProviderExecution",
    "EmbeddingGenerationResult",
    "IncomingMessage",
    "IngestResult",
    "MemoryItemPreview",
    "MemoryItemRefreshResult",
    "MemoryNotePreview",
    "MemoryReviewPreview",
    "MemorySourcePreview",
    "MemoryStatusPreview",
    "PendingSummaryOutcome",
    "ProviderCallTelemetry",
    "ProviderOperationError",
    "RangeSummaryResult",
    "RuntimeSettingPreview",
    "ScopeStatusPreview",
    "SettingsChangePreview",
    "SkippedMessagePreview",
    "SummaryBatchOutcome",
    "SummaryClaim",
    "SummaryGenerationResult",
    "SummaryTemplatePreview",
    "SystemPromptTemplatePreview",
]
