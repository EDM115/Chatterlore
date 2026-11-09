from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl

from .identity import TelegramIdentity, profile_string

MAX_INIT_DATA_BYTES = 16_384
MAX_INIT_DATA_FIELDS = 64
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class MiniAppValidationError(ValueError):
    pass


MiniAppValidationClassification = Literal[
    "auth_date",
    "configuration",
    "duplicate",
    "encoding",
    "expired",
    "future",
    "identity",
    "malformed",
    "signature",
    "size",
]


def classify_mini_app_validation_error(
    error: MiniAppValidationError,
) -> MiniAppValidationClassification:
    message = str(error)
    if message == "Mini App authentication data has expired":
        return "expired"
    if message == "Mini App authentication date is in the future":
        return "future"
    if message.startswith("Mini App signature "):
        return "signature"
    if message.startswith("Telegram user "):
        return "identity"
    if message == "Mini App init data has an invalid size":
        return "size"
    if message == "Mini App init data is malformed":
        return "encoding"
    if message.startswith("duplicate Mini App field:"):
        return "duplicate"
    if message == "Mini App auth_date is invalid":
        return "auth_date"
    if message in {
        "Mini App validation is not configured",
        "validation time must be timezone-aware",
    }:
        return "configuration"
    return "malformed"


def validate_mini_app_init_data(
    raw_init_data: str,
    *,
    bot_token: str,
    now: datetime,
    max_age_seconds: int,
    clock_skew_seconds: int = 30,
) -> TelegramIdentity:
    if not raw_init_data or len(raw_init_data.encode("utf-8")) > MAX_INIT_DATA_BYTES:
        raise MiniAppValidationError("Mini App init data has an invalid size")
    if not bot_token:
        raise MiniAppValidationError("Mini App validation is not configured")
    _validate_percent_encoding(raw_init_data)
    try:
        pairs = parse_qsl(
            raw_init_data,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=MAX_INIT_DATA_FIELDS,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise MiniAppValidationError("Mini App init data is malformed") from exc
    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise MiniAppValidationError(f"duplicate Mini App field: {key}")
        fields[key] = value
    received_hash = fields.pop("hash", "")
    if len(received_hash) != 64:
        raise MiniAppValidationError("Mini App signature is missing or malformed")
    try:
        bytes.fromhex(received_hash)
    except ValueError as exc:
        raise MiniAppValidationError("Mini App signature is missing or malformed") from exc
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash.lower(), expected_hash):
        raise MiniAppValidationError("Mini App signature is invalid")
    authenticated_at = _auth_date(fields.get("auth_date"))
    utc_now = _as_utc(now)
    age_seconds = (utc_now - authenticated_at).total_seconds()
    if age_seconds < -clock_skew_seconds:
        raise MiniAppValidationError("Mini App authentication date is in the future")
    if age_seconds > max_age_seconds:
        raise MiniAppValidationError("Mini App authentication data has expired")
    return _telegram_identity(fields.get("user"), authenticated_at=authenticated_at)


def _validate_percent_encoding(raw: str) -> None:
    for index, character in enumerate(raw):
        if character != "%":
            continue
        if (
            index + 2 >= len(raw)
            or raw[index + 1] not in HEX_DIGITS
            or raw[index + 2] not in HEX_DIGITS
        ):
            raise MiniAppValidationError("Mini App init data is malformed")


def _auth_date(raw: str | None) -> datetime:
    try:
        timestamp = int(raw or "")
    except ValueError as exc:
        raise MiniAppValidationError("Mini App auth_date is invalid") from exc
    if timestamp < 1:
        raise MiniAppValidationError("Mini App auth_date is invalid")
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise MiniAppValidationError("Mini App auth_date is invalid") from exc


def _telegram_identity(raw: str | None, *, authenticated_at: datetime) -> TelegramIdentity:
    try:
        user = json.loads(raw or "")
    except json.JSONDecodeError as exc:
        raise MiniAppValidationError("Telegram user payload is invalid") from exc
    if not isinstance(user, dict):
        raise MiniAppValidationError("Telegram user payload is invalid")
    telegram_user_id = user.get("id")
    if (
        isinstance(telegram_user_id, bool)
        or not isinstance(telegram_user_id, int)
        or telegram_user_id < 1
        or telegram_user_id > 9_223_372_036_854_775_807
    ):
        raise MiniAppValidationError("Telegram user ID is invalid")
    is_bot = user.get("is_bot", False)
    if not isinstance(is_bot, bool) or is_bot:
        raise MiniAppValidationError("Telegram user must be a human account")
    is_premium = user.get("is_premium")
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        first_name=profile_string(user, "first_name") or f"Telegram user {telegram_user_id}",
        last_name=profile_string(user, "last_name"),
        username=profile_string(user, "username"),
        language_code=profile_string(user, "language_code"),
        photo_url=profile_string(user, "photo_url"),
        is_bot=False,
        is_premium=is_premium if isinstance(is_premium, bool) else None,
        authenticated_at=authenticated_at,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise MiniAppValidationError("validation time must be timezone-aware")
    return value.astimezone(UTC)
