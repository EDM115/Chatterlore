from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.factory import ProviderSelection
from pzp_ai_bot.db.models import KnowledgeItem
from pzp_ai_bot.db.repos.challenges import ForgetChallengeRepository
from pzp_ai_bot.db.repos.embeddings import EmbeddingRepository
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.knowledge import KnowledgeRepository
from pzp_ai_bot.db.repos.messages import MessageRepository
from pzp_ai_bot.db.repos.profiles import MemoryReviewRepository
from pzp_ai_bot.db.repos.settings import ScopeSettingsRepository
from pzp_ai_bot.db.repos.shared import EffectiveScopeSettings, utc_now
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.contracts import (
    AIRequestOptions,
    MemoryItemPreview,
    MemoryNotePreview,
    MemoryReviewPreview,
    MemorySourcePreview,
)
from pzp_ai_bot.memory.provider_operations import ProviderOperations
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.settings_profiles import MemorySettingsService


class ProviderSelector(Protocol):
    def __call__(
        self,
        scope_settings: EffectiveScopeSettings,
        options: AIRequestOptions | None = None,
        *,
        summary: bool = False,
    ) -> ProviderSelection: ...


CompactMemory = Callable[..., Awaitable[Path]]
RebuildDailyProjection = Callable[[], Awaitable[list[Path]]]


class MemoryMutationService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        settings_profiles: MemorySettingsService,
        select_provider: ProviderSelector,
        provider_operations: ProviderOperations,
        compact_memory: CompactMemory,
        rebuild_daily_projection: RebuildDailyProjection,
    ) -> None:
        self.session_factory = session_factory
        self.settings_profiles = settings_profiles
        self.select_provider = select_provider
        self.provider_operations = provider_operations
        self.compact_memory = compact_memory
        self.rebuild_daily_projection = rebuild_daily_projection

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
                scope, self.settings_profiles.default_scope_settings()
            )
        selection = self.select_provider(scope_settings)
        embedding_generation = await self.provider_operations.generate_embeddings(
            selection,
            [clean_content],
            operation="embed_knowledge",
            metadata={"scope": scope.key, "target_type": "knowledge_item", "kind": "manual_note"},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self.provider_operations.add_embedding_usage(
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
            await self.provider_operations.add_embedding_usage(
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
                item_scope, self.settings_profiles.default_scope_settings()
            )
        selection = self.select_provider(scope_settings)
        embedding_generation = await self.provider_operations.generate_embeddings(
            selection,
            [updated_content],
            operation="embed_knowledge",
            metadata={"scope": item_scope.key, "target_type": "knowledge_item"},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self.provider_operations.add_embedding_usage(
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
            await self.provider_operations.add_embedding_usage(
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

    async def browse_items(
        self, scope: Scope | None = None, *, kind: str | None = None, limit: int = 20
    ) -> list[MemoryItemPreview]:
        async with session_scope(self.session_factory) as session:
            rows = await KnowledgeRepository(session).list_recent(scope, kind=kind, limit=limit)
            return [memory_item_preview(row) for row in rows]

    async def browse_items_page(
        self,
        scope: Scope | None = None,
        *,
        kind: str | None = None,
        after_id: int = 0,
        limit: int = 20,
    ) -> list[MemoryItemPreview]:
        async with session_scope(self.session_factory) as session:
            rows = await KnowledgeRepository(session).list_page(
                scope, after_id=after_id, kind=kind, limit=limit
            )
            return [memory_item_preview(row) for row in rows]

    async def memory_item(self, item_id: int) -> MemoryItemPreview:
        async with session_scope(self.session_factory) as session:
            item = await KnowledgeRepository(session).get(item_id)
            if item is None:
                raise ValueError(f"memory item #{item_id} was not found")
            return memory_item_preview(item)

    async def memory_item_sources(
        self, item_id: int, *, accessed_by: int, limit: int = 100
    ) -> tuple[Scope, list[MemorySourcePreview]]:
        async with session_scope(self.session_factory) as session:
            item = await KnowledgeRepository(session).get(item_id)
            if item is None:
                raise ValueError(f"memory item #{item_id} was not found")
            item_scope = Scope(item.chat_id, item.message_thread_id)
            messages = await MessageRepository(session).by_telegram_ids_exact(
                item_scope, list(item.source_message_ids), limit=limit
            )
            previews = [
                MemorySourcePreview(
                    telegram_message_id=message.telegram_message_id,
                    user_id=message.user_id,
                    username=message.username,
                    text=message.text[:8_000],
                    created_at=message.created_at,
                    message_permalink=message.message_permalink,
                )
                for message in messages
            ]
            await EventRepository(session).add(
                event_type="control_plane.memory.raw_sources_accessed",
                scope=item_scope,
                user_id=accessed_by,
                payload={"knowledge_item_id": item_id, "source_count": len(previews)},
            )
            return item_scope, previews

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

    async def prune_retention(self, scope: Scope, *, now: datetime | None = None) -> dict[str, int]:
        current = now or utc_now()
        needs_compact = False
        dirty_scopes: set[Scope] = set()
        async with session_scope(self.session_factory) as session:
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self.settings_profiles.default_scope_settings()
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
                    created_at=item.created_at,
                )
                for item in items
            ]

    async def pending_reviews_page(
        self, scope: Scope | None = None, *, after_id: int = 0, limit: int = 50
    ) -> list[MemoryReviewPreview]:
        async with session_scope(self.session_factory) as session:
            items = await MemoryReviewRepository(session).pending_page(
                scope, after_id=after_id, limit=limit
            )
            return [
                MemoryReviewPreview(
                    id=item.id,
                    scope=Scope(item.chat_id, item.message_thread_id),
                    kind=item.kind,
                    content=item.content,
                    source_message_ids=list(item.source_message_ids),
                    created_at=item.created_at,
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
                scope, self.settings_profiles.default_scope_settings()
            )
            review_kind = review.kind
            review_content = review.content
            source_message_ids = list(review.source_message_ids)
        selection = self.select_provider(scope_settings)
        embedding_generation = await self.provider_operations.generate_embeddings(
            selection,
            [review_content],
            operation="embed_knowledge",
            metadata={"scope": scope.key, "target_type": "knowledge_item", "kind": review_kind},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self.provider_operations.add_embedding_usage(
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
            await self.provider_operations.add_embedding_usage(
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
                    seconds=int(
                        self.settings_profiles.runtime_setting("forget_confirm_ttl_seconds")
                    )
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
            result, dirty_scopes = await self._delete_scope_in_session(
                session, scope, user_id=user_id
            )
        await self.compact_memory(dirty_scopes=dirty_scopes)
        detail = (
            f"{result['messages']} messages, {result['knowledge_items']} knowledge items, "
            f"{result['embeddings']} embeddings."
        )
        return (True, f"Forgot scope {scope.key}: {detail}")

    async def forget_confirmed(self, scope: Scope, *, user_id: int) -> dict[str, int]:
        async with session_scope(self.session_factory) as session:
            result, dirty_scopes = await self._delete_scope_in_session(
                session, scope, user_id=user_id
            )
        await self.compact_memory(dirty_scopes=dirty_scopes)
        return result

    async def _delete_scope_in_session(
        self, session: AsyncSession, scope: Scope, *, user_id: int
    ) -> tuple[dict[str, int], set[Scope]]:
        dirty_scopes = {
            row.scope
            for row in await KnowledgeRepository(session).scope_counts()
            if scope.contains(row.scope)
        }
        result = {
            "messages": await MessageRepository(session).delete_scope(scope),
            "knowledge_items": await KnowledgeRepository(session).delete_scope(scope),
            "embeddings": await EmbeddingRepository(session).delete_scope(scope),
        }
        await EventRepository(session).add(
            event_type="forget", scope=scope, user_id=user_id, payload=result
        )
        return result, dirty_scopes


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
