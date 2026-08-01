from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.control_plane.app import install_control_plane
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

UNSAFE_WEBHOOK_SECRETS = {"", "change-me", "changeme", "default", "placeholder", "secret"}


def require_safe_webhook_secret(secret: str) -> None:
    if secret.strip().lower() in UNSAFE_WEBHOOK_SECRETS:
        raise RuntimeError("WEBHOOK_SECRET must be set to a non-default value for webhook mode")


def create_webhook_app(
    context: AppContext, *, bot: Bot | None = None, dispatcher: Dispatcher | None = None
) -> FastAPI:
    if bot is None and not context.settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for webhook mode")
    if not context.settings.webhook_url:
        raise RuntimeError("WEBHOOK_URL is required for webhook mode")
    require_safe_webhook_secret(context.settings.webhook_secret)

    owns_bot = bot is None
    telegram_bot: Any = bot or Bot(context.settings.telegram_bot_token)
    telegram_dispatcher: Any = dispatcher or build_dispatcher(context)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        idle_task = start_idle_summarizer(context)
        settings_task = start_runtime_settings_refresher(context)
        alert_task = start_admin_alert_delivery(context, telegram_bot)
        app.state.background_tasks = {idle_task.name: idle_task, settings_task.name: settings_task}
        if alert_task is not None:
            app.state.background_tasks[ADMIN_ALERT_DELIVERY_TASK_NAME] = alert_task
        try:
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
            if context.control_plane is not None:
                await context.control_plane.oidc.aclose()
            if owns_bot:
                await telegram_bot.session.close()

    app = FastAPI(title="PZP AI Telegram Memory Bot", lifespan=lifespan)
    app.state.telegram_bot = telegram_bot
    app.state.telegram_dispatcher = telegram_dispatcher

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

    if context.settings.control_plane_enabled:
        install_control_plane(app, context)

    return app


async def run_webhook(context: AppContext) -> None:
    import uvicorn

    app = create_webhook_app(context)
    config = uvicorn.Config(
        app, host=context.settings.webapp_host, port=context.settings.webapp_port, log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()
