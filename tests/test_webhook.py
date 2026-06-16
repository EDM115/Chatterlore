from __future__ import annotations

from fastapi.testclient import TestClient

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.telegram.webhook import create_webhook_app


def test_webhook_rejects_bad_secret(context: AppContext) -> None:
    app = create_webhook_app(context)
    client = TestClient(app)
    response = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "bad"},
    )
    assert response.status_code == 401
