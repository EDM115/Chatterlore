from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from secrets import token_urlsafe
from typing import Literal

from sqlalchemy.ext.asyncio import async_sessionmaker

from pzp_ai_bot.ai.protocols import ChatMessage, ChatProvider, EmbeddingProvider
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.repositories import (
    EmbeddingRepository,
    EventRepository,
    KnowledgeRepository,
    MessageRepository,
    PolicyRepository,
)
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.markdown import MarkdownMemoryStore
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


@dataclass(frozen=True, slots=True)
class IngestResult:
    learned: bool
    reason: str
    message_db_id: int | None = None
    summarized: bool = False


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

    async def learn_status(self, scope: Scope) -> dict[str, bool | str]:
        async with session_scope(self.session_factory) as session:
            policy = PolicyService(self.settings, PolicyRepository(session))
            return await policy.status(scope)

    async def ingest(self, incoming: IncomingMessage) -> IngestResult:
        text = normalize_text(incoming.text)
        async with session_scope(self.session_factory) as session:
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
            message = await message_repo.add(
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
            )
            if not decision.allowed:
                return IngestResult(False, decision.reason, message.id)
            vector = (await self.embedding_provider.embed([text]))[0]
            await EmbeddingRepository(session).add(
                target_type="message",
                target_id=message.id,
                scope=incoming.scope,
                provider=self.embedding_provider.name,
                model=getattr(
                    self.embedding_provider, "embedding_model", self.embedding_provider.model
                ),
                vector=vector,
            )
            pending = await message_repo.pending_for_summary(incoming.scope)
            summarized = False
            if len(pending) >= self.settings.summary_batch_size:
                await self._summarize_pending_in_session(session, incoming.scope, pending)
                summarized = True
            return IngestResult(True, decision.reason, message.id, summarized)

    async def summarize_now(self, scope: Scope) -> int:
        async with session_scope(self.session_factory) as session:
            pending = await MessageRepository(session).pending_for_summary(scope)
            if not pending:
                return 0
            await self._summarize_pending_in_session(session, scope, pending)
            return len(pending)

    async def remember(
        self, scope: Scope, content: str, source_message_id: int | None = None
    ) -> int:
        item_id: int
        async with session_scope(self.session_factory) as session:
            item = await KnowledgeRepository(session).add(
                scope=scope,
                kind="manual_note",
                content=content.strip(),
                source_message_ids=[] if source_message_id is None else [source_message_id],
            )
            vector = (await self.embedding_provider.embed([content]))[0]
            await EmbeddingRepository(session).add(
                target_type="knowledge_item",
                target_id=item.id,
                scope=scope,
                provider=self.embedding_provider.name,
                model=getattr(
                    self.embedding_provider, "embedding_model", self.embedding_provider.model
                ),
                vector=vector,
            )
            self.markdown.append_daily(
                scope=scope,
                content=f"- {content.strip()}",
                source_message_ids=item.source_message_ids,
                kind="manual",
            )
            item_id = item.id
        await self.compact_memory()
        return item_id

    async def ask(self, scope: Scope, question: str, *, top_k: int | None = None) -> str:
        results = await self.search(scope, question, top_k=top_k or self.settings.retrieval_top_k)
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
            items = await KnowledgeRepository(session).list_scope()
            path = self.markdown.compact_main(items)
            await EventRepository(session).add(
                event_type="compact_memory", payload={"path": str(path)}
            )
            return path

    async def export_memory(
        self, scope: Scope | None = None, fmt: Literal["md", "jsonl"] = "md"
    ) -> str:
        async with session_scope(self.session_factory) as session:
            items = await KnowledgeRepository(session).list_scope(scope)
            if fmt == "jsonl":
                lines = [
                    json.dumps(
                        {
                            "id": item.id,
                            "scope": Scope(item.chat_id, item.message_thread_id).key,
                            "kind": item.kind,
                            "content": item.content,
                            "source_message_ids": item.source_message_ids,
                            "created_at": item.created_at.isoformat(),
                        },
                        ensure_ascii=False,
                    )
                    for item in items
                ]
                return "\n".join(lines) + ("\n" if lines else "")
            if scope is None:
                return self.markdown.read_main()
            lines = [f"# Memory export for `{scope.key}`", ""]
            for item in items:
                sources = ", ".join(str(source) for source in item.source_message_ids) or "manual"
                lines.append(f"- ({item.kind}, sources: {sources}) {item.content.strip()}")
            return "\n".join(lines).rstrip() + "\n"

    async def import_jsonl(self, path: Path, scope: Scope) -> int:
        count = 0
        async with session_scope(self.session_factory) as session:
            knowledge_repo = KnowledgeRepository(session)
            embedding_repo = EmbeddingRepository(session)
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    content = str(payload.get("content") or payload.get("text") or "").strip()
                    if not content:
                        continue
                    source_ids = [int(value) for value in payload.get("source_message_ids", [])]
                    item = await knowledge_repo.add(
                        scope=scope,
                        kind=str(payload.get("kind") or "imported_note"),
                        content=content,
                        source_message_ids=source_ids,
                    )
                    vector = (await self.embedding_provider.embed([content]))[0]
                    await embedding_repo.add(
                        target_type="knowledge_item",
                        target_id=item.id,
                        scope=scope,
                        provider=self.embedding_provider.name,
                        model=getattr(
                            self.embedding_provider,
                            "embedding_model",
                            self.embedding_provider.model,
                        ),
                        vector=vector,
                    )
                    self.markdown.append_daily(
                        scope=scope,
                        content=f"- {content}",
                        source_message_ids=source_ids,
                        kind="import",
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
            return {
                **message_counts,
                "knowledge_items": knowledge,
                "embeddings": embeddings,
                "events": events,
            }

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

    async def _summarize_pending_in_session(self, session, scope: Scope, pending) -> None:
        prompt_lines = [
            "Summarize durable group-chat memory.",
            "Keep stable facts, decisions, preferences, todos, and open questions.",
            "Do not dump raw chat. Preserve source references.",
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
        item = await KnowledgeRepository(session).add(
            scope=scope, kind="daily_summary", content=content, source_message_ids=source_ids
        )
        vector = (await self.embedding_provider.embed([content]))[0]
        await EmbeddingRepository(session).add(
            target_type="knowledge_item",
            target_id=item.id,
            scope=scope,
            provider=self.embedding_provider.name,
            model=getattr(
                self.embedding_provider, "embedding_model", self.embedding_provider.model
            ),
            vector=vector,
        )
        await MessageRepository(session).mark_summarized([message.id for message in pending])
        self.markdown.append_daily(
            scope=scope, content=content, source_message_ids=source_ids, kind="daily_summary"
        )
        await EventRepository(session).add_ai_usage(
            provider=self.chat_provider.name,
            model=self.chat_provider.model,
            operation="summarize",
            prompt_tokens=result.prompt_tokens if result else None,
            completion_tokens=result.completion_tokens if result else None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=error,
        )
        items = await KnowledgeRepository(session).list_scope()
        self.markdown.compact_main(items)


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


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


def fallback_answer(results: list[SearchResult]) -> str:
    if not results:
        return "I do not have scoped memory for that yet."
    return "From scoped memory:\n" + format_results(results)
