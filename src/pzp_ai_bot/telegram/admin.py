from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AdminCacheEntry:
    user_ids: set[int]
    expires_at: float


class AdminService:
    """Caches Telegram chat admins and honors configured bot admins."""

    def __init__(self, bot_admin_ids: set[int], ttl_seconds: int = 300) -> None:
        self.bot_admin_ids = bot_admin_ids
        self.ttl_seconds = ttl_seconds
        self._cache: dict[int, AdminCacheEntry] = {}

    def is_bot_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.bot_admin_ids

    async def is_chat_admin(self, bot: Any, chat_id: int, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if self.is_bot_admin(user_id):
            return True
        now = time.time()
        cached = self._cache.get(chat_id)
        if cached is None or cached.expires_at < now:
            try:
                admins = await bot.get_chat_administrators(chat_id)
            except Exception:
                admins = []
            user_ids = {
                int(member.user.id)
                for member in admins
                if getattr(member, "user", None) is not None
                and getattr(member.user, "id", None) is not None
            }
            cached = AdminCacheEntry(user_ids=user_ids, expires_at=now + self.ttl_seconds)
            self._cache[chat_id] = cached
        return user_id in cached.user_ids

    async def can_administer(self, bot: Any, chat_id: int, user_id: int | None) -> bool:
        return self.is_bot_admin(user_id) or await self.is_chat_admin(bot, chat_id, user_id)
