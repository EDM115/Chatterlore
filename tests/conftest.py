from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from pzp_ai_bot.app_context import AppContext, build_context
from pzp_ai_bot.config import Settings


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        bot_admins="111",
        webhook_url="https://example.test/telegram/webhook",
        webhook_secret="secret",  # noqa: S106
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}",
        memory_dir=tmp_path / "memory",
        ai_provider="fake",
        learning_default_enabled=False,
        summary_batch_size=2,
    )


@pytest.fixture
async def context(test_settings: Settings) -> AsyncIterator[AppContext]:
    ctx = await build_context(test_settings)
    yield ctx
