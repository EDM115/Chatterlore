from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.factory import ProviderRegistry, ProviderSelection
from pzp_ai_bot.ai.protocols import ChatMessage, ChatProvider, EmbeddingProvider
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import KnowledgeItem
from pzp_ai_bot.db.repositories import (
    AIUsageStats,
    CanonicalScopeSnapshotRepository,
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
    canonical_scope_fingerprint,
    fallback_entries,
    parse_canonical_response,
)
from pzp_ai_bot.memory.contracts import (
    AIRequestOptions,
    AuditEventPreview,
    CanonicalScopeInput,
    CanonicalScopeRefresh,
    ChatProviderExecution,
    EmbeddingGenerationResult,
    IncomingMessage,
    IngestResult,
    MemoryItemPreview,
    MemoryItemRefreshResult,
    MemoryNotePreview,
    MemoryReviewPreview,
    MemoryStatusPreview,
    PendingSummaryOutcome,
    ProviderCallTelemetry,
    ProviderOperationError,
    RangeSummaryResult,
    RuntimeSettingPreview,
    ScopeStatusPreview,
    SettingsChangePreview,
    SkippedMessagePreview,
    SummaryBatchOutcome,
    SummaryClaim,
    SummaryGenerationResult,
    SummaryTemplatePreview,
    SystemPromptTemplatePreview,
)
from pzp_ai_bot.memory.markdown import MarkdownMemoryStore
from pzp_ai_bot.memory.policy import PolicyService
from pzp_ai_bot.memory.prompts import (
    ANSWER_INSUFFICIENT_CONTEXT,
    BUILTIN_SYSTEM_PROMPTS,
    CANONICAL_PROMPT_VERSION,
    PROMPT_VERSION,
    SUMMARY_BASE_PROMPT,
    PromptTemplate,
    prompt_metadata,
)
from pzp_ai_bot.memory.provider_operations import ProviderOperations, provider_call_telemetry
from pzp_ai_bot.memory.provider_operations import telemetry_scope as telemetry_scope
from pzp_ai_bot.memory.retrieval import Retriever, SearchResult
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.transfer import (
    MemoryExportChunk,
    MemoryExportFormat,
    MemoryTransferService,
    TelegramDesktopImportResult,
    normalize_text,
)
from pzp_ai_bot.memory.transfer import format_item_metadata as format_item_metadata
from pzp_ai_bot.memory.transfer import parse_memory_export_format as parse_memory_export_format

SAFE_RUNTIME_SETTING_TYPES: dict[str, str] = {
    "learning_default_enabled": "bool",
    "retrieval_candidate_limit": "positive_int",
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

SUMMARY_CLAIM_TIMEOUT_SECONDS = 15 * 60


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
        self._provider_operations = ProviderOperations()
        self._transfer = MemoryTransferService(
            session_factory=self.session_factory,
            markdown=self.markdown,
            default_scope_settings=self._default_scope_settings,
            select_provider=self._select_provider,
            generate_embeddings=self._generate_embeddings,
            add_embedding_usage_in_session=self._add_embedding_usage_in_session,
            compact_memory=self.compact_memory,
        )

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
            policy_snapshot = await policies.load_snapshot()
            scopes_by_key: dict[str, Scope] = {}
            for row in message_counts.values():
                scopes_by_key[row.scope.key] = row.scope
            for row in knowledge_counts.values():
                scopes_by_key[row.scope.key] = row.scope
            for key, metadata in metadata_by_scope.items():
                scopes_by_key[key] = Scope(metadata.chat_id, metadata.message_thread_id)
            for policy in policy_snapshot.rules:
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
                status = await policies.status(scope, snapshot=policy_snapshot)
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
            "retrieval_candidate_limit": self.settings.retrieval_candidate_limit,
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
        trigger_summary = False
        async with session_scope(self.session_factory) as session:
            existing = await MessageRepository(session).get_logical(
                incoming.scope, incoming.telegram_message_id
            )
            if existing is not None:
                return IngestResult(existing.learned, "duplicate_message", existing.id, False)
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
        if decision.allowed:
            selection = self._select_provider(scope_settings)
            embedding_generation = await self._generate_embeddings(
                selection,
                [text],
                operation="embed_message",
                metadata={"scope": incoming.scope.key, "target_type": "message"},
            )
            if embedding_generation.error is not None:
                async with session_scope(self.session_factory) as session:
                    await self._add_embedding_usage_in_session(
                        session,
                        selection=selection,
                        generation=embedding_generation,
                        texts_count=1,
                        operation="embed_message",
                        metadata={"scope": incoming.scope.key, "target_type": "message"},
                    )
                raise embedding_generation.error
        else:
            selection = None
            embedding_generation = None
        async with session_scope(self.session_factory) as session:
            if any((incoming.chat_title, incoming.topic_title, incoming.chat_username)):
                await ScopeMetadataRepository(session).upsert(
                    incoming.scope,
                    chat_title=incoming.chat_title,
                    topic_title=incoming.topic_title,
                    chat_username=incoming.chat_username,
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
            assert selection is not None
            assert embedding_generation is not None
            await self._add_embedding_usage_in_session(
                session,
                selection=selection,
                generation=embedding_generation,
                texts_count=1,
                operation="embed_message",
                metadata={"scope": incoming.scope.key, "target_type": "message"},
            )
            await EmbeddingRepository(session).add(
                target_type="message",
                target_id=message.id,
                scope=incoming.scope,
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                vector=embedding_generation.vectors[0],
            )
            pending = await message_repo.pending_for_summary(
                incoming.scope, limit=scope_settings.summary_batch_size
            )
            trigger_summary = len(pending) >= scope_settings.summary_batch_size
            message_id = message.id
        if trigger_summary:
            summary_outcome = await self._process_summary_batch(incoming.scope)
            needs_compact = summary_outcome.needs_compact
            summarized = summary_outcome.completed_messages > 0
        if needs_compact:
            await self.compact_memory(dirty_scopes={incoming.scope})
        return IngestResult(True, decision.reason, message_id, summarized)

    async def summarize_now(
        self,
        scope: Scope,
        *,
        template_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> int:
        if scope.is_global and template_name is not None:
            raise ValueError("template summaries require a concrete scope")
        if scope.is_global:
            async with session_scope(self.session_factory) as session:
                scopes = await MessageRepository(session).pending_scopes()
        else:
            scopes = [scope]
        count = 0
        dirty_scopes: set[Scope] = set()
        for pending_scope in scopes:
            while True:
                summary_outcome = await self._process_summary_batch(
                    pending_scope, template_name=template_name, ai_options=ai_options
                )
                if summary_outcome.claimed_messages == 0:
                    break
                count += summary_outcome.completed_messages
                if summary_outcome.needs_compact:
                    dirty_scopes.add(pending_scope)
                if summary_outcome.completed_messages == 0:
                    break
        if dirty_scopes:
            await self.compact_memory(dirty_scopes=dirty_scopes)
        return count

    async def summarize_idle(self, *, now: datetime | None = None) -> int:
        claim_now = now or utc_now()
        cutoff = claim_now - timedelta(seconds=int(self.runtime_setting("summary_idle_seconds")))
        async with session_scope(self.session_factory) as session:
            scopes = await MessageRepository(session).pending_scopes(idle_before=cutoff)
        count = 0
        dirty_scopes: set[Scope] = set()
        for scope in scopes:
            summary_outcome = await self._process_summary_batch(
                scope, now=claim_now, created_before=cutoff
            )
            count += summary_outcome.completed_messages
            if summary_outcome.needs_compact:
                dirty_scopes.add(scope)
        if dirty_scopes:
            await self.compact_memory(dirty_scopes=dirty_scopes)
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
        clean_content = content.strip()
        async with session_scope(self.session_factory) as session:
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
        selection = self._select_provider(scope_settings)
        embedding_generation = await self._generate_embeddings(
            selection,
            [clean_content],
            operation="embed_knowledge",
            metadata={"scope": scope.key, "target_type": "knowledge_item", "kind": "manual_note"},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self._add_embedding_usage_in_session(
                    session,
                    selection=selection,
                    generation=embedding_generation,
                    texts_count=1,
                    operation="embed_knowledge",
                    metadata={
                        "scope": scope.key,
                        "target_type": "knowledge_item",
                        "kind": "manual_note",
                    },
                )
            raise embedding_generation.error
        async with session_scope(self.session_factory) as session:
            item = await KnowledgeRepository(session).add(
                scope=scope,
                kind="manual_note",
                content=clean_content,
                source_message_ids=[] if source_message_id is None else [source_message_id],
                metadata_json=metadata,
            )
            await self._add_embedding_usage_in_session(
                session,
                selection=selection,
                generation=embedding_generation,
                texts_count=1,
                operation="embed_knowledge",
                metadata={
                    "scope": scope.key,
                    "target_type": "knowledge_item",
                    "kind": "manual_note",
                },
            )
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item.id,
                scope=scope,
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                vector=embedding_generation.vectors[0],
            )
            item_id = item.id
        await self.compact_memory(dirty_scopes={scope})
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
            item = await repo.get(item_id)
            if item is not None and (
                item.kind != "manual_note"
                or not scope.contains(Scope(item.chat_id, item.message_thread_id))
            ):
                item = None
            if item is None:
                raise ValueError(f"manual note #{item_id} was not found in scope {scope.key}")
            item_scope = Scope(item.chat_id, item.message_thread_id)
            updated_content = content.strip() if content is not None else item.content
            scope_settings = await ScopeSettingsRepository(session).effective(
                item_scope, self._default_scope_settings()
            )
        selection = self._select_provider(scope_settings)
        embedding_generation = await self._generate_embeddings(
            selection,
            [updated_content],
            operation="embed_knowledge",
            metadata={"scope": item_scope.key, "target_type": "knowledge_item"},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self._add_embedding_usage_in_session(
                    session,
                    selection=selection,
                    generation=embedding_generation,
                    texts_count=1,
                    operation="embed_knowledge",
                    metadata={"scope": item_scope.key, "target_type": "knowledge_item"},
                )
            raise embedding_generation.error
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
            await self._add_embedding_usage_in_session(
                session,
                selection=selection,
                generation=embedding_generation,
                texts_count=1,
                operation="embed_knowledge",
                metadata={"scope": item_scope.key, "target_type": "knowledge_item"},
            )
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item.id,
                scope=item_scope,
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                vector=embedding_generation.vectors[0],
            )
            await EventRepository(session).add(
                event_type="manual_note_updated",
                scope=scope,
                payload={"knowledge_item_id": item.id, "metadata": item.metadata_json or {}},
            )
            preview = memory_note_preview(item)
        await self.compact_memory(dirty_scopes={item_scope})
        return preview

    async def delete_note(self, scope: Scope, item_id: int) -> None:
        async with session_scope(self.session_factory) as session:
            repository = KnowledgeRepository(session)
            item = await repository.get(item_id)
            if item is not None and (
                item.kind != "manual_note"
                or not scope.contains(Scope(item.chat_id, item.message_thread_id))
            ):
                item = None
            if item is None:
                raise ValueError(f"manual note #{item_id} was not found in scope {scope.key}")
            item_scope = Scope(item.chat_id, item.message_thread_id)
            deleted = await repository.delete_manual_note(item_id, scope=item_scope)
            if not deleted:
                raise ValueError(f"manual note #{item_id} was not found in scope {scope.key}")
            await EmbeddingRepository(session).delete_targets(
                target_type="knowledge_item", target_ids=[item_id]
            )
            await EventRepository(session).add(
                event_type="manual_note_deleted",
                scope=item_scope,
                payload={"knowledge_item_id": item_id},
            )
        await self.compact_memory(dirty_scopes={item_scope})

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
            ChatMessage(role="user", content=f"Question: {question}\n\nScoped memory:\n{context}"),
        ]
        execution = await self._execute_chat_provider(
            selection,
            messages,
            operation="ask",
            metadata={
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
            temperature=0.1,
            max_tokens=700,
            reasoning_effort=reasoning_level,
        )
        content = (
            execution.result.content if execution.result is not None else fallback_answer(results)
        )
        async with session_scope(self.session_factory) as session:
            await self._add_provider_usage_in_session(session, execution.telemetry)
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
            scopes = await self._retrieval_scopes_in_session(session, scope, profile_name)
        embedding_generation = await self._generate_embeddings(
            selection,
            [query],
            operation="embed_query",
            metadata={"scope": scope.key, "memory_profile": profile_name},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self._add_embedding_usage_in_session(
                    session,
                    selection=selection,
                    generation=embedding_generation,
                    texts_count=1,
                    operation="embed_query",
                    metadata={"scope": scope.key, "memory_profile": profile_name},
                )
            raise embedding_generation.error
        query_vector = embedding_generation.vectors[0]
        async with session_scope(self.session_factory) as session:
            await self._add_embedding_usage_in_session(
                session,
                selection=selection,
                generation=embedding_generation,
                texts_count=1,
                operation="embed_query",
                metadata={"scope": scope.key, "memory_profile": profile_name},
            )
            retriever = Retriever(
                embedding_repo=EmbeddingRepository(session),
                knowledge_repo=KnowledgeRepository(session),
                message_repo=MessageRepository(session),
            )
            results: list[SearchResult] = []
            for source_scope in scopes:
                results.extend(
                    await retriever.search(
                        query_vector,
                        source_scope,
                        top_k,
                        candidate_limit=int(self.runtime_setting("retrieval_candidate_limit")),
                    )
                )
            results.sort(key=search_result_rank)
            return results[:top_k]

    async def compact_memory(
        self, dirty_scopes: set[Scope] | None = None, *, force: bool = False
    ) -> Path:
        await self.rebuild_daily_projection()
        async with session_scope(self.session_factory) as session:
            current_scopes = {
                row.scope for row in await KnowledgeRepository(session).scope_counts()
            }
            valid_snapshots = await CanonicalScopeSnapshotRepository(session).list()
        if force or dirty_scopes is None:
            target_scopes = set(current_scopes)
        else:
            target_scopes = (set(dirty_scopes) & current_scopes) | (
                current_scopes - set(valid_snapshots)
            )
        refreshes: dict[Scope, CanonicalScopeRefresh] = {}
        for scope in sorted(target_scopes, key=lambda value: value.key):
            refreshes[scope] = await self._refresh_canonical_scope(scope, force=force)
        for _ in range(3):
            async with session_scope(self.session_factory) as session:
                latest_scopes = {
                    row.scope for row in await KnowledgeRepository(session).scope_counts()
                }
                latest_snapshots = await CanonicalScopeSnapshotRepository(session).list()
            missing_scopes = latest_scopes - set(latest_snapshots) - set(refreshes)
            if not missing_scopes:
                break
            for scope in sorted(missing_scopes, key=lambda value: value.key):
                refreshes[scope] = await self._refresh_canonical_scope(scope, force=False)
        async with session_scope(self.session_factory) as session:
            knowledge_repo = KnowledgeRepository(session)
            snapshot_repo = CanonicalScopeSnapshotRepository(session)
            metadata_repo = ScopeMetadataRepository(session)
            message_repo = MessageRepository(session)
            current_scopes = {row.scope for row in await knowledge_repo.scope_counts()}
            await snapshot_repo.delete_missing({scope.key for scope in current_scopes})
            snapshots = await snapshot_repo.list()
            entries_by_scope: dict[Scope, list[CanonicalMemoryEntry]] = {}
            metadata_by_scope = await metadata_repo.list_all()
            sources_by_scope = {}
            for scope in sorted(current_scopes, key=lambda value: value.key):
                refresh = refreshes.get(scope)
                snapshot = snapshots.get(scope)
                if refresh is not None and not refresh.snapshot_persisted:
                    entries = list(refresh.entries)
                elif snapshot is not None:
                    entries = list(snapshot.entries)
                else:
                    entries = fallback_entries(await knowledge_repo.list_exact_scope(scope))
                entries_by_scope[scope] = entries
                metadata = await metadata_repo.get_effective(scope)
                if metadata is not None:
                    metadata_by_scope[scope.key] = metadata
                source_ids = sorted(
                    {source for entry in entries for source in entry.source_message_ids}
                )
                sources_by_scope[scope.key] = await message_repo.source_references(
                    scope, source_ids, metadata
                )
        path = self.markdown.write_canonical(entries_by_scope, metadata_by_scope, sources_by_scope)
        async with session_scope(self.session_factory) as session:
            await EventRepository(session).add(
                event_type="compact_memory",
                payload={
                    "path": str(path),
                    "dirty_scopes": (
                        None
                        if dirty_scopes is None
                        else sorted(scope.key for scope in dirty_scopes)
                    ),
                    "force": force,
                    "provider_calls": sum(refresh.provider_calls for refresh in refreshes.values()),
                    "recomputed_scopes": sorted(
                        scope.key
                        for scope, refresh in refreshes.items()
                        if refresh.snapshot_persisted
                    ),
                },
            )
        return path

    async def rebuild_daily_projection(self) -> list[Path]:
        async with session_scope(self.session_factory) as session:
            items = await KnowledgeRepository(session).list_scope()
        try:
            return self.markdown.rebuild_daily(items)
        except Exception as exc:
            async with session_scope(self.session_factory) as session:
                await EventRepository(session).add(
                    event_type="markdown_projection_failed",
                    payload={"error_type": type(exc).__name__},
                )
            raise

    async def export_memory(
        self, scope: Scope | None = None, fmt: MemoryExportFormat = "md"
    ) -> str:
        return await self._transfer.export_memory(scope=scope, fmt=fmt)

    async def export_memory_chunks(
        self, scope: Scope | None = None, fmt: MemoryExportFormat = "md"
    ) -> AsyncIterator[MemoryExportChunk]:
        async for chunk in self._transfer.export_memory_chunks(scope=scope, fmt=fmt):
            yield chunk

    async def import_jsonl(self, path: Path, scope: Scope) -> int:
        return await self._transfer.import_jsonl(path, scope)

    async def import_telegram_desktop_json(
        self, path: Path, scope: Scope
    ) -> TelegramDesktopImportResult:
        return await self._transfer.import_telegram_desktop_json(path, scope)

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
        await self.compact_memory(dirty_scopes={item_scope})
        return preview

    async def refresh_memory_item(
        self, scope: Scope, item_id: int, *, mode: str = "embed", updated_by: int | None = None
    ) -> MemoryItemRefreshResult:
        clean_mode = mode.strip().lower()
        if clean_mode not in {"embed", "summarize", "both"}:
            raise ValueError("mode must be embed, summarize or both")
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
            item_kind = item.kind
            refreshed_content = item.content
            summary_source_ids: tuple[int, ...] = ()
            summary_texts: tuple[str, ...] = ()
            template_name = str((item.metadata_json or {}).get("template") or "general")
            template_instruction = ""
            template_version = SUMMARY_BASE_PROMPT.version
            if clean_mode in {"summarize", "both"}:
                source_ids = list(item.source_message_ids)
                if not source_ids:
                    raise ValueError("memory item has no source messages to summarize")
                messages = await MessageRepository(session).by_telegram_ids(item_scope, source_ids)
                if not messages:
                    raise ValueError("memory item source messages were not found")
                template_instruction = await self._summary_template_prompt_in_session(
                    session, template_name
                )
                summary_source_ids = tuple(message.telegram_message_id for message in messages)
                summary_texts = tuple(message.normalized_text for message in messages)
        generation: SummaryGenerationResult | None = None
        if clean_mode in {"summarize", "both"}:
            generation = await self._generate_summary(
                scope=item_scope,
                source_telegram_ids=summary_source_ids,
                normalized_texts=summary_texts,
                scope_settings=scope_settings,
                template_name=template_name,
                template_instruction=template_instruction,
                template_version=template_version,
                context_line=f"Refresh memory item #{item_id} from its source messages.",
                operation="memory_item_refresh_summary",
                usage_metadata={"knowledge_item_id": item_id},
            )
            if generation.content is None:
                async with session_scope(self.session_factory) as session:
                    await self._add_refresh_summary_usage_in_session(
                        session, item_scope, item_id, generation
                    )
                raise RuntimeError(f"summary generation failed: {generation.error_type}")
            refreshed_content = generation.content
        embedding_selection = self._select_provider(scope_settings)
        embedding_generation = await self._generate_embeddings(
            embedding_selection,
            [refreshed_content],
            operation="embed_knowledge",
            metadata={
                "scope": item_scope.key,
                "target_type": "knowledge_item",
                "kind": item_kind,
                "refresh": clean_mode,
            },
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                if generation is not None:
                    await self._add_refresh_summary_usage_in_session(
                        session, item_scope, item_id, generation
                    )
                await self._add_embedding_usage_in_session(
                    session,
                    selection=embedding_selection,
                    generation=embedding_generation,
                    texts_count=1,
                    operation="embed_knowledge",
                    metadata={
                        "scope": item_scope.key,
                        "target_type": "knowledge_item",
                        "kind": item_kind,
                        "refresh": clean_mode,
                    },
                )
            raise embedding_generation.error
        async with session_scope(self.session_factory) as session:
            repo = KnowledgeRepository(session)
            item = await repo.get(item_id)
            if item is None or not scope.contains(Scope(item.chat_id, item.message_thread_id)):
                raise ValueError(f"memory item #{item_id} was not found in scope {scope.key}")
            if generation is not None:
                await self._add_refresh_summary_usage_in_session(
                    session, item_scope, item_id, generation
                )
                await repo.update_item(
                    item_id,
                    scope=item_scope,
                    content=refreshed_content,
                    metadata_updates={
                        "refreshed_at": utc_now().isoformat(),
                        "refresh_mode": clean_mode,
                    },
                )
            embeddings_deleted = await EmbeddingRepository(session).delete_targets(
                target_type="knowledge_item", target_ids=[item_id]
            )
            await self._add_embedding_usage_in_session(
                session,
                selection=embedding_selection,
                generation=embedding_generation,
                texts_count=1,
                operation="embed_knowledge",
                metadata={
                    "scope": item_scope.key,
                    "target_type": "knowledge_item",
                    "kind": item_kind,
                    "refresh": clean_mode,
                },
            )
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item_id,
                scope=item_scope,
                provider=embedding_selection.embedding_provider.name,
                model=embedding_selection.embedding_model,
                vector=embedding_generation.vectors[0],
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
                id=item_id,
                scope=item_scope,
                kind=item_kind,
                mode=clean_mode,
                summarized=generation is not None,
                embeddings_refreshed=1,
            )
        await self.compact_memory(dirty_scopes={item_scope})
        return result

    async def _add_refresh_summary_usage_in_session(
        self, session: AsyncSession, scope: Scope, item_id: int, generation: SummaryGenerationResult
    ) -> None:
        metadata = dict(generation.telemetry.metadata)
        if (
            generation.telemetry.operation != "memory_item_refresh_summary"
            or metadata.get("scope") != scope.key
            or metadata.get("knowledge_item_id") != item_id
        ):
            raise RuntimeError("refresh summary telemetry does not match its persistence boundary")
        await self._add_provider_usage_in_session(session, generation.telemetry)

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
        dirty_scopes: set[Scope] = set()
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
                dirty_scopes = {
                    Scope(item.chat_id, item.message_thread_id)
                    for item in (await knowledge_repo.get_many(knowledge_ids)).values()
                }
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
            await self.compact_memory(dirty_scopes=dirty_scopes)
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
                    updated_by=updated_by,
                )
            await EventRepository(session).add(
                event_type="memory_profile_permission",
                scope=destination,
                user_id=updated_by,
                payload={"profile": profile.name, "can_query": can_query},
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
        request = ai_options or AIRequestOptions()
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
            template_instruction = (
                request.summary_template_override
                or await self._summary_template_prompt_in_session(session, template_name)
            )
            template_version = (
                PROMPT_VERSION if request.summary_template_override else SUMMARY_BASE_PROMPT.version
            )
            source_ids = tuple(message.telegram_message_id for message in pending)
            message_ids = [message.id for message in pending]
            normalized_texts = tuple(message.normalized_text for message in pending)
            started_at = start.created_at
            ended_at = end.created_at
        generation = await self._generate_summary(
            scope=scope,
            source_telegram_ids=source_ids,
            normalized_texts=normalized_texts,
            scope_settings=scope_settings,
            template_name=template_name,
            template_instruction=template_instruction,
            template_version=template_version,
            context_line=(
                f"Range: Telegram messages {start_message_id}..{end_message_id}, "
                f"dates {started_at.isoformat()}..{ended_at.isoformat()}."
            ),
            operation="range_summary",
            usage_metadata={"start_message_id": start_message_id, "end_message_id": end_message_id},
            ai_options=ai_options,
        )
        if generation.content is None:
            async with session_scope(self.session_factory) as session:
                await self._add_range_summary_usage_in_session(
                    session, scope, template_name, generation
                )
            raise RuntimeError(f"summary generation failed: {generation.error_type}")
        embedding_selection = self._select_provider(scope_settings)
        embedding_generation = await self._generate_embeddings(
            embedding_selection,
            [generation.content],
            operation="embed_knowledge",
            metadata={"scope": scope.key, "target_type": "knowledge_item", "kind": "range_summary"},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self._add_range_summary_usage_in_session(
                    session, scope, template_name, generation
                )
                await self._add_embedding_usage_in_session(
                    session,
                    selection=embedding_selection,
                    generation=embedding_generation,
                    texts_count=1,
                    operation="embed_knowledge",
                    metadata={
                        "scope": scope.key,
                        "target_type": "knowledge_item",
                        "kind": "range_summary",
                    },
                )
            raise embedding_generation.error
        source_id_list = list(source_ids)
        async with session_scope(self.session_factory) as session:
            await self._add_range_summary_usage_in_session(
                session, scope, template_name, generation
            )
            await self._add_embedding_usage_in_session(
                session,
                selection=embedding_selection,
                generation=embedding_generation,
                texts_count=1,
                operation="embed_knowledge",
                metadata={
                    "scope": scope.key,
                    "target_type": "knowledge_item",
                    "kind": "range_summary",
                },
            )
            item = await KnowledgeRepository(session).add(
                scope=scope,
                kind="range_summary",
                content=generation.content,
                source_message_ids=source_id_list,
                metadata_json={
                    "template": template_name,
                    "start_message_id": start_message_id,
                    "end_message_id": end_message_id,
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                },
            )
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item.id,
                scope=scope,
                provider=embedding_selection.embedding_provider.name,
                model=embedding_selection.embedding_model,
                vector=embedding_generation.vectors[0],
            )
            await MessageRepository(session).mark_summarized(message_ids)
            await EventRepository(session).add(
                event_type="range_summary",
                scope=scope,
                payload={
                    "knowledge_item_id": item.id,
                    "template": template_name,
                    "source_message_ids": source_id_list,
                },
            )
            summary = RangeSummaryResult(
                item.id, source_id_list, started_at, ended_at, template_name
            )
        await self.compact_memory(dirty_scopes={scope})
        return summary

    async def _add_range_summary_usage_in_session(
        self,
        session: AsyncSession,
        scope: Scope,
        template_name: str,
        generation: SummaryGenerationResult,
    ) -> None:
        metadata = dict(generation.telemetry.metadata)
        if (
            generation.telemetry.operation != "range_summary"
            or metadata.get("scope") != scope.key
            or metadata.get("summary_template") != template_name
        ):
            raise RuntimeError("range summary telemetry does not match its persistence boundary")
        await self._add_provider_usage_in_session(session, generation.telemetry)

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

    async def pending_review_scope(self, review_id: int) -> Scope:
        async with session_scope(self.session_factory) as session:
            review = await MemoryReviewRepository(session).get_pending(review_id)
            if review is None:
                raise ValueError(f"pending review #{review_id} was not found")
            return Scope(review.chat_id, review.message_thread_id)

    async def approve_review(self, review_id: int, *, reviewed_by: int | None = None) -> int:
        async with session_scope(self.session_factory) as session:
            repo = MemoryReviewRepository(session)
            review = await repo.get_pending(review_id)
            if review is None:
                raise ValueError(f"pending review #{review_id} was not found")
            scope = Scope(review.chat_id, review.message_thread_id)
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            review_kind = review.kind
            review_content = review.content
            source_message_ids = list(review.source_message_ids)
        selection = self._select_provider(scope_settings)
        embedding_generation = await self._generate_embeddings(
            selection,
            [review_content],
            operation="embed_knowledge",
            metadata={"scope": scope.key, "target_type": "knowledge_item", "kind": review_kind},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self._add_embedding_usage_in_session(
                    session,
                    selection=selection,
                    generation=embedding_generation,
                    texts_count=1,
                    operation="embed_knowledge",
                    metadata={
                        "scope": scope.key,
                        "target_type": "knowledge_item",
                        "kind": review_kind,
                    },
                )
            raise embedding_generation.error
        async with session_scope(self.session_factory) as session:
            repo = MemoryReviewRepository(session)
            review = await repo.get_pending(review_id)
            if review is None:
                raise ValueError(f"pending review #{review_id} was not found")
            await self._add_embedding_usage_in_session(
                session,
                selection=selection,
                generation=embedding_generation,
                texts_count=1,
                operation="embed_knowledge",
                metadata={"scope": scope.key, "target_type": "knowledge_item", "kind": review_kind},
            )
            item = await KnowledgeRepository(session).add(
                scope=scope,
                kind=review_kind,
                content=review_content,
                source_message_ids=source_message_ids,
            )
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item.id,
                scope=scope,
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                vector=embedding_generation.vectors[0],
            )
            await repo.set_status(review, status="approved", reviewed_by=reviewed_by)
            await EventRepository(session).add(
                event_type="memory_review_approved",
                scope=scope,
                user_id=reviewed_by,
                payload={"review_id": review_id, "knowledge_item_id": item.id},
            )
            item_id = item.id
        await self.compact_memory(dirty_scopes={scope})
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
        await self.rebuild_daily_projection()

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
            dirty_scopes = {
                row.scope
                for row in await KnowledgeRepository(session).scope_counts()
                if scope.contains(row.scope)
            }
            messages = await MessageRepository(session).delete_scope(scope)
            items = await KnowledgeRepository(session).delete_scope(scope)
            embeddings = await EmbeddingRepository(session).delete_scope(scope)
            await EventRepository(session).add(
                event_type="forget",
                scope=scope,
                user_id=user_id,
                payload={"messages": messages, "knowledge_items": items, "embeddings": embeddings},
            )
        await self.compact_memory(dirty_scopes=dirty_scopes)
        detail = f"{messages} messages, {items} knowledge items, {embeddings} embeddings."
        return (True, f"Forgot scope {scope.key}: {detail}")

    async def _claim_summary_batch(
        self,
        scope: Scope,
        *,
        template_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
        now: datetime | None = None,
        created_before: datetime | None = None,
    ) -> SummaryClaim | None:
        claim_now = now or utc_now()
        stale_before = claim_now - timedelta(seconds=SUMMARY_CLAIM_TIMEOUT_SECONDS)
        clean_template_name = template_name or "general"
        request = ai_options or AIRequestOptions()
        token = token_urlsafe(24)
        async with session_scope(self.session_factory) as session:
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self._default_scope_settings()
            )
            template_instruction = (
                request.summary_template_override
                or await self._summary_template_prompt_in_session(session, clean_template_name)
            )
            template_version = (
                PROMPT_VERSION if request.summary_template_override else SUMMARY_BASE_PROMPT.version
            )
            message_repo = MessageRepository(session)
            stale_ids = await message_repo.stale_summary_claim_ids(
                scope, stale_before=stale_before, created_before=created_before
            )
            pending = await message_repo.claim_for_summary(
                scope,
                limit=scope_settings.summary_batch_size,
                token=token,
                now=claim_now,
                stale_before=stale_before,
                created_before=created_before,
            )
            if not pending:
                return None
            recovered_stale_claims = sum(message.id in stale_ids for message in pending)
            if recovered_stale_claims:
                await EventRepository(session).add(
                    event_type="summary_stale_claims_recovered",
                    scope=scope,
                    payload={
                        "recovered_claims": recovered_stale_claims,
                        "claim_timeout_seconds": SUMMARY_CLAIM_TIMEOUT_SECONDS,
                    },
                )
            return SummaryClaim(
                token=token,
                scope=scope,
                message_ids=tuple(message.id for message in pending),
                source_telegram_ids=tuple(message.telegram_message_id for message in pending),
                normalized_texts=tuple(message.normalized_text for message in pending),
                scope_settings=scope_settings,
                template_name=clean_template_name,
                template_instruction=template_instruction,
                template_version=template_version,
                recovered_stale_claims=recovered_stale_claims,
            )

    async def _process_summary_batch(
        self,
        scope: Scope,
        *,
        template_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
        now: datetime | None = None,
        created_before: datetime | None = None,
    ) -> SummaryBatchOutcome:
        claim = await self._claim_summary_batch(
            scope,
            template_name=template_name,
            ai_options=ai_options,
            now=now,
            created_before=created_before,
        )
        if claim is None:
            return SummaryBatchOutcome(0, 0, False)
        generation = await self._summarize_claim(claim, ai_options=ai_options)
        embedding_generation: EmbeddingGenerationResult | None = None
        if generation.content is not None and not claim.scope_settings.review_before_compact:
            embedding_generation = await self._generate_embeddings(
                generation.selection,
                [generation.content],
                operation="embed_knowledge",
                metadata={
                    "scope": claim.scope.key,
                    "target_type": "knowledge_item",
                    "kind": "daily_summary",
                },
            )
        outcome = await self._finalize_summary_claim(
            claim, generation, embedding_generation=embedding_generation
        )
        return SummaryBatchOutcome(
            claimed_messages=len(claim.message_ids),
            completed_messages=outcome.completed_messages,
            needs_compact=outcome.needs_compact,
        )

    async def _finalize_summary_claim(
        self,
        claim: SummaryClaim,
        generation: SummaryGenerationResult,
        *,
        embedding_generation: EmbeddingGenerationResult | None,
    ) -> PendingSummaryOutcome:
        embedding_failure = (
            embedding_generation.failure if embedding_generation is not None else None
        )
        if generation.content is None or embedding_failure is not None:
            if generation.content is None:
                error_type = generation.error_type
            else:
                assert embedding_failure is not None
                error_type = embedding_failure.error_type
            async with session_scope(self.session_factory) as session:
                await MessageRepository(session).release_summary_claim(claim.token)
                await EventRepository(session).add(
                    event_type="summary_failed",
                    scope=claim.scope,
                    payload={
                        "source_message_ids": list(claim.source_telegram_ids),
                        "provider": generation.selection.chat_provider.name,
                        "model": generation.selection.chat_model,
                        "operation": "summarize",
                        "exception_type": error_type,
                    },
                )
            async with session_scope(self.session_factory) as session:
                await self._add_summary_usage_in_session(session, claim, generation)
                if embedding_generation is not None:
                    await self._add_embedding_usage_in_session(
                        session,
                        selection=generation.selection,
                        generation=embedding_generation,
                        texts_count=1,
                        operation="embed_knowledge",
                        metadata={
                            "scope": claim.scope.key,
                            "target_type": "knowledge_item",
                            "kind": "daily_summary",
                        },
                    )
            return PendingSummaryOutcome(0, False)
        async with session_scope(self.session_factory) as session:
            message_repo = MessageRepository(session)
            await self._add_summary_usage_in_session(session, claim, generation)
            if embedding_generation is not None:
                await self._add_embedding_usage_in_session(
                    session,
                    selection=generation.selection,
                    generation=embedding_generation,
                    texts_count=1,
                    operation="embed_knowledge",
                    metadata={
                        "scope": claim.scope.key,
                        "target_type": "knowledge_item",
                        "kind": "daily_summary",
                    },
                )
            owned = await message_repo.claimed_by_token(claim.token)
            if tuple(message.id for message in owned) != claim.message_ids:
                await message_repo.release_summary_claim(claim.token)
                await EventRepository(session).add(
                    event_type="summary_stale_result",
                    scope=claim.scope,
                    payload={
                        "expected_messages": len(claim.message_ids),
                        "owned_messages": len(owned),
                    },
                )
                return PendingSummaryOutcome(0, False)
            finalized = await message_repo.finalize_summary_claim(
                claim.token, list(claim.message_ids), summarized_at=utc_now()
            )
            if finalized != len(claim.message_ids):
                raise RuntimeError("summary claim ownership changed during finalization")
            source_ids = list(claim.source_telegram_ids)
            if claim.scope_settings.review_before_compact:
                review = await MemoryReviewRepository(session).add(
                    scope=claim.scope,
                    kind="daily_summary",
                    content=generation.content,
                    source_message_ids=source_ids,
                )
                await EventRepository(session).add(
                    event_type="memory_review_created",
                    scope=claim.scope,
                    payload={"review_id": review.id, "source_message_ids": source_ids},
                )
                return PendingSummaryOutcome(finalized, False)
            item = await KnowledgeRepository(session).add(
                scope=claim.scope,
                kind="daily_summary",
                content=generation.content,
                source_message_ids=source_ids,
                metadata_json={"template": claim.template_name},
            )
            assert embedding_generation is not None
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item.id,
                scope=claim.scope,
                provider=generation.selection.embedding_provider.name,
                model=generation.selection.embedding_model,
                vector=embedding_generation.vectors[0],
            )
            return PendingSummaryOutcome(finalized, True)

    async def _add_summary_usage_in_session(
        self, session: AsyncSession, claim: SummaryClaim, generation: SummaryGenerationResult
    ) -> None:
        metadata = dict(generation.telemetry.metadata)
        if (
            generation.telemetry.operation != "summarize"
            or metadata.get("scope") != claim.scope.key
            or metadata.get("summary_template") != claim.template_name
        ):
            raise RuntimeError("summary telemetry does not match its persistence boundary")
        await self._add_provider_usage_in_session(session, generation.telemetry)

    async def _summarize_claim(
        self, claim: SummaryClaim, *, ai_options: AIRequestOptions | None = None
    ) -> SummaryGenerationResult:
        return await self._generate_summary(
            scope=claim.scope,
            source_telegram_ids=claim.source_telegram_ids,
            normalized_texts=claim.normalized_texts,
            scope_settings=claim.scope_settings,
            template_name=claim.template_name,
            template_instruction=claim.template_instruction,
            template_version=claim.template_version,
            context_line="Scope summary from pending learned messages.",
            operation="summarize",
            usage_metadata={},
            ai_options=ai_options,
        )

    async def _generate_summary(
        self,
        *,
        scope: Scope,
        source_telegram_ids: tuple[int, ...],
        normalized_texts: tuple[str, ...],
        scope_settings: EffectiveScopeSettings,
        template_name: str,
        template_instruction: str,
        template_version: str,
        context_line: str,
        operation: str,
        usage_metadata: dict[str, Any],
        ai_options: AIRequestOptions | None = None,
    ) -> SummaryGenerationResult:
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
        for normalized_text, telegram_message_id in zip(
            normalized_texts, source_telegram_ids, strict=True
        ):
            prompt_lines.append(f"- {normalized_text} [source:{telegram_message_id}]")
        execution = await self._execute_chat_provider(
            selection,
            [ChatMessage(role="user", content="\n".join(prompt_lines))],
            operation=operation,
            metadata={
                "prompt_version": PROMPT_VERSION,
                "summary_template": template_name,
                "summary_template_version": template_version,
                "provider_profile": selection.profile.name,
                "chat_model": selection.chat_model,
                "embedding_model": selection.embedding_model,
                "reasoning_level": reasoning_level,
                "scope": scope.key,
                **usage_metadata,
            },
            temperature=0.1,
            max_tokens=900,
            reasoning_effort=reasoning_level,
        )
        failure = execution.failure
        telemetry = execution.telemetry
        if execution.result is None:
            content = None
        else:
            content = execution.result.content.strip() or None
            if content is None:
                failure = ProviderOperationError(
                    error_type="EmptySummaryResponse",
                    safe_summary="EmptySummaryResponse: provider returned empty content",
                )
                telemetry = provider_call_telemetry(
                    provider=telemetry.provider,
                    model=telemetry.model,
                    operation=telemetry.operation,
                    latency_ms=telemetry.latency_ms,
                    prompt_tokens=telemetry.prompt_tokens,
                    completion_tokens=telemetry.completion_tokens,
                    failure=failure,
                    metadata=dict(telemetry.metadata),
                )
        return SummaryGenerationResult(
            content=content,
            chat_result=execution.result,
            failure=failure,
            telemetry=telemetry,
            selection=selection,
            reasoning_level=reasoning_level,
            template_version=template_version,
        )

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

    async def _load_canonical_scope_input(
        self, session: AsyncSession, scope: Scope
    ) -> CanonicalScopeInput | None:
        items = await KnowledgeRepository(session).list_exact_scope(scope)
        if not items:
            return None
        scope_settings = await ScopeSettingsRepository(session).effective(
            scope, self._default_scope_settings()
        )
        selection = self._select_provider(scope_settings, summary=True)
        reasoning_level = self._reasoning_level(scope_settings)
        profile = selection.profile
        item_fingerprint = canonical_scope_fingerprint(
            scope.key,
            items,
            {
                "prompt_version": CANONICAL_PROMPT_VERSION,
                "provider": profile.provider,
                "provider_profile": profile.name,
                "chat_model": selection.chat_model,
                "reasoning_level": reasoning_level,
                "openai_base_url": profile.openai_base_url,
                "redact_before_ai": profile.redact_before_ai,
                "redaction_custom_patterns": list(profile.redaction_custom_patterns),
                "temperature": 0.1,
                "max_tokens": 1200,
            },
        )
        return CanonicalScopeInput(
            scope=scope,
            items=tuple(items),
            scope_settings=scope_settings,
            selection=selection,
            reasoning_level=reasoning_level,
            item_fingerprint=item_fingerprint,
        )

    async def _refresh_canonical_scope(self, scope: Scope, *, force: bool) -> CanonicalScopeRefresh:
        provider_calls = 0
        for _ in range(3):
            async with session_scope(self.session_factory) as session:
                snapshot_repo = CanonicalScopeSnapshotRepository(session)
                canonical_input = await self._load_canonical_scope_input(session, scope)
                existing = await snapshot_repo.get(scope)
                if canonical_input is None:
                    await snapshot_repo.delete_scope(scope)
                    return CanonicalScopeRefresh((), provider_calls, True)
                if (
                    not force
                    and existing is not None
                    and existing.item_fingerprint == canonical_input.item_fingerprint
                ):
                    return CanonicalScopeRefresh(existing.entries, provider_calls, True)
            entries, telemetry = await self._canonical_entries_for_scope(canonical_input)
            provider_calls += 1
            async with session_scope(self.session_factory) as session:
                await self._add_provider_usage_in_session(session, telemetry)
                if telemetry.error_type is not None:
                    await EventRepository(session).add(
                        event_type="canonical_snapshot_refresh_failed",
                        scope=scope,
                        payload={
                            "error_type": telemetry.error_type,
                            "item_fingerprint": canonical_input.item_fingerprint,
                        },
                    )
                    retained = await CanonicalScopeSnapshotRepository(session).get(scope)
                    if retained is not None:
                        return CanonicalScopeRefresh(retained.entries, provider_calls, False)
                    return CanonicalScopeRefresh(
                        tuple(fallback_entries(list(canonical_input.items))), provider_calls, False
                    )
                current_input = await self._load_canonical_scope_input(session, scope)
                snapshot_repo = CanonicalScopeSnapshotRepository(session)
                if current_input is None:
                    await snapshot_repo.delete_scope(scope)
                    return CanonicalScopeRefresh((), provider_calls, True)
                if current_input.item_fingerprint != canonical_input.item_fingerprint:
                    await EventRepository(session).add(
                        event_type="canonical_snapshot_stale_result",
                        scope=scope,
                        payload={
                            "discarded_fingerprint": canonical_input.item_fingerprint,
                            "current_fingerprint": current_input.item_fingerprint,
                        },
                    )
                    continue
                await snapshot_repo.upsert(
                    scope=scope,
                    item_fingerprint=canonical_input.item_fingerprint,
                    entries=entries,
                    provider=telemetry.provider,
                    provider_profile=canonical_input.selection.profile.name,
                    model=canonical_input.selection.chat_model,
                    reasoning_level=canonical_input.reasoning_level,
                    prompt_version=CANONICAL_PROMPT_VERSION,
                )
                return CanonicalScopeRefresh(tuple(entries), provider_calls, True)
        async with session_scope(self.session_factory) as session:
            current_input = await self._load_canonical_scope_input(session, scope)
            existing = await CanonicalScopeSnapshotRepository(session).get(scope)
            await EventRepository(session).add(
                event_type="canonical_snapshot_retry_exhausted",
                scope=scope,
                payload={"attempts": provider_calls},
            )
        if existing is not None:
            return CanonicalScopeRefresh(existing.entries, provider_calls, False)
        if current_input is None:
            return CanonicalScopeRefresh((), provider_calls, False)
        return CanonicalScopeRefresh(
            tuple(fallback_entries(list(current_input.items))), provider_calls, False
        )

    async def _canonical_entries_for_scope(
        self, canonical_input: CanonicalScopeInput
    ) -> tuple[list[CanonicalMemoryEntry], ProviderCallTelemetry]:
        scope = canonical_input.scope
        items = list(canonical_input.items)
        selection = canonical_input.selection
        reasoning_level = canonical_input.reasoning_level
        prompt = canonical_prompt(scope.key, items)
        prompt = (
            f"Reasoning level setting: {reasoning_level}.\n"
            f"Selected chat model: {selection.chat_model}.\n{prompt}"
        )
        execution = await self._execute_chat_provider(
            selection,
            [ChatMessage(role="user", content=prompt)],
            operation="canonical_compact",
            metadata={
                "prompt_version": PROMPT_VERSION,
                "provider_profile": selection.profile.name,
                "chat_model": selection.chat_model,
                "embedding_model": selection.embedding_model,
                "reasoning_level": reasoning_level,
                "scope": scope.key,
            },
            temperature=0.1,
            max_tokens=1200,
            reasoning_effort=reasoning_level,
        )
        telemetry = execution.telemetry
        if execution.result is None:
            entries = fallback_entries(items)
        else:
            try:
                parsed = parse_canonical_response(
                    execution.result.content, {int(item.id): item for item in items}
                )
            except Exception as error:
                failure = ProviderOperationError.from_exception(error)
                telemetry = provider_call_telemetry(
                    provider=telemetry.provider,
                    model=telemetry.model,
                    operation=telemetry.operation,
                    latency_ms=telemetry.latency_ms,
                    prompt_tokens=telemetry.prompt_tokens,
                    completion_tokens=telemetry.completion_tokens,
                    failure=failure,
                    metadata=dict(telemetry.metadata),
                )
                entries = fallback_entries(items)
            else:
                entries = parsed or fallback_entries(items)
        return entries, telemetry

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

    async def _execute_chat_provider(
        self,
        selection: ProviderSelection,
        messages: list[ChatMessage],
        *,
        operation: str,
        metadata: dict[str, Any],
        temperature: float,
        max_tokens: int,
        reasoning_effort: str | None,
    ) -> ChatProviderExecution:
        return await self._provider_operations.execute_chat(
            selection,
            messages,
            operation=operation,
            metadata=metadata,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

    async def _generate_embeddings(
        self,
        selection: ProviderSelection,
        texts: list[str],
        *,
        operation: str,
        metadata: dict[str, Any],
    ) -> EmbeddingGenerationResult:
        return await self._provider_operations.generate_embeddings(
            selection, texts, operation=operation, metadata=metadata
        )

    async def _add_embedding_usage_in_session(
        self,
        session: AsyncSession,
        *,
        selection: ProviderSelection,
        generation: EmbeddingGenerationResult,
        texts_count: int,
        operation: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._provider_operations.add_embedding_usage(
            session,
            selection=selection,
            generation=generation,
            texts_count=texts_count,
            operation=operation,
            metadata=metadata,
        )

    async def _add_provider_usage_in_session(
        self, session: AsyncSession, telemetry: ProviderCallTelemetry
    ) -> None:
        await self._provider_operations.add_usage(session, telemetry)

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
        "candidate_limit": "retrieval_candidate_limit",
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
