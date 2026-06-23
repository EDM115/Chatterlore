from __future__ import annotations

import json

import pytest

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import Scope


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
async def test_forget_requires_confirmation_and_deletes_scope(context: AppContext) -> None:
    scope = Scope(-100, 1)
    await context.memory.remember(scope, "Delete me.")
    ok, prompt = await context.memory.forget(scope, user_id=111, confirm=None)
    assert ok is False
    token = prompt.split("confirm=", 1)[1].split(" ", 1)[0]
    ok, result = await context.memory.forget(scope, user_id=111, confirm=token)
    assert ok is True
    assert "Forgot scope" in result
    assert not await context.memory.search(scope, "Delete me")


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
