from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

import pzp_ai_bot.db.session as database_session
from pzp_ai_bot.app_context import AppContext, build_context
from pzp_ai_bot.config import Settings


@pytest.fixture(autouse=True)
async def dispose_test_database_engines(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    engines: list[AsyncEngine] = []
    create_engine = database_session.create_engine

    def create_owned_engine(settings: Settings) -> AsyncEngine:
        engine = create_engine(settings)
        engines.append(engine)
        return engine

    monkeypatch.setattr(database_session, "create_engine", create_owned_engine)
    try:
        yield
    finally:
        # Synchronous API fixtures also create contexts outside the async context fixture
        for engine in engines:
            await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def isolate_settings_from_repository_dotenv() -> Iterator[None]:
    original_env_file = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    yield
    Settings.model_config["env_file"] = original_env_file


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        bot_admins="111",
        webhook_url="https://example.test/telegram/webhook",
        webhook_secret="test-webhook-token",  # noqa: S106
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}",
        memory_dir=tmp_path / "memory",
        ai_provider="fake",
        control_plane_enabled=False,
        learning_default_enabled=False,
        summary_batch_size=2,
    )


@pytest.fixture
async def context(test_settings: Settings) -> AsyncIterator[AppContext]:
    ctx = await build_context(test_settings)
    try:
        yield ctx
    finally:
        await ctx.session_factory.kw["bind"].dispose()
