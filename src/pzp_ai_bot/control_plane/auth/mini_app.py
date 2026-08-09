from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

from .identity import TelegramIdentity

MAX_INIT_DATA_BYTES = 16_384
MAX_INIT_DATA_FIELDS = 64
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class MiniAppValidationError(ValueError):
    pass


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
    first_name = _required_string(user, "first_name", maximum=255)
    is_bot = user.get("is_bot", False)
    if not isinstance(is_bot, bool) or is_bot:
        raise MiniAppValidationError("Telegram user must be a human account")
    is_premium = user.get("is_premium")
    if is_premium is not None and not isinstance(is_premium, bool):
        raise MiniAppValidationError("Telegram user premium flag is invalid")
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        first_name=first_name,
        last_name=_optional_string(user, "last_name", maximum=255),
        username=_optional_string(user, "username", maximum=255),
        language_code=_optional_string(user, "language_code", maximum=32),
        photo_url=_optional_string(user, "photo_url", maximum=512),
        is_bot=False,
        is_premium=is_premium,
        authenticated_at=authenticated_at,
    )


def _required_string(payload: dict[str, Any], key: str, *, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise MiniAppValidationError(f"Telegram user {key} is invalid")
    return value


def _optional_string(payload: dict[str, Any], key: str, *, maximum: int) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise MiniAppValidationError(f"Telegram user {key} is invalid")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise MiniAppValidationError("validation time must be timezone-aware")
    return value.astimezone(UTC)
