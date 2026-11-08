from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chatterlore.config import Settings

from .auth.sessions import ControlPlaneSessionService

SESSION_COOKIE_NAME = "chatterlore_control_session"
CSRF_HEADER_NAME = "x-csrf-token"


class ControlPlaneRequestGuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        sessions: ControlPlaneSessionService,
        public_post_paths: frozenset[str] | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self.sessions = sessions
        self.public_post_paths = public_post_paths or frozenset({"/api/control/v1/auth/mini-app"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET")).upper()
        if path.startswith("/api/control/v1/") and method not in {"GET", "HEAD", "OPTIONS"}:
            request = Request(scope)
            if not _same_origin(request.headers, self.settings.control_plane_public_origin):
                await _send_error(
                    scope,
                    receive,
                    send,
                    status_code=403,
                    code="origin_rejected",
                    message="Request origin is not allowed.",
                )
                return
            if path not in self.public_post_paths:
                session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
                csrf_token = request.headers.get(CSRF_HEADER_NAME, "")
                if (
                    not session_token
                    or not csrf_token
                    or not await self.sessions.validate_csrf(
                        session_token, csrf_token, now=datetime.now(UTC)
                    )
                ):
                    await _send_error(
                        scope,
                        receive,
                        send,
                        status_code=403,
                        code="csrf_rejected",
                        message="CSRF validation failed.",
                    )
                    return
        await self.app(scope, receive, send)


class ControlPlaneSecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                path = str(scope.get("path", ""))
                headers["Cache-Control"] = (
                    "no-store, private"
                    if path.endswith("/reveal") or path.endswith("/memory/export")
                    else "no-store"
                )
                headers["Pragma"] = "no-cache"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=()"
                )
                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; base-uri 'none'; object-src 'none'; "
                    "script-src 'self' https://telegram.org; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
                    "connect-src 'self'; "
                    "frame-ancestors 'self' https://telegram.org https://*.telegram.org"
                )
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _same_origin(headers: Headers, expected_origin: str) -> bool:
    origin = headers.get("origin")
    if origin is not None:
        return origin == expected_origin
    referer = headers.get("referer")
    if referer is None:
        return False
    try:
        parts = urlsplit(referer)
    except ValueError:
        return False
    actual_origin = f"{parts.scheme}://{parts.netloc}"
    return actual_origin == expected_origin


async def _send_error(
    scope: Scope, receive: Receive, send: Send, *, status_code: int, code: str, message: str
) -> None:
    response = JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )
    await response(scope, receive, send)
