from __future__ import annotations

from aiogram import Bot, Dispatcher

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.telegram.router import build_router


async def run_polling(context: AppContext) -> None:
    if not context.settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for polling")
    bot = Bot(context.settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(context))
    await bot.delete_webhook(drop_pending_updates=False)
    await dispatcher.start_polling(bot)
