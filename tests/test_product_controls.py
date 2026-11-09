from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

import chatterlore.memory.transfer as transfer_module
from chatterlore.app_context import AppContext
from chatterlore.memory.scope import Scope, parse_scope
from chatterlore.memory.service import IncomingMessage, format_settings_change_preview
from chatterlore.telegram.utils import parse_key_values


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
    await context.memory.set_scope_metadata(
        scope, chat_title="Product Controls", topic_title="Status"
    )
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

    assert status.scope_label.startswith("Product Controls / Status")
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
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram import must not use Path.read_text")
        ),
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


@pytest.mark.parametrize("target", [Scope.global_scope(), Scope(-604), Scope(-604, 7)])
@pytest.mark.asyncio
async def test_settings_preview_suggested_confirmation_preserves_target(
    context: AppContext, target: Scope
) -> None:
    current = Scope(111)
    preview = await context.memory.preview_scope_settings(
        target, {"retrieval_top_k": 9}, user_id=111
    )
    suggested = re.search(r"/settings (.+?) before ", format_settings_change_preview(preview))
    assert suggested is not None
    args = parse_key_values(suggested.group(1))
    command_scope = parse_scope(args.get("scope", "this_topic"), current=current)

    confirmed = await context.memory.confirm_scope_settings(
        command_scope, user_id=111, confirm=args["confirm"]
    )

    assert confirmed.applied is True
    assert confirmed.scope == target
    assert (await context.memory.get_scope_settings(target)).retrieval_top_k == 9
    if not target.is_global:
        assert (await context.memory.get_scope_settings(current)).retrieval_top_k != 9


@pytest.mark.asyncio
async def test_ask_keeps_compact_source_references(context: AppContext) -> None:
    scope = Scope(-605, None)
    await context.memory.remember(
        scope, "Source references should be compact.", source_message_id=77
    )

    answer = await context.memory.ask(scope, "What should source references be?")

    assert "source:77" in answer or "Sources: -605:main#77" in answer


@pytest.mark.asyncio
async def test_telegram_update_dump_import_learns_links_and_document_metadata(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-606, None)
    export_path = tmp_path / "updates.json"
    export_path.write_text(
        json.dumps(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 100,
                        "message": {
                            "message_id": 31,
                            "date": 1_782_201_600,
                            "chat": {"id": -606, "type": "supergroup", "title": "Update Dump"},
                            "from": {
                                "id": 333,
                                "is_bot": False,
                                "first_name": "Dana",
                                "username": "dana",
                            },
                            "caption": "Read the runbook",
                            "caption_entities": [
                                {
                                    "type": "text_link",
                                    "offset": 9,
                                    "length": 7,
                                    "url": "https://example.test/runbook",
                                }
                            ],
                            "document": {
                                "file_id": "doc",
                                "file_unique_id": "uniq",
                                "file_name": "runbook.pdf",
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram import must not use Path.read_text")
        ),
    )

    result = await context.memory.import_telegram_desktop_json(export_path, scope)
    found = await context.memory.search(scope, "runbook pdf")

    assert result.imported == 1
    assert found
    assert "https://example.test/runbook" in found[0].content
    assert "runbook.pdf" in found[0].content


@pytest.mark.asyncio
async def test_telegram_import_checkpoints_middle_batch_failure_and_retries_idempotently(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transfer_module, "IMPORT_BATCH_SIZE", 2)
    scope = Scope(-608, None)
    export_path = tmp_path / "partial-updates.json"
    export_path.write_text(
        json.dumps(
            [
                {
                    "message": {
                        "message_id": index,
                        "date": 1_782_201_600 + index,
                        "chat": {"id": -608, "type": "supergroup"},
                        "text": f"Imported message {index}.",
                    }
                }
                for index in range(1, 6)
            ]
        ),
        encoding="utf-8",
    )
    original_embed = context.memory.embedding_provider.embed
    calls = 0

    async def fail_second_batch(texts: Sequence[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("sensitive provider failure")
        return await original_embed(texts)

    monkeypatch.setattr(context.memory.embedding_provider, "embed", fail_second_batch)

    with pytest.raises(RuntimeError, match="sensitive provider failure"):
        await context.memory.import_telegram_desktop_json(export_path, scope)

    status = await context.memory.memory_status(scope)
    failures = await context.memory.audit_events(
        event_type="telegram_json_import_failed", scope=scope
    )
    assert status.messages == 2
    assert failures[0].payload["imported"] == 2
    assert failures[0].payload["completed_batches"] == 1
    assert "sensitive" not in json.dumps(failures[0].payload)

    monkeypatch.setattr(context.memory.embedding_provider, "embed", original_embed)
    retry = await context.memory.import_telegram_desktop_json(export_path, scope)

    assert retry.imported == 3
    assert retry.skipped == 2
    assert (await context.memory.memory_status(scope)).messages == 5


@pytest.mark.asyncio
async def test_telegram_import_rejects_unsupported_shape_before_business_writes(
    context: AppContext, tmp_path: Path
) -> None:
    scope = Scope(-609, None)
    export_path = tmp_path / "unsupported.json"
    export_path.write_text(
        json.dumps(
            {
                "name": "Not an export",
                "messages": {"id": 1, "type": "message", "text": "Must not be imported."},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Telegram Desktop export or Bot API update dump"):
        await context.memory.import_telegram_desktop_json(export_path, scope)

    assert (await context.memory.memory_status(scope)).messages == 0


@pytest.mark.asyncio
async def test_representative_import_export_forget_memory_workflow(
    context: AppContext, tmp_path
) -> None:
    scope = Scope(-607, None)
    export_path = tmp_path / "representative.json"
    messages = [
        "Decision: Beta launch uses the private Telegram group first.",
        "Todo: Dana should publish the beta runbook.",
        "Open question: Who owns provider cost review?",
        "Preference: Keep summaries concise for mobile readers.",
        "Fact: SQLite remains the beta storage backend.",
        "Decision: Cross-chat retrieval stays disabled unless explicitly permissioned.",
        "Todo: Review export contents before sharing them.",
        "Fact: Local fake provider is used for offline verification.",
        "Todo: Rotate Telegram tokens before private beta.",
        "Preference: Use scoped topic memory by default.",
        "Decision: Retention pruning keeps durable summaries.",
        "Open question: Which imported topics need manual cleanup?",
    ]
    export_path.write_text(
        json.dumps(
            {
                "name": "Representative Beta Group",
                "messages": [
                    {
                        "id": index,
                        "type": "message",
                        "date": "2026-06-24T10:00:00",
                        "from_id": "user333",
                        "from": "Dana",
                        "text": text,
                    }
                    for index, text in enumerate(messages, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )

    imported = await context.memory.import_telegram_desktop_json(export_path, scope)
    summarized = await context.memory.summarize_now(scope)
    exported = await context.memory.export_memory(scope, fmt="jsonl")
    answer = await context.memory.ask(scope, "What is the beta launch decision?")
    ok, prompt = await context.memory.forget(scope, user_id=111, confirm=None)
    token = prompt.split("confirm=", 1)[1].split(" ", 1)[0]
    confirmed, result = await context.memory.forget(scope, user_id=111, confirm=token)
    remaining = await context.memory.search(scope, "beta launch")

    assert imported.imported == len(messages)
    assert summarized == len(messages)
    assert "Beta launch uses the private Telegram group first" in exported
    assert "beta" in answer.lower()
    assert ok is False
    assert confirmed is True
    assert "Forgot scope" in result
    assert remaining == []
