from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from pzp_ai_bot.ai.factory import ProviderSelection
from pzp_ai_bot.ai.fake import FakeProvider
from pzp_ai_bot.ai.protocols import ChatResult
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import AIUsage, CanonicalScopeSnapshot, KnowledgeItem
from pzp_ai_bot.db.repositories import (
    CanonicalScopeSnapshotRepository,
    EventRepository,
    KnowledgeRepository,
    MessageRepository,
    utc_now,
)
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.canonical import CanonicalMemoryEntry
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import IncomingMessage


def daily_projection_text(memory_dir: Path) -> str:
    daily_dir = memory_dir / "daily"
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(daily_dir.glob("*/*.md")))


def incoming(
    scope: Scope, text: str, *, message_id: int, created_at: datetime | None = None
) -> IncomingMessage:
    return IncomingMessage(
        telegram_message_id=message_id,
        scope=scope,
        user_id=222,
        username="dana",
        text=text,
        created_at=created_at or datetime(2026, 6, 23, tzinfo=UTC),
        chat_title="Build Chat",
        chat_username="buildchat",
        topic_title="Roadmap" if scope.thread_id else None,
        message_permalink=f"https://t.me/buildchat/{message_id}",
    )


class RetrySummaryProvider:
    name = "retry-summary"
    model = "retry-summary-chat"
    embedding_model = "retry-summary-embedding"
    dimensions = 3

    def __init__(self, *, failure_mode: str, raw_sentinel: str) -> None:
        self.failure_mode = failure_mode
        self.raw_sentinel = raw_sentinel
        self.generate_calls = 0

    async def generate(
        self,
        messages,
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        self.generate_calls += 1
        if self.generate_calls == 1:
            if self.failure_mode == "exception":
                raise RuntimeError(f"provider request failed around {self.raw_sentinel}")
            return ChatResult(content=" \n", model=self.model, prompt_tokens=1, completion_tokens=0)
        return ChatResult(
            content="Decision: retry-safe summary completed. [source:401] [source:402]",
            model=self.model,
            prompt_tokens=10,
            completion_tokens=6,
        )

    async def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _text in texts]


class BatchSummaryProvider:
    name = "batch-summary"
    model = "batch-summary-chat"
    embedding_model = "batch-summary-embedding"
    dimensions = 3

    def __init__(self, *, barrier_size: int = 0, block: bool = False) -> None:
        self.barrier_size = barrier_size
        self.summary_source_counts: list[int] = []
        self.summary_started = asyncio.Event()
        self.summary_release = asyncio.Event()
        if not block:
            self.summary_release.set()

    async def generate(
        self,
        messages,
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        prompt = "\n".join(message.content for message in messages)
        if "Scope summary from pending learned messages." in prompt:
            self.summary_source_counts.append(prompt.count("[source:"))
            self.summary_started.set()
            if self.barrier_size and len(self.summary_source_counts) >= self.barrier_size:
                self.summary_release.set()
            await self.summary_release.wait()
            return ChatResult(
                content=f"Fact: bounded summary batch {len(self.summary_source_counts)}.",
                model=self.model,
                prompt_tokens=10,
                completion_tokens=5,
            )
        return ChatResult(content="[]", model=self.model, prompt_tokens=2, completion_tokens=1)

    async def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _text in texts]


class FailingEmbeddingProvider(BatchSummaryProvider):
    async def embed(self, texts):
        raise RuntimeError("embedding provider unavailable")


class CanonicalRecordingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.canonical_prompts: list[str] = []
        self.canonical_started = asyncio.Event()
        self.canonical_release = asyncio.Event()
        self.canonical_release.set()
        self.fail_next_canonical: str | None = None
        self.block_next_canonical = False

    async def generate(
        self,
        messages,
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        prompt = "\n".join(message.content for message in messages)
        if "Canonicalize durable group-chat memory" in prompt:
            self.canonical_prompts.append(prompt)
            self.canonical_started.set()
            if self.block_next_canonical:
                self.block_next_canonical = False
                await self.canonical_release.wait()
            if self.fail_next_canonical is not None:
                sentinel = self.fail_next_canonical
                self.fail_next_canonical = None
                raise RuntimeError(f"canonical provider failed around {sentinel}")
        return await super().generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )


class RetrySummaryRegistry:
    def __init__(
        self, settings: Settings, provider: RetrySummaryProvider | BatchSummaryProvider
    ) -> None:
        self.settings = settings
        self.provider = provider

    def select(
        self,
        profile_name: str | None = None,
        *,
        chat_model: str | None = None,
        embedding_model: str | None = None,
    ) -> ProviderSelection:
        profile = self.settings.provider_profile_by_name(profile_name)
        return ProviderSelection(
            profile=profile,
            chat_provider=self.provider,
            embedding_provider=self.provider,
            chat_model=chat_model or self.provider.model,
            embedding_model=embedding_model or self.provider.embedding_model,
        )


async def seed_pending_messages(context, scope: Scope, count: int) -> None:
    async with session_scope(context.session_factory) as session:
        repository = MessageRepository(session)
        for message_id in range(1, count + 1):
            await repository.add(
                telegram_message_id=message_id,
                scope=scope,
                user_id=222,
                username="batch",
                text=f"Pending batch source {message_id}",
                normalized_text=f"pending batch source {message_id}",
                learned=True,
                skip_reason=None,
                reply_to_message_id=None,
                created_at=datetime(2026, 7, 29, 12, message_id, tzinfo=UTC),
            )


async def test_canonical_compaction_groups_deduplicates_and_links_sources(context) -> None:
    scope = Scope(-1001234567890, 7)
    await context.memory.set_scope_metadata(
        scope, chat_title="Build Chat", topic_title="Roadmap", chat_username="buildchat"
    )

    await context.memory.remember(
        scope, "Fact: Release codename is Bluebird.", source_message_id=10
    )
    await context.memory.remember(
        scope, "Canonical note: release codename is Bluebird", source_message_id=11
    )
    await context.memory.remember(
        scope, "Decision: Dana owns release coordination.", source_message_id=12
    )

    memory = (context.settings.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "## Build Chat / Roadmap" in memory
    assert "### Facts" in memory
    assert "### Decisions" in memory
    assert memory.lower().count("release codename is bluebird") == 1
    assert "[#10](https://t.me/buildchat/10)" in memory
    assert "[#11](https://t.me/buildchat/11)" in memory
    assert "[#12](https://t.me/buildchat/12)" in memory

    exported = await context.memory.export_memory(scope, fmt="jsonl")
    rows = [json.loads(line) for line in exported.splitlines()]
    assert rows[0]["scope_label"].startswith("Build Chat / Roadmap")
    assert rows[0]["source_references"][0]["url"] == "https://t.me/buildchat/10"


async def test_scope_settings_override_batch_status_and_review_workflow(context) -> None:
    scope = Scope(-100200, 5)
    await context.memory.set_learning(scope, True, updated_by=111)
    settings = await context.memory.set_scope_settings(
        scope,
        {
            "retrieval_top_k": 1,
            "summary_batch_size": 3,
            "summary_model": "summary-special",
            "review_before_compact": True,
        },
        updated_by=111,
    )
    assert settings.retrieval_top_k == 1
    assert settings.summary_batch_size == 3
    assert settings.summary_model == "summary-special"
    assert settings.review_before_compact is True

    status = await context.memory.learn_status(scope)
    assert status["summary_batch_size"] == 3
    assert status["summary_model"] == "summary-special"
    assert status["review_before_compact"] is True

    await context.memory.ingest(incoming(scope, "Project Quartz uses SQLite.", message_id=1))
    second = await context.memory.ingest(
        incoming(scope, "Todo: Dana should draft the runbook.", message_id=2)
    )
    assert second.summarized is False
    assert (await context.memory.stats())["pending_reviews"] == 0

    third = await context.memory.ingest(
        incoming(scope, "Open question: who owns beta invites?", message_id=3)
    )
    assert third.summarized is True
    stats = await context.memory.stats()
    assert stats["knowledge_items"] == 0
    assert stats["pending_reviews"] == 1

    reviews = await context.memory.pending_reviews(scope)
    assert len(reviews) == 1
    assert "Project Quartz uses SQLite" in reviews[0].content
    assert await context.memory.pending_review_scope(reviews[0].id) == scope
    assert "Project Quartz uses SQLite" not in daily_projection_text(context.settings.memory_dir)
    assert "Project Quartz uses SQLite" not in (
        context.settings.memory_dir / "MEMORY.md"
    ).read_text(encoding="utf-8")

    item_id = await context.memory.approve_review(reviews[0].id, reviewed_by=111)
    assert item_id > 0
    stats = await context.memory.stats()
    assert stats["knowledge_items"] == 1
    assert stats["pending_reviews"] == 0
    exported = await context.memory.export_memory(scope, fmt="md")
    assert "Project Quartz uses SQLite" in exported
    assert (
        daily_projection_text(context.settings.memory_dir).count("Project Quartz uses SQLite") == 1
    )
    assert "Project Quartz uses SQLite" in (context.settings.memory_dir / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    event_types = {
        event.event_type for event in await context.memory.audit_events(scope=scope, limit=20)
    }
    assert {"memory_review_created", "memory_review_approved"} <= event_types


async def test_rejected_review_never_enters_markdown(context) -> None:
    scope = Scope(-100201, 6)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_scope_settings(
        scope, {"summary_batch_size": 2, "review_before_compact": True}, updated_by=111
    )

    await context.memory.ingest(incoming(scope, "Reject this private proposal.", message_id=10))
    await context.memory.ingest(incoming(scope, "It must stay SQLite-only.", message_id=11))
    reviews = await context.memory.pending_reviews(scope)
    assert len(reviews) == 1
    assert "Reject this private proposal" in reviews[0].content
    assert "Reject this private proposal" not in daily_projection_text(context.settings.memory_dir)

    await context.memory.reject_review(reviews[0].id, reviewed_by=111)

    assert not await context.memory.pending_reviews(scope)
    assert (await context.memory.stats())["knowledge_items"] == 0
    assert "Reject this private proposal" not in daily_projection_text(context.settings.memory_dir)
    assert "Reject this private proposal" not in (
        context.settings.memory_dir / "MEMORY.md"
    ).read_text(encoding="utf-8")
    events = await context.memory.audit_events(scope=scope, limit=20)
    assert {event.event_type for event in events} >= {
        "memory_review_created",
        "memory_review_rejected",
    }
    assert all("Reject this private proposal" not in json.dumps(event.payload) for event in events)


async def test_retention_prunes_raw_messages_without_deleting_durable_memory(context) -> None:
    scope = Scope(-100300, None)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_scope_settings(
        scope, {"raw_message_retention_days": 1}, updated_by=111
    )
    old = datetime(2026, 1, 1, tzinfo=UTC)

    await context.memory.ingest(
        incoming(scope, "Project Quartz keeps durable summaries.", message_id=1, created_at=old)
    )
    await context.memory.ingest(
        incoming(scope, "Decision: keep raw retention short.", message_id=2, created_at=old)
    )
    before = await context.memory.stats()
    assert before["messages"] == 2
    assert before["knowledge_items"] == 1

    pruned = await context.memory.prune_retention(scope, now=old + timedelta(days=3))
    assert pruned["raw_messages"] == 2
    assert pruned["knowledge_items"] == 0
    assert pruned["embeddings"] == 2

    after = await context.memory.stats()
    assert after["messages"] == 0
    assert after["knowledge_items"] == 1
    exported = await context.memory.export_memory(scope, fmt="md")
    assert "Project Quartz keeps durable summaries" in exported
    assert "Project Quartz keeps durable summaries" in daily_projection_text(
        context.settings.memory_dir
    )
    assert "Project Quartz keeps durable summaries" in (
        context.settings.memory_dir / "MEMORY.md"
    ).read_text(encoding="utf-8")


async def test_knowledge_retention_removes_daily_and_canonical_markdown(context) -> None:
    scope = Scope(-100301, None)
    await context.memory.set_scope_settings(scope, {"knowledge_retention_days": 1}, updated_by=111)
    await context.memory.remember(scope, "Expire this durable memory.")
    assert "Expire this durable memory" in daily_projection_text(context.settings.memory_dir)

    pruned = await context.memory.prune_retention(scope, now=utc_now() + timedelta(days=2))

    assert pruned["raw_messages"] == 0
    assert pruned["knowledge_items"] == 1
    assert pruned["embeddings"] == 1
    assert (await context.memory.stats())["knowledge_items"] == 0
    assert "Expire this durable memory" not in daily_projection_text(context.settings.memory_dir)
    assert "Expire this durable memory" not in (
        context.settings.memory_dir / "MEMORY.md"
    ).read_text(encoding="utf-8")
    events = await context.memory.audit_events(scope=scope, event_type="retention_prune", limit=5)
    assert events and events[0].payload["knowledge_items"] == 1


@pytest.mark.parametrize("failure_mode", ["exception", "empty"])
@pytest.mark.parametrize("review_before_compact", [False, True])
async def test_summary_failure_stays_pending_and_retry_succeeds_once(
    context, failure_mode: str, review_before_compact: bool
) -> None:
    scope = Scope(-100400, 8 if review_before_compact else 7)
    raw_sentinel = "PRIVATE-RAW-SENTINEL-DO-NOT-PERSIST"
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_scope_settings(
        scope,
        {"summary_batch_size": 100, "review_before_compact": review_before_compact},
        updated_by=111,
    )
    await context.memory.ingest(incoming(scope, f"{raw_sentinel} first source.", message_id=401))
    await context.memory.ingest(incoming(scope, "Second source stays retryable.", message_id=402))
    provider = RetrySummaryProvider(failure_mode=failure_mode, raw_sentinel=raw_sentinel)
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    assert await context.memory.summarize_now(scope) == 0
    assert (await context.memory.stats())["knowledge_items"] == 0
    assert not await context.memory.pending_reviews(scope)
    async with session_scope(context.session_factory) as session:
        pending_after_failure = await MessageRepository(session).pending_for_summary(scope)
        failed_usages = list(
            (
                await session.scalars(
                    select(AIUsage).where(AIUsage.operation == "summarize").order_by(AIUsage.id)
                )
            ).all()
        )
    assert [message.telegram_message_id for message in pending_after_failure] == [401, 402]
    assert len(failed_usages) == 1
    assert failed_usages[0].error == (
        "RuntimeError: provider operation failed"
        if failure_mode == "exception"
        else "EmptySummaryResponse: provider returned empty content"
    )
    assert failed_usages[0].metadata_json is not None
    assert failed_usages[0].metadata_json["error_type"] == (
        "RuntimeError" if failure_mode == "exception" else "EmptySummaryResponse"
    )
    failure_events = await context.memory.audit_events(
        scope=scope, event_type="summary_failed", limit=10
    )
    assert len(failure_events) == 1
    assert failure_events[0].payload == {
        "source_message_ids": [401, 402],
        "provider": "retry-summary",
        "model": failed_usages[0].model,
        "operation": "summarize",
        "exception_type": (
            "RuntimeError" if failure_mode == "exception" else "EmptySummaryResponse"
        ),
    }
    failed_diagnostics = json.dumps(
        {
            "events": [event.payload for event in failure_events],
            "usages": [
                {"error": usage.error, "metadata": usage.metadata_json} for usage in failed_usages
            ],
        }
    )
    assert raw_sentinel not in failed_diagnostics
    assert raw_sentinel not in daily_projection_text(context.settings.memory_dir)
    assert raw_sentinel not in (context.settings.memory_dir / "MEMORY.md").read_text(
        encoding="utf-8"
    )

    assert await context.memory.summarize_now(scope) == 2
    assert await context.memory.summarize_now(scope) == 0
    async with session_scope(context.session_factory) as session:
        assert not await MessageRepository(session).pending_for_summary(scope)
        all_usages = list(
            (
                await session.scalars(
                    select(AIUsage).where(AIUsage.operation == "summarize").order_by(AIUsage.id)
                )
            ).all()
        )
    assert len(all_usages) == 2
    if review_before_compact:
        reviews = await context.memory.pending_reviews(scope)
        assert len(reviews) == 1
        assert reviews[0].source_message_ids == [401, 402]
        assert (await context.memory.stats())["knowledge_items"] == 0
    else:
        assert not await context.memory.pending_reviews(scope)
        assert (await context.memory.stats())["knowledge_items"] == 1
        assert "retry-safe summary completed" in daily_projection_text(context.settings.memory_dir)


async def test_ingest_reports_summary_completion_only_after_success(context) -> None:
    scope = Scope(-100401, None)
    raw_sentinel = "INGEST-PRIVATE-RAW-SENTINEL"
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 2}, updated_by=111)
    provider = RetrySummaryProvider(failure_mode="exception", raw_sentinel=raw_sentinel)
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    first = await context.memory.ingest(
        incoming(scope, f"{raw_sentinel} first source.", message_id=401)
    )
    second = await context.memory.ingest(
        incoming(scope, "Second source triggers the failed attempt.", message_id=402)
    )

    assert first.summarized is False
    assert second.summarized is False
    assert await context.memory.summarize_now(scope) == 2
    assert (await context.memory.stats())["knowledge_items"] == 1


async def test_ingest_processes_at_most_one_summary_batch(context) -> None:
    scope = Scope(-100407, None)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 2}, updated_by=111)
    await seed_pending_messages(context, scope, 4)
    provider = BatchSummaryProvider()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    result = await context.memory.ingest(
        incoming(
            scope,
            "This message triggers exactly one bounded batch.",
            message_id=5,
            created_at=datetime(2026, 7, 29, 13, tzinfo=UTC),
        )
    )

    assert result.summarized is True
    assert provider.summary_source_counts == [2]
    async with session_scope(context.session_factory) as session:
        pending = await MessageRepository(session).pending_for_summary(scope)
    assert [message.telegram_message_id for message in pending] == [3, 4, 5]


async def test_idle_tick_processes_at_most_one_summary_batch_per_scope(context) -> None:
    scope = Scope(-100408, None)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 2}, updated_by=111)
    await seed_pending_messages(context, scope, 5)
    provider = BatchSummaryProvider()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    assert await context.memory.summarize_idle(now=utc_now() + timedelta(hours=1)) == 2
    assert provider.summary_source_counts == [2]
    async with session_scope(context.session_factory) as session:
        pending = await MessageRepository(session).pending_for_summary(scope)
    assert [message.telegram_message_id for message in pending] == [3, 4, 5]


async def test_ingest_embedding_failure_persists_no_partial_memory(context) -> None:
    scope = Scope(-100409, None)
    await context.memory.set_learning(scope, True, updated_by=111)
    provider = FailingEmbeddingProvider()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    with pytest.raises(RuntimeError, match="embedding provider unavailable"):
        await context.memory.ingest(
            incoming(scope, "This message must remain transient.", message_id=1)
        )

    async with session_scope(context.session_factory) as session:
        counts = await MessageRepository(session).counts(scope)
    assert counts == {"messages": 0, "learned": 0, "skipped": 0}
    assert (await context.memory.stats())["embeddings"] == 0


async def test_summary_backlog_is_split_into_hard_bounded_batches(context) -> None:
    scope = Scope(-100402, None)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 2}, updated_by=111)
    await seed_pending_messages(context, scope, 7)
    provider = BatchSummaryProvider()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    assert await context.memory.summarize_now(scope) == 7
    assert provider.summary_source_counts == [2, 2, 2, 1]
    async with session_scope(context.session_factory) as session:
        items = list(
            (
                await session.scalars(
                    select(KnowledgeItem)
                    .where(KnowledgeItem.chat_id == scope.chat_id)
                    .order_by(KnowledgeItem.id)
                )
            ).all()
        )
    assert [len(item.source_message_ids) for item in items] == [2, 2, 2, 1]
    assert sorted(source for item in items for source in item.source_message_ids) == list(
        range(1, 8)
    )


async def test_concurrent_summarizers_finalize_disjoint_batches_once(context) -> None:
    scope = Scope(-100403, None)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 2}, updated_by=111)
    await seed_pending_messages(context, scope, 4)
    provider = BatchSummaryProvider(barrier_size=2, block=True)
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    first, second = await asyncio.wait_for(
        asyncio.gather(context.memory.summarize_now(scope), context.memory.summarize_now(scope)),
        timeout=10,
    )

    assert first + second == 4
    assert provider.summary_source_counts == [2, 2]
    async with session_scope(context.session_factory) as session:
        items = list(
            (
                await session.scalars(
                    select(KnowledgeItem)
                    .where(KnowledgeItem.chat_id == scope.chat_id)
                    .order_by(KnowledgeItem.id)
                )
            ).all()
        )
    source_groups = [set(item.source_message_ids) for item in items]
    assert len(source_groups) == 2
    assert source_groups[0].isdisjoint(source_groups[1])
    assert set.union(*source_groups) == {1, 2, 3, 4}


async def test_blocked_summary_provider_holds_no_sqlite_write_transaction(context) -> None:
    scope = Scope(-100404, None)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 2}, updated_by=111)
    await seed_pending_messages(context, scope, 2)
    provider = BatchSummaryProvider(block=True)
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    summary_task = asyncio.create_task(context.memory.summarize_now(scope))
    await asyncio.wait_for(provider.summary_started.wait(), timeout=5)
    await asyncio.wait_for(
        context.memory.set_learning(Scope(-100405, None), True, updated_by=111), timeout=2
    )
    provider.summary_release.set()

    assert await asyncio.wait_for(summary_task, timeout=10) == 2


async def test_stale_summary_claim_recovers_without_taking_fresh_claim(context) -> None:
    scope = Scope(-100406, None)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 2}, updated_by=111)
    await seed_pending_messages(context, scope, 2)
    now = utc_now()
    async with session_scope(context.session_factory) as session:
        repository = MessageRepository(session)
        old = await repository.claim_for_summary(
            scope,
            limit=1,
            token="abandoned",  # noqa: S106
            now=now - timedelta(hours=1),
            stale_before=now - timedelta(hours=2),
        )
        fresh = await repository.claim_for_summary(
            scope,
            limit=1,
            token="active",  # noqa: S106
            now=now,
            stale_before=now - timedelta(hours=2),
        )
    provider = BatchSummaryProvider()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)

    assert await context.memory.summarize_now(scope) == 1
    async with session_scope(context.session_factory) as session:
        active = await MessageRepository(session).claimed_by_token("active")
    assert [message.id for message in active] == [fresh[0].id]
    assert old[0].id != fresh[0].id
    recovery_events = await context.memory.audit_events(
        scope=scope, event_type="summary_stale_claims_recovered", limit=5
    )
    assert recovery_events[0].payload == {"recovered_claims": 1, "claim_timeout_seconds": 900}


async def test_failed_summary_releases_claim_before_separate_usage_write(
    context, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-100410, None)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 2}, updated_by=111)
    await seed_pending_messages(context, scope, 2)
    provider = RetrySummaryProvider(
        failure_mode="exception", raw_sentinel="PRIVATE-SUMMARY-ORDER-SENTINEL"
    )
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    context.memory.provider_registry = RetrySummaryRegistry(context.settings, provider)
    order: list[str] = []
    sessions: dict[str, int] = {}
    original_release = MessageRepository.release_summary_claim
    original_add_usage = EventRepository.add_ai_usage

    async def observed_release(repository, token):
        order.append("release")
        sessions["release"] = id(repository.session)
        return await original_release(repository, token)

    async def observed_add_usage(repository, **kwargs):
        if kwargs["operation"] == "summarize":
            order.append("usage")
            sessions["usage"] = id(repository.session)
        return await original_add_usage(repository, **kwargs)

    monkeypatch.setattr(MessageRepository, "release_summary_claim", observed_release)
    monkeypatch.setattr(EventRepository, "add_ai_usage", observed_add_usage)

    assert await context.memory.summarize_now(scope) == 0
    assert order == ["release", "usage"]
    assert sessions["release"] != sessions["usage"]


async def test_canonical_snapshots_limit_provider_work_to_dirty_scopes(context) -> None:
    scope_a = Scope(-100501, None)
    scope_b = Scope(-100502, 8)
    provider = CanonicalRecordingProvider()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider

    item_a = await context.memory.remember(scope_a, "Fact: scope A starts blue.")
    await context.memory.remember(scope_b, "Decision: scope B remains green.")

    assert len(provider.canonical_prompts) == 2
    assert f"Scope: {scope_a.key}" in provider.canonical_prompts[0]
    assert f"Scope: {scope_b.key}" in provider.canonical_prompts[1]

    provider.canonical_prompts.clear()
    await context.memory.compact_memory()
    assert provider.canonical_prompts == []

    await context.memory.edit_note(scope_a, item_a, content="Fact: scope A changes to amber.")
    assert len(provider.canonical_prompts) == 1
    assert f"Scope: {scope_a.key}" in provider.canonical_prompts[0]

    provider.canonical_prompts.clear()
    await context.memory.delete_memory_item(scope_a, item_a)
    memory = context.memory.markdown.read_main()
    assert provider.canonical_prompts == []
    assert "scope A changes to amber" not in memory
    assert "scope B remains green" in memory

    async with session_scope(context.session_factory) as session:
        snapshots = await CanonicalScopeSnapshotRepository(session).list()
    assert set(snapshots) == {scope_b}

    await context.memory.compact_memory(force=True)
    assert len(provider.canonical_prompts) == 1
    assert f"Scope: {scope_b.key}" in provider.canonical_prompts[0]
    async with session_scope(context.session_factory) as session:
        canonical_usage_count = len(
            list(
                (
                    await session.scalars(
                        select(AIUsage).where(AIUsage.operation == "canonical_compact")
                    )
                ).all()
            )
        )
    assert canonical_usage_count == 4


async def test_canonical_snapshot_fingerprint_changes_with_reasoning_setting(context) -> None:
    scope = Scope(-100503, None)
    provider = CanonicalRecordingProvider()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider

    await context.memory.remember(scope, "Fact: fingerprint settings are observable.")
    provider.canonical_prompts.clear()

    await context.memory.set_scope_settings(scope, {"reasoning_level": "high"}, updated_by=111)
    await context.memory.compact_memory()

    assert len(provider.canonical_prompts) == 1
    assert "Reasoning level setting: high." in provider.canonical_prompts[0]


async def test_concurrent_canonical_drift_discards_and_retries_old_provider_result(context) -> None:
    scope = Scope(-100504, None)
    provider = CanonicalRecordingProvider()
    provider.block_next_canonical = True
    provider.canonical_release.clear()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    async with session_scope(context.session_factory) as session:
        item = await KnowledgeRepository(session).add(
            scope=scope,
            kind="manual_note",
            content="Fact: old canonical input.",
            source_message_ids=[],
        )
        item_id = item.id

    compact_task = asyncio.create_task(context.memory.compact_memory(dirty_scopes={scope}))
    await asyncio.wait_for(provider.canonical_started.wait(), timeout=5)
    async with session_scope(context.session_factory) as session:
        updated = await KnowledgeRepository(session).update_item(
            item_id, scope=scope, content="Fact: current canonical input."
        )
        assert updated is not None
    provider.canonical_release.set()

    await asyncio.wait_for(compact_task, timeout=10)

    assert len(provider.canonical_prompts) == 2
    memory = context.memory.markdown.read_main()
    assert "current canonical input" in memory
    assert "old canonical input" not in memory


async def test_failed_canonical_refresh_retains_snapshot_and_remains_retryable(context) -> None:
    scope = Scope(-100505, None)
    sentinel = "PRIVATE-CANONICAL-FAILURE-SENTINEL"
    provider = CanonicalRecordingProvider()
    context.memory.chat_provider = provider
    context.memory.embedding_provider = provider
    item_id = await context.memory.remember(scope, "Fact: last valid canonical snapshot.")
    async with session_scope(context.session_factory) as session:
        before = await CanonicalScopeSnapshotRepository(session).get(scope)
        assert before is not None
        updated = await KnowledgeRepository(session).update_item(
            item_id, scope=scope, content="Fact: provider retry eventually refreshes this."
        )
        assert updated is not None

    provider.fail_next_canonical = sentinel
    provider.canonical_prompts.clear()
    await context.memory.compact_memory(dirty_scopes={scope})

    assert len(provider.canonical_prompts) == 1
    assert "last valid canonical snapshot" in context.memory.markdown.read_main()
    async with session_scope(context.session_factory) as session:
        after_failure = await CanonicalScopeSnapshotRepository(session).get(scope)
        usages = list(
            (
                await session.scalars(
                    select(AIUsage)
                    .where(AIUsage.operation == "canonical_compact")
                    .order_by(AIUsage.id)
                )
            ).all()
        )
    assert after_failure is not None
    assert after_failure.item_fingerprint == before.item_fingerprint
    assert usages[-1].error == "RuntimeError: provider operation failed"
    assert sentinel not in f"{usages[-1].error} {usages[-1].metadata_json}"

    provider.canonical_prompts.clear()
    await context.memory.compact_memory()

    assert len(provider.canonical_prompts) == 1
    assert "provider retry eventually refreshes this" in context.memory.markdown.read_main()


async def test_canonical_snapshot_repository_fails_closed_on_corrupt_entries(context) -> None:
    scope = Scope.global_scope()
    entry = CanonicalMemoryEntry(
        category="Facts", content="Validated snapshot entry.", source_message_ids=[1], item_ids=[2]
    )
    async with session_scope(context.session_factory) as session:
        repository = CanonicalScopeSnapshotRepository(session)
        await repository.upsert(
            scope=scope,
            item_fingerprint="a" * 64,
            entries=[entry],
            provider="fake",
            provider_profile="default",
            model="fake-chat",
            reasoning_level="medium",
            prompt_version="canonical-v1",
        )
        first = await repository.get(scope)
        assert first is not None
        assert first.entries == (entry,)
        await repository.upsert(
            scope=scope,
            item_fingerprint="b" * 64,
            entries=[entry],
            provider="fake",
            provider_profile="default",
            model="fake-chat",
            reasoning_level="high",
            prompt_version="canonical-v1",
        )
        replacement = await repository.get(scope)
        assert replacement is not None
        assert replacement.item_fingerprint == "b" * 64
        assert replacement.reasoning_level == "high"

    async with session_scope(context.session_factory) as session:
        row = await session.scalar(
            select(CanonicalScopeSnapshot).where(CanonicalScopeSnapshot.scope_key == scope.key)
        )
        assert row is not None
        row.entries_json = [{"category": "Facts", "content": 7}]

    async with session_scope(context.session_factory) as session:
        repository = CanonicalScopeSnapshotRepository(session)
        assert await repository.get(scope) is None
        assert await repository.delete_scope(scope) is True
        assert await repository.delete_scope(scope) is False
