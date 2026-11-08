from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request

from chatterlore.db.models import AdminAlert

from ..schemas import AdminAlertPage, AdminAlertSummary, ControlPlaneErrorEnvelope
from .common import ControlPlaneAPIError, _require_bot_admin


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
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
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
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
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
