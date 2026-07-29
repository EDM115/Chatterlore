from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import event, select, text

from pzp_ai_bot.ai.fake import FakeProvider
from pzp_ai_bot.app_context import AppContext, build_context
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import AIUsage, ForgetChallenge
from pzp_ai_bot.db.repositories import EventRepository, utc_now
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.main import sqlite_stats
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import (
    IncomingMessage,
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
async def test_ai_usage_stats_aggregate_in_sql_with_scope_hierarchy(context: AppContext) -> None:
    main_scope = Scope(-100901, None)
    topic_scope = Scope(-100901, 7)
    other_scope = Scope(-100902, None)
    async with session_scope(context.session_factory) as session:
        repository = EventRepository(session)
        await repository.add_ai_usage(
            scope=main_scope,
            provider="fake",
            model="fake-chat",
            operation="ask",
            latency_ms=10,
            metadata_json={"scope": main_scope.key},
        )
        await repository.add_ai_usage(
            scope=topic_scope,
            provider="fake",
            model="fake-embedding",
            operation="embed_query",
            latency_ms=30,
            error="RuntimeError: provider operation failed",
            metadata_json={"scope": topic_scope.key},
        )
        await repository.add_ai_usage(
            scope=other_scope,
            provider="fake",
            model="fake-chat",
            operation="ask",
            latency_ms=50,
            metadata_json={"scope": other_scope.key},
        )

    statements: list[str] = []

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        if "ai_usage" in statement.lower():
            statements.append(statement)

    engine = context.session_factory.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
    try:
        async with session_scope(context.session_factory) as session:
            repository = EventRepository(session)
            chat_stats = await repository.ai_usage_stats(main_scope)
            topic_stats = await repository.ai_usage_stats(topic_scope)
            global_stats = await repository.ai_usage_stats()
            rows = list((await session.scalars(select(AIUsage).order_by(AIUsage.id))).all())
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert chat_stats.total == 2
    assert chat_stats.errors == 1
    assert chat_stats.average_latency_ms == 20
    assert chat_stats.operations == {"ask": 1, "embed_query": 1}
    assert topic_stats.total == 1
    assert topic_stats.operations == {"embed_query": 1}
    assert global_stats.total == 3
    assert [(row.chat_id, row.message_thread_id) for row in rows] == [
        (main_scope.chat_id, None),
        (topic_scope.chat_id, topic_scope.thread_id),
        (other_scope.chat_id, None),
    ]
    aggregate_statements = [
        statement for statement in statements if "select ai_usage.id" not in statement.lower()
    ]
    assert len(aggregate_statements) == 6
    assert all("count(" in statement.lower() for statement in aggregate_statements)


@pytest.mark.asyncio
async def test_telemetry_query_plans_use_scope_and_type_indexes(context: AppContext) -> None:
    engine = context.session_factory.kw["bind"]
    async with engine.connect() as connection:
        usage_plan = (
            await connection.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT count(id) FROM ai_usage "
                    "WHERE chat_id = -100901 ORDER BY created_at DESC"
                )
            )
        ).all()
        event_plan = (
            await connection.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT id FROM bot_events "
                    "WHERE event_type = 'forget' AND chat_id = -100901 "
                    "ORDER BY created_at DESC LIMIT 10"
                )
            )
        ).all()

    assert "ix_ai_usage_scope_created_at" in " ".join(str(row) for row in usage_plan)
    assert "ix_bot_events_type_scope_created_at" in " ".join(str(row) for row in event_plan)


@pytest.mark.asyncio
async def test_provider_operations_persist_normalized_usage_scope(context: AppContext) -> None:
    scope = Scope(-100903, 9)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_scope_settings(scope, {"summary_batch_size": 1}, updated_by=111)
    result = await context.memory.ingest(
        IncomingMessage(
            telegram_message_id=1,
            scope=scope,
            user_id=222,
            username="telemetry",
            text="Decision: normalized telemetry scope stays queryable.",
            created_at=utc_now(),
        )
    )
    assert result.summarized is True
    await context.memory.search(scope, "normalized telemetry")
    await context.memory.ask(scope, "What stays queryable?")

    async with session_scope(context.session_factory) as session:
        rows = list((await session.scalars(select(AIUsage).order_by(AIUsage.id))).all())

    operations = {row.operation for row in rows}
    assert {
        "ask",
        "canonical_compact",
        "embed_knowledge",
        "embed_message",
        "embed_query",
        "summarize",
    } <= operations
    assert all(
        (row.chat_id, row.message_thread_id) == (scope.chat_id, scope.thread_id) for row in rows
    )


@pytest.mark.asyncio
async def test_persisted_forget_confirmation_survives_context_restart(
    context: AppContext, test_settings: Settings
) -> None:
    scope = Scope(-505, 5)
    await context.memory.remember(scope, "Operations forget target", source_message_id=10)

    ok, prompt = await context.memory.forget(scope, user_id=111, confirm=None)
    assert ok is False
    token = confirmation_token(prompt)

    restarted = await build_context(test_settings, ensure_schema=False)
    ok, result = await restarted.memory.forget(scope, user_id=111, confirm=token)

    assert ok is True
    assert "Forgot scope -505:5" in result
    assert not await restarted.memory.search(scope, "Operations forget target")


@pytest.mark.asyncio
async def test_context_startup_reconciles_daily_projection_without_provider_calls(
    context: AppContext, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-504, 4)
    await context.memory.remember(scope, "Committed startup memory.")
    generated_path = next(test_settings.memory_dir.glob("daily/*/*.md"))
    generated_path.write_text(
        generated_path.read_text(encoding="utf-8") + "\nPrivate rejected proposal.\n",
        encoding="utf-8",
    )
    stale_path = test_settings.memory_dir / "daily" / "2026-01-01" / "-999_main.md"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text("Deleted historical memory.", encoding="utf-8")

    async def reject_provider_call(_provider: FakeProvider, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("startup projection must not contact a provider")

    monkeypatch.setattr(FakeProvider, "generate", reject_provider_call)
    restarted = await build_context(test_settings, ensure_schema=False)

    daily = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(restarted.settings.memory_dir.glob("daily/*/*.md"))
    )
    assert daily.count("Committed startup memory.") == 1
    assert "Private rejected proposal." not in daily
    assert "Deleted historical memory." not in daily
    assert not stale_path.exists()


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
        {
            "top_k": "9",
            "candidate_limit": "1234",
            "answer_min_score": "0.25",
            "learning_default_enabled": "on",
        }
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
        "retrieval_candidate_limit",
        "retrieval_top_k",
    }
    assert context.memory.runtime_setting("retrieval_top_k") == 9
    assert context.memory.runtime_setting("retrieval_candidate_limit") == 1234
    assert context.memory.runtime_setting("answer_min_score") == 0.25
    assert context.memory.runtime_setting("learning_default_enabled") is True
    assert effective.retrieval_top_k == 9
    assert "retrieval_candidate_limit: 1234" in format_runtime_settings(settings)
    assert "retrieval_top_k: 9" in format_runtime_settings(settings)
    assert events and events[0].payload["updates"]["retrieval_top_k"] == 9
    assert json.loads(jsonl.splitlines()[0])["event_type"] == "runtime_settings_update"

    with pytest.raises(ValueError, match="retrieval_candidate_limit must be >= 1"):
        await context.memory.set_runtime_settings({"retrieval_candidate_limit": 0})


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
async def test_sqlite_stats_include_operations_tables(context: AppContext) -> None:
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


@pytest.mark.asyncio
async def test_background_loop_recovers_with_bounded_content_free_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[int] = []
    warnings: list[tuple[str, dict[str, Any]]] = []

    class MemoryDouble:
        calls = 0

        async def refresh_runtime_settings(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("SECRET-PROVIDER-DETAIL")
            if self.calls == 3:
                raise asyncio.CancelledError

    async def fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    memory = MemoryDouble()
    context = SimpleNamespace(
        settings=SimpleNamespace(runtime_settings_refresh_seconds=4), memory=memory
    )
    state = scheduler_module.BackgroundTaskState(name="pzp-runtime-settings-refresher")
    monkeypatch.setattr(scheduler_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        scheduler_module,
        "logger",
        SimpleNamespace(warning=lambda event, **values: warnings.append((event, values))),
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler_module.runtime_settings_refresher_loop(cast(Any, context), state=state)

    assert memory.calls == 3
    assert sleeps == [4, 1, 4]
    assert state.running is False
    assert state.last_success_at is not None
    assert state.last_failure_at is not None
    assert state.last_exception_type is None
    assert state.consecutive_failures == 0
    assert warnings == [
        (
            "background_task_iteration_failed",
            {
                "event_type": "background_task_iteration_failed",
                "task": "pzp-runtime-settings-refresher",
                "exception_type": "RuntimeError",
                "consecutive_failures": 1,
                "backoff_seconds": 1,
            },
        )
    ]
    assert "SECRET-PROVIDER-DETAIL" not in json.dumps(warnings)


@pytest.mark.asyncio
async def test_background_loop_caps_repeated_failure_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[int] = []

    class MemoryDouble:
        calls = 0

        async def refresh_runtime_settings(self) -> None:
            self.calls += 1
            if self.calls == 4:
                raise asyncio.CancelledError
            raise OSError("do not log this filesystem detail")

    async def fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    context = SimpleNamespace(
        settings=SimpleNamespace(runtime_settings_refresh_seconds=3), memory=MemoryDouble()
    )
    state = scheduler_module.BackgroundTaskState(name="pzp-runtime-settings-refresher")
    monkeypatch.setattr(scheduler_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        scheduler_module, "logger", SimpleNamespace(warning=lambda event, **values: None)
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler_module.runtime_settings_refresher_loop(cast(Any, context), state=state)

    assert sleeps == [3, 1, 2, 3]
    assert state.consecutive_failures == 3
    assert state.last_exception_type == "OSError"


@pytest.mark.asyncio
async def test_background_loop_cancellation_during_sleep_is_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MemoryDouble:
        async def refresh_runtime_settings(self) -> None:
            raise AssertionError("work must not run after sleep cancellation")

    async def cancelled_sleep(delay: int) -> None:
        raise asyncio.CancelledError

    context = SimpleNamespace(
        settings=SimpleNamespace(runtime_settings_refresh_seconds=3), memory=MemoryDouble()
    )
    state = scheduler_module.BackgroundTaskState(name="pzp-runtime-settings-refresher")
    monkeypatch.setattr(scheduler_module.asyncio, "sleep", cancelled_sleep)

    with pytest.raises(asyncio.CancelledError):
        await scheduler_module.runtime_settings_refresher_loop(cast(Any, context), state=state)

    assert state.running is False
    assert state.consecutive_failures == 0


@pytest.mark.asyncio
async def test_stop_task_handles_running_cancelled_and_failed_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        scheduler_module,
        "logger",
        SimpleNamespace(warning=lambda event, **values: warnings.append((event, values))),
    )

    blocker = asyncio.Event()

    async def wait_forever() -> None:
        await blocker.wait()

    running_state = scheduler_module.BackgroundTaskState(name="running", running=True)
    running_handle = scheduler_module.BackgroundTaskHandle(
        task=asyncio.create_task(wait_forever()), state=running_state
    )
    await scheduler_module.stop_task(running_handle)
    assert running_handle.task.cancelled()
    assert running_state.running is False

    async def fail_now() -> None:
        raise RuntimeError("SECRET-FAILED-TASK-DETAIL")

    failed_state = scheduler_module.BackgroundTaskState(name="failed", running=True)
    failed_handle = scheduler_module.BackgroundTaskHandle(
        task=asyncio.create_task(fail_now()), state=failed_state
    )
    await asyncio.sleep(0)
    await scheduler_module.stop_task(failed_handle)
    assert failed_state.running is False
    assert warnings[-1] == (
        "background_task_stopped_unexpectedly",
        {
            "event_type": "background_task_stopped_unexpectedly",
            "task": "failed",
            "exception_type": "RuntimeError",
        },
    )
    assert "SECRET-FAILED-TASK-DETAIL" not in json.dumps(warnings)
