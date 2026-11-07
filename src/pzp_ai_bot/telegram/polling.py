from __future__ import annotations

from typing import Any

from aiogram import Bot, Dispatcher

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.telegram.commands import register_bot_commands
from pzp_ai_bot.telegram.dispatcher import build_dispatcher
from pzp_ai_bot.telegram.scheduler import (
    start_admin_alert_delivery,
    start_embedding_index_worker,
    start_idle_summarizer,
    start_runtime_settings_refresher,
    stop_task,
)


async def run_polling(
    context: AppContext, *, bot: Bot | None = None, dispatcher: Dispatcher | None = None
) -> None:
    if bot is None and not context.settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for polling")
    owns_bot = bot is None
    telegram_bot: Any = bot or Bot(context.settings.telegram_bot_token)
    telegram_dispatcher: Any = dispatcher or build_dispatcher(context)
    idle_task = start_idle_summarizer(context)
    settings_task = start_runtime_settings_refresher(context)
    index_task = start_embedding_index_worker(context)
    alert_task = start_admin_alert_delivery(context, telegram_bot)
    try:
        await telegram_bot.delete_webhook(drop_pending_updates=False)
        await register_bot_commands(telegram_bot)
        await telegram_dispatcher.start_polling(telegram_bot)
    finally:
        await stop_task(idle_task)
        await stop_task(settings_task)
        await stop_task(index_task)
        if alert_task is not None:
            await stop_task(alert_task)
        if owns_bot:
            await telegram_bot.session.close()
        await context.memory.provider_registry.aclose()
