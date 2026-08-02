from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.credential_pool import CredentialPoolService
from pzp_ai_bot.ai.factory import ProviderRegistry
from pzp_ai_bot.config import Settings
from pzp_ai_bot.memory.service import MemoryService
from pzp_ai_bot.telegram.admin import AdminService

from .alerts import AdminAlertService
from .audit import ControlPlaneAuditService
from .auth.oidc import TelegramOIDCClient
from .auth.sessions import ControlPlaneSessionService
from .auth.transactions import OIDCTransactionService
from .authorization import ControlPlaneAuthorizationService
from .catalog import ProviderCapabilityCatalog
from .insights import ControlPlaneInsightsService
from .management import ControlPlaneManagementService
from .operations import ControlPlaneOperationsService
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
    management: ControlPlaneManagementService
    catalog: ProviderCapabilityCatalog
    insights: ControlPlaneInsightsService
    operations: ControlPlaneOperationsService
    audit: ControlPlaneAuditService


def build_control_plane_runtime(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    admins: AdminService,
    provider_registry: ProviderRegistry,
    memory: MemoryService,
) -> ControlPlaneRuntime:
    secrets = ProviderSecretService(settings, session_factory, provider_registry)
    credential_pool = CredentialPoolService(settings, session_factory, secrets.vault)
    sessions = ControlPlaneSessionService(session_factory, settings)
    provider_registry.configure_credential_pool(credential_pool)
    insights = ControlPlaneInsightsService(
        settings, session_factory, admins=admins, secrets=secrets
    )
    return ControlPlaneRuntime(
        sessions=sessions,
        transactions=OIDCTransactionService(session_factory, settings),
        authorization=ControlPlaneAuthorizationService(session_factory, telegram_admins=admins),
        oidc=TelegramOIDCClient(settings),
        secrets=secrets,
        credential_pool=credential_pool,
        alerts=AdminAlertService(settings, session_factory),
        management=ControlPlaneManagementService(
            session_factory, admins=admins, sessions=sessions, memory=memory
        ),
        catalog=ProviderCapabilityCatalog(settings, secrets),
        insights=insights,
        operations=ControlPlaneOperationsService(
            settings, session_factory, memory=memory, secrets=secrets, insights=insights
        ),
        audit=ControlPlaneAuditService(session_factory),
    )
