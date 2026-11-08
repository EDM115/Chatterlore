from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chatterlore.db.models import ForgetChallenge, SettingsChallenge
from chatterlore.memory.scope import Scope

from .shared import _nullable_eq, utc_now


@dataclass(frozen=True, slots=True)
class ForgetChallengeView:
    id: int
    scope: Scope
    user_id: int
    token_hash: str
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SettingsChallengeView:
    id: int
    scope: Scope
    user_id: int
    token_hash: str
    updates: dict[str, Any]
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


class ForgetChallengeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def latest_active(
        self, *, scope: Scope, user_id: int, now: datetime | None = None
    ) -> ForgetChallengeView | None:
        current = now or utc_now()
        row = await self.session.scalar(
            select(ForgetChallenge)
            .where(
                _nullable_eq(ForgetChallenge.chat_id, scope.chat_id),
                _nullable_eq(ForgetChallenge.message_thread_id, scope.thread_id),
                ForgetChallenge.user_id == user_id,
                ForgetChallenge.consumed_at.is_(None),
                ForgetChallenge.expires_at >= current,
            )
            .order_by(ForgetChallenge.created_at.desc(), ForgetChallenge.id.desc())
        )
        return forget_challenge_view(row) if row is not None else None

    async def create(
        self, *, scope: Scope, user_id: int, token_hash: str, expires_at: datetime
    ) -> ForgetChallengeView:
        current = utc_now()
        await self.session.execute(
            update(ForgetChallenge)
            .where(
                _nullable_eq(ForgetChallenge.chat_id, scope.chat_id),
                _nullable_eq(ForgetChallenge.message_thread_id, scope.thread_id),
                ForgetChallenge.user_id == user_id,
                ForgetChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=current)
        )
        row = ForgetChallenge(
            chat_id=scope.chat_id,
            message_thread_id=scope.thread_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            consumed_at=None,
            created_at=current,
        )
        self.session.add(row)
        await self.session.flush()
        return forget_challenge_view(row)

    async def consume(self, challenge_id: int) -> None:
        row = await self.session.get(ForgetChallenge, challenge_id)
        if row is not None:
            row.consumed_at = utc_now()
            await self.session.flush()


class SettingsChallengeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def latest_active(
        self, *, scope: Scope, user_id: int, now: datetime | None = None
    ) -> SettingsChallengeView | None:
        current = now or utc_now()
        row = await self.session.scalar(
            select(SettingsChallenge)
            .where(
                _nullable_eq(SettingsChallenge.chat_id, scope.chat_id),
                _nullable_eq(SettingsChallenge.message_thread_id, scope.thread_id),
                SettingsChallenge.user_id == user_id,
                SettingsChallenge.consumed_at.is_(None),
                SettingsChallenge.expires_at >= current,
            )
            .order_by(SettingsChallenge.created_at.desc(), SettingsChallenge.id.desc())
        )
        return settings_challenge_view(row) if row is not None else None

    async def create(
        self,
        *,
        scope: Scope,
        user_id: int,
        token_hash: str,
        updates: dict[str, Any],
        expires_at: datetime,
    ) -> SettingsChallengeView:
        current = utc_now()
        await self.session.execute(
            update(SettingsChallenge)
            .where(
                _nullable_eq(SettingsChallenge.chat_id, scope.chat_id),
                _nullable_eq(SettingsChallenge.message_thread_id, scope.thread_id),
                SettingsChallenge.user_id == user_id,
                SettingsChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=current)
        )
        row = SettingsChallenge(
            chat_id=scope.chat_id,
            message_thread_id=scope.thread_id,
            user_id=user_id,
            token_hash=token_hash,
            updates=updates,
            expires_at=expires_at,
            consumed_at=None,
            created_at=current,
        )
        self.session.add(row)
        await self.session.flush()
        return settings_challenge_view(row)

    async def consume(self, challenge_id: int) -> None:
        row = await self.session.get(SettingsChallenge, challenge_id)
        if row is not None:
            row.consumed_at = utc_now()
            await self.session.flush()


def forget_challenge_view(row: ForgetChallenge) -> ForgetChallengeView:
    return ForgetChallengeView(
        id=row.id,
        scope=Scope(row.chat_id, row.message_thread_id),
        user_id=row.user_id,
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
        created_at=row.created_at,
    )


def settings_challenge_view(row: SettingsChallenge) -> SettingsChallengeView:
    return SettingsChallengeView(
        id=row.id,
        scope=Scope(row.chat_id, row.message_thread_id),
        user_id=row.user_id,
        token_hash=row.token_hash,
        updates=dict(row.updates),
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
        created_at=row.created_at,
    )
