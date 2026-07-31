from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.config import Settings
from pzp_ai_bot.telegram.admin import AdminService

from .auth.oidc import TelegramOIDCClient
from .auth.sessions import ControlPlaneSessionService
from .auth.transactions import OIDCTransactionService
from .authorization import ControlPlaneAuthorizationService


@dataclass(slots=True)
class ControlPlaneRuntime:
    sessions: ControlPlaneSessionService
    transactions: OIDCTransactionService
    authorization: ControlPlaneAuthorizationService
    oidc: TelegramOIDCClient


def build_control_plane_runtime(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession], admins: AdminService
) -> ControlPlaneRuntime:
    return ControlPlaneRuntime(
        sessions=ControlPlaneSessionService(session_factory, settings),
        transactions=OIDCTransactionService(session_factory, settings),
        authorization=ControlPlaneAuthorizationService(session_factory, telegram_admins=admins),
        oidc=TelegramOIDCClient(settings),
    )
