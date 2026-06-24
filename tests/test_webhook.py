from __future__ import annotations

import pytest
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


@pytest.mark.parametrize(
    "secret", ["", "change-me", " Change-Me ", "changeme", "placeholder", "default", "secret"]
)
def test_webhook_rejects_default_or_blank_secret(context: AppContext, secret: str) -> None:
    context.settings.webhook_secret = secret

    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        create_webhook_app(context)
