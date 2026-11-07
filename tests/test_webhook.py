from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiogram import Bot
from fastapi.testclient import TestClient

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.telegram.dispatcher import build_dispatcher
from pzp_ai_bot.telegram.webhook import create_webhook_app


class HealthBotDouble:
    def __init__(self) -> None:
        self.session = SimpleNamespace(close=self.close)

    async def delete_my_commands(self) -> None:
        return None

    async def set_webhook(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def close(self) -> None:
        return None


def test_webhook_rejects_bad_secret(context: AppContext) -> None:
    app = create_webhook_app(context)
    client = TestClient(app)
    response = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "bad"},
    )
    assert response.status_code == 401


def test_webhook_openapi_characterizes_current_reserved_routes(context: AppContext) -> None:
    app = create_webhook_app(context)

    assert set(app.openapi()["paths"]) == {"/telegram/webhook", "/health"}


@pytest.mark.parametrize(
    "secret", ["", "change-me", " Change-Me ", "changeme", "placeholder", "default", "secret"]
)
def test_webhook_rejects_default_or_blank_secret(context: AppContext, secret: str) -> None:
    context.settings.webhook_secret = secret

    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        create_webhook_app(context)


def test_health_reports_running_critical_background_tasks(context: AppContext) -> None:
    app = create_webhook_app(
        context, bot=cast(Bot, HealthBotDouble()), dispatcher=build_dispatcher(context)
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "tasks": {"pzp-idle-summarizer": True, "pzp-runtime-settings-refresher": True},
        "control_plane": {
            "enabled": False,
            "oidc_configured": False,
            "master_keys_configured": False,
            "session_store": "database",
            "provider_pools_enabled": False,
            "alert_worker": {"configured": False, "running": False, "critical": False},
        },
    }


def test_health_returns_503_when_critical_background_task_is_dead(context: AppContext) -> None:
    app = create_webhook_app(
        context, bot=cast(Bot, HealthBotDouble()), dispatcher=build_dispatcher(context)
    )

    with TestClient(app) as client:
        idle_handle = app.state.background_tasks["pzp-idle-summarizer"]
        cast(Any, client).portal.call(idle_handle.task.cancel)
        cast(Any, client).portal.call(asyncio.sleep, 0)
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "tasks": {"pzp-idle-summarizer": False, "pzp-runtime-settings-refresher": True},
        "control_plane": {
            "enabled": False,
            "oidc_configured": False,
            "master_keys_configured": False,
            "session_store": "database",
            "provider_pools_enabled": False,
            "alert_worker": {"configured": False, "running": False, "critical": False},
        },
    }
