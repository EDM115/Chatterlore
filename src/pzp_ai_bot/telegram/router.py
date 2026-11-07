from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.telegram.routers.control_plane import register_control_plane_enrollment_handler
from pzp_ai_bot.telegram.routers.embedding_indexes import register_embedding_index_handlers
from pzp_ai_bot.telegram.routers.learning import (
    register_learning_policy_handlers,
    register_learning_status_handlers,
)
from pzp_ai_bot.telegram.routers.memory import (
    register_forget_handler,
    register_ingest_handler,
    register_memory_item_handlers,
    register_review_handlers,
)
from pzp_ai_bot.telegram.routers.operations import register_operations_handlers
from pzp_ai_bot.telegram.routers.retrieval import (
    EXPORT_DOCUMENT_CHUNK_BYTES as EXPORT_DOCUMENT_CHUNK_BYTES,
)
from pzp_ai_bot.telegram.routers.retrieval import (
    TELEGRAM_DOCUMENT_MAX_BYTES as TELEGRAM_DOCUMENT_MAX_BYTES,
)
from pzp_ai_bot.telegram.routers.retrieval import export_document_stream as export_document_stream
from pzp_ai_bot.telegram.routers.retrieval import export_documents as export_documents
from pzp_ai_bot.telegram.routers.retrieval import (
    register_query_handlers,
    register_range_summary_handler,
    register_summarization_handlers,
)
from pzp_ai_bot.telegram.routers.settings import (
    register_profile_template_handlers,
    register_scope_settings_handlers,
)
from pzp_ai_bot.telegram.routers.shared import RouterGuards
from pzp_ai_bot.telegram.safe_api import safe_answer
from pzp_ai_bot.telegram.utils import message_user_id


def build_router(context: AppContext) -> Router:
    router = Router(name="pzp-memory")

    async def require_admin(message: Message) -> bool:
        user_id = message_user_id(message)
        ok = await context.admins.can_administer(message.bot, int(message.chat.id), user_id)
        if not ok:
            await safe_answer(message, "This command requires a bot admin or Telegram chat admin.")
        return ok

    async def require_bot_admin(message: Message) -> bool:
        if context.admins.is_bot_admin(message_user_id(message)):
            return True
        await safe_answer(message, "This command requires a configured bot admin.")
        return False

    async def require_scope_admin(message: Message, scope: Scope) -> bool:
        user_id = message_user_id(message)
        if context.admins.is_bot_admin(user_id):
            return True
        if scope.is_global:
            await safe_answer(message, "Only configured bot admins can target all scopes.")
            return False
        if scope.chat_id != int(message.chat.id):
            await safe_answer(message, "Only configured bot admins can target another chat scope.")
            return False
        ok = await context.admins.can_administer(message.bot, int(message.chat.id), user_id)
        if not ok:
            await safe_answer(message, "This command requires a bot admin or Telegram chat admin.")
        return ok

    guards = RouterGuards(
        require_admin=require_admin,
        require_bot_admin=require_bot_admin,
        require_scope_admin=require_scope_admin,
    )

    register_control_plane_enrollment_handler(router, context)

    register_query_handlers(router, context, guards)

    register_operations_handlers(router, context, guards)

    register_embedding_index_handlers(router, context, guards)

    register_learning_status_handlers(router, context, guards)

    register_scope_settings_handlers(router, context, guards)

    register_learning_policy_handlers(router, context, guards)

    register_memory_item_handlers(router, context, guards)

    register_summarization_handlers(router, context, guards)

    register_review_handlers(router, context, guards)

    register_profile_template_handlers(router, context, guards)

    register_range_summary_handler(router, context, guards)

    register_forget_handler(router, context, guards)

    register_ingest_handler(router, context)

    return router
