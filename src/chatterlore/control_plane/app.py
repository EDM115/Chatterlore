from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from chatterlore.app_context import AppContext

from .api.alerts import _build_alert_router
from .api.audit import _build_audit_router
from .api.auth import CONTROL_PLANE_DEV_AUTH_PATH, DevelopmentAuthConfig, _build_auth_router
from .api.auth import CONTROL_PLANE_DEV_STEP_UP_PATH as CONTROL_PLANE_DEV_STEP_UP_PATH
from .api.common import ControlPlaneAPIError, RateLimiter
from .api.management import _build_management_router
from .api.memory import _build_memory_router
from .api.memory import _memory_export_stream as _memory_export_stream
from .api.operations import _build_operations_router
from .api.providers import _build_provider_router
from .api.query import _build_query_router
from .api.stats import _build_stats_router
from .auth.oidc import TelegramOIDCClient
from .embedding_routes import build_embedding_router
from .middleware import ControlPlaneRequestGuardMiddleware, ControlPlaneSecurityHeadersMiddleware


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
        from chatterlore.telegram.scheduler import start_embedding_index_worker, stop_task

        index_task = start_embedding_index_worker(context)
        try:
            yield
        finally:
            await stop_task(index_task)
            await runtime.oidc.aclose()
            await context.memory.provider_registry.aclose()

    app = FastAPI(
        title="Chatterlore Control Plane API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    install_control_plane(app, context, development_auth=development_auth)
    return app


def create_control_plane_schema_app() -> FastAPI:
    app = FastAPI(
        title="Chatterlore Control Plane API",
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
    app.include_router(build_embedding_router())
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
    app.include_router(build_embedding_router())
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
