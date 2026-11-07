from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Never
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from pzp_ai_bot.ai.credential_pool import ProviderCallError
from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.models import AdminAlert
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.contracts import AIRequestOptions
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.settings_profiles import SettingsVersionConflictError
from pzp_ai_bot.memory.transfer import MemoryExportFormat

from .auth.mini_app import (
    MiniAppValidationError,
    classify_mini_app_validation_error,
    validate_mini_app_init_data,
)
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
from .management import ControlPlaneManagementConflictError, ControlPlaneManagementError
from .middleware import (
    SESSION_COOKIE_NAME,
    ControlPlaneRequestGuardMiddleware,
    ControlPlaneSecurityHeadersMiddleware,
)
from .runtime import ControlPlaneRuntime
from .schemas import (
    AdminAlertPage,
    AdminAlertSummary,
    AIUsagePage,
    AuditEventPage,
    BotAdminCreateRequest,
    BotAdminResponse,
    ChatAdminCreateRequest,
    ChatAdminResponse,
    ChatEnrollmentCreateRequest,
    ChatEnrollmentResponse,
    ChatForgetResponse,
    ConfirmedChatActionRequest,
    ControlPlaneConflictEnvelope,
    ControlPlaneErrorEnvelope,
    ControlPlaneSessionResponse,
    ControlPlaneUserResponse,
    DeleteMutationResponse,
    DevelopmentStepUpRequest,
    ManagedChatMutationResponse,
    ManagedChatPage,
    ManagedChatResponse,
    ManagedChatStatusRequest,
    MemoryItemPage,
    MemoryItemRefreshRequest,
    MemoryItemRefreshResponse,
    MemoryItemResponse,
    MemoryRawSourcesResponse,
    MemoryReviewMutationResponse,
    MemoryReviewPage,
    MiniAppAuthRequest,
    MiniAppStepUpRequest,
    OperationCheckRequest,
    OperationCheckResponse,
    OperationsHealthResponse,
    ProviderCredentialCreateRequest,
    ProviderCredentialDeleteRequest,
    ProviderCredentialMutationResponse,
    ProviderCredentialReplaceRequest,
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
    QueryAskRequest,
    QueryAskResponse,
    QueryCatalogResponse,
    QuerySearchRequest,
    QuerySearchResponse,
    RuntimeSettingResponse,
    RuntimeSettingsMutationResponse,
    RuntimeSettingsUpdateRequest,
    ScopeSettingsMutationResponse,
    ScopeSettingsResponse,
    ScopeSettingsUpdateRequest,
    StatsOverviewResponse,
    StatsTimeseriesResponse,
    StepUpResponse,
    VersionedMutationRequest,
)
from .secrets.service import ProviderSecretConflictError

CONTROL_PLANE_DEV_AUTH_PATH = "/api/control/v1/auth/dev"
CONTROL_PLANE_DEV_STEP_UP_PATH = "/api/control/v1/auth/dev/step-up"


@dataclass(frozen=True, slots=True)
class DevelopmentAuthConfig:
    telegram_user_id: int


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
    context: AppContext,
    *,
    oidc_client: TelegramOIDCClient | Any | None = None,
    development_auth: DevelopmentAuthConfig | None = None,
) -> FastAPI:
    runtime = context.control_plane
    if not context.settings.control_plane_enabled or runtime is None:
        raise RuntimeError("the control plane is not enabled")
    if oidc_client is not None:
        runtime.oidc = oidc_client

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
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
    install_control_plane(app, context, development_auth=development_auth)
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
    app.include_router(_build_management_router())
    app.include_router(_build_query_router())
    app.include_router(_build_memory_router())
    app.include_router(_build_stats_router())
    app.include_router(_build_audit_router())
    app.include_router(_build_operations_router())
    return app


def install_control_plane(
    app: FastAPI, context: AppContext, *, development_auth: DevelopmentAuthConfig | None = None
) -> None:
    if context.control_plane is None:
        raise RuntimeError("the control plane is not enabled")
    app.state.control_plane_context = context
    app.state.control_plane_runtime = context.control_plane
    app.state.control_plane_rate_limiter = RateLimiter(
        window_seconds=context.settings.control_plane_rate_limit_window_seconds
    )
    app.include_router(_build_auth_router(development_auth))
    app.include_router(_build_provider_router())
    app.include_router(_build_alert_router())
    app.include_router(_build_management_router())
    app.include_router(_build_query_router())
    app.include_router(_build_memory_router())
    app.include_router(_build_stats_router())
    app.include_router(_build_audit_router())
    app.include_router(_build_operations_router())
    public_post_paths = {"/api/control/v1/auth/mini-app"}
    if development_auth is not None:
        public_post_paths.add(CONTROL_PLANE_DEV_AUTH_PATH)
    app.add_middleware(
        ControlPlaneRequestGuardMiddleware,
        settings=context.settings,
        sessions=context.control_plane.sessions,
        public_post_paths=frozenset(public_post_paths),
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
        openapi_extra={"x-pzp-capability": "authenticated"},
        responses={401: {"model": ControlPlaneErrorEnvelope}},
    )
    async def get_session(request: Request) -> dict[str, Any]:
        _, runtime = _services(request)
        token = _session_token(request)
        authenticated = await _authenticate(runtime, token)
        csrf_token = await runtime.sessions.get_csrf_token(token, now=datetime.now(UTC))
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
        openapi_extra={"x-pzp-capability": "authenticated_same_identity"},
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


def _build_provider_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/providers", tags=["providers"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
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
        "/{name}/credentials/{credential_id}/replace",
        response_model=ProviderCredentialMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses={**versioned_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def replace_provider_credential(
        request: Request, name: str, credential_id: int, payload: ProviderCredentialReplaceRequest
    ) -> dict[str, Any]:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="credential_replace",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="credential:replace", context=context)
        if len(payload.value) > context.settings.control_plane_max_secret_length:
            raise ControlPlaneAPIError(
                422, "credential_invalid", "The provider credential is invalid."
            )
        try:
            credential = await runtime.secrets.replace_credential(
                name,
                credential_id,
                expected_version=payload.version,
                value=payload.value,
                label=payload.label,
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
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
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


def _build_management_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1", tags=["management"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        404: {"model": ControlPlaneErrorEnvelope},
        409: {"model": ControlPlaneConflictEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
    }

    @router.get(
        "/bot-settings/admins",
        response_model=list[BotAdminResponse],
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_bot_admins(request: Request) -> list[Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        return await runtime.management.list_bot_admins()

    @router.post(
        "/bot-settings/admins",
        response_model=BotAdminResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def create_bot_admin(request: Request, payload: BotAdminCreateRequest) -> Any:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            return await runtime.management.grant_bot_admin(
                telegram_user_id=payload.telegram_user_id,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.delete(
        "/bot-settings/admins/{telegram_user_id}",
        response_model=BotAdminResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses=common_errors,
    )
    async def delete_bot_admin(
        request: Request, telegram_user_id: int, payload: VersionedMutationRequest = Body()
    ) -> Any:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _require_step_up(authenticated, action="bot_admin:delete", context=context)
        try:
            return await runtime.management.revoke_bot_admin(
                telegram_user_id=telegram_user_id,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.get(
        "/bot-settings/runtime",
        response_model=list[RuntimeSettingResponse],
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_runtime_settings(request: Request) -> list[Any]:
        context, _, _, _ = await _require_bot_admin(request)
        return await context.memory.list_runtime_settings()

    @router.patch(
        "/bot-settings/runtime",
        response_model=RuntimeSettingsMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def update_runtime_settings(
        request: Request, payload: RuntimeSettingsUpdateRequest
    ) -> dict[str, Any]:
        context, _, authenticated, _ = await _require_bot_admin(request)
        try:
            mutation = await context.memory.set_runtime_setting_versioned(
                payload.updates,
                expected_version=payload.version,
                updated_by=authenticated.identity.telegram_user_id,
            )
        except SettingsVersionConflictError as exc:
            _raise_settings_conflict(exc)
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "runtime_settings_invalid", "The runtime settings update is invalid."
            ) from exc
        return {
            "items": await context.memory.list_runtime_settings(),
            "version": mutation.version,
            "audit_event_id": mutation.audit_event_id,
        }

    @router.post(
        "/chats/enrollment-requests",
        response_model=ChatEnrollmentResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def create_chat_enrollment(request: Request, payload: ChatEnrollmentCreateRequest) -> Any:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        bot = _telegram_bot(request)
        try:
            return await runtime.management.create_chat_enrollment(
                telegram_user_id=authenticated.identity.telegram_user_id,
                bot=bot,
                delivery=payload.delivery,
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.get(
        "/chats/enrollment-requests/{request_id}",
        response_model=ChatEnrollmentResponse,
        openapi_extra={"x-pzp-capability": "authenticated"},
        responses=common_errors,
    )
    async def get_chat_enrollment(request: Request, request_id: int) -> Any:
        _, runtime, authenticated, _ = await _require_authenticated(request)
        try:
            return await runtime.management.get_chat_enrollment(
                request_id=request_id, telegram_user_id=authenticated.identity.telegram_user_id
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.get(
        "/chats",
        response_model=ManagedChatPage,
        openapi_extra={"x-pzp-capability": "authenticated_admin"},
        responses=common_errors,
    )
    async def list_managed_chats(
        request: Request,
        cursor: int | None = Query(default=None, ge=1),
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, Any]:
        _, runtime, authenticated, actor = await _require_authenticated(request)
        chats, next_cursor, has_more = await runtime.management.list_managed_chats_page(
            limit=limit, before_id=cursor
        )
        if actor.is_bot_admin:
            return {"items": chats, "next_cursor": next_cursor, "has_more": has_more}
        bot = getattr(request.app.state, "telegram_bot", None)
        visible: list[Any] = []
        for chat in chats:
            if chat.status != "active":
                continue
            resolved = await runtime.authorization.resolve(
                authenticated.identity.telegram_user_id, bot=bot, chat_id=chat.chat_id
            )
            if CAPABILITY_CHAT_ADMIN in resolved.capabilities_for_chat(chat.chat_id):
                visible.append(chat)
        return {"items": visible, "next_cursor": next_cursor, "has_more": has_more}

    @router.patch(
        "/chats/{chat_id}/status",
        response_model=ManagedChatMutationResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def update_managed_chat_status(
        request: Request, chat_id: int, payload: ManagedChatStatusRequest
    ) -> Any:
        _, runtime, authenticated, _ = await _require_chat_admin(request, chat_id)
        try:
            managed = await runtime.management.set_chat_status(
                chat_id=chat_id,
                status=payload.status,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
            return _managed_chat_mutation_payload(managed)
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.post(
        "/chats/{chat_id}/leave",
        response_model=ManagedChatResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses=common_errors,
    )
    async def leave_managed_chat(
        request: Request, chat_id: int, payload: ConfirmedChatActionRequest
    ) -> Any:
        context, runtime, authenticated, actor = await _require_chat_admin(request, chat_id)
        if not actor.is_bot_admin:
            raise ControlPlaneAPIError(
                403, "bot_admin_required", "Bot administrator access is required."
            )
        _require_step_up(authenticated, action="chat:leave", context=context)
        if payload.confirmation != f"LEAVE {chat_id}":
            raise ControlPlaneAPIError(
                422, "chat_confirmation_invalid", f"Type LEAVE {chat_id} to confirm this action."
            )
        try:
            return await runtime.management.leave_chat(
                chat_id=chat_id,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
                bot=_telegram_bot(request),
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.post(
        "/chats/{chat_id}/forget",
        response_model=ChatForgetResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses=common_errors,
    )
    async def forget_managed_chat(
        request: Request, chat_id: int, payload: ConfirmedChatActionRequest
    ) -> dict[str, Any]:
        context, runtime, authenticated, actor = await _require_chat_admin(request, chat_id)
        if not actor.is_bot_admin:
            raise ControlPlaneAPIError(
                403, "bot_admin_required", "Bot administrator access is required."
            )
        _require_step_up(authenticated, action="chat:forget", context=context)
        if payload.confirmation != f"FORGET {chat_id}":
            raise ControlPlaneAPIError(
                422, "chat_confirmation_invalid", f"Type FORGET {chat_id} to confirm this action."
            )
        managed = await runtime.management.get_managed_chat(chat_id)
        if managed.version != payload.version:
            raise ControlPlaneAPIError(
                409,
                "version_conflict",
                "The resource changed concurrently.",
                current={"version": managed.version},
            )
        result = await runtime.management.forget_chat(
            chat_id=chat_id, actor_user_id=authenticated.identity.telegram_user_id
        )
        return {"chat_id": chat_id, "version": managed.version, **result}

    @router.get(
        "/chats/{chat_id}/admins",
        response_model=list[ChatAdminResponse],
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_chat_admins(request: Request, chat_id: int) -> list[Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        return await runtime.management.list_chat_admins(chat_id)

    @router.post(
        "/chats/{chat_id}/admins",
        response_model=ChatAdminResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def create_chat_admin(
        request: Request, chat_id: int, payload: ChatAdminCreateRequest
    ) -> Any:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            return await runtime.management.grant_chat_admin(
                chat_id=chat_id,
                telegram_user_id=payload.telegram_user_id,
                actor_user_id=authenticated.identity.telegram_user_id,
                bot=_telegram_bot(request),
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.delete(
        "/chats/{chat_id}/admins/{telegram_user_id}",
        response_model=ChatAdminResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def delete_chat_admin(
        request: Request,
        chat_id: int,
        telegram_user_id: int,
        payload: VersionedMutationRequest = Body(),
    ) -> Any:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            return await runtime.management.revoke_chat_admin(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.get(
        "/chats/{chat_id}/settings",
        response_model=ScopeSettingsResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def get_chat_settings(
        request: Request, chat_id: int, thread_id: int | None = Query(default=None)
    ) -> dict[str, Any]:
        context, runtime, _, _ = await _require_chat_admin(request, chat_id)
        return await _scope_settings_payload(context, runtime, Scope(chat_id, thread_id))

    @router.patch(
        "/chats/{chat_id}/settings",
        response_model=ScopeSettingsMutationResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def update_chat_settings(
        request: Request,
        chat_id: int,
        payload: ScopeSettingsUpdateRequest,
        thread_id: int | None = Query(default=None),
    ) -> dict[str, Any]:
        context, runtime, authenticated, _ = await _require_chat_admin(request, chat_id)
        if (
            not payload.settings
            and payload.learning_enabled is None
            and payload.allow_admins is None
        ):
            raise ControlPlaneAPIError(
                422, "scope_settings_empty", "At least one scope setting or policy is required."
            )
        scope = Scope(chat_id, thread_id)
        try:
            mutation = await context.memory.update_scope_configuration(
                scope,
                payload.settings,
                learning_enabled=payload.learning_enabled,
                allow_admins=payload.allow_admins,
                expected_version=payload.version,
                updated_by=authenticated.identity.telegram_user_id,
            )
        except SettingsVersionConflictError as exc:
            _raise_settings_conflict(exc)
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "scope_settings_invalid", "The scope settings update is invalid."
            ) from exc
        response = await _scope_settings_payload(context, runtime, scope)
        response["audit_event_id"] = mutation.audit_event_id
        return response

    return router


def _build_query_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/query", tags=["query"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        404: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
        429: {"model": ControlPlaneErrorEnvelope},
    }

    @router.get(
        "/catalog",
        response_model=QueryCatalogResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def query_catalog(request: Request, scope: str = Query(min_length=1, max_length=128)):
        _, runtime, parsed = await _require_active_query_scope(request, scope)
        return await runtime.catalog.for_scope(parsed.key)

    @router.post(
        "/search",
        response_model=QuerySearchResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def query_search(request: Request, payload: QuerySearchRequest) -> dict[str, Any]:
        context, _, scope = await _require_active_query_scope(request, payload.scope)
        try:
            execution = await context.memory.search_with_metadata(
                scope,
                payload.query,
                top_k=payload.top_k,
                profile_name=payload.memory_profile,
                ai_options=_query_ai_options(payload),
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "query_selection_invalid", "The query selection is invalid."
            ) from exc
        return _query_execution_payload(execution)

    @router.post(
        "/ask",
        response_model=QueryAskResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def query_ask(request: Request, payload: QueryAskRequest) -> dict[str, Any]:
        context, _, scope = await _require_active_query_scope(request, payload.scope)
        try:
            execution = await context.memory.ask_with_metadata(
                scope,
                payload.question,
                top_k=payload.top_k,
                profile_name=payload.memory_profile,
                ai_options=_query_ai_options(payload),
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "query_selection_invalid", "The query selection is invalid."
            ) from exc
        return {
            **_query_execution_payload(execution),
            "answer": execution.answer,
            "insufficient_context": execution.insufficient_context,
        }

    return router


def _build_memory_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/memory", tags=["memory"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        404: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
        429: {"model": ControlPlaneErrorEnvelope},
    }

    @router.get(
        "/export",
        response_class=StreamingResponse,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def export_memory(
        request: Request,
        scope: str = Query(min_length=1, max_length=128),
        fmt: MemoryExportFormat = Query(default="md", alias="format"),
    ) -> StreamingResponse:
        context, parsed, _ = await _require_memory_scope(request, scope)
        suffix = "md" if fmt == "md" else "jsonl"
        media_type = "text/markdown" if fmt == "md" else "application/x-ndjson"
        return StreamingResponse(
            _memory_export_stream(context, None if parsed.is_global else parsed, fmt),
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="memory_{parsed.filename_stem}.{suffix}"'
                ),
                "Cache-Control": "no-store, private",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get(
        "/items",
        response_model=MemoryItemPage,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def list_memory_items(
        request: Request,
        scope: str = Query(min_length=1, max_length=128),
        kind: str | None = Query(default=None, min_length=1, max_length=64),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        context, parsed, _ = await _require_memory_scope(request, scope)
        items = await context.memory.browse_items_page(
            None if parsed.is_global else parsed, kind=kind, after_id=cursor, limit=limit + 1
        )
        has_more = len(items) > limit
        page = items[:limit]
        return {
            "items": [_memory_item_payload(item) for item in page],
            "next_cursor": page[-1].id if has_more and page else None,
            "has_more": has_more,
        }

    @router.get(
        "/items/{item_id}",
        response_model=MemoryItemResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def get_memory_item(request: Request, item_id: int) -> dict[str, Any]:
        _context, item = await _authorized_memory_item(request, item_id)
        return _memory_item_payload(item)

    @router.post(
        "/items/{item_id}/sources",
        response_model=MemoryRawSourcesResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def get_memory_item_sources(request: Request, item_id: int) -> dict[str, Any]:
        context, item = await _authorized_memory_item(request, item_id)
        authenticated = await _authenticate(
            request.app.state.control_plane_runtime, _session_token(request)
        )
        scope, sources = await context.memory.memory_item_sources(
            item.id, accessed_by=authenticated.identity.telegram_user_id, limit=100
        )
        return {
            "item_id": item.id,
            "scope": scope.key,
            "sources": [
                {
                    "telegram_message_id": source.telegram_message_id,
                    "user_id": source.user_id,
                    "username": source.username,
                    "text": source.text,
                    "created_at": _as_utc(source.created_at),
                    "message_permalink": source.message_permalink,
                }
                for source in sources
            ],
        }

    @router.post(
        "/items/{item_id}/refresh",
        response_model=MemoryItemRefreshResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def refresh_memory_item(
        request: Request, item_id: int, payload: MemoryItemRefreshRequest
    ) -> dict[str, Any]:
        context, item = await _authorized_memory_item(request, item_id)
        authenticated = await _authenticate(
            request.app.state.control_plane_runtime, _session_token(request)
        )
        try:
            result = await context.memory.refresh_memory_item(
                item.scope,
                item.id,
                mode=payload.mode,
                updated_by=authenticated.identity.telegram_user_id,
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "memory_refresh_invalid", "The memory refresh request is invalid."
            ) from exc
        return {
            "id": result.id,
            "scope": result.scope.key,
            "kind": result.kind,
            "mode": result.mode,
            "summarized": result.summarized,
            "embeddings_refreshed": result.embeddings_refreshed,
        }

    @router.delete(
        "/items/{item_id}",
        response_model=MemoryItemResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def delete_memory_item(request: Request, item_id: int) -> dict[str, Any]:
        context, item = await _authorized_memory_item(request, item_id)
        authenticated = await _authenticate(
            request.app.state.control_plane_runtime, _session_token(request)
        )
        deleted = await context.memory.delete_memory_item(
            item.scope, item.id, deleted_by=authenticated.identity.telegram_user_id
        )
        return _memory_item_payload(deleted)

    @router.get(
        "/reviews",
        response_model=MemoryReviewPage,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def list_memory_reviews(
        request: Request,
        scope: str = Query(min_length=1, max_length=128),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        context, parsed, _ = await _require_memory_scope(request, scope)
        reviews = await context.memory.pending_reviews_page(
            None if parsed.is_global else parsed, after_id=cursor, limit=limit + 1
        )
        has_more = len(reviews) > limit
        page = reviews[:limit]
        return {
            "items": [_memory_review_payload(review) for review in page],
            "next_cursor": page[-1].id if has_more and page else None,
            "has_more": has_more,
        }

    @router.post(
        "/reviews/{review_id}/approve",
        response_model=MemoryReviewMutationResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def approve_memory_review(request: Request, review_id: int) -> dict[str, Any]:
        context, authenticated = await _authorized_pending_review(request, review_id)
        try:
            item_id = await context.memory.approve_review(
                review_id, reviewed_by=authenticated.identity.telegram_user_id
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                404, "memory_review_not_found", "The pending memory review was not found."
            ) from exc
        return {"review_id": review_id, "status": "approved", "knowledge_item_id": item_id}

    @router.post(
        "/reviews/{review_id}/reject",
        response_model=MemoryReviewMutationResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def reject_memory_review(request: Request, review_id: int) -> dict[str, Any]:
        context, authenticated = await _authorized_pending_review(request, review_id)
        try:
            await context.memory.reject_review(
                review_id, reviewed_by=authenticated.identity.telegram_user_id
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                404, "memory_review_not_found", "The pending memory review was not found."
            ) from exc
        return {"review_id": review_id, "status": "rejected", "knowledge_item_id": None}

    return router


def _build_stats_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/stats", tags=["stats"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
    }

    @router.get(
        "/overview",
        response_model=StatsOverviewResponse,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def stats_overview(
        request: Request, scope: str = Query(default="all", min_length=1, max_length=128)
    ) -> dict[str, Any]:
        _, parsed, _ = await _require_memory_scope(request, scope)
        _, runtime = _services(request)
        payload = await runtime.insights.overview(None if parsed.is_global else parsed)
        payload["runtime"]["scheduler"] = _scheduler_status(request)
        return payload

    @router.get(
        "/timeseries",
        response_model=StatsTimeseriesResponse,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def stats_timeseries(
        request: Request,
        scope: str = Query(default="all", min_length=1, max_length=128),
        days: int = Query(default=30, ge=1, le=366),
    ) -> dict[str, Any]:
        _, parsed, _ = await _require_memory_scope(request, scope)
        _, runtime = _services(request)
        return await runtime.insights.timeseries(None if parsed.is_global else parsed, days=days)

    return router


def _build_audit_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/audit", tags=["audit"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
    }

    @router.get(
        "/events",
        response_model=AuditEventPage,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def audit_events(
        request: Request,
        scope: str = Query(default="all", min_length=1, max_length=128),
        event_type: str | None = Query(default=None, min_length=1, max_length=64),
        cursor: int | None = Query(default=None, ge=1),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        _, parsed, _ = await _require_memory_scope(request, scope)
        _, runtime = _services(request)
        return await runtime.audit.events(
            scope=None if parsed.is_global else parsed,
            event_type=event_type,
            before_id=cursor,
            limit=limit,
        )

    @router.get(
        "/ai-usage",
        response_model=AIUsagePage,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def audit_ai_usage(
        request: Request,
        scope: str = Query(default="all", min_length=1, max_length=128),
        cursor: int | None = Query(default=None, ge=1),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        _, parsed, _ = await _require_memory_scope(request, scope)
        _, runtime = _services(request)
        return await runtime.audit.ai_usage(
            scope=None if parsed.is_global else parsed, before_id=cursor, limit=limit
        )

    return router


def _build_operations_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/operations", tags=["operations"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
    }

    @router.get(
        "/health",
        response_model=OperationsHealthResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def operations_health(request: Request) -> dict[str, Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        return await runtime.operations.health()

    @router.post(
        "/checks/{name}",
        response_model=OperationCheckResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def run_operation_check(
        request: Request, name: str, payload: OperationCheckRequest
    ) -> dict[str, Any]:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            return await runtime.operations.run_check(
                name,
                actor_user_id=authenticated.identity.telegram_user_id,
                profile_name=payload.profile_name,
                mode=payload.mode,
                allow_download=payload.allow_download,
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "operation_check_invalid", "The requested operation check is invalid."
            ) from exc

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


async def _authorized_memory_item(request: Request, item_id: int) -> tuple[AppContext, Any]:
    context, _ = _services(request)
    try:
        item = await context.memory.memory_item(item_id)
    except ValueError as exc:
        raise ControlPlaneAPIError(
            404, "memory_item_not_found", "The requested memory item was not found."
        ) from exc
    await _require_memory_scope(request, item.scope.key)
    return context, item


async def _authorized_pending_review(
    request: Request, review_id: int
) -> tuple[AppContext, AuthenticatedSession]:
    context, _ = _services(request)
    try:
        scope = await context.memory.pending_review_scope(review_id)
    except ValueError as exc:
        raise ControlPlaneAPIError(
            404, "memory_review_not_found", "The pending memory review was not found."
        ) from exc
    context, _, authenticated = await _require_memory_scope(request, scope.key)
    return context, authenticated


def _query_ai_options(payload: Any) -> AIRequestOptions:
    return AIRequestOptions(
        provider_profile=payload.provider_profile,
        retrieval_provider_profile=payload.retrieval_provider_profile,
        chat_provider_profile=payload.chat_provider_profile,
        chat_model=payload.chat_model,
        embedding_model=payload.embedding_model,
        reasoning_level=payload.reasoning_level,
    )


def _query_execution_payload(execution: Any) -> dict[str, Any]:
    selection = execution.selection
    return {
        "destination_scope": execution.destination_scope.key,
        "source_scopes": [scope.key for scope in execution.source_scopes],
        "memory_profile": execution.memory_profile,
        "selection": {
            "retrieval_provider_profile": selection.retrieval_provider_profile,
            "embedding_model": selection.embedding_model,
            "chat_provider_profile": selection.chat_provider_profile,
            "chat_model": selection.chat_model,
            "reasoning_level": selection.reasoning_level,
        },
        "results": [
            {
                "content": result.content[:4_000],
                "score": result.score,
                "source": result.source,
                "scope": result.scope.key,
                "source_message_ids": result.source_message_ids[:100],
            }
            for result in execution.results
        ],
        "elapsed_ms": execution.elapsed_ms,
    }


def _memory_item_payload(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "scope": item.scope.key,
        "kind": item.kind,
        "content": item.content,
        "source_message_ids": item.source_message_ids,
        "metadata": item.metadata,
        "created_at": _as_utc(item.created_at),
        "updated_at": _as_utc(item.updated_at),
    }


def _memory_review_payload(review: Any) -> dict[str, Any]:
    return {
        "id": review.id,
        "scope": review.scope.key,
        "kind": review.kind,
        "content": review.content,
        "source_message_ids": review.source_message_ids,
        "created_at": _as_utc(review.created_at),
    }


async def _memory_export_stream(
    context: AppContext, scope: Scope | None, fmt: MemoryExportFormat
) -> AsyncGenerator[bytes, None]:
    chunks = context.memory.export_memory_chunks(scope, fmt=fmt)
    try:
        async for chunk in chunks:
            yield chunk.text.encode("utf-8")
    finally:
        close = getattr(chunks, "aclose", None)
        if close is not None:
            await close()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _scheduler_status(request: Request) -> list[dict[str, Any]]:
    handles = getattr(request.app.state, "background_tasks", {})
    out: list[dict[str, Any]] = []
    for name, handle in sorted(handles.items()):
        task = getattr(handle, "task", handle)
        state = getattr(handle, "state", None)
        if task.cancelled():
            task_state = "cancelled"
        elif task.done():
            task_state = "failed" if task.exception() is not None else "completed"
        else:
            task_state = "running"
        out.append(
            {
                "name": str(getattr(state, "name", name)),
                "state": task_state,
                "healthy": bool(getattr(handle, "healthy", task_state == "running")),
                "last_success_at": getattr(state, "last_success_at", None),
                "last_failure_at": getattr(state, "last_failure_at", None),
                "last_exception_type": getattr(state, "last_exception_type", None),
                "consecutive_failures": int(getattr(state, "consecutive_failures", 0)),
            }
        )
    return out


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


async def _scope_settings_payload(
    context: AppContext, runtime: ControlPlaneRuntime, scope: Scope
) -> dict[str, Any]:
    settings = await runtime.management.scope_settings(scope)
    policy = await context.memory.learn_status(scope)
    return {
        "scope": settings.scope,
        "effective": settings.effective,
        "inherited": settings.inherited,
        "overrides": settings.overrides,
        "policy": {
            "learning_enabled": bool(policy["learning_enabled"]),
            "allow_admins": bool(policy["allow_admins"]),
        },
        "version": settings.version,
    }


def _managed_chat_mutation_payload(managed: Any) -> dict[str, Any]:
    if managed.audit_event_id is None:
        raise RuntimeError("managed chat mutation did not produce an audit event")
    return {
        "chat_id": managed.chat_id,
        "title": managed.title,
        "username": managed.username,
        "status": managed.status,
        "version": managed.version,
        "updated_at": managed.updated_at,
        "audit_event_id": managed.audit_event_id,
    }


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
        secure=_session_cookie_secure(context),
        httponly=True,
        samesite="lax",
    )


def _session_cookie_secure(context: AppContext) -> bool:
    return urlsplit(context.settings.control_plane_public_origin).scheme == "https"


def _development_identity(telegram_user_id: int, *, now: datetime):
    from .auth.identity import TelegramIdentity

    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        first_name="Development administrator",
        last_name=None,
        username="pzp_control_plane_dev",
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
