from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import pzp_ai_bot.memory.transfer as transfer_module
from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.models import KnowledgeItem
from pzp_ai_bot.db.repositories import KnowledgeRepository
from pzp_ai_bot.memory.markdown import MarkdownMemoryStore
from pzp_ai_bot.memory.scope import Scope


def daily_projection_text(memory_dir: Path) -> str:
    daily_dir = memory_dir / "daily"
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(daily_dir.glob("*/*.md")))


def test_rebuild_daily_projection_is_deterministic_and_preserves_unrelated_files(
    tmp_path: Path,
) -> None:
    memory_dir = tmp_path / "memory"
    store = MarkdownMemoryStore(memory_dir)
    stale_path = memory_dir / "daily" / "2026-07-28" / "-999_main.md"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text("stale generated memory", encoding="utf-8")
    unrelated_path = stale_path.with_name("operator-notes.txt")
    unrelated_path.write_text("keep this file", encoding="utf-8")
    created_at = datetime(2026, 7, 29, 9, 15, tzinfo=UTC)
    item = KnowledgeItem(
        id=7,
        chat_id=-100,
        message_thread_id=5,
        kind="manual_note",
        content="Projection note.",
        source_message_ids=[42],
        metadata_json=None,
        created_at=created_at,
        updated_at=created_at,
    )
    expected_path = memory_dir / "daily" / "2026-07-29" / "-100_5.md"

    assert store.rebuild_daily([item]) == [expected_path]
    first_bytes = expected_path.read_bytes()
    assert store.rebuild_daily([item]) == [expected_path]

    assert expected_path.read_bytes() == first_bytes
    assert expected_path.read_text(encoding="utf-8").count("Projection note.") == 1
    assert not stale_path.exists()
    assert unrelated_path.read_text(encoding="utf-8") == "keep this file"
    assert not list(memory_dir.glob(".daily-staging-*"))


@pytest.mark.asyncio
async def test_remember_search_and_ask_are_scoped(context: AppContext) -> None:
    left = Scope(-100, 1)
    right = Scope(-100, 2)
    await context.memory.remember(left, "The release codename is bluebird.", source_message_id=10)
    await context.memory.remember(right, "The release codename is redwood.", source_message_id=20)

    left_results = await context.memory.search(left, "bluebird codename")
    assert left_results
    assert "bluebird" in left_results[0].content
    assert all(result.scope == left for result in left_results)

    answer = await context.memory.ask(left, "What is the release codename?")
    assert "bluebird" in answer
    assert "redwood" not in answer


@pytest.mark.asyncio
async def test_remember_emits_content_free_create_audit_event(context: AppContext) -> None:
    scope = Scope(-100, 3)
    content = "Sensitive durable note content must stay out of audit metadata."

    item_id = await context.memory.remember(
        scope,
        content,
        source_message_id=42,
        tags=["private"],
        priority=8,
        source="https://example.invalid/private-note",
    )

    events = await context.memory.audit_events(
        scope=scope, event_type="manual_note_created", limit=5
    )
    assert len(events) == 1
    assert events[0].scope == scope
    assert events[0].payload == {"knowledge_item_id": item_id}
    assert content not in json.dumps(events[0].payload)


@pytest.mark.asyncio
async def test_edit_note_emits_content_free_audit_event(context: AppContext) -> None:
    scope = Scope(-100, 4)
    original_content = "Original durable note content."
    updated_content = "Updated durable note content must stay out of audit metadata."
    item_id = await context.memory.remember(scope, original_content)

    await context.memory.edit_note(
        scope,
        item_id,
        content=updated_content,
        tags=["private-tag"],
        priority=7,
        source="https://example.invalid/note?token=private-value",
    )

    events = await context.memory.audit_events(
        scope=scope, event_type="manual_note_updated", limit=5
    )
    assert len(events) == 1
    assert events[0].scope == scope
    assert events[0].payload == {"knowledge_item_id": item_id}
    serialized_payload = json.dumps(events[0].payload)
    assert original_content not in serialized_payload
    assert updated_content not in serialized_payload
    assert "private-tag" not in serialized_payload
    assert "private-value" not in serialized_payload


@pytest.mark.asyncio
async def test_forget_requires_confirmation_and_deletes_scope(context: AppContext) -> None:
    scope = Scope(-100, 1)
    await context.memory.remember(scope, "Delete me.")
    assert "Delete me." in daily_projection_text(context.settings.memory_dir)
    ok, prompt = await context.memory.forget(scope, user_id=111, confirm=None)
    assert ok is False
    token = prompt.split("confirm=", 1)[1].split(" ", 1)[0]
    ok, result = await context.memory.forget(scope, user_id=111, confirm=token)
    assert ok is True
    assert "Forgot scope" in result
    assert not await context.memory.search(scope, "Delete me")
    assert "Delete me." not in daily_projection_text(context.settings.memory_dir)
    assert "Delete me." not in (context.settings.memory_dir / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    events = await context.memory.audit_events(scope=scope, event_type="forget", limit=5)
    assert events and events[0].payload["knowledge_items"] == 1


@pytest.mark.asyncio
async def test_global_forget_removes_all_generated_markdown_but_keeps_audit(
    context: AppContext,
) -> None:
    left = Scope(-101, None)
    right = Scope(-102, 2)
    await context.memory.remember(left, "Forget global left.")
    await context.memory.remember(right, "Forget global right.")
    events_before = (await context.memory.stats())["events"]

    ok, prompt = await context.memory.forget(Scope.global_scope(), user_id=111, confirm=None)
    assert ok is False
    token = prompt.split("confirm=", 1)[1].split(" ", 1)[0]
    ok, _result = await context.memory.forget(Scope.global_scope(), user_id=111, confirm=token)

    assert ok is True
    stats = await context.memory.stats()
    assert stats["knowledge_items"] == 0
    assert stats["embeddings"] == 0
    assert stats["events"] > events_before
    assert "Forget global" not in daily_projection_text(context.settings.memory_dir)
    assert not list((context.settings.memory_dir / "daily").glob("*/*.md"))
    canonical = (context.settings.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "Forget global left." not in canonical
    assert "Forget global right." not in canonical
    events = await context.memory.audit_events(event_type="forget", limit=5)
    assert events and events[0].scope is None


@pytest.mark.asyncio
async def test_note_edit_and_delete_rebuild_daily_and_canonical_markdown(
    context: AppContext,
) -> None:
    scope = Scope(-103, None)
    item_id = await context.memory.remember(scope, "Original note.")

    await context.memory.edit_note(scope, item_id, content="Updated note.")

    daily = daily_projection_text(context.settings.memory_dir)
    canonical = (context.settings.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "Original note." not in daily
    assert daily.count("Updated note.") == 1
    assert "Original note." not in canonical
    assert "Updated note." in canonical

    await context.memory.delete_note(scope, item_id)

    assert "Updated note." not in daily_projection_text(context.settings.memory_dir)
    assert "Updated note." not in (context.settings.memory_dir / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    event_types = {
        event.event_type for event in await context.memory.audit_events(scope=scope, limit=10)
    }
    assert {"manual_note_updated", "manual_note_deleted"} <= event_types


@pytest.mark.asyncio
async def test_memory_item_delete_removes_all_content_projections(context: AppContext) -> None:
    scope = Scope(-104, None)
    item_id = await context.memory.remember(scope, "Delete one item.")

    deleted = await context.memory.delete_memory_item(scope, item_id, deleted_by=111)

    assert deleted.content == "Delete one item."
    assert (await context.memory.stats())["knowledge_items"] == 0
    assert "Delete one item." not in daily_projection_text(context.settings.memory_dir)
    assert "Delete one item." not in (context.settings.memory_dir / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    events = await context.memory.audit_events(
        scope=scope, event_type="memory_item_deleted", limit=5
    )
    assert events and events[0].payload["knowledge_item_id"] == item_id


@pytest.mark.asyncio
async def test_business_transaction_rollback_writes_no_markdown(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    original_commit = AsyncSession.commit

    async def fail_commit(_session: AsyncSession) -> None:
        raise RuntimeError("forced business commit failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="forced business commit failure"):
        await context.memory.remember(Scope(-105, None), "Never committed.")
    monkeypatch.setattr(AsyncSession, "commit", original_commit)

    assert (await context.memory.stats())["knowledge_items"] == 0
    assert "Never committed." not in daily_projection_text(context.settings.memory_dir)
    assert "Never committed." not in (context.settings.memory_dir / "MEMORY.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_projection_failure_is_audited_without_memory_content(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await context.memory.remember(Scope(-106, None), "Sensitive committed memory.")

    def fail_projection(_items: list[KnowledgeItem]) -> list[Path]:
        raise OSError("forced projection failure")

    monkeypatch.setattr(context.memory.markdown, "rebuild_daily", fail_projection)
    with pytest.raises(OSError, match="forced projection failure"):
        await context.memory.rebuild_daily_projection()

    events = await context.memory.audit_events(event_type="markdown_projection_failed", limit=5)
    assert events
    assert events[0].payload == {"error_type": "OSError"}
    assert "Sensitive committed memory" not in json.dumps(events[0].payload)


@pytest.mark.asyncio
async def test_import_export_jsonl_round_trip(context: AppContext, tmp_path) -> None:
    scope = Scope(-200, None)
    path = tmp_path / "import.jsonl"
    path.write_text(
        json.dumps({"content": "Imported note about backups.", "source_message_ids": [5]}) + "\n",
        encoding="utf-8",
    )
    count = await context.memory.import_jsonl(path, scope)
    assert count == 1
    exported = await context.memory.export_memory(scope, fmt="jsonl")
    assert "Imported note about backups" in exported
    assert '"scope": "-200:main"' in exported


@pytest.mark.asyncio
async def test_jsonl_import_streams_one_thousand_rows_in_fixed_batches(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-201, None)
    path = tmp_path / "bulk.jsonl"
    path.write_text(
        "".join(
            json.dumps({"content": f"Bulk note {index}.", "source_message_ids": [index]}) + "\n"
            for index in range(1_000)
        ),
        encoding="utf-8",
    )
    original_embed = context.memory.embedding_provider.embed
    calls: list[int] = []

    async def record_embed(texts) -> list[list[float]]:
        calls.append(len(texts))
        return await original_embed(texts)

    monkeypatch.setattr(context.memory.embedding_provider, "embed", record_embed)

    imported = await context.memory.import_jsonl(path, scope)

    assert imported == 1_000
    assert calls == [transfer_module.IMPORT_BATCH_SIZE] * 10
    checkpoints = await context.memory.audit_events(
        event_type="jsonl_import_batch", scope=scope, limit=20
    )
    assert len(checkpoints) == 10
    assert checkpoints[0].payload["checkpoint_imported"] == 1_000


@pytest.mark.asyncio
async def test_jsonl_import_preserves_committed_batches_and_original_line_number(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transfer_module, "IMPORT_BATCH_SIZE", 2)
    scope = Scope(-202, None)
    path = tmp_path / "partial.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"content": "Committed first."}),
                json.dumps({"content": "Committed second."}),
                '{"content": ',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="malformed JSONL at line 3"):
        await context.memory.import_jsonl(path, scope)

    items = await context.memory.browse_items(scope)
    assert {item.content for item in items} == {"Committed first.", "Committed second."}
    failures = await context.memory.audit_events(event_type="jsonl_import_failed", scope=scope)
    assert failures[0].payload["imported"] == 2
    assert failures[0].payload["completed_batches"] == 1
    assert "content" not in failures[0].payload

    with pytest.raises(ValueError, match="malformed JSONL at line 3"):
        await context.memory.import_jsonl(path, scope)
    assert len(await context.memory.browse_items(scope)) == 2


@pytest.mark.asyncio
async def test_export_memory_uses_keyset_chunks_and_streams_global_markdown(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-203, None)
    for index in range(5):
        await context.memory.remember(scope, f"Export item {index}.")
    monkeypatch.setattr(transfer_module, "EXPORT_QUERY_BATCH_SIZE", 2)
    cursors: list[int] = []
    original_list_page = KnowledgeRepository.list_page

    async def record_list_page(self, scope=None, *, after_id=0, limit):
        cursors.append(after_id)
        return await original_list_page(self, scope, after_id=after_id, limit=limit)

    monkeypatch.setattr(KnowledgeRepository, "list_page", record_list_page)
    chunks = [chunk.text async for chunk in context.memory.export_memory_chunks(scope, fmt="jsonl")]

    assert len(cursors) == 4
    assert cursors == sorted(cursors)
    assert len("".join(chunks).splitlines()) == 5
    exported_ids = [json.loads(line)["id"] for line in "".join(chunks).splitlines()]
    assert exported_ids == sorted(exported_ids)
    assert all(len(chunk) <= transfer_module.EXPORT_TEXT_CHUNK_CHARS for chunk in chunks)

    expected_main = context.memory.markdown.main_path.read_text(encoding="utf-8")

    def reject_full_read() -> str:
        raise AssertionError("global export must stream the canonical file")

    monkeypatch.setattr(context.memory.markdown, "read_main", reject_full_read)
    streamed_main = "".join(
        [chunk.text async for chunk in context.memory.export_memory_chunks(None, fmt="md")]
    )
    assert streamed_main == expected_main


@pytest.mark.asyncio
async def test_jsonl_import_enforces_file_and_row_limits_with_checkpoint_semantics(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-204, None)
    oversized = tmp_path / "oversized.jsonl"
    oversized.write_text(json.dumps({"content": "Too large."}) + "\n", encoding="utf-8")
    monkeypatch.setattr(transfer_module, "MAX_IMPORT_FILE_BYTES", 1)

    with pytest.raises(ValueError, match="file limit"):
        await context.memory.import_jsonl(oversized, scope)
    assert (await context.memory.memory_status(scope)).knowledge_items == 0

    monkeypatch.setattr(transfer_module, "MAX_IMPORT_FILE_BYTES", 512 * 1024 * 1024)
    monkeypatch.setattr(transfer_module, "MAX_IMPORT_ROWS", 2)
    monkeypatch.setattr(transfer_module, "IMPORT_BATCH_SIZE", 2)
    row_limited = tmp_path / "row-limited.jsonl"
    row_limited.write_text(
        "".join(json.dumps({"content": f"Limited {index}."}) + "\n" for index in range(3)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="2-row limit"):
        await context.memory.import_jsonl(row_limited, scope)
    assert (await context.memory.memory_status(scope)).knowledge_items == 2
