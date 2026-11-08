from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..schemas import (
    ControlPlaneErrorEnvelope,
    OperationCheckRequest,
    OperationCheckResponse,
    OperationsHealthResponse,
)
from .common import ControlPlaneAPIError, _require_bot_admin


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
