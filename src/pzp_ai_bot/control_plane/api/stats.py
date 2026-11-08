from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from ..schemas import ControlPlaneErrorEnvelope, StatsOverviewResponse, StatsTimeseriesResponse
from .common import _require_memory_scope, _services
from .operations import _scheduler_status


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
