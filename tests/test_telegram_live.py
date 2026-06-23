from __future__ import annotations

import os

import pytest
from aiogram import Bot

pytestmark = pytest.mark.telegram_live


@pytest.mark.skipif(
    not os.getenv("TELEGRAM_LIVE_BOT_TOKEN") or not os.getenv("TELEGRAM_LIVE_CHAT_ID"),
    reason="set TELEGRAM_LIVE_BOT_TOKEN and TELEGRAM_LIVE_CHAT_ID to run live Telegram smoke tests",
)
@pytest.mark.asyncio
async def test_telegram_live_get_me() -> None:
    bot = Bot(os.environ["TELEGRAM_LIVE_BOT_TOKEN"])
    try:
        me = await bot.get_me()
        assert me.id > 0
    finally:
        await bot.session.close()
