from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.protocols import ChatMessage, ChatProvider, ChatResult, EmbeddingProvider
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import KnowledgeItem
from pzp_ai_bot.db.repositories import (
    EffectiveScopeSettings,
    EmbeddingRepository,
    EventRepository,
    KnowledgeRepository,
    MemoryReviewRepository,
    MessageRepository,
    PolicyRepository,
    ScopeMetadataRepository,
    ScopeSettingsRepository,
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
from pzp_ai_bot.memory.retrieval import Retriever, SearchResult
from pzp_ai_bot.memory.scope import Scope


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
class MemoryReviewPreview:
    id: int
    scope: Scope
    kind: str
    content: str
    source_message_ids: list[int]


class MemoryService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker,
        chat_provider: ChatProvider,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.chat_provider = chat_provider
        self.embedding_provider = embedding_provider
        self.markdown = MarkdownMemoryStore(settings.memory_dir)
        self._forget_tokens: dict[tuple[str, int], str] = {}

    async def set_learning(
        self, scope: Scope | None, enabled: bool, updated_by: int | None = None
    ) -> None:
        async with session_scope(self.session_factory) as session:
            policy = PolicyService(self.settings, PolicyRepository(session))
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
            policy = PolicyService(self.settings, PolicyRepository(session))
            await policy.set_user(scope, user_id, allowed, updated_by)

    async def set_allow_admins(
        self, scope: Scope | None, enabled: bool, updated_by: int | None = None
    ) -> None:
        async with session_scope(self.session_factory) as session:
            policy = PolicyService(self.settings, PolicyRepository(session))
            await policy.set_allow_admins(scope, enabled, updated_by)

    async def learn_status(self, scope: Scope) -> dict[str, bool | int | str | None]:
        async with session_scope(self.session_factory) as session:
            policy = PolicyService(self.settings, PolicyRepository(session))
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
        validate_scope_settings(updates)
        async with session_scope(self.session_factory) as session:
            repo = ScopeSettingsRepository(session)
            await repo.set_settings(scope, updates, updated_by)
            await EventRepository(session).add(
                event_type="scope_settings", scope=scope, user_id=updated_by, payload=dict(updates)
            )
            return await repo.effective(scope, self._default_scope_settings())

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
            policy = PolicyService(self.settings, PolicyRepository(session))
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
            vector = (await self.embedding_provider.embed([text]))[0]
            await EmbeddingRepository(session).add(
                target_type="message",
                target_id=message.id,
                scope=incoming.scope,
                provider=self.embedding_provider.name,
                model=self._embedding_model_name(),
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

    async def summarize_now(self, scope: Scope) -> int:
        async with session_scope(self.session_factory) as session:
            if scope.is_global:
                count, needs_compact = await self._summarize_pending_scopes_in_session(session)
            else:
                pending = await MessageRepository(session).pending_for_summary(scope)
                if not pending:
                    return 0
                scope_settings = await ScopeSettingsRepository(session).effective(
                    scope, self._default_scope_settings()
                )
                needs_compact = await self._summarize_pending_in_session(
                    session, scope, pending, scope_settings
                )
                count = len(pending)
        if needs_compact:
            await self.compact_memory()
        return count

    async def summarize_idle(self, *, now: datetime | None = None) -> int:
        cutoff = (now or utc_now()) - timedelta(seconds=self.settings.summary_idle_seconds)
        async with session_scope(self.session_factory) as session:
            count, needs_compact = await self._summarize_pending_scopes_in_session(
                session, idle_before=cutoff
            )
        if needs_compact:
            await self.compact_memory()
        return count

    async def remember(
        self, scope: Scope, content: str, source_message_id: int | None = None
    ) -> int:
        async with session_scope(self.session_factory) as session:
            item = await self._add_knowledge_in_session(
                session,
                scope=scope,
                kind="manual_note",
                content=content.strip(),
                source_message_ids=[] if source_message_id is None else [source_message_id],
                daily_kind="manual",
            )
            item_id = item.id
        await self.compact_memory()
        return item_id

    async def ask(self, scope: Scope, question: str, *, top_k: int | None = None) -> str:
        if top_k is None:
            top_k = (await self.get_scope_settings(scope)).retrieval_top_k
        results = await self.search(scope, question, top_k=top_k)
        context = format_results(results)
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "Answer from scoped memory only. If the context is insufficient, say so. "
                    "Include compact source references."
                ),
            ),
            ChatMessage(role="user", content=f"Question: {question}\n\nScoped memory:\n{context}"),
        ]
        started = time.perf_counter()
        error: str | None = None
        try:
            result = await self.chat_provider.generate(messages, temperature=0.1, max_tokens=700)
            content = result.content
        except Exception as exc:
            error = str(exc)
            content = fallback_answer(results)
            result = None
        async with session_scope(self.session_factory) as session:
            await EventRepository(session).add_ai_usage(
                provider=self.chat_provider.name,
                model=self.chat_provider.model,
                operation="ask",
                prompt_tokens=result.prompt_tokens if result else None,
                completion_tokens=result.completion_tokens if result else None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=error,
            )
        return content

    async def search(self, scope: Scope, query: str, *, top_k: int = 6) -> list[SearchResult]:
        async with session_scope(self.session_factory) as session:
            retriever = Retriever(
                embedder=self.embedding_provider,
                embedding_repo=EmbeddingRepository(session),
                knowledge_repo=KnowledgeRepository(session),
                message_repo=MessageRepository(session),
            )
            return await retriever.search(query, scope, top_k)

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
                entries, result, error, latency_ms = await self._canonical_entries_for_scope(
                    scope, scope_items
                )
                entries_by_scope[scope] = entries
                await event_repo.add_ai_usage(
                    provider=self.chat_provider.name,
                    model=self.chat_provider.model,
                    operation="canonical_compact",
                    prompt_tokens=result.prompt_tokens if result else None,
                    completion_tokens=result.completion_tokens if result else None,
                    latency_ms=latency_ms,
                    error=error,
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
                lines.append(f"- ({item.kind}, source: {sources}) {item.content.strip()}")
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

    def arm_forget(self, scope: Scope, user_id: int) -> str:
        token = token_urlsafe(8)
        self._forget_tokens[(scope.key, user_id)] = token
        return token

    async def forget(self, scope: Scope, user_id: int, confirm: str | None) -> tuple[bool, str]:
        expected = self._forget_tokens.get((scope.key, user_id))
        if expected is None or expected != confirm:
            token = self.arm_forget(scope, user_id)
            return False, f"Confirmation required. Re-run with confirm={token}"
        async with session_scope(self.session_factory) as session:
            messages = await MessageRepository(session).delete_scope(scope)
            items = await KnowledgeRepository(session).delete_scope(scope)
            embeddings = await EmbeddingRepository(session).delete_scope(scope)
            await EventRepository(session).add(
                event_type="forget",
                scope=scope,
                user_id=user_id,
                payload={"messages": messages, "knowledge_items": items, "embeddings": embeddings},
            )
        self._forget_tokens.pop((scope.key, user_id), None)
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
        self, session: AsyncSession, scope: Scope, pending, scope_settings: EffectiveScopeSettings
    ) -> bool:
        prompt_lines = [
            "Summarize durable group-chat memory.",
            "Keep stable facts, decisions, preferences, todos, and open questions.",
            "Do not dump raw chat. Preserve source references.",
            f"Preferred summary model setting: {scope_settings.summary_model}.",
            "",
        ]
        for message in pending:
            prompt_lines.append(
                f"- {message.normalized_text} [source:{message.telegram_message_id}]"
            )
        started = time.perf_counter()
        error: str | None = None
        try:
            result = await self.chat_provider.generate(
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
        source_ids = [message.telegram_message_id for message in pending]
        await MessageRepository(session).mark_summarized([message.id for message in pending])
        await EventRepository(session).add_ai_usage(
            provider=self.chat_provider.name,
            model=self.chat_provider.model,
            operation="summarize",
            prompt_tokens=result.prompt_tokens if result else None,
            completion_tokens=result.completion_tokens if result else None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=error,
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
            daily_kind="daily_summary",
        )
        return True

    async def _add_knowledge_in_session(
        self,
        session: AsyncSession,
        *,
        scope: Scope,
        kind: str,
        content: str,
        source_message_ids: list[int],
        daily_kind: str,
    ) -> KnowledgeItem:
        item = await KnowledgeRepository(session).add(
            scope=scope, kind=kind, content=content, source_message_ids=source_message_ids
        )
        vector = (await self.embedding_provider.embed([content]))[0]
        await EmbeddingRepository(session).add(
            target_type="knowledge_item",
            target_id=item.id,
            scope=scope,
            provider=self.embedding_provider.name,
            model=self._embedding_model_name(),
            vector=vector,
        )
        self.markdown.append_daily(
            scope=scope,
            content=f"- {content}" if "\n" not in content else content,
            source_message_ids=source_message_ids,
            kind=daily_kind,
        )
        return item

    async def _canonical_entries_for_scope(
        self, scope: Scope, items: list[KnowledgeItem]
    ) -> tuple[list[CanonicalMemoryEntry], ChatResult | None, str | None, int]:
        prompt = canonical_prompt(scope.key, items)
        started = time.perf_counter()
        error: str | None = None
        try:
            result = await self.chat_provider.generate(
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
        return entries, result, error, int((time.perf_counter() - started) * 1000)

    def _default_scope_settings(self) -> EffectiveScopeSettings:
        return EffectiveScopeSettings(
            retrieval_top_k=self.settings.retrieval_top_k,
            summary_batch_size=self.settings.summary_batch_size,
            summary_model=self.settings.chat_model,
            raw_message_retention_days=self.settings.raw_message_retention_days,
            knowledge_retention_days=self.settings.knowledge_retention_days,
            review_before_compact=self.settings.review_before_compact,
        )

    def _embedding_model_name(self) -> str:
        return getattr(self.embedding_provider, "embedding_model", self.embedding_provider.model)


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def validate_scope_settings(updates: dict[str, int | str | bool | None]) -> None:
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
    if "summary_model" in updates and updates["summary_model"] is not None:
        model = str(updates["summary_model"]).strip()
        if not model:
            raise ValueError("summary_model cannot be empty")
        updates["summary_model"] = model
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


def parse_scope_settings_args(raw: dict[str, str]) -> dict[str, int | str | bool | None]:
    updates: dict[str, int | str | bool | None] = {}
    aliases = {
        "top_k": "retrieval_top_k",
        "retrieval_top_k": "retrieval_top_k",
        "summary_batch_size": "summary_batch_size",
        "summary_model": "summary_model",
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
