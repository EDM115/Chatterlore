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
        self._managed_bot_admin_ids: set[int] = set()
        self._delegated_chat_admin_ids: dict[int, set[int]] = {}
        self._active_managed_chat_ids: set[int] = set()
        self.ttl_seconds = ttl_seconds
        self._cache: dict[int, AdminCacheEntry] = {}

    def is_bot_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.effective_bot_admin_ids

    @property
    def effective_bot_admin_ids(self) -> set[int]:
        return self.bot_admin_ids | self._managed_bot_admin_ids

    def replace_managed_bot_admins(self, user_ids: set[int]) -> None:
        self._managed_bot_admin_ids = set(user_ids)

    def add_managed_bot_admin(self, user_id: int) -> None:
        self._managed_bot_admin_ids.add(user_id)

    def remove_managed_bot_admin(self, user_id: int) -> None:
        self._managed_bot_admin_ids.discard(user_id)

    def replace_delegated_chat_admins(self, grants: dict[int, set[int]]) -> None:
        self._delegated_chat_admin_ids = {
            chat_id: set(user_ids) for chat_id, user_ids in grants.items()
        }

    def add_delegated_chat_admin(self, chat_id: int, user_id: int) -> None:
        self._delegated_chat_admin_ids.setdefault(chat_id, set()).add(user_id)

    def remove_delegated_chat_admin(self, chat_id: int, user_id: int) -> None:
        user_ids = self._delegated_chat_admin_ids.get(chat_id)
        if user_ids is None:
            return
        user_ids.discard(user_id)
        if not user_ids:
            self._delegated_chat_admin_ids.pop(chat_id, None)

    def replace_active_managed_chats(self, chat_ids: set[int]) -> None:
        self._active_managed_chat_ids = set(chat_ids)

    def set_managed_chat_active(self, chat_id: int, active: bool) -> None:
        if active:
            self._active_managed_chat_ids.add(chat_id)
        else:
            self._active_managed_chat_ids.discard(chat_id)

    async def is_chat_admin(self, bot: Any, chat_id: int, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if self.is_bot_admin(user_id):
            return True
        if (
            chat_id in self._active_managed_chat_ids
            and user_id in self._delegated_chat_admin_ids.get(chat_id, set())
        ):
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

    def cache_stats(self) -> dict[str, int]:
        now = time.time()
        expired = sum(1 for entry in self._cache.values() if entry.expires_at < now)
        return {
            "entries": len(self._cache),
            "expired_entries": expired,
            "ttl_seconds": self.ttl_seconds,
            "bot_admins": len(self.effective_bot_admin_ids),
            "delegated_chat_admins": sum(
                len(user_ids) for user_ids in self._delegated_chat_admin_ids.values()
            ),
            "active_managed_chats": len(self._active_managed_chat_ids),
        }

    async def can_administer(self, bot: Any, chat_id: int, user_id: int | None) -> bool:
        return self.is_bot_admin(user_id) or await self.is_chat_admin(bot, chat_id, user_id)
