from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.credential_pool import CredentialPoolService
from pzp_ai_bot.ai.factory import ProviderRegistry
from pzp_ai_bot.config import Settings
from pzp_ai_bot.telegram.admin import AdminService

from .alerts import AdminAlertService
from .auth.oidc import TelegramOIDCClient
from .auth.sessions import ControlPlaneSessionService
from .auth.transactions import OIDCTransactionService
from .authorization import ControlPlaneAuthorizationService
from .secrets.service import ProviderSecretService


@dataclass(slots=True)
class ControlPlaneRuntime:
    sessions: ControlPlaneSessionService
    transactions: OIDCTransactionService
    authorization: ControlPlaneAuthorizationService
    oidc: TelegramOIDCClient
    secrets: ProviderSecretService
    credential_pool: CredentialPoolService
    alerts: AdminAlertService


def build_control_plane_runtime(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    admins: AdminService,
    provider_registry: ProviderRegistry,
) -> ControlPlaneRuntime:
    secrets = ProviderSecretService(settings, session_factory, provider_registry)
    credential_pool = CredentialPoolService(settings, session_factory, secrets.vault)
    provider_registry.configure_credential_pool(credential_pool)
    return ControlPlaneRuntime(
        sessions=ControlPlaneSessionService(session_factory, settings),
        transactions=OIDCTransactionService(session_factory, settings),
        authorization=ControlPlaneAuthorizationService(session_factory, telegram_admins=admins),
        oidc=TelegramOIDCClient(settings),
        secrets=secrets,
        credential_pool=credential_pool,
        alerts=AdminAlertService(settings, session_factory),
    )
