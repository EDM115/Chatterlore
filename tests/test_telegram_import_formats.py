# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from html import escape
from pathlib import Path

import pytest
from sqlalchemy import select

import chatterlore.memory.transfer as transfer_module
from chatterlore.app_context import AppContext
from chatterlore.db.models import Message
from chatterlore.db.session import session_scope
from chatterlore.memory.scope import Scope


def write_formats(
    path: Path, texts: list[str], *, thread_id: int | None = None
) -> tuple[Path, Path]:
    json_path = path / "result.json"
    html_path = path / "messages.html"
    raw_rows = [
        {
            "id": index,
            "type": "message",
            "date": "2026-11-07T18:18:44+00:00",
            "from": "Dana Example",
            "from_id": "user222",
            "text": text,
            **({"message_thread_id": thread_id} if thread_id is not None else {}),
        }
        for index, text in enumerate(texts, 1)
    ]
    json_path.write_text(
        json.dumps({"name": "Import Fixture", "messages": raw_rows}), encoding="utf-8"
    )
    html_path.write_text(
        '<html><body><div class="page_header"><div class="text bold">Import Fixture</div></div><div class="page_body chat_page"><div class="history">'
        + "".join(
            f'<div class="message default" id="message{index}"><div class="body"><div class="date details" title="07.11.2026 19:18:44 UTC+01:00">19:18</div><div class="from_name">Dana Example</div><div class="text">{escape(text)}</div></div></div>'
            for index, text in enumerate(texts, 1)
        )
        + "</div></div></body></html>",
        encoding="utf-8",
    )
    return json_path, html_path


@pytest.mark.asyncio
async def test_html_and_json_share_logical_ids_source_hashes_and_topic_isolation(
    context: AppContext, tmp_path: Path
) -> None:
    json_path, html_path = write_formats(tmp_path, ["PRIVATE_TEST marker Silver Otter 9731"])
    scope = Scope(-660, 5)

    first = await context.memory.import_telegram_export(html_path, scope)
    again = await context.memory.import_telegram_export(json_path, scope)

    assert (first.imported, again.imported, again.skipped) == (1, 0, 1)
    async with session_scope(context.session_factory) as database:
        [row] = (await database.scalars(select(Message))).all()
        assert row.message_thread_id == 5
        assert row.user_id is None
        assert row.username == "Dana Example"
        assert row.created_at.replace(tzinfo=UTC) == datetime(2026, 11, 7, 18, 18, 44, tzinfo=UTC)
        assert row.embedding_source_hash is not None
    assert "Silver Otter 9731" in (await context.memory.search(scope, "marker"))[0].content
    assert await context.memory.search(Scope(-660, 3), "marker") == []
    assert len(await context.memory.search(Scope(-660, None), "marker")) == 1
    events = await context.memory.audit_events(scope=scope, event_type="telegram_html_import_batch")
    assert events[0].payload["imported"] == 1
    assert "PRIVATE_TEST" not in json.dumps(events[0].payload)


@pytest.mark.asyncio
async def test_export_directory_prefers_json_and_falls_back_to_html_only(
    context: AppContext, tmp_path: Path
) -> None:
    json_path, html_path = write_formats(tmp_path, ["JSON is the preferred source"])
    html_path.write_text("not valid HTML", encoding="utf-8")

    preferred = await context.memory.import_telegram_export(tmp_path, Scope(-661, None))

    assert preferred.imported == 1
    write_formats(tmp_path, ["HTML-only archive"])
    json_path.unlink()
    fallback = await context.memory.import_telegram_export(tmp_path, Scope(-662, 1))
    assert fallback.imported == 1
    assert (
        "HTML-only archive" in (await context.memory.search(Scope(-662, 1), "archive"))[0].content
    )


@pytest.mark.asyncio
async def test_whole_forum_import_is_aggregate_only_and_never_guesses_from_replies(
    context: AppContext, tmp_path: Path
) -> None:
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "name": "Forum",
                "messages": [
                    {
                        "id": 3,
                        "type": "service",
                        "action": "topic_created",
                        "title": "News",
                        "text": "",
                    },
                    {
                        "id": 4,
                        "type": "message",
                        "date": "2026-11-07T18:18:44Z",
                        "reply_to_message_id": 3,
                        "text": "A cross-topic reply does not establish ownership",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = await context.memory.import_telegram_export(path, Scope(-663, None))

    assert result.imported == 1
    assert await context.memory.search(Scope(-663, 3), "ownership") == []
    assert len(await context.memory.search(Scope(-663, None), "ownership")) == 1


@pytest.mark.asyncio
async def test_explicit_source_thread_conflict_fails_before_any_provider_call_or_message_write(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_path, _ = write_formats(tmp_path, ["Must not move this source"], thread_id=5)

    async def forbidden_embed(_texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("scope validation must precede inference")

    monkeypatch.setattr(context.memory.embedding_provider, "embed", forbidden_embed)
    with pytest.raises(ValueError, match=r"thread.*target|target.*topic"):
        await context.memory.import_telegram_export(json_path, Scope(-664, 3))
    assert (await context.memory.memory_status(Scope(-664, None))).messages == 0


@pytest.mark.asyncio
async def test_html_batches_commit_before_later_failure_and_retry_across_formats(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transfer_module, "IMPORT_BATCH_SIZE", 2)
    json_path, html_path = write_formats(tmp_path, [f"Batch source {index}" for index in range(5)])
    original_embed = context.memory.embedding_provider.embed
    database_path = context.session_factory.kw["bind"].url.database
    calls = 0

    async def checked_embed(texts: Sequence[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        assert len(texts) <= 2
        with sqlite3.connect(database_path, timeout=0.05) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        if calls == 2:
            raise RuntimeError("PRIVATE_TEST provider failure")
        return await original_embed(texts)

    monkeypatch.setattr(context.memory.embedding_provider, "embed", checked_embed)
    with pytest.raises(RuntimeError, match="provider failure"):
        await context.memory.import_telegram_export(html_path, Scope(-665, None))
    assert (await context.memory.memory_status(Scope(-665, None))).messages == 2
    failures = await context.memory.audit_events(
        scope=Scope(-665, None), event_type="telegram_html_import_failed"
    )
    assert failures[0].payload["completed_batches"] == 1
    assert "PRIVATE_TEST" not in json.dumps(failures[0].payload)
    monkeypatch.setattr(context.memory.embedding_provider, "embed", original_embed)
    retry = await context.memory.import_telegram_export(json_path, Scope(-665, None))
    assert (retry.imported, retry.skipped) == (3, 2)
    assert (await context.memory.memory_status(Scope(-665, None))).messages == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", [5.5, True, "not-a-thread"])
async def test_noninteger_source_threads_are_not_coerced_into_a_target(
    context: AppContext, tmp_path: Path, thread_id
) -> None:
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "text": "Invalid source metadata",
                        "message_thread_id": thread_id,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="positive integer"):
        await context.memory.import_telegram_export(path, Scope(-666, 5))
    assert (await context.memory.memory_status(Scope(-666, None))).messages == 0
