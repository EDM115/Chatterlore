from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import IncomingMessage


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
