from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.factory import ProviderRegistry, ProviderSelection
from pzp_ai_bot.ai.protocols import ChatMessage, ChatProvider, EmbeddingProvider
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.repositories import (
    AIUsageStats,
    EffectiveScopeSettings,
    EmbeddingRepository,
    EventRepository,
    KnowledgeRepository,
    MemoryProfileView,
    MemoryReviewRepository,
    MessageRepository,
    PolicyRepository,
    ScopeMetadataRepository,
    ScopeSettingsRepository,
)
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.canonical import CanonicalMemoryEntry
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
from pzp_ai_bot.memory.mutation_service import MemoryMutationService
from pzp_ai_bot.memory.mutation_service import memory_item_preview as memory_item_preview
from pzp_ai_bot.memory.mutation_service import memory_note_preview as memory_note_preview
from pzp_ai_bot.memory.mutation_service import normalize_tags as normalize_tags
from pzp_ai_bot.memory.mutation_service import note_metadata as note_metadata
from pzp_ai_bot.memory.policy import PolicyService
from pzp_ai_bot.memory.prompts import PromptTemplate
from pzp_ai_bot.memory.provider_operations import ProviderOperations
from pzp_ai_bot.memory.provider_operations import provider_call_telemetry as provider_call_telemetry
from pzp_ai_bot.memory.provider_operations import telemetry_scope as telemetry_scope
from pzp_ai_bot.memory.retrieval import SearchResult
from pzp_ai_bot.memory.retrieval_service import MemoryRetrievalService
from pzp_ai_bot.memory.retrieval_service import append_answer_sources as append_answer_sources
from pzp_ai_bot.memory.retrieval_service import compact_answer_sources as compact_answer_sources
from pzp_ai_bot.memory.retrieval_service import dedupe_scope_list as dedupe_scope_list
from pzp_ai_bot.memory.retrieval_service import fallback_answer as fallback_answer
from pzp_ai_bot.memory.retrieval_service import format_results as format_results
from pzp_ai_bot.memory.retrieval_service import search_result_rank as search_result_rank
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.settings_profiles import (
    SAFE_RUNTIME_SETTING_TYPES,
    MemorySettingsService,
    validate_scope_settings,
)
from pzp_ai_bot.memory.summary_service import (
    SUMMARY_CLAIM_TIMEOUT_SECONDS as SUMMARY_CLAIM_TIMEOUT_SECONDS,
)
from pzp_ai_bot.memory.summary_service import MemorySummaryService
from pzp_ai_bot.memory.transfer import (
    MemoryExportChunk,
    MemoryExportFormat,
    MemoryTransferService,
    TelegramDesktopImportResult,
    normalize_text,
)
from pzp_ai_bot.memory.transfer import format_item_metadata as format_item_metadata
from pzp_ai_bot.memory.transfer import parse_memory_export_format as parse_memory_export_format


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
        self._provider_operations = ProviderOperations()
        self._settings_profiles = MemorySettingsService(
            settings=self.settings, session_factory=self.session_factory
        )
        self._retrieval_service = MemoryRetrievalService(
            settings=self.settings,
            session_factory=self.session_factory,
            settings_profiles=self._settings_profiles,
            select_provider=self._select_provider,
            reasoning_level=self._reasoning_level,
            provider_operations=self._provider_operations,
        )
        self._mutation_service = MemoryMutationService(
            session_factory=self.session_factory,
            settings_profiles=self._settings_profiles,
            select_provider=self._select_provider,
            provider_operations=self._provider_operations,
            compact_memory=self.compact_memory,
            rebuild_daily_projection=self.rebuild_daily_projection,
        )
        self._summary_service = MemorySummaryService(
            session_factory=self.session_factory,
            markdown=self.markdown,
            settings_profiles=self._settings_profiles,
            select_provider=self._select_provider,
            reasoning_level=self._reasoning_level,
            provider_operations=self._provider_operations,
        )
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
        await self._settings_profiles.set_scope_metadata(
            scope, chat_title=chat_title, topic_title=topic_title, chat_username=chat_username
        )

    async def get_scope_settings(self, scope: Scope) -> EffectiveScopeSettings:
        return await self._settings_profiles.get_scope_settings(scope)

    async def set_scope_settings(
        self,
        scope: Scope,
        updates: dict[str, int | str | bool | None],
        *,
        updated_by: int | None = None,
    ) -> EffectiveScopeSettings:
        return await self._settings_profiles.set_scope_settings(
            scope, updates, updated_by=updated_by
        )

    def settings_token_hash(self, scope: Scope, user_id: int, token: str) -> str:
        return self._settings_profiles.settings_token_hash(scope, user_id, token)

    async def preview_scope_settings(
        self, scope: Scope, updates: dict[str, int | str | bool | None], *, user_id: int
    ) -> SettingsChangePreview:
        return await self._settings_profiles.preview_scope_settings(scope, updates, user_id=user_id)

    async def confirm_scope_settings(
        self, scope: Scope, *, user_id: int, confirm: str
    ) -> SettingsChangePreview:
        return await self._settings_profiles.confirm_scope_settings(
            scope, user_id=user_id, confirm=confirm
        )

    async def refresh_runtime_settings(self) -> dict[str, Any]:
        return await self._settings_profiles.refresh_runtime_settings()

    async def set_runtime_settings(
        self, updates: dict[str, Any], *, updated_by: int | None = None
    ) -> list[RuntimeSettingPreview]:
        return await self._settings_profiles.set_runtime_settings(updates, updated_by=updated_by)

    async def list_runtime_settings(self) -> list[RuntimeSettingPreview]:
        return await self._settings_profiles.list_runtime_settings()

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
        return self._settings_profiles.runtime_default(key)

    def runtime_setting(self, key: str) -> Any:
        return self._settings_profiles.runtime_setting(key)

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
        return await self._summary_service.summarize_now(
            scope, template_name=template_name, ai_options=ai_options
        )

    async def summarize_idle(self, *, now: datetime | None = None) -> int:
        return await self._summary_service.summarize_idle(now=now)

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
        return await self._mutation_service.remember(
            scope, content, source_message_id, tags=tags, priority=priority, source=source
        )

    async def list_notes(
        self, scope: Scope | None = None, *, tag: str | None = None
    ) -> list[MemoryNotePreview]:
        return await self._mutation_service.list_notes(scope, tag=tag)

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
        return await self._mutation_service.edit_note(
            scope,
            item_id,
            content=content,
            tags=tags,
            priority=priority,
            source=source,
            clear_priority=clear_priority,
            clear_source=clear_source,
        )

    async def delete_note(self, scope: Scope, item_id: int) -> None:
        await self._mutation_service.delete_note(scope, item_id)

    async def ask(
        self,
        scope: Scope,
        question: str,
        *,
        top_k: int | None = None,
        profile_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> str:
        return await self._retrieval_service.ask(
            scope, question, top_k=top_k, profile_name=profile_name, ai_options=ai_options
        )

    async def search(
        self,
        scope: Scope,
        query: str,
        *,
        top_k: int = 6,
        profile_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> list[SearchResult]:
        return await self._retrieval_service.search(
            scope, query, top_k=top_k, profile_name=profile_name, ai_options=ai_options
        )

    async def compact_memory(
        self, dirty_scopes: set[Scope] | None = None, *, force: bool = False
    ) -> Path:
        return await self._summary_service.compact_memory(dirty_scopes=dirty_scopes, force=force)

    async def rebuild_daily_projection(self) -> list[Path]:
        return await self._summary_service.rebuild_daily_projection()

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
        return await self._mutation_service.browse_items(scope, kind=kind, limit=limit)

    async def delete_memory_item(
        self, scope: Scope, item_id: int, *, deleted_by: int | None = None
    ) -> MemoryItemPreview:
        return await self._mutation_service.delete_memory_item(
            scope, item_id, deleted_by=deleted_by
        )

    async def refresh_memory_item(
        self, scope: Scope, item_id: int, *, mode: str = "embed", updated_by: int | None = None
    ) -> MemoryItemRefreshResult:
        return await self._summary_service.refresh_memory_item(
            scope, item_id, mode=mode, updated_by=updated_by
        )

    async def _add_refresh_summary_usage_in_session(
        self, session: AsyncSession, scope: Scope, item_id: int, generation: SummaryGenerationResult
    ) -> None:
        await self._summary_service.add_refresh_summary_usage_in_session(
            session, scope, item_id, generation
        )

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
        return await self._mutation_service.prune_retention(scope, now=now)

    async def set_memory_profile(
        self,
        name: str,
        source_scopes: list[Scope],
        *,
        description: str | None = None,
        updated_by: int | None = None,
    ) -> MemoryProfileView:
        return await self._settings_profiles.set_memory_profile(
            name, source_scopes, description=description, updated_by=updated_by
        )

    async def list_memory_profiles(self) -> list[MemoryProfileView]:
        return await self._settings_profiles.list_memory_profiles()

    async def allow_memory_profile(
        self,
        *,
        destination: Scope,
        profile_name: str,
        can_query: bool,
        updated_by: int | None = None,
    ) -> MemoryProfileView:
        return await self._settings_profiles.allow_memory_profile(
            destination=destination,
            profile_name=profile_name,
            can_query=can_query,
            updated_by=updated_by,
        )

    async def set_summary_template(
        self, name: str, prompt: str, *, updated_by: int | None = None
    ) -> SummaryTemplatePreview:
        return await self._settings_profiles.set_summary_template(
            name, prompt, updated_by=updated_by
        )

    async def list_summary_templates(self) -> list[SummaryTemplatePreview]:
        return await self._settings_profiles.list_summary_templates()

    async def set_system_prompt_template(
        self, name: str, prompt: str, *, updated_by: int | None = None
    ) -> SystemPromptTemplatePreview:
        return await self._settings_profiles.set_system_prompt_template(
            name, prompt, updated_by=updated_by
        )

    async def list_system_prompt_templates(self) -> list[SystemPromptTemplatePreview]:
        return await self._settings_profiles.list_system_prompt_templates()

    async def summarize_range(
        self,
        scope: Scope,
        *,
        start_message_id: int,
        end_message_id: int,
        template_name: str = "general",
        ai_options: AIRequestOptions | None = None,
    ) -> RangeSummaryResult:
        return await self._summary_service.summarize_range(
            scope,
            start_message_id=start_message_id,
            end_message_id=end_message_id,
            template_name=template_name,
            ai_options=ai_options,
        )

    async def _add_range_summary_usage_in_session(
        self,
        session: AsyncSession,
        scope: Scope,
        template_name: str,
        generation: SummaryGenerationResult,
    ) -> None:
        await self._summary_service.add_range_summary_usage_in_session(
            session, scope, template_name, generation
        )

    async def pending_reviews(self, scope: Scope | None = None) -> list[MemoryReviewPreview]:
        return await self._mutation_service.pending_reviews(scope)

    async def pending_review_scope(self, review_id: int) -> Scope:
        return await self._mutation_service.pending_review_scope(review_id)

    async def approve_review(self, review_id: int, *, reviewed_by: int | None = None) -> int:
        return await self._mutation_service.approve_review(review_id, reviewed_by=reviewed_by)

    async def reject_review(self, review_id: int, *, reviewed_by: int | None = None) -> None:
        await self._mutation_service.reject_review(review_id, reviewed_by=reviewed_by)

    def forget_token_hash(self, scope: Scope, user_id: int, token: str) -> str:
        return self._mutation_service.forget_token_hash(scope, user_id, token)

    async def forget(self, scope: Scope, user_id: int, confirm: str | None) -> tuple[bool, str]:
        return await self._mutation_service.forget(scope, user_id, confirm)

    async def _claim_summary_batch(
        self,
        scope: Scope,
        *,
        template_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
        now: datetime | None = None,
        created_before: datetime | None = None,
    ) -> SummaryClaim | None:
        return await self._summary_service.claim_summary_batch(
            scope,
            template_name=template_name,
            ai_options=ai_options,
            now=now,
            created_before=created_before,
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
        return await self._summary_service.process_summary_batch(
            scope,
            template_name=template_name,
            ai_options=ai_options,
            now=now,
            created_before=created_before,
        )

    async def _finalize_summary_claim(
        self,
        claim: SummaryClaim,
        generation: SummaryGenerationResult,
        *,
        embedding_generation: EmbeddingGenerationResult | None,
    ) -> PendingSummaryOutcome:
        return await self._summary_service.finalize_summary_claim(
            claim, generation, embedding_generation=embedding_generation
        )

    async def _add_summary_usage_in_session(
        self, session: AsyncSession, claim: SummaryClaim, generation: SummaryGenerationResult
    ) -> None:
        await self._summary_service.add_summary_usage_in_session(session, claim, generation)

    async def _summarize_claim(
        self, claim: SummaryClaim, *, ai_options: AIRequestOptions | None = None
    ) -> SummaryGenerationResult:
        return await self._summary_service.summarize_claim(claim, ai_options=ai_options)

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
        return await self._summary_service.generate_summary(
            scope=scope,
            source_telegram_ids=source_telegram_ids,
            normalized_texts=normalized_texts,
            scope_settings=scope_settings,
            template_name=template_name,
            template_instruction=template_instruction,
            template_version=template_version,
            context_line=context_line,
            operation=operation,
            usage_metadata=usage_metadata,
            ai_options=ai_options,
        )

    async def _summary_template_prompt_in_session(
        self, session: AsyncSession, template_name: str
    ) -> str:
        return await self._summary_service.summary_template_prompt_in_session(
            session, template_name
        )

    async def _retrieval_scopes_in_session(
        self, session: AsyncSession, destination: Scope, profile_name: str | None
    ) -> list[Scope]:
        return await self._retrieval_service.retrieval_scopes_in_session(
            session, destination, profile_name
        )

    async def _load_canonical_scope_input(
        self, session: AsyncSession, scope: Scope
    ) -> CanonicalScopeInput | None:
        return await self._summary_service.load_canonical_scope_input(session, scope)

    async def _refresh_canonical_scope(self, scope: Scope, *, force: bool) -> CanonicalScopeRefresh:
        return await self._summary_service.refresh_canonical_scope(scope, force=force)

    async def _canonical_entries_for_scope(
        self, canonical_input: CanonicalScopeInput
    ) -> tuple[list[CanonicalMemoryEntry], ProviderCallTelemetry]:
        return await self._summary_service.canonical_entries_for_scope(canonical_input)

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
        return await self._settings_profiles.system_prompt_template_in_session(session, name)

    async def _answer_system_prompt_in_session(
        self,
        session: AsyncSession,
        scope_settings: EffectiveScopeSettings,
        options: AIRequestOptions | None,
    ) -> PromptTemplate:
        return await self._settings_profiles.answer_system_prompt_in_session(
            session, scope_settings, options
        )

    def _default_scope_settings(self) -> EffectiveScopeSettings:
        return self._settings_profiles.default_scope_settings()

    def _embedding_model_name(self) -> str:
        return getattr(self.embedding_provider, "embedding_model", self.embedding_provider.model)


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


def scope_label(scope: Scope, metadata: Any) -> str:
    if metadata is None:
        return f"`{scope.key}`"
    parts = [value for value in (metadata.chat_title, metadata.topic_title) if value]
    label = " / ".join(parts) or scope.key
    if metadata.chat_username:
        return f"{label} (`{scope.key}`, @{metadata.chat_username.removeprefix('@')})"
    return f"{label} (`{scope.key}`)"
