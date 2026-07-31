from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from pzp_ai_bot.control_plane.auth.mini_app import (
    MiniAppValidationError,
    validate_mini_app_init_data,
)

BOT_TOKEN = "123456:telegram-secret"  # noqa: S105
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def signed_init_data(*, auth_date: datetime = NOW, user_id: int = 887_758_628) -> str:
    fields = {
        "auth_date": str(int(auth_date.timestamp())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(
            {
                "id": user_id,
                "is_bot": False,
                "first_name": "Lu",
                "last_name": "Lederrey",
                "username": "lederrey",
                "language_code": "en",
                "is_premium": True,
            },
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_mini_app_init_data_returns_typed_telegram_identity() -> None:
    result = validate_mini_app_init_data(
        signed_init_data(), bot_token=BOT_TOKEN, now=NOW, max_age_seconds=300
    )

    assert result.telegram_user_id == 887_758_628
    assert result.first_name == "Lu"
    assert result.last_name == "Lederrey"
    assert result.username == "lederrey"
    assert result.language_code == "en"
    assert result.is_premium is True
    assert result.authenticated_at == NOW


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (signed_init_data() + "&auth_date=1", "duplicate"),
        (signed_init_data().replace("AAHdF6IQAAAAAN0XohDhrOrc", "tampered"), "signature"),
        (signed_init_data(auth_date=NOW - timedelta(seconds=301)), "expired"),
        (signed_init_data(auth_date=NOW + timedelta(seconds=31)), "future"),
        (signed_init_data(user_id=0), "Telegram user"),
    ],
)
def test_mini_app_validation_rejects_ambiguous_tampered_or_stale_input(
    raw: str, error: str
) -> None:
    with pytest.raises(MiniAppValidationError, match=error):
        validate_mini_app_init_data(
            raw, bot_token=BOT_TOKEN, now=NOW, max_age_seconds=300, clock_skew_seconds=30
        )
