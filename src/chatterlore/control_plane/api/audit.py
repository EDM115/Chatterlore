from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from ..schemas import AIUsagePage, AuditEventPage, ControlPlaneErrorEnvelope
from .common import _require_memory_scope, _services


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
        openapi_extra={"x-chatterlore-capability": "scope_admin"},
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
        openapi_extra={"x-chatterlore-capability": "scope_admin"},
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
