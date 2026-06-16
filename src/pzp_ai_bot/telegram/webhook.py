from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.telegram.router import build_router


def create_webhook_app(context: AppContext) -> FastAPI:
    if not context.settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for webhook mode")
    if not context.settings.webhook_url:
        raise RuntimeError("WEBHOOK_URL is required for webhook mode")

    bot = Bot(context.settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(context))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await bot.set_webhook(
            context.settings.webhook_url,
            secret_token=context.settings.webhook_secret,
            drop_pending_updates=False,
        )
        try:
            yield
        finally:
            await bot.session.close()

    app = FastAPI(title="PZP AI Telegram Memory Bot", lifespan=lifespan)

    @app.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)
    ) -> dict[str, bool]:
        if x_telegram_bot_api_secret_token != context.settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dispatcher.feed_update(bot, update)
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
