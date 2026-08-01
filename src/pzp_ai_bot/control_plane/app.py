from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Never

from fastapi import APIRouter, Body, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response

from pzp_ai_bot.ai.credential_pool import ProviderCallError
from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.models import AdminAlert
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.session import session_scope

from .auth.mini_app import MiniAppValidationError, validate_mini_app_init_data
from .auth.oidc import OIDCValidationError, TelegramOIDCClient
from .auth.sessions import (
    SENSITIVE_ACTIONS,
    AuthenticatedSession,
    IssuedSession,
    SessionAuthenticationError,
    has_fresh_step_up,
)
from .auth.transactions import OIDCTransactionError
from .authorization import CAPABILITY_CHAT_ADMIN, ControlPlaneActor
from .middleware import (
    SESSION_COOKIE_NAME,
    ControlPlaneRequestGuardMiddleware,
    ControlPlaneSecurityHeadersMiddleware,
)
from .runtime import ControlPlaneRuntime
from .schemas import (
    AdminAlertPage,
    AdminAlertSummary,
    ControlPlaneConflictEnvelope,
    ControlPlaneErrorEnvelope,
    ControlPlaneSessionResponse,
    ControlPlaneUserResponse,
    DeleteMutationResponse,
    MiniAppAuthRequest,
    MiniAppStepUpRequest,
    ProviderCredentialCreateRequest,
    ProviderCredentialDeleteRequest,
    ProviderCredentialMutationResponse,
    ProviderCredentialRevealResponse,
    ProviderCredentialSummary,
    ProviderCredentialTestRequest,
    ProviderCredentialTestResponse,
    ProviderCredentialUpdateRequest,
    ProviderPoolSummary,
    ProviderProfileCreateRequest,
    ProviderProfileDeleteRequest,
    ProviderProfileMutationResponse,
    ProviderProfileResponse,
    ProviderProfileUpdateRequest,
    StepUpResponse,
)
from .secrets.service import ProviderSecretConflictError


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
    app.include_router(_build_provider_router())
    app.include_router(_build_alert_router())
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
    app.include_router(_build_provider_router())
    app.include_router(_build_alert_router())
    app.add_middleware(
        ControlPlaneRequestGuardMiddleware,
        settings=context.settings,
        sessions=context.control_plane.sessions,
    )
    app.add_middleware(ControlPlaneSecurityHeadersMiddleware)

    @app.exception_handler(ControlPlaneAPIError)
    async def control_plane_error_handler(_: Request, exc: ControlPlaneAPIError) -> JSONResponse:
        content: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message}}
        if exc.current is not None:
            content["current"] = exc.current
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def control_plane_validation_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields: dict[str, list[str]] = defaultdict(list)
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"] if part != "body")
            fields[location or "request"].append(str(error["msg"]))
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": "Request validation failed.",
                    "field_errors": fields,
                }
            },
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
        "/auth/telegram/step-up/start",
        response_class=RedirectResponse,
        openapi_extra={"x-pzp-capability": "authenticated_same_identity"},
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


def _build_provider_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/providers", tags=["providers"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope},
    }
    versioned_errors: dict[int | str, dict[str, Any]] = {
        **common_errors,
        409: {"model": ControlPlaneConflictEnvelope},
    }

    @router.get(
        "",
        response_model=list[ProviderProfileResponse],
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_providers(request: Request) -> list[dict[str, Any]]:
        _, runtime, _, _ = await _require_bot_admin(request)
        return [profile.as_dict() for profile in await runtime.secrets.list_profiles()]

    @router.post(
        "",
        response_model=ProviderProfileMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def create_provider(
        request: Request, payload: ProviderProfileCreateRequest
    ) -> dict[str, Any]:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            profile = await runtime.secrets.create_profile(
                payload.model_dump(),
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except ValueError as exc:
            _raise_provider_error(exc)
        return _profile_mutation_payload(profile)

    @router.get(
        "/{name}",
        response_model=ProviderProfileResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def get_provider(request: Request, name: str) -> dict[str, Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        try:
            return (await runtime.secrets.get_profile(name)).as_dict()
        except ValueError as exc:
            _raise_provider_error(exc)

    @router.patch(
        "/{name}",
        response_model=ProviderProfileMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=versioned_errors,
    )
    async def update_provider(
        request: Request, name: str, payload: ProviderProfileUpdateRequest
    ) -> dict[str, Any]:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        updates = payload.model_dump(exclude={"version"}, exclude_none=True)
        try:
            profile = await runtime.secrets.update_profile(
                name,
                expected_version=payload.version,
                updates=updates,
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return _profile_mutation_payload(profile)

    @router.delete(
        "/{name}",
        response_model=DeleteMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses={**versioned_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def delete_provider(
        request: Request, name: str, payload: ProviderProfileDeleteRequest = Body()
    ) -> DeleteMutationResponse:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="provider_delete",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="profile:delete", context=context)
        try:
            audit_event_id = await runtime.secrets.delete_profile(
                name,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return DeleteMutationResponse(audit_event_id=audit_event_id, version=payload.version + 1)

    @router.get(
        "/{name}/credentials",
        response_model=list[ProviderCredentialSummary],
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_provider_credentials(request: Request, name: str) -> list[dict[str, Any]]:
        _, runtime, _, _ = await _require_bot_admin(request)
        try:
            return [item.as_dict() for item in await runtime.secrets.list_credentials(name)]
        except ValueError as exc:
            _raise_provider_error(exc)

    @router.get(
        "/{name}/pool",
        response_model=ProviderPoolSummary,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def get_provider_pool(request: Request, name: str) -> dict[str, Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        try:
            return (await runtime.credential_pool.summary(name)).as_dict()
        except ValueError as exc:
            _raise_provider_error(exc)

    @router.post(
        "/{name}/credentials",
        response_model=ProviderCredentialMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses={**common_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def create_provider_credential(
        request: Request, name: str, payload: ProviderCredentialCreateRequest
    ) -> dict[str, Any]:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="credential_create",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="credential:create", context=context)
        if len(payload.value) > context.settings.control_plane_max_secret_length:
            raise ControlPlaneAPIError(
                422, "credential_invalid", "The provider credential is invalid."
            )
        try:
            credential = await runtime.secrets.create_credential(
                name,
                label=payload.label,
                value=payload.value,
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except ValueError as exc:
            _raise_provider_error(exc)
        return _credential_mutation_payload(credential)

    @router.patch(
        "/{name}/credentials/{credential_id}",
        response_model=ProviderCredentialMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=versioned_errors,
    )
    async def update_provider_credential(
        request: Request, name: str, credential_id: int, payload: ProviderCredentialUpdateRequest
    ) -> dict[str, Any]:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            credential = await runtime.secrets.update_credential(
                name,
                credential_id,
                expected_version=payload.version,
                updates=payload.model_dump(exclude={"version"}, exclude_none=True),
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return _credential_mutation_payload(credential)

    @router.post(
        "/{name}/credentials/{credential_id}/reveal",
        response_model=ProviderCredentialRevealResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses={**common_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def reveal_provider_credential(
        request: Request, name: str, credential_id: int
    ) -> JSONResponse:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="credential_reveal",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="credential:reveal", context=context)
        try:
            value = await runtime.secrets.reveal_credential(
                name, credential_id, actor_user_id=authenticated.identity.telegram_user_id
            )
        except ValueError as exc:
            _raise_provider_error(exc)
        expires_at = datetime.now(UTC) + timedelta(seconds=30)
        return JSONResponse(
            {"credential_id": credential_id, "value": value, "expires_at": expires_at.isoformat()},
            headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"},
        )

    @router.post(
        "/{name}/credentials/{credential_id}/test",
        response_model=ProviderCredentialTestResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses={
            **common_errors,
            429: {"model": ControlPlaneErrorEnvelope},
            503: {"model": ControlPlaneErrorEnvelope},
        },
    )
    async def test_provider_credential(
        request: Request, name: str, credential_id: int, payload: ProviderCredentialTestRequest
    ) -> dict[str, Any]:
        context, _, _, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="provider_credential_test",
            limit=context.settings.control_plane_provider_test_rate_limit,
        )
        try:
            return await context.memory.provider_registry.test_managed_credential(
                name, credential_id, mode=payload.mode
            )
        except ProviderCallError as exc:
            raise ControlPlaneAPIError(
                503, "provider_credential_test_failed", "The provider credential check failed."
            ) from exc
        except ValueError as exc:
            _raise_provider_error(exc)

    @router.delete(
        "/{name}/credentials/{credential_id}",
        response_model=DeleteMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses={**versioned_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def delete_provider_credential(
        request: Request,
        name: str,
        credential_id: int,
        payload: ProviderCredentialDeleteRequest = Body(),
    ) -> DeleteMutationResponse:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="credential_delete",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="credential:delete", context=context)
        try:
            audit_event_id = await runtime.secrets.delete_credential(
                name,
                credential_id,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return DeleteMutationResponse(audit_event_id=audit_event_id, version=payload.version + 1)

    return router


def _build_alert_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/alerts", tags=["alerts"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope},
    }

    @router.get(
        "",
        response_model=AdminAlertPage,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_admin_alerts(
        request: Request,
        cursor: int | None = Query(default=None, ge=1),
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        alerts = await runtime.alerts.list_alerts(limit=limit + 1, before_id=cursor)
        has_more = len(alerts) > limit
        page = alerts[:limit]
        return {
            "items": [_alert_payload(alert) for alert in page],
            "next_cursor": page[-1].id if has_more and page else None,
            "has_more": has_more,
        }

    @router.post(
        "/{alert_id}/retry",
        response_model=AdminAlertSummary,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses={**common_errors, 409: {"model": ControlPlaneErrorEnvelope}},
    )
    async def retry_admin_alert(request: Request, alert_id: int) -> dict[str, Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        alert = await runtime.alerts.retry(alert_id, now=datetime.now(UTC))
        if alert is None:
            raise ControlPlaneAPIError(
                409, "admin_alert_not_retryable", "The admin alert cannot be retried."
            )
        return _alert_payload(alert)

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


def _profile_mutation_payload(profile: Any) -> dict[str, Any]:
    payload = profile.as_dict()
    payload["audit_event_id"] = int(profile.audit_event_id)
    return payload


def _credential_mutation_payload(credential: Any) -> dict[str, Any]:
    payload = credential.as_dict()
    payload["audit_event_id"] = int(credential.audit_event_id)
    return payload


def _alert_payload(alert: AdminAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "parent_intent_id": alert.parent_intent_id,
        "alert_type": alert.alert_type,
        "profile_name": alert.profile_name,
        "credential_fingerprint": alert.credential_fingerprint,
        "credential_label": alert.credential_label,
        "health_epoch": alert.health_epoch,
        "recipient_telegram_id": alert.recipient_telegram_id,
        "state": alert.state,
        "attempts": alert.attempts,
        "next_attempt_at": alert.next_attempt_at,
        "created_at": alert.created_at,
        "last_attempt_at": alert.last_attempt_at,
        "delivered_at": alert.delivered_at,
        "error_classification": alert.error_classification,
    }


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
