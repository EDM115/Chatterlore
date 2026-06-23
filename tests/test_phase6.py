from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import IncomingMessage


def incoming(
    scope: Scope, text: str, *, message_id: int, user_id: int = 222, is_command: bool = False
) -> IncomingMessage:
    return IncomingMessage(
        telegram_message_id=message_id,
        scope=scope,
        user_id=user_id,
        username=f"user{user_id}",
        text=text,
        created_at=datetime(2026, 6, 23, tzinfo=UTC),
        is_command=is_command,
    )


@pytest.mark.asyncio
async def test_memory_status_known_scopes_and_skip_debug(context: AppContext) -> None:
    scope = Scope(-601, 10)
    await context.memory.set_scope_metadata(scope, chat_title="Phase 6", topic_title="Status")
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.ingest(
        incoming(scope, "Decision: keep scoped status visible.", message_id=1)
    )
    await context.memory.ingest(incoming(scope, "/not_learned", message_id=2, is_command=True))
    await context.memory.remember(scope, "Manual status note.", source_message_id=1)
    await context.memory.ask(scope, "What is the status decision?")

    status = await context.memory.memory_status(scope)
    scopes = await context.memory.known_scopes(Scope(-601, None))
    skipped = await context.memory.skipped_messages(scope)

    assert status.scope_label.startswith("Phase 6 / Status")
    assert status.messages == 2
    assert status.learned == 1
    assert status.skipped == 1
    assert status.knowledge_items == 1
    assert status.ai_usage_total >= 1
    assert [item.scope for item in scopes] == [scope]
    assert skipped[0].telegram_message_id == 2
    assert "commands are not learned" in skipped[0].explanation


@pytest.mark.asyncio
async def test_telegram_desktop_json_import_learns_text_links_and_attachments(
    context: AppContext, tmp_path
) -> None:
    scope = Scope(-602, None)
    export_path = tmp_path / "result.json"
    export_path.write_text(
        json.dumps(
            {
                "name": "Imported Group",
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "date": "2026-06-23T10:00:00",
                        "from": "Dana",
                        "from_id": "user123",
                        "text": ["Read ", {"type": "link", "text": "https://example.test/runbook"}],
                        "text_entities": [
                            {"type": "plain", "text": "Read "},
                            {"type": "link", "text": "https://example.test/runbook"},
                        ],
                        "file": "files/runbook.pdf",
                    },
                    {"id": 2, "type": "service", "date": "2026-06-23T10:01:00", "text": "ignored"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = await context.memory.import_telegram_desktop_json(export_path, scope)
    found = await context.memory.search(scope, "runbook pdf")

    assert result.imported == 1
    assert result.skipped == 0
    assert found
    assert "runbook.pdf" in found[0].content
    assert "https://example.test/runbook" in found[0].content


@pytest.mark.asyncio
async def test_memory_item_browse_refresh_and_delete(context: AppContext) -> None:
    scope = Scope(-603, None)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.ingest(
        incoming(scope, "Decision: refresh item content from source.", message_id=1)
    )
    item_id = await context.memory.remember(scope, "Old item content.", source_message_id=1)

    items = await context.memory.browse_items(scope)
    refresh = await context.memory.refresh_memory_item(scope, item_id, mode="both", updated_by=111)
    deleted = await context.memory.delete_memory_item(scope, item_id, deleted_by=111)
    remaining = await context.memory.browse_items(scope)

    assert [item.id for item in items] == [item_id]
    assert refresh.summarized is True
    assert refresh.embeddings_refreshed == 1
    assert deleted.id == item_id
    assert all(item.id != item_id for item in remaining)


@pytest.mark.asyncio
async def test_settings_preview_requires_confirmation(context: AppContext) -> None:
    scope = Scope(-604, None)
    preview = await context.memory.preview_scope_settings(
        scope, {"retrieval_top_k": 9}, user_id=111
    )
    before = await context.memory.get_scope_settings(scope)

    assert preview.token
    assert preview.applied is False
    assert before.retrieval_top_k != 9

    confirmed = await context.memory.confirm_scope_settings(
        scope, user_id=111, confirm=preview.token
    )
    after = await context.memory.get_scope_settings(scope)
    events = await context.memory.audit_events(event_type="scope_settings_confirmed", scope=scope)

    assert confirmed.applied is True
    assert after.retrieval_top_k == 9
    assert events and events[0].payload["updates"] == {"retrieval_top_k": 9}


@pytest.mark.asyncio
async def test_ask_keeps_compact_source_references(context: AppContext) -> None:
    scope = Scope(-605, None)
    await context.memory.remember(
        scope, "Phase 6 source references should be compact.", source_message_id=77
    )

    answer = await context.memory.ask(scope, "What should source references be?")

    assert "source:77" in answer or "Sources: -605:main#77" in answer
