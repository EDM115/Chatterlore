from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import IncomingMessage


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

    item_id = await context.memory.approve_review(reviews[0].id, reviewed_by=111)
    assert item_id > 0
    stats = await context.memory.stats()
    assert stats["knowledge_items"] == 1
    assert stats["pending_reviews"] == 0
    exported = await context.memory.export_memory(scope, fmt="md")
    assert "Project Quartz uses SQLite" in exported


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
