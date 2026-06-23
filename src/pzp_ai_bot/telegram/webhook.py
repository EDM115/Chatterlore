from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.telegram.commands import register_bot_commands
from pzp_ai_bot.telegram.dispatcher import build_dispatcher
from pzp_ai_bot.telegram.scheduler import start_idle_summarizer, stop_idle_summarizer


def create_webhook_app(
    context: AppContext, *, bot: Bot | None = None, dispatcher: Dispatcher | None = None
) -> FastAPI:
    if bot is None and not context.settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for webhook mode")
    if not context.settings.webhook_url:
        raise RuntimeError("WEBHOOK_URL is required for webhook mode")

    owns_bot = bot is None
    telegram_bot: Any = bot or Bot(context.settings.telegram_bot_token)
    telegram_dispatcher: Any = dispatcher or build_dispatcher(context)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        idle_task = start_idle_summarizer(context)
        try:
            await register_bot_commands(telegram_bot)
            await telegram_bot.set_webhook(
                context.settings.webhook_url,
                secret_token=context.settings.webhook_secret,
                drop_pending_updates=False,
            )
            yield
        finally:
            await stop_idle_summarizer(idle_task)
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
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


async def run_webhook(context: AppContext) -> None:
    import uvicorn

    app = create_webhook_app(context)
    config = uvicorn.Config(
        app, host=context.settings.webapp_host, port=context.settings.webapp_port, log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()
