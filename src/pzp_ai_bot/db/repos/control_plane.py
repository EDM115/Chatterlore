from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.control_plane.auth.identity import TelegramIdentity
from pzp_ai_bot.db.models import (
    BotAdminGrant,
    ChatAdminGrant,
    ControlPlaneSession,
    ControlPlaneUser,
    ManagedChat,
    OIDCAuthTransaction,
)


class ControlPlaneUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_identity(self, identity: TelegramIdentity) -> ControlPlaneUser:
        user = await self.session.scalar(
            select(ControlPlaneUser).where(
                ControlPlaneUser.telegram_user_id == identity.telegram_user_id
            )
        )
        if user is None:
            user = ControlPlaneUser(
                telegram_user_id=identity.telegram_user_id,
                username=identity.username,
                first_name=identity.first_name,
                last_name=identity.last_name,
                language_code=identity.language_code,
                photo_url=identity.photo_url,
                is_bot=identity.is_bot,
                is_premium=identity.is_premium,
                first_authenticated_at=identity.authenticated_at,
                last_authenticated_at=identity.authenticated_at,
                disabled_at=None,
                version=1,
            )
            self.session.add(user)
        else:
            user.username = identity.username
            user.first_name = identity.first_name
            user.last_name = identity.last_name
            user.language_code = identity.language_code
            user.photo_url = identity.photo_url
            user.is_bot = identity.is_bot
            user.is_premium = identity.is_premium
            user.last_authenticated_at = identity.authenticated_at
            user.version += 1
        await self.session.flush()
        return user


class OIDCTransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        state_hash: str,
        pkce_ciphertext: bytes,
        pkce_nonce: bytes,
        oidc_nonce: str,
        redirect_uri: str,
        return_to: str,
        action: str,
        expected_telegram_user_id: int | None,
        created_at: datetime,
        expires_at: datetime,
    ) -> OIDCAuthTransaction:
        transaction = OIDCAuthTransaction(
            state_hash=state_hash,
            pkce_ciphertext=pkce_ciphertext,
            pkce_nonce=pkce_nonce,
            oidc_nonce=oidc_nonce,
            redirect_uri=redirect_uri,
            return_to=return_to,
            action=action,
            expected_telegram_user_id=expected_telegram_user_id,
            created_at=created_at,
            expires_at=expires_at,
            consumed_at=None,
        )
        self.session.add(transaction)
        await self.session.flush()
        return transaction

    async def consume(
        self, *, state_hash: str, consumed_at: datetime
    ) -> OIDCAuthTransaction | None:
        statement = (
            update(OIDCAuthTransaction)
            .where(
                OIDCAuthTransaction.state_hash == state_hash,
                OIDCAuthTransaction.consumed_at.is_(None),
                OIDCAuthTransaction.expires_at > consumed_at,
            )
            .values(consumed_at=consumed_at)
            .returning(OIDCAuthTransaction)
        )
        return (await self.session.scalars(statement)).one_or_none()

    async def cleanup(self, *, now: datetime) -> int:
        result = await self.session.execute(
            delete(OIDCAuthTransaction).where(
                (OIDCAuthTransaction.expires_at <= now)
                | (OIDCAuthTransaction.consumed_at.is_not(None))
            )
        )
        return _rowcount(result)


class ControlPlaneSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        user_id: int,
        token_hash: str,
        csrf_hash: str,
        client_kind: str,
        created_at: datetime,
        idle_expires_at: datetime,
        absolute_expires_at: datetime,
    ) -> ControlPlaneSession:
        stored = ControlPlaneSession(
            user_id=user_id,
            token_hash=token_hash,
            csrf_hash=csrf_hash,
            client_kind=client_kind,
            created_at=created_at,
            authenticated_at=created_at,
            last_seen_at=created_at,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            step_up_at=None,
            step_up_action=None,
            revoked_at=None,
            revoke_reason=None,
        )
        self.session.add(stored)
        await self.session.flush()
        return stored

    async def get_active(
        self, *, token_hash: str, now: datetime
    ) -> tuple[ControlPlaneSession, ControlPlaneUser] | None:
        statement = (
            select(ControlPlaneSession, ControlPlaneUser)
            .join(ControlPlaneUser, ControlPlaneUser.id == ControlPlaneSession.user_id)
            .where(
                ControlPlaneSession.token_hash == token_hash,
                ControlPlaneSession.revoked_at.is_(None),
                ControlPlaneSession.idle_expires_at > now,
                ControlPlaneSession.absolute_expires_at > now,
                ControlPlaneUser.disabled_at.is_(None),
            )
        )
        row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    async def touch(
        self, stored: ControlPlaneSession, *, now: datetime, idle_expires_at: datetime
    ) -> None:
        stored.last_seen_at = now
        stored.idle_expires_at = min(_as_aware(stored.absolute_expires_at), idle_expires_at)
        await self.session.flush()

    async def rotate_csrf(
        self, stored: ControlPlaneSession, *, csrf_hash: str, now: datetime
    ) -> None:
        stored.csrf_hash = csrf_hash
        stored.last_seen_at = now
        await self.session.flush()

    async def mark_step_up(
        self, stored: ControlPlaneSession, *, now: datetime, action: str
    ) -> None:
        stored.step_up_at = now
        stored.step_up_action = action
        stored.last_seen_at = now
        await self.session.flush()

    async def revoke_token(self, *, token_hash: str, now: datetime, reason: str) -> bool:
        result = await self.session.execute(
            update(ControlPlaneSession)
            .where(
                ControlPlaneSession.token_hash == token_hash,
                ControlPlaneSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoke_reason=reason)
        )
        return bool(_rowcount(result))

    async def cleanup(self, *, now: datetime) -> int:
        result = await self.session.execute(
            delete(ControlPlaneSession).where(
                (ControlPlaneSession.absolute_expires_at <= now)
                | (ControlPlaneSession.revoked_at.is_not(None))
            )
        )
        return _rowcount(result)


class ControlPlaneAccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_bot_admin(self, telegram_user_id: int) -> bool:
        grant_id = await self.session.scalar(
            select(BotAdminGrant.id).where(
                BotAdminGrant.telegram_user_id == telegram_user_id,
                BotAdminGrant.revoked_at.is_(None),
            )
        )
        return grant_id is not None

    async def active_chat_admin_ids(self, telegram_user_id: int) -> set[int]:
        statement = (
            select(ChatAdminGrant.chat_id)
            .join(ManagedChat, ManagedChat.chat_id == ChatAdminGrant.chat_id)
            .where(
                ChatAdminGrant.telegram_user_id == telegram_user_id,
                ChatAdminGrant.revoked_at.is_(None),
                ManagedChat.status == "active",
                ManagedChat.removed_at.is_(None),
            )
        )
        return {int(value) for value in (await self.session.scalars(statement)).all()}

    async def grant_bot_admin(
        self, *, telegram_user_id: int, actor_user_id: int, now: datetime
    ) -> BotAdminGrant:
        grant = await self.session.scalar(
            select(BotAdminGrant).where(BotAdminGrant.telegram_user_id == telegram_user_id)
        )
        if grant is None:
            grant = BotAdminGrant(
                telegram_user_id=telegram_user_id,
                granted_by=actor_user_id,
                granted_at=now,
                revoked_by=None,
                revoked_at=None,
                version=1,
            )
            self.session.add(grant)
        else:
            grant.granted_by = actor_user_id
            grant.granted_at = now
            grant.revoked_by = None
            grant.revoked_at = None
            grant.version += 1
        await self.session.flush()
        return grant

    async def revoke_bot_admin(
        self, *, telegram_user_id: int, actor_user_id: int, now: datetime
    ) -> bool:
        result = await self.session.execute(
            update(BotAdminGrant)
            .where(
                BotAdminGrant.telegram_user_id == telegram_user_id,
                BotAdminGrant.revoked_at.is_(None),
            )
            .values(revoked_by=actor_user_id, revoked_at=now, version=BotAdminGrant.version + 1)
        )
        return bool(_rowcount(result))

    async def grant_chat_admin(
        self, *, chat_id: int, telegram_user_id: int, actor_user_id: int, now: datetime
    ) -> ChatAdminGrant:
        grant = await self.session.scalar(
            select(ChatAdminGrant).where(
                ChatAdminGrant.chat_id == chat_id,
                ChatAdminGrant.telegram_user_id == telegram_user_id,
            )
        )
        if grant is None:
            grant = ChatAdminGrant(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                granted_by=actor_user_id,
                granted_at=now,
                revoked_by=None,
                revoked_at=None,
                version=1,
            )
            self.session.add(grant)
        else:
            grant.granted_by = actor_user_id
            grant.granted_at = now
            grant.revoked_by = None
            grant.revoked_at = None
            grant.version += 1
        await self.session.flush()
        return grant

    async def revoke_chat_admin(
        self, *, chat_id: int, telegram_user_id: int, actor_user_id: int, now: datetime
    ) -> bool:
        result = await self.session.execute(
            update(ChatAdminGrant)
            .where(
                ChatAdminGrant.chat_id == chat_id,
                ChatAdminGrant.telegram_user_id == telegram_user_id,
                ChatAdminGrant.revoked_at.is_(None),
            )
            .values(revoked_by=actor_user_id, revoked_at=now, version=ChatAdminGrant.version + 1)
        )
        return bool(_rowcount(result))

    async def upsert_managed_chat(
        self,
        *,
        chat_id: int,
        title: str | None,
        username: str | None,
        status: str,
        actor_user_id: int,
        now: datetime,
    ) -> ManagedChat:
        if status not in {"active", "paused", "removed"}:
            raise ValueError("managed chat status is invalid")
        managed = await self.session.scalar(
            select(ManagedChat).where(ManagedChat.chat_id == chat_id)
        )
        if managed is None:
            managed = ManagedChat(
                chat_id=chat_id,
                title=title,
                username=username,
                status=status,
                enrollment_token_hash=None,
                enrollment_expires_at=None,
                created_by=actor_user_id,
                created_at=now,
                updated_by=actor_user_id,
                updated_at=now,
                paused_at=now if status == "paused" else None,
                removed_at=now if status == "removed" else None,
                version=1,
            )
            self.session.add(managed)
        else:
            managed.title = title
            managed.username = username
            managed.status = status
            managed.updated_by = actor_user_id
            managed.updated_at = now
            managed.paused_at = now if status == "paused" else None
            managed.removed_at = now if status == "removed" else None
            managed.version += 1
        await self.session.flush()
        return managed


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)
