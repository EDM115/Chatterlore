from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)

DEFAULT_COOLDOWN_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 3_600
_QUOTA_ERROR_CODES = frozenset({"insufficient_quota"})


class ProviderFailureClassification(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    TRANSPORT = "transport"
    PROVIDER = "provider"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderFailureDecision:
    classification: ProviderFailureClassification
    same_key_retry: bool
    state_transition: Literal["cooldown", "disabled"] | None
    rotate: bool
    retry_after_seconds: int | None = None
    alert_type: Literal["credential_cooldown", "credential_disabled"] | None = None


def classify_provider_failure(
    error: Exception, *, now: datetime | None = None
) -> ProviderFailureDecision:
    current = now or datetime.now(UTC)
    if isinstance(error, AuthenticationError):
        return _disabled(ProviderFailureClassification.AUTHENTICATION)
    if isinstance(error, PermissionDeniedError):
        return _disabled(ProviderFailureClassification.PERMISSION)
    if isinstance(error, RateLimitError):
        if _safe_error_code(error) in _QUOTA_ERROR_CODES:
            return _disabled(ProviderFailureClassification.QUOTA)
        return _cooldown(
            ProviderFailureClassification.RATE_LIMIT,
            same_key_retry=False,
            retry_after_seconds=_bounded_retry_after(error, current),
        )
    if isinstance(error, (APITimeoutError, APIConnectionError)):
        return _cooldown(ProviderFailureClassification.TRANSPORT, same_key_retry=True)
    if isinstance(error, APIStatusError):
        status_code = error.status_code
        if status_code in {408, 409} or status_code >= 500:
            return _cooldown(ProviderFailureClassification.PROVIDER, same_key_retry=True)
        if 400 <= status_code < 500:
            return ProviderFailureDecision(
                classification=ProviderFailureClassification.INVALID_REQUEST,
                same_key_retry=False,
                state_transition=None,
                rotate=False,
            )
    return ProviderFailureDecision(
        classification=ProviderFailureClassification.UNKNOWN,
        same_key_retry=False,
        state_transition=None,
        rotate=False,
    )


def _disabled(classification: ProviderFailureClassification) -> ProviderFailureDecision:
    return ProviderFailureDecision(
        classification=classification,
        same_key_retry=False,
        state_transition="disabled",
        rotate=True,
        alert_type="credential_disabled",
    )


def _cooldown(
    classification: ProviderFailureClassification,
    *,
    same_key_retry: bool,
    retry_after_seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> ProviderFailureDecision:
    return ProviderFailureDecision(
        classification=classification,
        same_key_retry=same_key_retry,
        state_transition="cooldown",
        rotate=True,
        retry_after_seconds=retry_after_seconds,
        alert_type="credential_cooldown",
    )


def _safe_error_code(error: APIStatusError) -> str | None:
    code = getattr(error, "code", None)
    if code is None and isinstance(error.body, dict):
        body_error = error.body.get("error")
        code = body_error.get("code") if isinstance(body_error, dict) else error.body.get("code")
    if not isinstance(code, str):
        return None
    normalized = code.strip().lower()
    return normalized if normalized in _QUOTA_ERROR_CODES else None


def _bounded_retry_after(error: APIStatusError, now: datetime) -> int:
    raw = error.response.headers.get("Retry-After")
    if raw is None:
        return DEFAULT_COOLDOWN_SECONDS
    try:
        seconds = int(raw)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return DEFAULT_COOLDOWN_SECONDS
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = int((retry_at - now).total_seconds())
    return max(1, min(seconds, MAX_RETRY_AFTER_SECONDS))


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "MAX_RETRY_AFTER_SECONDS",
    "ProviderFailureClassification",
    "ProviderFailureDecision",
    "classify_provider_failure",
]
