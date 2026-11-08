from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chatterlore.db.repos.control_plane import ControlPlaneAccessRepository
from chatterlore.db.session import session_scope
from chatterlore.telegram.admin import AdminService

CAPABILITY_BOT_ADMIN = "bot.admin"
CAPABILITY_CHAT_ADMIN = "chat.admin"


@dataclass(frozen=True, slots=True)
class ControlPlaneActor:
    telegram_user_id: int
    is_bot_admin: bool
    delegated_chat_ids: frozenset[int]
    native_chat_ids: frozenset[int]

    @property
    def capabilities(self) -> frozenset[str]:
        if self.is_bot_admin:
            return frozenset({CAPABILITY_BOT_ADMIN})
        return frozenset()

    def capabilities_for_chat(self, chat_id: int) -> set[str]:
        if self.is_bot_admin or chat_id in self.delegated_chat_ids | self.native_chat_ids:
            return {CAPABILITY_CHAT_ADMIN}
        return set()


class ControlPlaneAuthorizationService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, telegram_admins: AdminService
    ) -> None:
        self.session_factory = session_factory
        self.telegram_admins = telegram_admins

    async def resolve(
        self, telegram_user_id: int, *, bot: Any | None = None, chat_id: int | None = None
    ) -> ControlPlaneActor:
        async with session_scope(self.session_factory) as session:
            repository = ControlPlaneAccessRepository(session)
            managed_bot_admin = await repository.is_bot_admin(telegram_user_id)
            delegated_chat_ids = await repository.active_chat_admin_ids(telegram_user_id)
        bot_admin = self.telegram_admins.is_bot_admin(telegram_user_id) or managed_bot_admin
        native_chat_ids: set[int] = set()
        if (
            not bot_admin
            and bot is not None
            and chat_id is not None
            and await self.telegram_admins.is_chat_admin(bot, chat_id, telegram_user_id)
        ):
            native_chat_ids.add(chat_id)
        return ControlPlaneActor(
            telegram_user_id=telegram_user_id,
            is_bot_admin=bot_admin,
            delegated_chat_ids=frozenset(delegated_chat_ids),
            native_chat_ids=frozenset(native_chat_ids),
        )
