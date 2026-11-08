from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any, Never

from fastapi import Request

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.settings_profiles import SettingsVersionConflictError

from ..auth.sessions import AuthenticatedSession, SessionAuthenticationError, has_fresh_step_up
from ..authorization import CAPABILITY_CHAT_ADMIN, ControlPlaneActor
from ..management import ControlPlaneManagementConflictError, ControlPlaneManagementError
from ..middleware import SESSION_COOKIE_NAME
from ..runtime import ControlPlaneRuntime
from ..secrets.service import ProviderSecretConflictError


class ControlPlaneAPIError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, *, current: dict[str, Any] | None = None
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.current = current
        super().__init__(message)


class RateLimiter:
    def __init__(self, *, window_seconds: int) -> None:
        self.window_seconds = window_seconds
        self._requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, *, bucket: str, client: str, limit: int) -> int | None:
        now = time.monotonic()
        timestamps = self._requests[(bucket, client)]
        while timestamps and timestamps[0] <= now - self.window_seconds:
            timestamps.popleft()
        if len(timestamps) >= limit:
            return max(1, int(self.window_seconds - (now - timestamps[0])))
        timestamps.append(now)
        return None


def _services(request: Request) -> tuple[AppContext, ControlPlaneRuntime]:
    return (request.app.state.control_plane_context, request.app.state.control_plane_runtime)


def _session_token(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        raise ControlPlaneAPIError(401, "session_required", "An authenticated session is required.")
    return token


async def _authenticate(runtime: ControlPlaneRuntime, token: str) -> AuthenticatedSession:
    try:
        return await runtime.sessions.authenticate(token, now=datetime.now(UTC))
    except SessionAuthenticationError as exc:
        raise ControlPlaneAPIError(
            401, "session_invalid", "The authenticated session is invalid or expired."
        ) from exc


async def _require_bot_admin(
    request: Request,
) -> tuple[AppContext, ControlPlaneRuntime, AuthenticatedSession, ControlPlaneActor]:
    context, runtime = _services(request)
    authenticated = await _authenticate(runtime, _session_token(request))
    actor = await runtime.authorization.resolve(authenticated.identity.telegram_user_id)
    if not actor.is_bot_admin:
        raise ControlPlaneAPIError(
            403, "bot_admin_required", "Bot administrator access is required."
        )
    return context, runtime, authenticated, actor


async def _require_authenticated(
    request: Request,
) -> tuple[AppContext, ControlPlaneRuntime, AuthenticatedSession, ControlPlaneActor]:
    context, runtime = _services(request)
    authenticated = await _authenticate(runtime, _session_token(request))
    actor = await runtime.authorization.resolve(authenticated.identity.telegram_user_id)
    return context, runtime, authenticated, actor


async def _require_chat_admin(
    request: Request, chat_id: int
) -> tuple[AppContext, ControlPlaneRuntime, AuthenticatedSession, ControlPlaneActor]:
    context, runtime = _services(request)
    authenticated = await _authenticate(runtime, _session_token(request))
    bot = getattr(request.app.state, "telegram_bot", None)
    actor = await runtime.authorization.resolve(
        authenticated.identity.telegram_user_id, bot=bot, chat_id=chat_id
    )
    if actor.is_bot_admin:
        return context, runtime, authenticated, actor
    try:
        managed = await runtime.management.get_managed_chat(chat_id)
    except ControlPlaneManagementError as exc:
        _raise_management_error(exc)
    if managed.status != "active" or CAPABILITY_CHAT_ADMIN not in actor.capabilities_for_chat(
        chat_id
    ):
        raise ControlPlaneAPIError(
            403, "chat_admin_required", "Administrator access to this managed chat is required."
        )
    return context, runtime, authenticated, actor


async def _require_active_query_scope(
    request: Request, raw_scope: str
) -> tuple[AppContext, ControlPlaneRuntime, Scope]:
    try:
        scope = parse_scope(raw_scope)
    except ValueError as exc:
        raise ControlPlaneAPIError(422, "scope_invalid", "The requested scope is invalid.") from exc
    if scope.chat_id is None:
        raise ControlPlaneAPIError(422, "scope_invalid", "A concrete chat scope is required.")
    context, runtime, _, _ = await _require_chat_admin(request, scope.chat_id)
    try:
        managed = await runtime.management.get_managed_chat(scope.chat_id)
    except ControlPlaneManagementError as exc:
        _raise_management_error(exc)
    if managed.status != "active":
        raise ControlPlaneAPIError(
            409, "managed_chat_inactive", "The requested managed chat is not active."
        )
    return context, runtime, scope


async def _require_memory_scope(
    request: Request, raw_scope: str
) -> tuple[AppContext, Scope, AuthenticatedSession]:
    try:
        scope = parse_scope(raw_scope)
    except ValueError as exc:
        raise ControlPlaneAPIError(422, "scope_invalid", "The requested scope is invalid.") from exc
    if scope.is_global:
        context, _, authenticated, _ = await _require_bot_admin(request)
        return context, scope, authenticated
    assert scope.chat_id is not None
    context, _, authenticated, _ = await _require_chat_admin(request, scope.chat_id)
    return context, scope, authenticated


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _require_step_up(
    authenticated: AuthenticatedSession, *, action: str, context: AppContext
) -> None:
    if not has_fresh_step_up(
        authenticated,
        action=action,
        now=datetime.now(UTC),
        max_age_seconds=context.settings.control_plane_step_up_max_age_seconds,
    ):
        raise ControlPlaneAPIError(
            403, "step_up_required", "Fresh Telegram verification is required for this action."
        )


def _raise_provider_error(exc: ValueError) -> Never:
    dependency = getattr(exc, "code", None)
    if dependency in {"embedding_migration_required", "profile_in_use"}:
        raise ControlPlaneAPIError(
            409,
            dependency,
            "This provider is referenced by an embedding index. "
            "Create a new profile and migrate the index before changing or removing it.",
        ) from exc
    if isinstance(exc, ProviderSecretConflictError):
        raise ControlPlaneAPIError(
            409,
            "version_conflict",
            "The resource changed concurrently.",
            current={"version": exc.current_version},
        ) from exc
    message = str(exc)
    if "not found" in message:
        raise ControlPlaneAPIError(
            404, "provider_resource_not_found", "The provider resource was not found."
        ) from exc
    if "already exists" in message:
        raise ControlPlaneAPIError(
            409,
            "provider_already_exists",
            "The managed provider profile already exists.",
            current={},
        ) from exc
    raise ControlPlaneAPIError(
        422, "provider_request_invalid", "The provider request is invalid."
    ) from exc


def _raise_management_error(exc: ControlPlaneManagementError) -> Never:
    if isinstance(exc, ControlPlaneManagementConflictError):
        raise ControlPlaneAPIError(
            409,
            "version_conflict",
            "The resource changed concurrently.",
            current={"version": exc.current_version},
        ) from exc
    if exc.code.endswith("_not_found"):
        raise ControlPlaneAPIError(404, exc.code, "The requested resource was not found.") from exc
    if exc.code in {
        "bootstrap_owner_immutable",
        "last_owner_required",
        "managed_chat_inactive",
        "chat_enrollment_admin_required",
        "chat_enrollment_bot_missing",
    }:
        raise ControlPlaneAPIError(409, exc.code, str(exc)) from exc
    raise ControlPlaneAPIError(422, exc.code, str(exc)) from exc


def _raise_settings_conflict(exc: SettingsVersionConflictError) -> Never:
    raise ControlPlaneAPIError(
        409,
        "version_conflict",
        "The settings resource changed concurrently.",
        current={"version": exc.current_version},
    ) from exc


def _telegram_bot(request: Request) -> Any:
    bot = getattr(request.app.state, "telegram_bot", None)
    if bot is None:
        raise ControlPlaneAPIError(
            503,
            "telegram_runtime_unavailable",
            "Telegram-backed management is unavailable in this process.",
        )
    return bot


def _check_rate_limit(request: Request, *, bucket: str, limit: int) -> None:
    client = request.client.host if request.client else "unknown"
    retry_after = request.app.state.control_plane_rate_limiter.check(
        bucket=bucket, client=client, limit=limit
    )
    if retry_after is not None:
        raise ControlPlaneAPIError(429, "rate_limit_exceeded", "Too many authentication attempts.")
