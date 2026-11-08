from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.models import Embedding, KnowledgeItem, LearningPolicy, Message
from pzp_ai_bot.db.repositories import EmbeddingRepository, KnowledgeRepository, MessageRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.retrieval import Retriever, SearchResult
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import IncomingMessage
from pzp_ai_bot.memory.vector import (
    cosine_similarity,
    encode_vector,
    normalize,
    normalized_cosine_similarity,
)


def incoming(scope: Scope, text: str, *, message_id: int, user_id: int = 222) -> IncomingMessage:
    return IncomingMessage(
        telegram_message_id=message_id,
        scope=scope,
        user_id=user_id,
        username="user",
        text=text,
        created_at=datetime(2026, 6, 16, 12, message_id % 60, tzinfo=UTC),
    )


async def seed_retrieval_rows(
    context: AppContext, scope: Scope, *, knowledge_count: int, message_count: int, id_offset: int
) -> None:
    now = datetime.now(UTC)
    knowledge_ids = [id_offset + index for index in range(knowledge_count)]
    message_ids = [id_offset + 100_000 + index for index in range(message_count)]
    async with session_scope(context.session_factory) as session:
        if knowledge_ids:
            await session.execute(
                insert(KnowledgeItem),
                [
                    {
                        "id": item_id,
                        "chat_id": scope.chat_id,
                        "message_thread_id": scope.thread_id,
                        "kind": "fact",
                        "content": f"Durable retrieval fact {index}",
                        "source_message_ids": [index],
                        "metadata_json": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for index, item_id in enumerate(knowledge_ids)
                ],
            )
        if message_ids:
            await session.execute(
                insert(Message),
                [
                    {
                        "id": message_id,
                        "telegram_message_id": index + 1,
                        "chat_id": scope.chat_id,
                        "message_thread_id": scope.thread_id,
                        "user_id": 222,
                        "username": "benchmark",
                        "chat_title": None,
                        "chat_username": None,
                        "topic_title": None,
                        "message_permalink": None,
                        "text": f"Recent retrieval message {index}",
                        "normalized_text": f"recent retrieval message {index}",
                        "learned": True,
                        "skip_reason": None,
                        "reply_to_message_id": None,
                        "created_at": now + timedelta(microseconds=index),
                        "ingested_at": now,
                        "summarized_at": None,
                    }
                    for index, message_id in enumerate(message_ids)
                ],
            )
        await session.execute(
            insert(Embedding),
            [
                {
                    "id": id_offset + 200_000 + index,
                    "target_type": target_type,
                    "target_id": target_id,
                    "chat_id": scope.chat_id,
                    "message_thread_id": scope.thread_id,
                    "provider": "fake",
                    "model": "fake-embedding",
                    "dimension": 2,
                    "vector": encode_vector([1.0, float(index % 2)]),
                    "created_at": now + timedelta(microseconds=index),
                }
                for index, (target_type, target_id) in enumerate(
                    [
                        *(("knowledge_item", item_id) for item_id in knowledge_ids),
                        *(("message", message_id) for message_id in message_ids),
                    ]
                )
            ],
        )


async def measured_retrieval(
    context: AppContext, scope: Scope, *, candidate_limit: int
) -> tuple[list[SearchResult], int, float]:
    engine = context.session_factory.kw["bind"]
    assert isinstance(engine, AsyncEngine)
    query_count = 0

    def count_query(*_args) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_query)
    started = perf_counter()
    try:
        async with session_scope(context.session_factory) as session:
            results = await Retriever(
                embedding_repo=EmbeddingRepository(session),
                knowledge_repo=KnowledgeRepository(session),
                message_repo=MessageRepository(session),
            ).search([1.0, 0.0], scope, top_k=6, candidate_limit=candidate_limit)
    finally:
        elapsed = perf_counter() - started
        event.remove(engine.sync_engine, "before_cursor_execute", count_query)
    return results, query_count, elapsed


@pytest.mark.parametrize("candidate_count", [1_000, 10_000])
@pytest.mark.asyncio
async def test_retrieval_query_count_is_constant_and_candidate_work_is_bounded(
    context: AppContext, candidate_count: int
) -> None:
    scope = Scope(-10_000 - candidate_count, None)
    knowledge_count = 100
    await seed_retrieval_rows(
        context,
        scope,
        knowledge_count=knowledge_count,
        message_count=candidate_count - knowledge_count,
        id_offset=candidate_count * 1_000,
    )

    results, query_count, elapsed = await measured_retrieval(context, scope, candidate_limit=5_000)

    print(
        f"bounded retrieval candidates={candidate_count} queries={query_count} "
        f"elapsed_seconds={elapsed:.6f}"
    )
    assert results
    assert results[0].source.startswith("knowledge_item:")
    assert query_count == 4


def test_cosine_similarity_matches_hand_worked_geometry() -> None:
    query = [3.0, 4.0, 0.0]
    normalized_query = [0.6, 0.8, 0.0]
    assert normalize(query) == pytest.approx(normalized_query, abs=1e-12)

    cases = [
        ([5.0, 0.0, 12.0], 3 / 13),
        ([4.0, -3.0, 0.0], 0.0),
        ([-3.0, -4.0, 0.0], -1.0),
        ([3.0, 4.0, 0.0], 1.0),
        ([0.0, 0.0, 0.0], 0.0),
        ([1.0, 2.0], 0.0),
        ([], 0.0),
    ]
    for candidate, expected in cases:
        assert cosine_similarity(query, candidate) == pytest.approx(expected, abs=1e-12)
        assert normalized_cosine_similarity(normalized_query, candidate) == pytest.approx(
            expected, abs=1e-12
        )
    assert cosine_similarity([], []) == 0.0
    assert normalized_cosine_similarity([], []) == 0.0


@pytest.mark.asyncio
async def test_default_candidate_bound_keeps_older_durable_memory_eligible(
    context: AppContext,
) -> None:
    scope = Scope(-25_000, 5)
    candidate_limit = context.settings.retrieval_candidate_limit
    id_offset = 30_000_000
    await seed_retrieval_rows(
        context, scope, knowledge_count=1, message_count=candidate_limit, id_offset=id_offset
    )

    async with session_scope(context.session_factory) as session:
        candidates = await EmbeddingRepository(session).list_for_retrieval(
            scope, limit=candidate_limit
        )
    results, query_count, _elapsed = await measured_retrieval(
        context, scope, candidate_limit=candidate_limit
    )

    assert len(candidates) == candidate_limit
    assert candidates[0][0].target_type == "knowledge_item"
    assert all(row.target_type == "message" for row, _vector in candidates[1:])
    assert id_offset + 100_000 not in {row.target_id for row, _vector in candidates}
    assert results[0].content == "Durable retrieval fact 0"
    assert query_count == 4


@pytest.mark.asyncio
async def test_concurrent_main_chat_message_add_is_database_idempotent(context: AppContext) -> None:
    scope = Scope(-26_000, None)

    async def add_once(telegram_message_id: int, start: asyncio.Event) -> tuple[int, bool]:
        async with session_scope(context.session_factory) as session:
            await start.wait()
            message, created = await MessageRepository(session).add(
                telegram_message_id=telegram_message_id,
                scope=scope,
                user_id=222,
                username="concurrent",
                text="Concurrent Telegram delivery",
                normalized_text="concurrent telegram delivery",
                learned=True,
                skip_reason=None,
                reply_to_message_id=None,
                created_at=datetime(2026, 7, 29, tzinfo=UTC),
            )
            return message.id, created

    for iteration in range(5):
        start = asyncio.Event()
        telegram_message_id = 50_000 + iteration
        attempts = [
            asyncio.create_task(add_once(telegram_message_id, start)),
            asyncio.create_task(add_once(telegram_message_id, start)),
        ]
        await asyncio.sleep(0)
        start.set()
        results = await asyncio.gather(*attempts)

        assert sum(created for _message_id, created in results) == 1
        assert len({message_id for message_id, _created in results}) == 1
        async with session_scope(context.session_factory) as session:
            rows = list(
                await session.scalars(
                    select(Message).where(
                        Message.chat_id == scope.chat_id,
                        Message.message_thread_id.is_(None),
                        Message.telegram_message_id == telegram_message_id,
                    )
                )
            )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_same_telegram_message_id_remains_distinct_across_chats_and_topics(
    context: AppContext,
) -> None:
    async with session_scope(context.session_factory) as session:
        repository = MessageRepository(session)
        rows = [
            await repository.add(
                telegram_message_id=60_000,
                scope=Scope(-27_000, thread_id),
                user_id=222,
                username="topic",
                text=f"Topic {thread_id}",
                normalized_text=f"topic {thread_id}",
                learned=True,
                skip_reason=None,
                reply_to_message_id=None,
                created_at=datetime(2026, 7, 29, tzinfo=UTC),
            )
            for thread_id in (1, 2)
        ]
        rows.append(
            await repository.add(
                telegram_message_id=60_000,
                scope=Scope(-27_001, 1),
                user_id=222,
                username="other-chat",
                text="Other chat",
                normalized_text="other chat",
                learned=True,
                skip_reason=None,
                reply_to_message_id=None,
                created_at=datetime(2026, 7, 29, tzinfo=UTC),
            )
        )

    assert [created for _message, created in rows] == [True, True, True]
    assert len({message.id for message, _created in rows}) == 3


@pytest.mark.asyncio
async def test_concurrent_summary_claimers_receive_disjoint_bounded_batches(
    context: AppContext,
) -> None:
    scope = Scope(-28_000, None)
    async with session_scope(context.session_factory) as session:
        repository = MessageRepository(session)
        for message_id in range(1, 9):
            await repository.add(
                telegram_message_id=message_id,
                scope=scope,
                user_id=222,
                username="claim",
                text=f"Claim source {message_id}",
                normalized_text=f"claim source {message_id}",
                learned=True,
                skip_reason=None,
                reply_to_message_id=None,
                created_at=datetime(2026, 7, 29, 12, message_id, tzinfo=UTC),
            )

    start = asyncio.Event()
    now = datetime(2026, 7, 29, 13, tzinfo=UTC)

    async def claim(token: str) -> list[int]:
        async with session_scope(context.session_factory) as session:
            await start.wait()
            rows = await MessageRepository(session).claim_for_summary(
                scope, limit=3, token=token, now=now, stale_before=now - timedelta(minutes=15)
            )
            return [row.id for row in rows]

    attempts = [asyncio.create_task(claim("claim-a")), asyncio.create_task(claim("claim-b"))]
    await asyncio.sleep(0)
    start.set()
    first, second = await asyncio.gather(*attempts)

    assert len(first) == len(second) == 3
    assert set(first).isdisjoint(second)


@pytest.mark.asyncio
async def test_summary_claim_release_finalize_and_stale_recovery(context: AppContext) -> None:
    scope = Scope(-29_000, 4)
    async with session_scope(context.session_factory) as session:
        repository = MessageRepository(session)
        for message_id in range(1, 4):
            await repository.add(
                telegram_message_id=message_id,
                scope=scope,
                user_id=222,
                username="claim",
                text=f"Claim state {message_id}",
                normalized_text=f"claim state {message_id}",
                learned=True,
                skip_reason=None,
                reply_to_message_id=None,
                created_at=datetime(2026, 7, 29, 12, message_id, tzinfo=UTC),
            )

    base = datetime(2026, 7, 29, 13, tzinfo=UTC)
    async with session_scope(context.session_factory) as session:
        repository = MessageRepository(session)
        old = await repository.claim_for_summary(
            scope,
            limit=1,
            token="old-claim",  # noqa: S106
            now=base,
            stale_before=base - timedelta(minutes=15),
        )
        fresh = await repository.claim_for_summary(
            scope,
            limit=1,
            token="fresh-claim",  # noqa: S106
            now=base + timedelta(minutes=10),
            stale_before=base - timedelta(minutes=5),
        )
    assert len(old) == len(fresh) == 1

    async with session_scope(context.session_factory) as session:
        recovered = await MessageRepository(session).claim_for_summary(
            scope,
            limit=3,
            token="recovered-claim",  # noqa: S106
            now=base + timedelta(minutes=16),
            stale_before=base + timedelta(minutes=1),
        )
    assert [row.id for row in recovered] == [old[0].id, old[0].id + 2]

    async with session_scope(context.session_factory) as session:
        repository = MessageRepository(session)
        assert await repository.release_summary_claim("fresh-claim") == 1
        recovered_ids = [row.id for row in recovered]
        assert (
            await repository.finalize_summary_claim(
                "recovered-claim", recovered_ids, summarized_at=base + timedelta(minutes=17)
            )
            == 2
        )
        assert (
            await repository.finalize_summary_claim(
                "recovered-claim", recovered_ids, summarized_at=base + timedelta(minutes=18)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_summarize_now_all_groups_pending_messages_by_concrete_scope(
    context: AppContext,
) -> None:
    topic_scope = Scope(-100, 10)
    chat_scope = Scope(-200, None)
    await context.memory.set_learning(topic_scope, True, updated_by=111)
    await context.memory.set_learning(chat_scope, True, updated_by=111)
    await context.memory.ingest(
        incoming(topic_scope, "Topic alpha keeps release notes in Markdown.", message_id=1)
    )
    await context.memory.ingest(
        incoming(chat_scope, "Chat beta uses SQLite for local state.", message_id=2)
    )

    count = await context.memory.summarize_now(Scope.global_scope())

    assert count == 2
    exported = await context.memory.export_memory(None, fmt="jsonl")
    rows = [json.loads(line) for line in exported.splitlines()]
    assert {row["scope"] for row in rows} == {"-100:10", "-200:main"}
    assert "all:main" not in {row["scope"] for row in rows}


@pytest.mark.asyncio
async def test_summarize_idle_waits_for_scope_idle_timeout(context: AppContext) -> None:
    scope = Scope(-300, 3)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.ingest(
        incoming(scope, "Idle summaries should wait for quiet scopes.", message_id=3)
    )

    now = datetime.now(UTC)
    assert await context.memory.summarize_idle(now=now) == 0
    assert (
        await context.memory.summarize_idle(
            now=now + timedelta(seconds=context.settings.summary_idle_seconds + 1)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_duplicate_telegram_message_noops_without_duplicate_embeddings(
    context: AppContext,
) -> None:
    scope = Scope(-400, None)
    await context.memory.set_learning(scope, True, updated_by=111)

    first = await context.memory.ingest(
        incoming(scope, "Duplicate delivery should be idempotent.", message_id=4)
    )
    duplicate = await context.memory.ingest(
        incoming(scope, "Duplicate delivery should be idempotent.", message_id=4)
    )

    assert first.learned is True
    assert duplicate.learned is True
    assert duplicate.reason == "duplicate_message"
    stats = await context.memory.stats()
    assert stats["messages"] == 1
    assert stats["embeddings"] == 1


@pytest.mark.asyncio
async def test_policy_upsert_collapses_nullable_duplicate_rows(context: AppContext) -> None:
    scope = Scope(-500, None)
    async with session_scope(context.session_factory) as session:
        now = datetime.now(UTC)
        session.add_all(
            [
                LearningPolicy(
                    policy_type="chat_learning",
                    chat_id=scope.chat_id,
                    message_thread_id=None,
                    user_id=None,
                    key=None,
                    enabled=True,
                    value=None,
                    updated_by=1,
                    updated_at=now,
                ),
                LearningPolicy(
                    policy_type="chat_learning",
                    chat_id=scope.chat_id,
                    message_thread_id=None,
                    user_id=None,
                    key=None,
                    enabled=True,
                    value=None,
                    updated_by=2,
                    updated_at=now,
                ),
            ]
        )

    await context.memory.set_learning(scope, False, updated_by=111)

    async with session_scope(context.session_factory) as session:
        rows = list(
            (
                await session.scalars(
                    select(LearningPolicy).where(
                        LearningPolicy.policy_type == "chat_learning",
                        LearningPolicy.chat_id == scope.chat_id,
                        LearningPolicy.message_thread_id.is_(None),
                    )
                )
            ).all()
        )
    assert len(rows) == 1
    assert rows[0].enabled is False
    assert rows[0].updated_by == 111


@pytest.mark.asyncio
async def test_message_embeddings_are_retrieved_below_knowledge_items(context: AppContext) -> None:
    scope = Scope(-600, 6)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.ingest(
        incoming(scope, "The graphite roadmap is only in raw chat so far.", message_id=6)
    )

    raw_results = await context.memory.search(scope, "graphite roadmap", top_k=1)
    assert raw_results[0].source.startswith("message:")
    assert "graphite roadmap" in raw_results[0].content

    await context.memory.remember(scope, "Canonical note: Dana owns release coordination.")
    tiered_results = await context.memory.search(scope, "graphite roadmap", top_k=2)
    assert tiered_results[0].source.startswith("knowledge_item:")
    assert any(result.source.startswith("message:") for result in tiered_results[1:])


@pytest.mark.asyncio
async def test_import_jsonl_rejects_empty_and_malformed_files(
    context: AppContext, tmp_path
) -> None:
    scope = Scope(-700, None)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    malformed = tmp_path / "bad.jsonl"
    malformed.write_text("{not json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no memory items"):
        await context.memory.import_jsonl(empty, scope)
    with pytest.raises(ValueError, match="malformed JSONL at line 1"):
        await context.memory.import_jsonl(malformed, scope)

    stats = await context.memory.stats()
    assert stats["knowledge_items"] == 0
