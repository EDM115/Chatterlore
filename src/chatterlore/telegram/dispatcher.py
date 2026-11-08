from __future__ import annotations

from aiogram import Dispatcher

from chatterlore.app_context import AppContext
from chatterlore.telegram.middleware import StructuredLoggingMiddleware
from chatterlore.telegram.router import build_router


def build_dispatcher(context: AppContext) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.message.middleware(StructuredLoggingMiddleware())
    dispatcher.include_router(build_router(context))
    return dispatcher
