from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatterlore.db.models import LearningPolicy
from chatterlore.memory.scope import Scope

from .shared import _nullable_eq, utc_now


class PolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set_policy(
        self,
        *,
        policy_type: str,
        enabled: bool,
        scope: Scope | None = None,
        user_id: int | None = None,
        key: str | None = None,
        value: dict[str, Any] | None = None,
        updated_by: int | None = None,
    ) -> LearningPolicy:
        chat_id = scope.chat_id if scope else None
        thread_id = scope.thread_id if scope else None
        stmt = select(LearningPolicy).where(
            LearningPolicy.policy_type == policy_type,
            _nullable_eq(LearningPolicy.chat_id, chat_id),
            _nullable_eq(LearningPolicy.message_thread_id, thread_id),
            _nullable_eq(LearningPolicy.user_id, user_id),
            _nullable_eq(LearningPolicy.key, key),
        )
        rows = list((await self.session.scalars(stmt.order_by(LearningPolicy.id.asc()))).all())
        if not rows:
            row = LearningPolicy(
                policy_type=policy_type,
                chat_id=chat_id,
                message_thread_id=thread_id,
                user_id=user_id,
                key=key,
                enabled=enabled,
                value=value,
                updated_by=updated_by,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row = rows[0]
            row.enabled = enabled
            row.value = value
            row.updated_by = updated_by
            row.updated_at = utc_now()
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def list_policies(self) -> list[LearningPolicy]:
        return list((await self.session.scalars(select(LearningPolicy))).all())

    async def get_policy(
        self,
        *,
        policy_type: str,
        scope: Scope | None = None,
        user_id: int | None = None,
        key: str | None = None,
    ) -> LearningPolicy | None:
        chat_id = scope.chat_id if scope else None
        thread_id = scope.thread_id if scope else None
        return await self.session.scalar(
            select(LearningPolicy).where(
                LearningPolicy.policy_type == policy_type,
                _nullable_eq(LearningPolicy.chat_id, chat_id),
                _nullable_eq(LearningPolicy.message_thread_id, thread_id),
                _nullable_eq(LearningPolicy.user_id, user_id),
                _nullable_eq(LearningPolicy.key, key),
            )
        )
