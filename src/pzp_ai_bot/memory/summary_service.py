from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.factory import ProviderSelection
from pzp_ai_bot.ai.protocols import ChatMessage
from pzp_ai_bot.db.repos.canonical import CanonicalScopeSnapshotRepository
from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingRouteChanged
from pzp_ai_bot.db.repos.embeddings import EmbeddingRepository
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.knowledge import KnowledgeRepository
from pzp_ai_bot.db.repos.messages import MessageRepository
from pzp_ai_bot.db.repos.profiles import MemoryReviewRepository
from pzp_ai_bot.db.repos.settings import (
    ScopeMetadataRepository,
    ScopeSettingsRepository,
    SummaryTemplateRepository,
)
from pzp_ai_bot.db.repos.shared import EffectiveScopeSettings, utc_now
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
    CanonicalScopeInput,
    CanonicalScopeRefresh,
    EmbeddingGenerationResult,
    MemoryItemRefreshResult,
    PendingSummaryOutcome,
    ProviderCallTelemetry,
    ProviderOperationError,
    RangeSummaryResult,
    SummaryBatchOutcome,
    SummaryClaim,
    SummaryGenerationResult,
)
from pzp_ai_bot.memory.embedding_retry import record_discarded_embedding
from pzp_ai_bot.memory.markdown import MarkdownMemoryStore
from pzp_ai_bot.memory.prompts import CANONICAL_PROMPT_VERSION, PROMPT_VERSION, SUMMARY_BASE_PROMPT
from pzp_ai_bot.memory.provider_operations import ProviderOperations, provider_call_telemetry
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.settings_profiles import BUILTIN_SUMMARY_TEMPLATES, MemorySettingsService

SUMMARY_CLAIM_TIMEOUT_SECONDS = 15 * 60


class ProviderSelector(Protocol):
    def __call__(
        self,
        scope_settings: EffectiveScopeSettings,
        options: AIRequestOptions | None = None,
        *,
        summary: bool = False,
    ) -> ProviderSelection: ...


class ReasoningLevelResolver(Protocol):
    def __call__(
        self, scope_settings: EffectiveScopeSettings, options: AIRequestOptions | None = None
    ) -> str: ...


class MemorySummaryService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        markdown: MarkdownMemoryStore,
        settings_profiles: MemorySettingsService,
        select_provider: ProviderSelector,
        reasoning_level: ReasoningLevelResolver,
        provider_operations: ProviderOperations,
    ) -> None:
        self.session_factory = session_factory
        self.markdown = markdown
        self.settings_profiles = settings_profiles
        self.select_provider = select_provider
        self.reasoning_level = reasoning_level
        self.provider_operations = provider_operations
        self.scope_access_filter: Callable[[Scope], Awaitable[bool]] | None = None

    def set_scope_access_filter(
        self, scope_access_filter: Callable[[Scope], Awaitable[bool]] | None
    ) -> None:
        self.scope_access_filter = scope_access_filter

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
        if self.scope_access_filter is not None:
            scopes = [
                pending_scope
                for pending_scope in scopes
                if await self.scope_access_filter(pending_scope)
            ]
        count = 0
        dirty_scopes: set[Scope] = set()
        for pending_scope in scopes:
            while True:
                summary_outcome = await self.process_summary_batch(
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
        cutoff = claim_now - timedelta(
            seconds=int(self.settings_profiles.runtime_setting("summary_idle_seconds"))
        )
        async with session_scope(self.session_factory) as session:
            scopes = await MessageRepository(session).pending_scopes(idle_before=cutoff)
        if self.scope_access_filter is not None:
            scopes = [scope for scope in scopes if await self.scope_access_filter(scope)]
        count = 0
        dirty_scopes: set[Scope] = set()
        for scope in scopes:
            summary_outcome = await self.process_summary_batch(
                scope, now=claim_now, created_before=cutoff
            )
            count += summary_outcome.completed_messages
            if summary_outcome.needs_compact:
                dirty_scopes.add(scope)
        if dirty_scopes:
            await self.compact_memory(dirty_scopes=dirty_scopes)
        return count

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
            refreshes[scope] = await self.refresh_canonical_scope(scope, force=force)
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
                refreshes[scope] = await self.refresh_canonical_scope(scope, force=False)
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
                item_scope, self.settings_profiles.default_scope_settings()
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
                template_instruction = await self.summary_template_prompt_in_session(
                    session, template_name
                )
                summary_source_ids = tuple(message.telegram_message_id for message in messages)
                summary_texts = tuple(message.normalized_text for message in messages)
        generation: SummaryGenerationResult | None = None
        if clean_mode in {"summarize", "both"}:
            generation = await self.generate_summary(
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
                    await self.add_refresh_summary_usage_in_session(
                        session, item_scope, item_id, generation
                    )
                raise RuntimeError(f"summary generation failed: {generation.error_type}")
            refreshed_content = generation.content
        embedding_selection = self.select_provider(scope_settings)
        embedding_generation = await self.provider_operations.generate_embeddings(
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
                    await self.add_refresh_summary_usage_in_session(
                        session, item_scope, item_id, generation
                    )
                await self.provider_operations.add_embedding_usage(
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
                await self.add_refresh_summary_usage_in_session(
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
            await self.provider_operations.add_embedding_usage(
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
                **embedding_generation.persistence_fields(),
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

    async def add_refresh_summary_usage_in_session(
        self, session: AsyncSession, scope: Scope, item_id: int, generation: SummaryGenerationResult
    ) -> None:
        metadata = dict(generation.telemetry.metadata)
        if (
            generation.telemetry.operation != "memory_item_refresh_summary"
            or metadata.get("scope") != scope.key
            or metadata.get("knowledge_item_id") != item_id
        ):
            raise RuntimeError("refresh summary telemetry does not match its persistence boundary")
        await self.provider_operations.add_usage(session, generation.telemetry)

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
                scope, self.settings_profiles.default_scope_settings()
            )
            template_instruction = (
                request.summary_template_override
                or await self.summary_template_prompt_in_session(session, template_name)
            )
            template_version = (
                PROMPT_VERSION if request.summary_template_override else SUMMARY_BASE_PROMPT.version
            )
            source_ids = tuple(message.telegram_message_id for message in pending)
            message_ids = [message.id for message in pending]
            normalized_texts = tuple(message.normalized_text for message in pending)
            started_at = start.created_at
            ended_at = end.created_at
        generation = await self.generate_summary(
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
                await self.add_range_summary_usage_in_session(
                    session, scope, template_name, generation
                )
            raise RuntimeError(f"summary generation failed: {generation.error_type}")
        embedding_selection = self.select_provider(scope_settings)
        embedding_generation = await self.provider_operations.generate_embeddings(
            embedding_selection,
            [generation.content],
            operation="embed_knowledge",
            metadata={"scope": scope.key, "target_type": "knowledge_item", "kind": "range_summary"},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self.add_range_summary_usage_in_session(
                    session, scope, template_name, generation
                )
                await self.provider_operations.add_embedding_usage(
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
            await self.add_range_summary_usage_in_session(session, scope, template_name, generation)
            await self.provider_operations.add_embedding_usage(
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
                **embedding_generation.persistence_fields(),
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

    async def add_range_summary_usage_in_session(
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
        await self.provider_operations.add_usage(session, generation.telemetry)

    async def claim_summary_batch(
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
                scope, self.settings_profiles.default_scope_settings()
            )
            template_instruction = (
                request.summary_template_override
                or await self.summary_template_prompt_in_session(session, clean_template_name)
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

    async def process_summary_batch(
        self,
        scope: Scope,
        *,
        template_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
        now: datetime | None = None,
        created_before: datetime | None = None,
    ) -> SummaryBatchOutcome:
        claim = await self.claim_summary_batch(
            scope,
            template_name=template_name,
            ai_options=ai_options,
            now=now,
            created_before=created_before,
        )
        if claim is None:
            return SummaryBatchOutcome(0, 0, False)
        generation = await self.summarize_claim(claim, ai_options=ai_options)
        for attempt in range(3):
            embedding_generation: EmbeddingGenerationResult | None = None
            if generation.content is not None and not claim.scope_settings.review_before_compact:
                current_settings = await self.settings_profiles.get_scope_settings(scope)
                generation.embedding_selection = self.select_provider(current_settings)
                embedding_generation = await self.provider_operations.generate_embeddings(
                    generation.embedding_selection,
                    [generation.content],
                    operation="embed_knowledge",
                    metadata={
                        "scope": claim.scope.key,
                        "target_type": "knowledge_item",
                        "kind": "daily_summary",
                    },
                )
            try:
                outcome = await self.finalize_summary_claim(
                    claim, generation, embedding_generation=embedding_generation
                )
                break
            except EmbeddingRouteChanged as error:
                await record_discarded_embedding(
                    self.session_factory, self.provider_operations, error
                )
                if attempt == 2:
                    async with session_scope(self.session_factory) as session:
                        await MessageRepository(session).release_summary_claim(claim.token)
                    raise
        return SummaryBatchOutcome(
            claimed_messages=len(claim.message_ids),
            completed_messages=outcome.completed_messages,
            needs_compact=outcome.needs_compact,
        )

    async def finalize_summary_claim(
        self,
        claim: SummaryClaim,
        generation: SummaryGenerationResult,
        *,
        embedding_generation: EmbeddingGenerationResult | None,
    ) -> PendingSummaryOutcome:
        embedding_selection = generation.embedding_selection or generation.selection
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
                await self.add_summary_usage_in_session(session, claim, generation)
                if embedding_generation is not None:
                    await self.provider_operations.add_embedding_usage(
                        session,
                        selection=embedding_selection,
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
            await self.add_summary_usage_in_session(session, claim, generation)
            if embedding_generation is not None:
                await self.provider_operations.add_embedding_usage(
                    session,
                    selection=embedding_selection,
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
                provider=embedding_selection.embedding_provider.name,
                model=embedding_selection.embedding_model,
                vector=embedding_generation.vectors[0],
                **embedding_generation.persistence_fields(),
            )
            return PendingSummaryOutcome(finalized, True)

    async def add_summary_usage_in_session(
        self, session: AsyncSession, claim: SummaryClaim, generation: SummaryGenerationResult
    ) -> None:
        metadata = dict(generation.telemetry.metadata)
        if (
            generation.telemetry.operation != "summarize"
            or metadata.get("scope") != claim.scope.key
            or metadata.get("summary_template") != claim.template_name
        ):
            raise RuntimeError("summary telemetry does not match its persistence boundary")
        await self.provider_operations.add_usage(session, generation.telemetry)

    async def summarize_claim(
        self, claim: SummaryClaim, *, ai_options: AIRequestOptions | None = None
    ) -> SummaryGenerationResult:
        return await self.generate_summary(
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

    async def generate_summary(
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
        selection = self.select_provider(scope_settings, ai_options, summary=True)
        reasoning_level = self.reasoning_level(scope_settings, ai_options)
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
        execution = await self.provider_operations.execute_chat(
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

    async def summary_template_prompt_in_session(
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

    async def load_canonical_scope_input(
        self, session: AsyncSession, scope: Scope
    ) -> CanonicalScopeInput | None:
        items = await KnowledgeRepository(session).list_exact_scope(scope)
        if not items:
            return None
        scope_settings = await ScopeSettingsRepository(session).effective(
            scope, self.settings_profiles.default_scope_settings()
        )
        selection = self.select_provider(scope_settings, summary=True)
        reasoning_level = self.reasoning_level(scope_settings)
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

    async def refresh_canonical_scope(self, scope: Scope, *, force: bool) -> CanonicalScopeRefresh:
        provider_calls = 0
        for _ in range(3):
            async with session_scope(self.session_factory) as session:
                snapshot_repo = CanonicalScopeSnapshotRepository(session)
                canonical_input = await self.load_canonical_scope_input(session, scope)
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
            entries, telemetry = await self.canonical_entries_for_scope(canonical_input)
            provider_calls += 1
            async with session_scope(self.session_factory) as session:
                await self.provider_operations.add_usage(session, telemetry)
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
                current_input = await self.load_canonical_scope_input(session, scope)
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
            current_input = await self.load_canonical_scope_input(session, scope)
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

    async def canonical_entries_for_scope(
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
        execution = await self.provider_operations.execute_chat(
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
