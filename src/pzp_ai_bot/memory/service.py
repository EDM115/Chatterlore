from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.factory import ProviderRegistry, ProviderSelection
from pzp_ai_bot.ai.protocols import ChatMessage, ChatProvider, ChatResult, EmbeddingProvider
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import KnowledgeItem
from pzp_ai_bot.db.repositories import (
    AIUsageStats,
    EffectiveScopeSettings,
    EmbeddingRepository,
    EventRepository,
    ForgetChallengeRepository,
    KnowledgeRepository,
    MemoryProfileRepository,
    MemoryProfileView,
    MemoryReviewRepository,
    MessageRepository,
    PolicyRepository,
    RuntimeSettingRepository,
    RuntimeSettingView,
    ScopeMetadataRepository,
    ScopeSettingsRepository,
    SettingsChallengeRepository,
    SummaryTemplateRepository,
    SystemPromptTemplateRepository,
    utc_now,
)
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.canonical import (
    CanonicalMemoryEntry,
    canonical_prompt,
    fallback_entries,
    parse_canonical_response,
)
from pzp_ai_bot.memory.markdown import MarkdownMemoryStore, format_sources
from pzp_ai_bot.memory.policy import PolicyService
from pzp_ai_bot.memory.prompts import (
    ANSWER_INSUFFICIENT_CONTEXT,
    BUILTIN_SYSTEM_PROMPTS,
    PROMPT_VERSION,
    SUMMARY_BASE_PROMPT,
    PromptTemplate,
    prompt_metadata,
)
from pzp_ai_bot.memory.retrieval import Retriever, SearchResult
from pzp_ai_bot.memory.scope import Scope, parse_scope


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class IngestResult:
    learned: bool
    reason: str
    message_db_id: int | None = None
    summarized: bool = False


MemoryExportFormat = Literal["md", "jsonl"]


@dataclass(frozen=True, slots=True)
class JsonlImportItem:
    kind: str
    content: str
    source_message_ids: list[int]


@dataclass(frozen=True, slots=True)
class TelegramDesktopMessage:
    telegram_message_id: int
    user_id: int | None
    username: str | None
    text: str
    created_at: datetime
    reply_to_message_id: int | None
    chat_title: str | None


@dataclass(frozen=True, slots=True)
class MemoryReviewPreview:
    id: int
    scope: Scope
    kind: str
    content: str
    source_message_ids: list[int]


@dataclass(frozen=True, slots=True)
class MemoryNotePreview:
    id: int
    scope: Scope
    content: str
    tags: list[str]
    priority: int | None
    source: str | None
    source_message_ids: list[int]


@dataclass(frozen=True, slots=True)
class RangeSummaryResult:
    knowledge_item_id: int
    source_message_ids: list[int]
    started_at: datetime
    ended_at: datetime
    template_name: str


@dataclass(frozen=True, slots=True)
class SummaryTemplatePreview:
    name: str
    prompt: str
    builtin: bool


@dataclass(frozen=True, slots=True)
class SystemPromptTemplatePreview:
    name: str
    version: str
    prompt: str
    builtin: bool


@dataclass(frozen=True, slots=True)
class AIRequestOptions:
    provider_profile: str | None = None
    chat_model: str | None = None
    embedding_model: str | None = None
    reasoning_level: str | None = None
    system_prompt_template: str | None = None
    system_prompt_override: str | None = None
    summary_template_override: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEventPreview:
    id: int
    event_type: str
    scope: Scope | None
    user_id: int | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeSettingPreview:
    key: str
    value: Any
    default_value: Any
    previous_value: Any
    updated_by: int | None
    updated_at: datetime | None
    version: int | None


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class SkippedMessagePreview:
    scope: Scope
    telegram_message_id: int
    reason: str
    explanation: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryItemPreview:
    id: int
    scope: Scope
    kind: str
    content: str
    source_message_ids: list[int]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryItemRefreshResult:
    id: int
    scope: Scope
    kind: str
    mode: str
    summarized: bool
    embeddings_refreshed: int


@dataclass(frozen=True, slots=True)
class SettingsChangePreview:
    scope: Scope
    updates: dict[str, int | str | bool | None]
    current_values: dict[str, int | str | bool | None]
    token: str | None
    expires_at: datetime | None
    applied: bool


@dataclass(frozen=True, slots=True)
class TelegramDesktopImportResult:
    imported: int
    skipped: int


SAFE_RUNTIME_SETTING_TYPES: dict[str, str] = {
    "learning_default_enabled": "bool",
    "retrieval_top_k": "positive_int",
    "summary_batch_size": "positive_int",
    "summary_idle_seconds": "positive_int",
    "summary_model": "str",
    "system_prompt_template": "str",
    "provider_profile": "str",
    "chat_model": "str",
    "embedding_model": "str",
    "reasoning_level": "str",
    "answer_min_score": "non_negative_float",
    "forget_confirm_ttl_seconds": "positive_int",
    "raw_message_retention_days": "optional_positive_int",
    "knowledge_retention_days": "optional_positive_int",
    "review_before_compact": "bool",
}


BUILTIN_SUMMARY_TEMPLATES: dict[str, str] = {
    "general": "Keep stable facts, decisions, preferences, todos, and open questions.",
    "decisions": "Extract only durable decisions, owners, deadlines, and source-backed rationale.",
    "todos": "Extract action items, owners, due dates when present, blockers, and open follow-ups.",
    "meeting": (
        "Write concise meeting notes with decisions, todos, risks, and unresolved questions."
    ),
    "incident": (
        "Write an incident recap with impact, timeline, decisions, remediation todos, "
        "and open questions."
    ),
}


class MemoryService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker,
        chat_provider: ChatProvider,
        embedding_provider: EmbeddingProvider,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.chat_provider = chat_provider
        self.embedding_provider = embedding_provider
        self.provider_registry = provider_registry or ProviderRegistry(settings)
        self.markdown = MarkdownMemoryStore(settings.memory_dir)
        self._runtime_overrides: dict[str, Any] = {}

    async def set_learning(
        self, scope: Scope | None, enabled: bool, updated_by: int | None = None
    ) -> None:
        async with session_scope(self.session_factory) as session:
            policy = PolicyService(
                self.settings,
                PolicyRepository(session),
                learning_default_enabled=self.runtime_setting("learning_default_enabled"),
            )
            await policy.set_learning(scope, enabled, updated_by)
            await EventRepository(session).add(
                event_type="learning_policy",
                scope=scope,
                user_id=updated_by,
                payload={"enabled": enabled},
            )

    async def set_user_policy(
        self, scope: Scope | None, user_id: int, allowed: bool, updated_by: int | None = None
    ) -> None:
        async with session_scope(self.session_factory) as session:
            policy = PolicyService(
                self.settings,
                PolicyRepository(session),
                learning_default_enabled=self.runtime_setting("learning_default_enabled"),
            )
            await policy.set_user(scope, user_id, allowed, updated_by)

    async def set_allow_admins(
        self, scope: Scope | None, enabled: bool, updated_by: int | None = None
    ) -> None:
        async with session_scope(self.session_factory) as session:
            policy = PolicyService(
                self.settings,
                PolicyRepository(session),
                learning_default_enabled=self.runtime_setting("learning_default_enabled"),
            )
            await policy.set_allow_admins(scope, enabled, updated_by)

    async def learn_status(self, scope: Scope) -> dict[str, bool | int | str | None]:
        async with session_scope(self.session_factory) as session:
            policy = PolicyService(
                self.settings,
                PolicyRepository(session),
                learning_default_enabled=self.runtime_setting("learning_default_enabled"),
            )
            status: dict[str, bool | int | str | None] = {}
            status.update(await policy.status(scope))
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            status.update(scope_settings.as_dict())
            return status

    async def set_scope_metadata(
        self,
        scope: Scope,
        *,
        chat_title: str | None = None,
        topic_title: str | None = None,
        chat_username: str | None = None,
    ) -> None:
        async with session_scope(self.session_factory) as session:
            await ScopeMetadataRepository(session).upsert(
                scope, chat_title=chat_title, topic_title=topic_title, chat_username=chat_username
            )

    async def get_scope_settings(self, scope: Scope) -> EffectiveScopeSettings:
        async with session_scope(self.session_factory) as session:
            return await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )

    async def set_scope_settings(
        self,
        scope: Scope,
        updates: dict[str, int | str | bool | None],
        *,
        updated_by: int | None = None,
    ) -> EffectiveScopeSettings:
        validate_scope_settings(updates, self.settings)
        async with session_scope(self.session_factory) as session:
            repo = ScopeSettingsRepository(session)
            await repo.set_settings(scope, updates, updated_by)
            await EventRepository(session).add(
                event_type="scope_settings", scope=scope, user_id=updated_by, payload=dict(updates)
            )
            return await repo.effective(scope, self._default_scope_settings())

    def settings_token_hash(self, scope: Scope, user_id: int, token: str) -> str:
        payload = f"settings:{scope.key}:{user_id}:{token}".encode()
        return hashlib.sha256(payload).hexdigest()

    async def preview_scope_settings(
        self, scope: Scope, updates: dict[str, int | str | bool | None], *, user_id: int
    ) -> SettingsChangePreview:
        validate_scope_settings(updates, self.settings)
        token = token_urlsafe(8)
        expires_at = utc_now() + timedelta(
            seconds=int(self.runtime_setting("forget_confirm_ttl_seconds"))
        )
        async with session_scope(self.session_factory) as session:
            current = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            await SettingsChallengeRepository(session).create(
                scope=scope,
                user_id=user_id,
                token_hash=self.settings_token_hash(scope, user_id, token),
                updates=dict(updates),
                expires_at=expires_at,
            )
            await EventRepository(session).add(
                event_type="scope_settings_preview",
                scope=scope,
                user_id=user_id,
                payload={"updates": dict(updates), "expires_at": expires_at.isoformat()},
            )
            current_values = {key: current.as_dict().get(key) for key in updates}
        return SettingsChangePreview(
            scope=scope,
            updates=dict(updates),
            current_values=current_values,
            token=token,
            expires_at=expires_at,
            applied=False,
        )

    async def confirm_scope_settings(
        self, scope: Scope, *, user_id: int, confirm: str
    ) -> SettingsChangePreview:
        async with session_scope(self.session_factory) as session:
            challenge_repo = SettingsChallengeRepository(session)
            challenge = await challenge_repo.latest_active(scope=scope, user_id=user_id)
            if challenge is None or challenge.token_hash != self.settings_token_hash(
                scope, user_id, confirm
            ):
                raise ValueError(
                    "No matching pending settings confirmation. Re-run /settings with the "
                    "desired key=value changes."
                )
            updates = dict(challenge.updates)
            validate_scope_settings(updates, self.settings)
            settings_repo = ScopeSettingsRepository(session)
            current = await settings_repo.effective(scope, self._default_scope_settings())
            await settings_repo.set_settings(scope, updates, user_id)
            await challenge_repo.consume(challenge.id)
            await EventRepository(session).add(
                event_type="scope_settings_confirmed",
                scope=scope,
                user_id=user_id,
                payload={"updates": updates, "challenge_id": challenge.id},
            )
            current_values = {key: current.as_dict().get(key) for key in updates}
        return SettingsChangePreview(
            scope=scope,
            updates=updates,
            current_values=current_values,
            token=None,
            expires_at=None,
            applied=True,
        )

    async def refresh_runtime_settings(self) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            values = await RuntimeSettingRepository(session).values()
        self._runtime_overrides = validate_runtime_settings_values(values, self.settings)
        return dict(self._runtime_overrides)

    async def set_runtime_settings(
        self, updates: dict[str, Any], *, updated_by: int | None = None
    ) -> list[RuntimeSettingPreview]:
        validated = validate_runtime_settings_values(updates, self.settings)
        async with session_scope(self.session_factory) as session:
            repo = RuntimeSettingRepository(session)
            rows: list[RuntimeSettingView] = []
            for key, value in validated.items():
                rows.append(await repo.set_value(key=key, value=value, updated_by=updated_by))
            await EventRepository(session).add(
                event_type="runtime_settings_update",
                user_id=updated_by,
                payload={"updates": validated},
            )
        await self.refresh_runtime_settings()
        return [self._runtime_setting_preview(row) for row in rows]

    async def list_runtime_settings(self) -> list[RuntimeSettingPreview]:
        async with session_scope(self.session_factory) as session:
            rows = await RuntimeSettingRepository(session).list_settings()
        stored = {row.key: row for row in rows}
        previews: list[RuntimeSettingPreview] = []
        for key in sorted(SAFE_RUNTIME_SETTING_TYPES):
            row = stored.get(key)
            if row is None:
                previews.append(
                    RuntimeSettingPreview(
                        key=key,
                        value=self.runtime_setting(key),
                        default_value=self.runtime_default(key),
                        previous_value=None,
                        updated_by=None,
                        updated_at=None,
                        version=None,
                    )
                )
            else:
                previews.append(self._runtime_setting_preview(row))
        return previews

    async def audit_events(
        self, *, limit: int = 50, event_type: str | None = None, scope: Scope | None = None
    ) -> list[AuditEventPreview]:
        async with session_scope(self.session_factory) as session:
            rows = await EventRepository(session).list_events(
                limit=limit, event_type=event_type, scope=scope
            )
        return [
            AuditEventPreview(
                id=row.id,
                event_type=row.event_type,
                scope=Scope(row.chat_id, row.message_thread_id)
                if row.chat_id is not None
                else None,
                user_id=row.user_id,
                payload=row.payload or {},
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def memory_status(self, scope: Scope | None = None) -> MemoryStatusPreview:
        async with session_scope(self.session_factory) as session:
            message_counts = await MessageRepository(session).counts(scope)
            knowledge = await KnowledgeRepository(session).count(scope)
            embeddings = await EmbeddingRepository(session).count(scope)
            events = await EventRepository(session).count_events(scope)
            pending_reviews = await MemoryReviewRepository(session).count_pending(scope)
            ai_usage: AIUsageStats = await EventRepository(session).ai_usage_stats(scope)
            metadata = (
                await ScopeMetadataRepository(session).get_effective(scope) if scope else None
            )
        return MemoryStatusPreview(
            scope=scope,
            scope_label="all scopes" if scope is None else scope_label(scope, metadata),
            messages=message_counts["messages"],
            learned=message_counts["learned"],
            skipped=message_counts["skipped"],
            knowledge_items=knowledge,
            embeddings=embeddings,
            events=events,
            pending_reviews=pending_reviews,
            ai_usage_total=ai_usage.total,
            ai_usage_errors=ai_usage.errors,
            ai_usage_average_latency_ms=ai_usage.average_latency_ms,
            ai_usage_operations=ai_usage.operations,
        )

    async def known_scopes(self, root_scope: Scope | None = None) -> list[ScopeStatusPreview]:
        async with session_scope(self.session_factory) as session:
            message_counts = {
                row.scope.key: row for row in await MessageRepository(session).scope_counts()
            }
            knowledge_counts = {
                row.scope.key: row for row in await KnowledgeRepository(session).scope_counts()
            }
            metadata_by_scope = await ScopeMetadataRepository(session).list_all()
            policies = PolicyService(
                self.settings,
                PolicyRepository(session),
                learning_default_enabled=self.runtime_setting("learning_default_enabled"),
            )
            scopes_by_key: dict[str, Scope] = {}
            for row in message_counts.values():
                scopes_by_key[row.scope.key] = row.scope
            for row in knowledge_counts.values():
                scopes_by_key[row.scope.key] = row.scope
            for key, metadata in metadata_by_scope.items():
                scopes_by_key[key] = Scope(metadata.chat_id, metadata.message_thread_id)
            for policy in await PolicyRepository(session).list_policies():
                if policy.chat_id is not None:
                    policy_scope = Scope(policy.chat_id, policy.message_thread_id)
                    scopes_by_key[policy_scope.key] = policy_scope
            if root_scope is not None and root_scope.chat_id is not None:
                scopes_by_key = {
                    key: scope
                    for key, scope in scopes_by_key.items()
                    if scope.chat_id == root_scope.chat_id
                    and (root_scope.thread_id is None or scope.thread_id == root_scope.thread_id)
                }
            previews: list[ScopeStatusPreview] = []
            for scope in sorted(
                scopes_by_key.values(), key=lambda item: (item.chat_id or 0, item.thread_id or 0)
            ):
                counts = message_counts.get(scope.key)
                knowledge = knowledge_counts.get(scope.key)
                status = await policies.status(scope)
                learning_enabled = status.get("learning_enabled")
                allow_admins = status.get("allow_admins")
                metadata = metadata_by_scope.get(scope.key) or await ScopeMetadataRepository(
                    session
                ).get_effective(scope)
                previews.append(
                    ScopeStatusPreview(
                        scope=scope,
                        label=scope_label(scope, metadata),
                        learning_enabled=learning_enabled
                        if isinstance(learning_enabled, bool)
                        else None,
                        allow_admins=allow_admins if isinstance(allow_admins, bool) else None,
                        messages=counts.messages if counts else 0,
                        learned=counts.learned if counts else 0,
                        skipped=counts.skipped if counts else 0,
                        knowledge_items=knowledge.knowledge_items if knowledge else 0,
                        last_message_at=counts.last_message_at if counts else None,
                    )
                )
            return previews

    async def skipped_messages(
        self, scope: Scope, *, limit: int = 10, telegram_message_id: int | None = None
    ) -> list[SkippedMessagePreview]:
        async with session_scope(self.session_factory) as session:
            rows = await MessageRepository(session).skipped(
                scope, limit=limit, telegram_message_id=telegram_message_id
            )
            return [
                SkippedMessagePreview(
                    scope=Scope(row.chat_id, row.message_thread_id),
                    telegram_message_id=row.telegram_message_id,
                    reason=row.skip_reason or "unknown",
                    explanation=skip_reason_explanation(row.skip_reason),
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def runtime_default(self, key: str) -> Any:
        defaults = {
            "learning_default_enabled": self.settings.learning_default_enabled,
            "retrieval_top_k": self.settings.retrieval_top_k,
            "summary_batch_size": self.settings.summary_batch_size,
            "summary_idle_seconds": self.settings.summary_idle_seconds,
            "summary_model": self.settings.chat_model,
            "system_prompt_template": self.settings.system_prompt_template,
            "provider_profile": self.settings.ai_provider_profile,
            "chat_model": self.settings.chat_model,
            "embedding_model": self.settings.embedding_model,
            "reasoning_level": self.settings.reasoning_level,
            "answer_min_score": self.settings.answer_min_score,
            "forget_confirm_ttl_seconds": self.settings.forget_confirm_ttl_seconds,
            "raw_message_retention_days": self.settings.raw_message_retention_days,
            "knowledge_retention_days": self.settings.knowledge_retention_days,
            "review_before_compact": self.settings.review_before_compact,
        }
        if key not in defaults:
            raise ValueError(f"unknown runtime setting: {key}")
        return defaults[key]

    def runtime_setting(self, key: str) -> Any:
        return self._runtime_overrides.get(key, self.runtime_default(key))

    def _runtime_setting_preview(self, row: RuntimeSettingView) -> RuntimeSettingPreview:
        return RuntimeSettingPreview(
            key=row.key,
            value=row.value,
            default_value=self.runtime_default(row.key),
            previous_value=row.previous_value,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
            version=row.version,
        )

    async def ingest(self, incoming: IncomingMessage) -> IngestResult:
        text = normalize_text(incoming.text)
        summarized = False
        needs_compact = False
        async with session_scope(self.session_factory) as session:
            if any((incoming.chat_title, incoming.topic_title, incoming.chat_username)):
                await ScopeMetadataRepository(session).upsert(
                    incoming.scope,
                    chat_title=incoming.chat_title,
                    topic_title=incoming.topic_title,
                    chat_username=incoming.chat_username,
                )
            scope_settings = await ScopeSettingsRepository(session).effective(
                incoming.scope, self._default_scope_settings()
            )
            policy = PolicyService(
                self.settings,
                PolicyRepository(session),
                learning_default_enabled=self.runtime_setting("learning_default_enabled"),
            )
            decision = await policy.decide(
                scope=incoming.scope,
                user_id=incoming.user_id,
                is_bot=incoming.is_bot,
                is_command=incoming.is_command,
                text=text,
                is_chat_admin=incoming.is_chat_admin,
            )
            message_repo = MessageRepository(session)
            message, created = await message_repo.add(
                telegram_message_id=incoming.telegram_message_id,
                scope=incoming.scope,
                user_id=incoming.user_id,
                username=incoming.username,
                text=incoming.text,
                normalized_text=text,
                learned=decision.allowed,
                skip_reason=None if decision.allowed else decision.reason,
                reply_to_message_id=incoming.reply_to_message_id,
                created_at=incoming.created_at,
                chat_title=incoming.chat_title,
                chat_username=incoming.chat_username,
                topic_title=incoming.topic_title,
                message_permalink=incoming.message_permalink,
            )
            if not created:
                return IngestResult(message.learned, "duplicate_message", message.id, False)
            if not decision.allowed:
                return IngestResult(False, decision.reason, message.id)
            selection = self._select_provider(scope_settings)
            vector = (
                await self._embed_texts_in_session(
                    session,
                    selection=selection,
                    texts=[text],
                    operation="embed_message",
                    metadata={"scope": incoming.scope.key, "target_type": "message"},
                )
            )[0]
            await EmbeddingRepository(session).add(
                target_type="message",
                target_id=message.id,
                scope=incoming.scope,
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                vector=vector,
            )
            pending = await message_repo.pending_for_summary(incoming.scope)
            if len(pending) >= scope_settings.summary_batch_size:
                needs_compact = await self._summarize_pending_in_session(
                    session, incoming.scope, pending, scope_settings
                )
                summarized = True
            result = IngestResult(True, decision.reason, message.id, summarized)
        if needs_compact:
            await self.compact_memory()
        return result

    async def summarize_now(
        self,
        scope: Scope,
        *,
        template_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> int:
        async with session_scope(self.session_factory) as session:
            if scope.is_global:
                if template_name is not None:
                    raise ValueError("template summaries require a concrete scope")
                count, needs_compact = await self._summarize_pending_scopes_in_session(session)
            else:
                pending = await MessageRepository(session).pending_for_summary(scope)
                if not pending:
                    return 0
                scope_settings = await ScopeSettingsRepository(session).effective(
                    scope, self._default_scope_settings()
                )
                needs_compact = await self._summarize_pending_in_session(
                    session,
                    scope,
                    pending,
                    scope_settings,
                    template_name=template_name,
                    ai_options=ai_options,
                )
                count = len(pending)
        if needs_compact:
            await self.compact_memory()
        return count

    async def summarize_idle(self, *, now: datetime | None = None) -> int:
        cutoff = (now or utc_now()) - timedelta(
            seconds=int(self.runtime_setting("summary_idle_seconds"))
        )
        async with session_scope(self.session_factory) as session:
            count, needs_compact = await self._summarize_pending_scopes_in_session(
                session, idle_before=cutoff
            )
        if needs_compact:
            await self.compact_memory()
        return count

    async def remember(
        self,
        scope: Scope,
        content: str,
        source_message_id: int | None = None,
        *,
        tags: list[str] | None = None,
        priority: int | None = None,
        source: str | None = None,
    ) -> int:
        metadata = note_metadata(tags=tags, priority=priority, source=source)
        async with session_scope(self.session_factory) as session:
            item = await self._add_knowledge_in_session(
                session,
                scope=scope,
                kind="manual_note",
                content=content.strip(),
                source_message_ids=[] if source_message_id is None else [source_message_id],
                metadata_json=metadata,
                daily_kind="manual",
            )
            item_id = item.id
        await self.compact_memory()
        return item_id

    async def list_notes(
        self, scope: Scope | None = None, *, tag: str | None = None
    ) -> list[MemoryNotePreview]:
        async with session_scope(self.session_factory) as session:
            rows = await KnowledgeRepository(session).list_manual_notes(scope, tag)
            return [memory_note_preview(row) for row in rows]

    async def edit_note(
        self,
        scope: Scope,
        item_id: int,
        *,
        content: str | None = None,
        tags: list[str] | None = None,
        priority: int | None = None,
        source: str | None = None,
        clear_priority: bool = False,
        clear_source: bool = False,
    ) -> MemoryNotePreview:
        metadata_updates: dict[str, Any] = {}
        if tags is not None:
            metadata_updates["tags"] = normalize_tags(tags)
        if priority is not None or clear_priority:
            metadata_updates["priority"] = priority
        if source is not None or clear_source:
            metadata_updates["source"] = source
        async with session_scope(self.session_factory) as session:
            repo = KnowledgeRepository(session)
            item = await repo.update_manual_note(
                item_id,
                scope=scope,
                content=content.strip() if content is not None else None,
                metadata_updates=metadata_updates,
            )
            if item is None:
                raise ValueError(f"manual note #{item_id} was not found in scope {scope.key}")
            await EmbeddingRepository(session).delete_targets(
                target_type="knowledge_item", target_ids=[item.id]
            )
            scope_settings = await ScopeSettingsRepository(session).effective(
                Scope(item.chat_id, item.message_thread_id), self._default_scope_settings()
            )
            selection = self._select_provider(scope_settings)
            vector = (
                await self._embed_texts_in_session(
                    session,
                    selection=selection,
                    texts=[item.content],
                    operation="embed_knowledge",
                    metadata={
                        "scope": Scope(item.chat_id, item.message_thread_id).key,
                        "target_type": "knowledge_item",
                    },
                )
            )[0]
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item.id,
                scope=Scope(item.chat_id, item.message_thread_id),
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                vector=vector,
            )
            await EventRepository(session).add(
                event_type="manual_note_updated",
                scope=scope,
                payload={"knowledge_item_id": item.id, "metadata": item.metadata_json or {}},
            )
            preview = memory_note_preview(item)
        await self.compact_memory()
        return preview

    async def delete_note(self, scope: Scope, item_id: int) -> None:
        async with session_scope(self.session_factory) as session:
            deleted = await KnowledgeRepository(session).delete_manual_note(item_id, scope=scope)
            if not deleted:
                raise ValueError(f"manual note #{item_id} was not found in scope {scope.key}")
            await EmbeddingRepository(session).delete_targets(
                target_type="knowledge_item", target_ids=[item_id]
            )
            await EventRepository(session).add(
                event_type="manual_note_deleted",
                scope=scope,
                payload={"knowledge_item_id": item_id},
            )
        await self.compact_memory()

    async def ask(
        self,
        scope: Scope,
        question: str,
        *,
        top_k: int | None = None,
        profile_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> str:
        scope_settings = await self.get_scope_settings(scope)
        if top_k is None:
            top_k = scope_settings.retrieval_top_k
        results = await self.search(
            scope, question, top_k=top_k, profile_name=profile_name, ai_options=ai_options
        )
        best_score = max((result.score for result in results), default=0.0)
        if not results or best_score < float(self.runtime_setting("answer_min_score")):
            return f"{ANSWER_INSUFFICIENT_CONTEXT} Best scoped-memory score: {best_score:.3f}."
        context = format_results(results)
        async with session_scope(self.session_factory) as session:
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            selection = self._select_provider(scope_settings, ai_options)
            reasoning_level = self._reasoning_level(scope_settings, ai_options)
            system_template = await self._answer_system_prompt_in_session(
                session, scope_settings, ai_options
            )
            messages = [
                ChatMessage(
                    role="system",
                    content=(
                        f"{system_template.prompt}\n"
                        f"Prompt version: {system_template.version}.\n"
                        f"Reasoning level setting: {reasoning_level}."
                    ),
                ),
                ChatMessage(
                    role="user", content=f"Question: {question}\n\nScoped memory:\n{context}"
                ),
            ]
            started = time.perf_counter()
            error: str | None = None
            try:
                result = await selection.chat_provider.generate(
                    messages, temperature=0.1, max_tokens=700
                )
                content = result.content
            except Exception as exc:
                error = str(exc)
                content = fallback_answer(results)
                result = None
            await EventRepository(session).add_ai_usage(
                provider=selection.chat_provider.name,
                model=selection.chat_model,
                operation="ask",
                prompt_tokens=result.prompt_tokens if result else None,
                completion_tokens=result.completion_tokens if result else None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=error,
                metadata_json={
                    **prompt_metadata(
                        template_name=system_template.name,
                        template_version=system_template.version,
                        reasoning_level=reasoning_level,
                        provider_profile=selection.profile.name,
                    ),
                    "chat_model": selection.chat_model,
                    "embedding_model": selection.embedding_model,
                    "scope": scope.key,
                    "retrieval_results": len(results),
                    "best_score": best_score,
                    "memory_profile": profile_name,
                },
            )
            return append_answer_sources(content, results)

    async def search(
        self,
        scope: Scope,
        query: str,
        *,
        top_k: int = 6,
        profile_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> list[SearchResult]:
        async with session_scope(self.session_factory) as session:
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            selection = self._select_provider(scope_settings, ai_options)
            query_vector = (
                await self._embed_texts_in_session(
                    session,
                    selection=selection,
                    texts=[query],
                    operation="embed_query",
                    metadata={"scope": scope.key, "memory_profile": profile_name},
                )
            )[0]
            retriever = Retriever(
                embedding_repo=EmbeddingRepository(session),
                knowledge_repo=KnowledgeRepository(session),
                message_repo=MessageRepository(session),
            )
            scopes = await self._retrieval_scopes_in_session(session, scope, profile_name)
            results: list[SearchResult] = []
            for source_scope in scopes:
                results.extend(await retriever.search(query_vector, source_scope, top_k))
            results.sort(key=search_result_rank)
            return results[:top_k]

    async def compact_memory(self) -> Path:
        async with session_scope(self.session_factory) as session:
            knowledge_repo = KnowledgeRepository(session)
            metadata_repo = ScopeMetadataRepository(session)
            message_repo = MessageRepository(session)
            event_repo = EventRepository(session)
            items = await knowledge_repo.list_scope()
            metadata_by_scope = await metadata_repo.list_all()
            grouped_items: dict[Scope, list[KnowledgeItem]] = defaultdict(list)
            for item in items:
                grouped_items[Scope(item.chat_id, item.message_thread_id)].append(item)
            entries_by_scope: dict[Scope, list[CanonicalMemoryEntry]] = {}
            sources_by_scope = {}
            for scope, scope_items in grouped_items.items():
                metadata = await metadata_repo.get_effective(scope)
                if metadata is not None:
                    metadata_by_scope[scope.key] = metadata
                source_ids = sorted(
                    {source for item in scope_items for source in item.source_message_ids}
                )
                sources_by_scope[scope.key] = await message_repo.source_references(
                    scope, source_ids, metadata
                )
                (
                    entries,
                    result,
                    error,
                    latency_ms,
                    selection,
                    reasoning_level,
                ) = await self._canonical_entries_for_scope(session, scope, scope_items)
                entries_by_scope[scope] = entries
                await event_repo.add_ai_usage(
                    provider=selection.chat_provider.name,
                    model=selection.chat_model,
                    operation="canonical_compact",
                    prompt_tokens=result.prompt_tokens if result else None,
                    completion_tokens=result.completion_tokens if result else None,
                    latency_ms=latency_ms,
                    error=error,
                    metadata_json={
                        "prompt_version": PROMPT_VERSION,
                        "provider_profile": selection.profile.name,
                        "chat_model": selection.chat_model,
                        "embedding_model": selection.embedding_model,
                        "reasoning_level": reasoning_level,
                        "scope": scope.key,
                    },
                )
            path = self.markdown.write_canonical(
                entries_by_scope, metadata_by_scope, sources_by_scope
            )
            await event_repo.add(event_type="compact_memory", payload={"path": str(path)})
            return path

    async def export_memory(
        self, scope: Scope | None = None, fmt: MemoryExportFormat = "md"
    ) -> str:
        if fmt not in {"md", "jsonl"}:
            raise ValueError("format must be md or jsonl")
        async with session_scope(self.session_factory) as session:
            items = await KnowledgeRepository(session).list_scope(scope)
            metadata_repo = ScopeMetadataRepository(session)
            message_repo = MessageRepository(session)
            if fmt == "jsonl":
                lines = []
                for item in items:
                    item_scope = Scope(item.chat_id, item.message_thread_id)
                    metadata = await metadata_repo.get_effective(item_scope)
                    references = await message_repo.source_references(
                        item_scope, item.source_message_ids, metadata
                    )
                    lines.append(
                        json.dumps(
                            {
                                "id": item.id,
                                "scope": item_scope.key,
                                "scope_label": scope_label(item_scope, metadata),
                                "kind": item.kind,
                                "content": item.content,
                                "source_message_ids": item.source_message_ids,
                                "metadata": item.metadata_json or {},
                                "source_references": [
                                    {"message_id": ref.message_id, "url": ref.url}
                                    for ref in references.values()
                                ],
                                "created_at": item.created_at.isoformat(),
                            },
                            ensure_ascii=False,
                        )
                    )
                return "\n".join(lines) + ("\n" if lines else "")
            if scope is None:
                return self.markdown.read_main()
            metadata = await metadata_repo.get_effective(scope)
            references = await message_repo.source_references(
                scope, [source for item in items for source in item.source_message_ids], metadata
            )
            lines = [f"# Memory export for {scope_label(scope, metadata)}", ""]
            for item in items:
                sources = format_sources(item.source_message_ids, references)
                extra = format_item_metadata(item.metadata_json or {})
                detail = f"{item.kind}, source: {sources}{extra}"
                lines.append(f"- ({detail}) {item.content.strip()}")
            return "\n".join(lines).rstrip() + "\n"

    async def import_jsonl(self, path: Path, scope: Scope) -> int:
        rows = load_jsonl_import(path)
        count = 0
        async with session_scope(self.session_factory) as session:
            for row in rows:
                await self._add_knowledge_in_session(
                    session,
                    scope=scope,
                    kind=row.kind,
                    content=row.content,
                    source_message_ids=row.source_message_ids,
                    daily_kind="import",
                )
                count += 1
        await self.compact_memory()
        return count

    async def import_telegram_desktop_json(
        self, path: Path, scope: Scope
    ) -> TelegramDesktopImportResult:
        if scope.chat_id is None:
            raise ValueError("Telegram Desktop imports require a concrete chat or topic scope")
        rows = load_telegram_desktop_export(path)
        imported = 0
        skipped = 0
        async with session_scope(self.session_factory) as session:
            message_repo = MessageRepository(session)
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            selection = self._select_provider(scope_settings)
            for row in rows:
                if not row.text.strip():
                    skipped += 1
                    continue
                message, created = await message_repo.add(
                    telegram_message_id=row.telegram_message_id,
                    scope=scope,
                    user_id=row.user_id,
                    username=row.username,
                    text=row.text,
                    normalized_text=normalize_text(row.text),
                    learned=True,
                    skip_reason=None,
                    reply_to_message_id=row.reply_to_message_id,
                    created_at=row.created_at,
                    chat_title=row.chat_title,
                    chat_username=None,
                    topic_title=None,
                    message_permalink=None,
                )
                if not created:
                    skipped += 1
                    continue
                vector = (
                    await self._embed_texts_in_session(
                        session,
                        selection=selection,
                        texts=[message.normalized_text],
                        operation="embed_message",
                        metadata={
                            "scope": scope.key,
                            "target_type": "message",
                            "import": "telegram_json",
                        },
                    )
                )[0]
                await EmbeddingRepository(session).add(
                    target_type="message",
                    target_id=message.id,
                    scope=scope,
                    provider=selection.embedding_provider.name,
                    model=selection.embedding_model,
                    vector=vector,
                )
                imported += 1
            await EventRepository(session).add(
                event_type="telegram_json_import",
                scope=scope,
                payload={"imported": imported, "skipped": skipped, "path": str(path)},
            )
        return TelegramDesktopImportResult(imported=imported, skipped=skipped)

    async def browse_items(
        self, scope: Scope | None = None, *, kind: str | None = None, limit: int = 20
    ) -> list[MemoryItemPreview]:
        async with session_scope(self.session_factory) as session:
            rows = await KnowledgeRepository(session).list_recent(scope, kind=kind, limit=limit)
            return [memory_item_preview(row) for row in rows]

    async def delete_memory_item(
        self, scope: Scope, item_id: int, *, deleted_by: int | None = None
    ) -> MemoryItemPreview:
        async with session_scope(self.session_factory) as session:
            repo = KnowledgeRepository(session)
            item = await repo.get(item_id)
            if item is None:
                raise ValueError(f"memory item #{item_id} was not found")
            item_scope = Scope(item.chat_id, item.message_thread_id)
            if not scope.contains(item_scope):
                raise ValueError(f"memory item #{item_id} was not found in scope {scope.key}")
            preview = memory_item_preview(item)
            await repo.delete_item(item_id, scope=item_scope)
            embeddings = await EmbeddingRepository(session).delete_targets(
                target_type="knowledge_item", target_ids=[item_id]
            )
            await EventRepository(session).add(
                event_type="memory_item_deleted",
                scope=item_scope,
                user_id=deleted_by,
                payload={
                    "knowledge_item_id": item_id,
                    "embeddings": embeddings,
                    "kind": preview.kind,
                },
            )
        await self.compact_memory()
        return preview

    async def refresh_memory_item(
        self, scope: Scope, item_id: int, *, mode: str = "embed", updated_by: int | None = None
    ) -> MemoryItemRefreshResult:
        clean_mode = mode.strip().lower()
        if clean_mode not in {"embed", "summarize", "both"}:
            raise ValueError("mode must be embed, summarize or both")
        summarized = False
        async with session_scope(self.session_factory) as session:
            repo = KnowledgeRepository(session)
            item = await repo.get(item_id)
            if item is None:
                raise ValueError(f"memory item #{item_id} was not found")
            item_scope = Scope(item.chat_id, item.message_thread_id)
            if not scope.contains(item_scope):
                raise ValueError(f"memory item #{item_id} was not found in scope {scope.key}")
            scope_settings = await ScopeSettingsRepository(session).effective(
                item_scope, self._default_scope_settings()
            )
            if clean_mode in {"summarize", "both"}:
                source_ids = list(item.source_message_ids)
                if not source_ids:
                    raise ValueError("memory item has no source messages to summarize")
                messages = await MessageRepository(session).by_telegram_ids(item_scope, source_ids)
                if not messages:
                    raise ValueError("memory item source messages were not found")
                (
                    content,
                    result,
                    error,
                    latency_ms,
                    selection,
                    reasoning_level,
                    template_version,
                ) = await self._summarize_messages_in_session(
                    session,
                    item_scope,
                    messages,
                    scope_settings,
                    template_name=str((item.metadata_json or {}).get("template") or "general"),
                    context_line=f"Refresh memory item #{item.id} from its source messages.",
                )
                await repo.update_item(
                    item.id,
                    scope=item_scope,
                    content=content,
                    metadata_updates={
                        "refreshed_at": utc_now().isoformat(),
                        "refresh_mode": clean_mode,
                    },
                )
                await EventRepository(session).add_ai_usage(
                    provider=selection.chat_provider.name,
                    model=selection.chat_model,
                    operation="memory_item_refresh_summary",
                    prompt_tokens=result.prompt_tokens if result else None,
                    completion_tokens=result.completion_tokens if result else None,
                    latency_ms=latency_ms,
                    error=error,
                    metadata_json={
                        "scope": item_scope.key,
                        "knowledge_item_id": item.id,
                        "summary_template_version": template_version,
                        "provider_profile": selection.profile.name,
                        "chat_model": selection.chat_model,
                        "embedding_model": selection.embedding_model,
                        "reasoning_level": reasoning_level,
                    },
                )
                refreshed = await repo.get(item.id)
                if refreshed is not None:
                    item = refreshed
                summarized = True
            embeddings_deleted = await EmbeddingRepository(session).delete_targets(
                target_type="knowledge_item", target_ids=[item.id]
            )
            selection = self._select_provider(scope_settings)
            vector = (
                await self._embed_texts_in_session(
                    session,
                    selection=selection,
                    texts=[item.content],
                    operation="embed_knowledge",
                    metadata={
                        "scope": item_scope.key,
                        "target_type": "knowledge_item",
                        "kind": item.kind,
                        "refresh": clean_mode,
                    },
                )
            )[0]
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item.id,
                scope=item_scope,
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                vector=vector,
            )
            await EventRepository(session).add(
                event_type="memory_item_refreshed",
                scope=item_scope,
                user_id=updated_by,
                payload={
                    "knowledge_item_id": item.id,
                    "mode": clean_mode,
                    "embeddings_deleted": embeddings_deleted,
                },
            )
            result = MemoryItemRefreshResult(
                id=item.id,
                scope=item_scope,
                kind=item.kind,
                mode=clean_mode,
                summarized=summarized,
                embeddings_refreshed=1,
            )
        await self.compact_memory()
        return result

    async def stats(self) -> dict[str, int]:
        async with session_scope(self.session_factory) as session:
            message_counts = await MessageRepository(session).counts()
            knowledge = await KnowledgeRepository(session).count()
            embeddings = await EmbeddingRepository(session).count()
            events = await EventRepository(session).count_events()
            pending_reviews = await MemoryReviewRepository(session).count_pending()
            return {
                **message_counts,
                "knowledge_items": knowledge,
                "embeddings": embeddings,
                "events": events,
                "pending_reviews": pending_reviews,
            }

    async def prune_retention(self, scope: Scope, *, now: datetime | None = None) -> dict[str, int]:
        current = now or utc_now()
        needs_compact = False
        async with session_scope(self.session_factory) as session:
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            message_repo = MessageRepository(session)
            knowledge_repo = KnowledgeRepository(session)
            embedding_repo = EmbeddingRepository(session)
            raw_ids: list[int] = []
            knowledge_ids: list[int] = []
            if scope_settings.raw_message_retention_days is not None:
                raw_cutoff = current - timedelta(days=scope_settings.raw_message_retention_days)
                raw_ids = await message_repo.ids_older_than(scope, raw_cutoff)
            if scope_settings.knowledge_retention_days is not None:
                knowledge_cutoff = current - timedelta(days=scope_settings.knowledge_retention_days)
                knowledge_ids = await knowledge_repo.ids_older_than(scope, knowledge_cutoff)
            message_embeddings = await embedding_repo.delete_targets(
                target_type="message", target_ids=raw_ids
            )
            raw_messages = await message_repo.delete_ids(raw_ids)
            knowledge_embeddings = await embedding_repo.delete_targets(
                target_type="knowledge_item", target_ids=knowledge_ids
            )
            knowledge_items = await knowledge_repo.delete_ids(knowledge_ids)
            needs_compact = knowledge_items > 0
            result = {
                "raw_messages": raw_messages,
                "knowledge_items": knowledge_items,
                "embeddings": message_embeddings + knowledge_embeddings,
            }
            await EventRepository(session).add(
                event_type="retention_prune", scope=scope, payload=result
            )
        if needs_compact:
            await self.compact_memory()
        return result

    async def set_memory_profile(
        self,
        name: str,
        source_scopes: list[Scope],
        *,
        description: str | None = None,
        updated_by: int | None = None,
    ) -> MemoryProfileView:
        if not source_scopes:
            raise ValueError("profile must include at least one source scope")
        async with session_scope(self.session_factory) as session:
            profile = await MemoryProfileRepository(session).upsert_profile(
                name=name,
                source_scopes=source_scopes,
                description=description,
                updated_by=updated_by,
            )
            await EventRepository(session).add(
                event_type="memory_profile_set",
                user_id=updated_by,
                payload={
                    "profile": profile.name,
                    "source_scopes": [scope.key for scope in profile.source_scopes],
                },
            )
            return profile

    async def list_memory_profiles(self) -> list[MemoryProfileView]:
        async with session_scope(self.session_factory) as session:
            return await MemoryProfileRepository(session).list_profiles()

    async def allow_memory_profile(
        self,
        *,
        destination: Scope,
        profile_name: str,
        can_query: bool,
        can_learn: bool = False,
        updated_by: int | None = None,
    ) -> MemoryProfileView:
        async with session_scope(self.session_factory) as session:
            repo = MemoryProfileRepository(session)
            profile = await repo.get_profile(profile_name)
            if profile is None:
                raise ValueError(f"profile {profile_name!r} was not found")
            for source in profile.source_scopes:
                await repo.set_permission(
                    destination=destination,
                    source=source,
                    can_query=can_query,
                    can_learn=can_learn,
                    updated_by=updated_by,
                )
            await EventRepository(session).add(
                event_type="memory_profile_permission",
                scope=destination,
                user_id=updated_by,
                payload={"profile": profile.name, "can_query": can_query, "can_learn": can_learn},
            )
            return profile

    async def set_summary_template(
        self, name: str, prompt: str, *, updated_by: int | None = None
    ) -> SummaryTemplatePreview:
        async with session_scope(self.session_factory) as session:
            row = await SummaryTemplateRepository(session).upsert(
                name=name, prompt=prompt, updated_by=updated_by
            )
            await EventRepository(session).add(
                event_type="summary_template_set",
                user_id=updated_by,
                payload={"template": row.name},
            )
            return SummaryTemplatePreview(row.name, row.prompt, False)

    async def list_summary_templates(self) -> list[SummaryTemplatePreview]:
        previews = [
            SummaryTemplatePreview(name, prompt, True)
            for name, prompt in sorted(BUILTIN_SUMMARY_TEMPLATES.items())
        ]
        async with session_scope(self.session_factory) as session:
            for row in await SummaryTemplateRepository(session).list_templates():
                previews.append(SummaryTemplatePreview(row.name, row.prompt, False))
        return previews

    async def set_system_prompt_template(
        self, name: str, prompt: str, *, updated_by: int | None = None
    ) -> SystemPromptTemplatePreview:
        async with session_scope(self.session_factory) as session:
            row = await SystemPromptTemplateRepository(session).upsert(
                name=name, prompt=prompt, version=PROMPT_VERSION, updated_by=updated_by
            )
            await EventRepository(session).add(
                event_type="system_prompt_template_set",
                user_id=updated_by,
                payload={"template": row.name, "version": row.version},
            )
            return SystemPromptTemplatePreview(row.name, row.version, row.prompt, False)

    async def list_system_prompt_templates(self) -> list[SystemPromptTemplatePreview]:
        previews = [
            SystemPromptTemplatePreview(template.name, template.version, template.prompt, True)
            for template in sorted(BUILTIN_SYSTEM_PROMPTS.values(), key=lambda item: item.name)
        ]
        async with session_scope(self.session_factory) as session:
            for row in await SystemPromptTemplateRepository(session).list_templates():
                previews.append(
                    SystemPromptTemplatePreview(row.name, row.version, row.prompt, False)
                )
        return previews

    async def summarize_range(
        self,
        scope: Scope,
        *,
        start_message_id: int,
        end_message_id: int,
        template_name: str = "general",
        ai_options: AIRequestOptions | None = None,
    ) -> RangeSummaryResult:
        if scope.chat_id is None:
            raise ValueError("range summaries require a concrete scope")
        async with session_scope(self.session_factory) as session:
            message_repo = MessageRepository(session)
            start = await message_repo.get_by_telegram_id(scope, start_message_id)
            end = await message_repo.get_by_telegram_id(scope, end_message_id)
            if start is None or end is None:
                raise ValueError("both range boundary messages must exist in the same scope")
            if start.created_at > end.created_at:
                raise ValueError("range start message must not be newer than range end message")
            pending = await message_repo.learned_between_dates(
                scope, start.created_at, end.created_at
            )
            if not pending:
                raise ValueError("range contains no learned messages")
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            (
                content,
                result,
                error,
                latency_ms,
                selection,
                reasoning_level,
                template_version,
            ) = await self._summarize_messages_in_session(
                session,
                scope,
                pending,
                scope_settings,
                template_name=template_name,
                context_line=(
                    f"Range: Telegram messages {start_message_id}..{end_message_id}, "
                    f"dates {start.created_at.isoformat()}..{end.created_at.isoformat()}."
                ),
                ai_options=ai_options,
            )
            source_ids = [message.telegram_message_id for message in pending]
            item = await self._add_knowledge_in_session(
                session,
                scope=scope,
                kind="range_summary",
                content=content,
                source_message_ids=source_ids,
                metadata_json={
                    "template": template_name,
                    "start_message_id": start_message_id,
                    "end_message_id": end_message_id,
                    "started_at": start.created_at.isoformat(),
                    "ended_at": end.created_at.isoformat(),
                },
                daily_kind="range_summary",
            )
            await MessageRepository(session).mark_summarized([message.id for message in pending])
            await EventRepository(session).add_ai_usage(
                provider=selection.chat_provider.name,
                model=selection.chat_model,
                operation="range_summary",
                prompt_tokens=result.prompt_tokens if result else None,
                completion_tokens=result.completion_tokens if result else None,
                latency_ms=latency_ms,
                error=error,
                metadata_json={
                    "prompt_version": PROMPT_VERSION,
                    "summary_template": template_name,
                    "summary_template_version": template_version,
                    "provider_profile": selection.profile.name,
                    "chat_model": selection.chat_model,
                    "embedding_model": selection.embedding_model,
                    "reasoning_level": reasoning_level,
                    "scope": scope.key,
                },
            )
            await EventRepository(session).add(
                event_type="range_summary",
                scope=scope,
                payload={
                    "knowledge_item_id": item.id,
                    "template": template_name,
                    "source_message_ids": source_ids,
                },
            )
            summary = RangeSummaryResult(
                item.id, source_ids, start.created_at, end.created_at, template_name
            )
        await self.compact_memory()
        return summary

    async def pending_reviews(self, scope: Scope | None = None) -> list[MemoryReviewPreview]:
        async with session_scope(self.session_factory) as session:
            items = await MemoryReviewRepository(session).pending(scope)
            return [
                MemoryReviewPreview(
                    id=item.id,
                    scope=Scope(item.chat_id, item.message_thread_id),
                    kind=item.kind,
                    content=item.content,
                    source_message_ids=list(item.source_message_ids),
                )
                for item in items
            ]

    async def approve_review(self, review_id: int, *, reviewed_by: int | None = None) -> int:
        async with session_scope(self.session_factory) as session:
            repo = MemoryReviewRepository(session)
            review = await repo.get_pending(review_id)
            if review is None:
                raise ValueError(f"pending review #{review_id} was not found")
            scope = Scope(review.chat_id, review.message_thread_id)
            item = await self._add_knowledge_in_session(
                session,
                scope=scope,
                kind=review.kind,
                content=review.content,
                source_message_ids=list(review.source_message_ids),
                daily_kind="approved_review",
            )
            await repo.set_status(review, status="approved", reviewed_by=reviewed_by)
            await EventRepository(session).add(
                event_type="memory_review_approved",
                scope=scope,
                user_id=reviewed_by,
                payload={"review_id": review_id, "knowledge_item_id": item.id},
            )
            item_id = item.id
        await self.compact_memory()
        return item_id

    async def reject_review(self, review_id: int, *, reviewed_by: int | None = None) -> None:
        async with session_scope(self.session_factory) as session:
            repo = MemoryReviewRepository(session)
            review = await repo.get_pending(review_id)
            if review is None:
                raise ValueError(f"pending review #{review_id} was not found")
            scope = Scope(review.chat_id, review.message_thread_id)
            await repo.set_status(review, status="rejected", reviewed_by=reviewed_by)
            await EventRepository(session).add(
                event_type="memory_review_rejected",
                scope=scope,
                user_id=reviewed_by,
                payload={"review_id": review_id},
            )

    def forget_token_hash(self, scope: Scope, user_id: int, token: str) -> str:
        payload = f"{scope.key}:{user_id}:{token}".encode()
        return hashlib.sha256(payload).hexdigest()

    async def forget(self, scope: Scope, user_id: int, confirm: str | None) -> tuple[bool, str]:
        async with session_scope(self.session_factory) as session:
            challenge_repo = ForgetChallengeRepository(session)
            challenge = await challenge_repo.latest_active(scope=scope, user_id=user_id)
            if (
                challenge is None
                or not confirm
                or challenge.token_hash != self.forget_token_hash(scope, user_id, confirm)
            ):
                token = token_urlsafe(8)
                expires_at = utc_now() + timedelta(
                    seconds=int(self.runtime_setting("forget_confirm_ttl_seconds"))
                )
                await challenge_repo.create(
                    scope=scope,
                    user_id=user_id,
                    token_hash=self.forget_token_hash(scope, user_id, token),
                    expires_at=expires_at,
                )
                await EventRepository(session).add(
                    event_type="forget_confirmation_created",
                    scope=scope,
                    user_id=user_id,
                    payload={"expires_at": expires_at.isoformat()},
                )
                message = (
                    "Confirmation required. "
                    f"Re-run with confirm={token} before {expires_at.isoformat()}"
                )
                return (False, message)
            await challenge_repo.consume(challenge.id)
            messages = await MessageRepository(session).delete_scope(scope)
            items = await KnowledgeRepository(session).delete_scope(scope)
            embeddings = await EmbeddingRepository(session).delete_scope(scope)
            await EventRepository(session).add(
                event_type="forget",
                scope=scope,
                user_id=user_id,
                payload={"messages": messages, "knowledge_items": items, "embeddings": embeddings},
            )
        await self.compact_memory()
        detail = f"{messages} messages, {items} knowledge items, {embeddings} embeddings."
        return (True, f"Forgot scope {scope.key}: {detail}")

    async def _summarize_pending_scopes_in_session(
        self, session: AsyncSession, *, idle_before: datetime | None = None
    ) -> tuple[int, bool]:
        message_repo = MessageRepository(session)
        settings_repo = ScopeSettingsRepository(session)
        count = 0
        needs_compact = False
        for scope in await message_repo.pending_scopes(idle_before=idle_before):
            pending = await message_repo.pending_for_summary(scope)
            if not pending:
                continue
            scope_settings = await settings_repo.effective(scope, self._default_scope_settings())
            created_knowledge = await self._summarize_pending_in_session(
                session, scope, pending, scope_settings
            )
            needs_compact = needs_compact or created_knowledge
            count += len(pending)
        return count, needs_compact

    async def _summarize_pending_in_session(
        self,
        session: AsyncSession,
        scope: Scope,
        pending,
        scope_settings: EffectiveScopeSettings,
        *,
        template_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> bool:
        (
            content,
            result,
            error,
            latency_ms,
            selection,
            reasoning_level,
            template_version,
        ) = await self._summarize_messages_in_session(
            session,
            scope,
            pending,
            scope_settings,
            template_name=template_name or "general",
            context_line="Scope summary from pending learned messages.",
            ai_options=ai_options,
        )
        source_ids = [message.telegram_message_id for message in pending]
        await MessageRepository(session).mark_summarized([message.id for message in pending])
        await EventRepository(session).add_ai_usage(
            provider=selection.chat_provider.name,
            model=selection.chat_model,
            operation="summarize",
            prompt_tokens=result.prompt_tokens if result else None,
            completion_tokens=result.completion_tokens if result else None,
            latency_ms=latency_ms,
            error=error,
            metadata_json={
                "prompt_version": PROMPT_VERSION,
                "summary_template": template_name or "general",
                "summary_template_version": template_version,
                "provider_profile": selection.profile.name,
                "chat_model": selection.chat_model,
                "embedding_model": selection.embedding_model,
                "reasoning_level": reasoning_level,
                "scope": scope.key,
            },
        )
        if scope_settings.review_before_compact:
            review = await MemoryReviewRepository(session).add(
                scope=scope, kind="daily_summary", content=content, source_message_ids=source_ids
            )
            self.markdown.append_daily(
                scope=scope, content=content, source_message_ids=source_ids, kind="review_proposal"
            )
            await EventRepository(session).add(
                event_type="memory_review_created",
                scope=scope,
                payload={"review_id": review.id, "source_message_ids": source_ids},
            )
            return False
        await self._add_knowledge_in_session(
            session,
            scope=scope,
            kind="daily_summary",
            content=content,
            source_message_ids=source_ids,
            metadata_json={"template": template_name or "general"},
            daily_kind="daily_summary",
        )
        return True

    async def _summarize_messages_in_session(
        self,
        session: AsyncSession,
        scope: Scope,
        pending,
        scope_settings: EffectiveScopeSettings,
        *,
        template_name: str,
        context_line: str,
        ai_options: AIRequestOptions | None = None,
    ) -> tuple[str, ChatResult | None, str | None, int, ProviderSelection, str, str]:
        request = ai_options or AIRequestOptions()
        template_instruction = (
            request.summary_template_override
            or await self._summary_template_prompt_in_session(session, template_name)
        )
        template_version = (
            PROMPT_VERSION if request.summary_template_override else SUMMARY_BASE_PROMPT.version
        )
        selection = self._select_provider(scope_settings, ai_options, summary=True)
        reasoning_level = self._reasoning_level(scope_settings, ai_options)
        prompt_lines = [
            SUMMARY_BASE_PROMPT.prompt,
            f"Prompt version: {SUMMARY_BASE_PROMPT.version}.",
            template_instruction,
            "Do not dump raw chat. Preserve source references.",
            f"Preferred summary model setting: {scope_settings.summary_model}.",
            f"Selected chat model: {selection.chat_model}.",
            f"Reasoning level setting: {reasoning_level}.",
            f"Template: {template_name}.",
            f"Scope: {scope.key}.",
            context_line,
            "",
        ]
        for message in pending:
            prompt_lines.append(
                f"- {message.normalized_text} [source:{message.telegram_message_id}]"
            )
        started = time.perf_counter()
        error: str | None = None
        try:
            result = await selection.chat_provider.generate(
                [ChatMessage(role="user", content="\n".join(prompt_lines))],
                temperature=0.1,
                max_tokens=900,
            )
            content = result.content.strip()
        except Exception as exc:
            error = str(exc)
            content = "\n".join(
                f"- {message.normalized_text} [source:{message.telegram_message_id}]"
                for message in pending
            )
            result = None
        return (
            content,
            result,
            error,
            int((time.perf_counter() - started) * 1000),
            selection,
            reasoning_level,
            template_version,
        )

    async def _add_knowledge_in_session(
        self,
        session: AsyncSession,
        *,
        scope: Scope,
        kind: str,
        content: str,
        source_message_ids: list[int],
        metadata_json: dict[str, Any] | None = None,
        daily_kind: str,
    ) -> KnowledgeItem:
        item = await KnowledgeRepository(session).add(
            scope=scope,
            kind=kind,
            content=content,
            source_message_ids=source_message_ids,
            metadata_json=metadata_json,
        )
        scope_settings = await ScopeSettingsRepository(session).effective(
            scope, self._default_scope_settings()
        )
        selection = self._select_provider(scope_settings)
        vector = (
            await self._embed_texts_in_session(
                session,
                selection=selection,
                texts=[content],
                operation="embed_knowledge",
                metadata={"scope": scope.key, "target_type": "knowledge_item", "kind": kind},
            )
        )[0]
        await EmbeddingRepository(session).add(
            target_type="knowledge_item",
            target_id=item.id,
            scope=scope,
            provider=selection.embedding_provider.name,
            model=selection.embedding_model,
            vector=vector,
        )
        self.markdown.append_daily(
            scope=scope,
            content=f"- {content}" if "\n" not in content else content,
            source_message_ids=source_message_ids,
            kind=daily_kind,
        )
        return item

    async def _summary_template_prompt_in_session(
        self, session: AsyncSession, template_name: str
    ) -> str:
        clean_name = template_name.strip().lower()
        builtin = BUILTIN_SUMMARY_TEMPLATES.get(clean_name)
        if builtin is not None:
            return builtin
        prompt = await SummaryTemplateRepository(session).get_prompt(clean_name)
        if prompt is None:
            raise ValueError(f"summary template {template_name!r} was not found")
        return prompt

    async def _retrieval_scopes_in_session(
        self, session: AsyncSession, destination: Scope, profile_name: str | None
    ) -> list[Scope]:
        if profile_name is None:
            return [destination]
        profile_repo = MemoryProfileRepository(session)
        profile = await profile_repo.get_profile(profile_name)
        if profile is None:
            raise ValueError(f"profile {profile_name!r} was not found")
        policy = PolicyService(
            self.settings,
            PolicyRepository(session),
            learning_default_enabled=self.runtime_setting("learning_default_enabled"),
        )
        scopes = [destination]
        for source in profile.source_scopes:
            if source == destination:
                continue
            if not await profile_repo.can_query(destination=destination, source=source):
                continue
            if (await policy.status(source)).get("learning_enabled") is not True:
                continue
            scopes.append(source)
        if len(scopes) == 1:
            raise ValueError(
                f"profile {profile.name!r} has no permitted learning-enabled source scopes "
                f"for {destination.key}"
            )
        return dedupe_scope_list(scopes)

    async def _canonical_entries_for_scope(
        self, session: AsyncSession, scope: Scope, items: list[KnowledgeItem]
    ) -> tuple[
        list[CanonicalMemoryEntry], ChatResult | None, str | None, int, ProviderSelection, str
    ]:
        scope_settings = await ScopeSettingsRepository(session).effective(
            scope, self._default_scope_settings()
        )
        selection = self._select_provider(scope_settings, summary=True)
        reasoning_level = self._reasoning_level(scope_settings)
        prompt = canonical_prompt(scope.key, items)
        prompt = (
            f"Reasoning level setting: {reasoning_level}.\n"
            f"Selected chat model: {selection.chat_model}.\n{prompt}"
        )
        started = time.perf_counter()
        error: str | None = None
        try:
            result = await selection.chat_provider.generate(
                [ChatMessage(role="user", content=prompt)], temperature=0.1, max_tokens=1200
            )
            parsed = parse_canonical_response(
                result.content, {int(item.id): item for item in items}
            )
            entries = parsed or fallback_entries(items)
        except Exception as exc:
            error = str(exc)
            result = None
            entries = fallback_entries(items)
        return (
            entries,
            result,
            error,
            int((time.perf_counter() - started) * 1000),
            selection,
            reasoning_level,
        )

    def _select_provider(
        self,
        scope_settings: EffectiveScopeSettings,
        options: AIRequestOptions | None = None,
        *,
        summary: bool = False,
    ) -> ProviderSelection:
        request = options or AIRequestOptions()
        profile_name = request.provider_profile or scope_settings.provider_profile
        chat_model = request.chat_model or (
            scope_settings.summary_model if summary else scope_settings.chat_model
        )
        embedding_model = request.embedding_model or scope_settings.embedding_model
        default_profile = self.settings.provider_profile_by_name(self.settings.ai_provider_profile)
        if (
            profile_name == default_profile.name
            and chat_model == default_profile.chat_model
            and embedding_model == default_profile.embedding_model
        ):
            return ProviderSelection(
                profile=default_profile,
                chat_provider=self.chat_provider,
                embedding_provider=self.embedding_provider,
                chat_model=chat_model,
                embedding_model=embedding_model,
            )
        return self.provider_registry.select(
            profile_name, chat_model=chat_model, embedding_model=embedding_model
        )

    def _reasoning_level(
        self, scope_settings: EffectiveScopeSettings, options: AIRequestOptions | None = None
    ) -> str:
        requested = (options.reasoning_level if options else None) or scope_settings.reasoning_level
        return self.settings.validate_reasoning_level(requested)

    async def _embed_texts_in_session(
        self,
        session: AsyncSession,
        *,
        selection: ProviderSelection,
        texts: list[str],
        operation: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        started = time.perf_counter()
        error: str | None = None
        vectors: list[list[float]] = []
        try:
            vectors = await selection.embedding_provider.embed(texts)
            return vectors
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            dimensions = (
                len(vectors[0])
                if vectors
                else getattr(selection.embedding_provider, "dimensions", None)
            )
            usage_metadata = {
                "count": len(texts),
                "dimensions": dimensions,
                "provider_profile": selection.profile.name,
                "embedding_model": selection.embedding_model,
                "prompt_version": PROMPT_VERSION,
            }
            if metadata:
                usage_metadata.update(metadata)
            await EventRepository(session).add_ai_usage(
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                operation=operation,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=error,
                metadata_json=usage_metadata,
            )

    async def _system_prompt_template_in_session(
        self, session: AsyncSession, name: str
    ) -> PromptTemplate:
        clean_name = name.strip().lower()
        builtin = BUILTIN_SYSTEM_PROMPTS.get(clean_name)
        if builtin is not None:
            return builtin
        row = await SystemPromptTemplateRepository(session).get(clean_name)
        if row is None:
            raise ValueError(f"system prompt template {name!r} was not found")
        return PromptTemplate(name=row.name, version=row.version, prompt=row.prompt)

    async def _answer_system_prompt_in_session(
        self,
        session: AsyncSession,
        scope_settings: EffectiveScopeSettings,
        options: AIRequestOptions | None,
    ) -> PromptTemplate:
        request = options or AIRequestOptions()
        if request.system_prompt_override:
            return PromptTemplate(
                name="one_shot_override",
                version=PROMPT_VERSION,
                prompt=request.system_prompt_override.strip(),
            )
        template_name = request.system_prompt_template or scope_settings.system_prompt_template
        return await self._system_prompt_template_in_session(session, template_name)

    def _default_scope_settings(self) -> EffectiveScopeSettings:
        provider_profile = str(self.runtime_setting("provider_profile"))
        default_profile = self.settings.provider_profile_by_name(provider_profile)
        return EffectiveScopeSettings(
            retrieval_top_k=int(self.runtime_setting("retrieval_top_k")),
            summary_batch_size=int(self.runtime_setting("summary_batch_size")),
            summary_model=str(self.runtime_setting("summary_model")),
            system_prompt_template=str(self.runtime_setting("system_prompt_template")),
            provider_profile=default_profile.name,
            chat_model=str(self.runtime_setting("chat_model")),
            embedding_model=str(self.runtime_setting("embedding_model")),
            reasoning_level=str(self.runtime_setting("reasoning_level")),
            raw_message_retention_days=self.runtime_setting("raw_message_retention_days"),
            knowledge_retention_days=self.runtime_setting("knowledge_retention_days"),
            review_before_compact=bool(self.runtime_setting("review_before_compact")),
        )

    def _embedding_model_name(self) -> str:
        return getattr(self.embedding_provider, "embedding_model", self.embedding_provider.model)


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def validate_scope_settings(
    updates: dict[str, int | str | bool | None], settings: Settings | None = None
) -> None:
    active_settings = settings or Settings()
    positive_int_fields = {"retrieval_top_k", "summary_batch_size"}
    optional_positive_int_fields = {"raw_message_retention_days", "knowledge_retention_days"}
    for field in positive_int_fields:
        value = updates.get(field)
        if field in updates and (not isinstance(value, int) or value < 1):
            raise ValueError(f"{field} must be a positive integer")
    for field in optional_positive_int_fields:
        value = updates.get(field)
        if field in updates and value is not None and (not isinstance(value, int) or value < 1):
            raise ValueError(f"{field} must be a positive integer or off")
    model_fields = {"summary_model", "chat_model", "embedding_model"}
    for field in model_fields:
        if field in updates and updates[field] is not None:
            value = str(updates[field]).strip()
            if not value:
                raise ValueError(f"{field} cannot be empty")
            if field == "chat_model":
                value = active_settings.validate_chat_model(value)
            elif field == "embedding_model":
                value = active_settings.validate_embedding_model(value)
            updates[field] = value
    short_name_fields = {"system_prompt_template", "provider_profile", "reasoning_level"}
    for field in short_name_fields:
        if field in updates and updates[field] is not None:
            value = str(updates[field]).strip().lower()
            if not value:
                raise ValueError(f"{field} cannot be empty")
            if field == "reasoning_level":
                value = active_settings.validate_reasoning_level(value)
            updates[field] = value
    if "review_before_compact" in updates and not isinstance(
        updates["review_before_compact"], bool
    ):
        raise ValueError("review_before_compact must be true or false")


def parse_memory_export_format(raw: str) -> MemoryExportFormat:
    if raw == "md":
        return "md"
    if raw == "jsonl":
        return "jsonl"
    raise ValueError("format must be md or jsonl")


def load_jsonl_import(path: Path) -> list[JsonlImportItem]:
    rows: list[JsonlImportItem] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed JSONL at line {line_number}: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"malformed JSONL at line {line_number}: row must be an object")
            content = str(payload.get("content") or payload.get("text") or "").strip()
            if not content:
                raise ValueError(f"malformed JSONL at line {line_number}: content is required")
            raw_source_ids = payload.get("source_message_ids", [])
            if not isinstance(raw_source_ids, list):
                raise ValueError(
                    f"malformed JSONL at line {line_number}: source_message_ids must be a list"
                )
            try:
                source_ids = [int(value) for value in raw_source_ids]
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"malformed JSONL at line {line_number}: source_message_ids must be integers"
                ) from exc
            rows.append(
                JsonlImportItem(
                    kind=str(payload.get("kind") or "imported_note"),
                    content=content,
                    source_message_ids=source_ids,
                )
            )
    if not rows:
        raise ValueError("import file contains no memory items")
    return rows


def load_telegram_desktop_export(path: Path) -> list[TelegramDesktopMessage]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed Telegram JSON import: {exc.msg}") from exc
    rows = telegram_desktop_export_rows(payload)
    if rows is not None:
        return rows
    rows = telegram_update_dump_rows(payload)
    if rows is not None:
        return rows
    raise ValueError(
        "Telegram JSON import must be a Telegram Desktop export or Bot API update dump"
    )


def telegram_desktop_export_rows(payload: Any) -> list[TelegramDesktopMessage] | None:
    if not isinstance(payload, dict):
        return None
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    chat_title = str(payload.get("name") or "").strip() or None
    rows: list[TelegramDesktopMessage] = []
    for raw in messages:
        if not isinstance(raw, dict) or raw.get("type") != "message":
            continue
        try:
            message_id = int(raw["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Telegram Desktop message is missing a numeric id") from exc
        rows.append(
            TelegramDesktopMessage(
                telegram_message_id=message_id,
                user_id=telegram_desktop_user_id(raw.get("from_id")),
                username=telegram_desktop_username(raw.get("from")),
                text=telegram_desktop_message_text(raw),
                created_at=telegram_desktop_datetime(raw),
                reply_to_message_id=telegram_desktop_optional_int(raw.get("reply_to_message_id")),
                chat_title=chat_title,
            )
        )
    if not rows:
        raise ValueError("Telegram Desktop export contains no importable messages")
    return rows


def telegram_update_dump_rows(payload: Any) -> list[TelegramDesktopMessage] | None:
    messages = telegram_update_message_payloads(payload)
    if messages is None:
        return None
    rows: list[TelegramDesktopMessage] = []
    for raw in messages:
        try:
            message_id = int(raw["message_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Telegram update message is missing a numeric message_id") from exc
        rows.append(
            TelegramDesktopMessage(
                telegram_message_id=message_id,
                user_id=telegram_update_user_id(raw),
                username=telegram_update_username(raw),
                text=telegram_update_message_text(raw),
                created_at=telegram_update_datetime(raw),
                reply_to_message_id=telegram_update_reply_to_message_id(raw),
                chat_title=telegram_update_chat_title(raw),
            )
        )
    if not rows:
        raise ValueError("Telegram update dump contains no importable messages")
    return rows


def telegram_update_message_payloads(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("result"), list):
            candidates = payload["result"]
        elif "message_id" in payload or any(key in payload for key in TELEGRAM_UPDATE_MESSAGE_KEYS):
            candidates = [payload]
        else:
            return None
    elif isinstance(payload, list):
        candidates = payload
    else:
        return None
    messages: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if "message_id" in candidate:
            messages.append(candidate)
            continue
        for key in TELEGRAM_UPDATE_MESSAGE_KEYS:
            raw_message = candidate.get(key)
            if isinstance(raw_message, dict):
                messages.append(raw_message)
                break
    return messages


TELEGRAM_UPDATE_MESSAGE_KEYS = ("message", "edited_message", "channel_post", "edited_channel_post")


def telegram_desktop_message_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    content = telegram_desktop_text_value(row.get("text"))
    if content:
        parts.append(content)
    links = telegram_desktop_links(row)
    if links:
        parts.append("Links: " + ", ".join(links[:5]))
    attachment = telegram_desktop_attachment_label(row)
    if attachment:
        parts.append(attachment)
    return normalize_text("\n".join(parts))


def telegram_desktop_text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                chunks.append(str(item.get("text") or ""))
        return "".join(chunks).strip()
    return ""


def telegram_desktop_links(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    entities = row.get("text_entities")
    if not isinstance(entities, list):
        return out
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        candidate = ""
        if entity.get("type") in {"link", "url"}:
            candidate = str(entity.get("text") or "")
        elif entity.get("type") == "text_link":
            candidate = str(entity.get("href") or entity.get("url") or "")
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def telegram_desktop_attachment_label(row: dict[str, Any]) -> str | None:
    file_name = telegram_desktop_filename(row.get("file") or row.get("photo"))
    media_type = str(row.get("media_type") or "").strip().lower()
    if file_name:
        label = "Photo" if media_type == "photo" or row.get("photo") else "Attachment"
        return f"{label}: {file_name}"
    if media_type == "photo" or row.get("photo"):
        return "Photo attachment"
    return None


def telegram_desktop_filename(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    clean = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not clean or clean.startswith("("):
        return None
    return clean[:160]


def telegram_desktop_datetime(row: dict[str, Any]) -> datetime:
    raw_unix = row.get("date_unixtime")
    if raw_unix is not None:
        try:
            return datetime.fromtimestamp(int(raw_unix), tz=UTC)
        except (TypeError, ValueError):
            pass
    raw_date = str(row.get("date") or "").strip()
    if raw_date:
        try:
            parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return utc_now()


def telegram_desktop_user_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = "".join(char for char in value if char.isdigit())
        return int(digits) if digits else None
    return None


def telegram_desktop_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def telegram_desktop_username(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean[:255] if clean else None


def telegram_update_message_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    content = str(row.get("text") or row.get("caption") or "").strip()
    if content:
        parts.append(content)
    links = telegram_update_links(row, content)
    if links:
        parts.append("Links: " + ", ".join(links[:5]))
    attachment = telegram_update_attachment_label(row)
    if attachment:
        parts.append(attachment)
    return normalize_text("\n".join(parts))


def telegram_update_links(row: dict[str, Any], content: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    entities = row.get("entities") if row.get("text") is not None else row.get("caption_entities")
    if not isinstance(entities, list):
        return out
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        candidate = ""
        entity_type = entity.get("type")
        if entity_type in {"url", "email"}:
            candidate = telegram_update_entity_slice(content, entity)
        elif entity_type == "text_link":
            candidate = str(entity.get("url") or "")
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def telegram_update_entity_slice(content: str, entity: dict[str, Any]) -> str:
    try:
        offset = int(entity.get("offset", 0))
        length = int(entity.get("length", 0))
    except (TypeError, ValueError):
        return ""
    if offset < 0 or length <= 0:
        return ""
    return content[offset : offset + length]


def telegram_update_attachment_label(row: dict[str, Any]) -> str | None:
    document = row.get("document")
    if isinstance(document, dict):
        file_name = telegram_desktop_filename(document.get("file_name"))
        if file_name:
            return f"Attachment: {file_name}"
    if isinstance(row.get("photo"), list) and row["photo"]:
        return "Photo attachment"
    return None


def telegram_update_datetime(row: dict[str, Any]) -> datetime:
    raw_date = row.get("date")
    if raw_date is None:
        return utc_now()
    try:
        return datetime.fromtimestamp(int(raw_date), tz=UTC)
    except (TypeError, ValueError):
        return utc_now()


def telegram_update_user_id(row: dict[str, Any]) -> int | None:
    user = row.get("from")
    if not isinstance(user, dict):
        return None
    return telegram_desktop_optional_int(user.get("id"))


def telegram_update_username(row: dict[str, Any]) -> str | None:
    user = row.get("from")
    if not isinstance(user, dict):
        return None
    username = str(user.get("username") or "").strip()
    if username:
        return username[:255]
    name = " ".join(
        part
        for part in [
            str(user.get("first_name") or "").strip(),
            str(user.get("last_name") or "").strip(),
        ]
        if part
    )
    return name[:255] if name else None


def telegram_update_reply_to_message_id(row: dict[str, Any]) -> int | None:
    reply = row.get("reply_to_message")
    if not isinstance(reply, dict):
        return None
    return telegram_desktop_optional_int(reply.get("message_id"))


def telegram_update_chat_title(row: dict[str, Any]) -> str | None:
    chat = row.get("chat")
    if not isinstance(chat, dict):
        return None
    title = str(chat.get("title") or chat.get("username") or "").strip()
    return title[:255] if title else None


def note_metadata(
    *, tags: list[str] | None = None, priority: int | None = None, source: str | None = None
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    normalized_tags = normalize_tags(tags or [])
    if normalized_tags:
        metadata["tags"] = normalized_tags
    if priority is not None:
        if priority < 1:
            raise ValueError("priority must be a positive integer")
        metadata["priority"] = priority
    if source is not None and source.strip():
        metadata["source"] = source.strip()
    return metadata


def normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = raw.strip().lower().removeprefix("#")
        if not tag:
            continue
        if any(char.isspace() for char in tag):
            raise ValueError("tags cannot contain whitespace")
        if tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def memory_note_preview(item: KnowledgeItem) -> MemoryNotePreview:
    metadata = item.metadata_json or {}
    raw_tags = metadata.get("tags", [])
    tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
    raw_priority = metadata.get("priority")
    priority = int(raw_priority) if raw_priority is not None else None
    raw_source = metadata.get("source")
    return MemoryNotePreview(
        id=item.id,
        scope=Scope(item.chat_id, item.message_thread_id),
        content=item.content,
        tags=tags,
        priority=priority,
        source=str(raw_source) if raw_source else None,
        source_message_ids=list(item.source_message_ids),
    )


def memory_item_preview(item: KnowledgeItem) -> MemoryItemPreview:
    return MemoryItemPreview(
        id=item.id,
        scope=Scope(item.chat_id, item.message_thread_id),
        kind=item.kind,
        content=item.content,
        source_message_ids=list(item.source_message_ids),
        metadata=dict(item.metadata_json or {}),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def skip_reason_explanation(reason: str | None) -> str:
    explanations = {
        "learning_disabled": "Learning is disabled for this chat or topic.",
        "user_denied": "That author is denied for this scope.",
        "bot_message": "Bot-authored messages are ignored.",
        "command": "Telegram commands are not learned as memory.",
        "empty_text": "The update did not contain learnable text or supported metadata.",
        "missing_user": "The update did not include a Telegram user id.",
        "user_not_allowed": "An allowlist exists and that author is not on it.",
    }
    return explanations.get(
        reason or "", f"The message was skipped for reason: {reason or 'unknown'}."
    )


def parse_memory_item_mode(raw: str | None) -> str:
    mode = (raw or "embed").strip().lower()
    if mode not in {"embed", "summarize", "both"}:
        raise ValueError("mode must be embed, summarize or both")
    return mode


def parse_optional_limit(raw: str | None, *, default: int, maximum: int) -> int:
    if raw is None or raw == "":
        return default
    value = int(raw)
    if value < 1 or value > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def parse_note_create_args(raw: str | None) -> tuple[dict[str, Any], str]:
    tokens = (raw or "").strip().split()
    options: dict[str, Any] = {}
    content_start = 0
    for index, token in enumerate(tokens):
        if "=" not in token:
            content_start = index
            break
        key, value = token.split("=", 1)
        if key == "tags":
            options["tags"] = value.split(",")
        elif key == "priority":
            options["priority"] = int(value)
        elif key == "source":
            options["source"] = value
        else:
            content_start = index
            break
    else:
        content_start = len(tokens)
    content = " ".join(tokens[content_start:]).strip()
    if not content:
        raise ValueError("Usage: /remember [tags=a,b] [priority=N] [source=url] <text>")
    note_metadata(
        tags=options.get("tags"), priority=options.get("priority"), source=options.get("source")
    )
    return options, content


def parse_note_edit_args(raw: str | None) -> tuple[int, dict[str, Any], str | None]:
    tokens = (raw or "").strip().split()
    if not tokens:
        raise ValueError(
            "Usage: /memory_note_edit <id> [tags=a,b] [priority=N|off] [source=url|off] [text]"
        )
    item_id = int(tokens[0])
    updates: dict[str, Any] = {}
    content_start = len(tokens)
    for index, token in enumerate(tokens[1:], start=1):
        if "=" not in token:
            content_start = index
            break
        key, value = token.split("=", 1)
        if key == "tags":
            updates["tags"] = value.split(",") if value.lower() not in {"off", "none", ""} else []
        elif key == "priority":
            if value.lower() in {"off", "none", "0"}:
                updates["clear_priority"] = True
            else:
                updates["priority"] = int(value)
        elif key == "source":
            if value.lower() in {"off", "none", ""}:
                updates["clear_source"] = True
            else:
                updates["source"] = value
        else:
            content_start = index
            break
    content = " ".join(tokens[content_start:]).strip() if content_start < len(tokens) else None
    if not updates and content is None:
        raise ValueError("memory_note_edit requires metadata changes or new text")
    note_metadata(
        tags=updates.get("tags"), priority=updates.get("priority"), source=updates.get("source")
    )
    return item_id, updates, content


def parse_profile_sources(raw: str, *, current: Scope) -> list[Scope]:
    scopes = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        scopes.append(parse_scope(value, current=current))
    if not scopes:
        raise ValueError("sources must include at least one scope")
    return dedupe_scope_list(scopes)


def dedupe_scope_list(scopes: list[Scope]) -> list[Scope]:
    seen: set[str] = set()
    out: list[Scope] = []
    for scope in scopes:
        if scope.key in seen:
            continue
        seen.add(scope.key)
        out.append(scope)
    return out


def format_item_metadata(metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    tags = metadata.get("tags")
    if isinstance(tags, list) and tags:
        parts.append("tags:" + ",".join(str(tag) for tag in tags))
    priority = metadata.get("priority")
    if priority is not None:
        parts.append(f"priority:{priority}")
    source = metadata.get("source")
    if source:
        parts.append(f"note-source:{source}")
    template = metadata.get("template")
    if template:
        parts.append(f"template:{template}")
    if not parts:
        return ""
    return ", " + ", ".join(parts)


def search_result_rank(result: SearchResult) -> tuple[int, float]:
    tier = 0 if result.source.startswith("knowledge_item:") else 1
    return tier, -result.score


def format_results(results: list[SearchResult]) -> str:
    if not results:
        return "No scoped memory found."
    lines = []
    for result in results:
        sources = ", ".join(str(source) for source in result.source_message_ids) or result.source
        lines.append(
            f"[source:{sources} score:{result.score:.3f} scope:{result.scope.key}] {result.content}"
        )
    return "\n".join(lines)


def compact_answer_sources(results: list[SearchResult], *, limit: int = 8) -> str:
    refs: list[str] = []
    seen: set[str] = set()
    total_refs = sum(
        len(item.source_message_ids) if item.source_message_ids else 1 for item in results
    )
    for result in results:
        source_refs = [str(source_id) for source_id in result.source_message_ids] or [result.source]
        for source_ref in source_refs:
            ref = f"{result.scope.key}#{source_ref}"
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
            if len(refs) >= limit:
                suffix = " + more" if len(seen) < total_refs else ""
                return ", ".join(refs) + suffix
    return ", ".join(refs)


def append_answer_sources(answer: str, results: list[SearchResult]) -> str:
    sources = compact_answer_sources(results)
    if not sources:
        return answer
    lower = answer.lower()
    if "sources:" in lower or "[source:" in lower:
        return answer
    return f"{answer.rstrip()}\n\nSources: {sources}"


def format_memory_status(status: MemoryStatusPreview) -> str:
    lines = [f"Memory status for {status.scope_label}:"]
    values = {
        "messages": status.messages,
        "learned": status.learned,
        "skipped": status.skipped,
        "knowledge_items": status.knowledge_items,
        "embeddings": status.embeddings,
        "events": status.events,
        "pending_reviews": status.pending_reviews,
        "ai_usage_total": status.ai_usage_total,
        "ai_usage_errors": status.ai_usage_errors,
        "ai_usage_avg_latency_ms": status.ai_usage_average_latency_ms
        if status.ai_usage_average_latency_ms is not None
        else "n/a",
    }
    lines.extend(f"- {key}: {value}" for key, value in values.items())
    if status.ai_usage_operations:
        operations = ", ".join(
            f"{name}:{count}" for name, count in sorted(status.ai_usage_operations.items())
        )
        lines.append(f"- ai_usage_operations: {operations}")
    return "\n".join(lines)


def format_scope_statuses(scopes: list[ScopeStatusPreview]) -> str:
    if not scopes:
        return "No known scopes yet."
    lines = ["Known scopes:"]
    for item in scopes[:40]:
        learning = "on" if item.learning_enabled else "off"
        admins = "admins:on" if item.allow_admins else "admins:off"
        last = item.last_message_at.isoformat() if item.last_message_at else "never"
        lines.append(
            f"- {item.scope.key} {item.label} learning:{learning} {admins} "
            f"messages:{item.messages} learned:{item.learned} skipped:{item.skipped} "
            f"knowledge:{item.knowledge_items} last:{last}"
        )
    return "\n".join(lines)


def format_skipped_messages(messages: list[SkippedMessagePreview]) -> str:
    if not messages:
        return "No skipped messages found for that scope."
    lines = ["Skipped message diagnostics:"]
    for item in messages[:30]:
        lines.append(
            f"- #{item.telegram_message_id} {item.scope.key} {item.reason}: {item.explanation}"
        )
    return "\n".join(lines)


def format_memory_items(items: list[MemoryItemPreview]) -> str:
    if not items:
        return "No memory items found."
    lines = ["Memory items:"]
    for item in items[:30]:
        sources = ",".join(str(source) for source in item.source_message_ids) or "manual"
        compact = " ".join(item.content.split())
        lines.append(
            f"- #{item.id} {item.scope.key} {item.kind} sources:{sources}: {compact[:220]}"
        )
    return "\n".join(lines)


def format_memory_item_refresh(result: MemoryItemRefreshResult) -> str:
    summary = "yes" if result.summarized else "no"
    return (
        f"Refreshed memory item #{result.id} in {result.scope.key}: mode={result.mode}, "
        f"summarized={summary}, embeddings_refreshed={result.embeddings_refreshed}."
    )


def format_settings_change_preview(preview: SettingsChangePreview) -> str:
    lines = [f"Settings change for {preview.scope.key}:"]
    for key, value in preview.updates.items():
        old = preview.current_values.get(key)
        lines.append(
            f"- {key}: {old if old is not None else 'off'} -> "
            f"{value if value is not None else 'off'}"
        )
    if preview.applied:
        lines.append("Applied and audited.")
    elif preview.token and preview.expires_at:
        lines.append(
            f"Preview only. Re-run /settings confirm={preview.token} before "
            f"{preview.expires_at.isoformat()} to apply."
        )
    return "\n".join(lines)


def parse_scope_settings_args(raw: dict[str, str]) -> dict[str, int | str | bool | None]:
    updates: dict[str, int | str | bool | None] = {}
    aliases = {
        "top_k": "retrieval_top_k",
        "retrieval_top_k": "retrieval_top_k",
        "summary_batch_size": "summary_batch_size",
        "summary_model": "summary_model",
        "system_prompt": "system_prompt_template",
        "system_prompt_template": "system_prompt_template",
        "provider": "provider_profile",
        "provider_profile": "provider_profile",
        "chat_model": "chat_model",
        "embedding_model": "embedding_model",
        "reasoning": "reasoning_level",
        "reasoning_level": "reasoning_level",
        "raw_retention_days": "raw_message_retention_days",
        "raw_message_retention_days": "raw_message_retention_days",
        "knowledge_retention_days": "knowledge_retention_days",
        "review_before_compact": "review_before_compact",
    }
    for raw_key, raw_value in raw.items():
        if raw_key == "scope":
            continue
        key = aliases.get(raw_key)
        if key is None:
            raise ValueError(f"unknown setting: {raw_key}")
        if key in {"retrieval_top_k", "summary_batch_size"}:
            updates[key] = int(raw_value)
        elif key in {"raw_message_retention_days", "knowledge_retention_days"}:
            updates[key] = (
                None if raw_value.lower() in {"off", "none", "null", "0"} else int(raw_value)
            )
        elif key == "review_before_compact":
            updates[key] = parse_bool(raw_value)
        else:
            updates[key] = raw_value.strip()
    validate_scope_settings(updates)
    return updates


def parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean: {raw}")


def format_scope_settings(settings: EffectiveScopeSettings) -> str:
    return "\n".join(
        f"{key}: {value if value is not None else 'off'}"
        for key, value in settings.as_dict().items()
    )


def format_note_previews(notes: list[MemoryNotePreview]) -> str:
    if not notes:
        return "No manual notes found."
    lines = ["Manual notes:"]
    for note in notes[:30]:
        metadata = []
        if note.tags:
            metadata.append("tags:" + ",".join(note.tags))
        if note.priority is not None:
            metadata.append(f"priority:{note.priority}")
        if note.source:
            metadata.append(f"source:{note.source}")
        suffix = f" ({'; '.join(metadata)})" if metadata else ""
        compact = " ".join(note.content.split())
        lines.append(f"- #{note.id} {note.scope.key}{suffix}: {compact[:240]}")
    return "\n".join(lines)


def format_memory_profiles(profiles: list[MemoryProfileView]) -> str:
    if not profiles:
        return "No memory profiles configured."
    lines = ["Memory profiles:"]
    for profile in profiles:
        scopes = ", ".join(scope.key for scope in profile.source_scopes) or "none"
        description = f" - {profile.description}" if profile.description else ""
        lines.append(f"- {profile.name}: {scopes}{description}")
    return "\n".join(lines)


def format_summary_templates(templates: list[SummaryTemplatePreview]) -> str:
    if not templates:
        return "No summary templates configured."
    lines = ["Summary templates:"]
    for template in templates:
        origin = "built-in" if template.builtin else "custom"
        compact = " ".join(template.prompt.split())
        lines.append(f"- {template.name} ({origin}): {compact[:220]}")
    return "\n".join(lines)


def format_system_prompt_templates(templates: list[SystemPromptTemplatePreview]) -> str:
    if not templates:
        return "No system prompt templates configured."
    lines = ["System prompt templates:"]
    for template in templates:
        origin = "built-in" if template.builtin else "custom"
        compact = " ".join(template.prompt.split())
        lines.append(f"- {template.name} ({origin}, {template.version}): {compact[:260]}")
    return "\n".join(lines)


def format_range_summary(result: RangeSummaryResult) -> str:
    started = result.started_at.isoformat()
    ended = result.ended_at.isoformat()
    return (
        f"Created range summary #{result.knowledge_item_id} with template {result.template_name}: "
        f"{len(result.source_message_ids)} source messages from {started} to {ended}."
    )


def format_review_previews(reviews: list[MemoryReviewPreview]) -> str:
    if not reviews:
        return "No pending memory reviews."
    lines = ["Pending memory reviews:"]
    for review in reviews[:20]:
        sources = ", ".join(str(source) for source in review.source_message_ids) or "manual"
        compact = " ".join(review.content.split())
        lines.append(
            f"- #{review.id} {review.scope.key} {review.kind} sources:{sources} {compact[:240]}"
        )
    return "\n".join(lines)


def parse_runtime_settings_args(raw: dict[str, str]) -> dict[str, Any]:
    aliases = {
        "top_k": "retrieval_top_k",
        "provider": "provider_profile",
        "system_prompt": "system_prompt_template",
        "raw_retention_days": "raw_message_retention_days",
        "knowledge_retention_days": "knowledge_retention_days",
    }
    updates: dict[str, Any] = {}
    for raw_key, raw_value in raw.items():
        key = aliases.get(raw_key, raw_key)
        if key not in SAFE_RUNTIME_SETTING_TYPES:
            continue
        kind = SAFE_RUNTIME_SETTING_TYPES[key]
        value = raw_value.strip()
        if kind == "bool":
            updates[key] = parse_bool(value)
        elif kind == "positive_int":
            updates[key] = int(value)
        elif kind == "optional_positive_int":
            updates[key] = None if value.lower() == "off" else int(value)
        elif kind == "non_negative_float":
            updates[key] = float(value)
        else:
            updates[key] = value
    return updates


def validate_runtime_settings_values(values: dict[str, Any], settings: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in SAFE_RUNTIME_SETTING_TYPES:
            raise ValueError(f"unknown runtime setting: {key}")
        kind = SAFE_RUNTIME_SETTING_TYPES[key]
        if kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            out[key] = value
        elif kind == "positive_int":
            clean = int(value)
            if clean < 1:
                raise ValueError(f"{key} must be >= 1")
            out[key] = clean
        elif kind == "optional_positive_int":
            if value is None:
                out[key] = None
            else:
                clean = int(value)
                if clean < 1:
                    raise ValueError(f"{key} must be >= 1 or off")
                out[key] = clean
        elif kind == "non_negative_float":
            clean_float = float(value)
            if clean_float < 0:
                raise ValueError(f"{key} must be >= 0")
            out[key] = clean_float
        else:
            clean_str = str(value).strip()
            if not clean_str:
                raise ValueError(f"{key} cannot be empty")
            out[key] = clean_str
    if "provider_profile" in out:
        settings.provider_profile_by_name(str(out["provider_profile"]))
    if "chat_model" in out:
        settings.validate_chat_model(str(out["chat_model"]))
    if "summary_model" in out:
        settings.validate_chat_model(str(out["summary_model"]))
    if "embedding_model" in out:
        settings.validate_embedding_model(str(out["embedding_model"]))
    if "reasoning_level" in out:
        settings.validate_reasoning_level(str(out["reasoning_level"]))
    return out


def format_runtime_settings(settings: list[RuntimeSettingPreview]) -> str:
    if not settings:
        return "No runtime settings are editable."
    lines = ["Runtime settings:"]
    for setting in settings:
        value = setting.value if setting.value is not None else "off"
        default = setting.default_value if setting.default_value is not None else "off"
        suffix = "default" if setting.version is None else f"v{setting.version}"
        lines.append(f"- {setting.key}: {value} (default: {default}, {suffix})")
    return "\n".join(lines)


def format_audit_events(events: list[AuditEventPreview]) -> str:
    if not events:
        return "No audit events found."
    lines = ["Audit events:"]
    for event in events[:50]:
        scope = event.scope.key if event.scope is not None else "global"
        payload = json.dumps(event.payload, ensure_ascii=False, sort_keys=True)
        user = event.user_id if event.user_id is not None else "system"
        lines.append(
            f"- #{event.id} {event.created_at.isoformat()} {event.event_type} "
            f"scope={scope} user={user} payload={payload[:240]}"
        )
    return "\n".join(lines)


def audit_events_jsonl(events: list[AuditEventPreview]) -> str:
    lines = []
    for event in events:
        lines.append(
            json.dumps(
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "scope": event.scope.key if event.scope is not None else None,
                    "user_id": event.user_id,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines) + ("\n" if lines else "")


def fallback_answer(results: list[SearchResult]) -> str:
    if not results:
        return "I do not have scoped memory for that yet."
    return "From scoped memory:\n" + format_results(results)


def scope_label(scope: Scope, metadata: Any) -> str:
    if metadata is None:
        return f"`{scope.key}`"
    parts = [value for value in (metadata.chat_title, metadata.topic_title) if value]
    label = " / ".join(parts) or scope.key
    if metadata.chat_username:
        return f"{label} (`{scope.key}`, @{metadata.chat_username.removeprefix('@')})"
    return f"{label} (`{scope.key}`)"
