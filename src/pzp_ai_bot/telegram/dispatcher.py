from __future__ import annotations

from aiogram import Dispatcher

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.telegram.router import build_router


def build_dispatcher(context: AppContext) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(context))
    return dispatcher
