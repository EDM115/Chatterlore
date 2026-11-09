from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from chatterlore.app_context import AppContext
from chatterlore.db.repositories import PolicyRepository
from chatterlore.db.session import session_scope
from chatterlore.memory.policy import PolicyService
from chatterlore.memory.scope import Scope
from chatterlore.memory.service import IncomingMessage


def incoming(scope: Scope, text: str, user_id: int = 222, message_id: int = 1) -> IncomingMessage:
    return IncomingMessage(
        telegram_message_id=message_id,
        scope=scope,
        user_id=user_id,
        username="user",
        text=text,
        created_at=datetime(2026, 6, 16, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_learning_disabled_skips_message(context: AppContext) -> None:
    result = await context.memory.ingest(incoming(Scope(-100), "Project Apollo uses SQLite."))
    assert result.learned is False
    assert result.reason == "learning_disabled"
    stats = await context.memory.stats()
    assert stats["messages"] == 1
    assert stats["learned"] == 0


@pytest.mark.asyncio
async def test_learning_on_allows_and_summarizes(context: AppContext) -> None:
    scope = Scope(-100, 7)
    await context.memory.set_learning(scope, True, updated_by=111)
    one = await context.memory.ingest(incoming(scope, "Project Apollo uses SQLite.", message_id=1))
    two = await context.memory.ingest(
        incoming(scope, "Deployment should use polling locally.", message_id=2)
    )
    assert one.learned is True
    assert two.learned is True
    assert two.summarized is True
    memory = (context.settings.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "Project Apollo uses SQLite" in memory
    assert "source" in memory


@pytest.mark.asyncio
async def test_user_deny_overrides_learning(context: AppContext) -> None:
    scope = Scope(-100)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_user_policy(scope, 222, allowed=False, updated_by=111)
    result = await context.memory.ingest(
        incoming(scope, "This should not be learned.", user_id=222)
    )
    assert result.learned is False
    assert result.reason == "user_denied"


@pytest.mark.asyncio
async def test_user_policy_changes_replace_the_opposite_rule(context: AppContext) -> None:
    scope = Scope(-100, 7)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_user_policy(scope, 222, allowed=False, updated_by=111)
    denied = await context.memory.ingest(incoming(scope, "Denied first.", message_id=1))
    assert denied.learned is False
    assert denied.reason == "user_denied"

    await context.memory.set_user_policy(scope, 222, allowed=True, updated_by=111)
    allowed = await context.memory.ingest(incoming(scope, "Allowed again.", message_id=2))
    assert allowed.learned is True
    assert allowed.reason == "allowed"

    await context.memory.set_user_policy(scope, 222, allowed=False, updated_by=111)
    denied_again = await context.memory.ingest(incoming(scope, "Denied again.", message_id=3))
    assert denied_again.learned is False
    assert denied_again.reason == "user_denied"

    other_author = await context.memory.ingest(
        incoming(scope, "Other authors remain allowed.", user_id=333, message_id=4)
    )
    assert other_author.learned is True
    assert other_author.reason == "allowed"


@pytest.mark.parametrize("ancestor", [Scope.global_scope(), Scope(-100)])
@pytest.mark.asyncio
async def test_user_policy_local_allow_preserves_ancestor_deny(
    context: AppContext, ancestor: Scope
) -> None:
    scope = Scope(-100, 7)
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.set_user_policy(ancestor, 222, allowed=False, updated_by=111)
    await context.memory.set_user_policy(scope, 222, allowed=False, updated_by=111)
    await context.memory.set_user_policy(scope, 222, allowed=True, updated_by=111)

    denied = await context.memory.ingest(incoming(scope, "Still denied by ancestor.", message_id=1))
    assert denied.learned is False
    assert denied.reason == "user_denied"

    await context.memory.set_user_policy(ancestor, 222, allowed=True, updated_by=111)
    allowed = await context.memory.ingest(incoming(scope, "All denials reversed.", message_id=2))
    assert allowed.learned is True
    assert allowed.reason == "allowed"


@pytest.mark.asyncio
async def test_policy_snapshot_preserves_precedence_allowlist_and_reason_semantics(
    context: AppContext,
) -> None:
    chat_scope = Scope(-200, None)
    topic_scope = Scope(-200, 7)
    other_scope = Scope(-201, None)
    async with session_scope(context.session_factory) as session:
        repository = PolicyRepository(session)
        await repository.set_policy(
            policy_type="global_learning", enabled=True, scope=Scope.global_scope()
        )
        await repository.set_policy(policy_type="chat_learning", enabled=False, scope=chat_scope)
        await repository.set_policy(policy_type="topic_learning", enabled=True, scope=topic_scope)
        await repository.set_policy(
            policy_type="user_deny", enabled=True, scope=chat_scope, user_id=333
        )
        await repository.set_policy(
            policy_type="user_allow", enabled=True, scope=topic_scope, user_id=222
        )
        await repository.set_policy(policy_type="allow_admins", enabled=True, scope=chat_scope)
        policies = PolicyService(context.settings, repository)
        snapshot = await policies.load_snapshot()

        assert snapshot.scope_enabled(other_scope, default=False) is True
        assert snapshot.scope_enabled(chat_scope, default=True) is False
        assert snapshot.scope_enabled(topic_scope, default=False) is True
        assert snapshot.user_denied(topic_scope, 333) is True
        assert snapshot.has_allowlist(topic_scope) is True
        assert snapshot.user_allowed(topic_scope, 222) is True
        assert snapshot.admins_allowed(topic_scope) is True
        cases = [
            (chat_scope, 222, False, "learning_disabled"),
            (topic_scope, None, False, "missing_user"),
            (topic_scope, 333, False, "user_denied"),
            (topic_scope, 222, False, "allowed"),
            (topic_scope, 444, True, "allowed_admin"),
            (topic_scope, 444, False, "user_not_allowed"),
            (other_scope, 444, False, "allowed"),
        ]
        for scope, user_id, is_chat_admin, reason in cases:
            decision = await policies.decide(
                scope=scope,
                user_id=user_id,
                is_bot=False,
                is_command=False,
                text="learn me",
                is_chat_admin=is_chat_admin,
            )
            assert decision.reason == reason


@pytest.mark.asyncio
async def test_policy_decision_and_status_each_select_policies_once(context: AppContext) -> None:
    scope = Scope(-300, 9)
    await context.memory.set_learning(scope, True, updated_by=111)
    engine = context.session_factory.kw["bind"]
    assert isinstance(engine, AsyncEngine)
    policy_selects = 0

    def count_policy_select(
        _connection, _cursor, statement: str, _parameters, _context, _executemany
    ) -> None:
        nonlocal policy_selects
        if statement.lstrip().upper().startswith("SELECT") and "learning_policies" in statement:
            policy_selects += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_policy_select)
    try:
        async with session_scope(context.session_factory) as session:
            policies = PolicyService(context.settings, PolicyRepository(session))
            decision = await policies.decide(
                scope=scope, user_id=222, is_bot=False, is_command=False, text="learn me"
            )
        assert decision.reason == "allowed"
        assert policy_selects == 1

        policy_selects = 0
        async with session_scope(context.session_factory) as session:
            status = await PolicyService(context.settings, PolicyRepository(session)).status(scope)
        assert status["learning_enabled"] is True
        assert policy_selects == 1
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_policy_select)


@pytest.mark.asyncio
async def test_known_scopes_reuses_one_policy_snapshot(context: AppContext) -> None:
    scopes = [Scope(-400 - index, index or None) for index in range(6)]
    for index, scope in enumerate(scopes):
        await context.memory.set_learning(scope, index % 2 == 0, updated_by=111)
    engine = context.session_factory.kw["bind"]
    assert isinstance(engine, AsyncEngine)
    policy_selects = 0

    def count_policy_select(
        _connection, _cursor, statement: str, _parameters, _context, _executemany
    ) -> None:
        nonlocal policy_selects
        if statement.lstrip().upper().startswith("SELECT") and "learning_policies" in statement:
            policy_selects += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_policy_select)
    try:
        previews = await context.memory.known_scopes()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_policy_select)

    preview_by_scope = {preview.scope.key: preview for preview in previews}
    assert policy_selects == 1
    assert {scope.key for scope in scopes}.issubset(preview_by_scope)
    for index, scope in enumerate(scopes):
        assert preview_by_scope[scope.key].learning_enabled is (index % 2 == 0)


@pytest.mark.asyncio
async def test_policy_mutation_is_visible_to_the_next_decision(context: AppContext) -> None:
    scope = Scope(-500, None)
    await context.memory.set_learning(scope, True, updated_by=111)
    async with session_scope(context.session_factory) as session:
        allowed = await PolicyService(context.settings, PolicyRepository(session)).decide(
            scope=scope, user_id=222, is_bot=False, is_command=False, text="first decision"
        )

    await context.memory.set_learning(scope, False, updated_by=111)
    async with session_scope(context.session_factory) as session:
        denied = await PolicyService(context.settings, PolicyRepository(session)).decide(
            scope=scope, user_id=222, is_bot=False, is_command=False, text="next decision"
        )

    assert allowed.reason == "allowed"
    assert denied.reason == "learning_disabled"
