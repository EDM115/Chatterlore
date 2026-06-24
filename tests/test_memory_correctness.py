from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.models import LearningPolicy
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import IncomingMessage


def incoming(scope: Scope, text: str, *, message_id: int, user_id: int = 222) -> IncomingMessage:
    return IncomingMessage(
        telegram_message_id=message_id,
        scope=scope,
        user_id=user_id,
        username="user",
        text=text,
        created_at=datetime(2026, 6, 16, 12, message_id % 60, tzinfo=UTC),
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
