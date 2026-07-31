from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.auth.identity import TelegramIdentity
from pzp_ai_bot.db.models import ControlPlaneSession, ControlPlaneUser
from pzp_ai_bot.db.repos.control_plane import (
    ControlPlaneSessionRepository,
    ControlPlaneUserRepository,
)
from pzp_ai_bot.db.session import session_scope


class SessionAuthenticationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedSession:
    session_token: str
    csrf_token: str
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    session_id: int
    identity: TelegramIdentity
    client_kind: str
    authenticated_at: datetime
    step_up_at: datetime | None
    idle_expires_at: datetime
    absolute_expires_at: datetime


class ControlPlaneSessionService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def issue(
        self,
        identity: TelegramIdentity,
        *,
        client_kind: str,
        now: datetime,
        replace_session_token: str | None = None,
    ) -> IssuedSession:
        if client_kind not in {"browser", "mini_app"}:
            raise ValueError("unsupported control-plane session client")
        session_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        absolute_expires_at = now + timedelta(
            seconds=self.settings.control_plane_session_absolute_seconds
        )
        idle_expires_at = min(
            absolute_expires_at,
            now + timedelta(seconds=self.settings.control_plane_session_idle_seconds),
        )
        async with session_scope(self.session_factory) as session:
            if replace_session_token:
                await ControlPlaneSessionRepository(session).revoke_token(
                    token_hash=_secret_hash(replace_session_token), now=now, reason="account_switch"
                )
            user = await ControlPlaneUserRepository(session).upsert_identity(identity)
            await ControlPlaneSessionRepository(session).add(
                user_id=user.id,
                token_hash=_secret_hash(session_token),
                csrf_hash=_secret_hash(csrf_token),
                client_kind=client_kind,
                created_at=now,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=absolute_expires_at,
            )
        return IssuedSession(
            session_token=session_token,
            csrf_token=csrf_token,
            absolute_expires_at=absolute_expires_at,
        )

    async def authenticate(self, session_token: str, *, now: datetime) -> AuthenticatedSession:
        async with session_scope(self.session_factory) as session:
            active = await ControlPlaneSessionRepository(session).get_active(
                token_hash=_secret_hash(session_token), now=now
            )
            if active is None:
                raise SessionAuthenticationError("session is invalid or expired")
            stored, user = active
            await ControlPlaneSessionRepository(session).touch(
                stored,
                now=now,
                idle_expires_at=now
                + timedelta(seconds=self.settings.control_plane_session_idle_seconds),
            )
            return _authenticated_session(stored, user)

    async def validate_csrf(self, session_token: str, csrf_token: str, *, now: datetime) -> bool:
        async with session_scope(self.session_factory) as session:
            active = await ControlPlaneSessionRepository(session).get_active(
                token_hash=_secret_hash(session_token), now=now
            )
            return bool(
                active is not None
                and hmac.compare_digest(active[0].csrf_hash, _secret_hash(csrf_token))
            )

    async def rotate_csrf(self, session_token: str, *, now: datetime) -> str:
        csrf_token = secrets.token_urlsafe(32)
        async with session_scope(self.session_factory) as session:
            active = await ControlPlaneSessionRepository(session).get_active(
                token_hash=_secret_hash(session_token), now=now
            )
            if active is None:
                raise SessionAuthenticationError("session is invalid or expired")
            await ControlPlaneSessionRepository(session).rotate_csrf(
                active[0], csrf_hash=_secret_hash(csrf_token), now=now
            )
        return csrf_token

    async def mark_step_up(self, session_token: str, *, now: datetime) -> None:
        async with session_scope(self.session_factory) as session:
            active = await ControlPlaneSessionRepository(session).get_active(
                token_hash=_secret_hash(session_token), now=now
            )
            if active is None:
                raise SessionAuthenticationError("session is invalid or expired")
            await ControlPlaneSessionRepository(session).mark_step_up(active[0], now=now)

    async def revoke(self, session_token: str, *, now: datetime, reason: str = "logout") -> bool:
        async with session_scope(self.session_factory) as session:
            return await ControlPlaneSessionRepository(session).revoke_token(
                token_hash=_secret_hash(session_token), now=now, reason=reason
            )

    async def cleanup(self, *, now: datetime) -> int:
        async with session_scope(self.session_factory) as session:
            return await ControlPlaneSessionRepository(session).cleanup(now=now)


def _authenticated_session(
    stored: ControlPlaneSession, user: ControlPlaneUser
) -> AuthenticatedSession:
    return AuthenticatedSession(
        session_id=stored.id,
        identity=TelegramIdentity(
            telegram_user_id=user.telegram_user_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            language_code=user.language_code,
            photo_url=user.photo_url,
            is_bot=user.is_bot,
            is_premium=user.is_premium,
            authenticated_at=_as_aware(stored.authenticated_at),
        ),
        client_kind=stored.client_kind,
        authenticated_at=_as_aware(stored.authenticated_at),
        step_up_at=_as_aware(stored.step_up_at) if stored.step_up_at else None,
        idle_expires_at=_as_aware(stored.idle_expires_at),
        absolute_expires_at=_as_aware(stored.absolute_expires_at),
    )


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
