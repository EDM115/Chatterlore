from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from pzp_ai_bot.ai import build_providers
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.session import create_session_factory, upgrade_schema
from pzp_ai_bot.memory.service import MemoryService
from pzp_ai_bot.telegram.admin import AdminService


@dataclass(slots=True)
class AppContext:
    settings: Settings
    session_factory: async_sessionmaker
    memory: MemoryService
    admins: AdminService


async def build_context(settings: Settings, *, ensure_schema: bool = True) -> AppContext:
    if ensure_schema:
        upgrade_schema(settings)
    chat_provider, embedding_provider = build_providers(settings)
    session_factory = create_session_factory(settings)
    memory = MemoryService(
        settings=settings,
        session_factory=session_factory,
        chat_provider=chat_provider,
        embedding_provider=embedding_provider,
    )
    memory.markdown.ensure()
    admins = AdminService(settings.bot_admin_ids)
    return AppContext(
        settings=settings, session_factory=session_factory, memory=memory, admins=admins
    )
