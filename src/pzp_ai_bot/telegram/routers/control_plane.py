from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.control_plane.management import ControlPlaneManagementError
from pzp_ai_bot.telegram.safe_api import safe_answer
from pzp_ai_bot.telegram.utils import message_user_id


def register_control_plane_enrollment_handler(router: Router, context: AppContext) -> None:
    @router.message(F.chat_shared)
    async def complete_chat_enrollment(message: Message) -> None:
        runtime = context.control_plane
        shared = message.chat_shared
        user_id = message_user_id(message)
        if runtime is None or shared is None or user_id is None:
            await safe_answer(message, "This chat enrollment request is unavailable.")
            return
        chat_type = str(getattr(message.chat.type, "value", message.chat.type))
        if chat_type != "private" or int(message.chat.id) != user_id:
            await safe_answer(message, "Chat enrollment selections must be sent in the bot DM.")
            return
        try:
            completed = await runtime.management.complete_chat_enrollment(
                request_id=shared.request_id,
                telegram_user_id=user_id,
                chat_id=shared.chat_id,
                shared_title=shared.title,
                shared_username=shared.username,
                bot=message.bot,
            )
        except ControlPlaneManagementError:
            await safe_answer(
                message,
                "This chat enrollment could not be verified. "
                "Start a new request in the control plane.",
            )
            return
        await safe_answer(
            message,
            f"Enrolled Telegram chat {completed.chat_id}. You can return to the control plane.",
        )


__all__ = ["register_control_plane_enrollment_handler"]
