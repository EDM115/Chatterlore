from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from pzp_ai_bot.ai import ProviderRegistry
from pzp_ai_bot.ai.credential_pool import CredentialPoolService
from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.runtime import ControlPlaneRuntime, build_control_plane_runtime
from pzp_ai_bot.control_plane.secrets.service import ProviderSecretService
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
    control_plane: ControlPlaneRuntime | None = None


async def build_context(settings: Settings, *, ensure_schema: bool = True) -> AppContext:
    configure_logging(settings)
    if ensure_schema:
        upgrade_schema(settings)
    provider_registry = ProviderRegistry(settings)
    session_factory = create_session_factory(settings)
    provider_secrets = ProviderSecretService(settings, session_factory, provider_registry)
    if settings.control_plane_master_key_bytes and settings.control_plane_active_master_key_id:
        provider_registry.configure_credential_pool(
            CredentialPoolService(settings, session_factory, provider_secrets.vault)
        )
    await provider_secrets.refresh_registry()
    provider_selection = provider_registry.select(settings.ai_provider_profile)
    chat_provider = provider_selection.chat_provider
    embedding_provider = provider_selection.embedding_provider
    memory = MemoryService(
        settings=settings,
        session_factory=session_factory,
        chat_provider=chat_provider,
        embedding_provider=embedding_provider,
        provider_registry=provider_registry,
    )
    memory.markdown.ensure()
    memory.set_provider_profile_refresher(provider_secrets.refresh_registry)
    await memory.rebuild_daily_projection()
    await memory.refresh_runtime_settings()
    admins = AdminService(settings.bot_admin_ids, ttl_seconds=settings.admin_cache_ttl_seconds)
    control_plane = (
        build_control_plane_runtime(settings, session_factory, admins, provider_registry, memory)
        if settings.control_plane_enabled
        else None
    )
    if control_plane is not None:
        await control_plane.management.refresh_managed_authority()
        memory.set_scope_access_filter(control_plane.management.allows_scope)
        await control_plane.secrets.refresh_registry()
        memory.set_provider_profile_refresher(control_plane.secrets.refresh_registry)
        refreshed = provider_registry.select(settings.ai_provider_profile)
        memory.chat_provider = refreshed.chat_provider
        memory.embedding_provider = refreshed.embedding_provider
    return AppContext(
        settings=settings,
        session_factory=session_factory,
        memory=memory,
        admins=admins,
        control_plane=control_plane,
    )
