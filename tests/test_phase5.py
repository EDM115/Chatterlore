from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import select

from pzp_ai_bot.app_context import AppContext, build_context
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import ForgetChallenge
from pzp_ai_bot.db.repositories import utc_now
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.main import sqlite_stats
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import (
    audit_events_jsonl,
    format_runtime_settings,
    parse_runtime_settings_args,
)
from pzp_ai_bot.telegram import scheduler as scheduler_module

TOKEN_RE = re.compile(r"confirm=([^ ]+)")


def confirmation_token(prompt: str) -> str:
    match = TOKEN_RE.search(prompt)
    assert match, prompt
    return match.group(1)


@pytest.mark.asyncio
async def test_persisted_forget_confirmation_survives_context_restart(
    context: AppContext, test_settings: Settings
) -> None:
    scope = Scope(-505, 5)
    await context.memory.remember(scope, "Phase 5 forget target", source_message_id=10)

    ok, prompt = await context.memory.forget(scope, user_id=111, confirm=None)
    assert ok is False
    token = confirmation_token(prompt)

    restarted = await build_context(test_settings, ensure_schema=False)
    ok, result = await restarted.memory.forget(scope, user_id=111, confirm=token)

    assert ok is True
    assert "Forgot scope -505:5" in result
    assert not await restarted.memory.search(scope, "Phase 5 forget target")


@pytest.mark.asyncio
async def test_expired_forget_confirmation_does_not_delete_scope(context: AppContext) -> None:
    scope = Scope(-506, 6)
    await context.memory.remember(
        scope, "Keep this after an expired confirmation.", source_message_id=11
    )

    ok, prompt = await context.memory.forget(scope, user_id=111, confirm=None)
    assert ok is False
    expired_token = confirmation_token(prompt)
    async with session_scope(context.session_factory) as session:
        challenge = await session.scalar(
            select(ForgetChallenge).where(
                ForgetChallenge.chat_id == scope.chat_id,
                ForgetChallenge.message_thread_id == scope.thread_id,
                ForgetChallenge.user_id == 111,
            )
        )
        assert challenge is not None
        challenge.expires_at = utc_now() - timedelta(seconds=1)

    ok, new_prompt = await context.memory.forget(scope, user_id=111, confirm=expired_token)

    assert ok is False
    assert "Confirmation required" in new_prompt
    assert confirmation_token(new_prompt) != expired_token
    assert await context.memory.search(scope, "expired confirmation")


@pytest.mark.asyncio
async def test_runtime_settings_refresh_defaults_and_audit_events(context: AppContext) -> None:
    updates = parse_runtime_settings_args(
        {"top_k": "9", "answer_min_score": "0.25", "learning_default_enabled": "on"}
    )

    changed = await context.memory.set_runtime_settings(updates, updated_by=111)
    await context.memory.refresh_runtime_settings()
    settings = await context.memory.list_runtime_settings()
    effective = await context.memory.get_scope_settings(Scope(-507, None))
    events = await context.memory.audit_events(event_type="runtime_settings_update", limit=5)
    jsonl = audit_events_jsonl(events)

    assert {setting.key for setting in changed} == {
        "answer_min_score",
        "learning_default_enabled",
        "retrieval_top_k",
    }
    assert context.memory.runtime_setting("retrieval_top_k") == 9
    assert context.memory.runtime_setting("answer_min_score") == 0.25
    assert context.memory.runtime_setting("learning_default_enabled") is True
    assert effective.retrieval_top_k == 9
    assert "retrieval_top_k: 9" in format_runtime_settings(settings)
    assert events and events[0].payload["updates"]["retrieval_top_k"] == 9
    assert json.loads(jsonl.splitlines()[0])["event_type"] == "runtime_settings_update"


@pytest.mark.asyncio
async def test_audit_events_can_filter_by_scope(context: AppContext) -> None:
    left = Scope(-508, 1)
    right = Scope(-508, 2)
    await context.memory.set_learning(left, True, updated_by=111)
    await context.memory.set_learning(right, True, updated_by=222)

    events = await context.memory.audit_events(event_type="learning_policy", scope=left)

    assert events
    assert {event.scope for event in events} == {left}
    assert {event.user_id for event in events} == {111}


@pytest.mark.asyncio
async def test_sqlite_stats_include_phase5_tables(context: AppContext) -> None:
    path = context.settings.sqlite_path
    assert path is not None

    stats = sqlite_stats(path)

    runtime_settings_count = stats["table_runtime_settings"]
    forget_challenges_count = stats["table_forget_challenges"]
    bot_events_count = stats["table_bot_events"]
    assert isinstance(runtime_settings_count, int)
    assert isinstance(forget_challenges_count, int)
    assert isinstance(bot_events_count, int)
    assert runtime_settings_count >= 0
    assert forget_challenges_count >= 0
    assert bot_events_count >= 0


@pytest.mark.asyncio
async def test_runtime_settings_refresher_loop_uses_configured_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[int] = []

    class MemoryDouble:
        async def refresh_runtime_settings(self) -> None:
            raise asyncio.CancelledError

    async def fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    context = SimpleNamespace(
        settings=SimpleNamespace(runtime_settings_refresh_seconds=3), memory=MemoryDouble()
    )
    monkeypatch.setattr(scheduler_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await scheduler_module.runtime_settings_refresher_loop(cast(Any, context))

    assert sleeps == [3]
