from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from chatterlore.control_plane.auth.mini_app import (
    MiniAppValidationError,
    classify_mini_app_validation_error,
    validate_mini_app_init_data,
)

BOT_TOKEN = "123456:telegram-secret"  # noqa: S105
WRONG_BOT_TOKEN = "999999:wrong-bot-secret"  # noqa: S105
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def valid_fields(*, auth_date: datetime = NOW, user_id: int = 887_758_628) -> dict[str, str]:
    return {
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


def sign_fields(fields: dict[str, str], *, bot_token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signed = {
        **fields,
        "hash": hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest(),
    }
    return urlencode(signed)


def signed_init_data(*, auth_date: datetime = NOW, user_id: int = 887_758_628) -> str:
    return sign_fields(valid_fields(auth_date=auth_date, user_id=user_id))


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


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("auth_date=1&user=%ZZ&hash=" + "0" * 64, "malformed"),
        ("auth_date=1&user=%2&hash=" + "0" * 64, "malformed"),
        (urlencode({"auth_date": "1", "user": "{}"}), "signature"),
    ],
)
def test_mini_app_validation_rejects_malformed_encoding_and_missing_hash(
    raw: str, error: str
) -> None:
    with pytest.raises(MiniAppValidationError, match=error):
        validate_mini_app_init_data(raw, bot_token=BOT_TOKEN, now=NOW, max_age_seconds=300)


@pytest.mark.parametrize(
    ("field_updates", "removed_field", "error"),
    [
        ({"auth_date": str(10**100)}, None, "auth_date"),
        ({"user": "not-json"}, None, "user payload"),
        ({"user": "[]"}, None, "user payload"),
        ({"user": json.dumps({"id": True, "first_name": "Lu"})}, None, "user ID"),
        (
            {"user": json.dumps({"id": 9_223_372_036_854_775_808, "first_name": "Lu"})},
            None,
            "user ID",
        ),
        ({"user": json.dumps({"id": 887_758_628})}, None, "first_name"),
        (
            {"user": json.dumps({"id": 887_758_628, "first_name": "Lu", "is_bot": True})},
            None,
            "human account",
        ),
        ({}, "user", "user payload"),
    ],
)
def test_mini_app_validation_rejects_signed_invalid_identity_payloads(
    field_updates: dict[str, str], removed_field: str | None, error: str
) -> None:
    fields = valid_fields()
    fields.update(field_updates)
    if removed_field is not None:
        fields.pop(removed_field)

    with pytest.raises(MiniAppValidationError, match=error):
        validate_mini_app_init_data(
            sign_fields(fields), bot_token=BOT_TOKEN, now=NOW, max_age_seconds=300
        )


def test_mini_app_validation_rejects_a_valid_shape_signed_by_another_bot() -> None:
    raw = sign_fields(valid_fields(), bot_token=WRONG_BOT_TOKEN)

    with pytest.raises(MiniAppValidationError, match="signature"):
        validate_mini_app_init_data(raw, bot_token=BOT_TOKEN, now=NOW, max_age_seconds=300)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Mini App authentication data has expired", "expired"),
        ("Mini App authentication date is in the future", "future"),
        ("Mini App signature is invalid", "signature"),
        ("Mini App signature is missing or malformed", "signature"),
        ("Telegram user ID is invalid", "identity"),
        ("Telegram user first_name is invalid", "identity"),
        ("Mini App validation is not configured", "configuration"),
        ("validation time must be timezone-aware", "configuration"),
        ("Mini App init data has an invalid size", "size"),
        ("Mini App init data is malformed", "encoding"),
        ("duplicate Mini App field: attacker-controlled-name", "duplicate"),
        ("Mini App auth_date is invalid", "auth_date"),
        ("unexpected internal validator detail", "malformed"),
    ],
)
def test_mini_app_validation_error_classification_is_bounded(message: str, expected: str) -> None:
    classification = classify_mini_app_validation_error(MiniAppValidationError(message))

    assert classification == expected
    assert classification in {
        "configuration",
        "auth_date",
        "duplicate",
        "encoding",
        "expired",
        "future",
        "identity",
        "malformed",
        "signature",
        "size",
    }
