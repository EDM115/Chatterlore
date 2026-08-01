from __future__ import annotations

import base64
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.alerts import AdminAlertService
from pzp_ai_bot.db.repos.alerts import AdminAlertRepository
from pzp_ai_bot.db.repos.control_plane import ControlPlaneAccessRepository
from pzp_ai_bot.db.session import (
    create_schema,
    create_session_factory,
    session_scope,
    upgrade_schema,
)

NOW = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)


def alert_settings(tmp_path: Path) -> Settings:
    master_key = base64.b64encode(bytes([13]) * 32).decode()
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        bot_admins="111,222",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'alerts.db'}",
        memory_dir=tmp_path / "memory",
        control_plane_enabled=True,
        control_plane_public_origin="https://control.example.test",
        telegram_oidc_client_id="123456789",
        telegram_oidc_client_secret="oidc-secret",  # noqa: S106
        control_plane_master_keys=json.dumps({"primary": master_key}),
        control_plane_active_master_key_id="primary",
    )


def test_revision_0015_creates_the_admin_alert_outbox(tmp_path: Path) -> None:
    settings = alert_settings(tmp_path)

    upgrade_schema(settings)

    assert settings.sqlite_path is not None
    with sqlite3.connect(settings.sqlite_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'index'"
            ).fetchall()
        }

    assert revision == ("0015_admin_alert_outbox",)
    assert "admin_alerts" in tables
    assert {
        "uq_admin_alerts_dedupe_key",
        "ix_admin_alerts_delivery_due",
        "ix_admin_alerts_profile_created",
    } <= indexes


@pytest.mark.asyncio
async def test_alert_intents_are_deduplicated_by_safe_health_epoch(tmp_path: Path) -> None:
    settings = alert_settings(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)

    async with session_scope(factory) as session:
        alerts = AdminAlertRepository(session)
        first = await alerts.enqueue_intent(
            alert_type="credential_disabled",
            profile_name="primary",
            credential_fingerprint="0123456789abcdef",
            credential_label="primary key",
            health_epoch=7,
            payload={"classification": "authentication"},
            now=NOW,
        )
        duplicate = await alerts.enqueue_intent(
            alert_type="credential_disabled",
            profile_name="primary",
            credential_fingerprint="0123456789abcdef",
            credential_label="primary key",
            health_epoch=7,
            payload={"classification": "authentication"},
            now=NOW,
        )

    assert duplicate.id == first.id
    async with session_scope(factory) as session:
        stored = await AdminAlertRepository(session).list_recent(limit=100)
    assert len(stored) == 1
    serialized = json.dumps(stored[0].payload)
    assert "secret" not in serialized.lower()
    assert "exception" not in serialized.lower()


class AlertBotDouble:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.calls: dict[int, int] = {}

    async def send_message(self, chat_id: int, text: str) -> None:
        self.calls[chat_id] = self.calls.get(chat_id, 0) + 1
        if chat_id == 222 and self.calls[chat_id] == 1:
            raise TelegramRetryAfter(
                method=SendMessage(chat_id=chat_id, text=text),
                message="PRIVATE RATE LIMIT BODY",
                retry_after=30,
            )
        if chat_id == 333:
            raise TelegramForbiddenError(
                method=SendMessage(chat_id=chat_id, text=text), message="PRIVATE UNREACHABLE BODY"
            )
        self.messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_alert_delivery_resolves_current_admins_and_isolates_rate_limit_and_unreachable(
    tmp_path: Path,
) -> None:
    settings = alert_settings(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    async with session_scope(factory) as session:
        await ControlPlaneAccessRepository(session).grant_bot_admin(
            telegram_user_id=333, actor_user_id=111, now=NOW
        )
        await AdminAlertRepository(session).enqueue_intent(
            alert_type="credential_disabled",
            profile_name="primary",
            credential_fingerprint="0123456789abcdef",
            credential_label="primary key",
            health_epoch=8,
            payload={"classification": "authentication", "sentinel": "must-not-render"},
            now=NOW,
        )
    bot = AlertBotDouble()
    service = AdminAlertService(settings, factory)

    first_count = await service.deliver_pending(bot, now=NOW)

    assert first_count == 3
    assert [chat_id for chat_id, _text in bot.messages] == [111]
    assert all("must-not-render" not in text for _chat_id, text in bot.messages)
    assert all("PRIVATE" not in text for _chat_id, text in bot.messages)
    async with session_scope(factory) as session:
        rows = await AdminAlertRepository(session).list_recent(limit=100)
    deliveries = {row.recipient_telegram_id: row for row in rows if row.recipient_telegram_id}
    assert deliveries[111].state == "delivered"
    assert deliveries[222].state == "retry"
    retry_at = deliveries[222].next_attempt_at
    assert retry_at is not None
    assert retry_at.replace(tzinfo=retry_at.tzinfo or UTC) == NOW + timedelta(seconds=30)
    assert deliveries[333].state == "unreachable"
    assert deliveries[222].error_classification == "rate_limit"
    assert deliveries[333].error_classification == "unreachable"
    assert all("PRIVATE" not in repr(row.payload) for row in rows)

    second_count = await service.deliver_pending(bot, now=NOW + timedelta(seconds=31))

    assert second_count == 1
    assert bot.messages[-1][0] == 222
    async with session_scope(factory) as session:
        retried = await AdminAlertRepository(session).get(deliveries[222].id)
    assert retried is not None
    assert retried.state == "delivered"
    assert retried.attempts == 2
