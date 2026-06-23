from __future__ import annotations

from aiogram import Bot, Dispatcher

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.telegram.commands import register_bot_commands
from pzp_ai_bot.telegram.router import build_router
from pzp_ai_bot.telegram.scheduler import start_idle_summarizer, stop_idle_summarizer


async def run_polling(context: AppContext) -> None:
    if not context.settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for polling")
    bot = Bot(context.settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(context))
    idle_task = start_idle_summarizer(context)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await register_bot_commands(bot)
        await dispatcher.start_polling(bot)
    finally:
        await stop_idle_summarizer(idle_task)
        await bot.session.close()
