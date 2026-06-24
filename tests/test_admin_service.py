from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from pzp_ai_bot.telegram.admin import AdminService


class AdminBotDouble:
    def __init__(self) -> None:
        self.calls = 0

    async def get_chat_administrators(self, chat_id: int) -> list[Any]:
        self.calls += 1
        return [SimpleNamespace(user=SimpleNamespace(id=222))]


@pytest.mark.asyncio
async def test_admin_cache_uses_configurable_ttl_and_reports_stats() -> None:
    bot = AdminBotDouble()
    service = AdminService({111}, ttl_seconds=60)

    assert await service.is_chat_admin(bot, -100, 222) is True
    assert await service.is_chat_admin(bot, -100, 222) is True

    stats = service.cache_stats()
    assert bot.calls == 1
    assert stats == {"entries": 1, "expired_entries": 0, "ttl_seconds": 60, "bot_admins": 1}
    assert service.is_bot_admin(111) is True
