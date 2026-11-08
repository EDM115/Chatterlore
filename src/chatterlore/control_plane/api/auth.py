from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from chatterlore.app_context import AppContext
from chatterlore.db.repos.events import EventRepository
from chatterlore.db.session import session_scope

from ..auth.mini_app import (
    MiniAppValidationError,
    classify_mini_app_validation_error,
    validate_mini_app_init_data,
)
from ..auth.oidc import OIDCValidationError
from ..auth.sessions import SENSITIVE_ACTIONS, AuthenticatedSession, IssuedSession
from ..auth.transactions import OIDCTransactionError
from ..authorization import CAPABILITY_CHAT_ADMIN, ControlPlaneActor
from ..middleware import SESSION_COOKIE_NAME
from ..schemas import (
    ControlPlaneErrorEnvelope,
    ControlPlaneSessionResponse,
    ControlPlaneUserResponse,
    DevelopmentStepUpRequest,
    MiniAppAuthRequest,
    MiniAppStepUpRequest,
    StepUpResponse,
)
from .common import (
    ControlPlaneAPIError,
    _authenticate,
    _check_rate_limit,
    _services,
    _session_token,
)

CONTROL_PLANE_DEV_AUTH_PATH = "/api/control/v1/auth/dev"

CONTROL_PLANE_DEV_STEP_UP_PATH = "/api/control/v1/auth/dev/step-up"


@dataclass(frozen=True, slots=True)
class DevelopmentAuthConfig:
    telegram_user_id: int


def _build_auth_router(development_auth: DevelopmentAuthConfig | None = None) -> APIRouter:
    router = APIRouter()

    if development_auth is not None:

        @router.post(
            CONTROL_PLANE_DEV_AUTH_PATH,
            response_model=ControlPlaneSessionResponse,
            include_in_schema=False,
        )
        async def development_login(request: Request) -> JSONResponse:
            context, runtime = _services(request)
            if await request.body():
                raise ControlPlaneAPIError(
                    422,
                    "development_identity_input_rejected",
                    "Development authentication does not accept identity input.",
                )
            now = datetime.now(UTC)
            identity = _development_identity(development_auth.telegram_user_id, now=now)
            issued = await runtime.sessions.issue(
                identity,
                client_kind="browser",
                now=now,
                replace_session_token=request.cookies.get(SESSION_COOKIE_NAME),
            )
            actor = await runtime.authorization.resolve(identity.telegram_user_id)
            response = JSONResponse(
                _session_payload_from_issue(
                    identity=identity, client_kind="browser", issued=issued, actor=actor
                )
            )
            _set_session_cookie(response, issued, context)
            await _audit(
                context,
                method="development",
                outcome="success",
                reason="authenticated",
                user_id=identity.telegram_user_id,
            )
            return response

        @router.post(
            CONTROL_PLANE_DEV_STEP_UP_PATH, response_model=StepUpResponse, include_in_schema=False
        )
        async def development_step_up(
            request: Request, payload: DevelopmentStepUpRequest
        ) -> StepUpResponse:
            context, runtime = _services(request)
            token = _session_token(request)
            authenticated = await _authenticate(runtime, token)
            if authenticated.identity.telegram_user_id != development_auth.telegram_user_id:
                await _audit(
                    context,
                    method="development",
                    outcome="rejected",
                    reason="identity_mismatch",
                    user_id=authenticated.identity.telegram_user_id,
                )
                raise ControlPlaneAPIError(
                    403,
                    "development_identity_mismatch",
                    "Development identity does not match the active session.",
                )
            if payload.action not in SENSITIVE_ACTIONS:
                raise ControlPlaneAPIError(
                    400, "step_up_action_invalid", "The requested step-up action is invalid."
                )
            verified_at = datetime.now(UTC)
            await runtime.sessions.mark_step_up(token, now=verified_at, action=payload.action)
            await _audit(
                context,
                method="development",
                outcome="success",
                reason=f"step_up:{payload.action}",
                user_id=authenticated.identity.telegram_user_id,
            )
            return StepUpResponse(step_up=True, verified_at=verified_at, action=payload.action)

    @router.get(
        "/auth/telegram/start",
        response_class=RedirectResponse,
        openapi_extra={"x-chatterlore-capability": "public_oidc"},
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
        "/auth/telegram/step-up/start",
        response_class=RedirectResponse,
        openapi_extra={"x-chatterlore-capability": "authenticated_same_identity"},
        responses={
            400: {"model": ControlPlaneErrorEnvelope},
            401: {"model": ControlPlaneErrorEnvelope},
            429: {"model": ControlPlaneErrorEnvelope},
        },
    )
    async def telegram_step_up_start(
        request: Request,
        action: str = Query(min_length=1, max_length=64),
        return_to: str = Query(default="/", max_length=512),
    ) -> RedirectResponse:
        context, runtime = _services(request)
        _check_rate_limit(
            request,
            bucket="oidc_step_up_start",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        token = _session_token(request)
        authenticated = await _authenticate(runtime, token)
        if action not in SENSITIVE_ACTIONS:
            raise ControlPlaneAPIError(
                400, "step_up_action_invalid", "The requested step-up action is invalid."
            )
        try:
            transaction = await runtime.transactions.create(
                return_to=return_to,
                now=datetime.now(UTC),
                action=f"step_up:{action}",
                expected_telegram_user_id=authenticated.identity.telegram_user_id,
            )
            target = await runtime.oidc.authorization_url(transaction)
        except (OIDCTransactionError, OIDCValidationError) as exc:
            raise ControlPlaneAPIError(
                400, "oidc_step_up_rejected", "Telegram verification could not be started."
            ) from exc
        return RedirectResponse(target)

    @router.get(
        "/auth/telegram/callback",
        response_class=RedirectResponse,
        openapi_extra={"x-chatterlore-capability": "public_oidc"},
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
        if transaction.action.startswith("step_up:"):
            action = transaction.action.removeprefix("step_up:")
            token = _session_token(request)
            authenticated = await _authenticate(runtime, token)
            if authenticated.identity.telegram_user_id != identity.telegram_user_id:
                raise ControlPlaneAPIError(
                    403,
                    "oidc_identity_mismatch",
                    "Telegram identity does not match the active session.",
                )
            await runtime.sessions.mark_step_up(token, now=datetime.now(UTC), action=action)
            await _audit(
                context,
                method="oidc",
                outcome="success",
                reason=f"step_up:{action}",
                user_id=identity.telegram_user_id,
            )
            return RedirectResponse(transaction.return_to, status_code=303)
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
        openapi_extra={"x-chatterlore-capability": "telegram_identity"},
        responses={
            401: {"model": ControlPlaneErrorEnvelope},
            403: {"model": ControlPlaneErrorEnvelope},
            422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
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
            await _audit(
                context,
                method="mini_app",
                outcome="rejected",
                reason=f"validation_{classify_mini_app_validation_error(exc)}",
            )
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
        openapi_extra={"x-chatterlore-capability": "authenticated"},
        responses={401: {"model": ControlPlaneErrorEnvelope}},
    )
    async def get_session(request: Request) -> dict[str, Any]:
        context, runtime = _services(request)
        token = _session_token(request)
        authenticated = await _authenticate(runtime, token)
        csrf_token = await runtime.sessions.get_csrf_token(token, now=datetime.now(UTC))
        actor = await runtime.authorization.resolve(authenticated.identity.telegram_user_id)
        return _session_payload(
            authenticated=authenticated,
            csrf_token=csrf_token,
            actor=actor,
            step_up_max_age_seconds=context.settings.control_plane_step_up_max_age_seconds,
        )

    @router.post(
        "/api/control/v1/auth/logout",
        status_code=204,
        openapi_extra={"x-chatterlore-capability": "authenticated"},
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
            SESSION_COOKIE_NAME,
            path="/",
            secure=_session_cookie_secure(context),
            httponly=True,
            samesite="lax",
        )
        return response

    @router.post(
        "/api/control/v1/auth/step-up/mini-app",
        response_model=StepUpResponse,
        openapi_extra={"x-chatterlore-capability": "authenticated_same_identity"},
        responses={
            401: {"model": ControlPlaneErrorEnvelope},
            403: {"model": ControlPlaneErrorEnvelope},
            422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
            429: {"model": ControlPlaneErrorEnvelope},
        },
    )
    async def mini_app_step_up(request: Request, payload: MiniAppStepUpRequest) -> StepUpResponse:
        context, runtime = _services(request)
        _check_rate_limit(
            request,
            bucket="mini_app_step_up",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        token = _session_token(request)
        authenticated = await _authenticate(runtime, token)
        if payload.action not in SENSITIVE_ACTIONS and payload.action != "generic":
            raise ControlPlaneAPIError(
                400, "step_up_action_invalid", "The requested step-up action is invalid."
            )
        try:
            identity = validate_mini_app_init_data(
                payload.init_data,
                bot_token=context.settings.telegram_bot_token,
                now=datetime.now(UTC),
                max_age_seconds=context.settings.control_plane_step_up_max_age_seconds,
            )
        except MiniAppValidationError as exc:
            await _audit(
                context,
                method="mini_app",
                outcome="rejected",
                reason=f"step_up_{classify_mini_app_validation_error(exc)}",
                user_id=authenticated.identity.telegram_user_id,
            )
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
        await runtime.sessions.mark_step_up(token, now=verified_at, action=payload.action)
        await _audit(
            context,
            method="mini_app",
            outcome="success",
            reason=f"step_up:{payload.action}",
            user_id=identity.telegram_user_id,
        )
        return StepUpResponse(step_up=True, verified_at=verified_at, action=payload.action)

    return router


def _set_session_cookie(response: Response, issued: IssuedSession, context: AppContext) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.session_token,
        max_age=context.settings.control_plane_session_absolute_seconds,
        expires=issued.absolute_expires_at,
        path="/",
        secure=_session_cookie_secure(context),
        httponly=True,
        samesite="lax",
    )


def _session_cookie_secure(context: AppContext) -> bool:
    return urlsplit(context.settings.control_plane_public_origin).scheme == "https"


def _development_identity(telegram_user_id: int, *, now: datetime):
    from ..auth.identity import TelegramIdentity

    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        first_name="Development administrator",
        last_name=None,
        username="chatterlore_control_plane_dev",
        language_code="en",
        photo_url=None,
        is_bot=False,
        is_premium=None,
        authenticated_at=now,
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
    *,
    authenticated: AuthenticatedSession,
    csrf_token: str,
    actor: ControlPlaneActor,
    step_up_max_age_seconds: int,
) -> dict[str, Any]:
    return {
        "user": _user_payload(authenticated.identity),
        "client_kind": authenticated.client_kind,
        "authenticated_at": authenticated.authenticated_at,
        "step_up_at": authenticated.step_up_at,
        "step_up_action": authenticated.step_up_action,
        "step_up_expires_at": authenticated.step_up_at + timedelta(seconds=step_up_max_age_seconds)
        if authenticated.step_up_at
        else None,
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
