from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from pzp_ai_bot.app_context import AppContext, build_context
from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.app import DevelopmentAuthConfig, install_control_plane
from pzp_ai_bot.telegram.commands import register_bot_commands
from pzp_ai_bot.telegram.dispatcher import build_dispatcher
from pzp_ai_bot.telegram.scheduler import (
    ADMIN_ALERT_DELIVERY_TASK_NAME,
    IDLE_SUMMARIZER_TASK_NAME,
    RUNTIME_SETTINGS_REFRESHER_TASK_NAME,
    start_admin_alert_delivery,
    start_idle_summarizer,
    start_runtime_settings_refresher,
    stop_task,
)
from pzp_ai_bot.telegram.webhook import require_safe_webhook_secret

DEVELOPMENT_MASTER_KEY_ID = "development"
DEFAULT_DEVELOPMENT_ORIGIN = "http://localhost:3000"
DEFAULT_DEVELOPMENT_API_PORT = 8000
DEFAULT_DEVELOPMENT_MASTER_KEY_PATH = Path("data/control-plane-dev-master-key")
logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ControlPlaneDevelopmentConfig:
    user_id: int
    origin: str
    api_port: int
    telegram_connected: bool


def build_control_plane_development_settings(
    settings: Settings,
    *,
    environment: Mapping[str, str] | None = None,
    key_path: Path = DEFAULT_DEVELOPMENT_MASTER_KEY_PATH,
) -> tuple[Settings, ControlPlaneDevelopmentConfig]:
    values = (
        {
            "CONTROL_PLANE_DEV_USER_ID": settings.control_plane_dev_user_id,
            "CONTROL_PLANE_DEV_ORIGIN": settings.control_plane_dev_origin,
            "CONTROL_PLANE_DEV_API_PORT": settings.control_plane_dev_api_port,
        }
        if environment is None
        else environment
    )
    user_id = _positive_integer(
        values.get("CONTROL_PLANE_DEV_USER_ID", ""), "CONTROL_PLANE_DEV_USER_ID"
    )
    origin = _loopback_origin(values.get("CONTROL_PLANE_DEV_ORIGIN", DEFAULT_DEVELOPMENT_ORIGIN))
    api_port = _api_port(
        values.get("CONTROL_PLANE_DEV_API_PORT", str(DEFAULT_DEVELOPMENT_API_PORT))
    )
    master_keys, active_key_id = _development_keyring(settings, key_path)
    transaction_secret = settings.telegram_oidc_client_secret.strip() or _transaction_secret(
        master_keys, active_key_id
    )
    development_settings = settings.model_copy(
        update={
            "control_plane_enabled": True,
            "control_plane_public_origin": origin,
            "control_plane_master_keys": master_keys,
            "control_plane_active_master_key_id": active_key_id,
            "telegram_oidc_client_secret": transaction_secret,
        }
    )
    return development_settings, ControlPlaneDevelopmentConfig(
        user_id=user_id,
        origin=origin,
        api_port=api_port,
        telegram_connected=bool(settings.webhook_url.strip()),
    )


def load_or_create_development_master_key(path: Path) -> str:
    try:
        return _read_development_master_key(path)
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    try:
        with path.open("x", encoding="ascii", newline="\n") as key_file:
            key_file.write(encoded)
            key_file.flush()
            os.fsync(key_file.fileno())
    except FileExistsError:
        return _read_development_master_key(path)
    except OSError as exc:
        raise ValueError("development master key file could not be created") from exc
    return encoded


def create_control_plane_development_app(
    context: AppContext,
    config: ControlPlaneDevelopmentConfig,
    *,
    bot: Bot | None = None,
    dispatcher: Dispatcher | None = None,
) -> FastAPI:
    if context.control_plane is None:
        raise RuntimeError("the control plane is not enabled")
    runtime = context.control_plane
    context.admins.bot_admin_ids.add(config.user_id)
    connected = config.telegram_connected
    if connected:
        _validate_connected_telegram_settings(context.settings)
    owns_bot = connected and bot is None
    telegram_bot: Any | None = (
        bot or Bot(context.settings.telegram_bot_token) if connected else None
    )
    telegram_dispatcher: Any | None = dispatcher or build_dispatcher(context) if connected else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        idle_task = start_idle_summarizer(context)
        settings_task = start_runtime_settings_refresher(context)
        alert_task = (
            start_admin_alert_delivery(context, telegram_bot) if telegram_bot is not None else None
        )
        app.state.background_tasks = {idle_task.name: idle_task, settings_task.name: settings_task}
        if alert_task is not None:
            app.state.background_tasks[ADMIN_ALERT_DELIVERY_TASK_NAME] = alert_task
        try:
            if telegram_bot is not None:
                await register_bot_commands(telegram_bot)
                await telegram_bot.set_webhook(
                    context.settings.webhook_url,
                    secret_token=context.settings.webhook_secret,
                    drop_pending_updates=False,
                )
            yield
        finally:
            await stop_task(idle_task)
            await stop_task(settings_task)
            if alert_task is not None:
                await stop_task(alert_task)
            await runtime.oidc.aclose()
            if owns_bot and telegram_bot is not None:
                await telegram_bot.session.close()

    app = FastAPI(title="PZP AI Control Plane Development", lifespan=lifespan)
    if telegram_bot is not None and telegram_dispatcher is not None:

        @app.post("/telegram/webhook")
        async def telegram_webhook(
            request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)
        ) -> dict[str, bool]:
            if x_telegram_bot_api_secret_token != context.settings.webhook_secret:
                raise HTTPException(status_code=401, detail="invalid webhook secret")
            update = Update.model_validate(await request.json(), context={"bot": telegram_bot})
            await telegram_dispatcher.feed_update(telegram_bot, update)
            return {"ok": True}

    @app.get("/health")
    async def health() -> JSONResponse:
        handles = getattr(app.state, "background_tasks", {})
        task_health = {
            task_name: bool((handle := handles.get(task_name)) is not None and handle.healthy)
            for task_name in (IDLE_SUMMARIZER_TASK_NAME, RUNTIME_SETTINGS_REFRESHER_TASK_NAME)
        }
        healthy = all(task_health.values())
        return JSONResponse(
            status_code=200 if healthy else 503, content={"ok": healthy, "tasks": task_health}
        )

    install_control_plane(
        app, context, development_auth=DevelopmentAuthConfig(telegram_user_id=config.user_id)
    )
    return app


async def run_control_plane_development(
    settings: Settings, *, environment: Mapping[str, str] | None = None
) -> None:
    development_settings, config = build_control_plane_development_settings(
        settings, environment=environment
    )
    if not settings.control_plane_master_keys.strip():
        logger.warning(
            "control_plane_development_key_colocated",
            event_type="control_plane_development_key_colocated",
            key_path=str(DEFAULT_DEVELOPMENT_MASTER_KEY_PATH),
        )
    context = await build_context(development_settings)
    app = create_control_plane_development_app(context, config)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=config.api_port, log_level="info")
    )
    await server.serve()


def require_https_webhook_url(url: str) -> None:
    try:
        parts = urlsplit(url.strip())
        _ = parts.port
    except ValueError as exc:
        raise RuntimeError(
            "WEBHOOK_URL must be a valid HTTPS URL for connected development"
        ) from exc
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise RuntimeError("WEBHOOK_URL must be a valid HTTPS URL for connected development")


def _validate_connected_telegram_settings(settings: Settings) -> None:
    if not settings.telegram_bot_token.strip():
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required when WEBHOOK_URL is configured")
    require_safe_webhook_secret(settings.webhook_secret)
    require_https_webhook_url(settings.webhook_url)


def _development_keyring(settings: Settings, key_path: Path) -> tuple[str, str]:
    configured_keys = settings.control_plane_master_keys.strip()
    configured_active_id = settings.control_plane_active_master_key_id.strip()
    if configured_keys or configured_active_id:
        if not configured_keys or not configured_active_id:
            raise ValueError(
                "development mode requires both CONTROL_PLANE_MASTER_KEYS and "
                "CONTROL_PLANE_ACTIVE_MASTER_KEY_ID when either is configured"
            )
        keys = settings.control_plane_master_key_bytes
        if configured_active_id not in keys:
            raise ValueError("configured development master key ID is unavailable")
        return settings.control_plane_master_keys, configured_active_id
    encoded = load_or_create_development_master_key(key_path)
    return json.dumps({DEVELOPMENT_MASTER_KEY_ID: encoded}), DEVELOPMENT_MASTER_KEY_ID


def _read_development_master_key(path: Path) -> str:
    try:
        encoded = path.read_text(encoding="ascii")
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ValueError("development master key file could not be read") from exc
    try:
        material = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("development master key file is invalid") from exc
    if len(material) != 32:
        raise ValueError("development master key file is invalid")
    return encoded


def _transaction_secret(master_keys: str, active_key_id: str) -> str:
    encoded = str(json.loads(master_keys)[active_key_id])
    material = base64.b64decode(encoded, validate=True)
    return hashlib.sha256(b"pzp-control-plane-development-oidc\0" + material).hexdigest()


def _positive_integer(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _api_port(raw: str) -> int:
    value = _positive_integer(raw, "CONTROL_PLANE_DEV_API_PORT")
    if value > 65_535:
        raise ValueError("CONTROL_PLANE_DEV_API_PORT must be between 1 and 65535")
    return value


def _loopback_origin(raw: str) -> str:
    origin = raw.strip()
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError as exc:
        raise ValueError("CONTROL_PLANE_DEV_ORIGIN must be an exact loopback origin") from exc
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parts.username is not None
        or parts.password is not None
        or parts.path
        or parts.query
        or parts.fragment
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ValueError("CONTROL_PLANE_DEV_ORIGIN must be an exact loopback origin")
    return origin
