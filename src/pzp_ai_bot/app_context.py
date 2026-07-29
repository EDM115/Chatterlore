from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from pzp_ai_bot.ai import ProviderRegistry, build_providers
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.session import create_session_factory, upgrade_schema
from pzp_ai_bot.logging import configure_logging
from pzp_ai_bot.memory.service import MemoryService
from pzp_ai_bot.telegram.admin import AdminService


@dataclass(slots=True)
class AppContext:
    settings: Settings
    session_factory: async_sessionmaker
    memory: MemoryService
    admins: AdminService


async def build_context(settings: Settings, *, ensure_schema: bool = True) -> AppContext:
    configure_logging(settings)
    if ensure_schema:
        upgrade_schema(settings)
    provider_registry = ProviderRegistry(settings)
    chat_provider, embedding_provider = build_providers(settings)
    session_factory = create_session_factory(settings)
    memory = MemoryService(
        settings=settings,
        session_factory=session_factory,
        chat_provider=chat_provider,
        embedding_provider=embedding_provider,
        provider_registry=provider_registry,
    )
    memory.markdown.ensure()
    await memory.rebuild_daily_projection()
    await memory.refresh_runtime_settings()
    admins = AdminService(settings.bot_admin_ids, ttl_seconds=settings.admin_cache_ttl_seconds)
    return AppContext(
        settings=settings, session_factory=session_factory, memory=memory, admins=admins
    )
