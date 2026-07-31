from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.session import session_scope

from .auth.mini_app import MiniAppValidationError, validate_mini_app_init_data
from .auth.oidc import OIDCValidationError, TelegramOIDCClient
from .auth.sessions import AuthenticatedSession, IssuedSession, SessionAuthenticationError
from .auth.transactions import OIDCTransactionError
from .authorization import CAPABILITY_CHAT_ADMIN, ControlPlaneActor
from .middleware import (
    SESSION_COOKIE_NAME,
    ControlPlaneRequestGuardMiddleware,
    ControlPlaneSecurityHeadersMiddleware,
)
from .runtime import ControlPlaneRuntime
from .schemas import (
    ControlPlaneErrorEnvelope,
    ControlPlaneSessionResponse,
    ControlPlaneUserResponse,
    MiniAppAuthRequest,
    StepUpResponse,
)


class ControlPlaneAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
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


def create_control_plane_app(
    context: AppContext, *, oidc_client: TelegramOIDCClient | Any | None = None
) -> FastAPI:
    runtime = context.control_plane
    if not context.settings.control_plane_enabled or runtime is None:
        raise RuntimeError("the control plane is not enabled")
    if oidc_client is not None:
        runtime.oidc = oidc_client

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await runtime.oidc.aclose()

    app = FastAPI(
        title="PZP AI Control Plane API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    install_control_plane(app, context)
    return app


def create_control_plane_schema_app() -> FastAPI:
    app = FastAPI(
        title="PZP AI Control Plane API",
        summary="Telegram-authenticated control-plane contract",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(_build_auth_router())
    return app


def install_control_plane(app: FastAPI, context: AppContext) -> None:
    if context.control_plane is None:
        raise RuntimeError("the control plane is not enabled")
    app.state.control_plane_context = context
    app.state.control_plane_runtime = context.control_plane
    app.state.control_plane_rate_limiter = RateLimiter(
        window_seconds=context.settings.control_plane_rate_limit_window_seconds
    )
    app.include_router(_build_auth_router())
    app.add_middleware(
        ControlPlaneRequestGuardMiddleware,
        settings=context.settings,
        sessions=context.control_plane.sessions,
    )
    app.add_middleware(ControlPlaneSecurityHeadersMiddleware)

    @app.exception_handler(ControlPlaneAPIError)
    async def control_plane_error_handler(_: Request, exc: ControlPlaneAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )


def _build_auth_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/auth/telegram/start",
        response_class=RedirectResponse,
        openapi_extra={"x-pzp-capability": "public_oidc"},
        responses={429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def telegram_start(
        request: Request, return_to: str = Query(default="/", max_length=512)
    ) -> RedirectResponse:
        context, runtime = _services(request)
        _check_rate_limit(
            request, bucket="oidc_start", limit=context.settings.control_plane_auth_start_rate_limit
        )
        try:
            transaction = await runtime.transactions.create(
                return_to=return_to, now=datetime.now(UTC)
            )
            target = await runtime.oidc.authorization_url(transaction)
        except (OIDCTransactionError, OIDCValidationError) as exc:
            raise ControlPlaneAPIError(
                400, "oidc_start_rejected", "Telegram sign-in could not be started."
            ) from exc
        return RedirectResponse(target)

    @router.get(
        "/auth/telegram/callback",
        response_class=RedirectResponse,
        openapi_extra={"x-pzp-capability": "public_oidc"},
        responses={
            400: {"model": ControlPlaneErrorEnvelope},
            401: {"model": ControlPlaneErrorEnvelope},
            429: {"model": ControlPlaneErrorEnvelope},
        },
    )
    async def telegram_callback(
        request: Request, state: str = "", code: str = "", error: str = ""
    ) -> RedirectResponse:
        context, runtime = _services(request)
        _check_rate_limit(
            request,
            bucket="oidc_callback",
            limit=context.settings.control_plane_auth_callback_rate_limit,
        )
        try:
            transaction = await runtime.transactions.consume(state, now=datetime.now(UTC))
        except OIDCTransactionError as exc:
            await _audit(context, method="oidc", outcome="rejected", reason="state")
            raise ControlPlaneAPIError(
                400, "oidc_state_rejected", "Telegram sign-in state is invalid or expired."
            ) from exc
        if error:
            await _audit(context, method="oidc", outcome="rejected", reason="oauth")
            raise ControlPlaneAPIError(
                400, "oidc_provider_rejected", "Telegram sign-in was not completed."
            )
        if not code:
            await _audit(context, method="oidc", outcome="rejected", reason="missing_code")
            raise ControlPlaneAPIError(
                400, "oidc_code_missing", "Telegram sign-in code is missing."
            )
        try:
            identity = await runtime.oidc.exchange_code(
                code=code,
                code_verifier=transaction.code_verifier,
                redirect_uri=transaction.redirect_uri,
                expected_nonce=transaction.nonce,
                now=datetime.now(UTC),
            )
        except OIDCValidationError as exc:
            await _audit(context, method="oidc", outcome="rejected", reason="token")
            raise ControlPlaneAPIError(
                401, "oidc_identity_rejected", "Telegram identity could not be verified."
            ) from exc
        if (
            transaction.expected_telegram_user_id is not None
            and transaction.expected_telegram_user_id != identity.telegram_user_id
        ):
            await _audit(context, method="oidc", outcome="rejected", reason="identity")
            raise ControlPlaneAPIError(
                403, "oidc_identity_mismatch", "Telegram identity does not match the session."
            )
        issued = await runtime.sessions.issue(
            identity,
            client_kind="browser",
            now=datetime.now(UTC),
            replace_session_token=request.cookies.get(SESSION_COOKIE_NAME),
        )
        await _audit(
            context,
            method="oidc",
            outcome="success",
            reason="authenticated",
            user_id=identity.telegram_user_id,
        )
        response = RedirectResponse(transaction.return_to, status_code=303)
        _set_session_cookie(response, issued, context)
        return response

    @router.post(
        "/api/control/v1/auth/mini-app",
        response_model=ControlPlaneSessionResponse,
        openapi_extra={"x-pzp-capability": "telegram_identity"},
        responses={
            401: {"model": ControlPlaneErrorEnvelope},
            403: {"model": ControlPlaneErrorEnvelope},
            429: {"model": ControlPlaneErrorEnvelope},
        },
    )
    async def mini_app_login(request: Request, payload: MiniAppAuthRequest) -> JSONResponse:
        context, runtime = _services(request)
        _check_rate_limit(
            request, bucket="mini_app", limit=context.settings.control_plane_mini_app_rate_limit
        )
        try:
            identity = validate_mini_app_init_data(
                payload.init_data,
                bot_token=context.settings.telegram_bot_token,
                now=datetime.now(UTC),
                max_age_seconds=context.settings.control_plane_auth_max_age_seconds,
            )
        except MiniAppValidationError as exc:
            await _audit(context, method="mini_app", outcome="rejected", reason="signature")
            raise ControlPlaneAPIError(
                401,
                "mini_app_identity_rejected",
                "Telegram Mini App identity could not be verified.",
            ) from exc
        issued = await runtime.sessions.issue(
            identity,
            client_kind="mini_app",
            now=datetime.now(UTC),
            replace_session_token=request.cookies.get(SESSION_COOKIE_NAME),
        )
        actor = await runtime.authorization.resolve(identity.telegram_user_id)
        response = JSONResponse(
            _session_payload_from_issue(
                identity=identity, client_kind="mini_app", issued=issued, actor=actor
            )
        )
        _set_session_cookie(response, issued, context)
        await _audit(
            context,
            method="mini_app",
            outcome="success",
            reason="authenticated",
            user_id=identity.telegram_user_id,
        )
        return response

    @router.get(
        "/api/control/v1/session",
        response_model=ControlPlaneSessionResponse,
        openapi_extra={"x-pzp-capability": "authenticated"},
        responses={401: {"model": ControlPlaneErrorEnvelope}},
    )
    async def get_session(request: Request) -> dict[str, Any]:
        _, runtime = _services(request)
        token = _session_token(request)
        authenticated = await _authenticate(runtime, token)
        csrf_token = await runtime.sessions.rotate_csrf(token, now=datetime.now(UTC))
        actor = await runtime.authorization.resolve(authenticated.identity.telegram_user_id)
        return _session_payload(authenticated=authenticated, csrf_token=csrf_token, actor=actor)

    @router.post(
        "/api/control/v1/auth/logout",
        status_code=204,
        openapi_extra={"x-pzp-capability": "authenticated"},
        responses={
            401: {"model": ControlPlaneErrorEnvelope},
            403: {"model": ControlPlaneErrorEnvelope},
        },
    )
    async def logout(request: Request) -> Response:
        context, runtime = _services(request)
        token = _session_token(request)
        authenticated = await _authenticate(runtime, token)
        await runtime.sessions.revoke(token, now=datetime.now(UTC), reason="logout")
        await _audit(
            context,
            method=authenticated.client_kind,
            outcome="success",
            reason="logout",
            user_id=authenticated.identity.telegram_user_id,
        )
        response = Response(status_code=204)
        response.delete_cookie(
            SESSION_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax"
        )
        return response

    @router.post(
        "/api/control/v1/auth/step-up/mini-app",
        response_model=StepUpResponse,
        openapi_extra={"x-pzp-capability": "authenticated_same_identity"},
        responses={
            401: {"model": ControlPlaneErrorEnvelope},
            403: {"model": ControlPlaneErrorEnvelope},
            429: {"model": ControlPlaneErrorEnvelope},
        },
    )
    async def mini_app_step_up(request: Request, payload: MiniAppAuthRequest) -> StepUpResponse:
        context, runtime = _services(request)
        _check_rate_limit(
            request,
            bucket="mini_app_step_up",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        token = _session_token(request)
        authenticated = await _authenticate(runtime, token)
        try:
            identity = validate_mini_app_init_data(
                payload.init_data,
                bot_token=context.settings.telegram_bot_token,
                now=datetime.now(UTC),
                max_age_seconds=context.settings.control_plane_step_up_max_age_seconds,
            )
        except MiniAppValidationError as exc:
            raise ControlPlaneAPIError(
                401,
                "mini_app_step_up_rejected",
                "Telegram Mini App identity could not be verified.",
            ) from exc
        if identity.telegram_user_id != authenticated.identity.telegram_user_id:
            await _audit(
                context,
                method="mini_app",
                outcome="rejected",
                reason="identity_mismatch",
                user_id=authenticated.identity.telegram_user_id,
            )
            raise ControlPlaneAPIError(
                403,
                "mini_app_identity_mismatch",
                "Telegram identity does not match the active session.",
            )
        verified_at = datetime.now(UTC)
        await runtime.sessions.mark_step_up(token, now=verified_at)
        await _audit(
            context,
            method="mini_app",
            outcome="success",
            reason="step_up",
            user_id=identity.telegram_user_id,
        )
        return StepUpResponse(step_up=True, verified_at=verified_at)

    return router


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


def _check_rate_limit(request: Request, *, bucket: str, limit: int) -> None:
    client = request.client.host if request.client else "unknown"
    retry_after = request.app.state.control_plane_rate_limiter.check(
        bucket=bucket, client=client, limit=limit
    )
    if retry_after is not None:
        raise ControlPlaneAPIError(429, "rate_limit_exceeded", "Too many authentication attempts.")


def _set_session_cookie(response: Response, issued: IssuedSession, context: AppContext) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.session_token,
        max_age=context.settings.control_plane_session_absolute_seconds,
        expires=issued.absolute_expires_at,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _session_payload_from_issue(
    *, identity, client_kind: str, issued: IssuedSession, actor: ControlPlaneActor
) -> dict[str, Any]:
    return {
        "user": _user_payload(identity),
        "client_kind": client_kind,
        "authenticated_at": identity.authenticated_at.isoformat(),
        "step_up_at": None,
        "expires_at": issued.absolute_expires_at.isoformat(),
        "csrf_token": issued.csrf_token,
        "capabilities": sorted(actor.capabilities),
        "chat_capabilities": _chat_capabilities(actor),
    }


def _session_payload(
    *, authenticated: AuthenticatedSession, csrf_token: str, actor: ControlPlaneActor
) -> dict[str, Any]:
    return {
        "user": _user_payload(authenticated.identity),
        "client_kind": authenticated.client_kind,
        "authenticated_at": authenticated.authenticated_at,
        "step_up_at": authenticated.step_up_at,
        "expires_at": authenticated.absolute_expires_at,
        "csrf_token": csrf_token,
        "capabilities": sorted(actor.capabilities),
        "chat_capabilities": _chat_capabilities(actor),
    }


def _user_payload(identity) -> dict[str, Any]:
    return ControlPlaneUserResponse(
        telegram_user_id=identity.telegram_user_id,
        first_name=identity.first_name,
        last_name=identity.last_name,
        username=identity.username,
        language_code=identity.language_code,
        photo_url=identity.photo_url,
        is_premium=identity.is_premium,
    ).model_dump(mode="json")


def _chat_capabilities(actor: ControlPlaneActor) -> dict[str, list[str]]:
    return {
        str(chat_id): [CAPABILITY_CHAT_ADMIN]
        for chat_id in sorted(actor.delegated_chat_ids | actor.native_chat_ids)
    }


async def _audit(
    context: AppContext, *, method: str, outcome: str, reason: str, user_id: int | None = None
) -> None:
    async with session_scope(context.session_factory) as session:
        await EventRepository(session).add(
            event_type=f"control_plane.auth.{outcome}",
            user_id=user_id,
            payload={"method": method, "outcome": outcome, "reason": reason},
        )
