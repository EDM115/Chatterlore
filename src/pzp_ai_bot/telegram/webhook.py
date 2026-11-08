from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.control_plane.app import install_control_plane
from pzp_ai_bot.telegram.commands import clear_bot_commands
from pzp_ai_bot.telegram.dispatcher import build_dispatcher
from pzp_ai_bot.telegram.scheduler import (
    ADMIN_ALERT_DELIVERY_TASK_NAME,
    IDLE_SUMMARIZER_TASK_NAME,
    RUNTIME_SETTINGS_REFRESHER_TASK_NAME,
    start_admin_alert_delivery,
    start_embedding_index_worker,
    start_idle_summarizer,
    start_runtime_settings_refresher,
    stop_task,
)

UNSAFE_WEBHOOK_SECRETS = {"", "change-me", "changeme", "default", "placeholder", "secret"}
MAX_WEBHOOK_ANSWER_TASKS = 128
logger = structlog.get_logger(__name__)


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
    answer_tasks: dict[int, asyncio.Task[Any]] = {}
    completed_answers: deque[int] = deque(maxlen=256)

    def answer_finished(update_id: int, task: asyncio.Task[Any]) -> None:
        answer_tasks.pop(update_id, None)
        completed_answers.append(update_id)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning(
                "telegram_webhook_answer_failed",
                update_id=update_id,
                error_type=type(error).__name__,
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        idle_task = start_idle_summarizer(context)
        settings_task = start_runtime_settings_refresher(context)
        index_task = start_embedding_index_worker(context)
        alert_task = start_admin_alert_delivery(context, telegram_bot)
        app.state.background_tasks = {idle_task.name: idle_task, settings_task.name: settings_task}
        app.state.background_tasks[index_task.name] = index_task
        if alert_task is not None:
            app.state.background_tasks[ADMIN_ALERT_DELIVERY_TASK_NAME] = alert_task
        try:
            await clear_bot_commands(telegram_bot)
            await telegram_bot.set_webhook(
                context.settings.webhook_url,
                secret_token=context.settings.webhook_secret,
                drop_pending_updates=False,
                allowed_updates=telegram_dispatcher.resolve_used_update_types(),
            )
            yield
        finally:
            await telegram_dispatcher.emit_shutdown(bot=telegram_bot)
            pending_answers = tuple(answer_tasks.values())
            for task in pending_answers:
                task.cancel()
            if pending_answers:
                await asyncio.gather(*pending_answers, return_exceptions=True)
            await stop_task(idle_task)
            await stop_task(settings_task)
            await stop_task(index_task)
            if alert_task is not None:
                await stop_task(alert_task)
            if context.control_plane is not None:
                await context.control_plane.oidc.aclose()
            if owns_bot:
                await telegram_bot.session.close()
            await context.memory.provider_registry.aclose()

    app = FastAPI(title="PZP AI Telegram Memory Bot", lifespan=lifespan)
    app.state.telegram_bot = telegram_bot
    app.state.telegram_dispatcher = telegram_dispatcher
    app.state.webhook_answer_tasks = answer_tasks

    @app.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)
    ) -> dict[str, bool]:
        if x_telegram_bot_api_secret_token != context.settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        update = Update.model_validate(await request.json(), context={"bot": telegram_bot})
        text = (update.message.text or update.message.caption) if update.message else None
        command = text.split(maxsplit=1)[0].split("@", 1)[0] if text and text.strip() else ""
        if command == "/ask":
            # Acknowledge before generation so Telegram can deliver this chat's Stop update
            if update.update_id in answer_tasks or update.update_id in completed_answers:
                return {"ok": True}
            if len(answer_tasks) >= MAX_WEBHOOK_ANSWER_TASKS:
                raise HTTPException(status_code=503, detail="answer capacity unavailable")
            task = asyncio.create_task(
                telegram_dispatcher.feed_update(telegram_bot, update),
                name=f"telegram-answer-{update.update_id}",
            )
            answer_tasks[update.update_id] = task
            task.add_done_callback(lambda done, uid=update.update_id: answer_finished(uid, done))
            return {"ok": True}
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
        safe_control_plane = context.settings.control_plane_safe_config
        alert_handle = handles.get(ADMIN_ALERT_DELIVERY_TASK_NAME)
        control_plane_health = {
            "enabled": context.settings.control_plane_enabled,
            "oidc_configured": bool(safe_control_plane["oidc_client_configured"]),
            "master_keys_configured": bool(safe_control_plane["master_keys_configured"]),
            "session_store": safe_control_plane["session_store"],
            "provider_pools_enabled": bool(safe_control_plane["provider_pools_enabled"]),
            "alert_worker": {
                "configured": context.settings.control_plane_enabled,
                "running": bool(alert_handle is not None and alert_handle.healthy),
                "critical": False,
            },
        }
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"ok": healthy, "tasks": task_health, "control_plane": control_plane_health},
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
