from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chatterlore.config import Settings
from chatterlore.db.models import AdminAlert
from chatterlore.db.repos.alerts import AdminAlertRepository
from chatterlore.db.repos.control_plane import ControlPlaneAccessRepository
from chatterlore.db.session import session_scope

MAX_TELEGRAM_RETRY_AFTER_SECONDS = 3_600


class AdminAlertService:
    def __init__(
        self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory

    async def deliver_pending(self, bot: Any, *, now: datetime, limit: int = 100) -> int:
        await self._expand_intents(now=now, limit=limit)
        async with session_scope(self.session_factory) as session:
            delivery_ids = [
                item.id
                for item in await AdminAlertRepository(session).due_deliveries(now=now, limit=limit)
            ]
        attempted = 0
        for delivery_id in delivery_ids:
            if await self._deliver_one(bot, delivery_id=delivery_id, now=now):
                attempted += 1
        return attempted

    async def list_alerts(
        self, *, limit: int = 100, before_id: int | None = None
    ) -> list[AdminAlert]:
        async with session_scope(self.session_factory) as session:
            return await AdminAlertRepository(session).list_recent(limit=limit, before_id=before_id)

    async def retry(self, alert_id: int, *, now: datetime) -> AdminAlert | None:
        async with session_scope(self.session_factory) as session:
            return await AdminAlertRepository(session).retry(alert_id, now=now)

    async def _expand_intents(self, *, now: datetime, limit: int) -> None:
        async with session_scope(self.session_factory) as session:
            repository = AdminAlertRepository(session)
            intents = await repository.pending_intents(limit=limit)
            if not intents:
                return
            recipients = set(self.settings.bot_admin_ids)
            recipients.update(await ControlPlaneAccessRepository(session).active_bot_admin_ids())
            for intent in intents:
                for recipient in sorted(recipients):
                    await repository.enqueue_delivery(
                        intent, recipient_telegram_id=recipient, now=now
                    )
                await repository.mark_expanded(intent, has_recipients=bool(recipients))

    async def _deliver_one(self, bot: Any, *, delivery_id: int, now: datetime) -> bool:
        async with session_scope(self.session_factory) as session:
            repository = AdminAlertRepository(session)
            delivery = await repository.get(delivery_id)
            if delivery is None or delivery.recipient_telegram_id is None:
                return False
            text = _safe_alert_text(delivery)
            try:
                await bot.send_message(delivery.recipient_telegram_id, text)
            except TelegramRetryAfter as error:
                retry_after = max(1, min(int(error.retry_after), MAX_TELEGRAM_RETRY_AFTER_SECONDS))
                await repository.mark_retry(
                    delivery,
                    now=now,
                    next_attempt_at=now + timedelta(seconds=retry_after),
                    classification="rate_limit",
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                await repository.mark_unreachable(delivery, now=now)
            except Exception:
                delay = min(2 ** min(delivery.attempts + 1, 10) * 15, 3_600)
                await repository.mark_retry(
                    delivery,
                    now=now,
                    next_attempt_at=now + timedelta(seconds=delay),
                    classification="telegram_error",
                )
            else:
                await repository.mark_delivered(delivery, now=now)
            return True


def _safe_alert_text(alert: AdminAlert) -> str:
    classification = alert.payload.get("classification")
    safe_classification = (
        classification
        if classification
        in {
            "authentication",
            "permission",
            "quota",
            "rate_limit",
            "transport",
            "provider",
            "recovered",
            "exhausted",
        }
        else "provider_health"
    )
    key_line = (
        f"Key: {alert.credential_label} ({alert.credential_fingerprint})"
        if alert.credential_label and alert.credential_fingerprint
        else "Key: provider pool"
    )
    return "\n".join(
        (
            "Chatterlore provider alert",
            f"Profile: {alert.profile_name}",
            key_line,
            f"Event: {alert.alert_type}",
            f"Classification: {safe_classification}",
        )
    )


__all__ = ["MAX_TELEGRAM_RETRY_AFTER_SECONDS", "AdminAlertService"]
