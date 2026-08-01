from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import AdminAlert


class AdminAlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, alert_id: int) -> AdminAlert | None:
        return await self.session.get(AdminAlert, alert_id)

    async def enqueue_intent(
        self,
        *,
        alert_type: str,
        profile_name: str,
        credential_fingerprint: str | None,
        credential_label: str | None,
        health_epoch: int,
        payload: dict[str, Any],
        now: datetime,
    ) -> AdminAlert:
        dedupe_key = _dedupe_key(alert_type, profile_name, credential_fingerprint, health_epoch)
        await self.session.execute(
            sqlite_insert(AdminAlert)
            .values(
                parent_intent_id=None,
                alert_type=alert_type,
                profile_name=profile_name,
                credential_fingerprint=credential_fingerprint,
                credential_label=credential_label,
                health_epoch=health_epoch,
                dedupe_key=dedupe_key,
                payload=dict(payload),
                recipient_telegram_id=None,
                state="pending",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                last_attempt_at=None,
                delivered_at=None,
                error_classification=None,
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
        stored = await self.session.scalar(
            select(AdminAlert).where(AdminAlert.dedupe_key == dedupe_key)
        )
        if stored is None:
            raise RuntimeError("admin alert intent could not be stored")
        return stored

    async def enqueue_delivery(
        self, intent: AdminAlert, *, recipient_telegram_id: int, now: datetime
    ) -> AdminAlert:
        dedupe_key = f"{intent.dedupe_key}:recipient:{recipient_telegram_id}"
        await self.session.execute(
            sqlite_insert(AdminAlert)
            .values(
                parent_intent_id=intent.id,
                alert_type=intent.alert_type,
                profile_name=intent.profile_name,
                credential_fingerprint=intent.credential_fingerprint,
                credential_label=intent.credential_label,
                health_epoch=intent.health_epoch,
                dedupe_key=dedupe_key,
                payload=dict(intent.payload),
                recipient_telegram_id=recipient_telegram_id,
                state="pending",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                last_attempt_at=None,
                delivered_at=None,
                error_classification=None,
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
        stored = await self.session.scalar(
            select(AdminAlert).where(AdminAlert.dedupe_key == dedupe_key)
        )
        if stored is None:
            raise RuntimeError("admin alert delivery could not be stored")
        return stored

    async def pending_intents(self, *, limit: int) -> list[AdminAlert]:
        statement = (
            select(AdminAlert)
            .where(AdminAlert.recipient_telegram_id.is_(None), AdminAlert.state == "pending")
            .order_by(AdminAlert.id)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def due_deliveries(self, *, now: datetime, limit: int) -> list[AdminAlert]:
        statement = (
            select(AdminAlert)
            .where(
                AdminAlert.recipient_telegram_id.is_not(None),
                AdminAlert.state.in_(("pending", "retry")),
                or_(AdminAlert.next_attempt_at.is_(None), AdminAlert.next_attempt_at <= now),
            )
            .order_by(AdminAlert.id)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def mark_expanded(self, intent: AdminAlert, *, has_recipients: bool) -> None:
        intent.state = "expanded" if has_recipients else "no_recipients"
        intent.next_attempt_at = None
        await self.session.flush()

    async def mark_delivered(self, delivery: AdminAlert, *, now: datetime) -> None:
        delivery.state = "delivered"
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.delivered_at = now
        delivery.next_attempt_at = None
        delivery.error_classification = None
        await self.session.flush()

    async def mark_retry(
        self, delivery: AdminAlert, *, now: datetime, next_attempt_at: datetime, classification: str
    ) -> None:
        delivery.state = "retry"
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.next_attempt_at = next_attempt_at
        delivery.error_classification = classification
        await self.session.flush()

    async def mark_unreachable(
        self, delivery: AdminAlert, *, now: datetime, classification: str = "unreachable"
    ) -> None:
        delivery.state = "unreachable"
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.next_attempt_at = None
        delivery.error_classification = classification
        await self.session.flush()

    async def retry(self, alert_id: int, *, now: datetime) -> AdminAlert | None:
        stored = await self.get(alert_id)
        if stored is None or stored.state == "delivered":
            return None
        stored.state = "pending" if stored.recipient_telegram_id is None else "retry"
        stored.next_attempt_at = now
        stored.error_classification = None
        await self.session.flush()
        return stored

    async def list_recent(self, *, limit: int, before_id: int | None = None) -> list[AdminAlert]:
        statement = select(AdminAlert)
        if before_id is not None:
            statement = statement.where(AdminAlert.id < before_id)
        statement = statement.order_by(AdminAlert.id.desc()).limit(min(101, max(1, limit)))
        return list((await self.session.scalars(statement)).all())


def _dedupe_key(
    alert_type: str, profile_name: str, credential_fingerprint: str | None, health_epoch: int
) -> str:
    return f"{alert_type}:{profile_name}:{credential_fingerprint or 'pool'}:{health_epoch}"


__all__ = ["AdminAlertRepository"]
