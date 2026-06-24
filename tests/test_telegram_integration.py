from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.methods import SendDocument, SendMessage, TelegramMethod
from aiogram.types import BufferedInputFile, Update
from aiogram.types import Message as TelegramMessage
from fastapi.testclient import TestClient
from sqlalchemy import select

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.models import Message
from pzp_ai_bot.db.repositories import EffectiveScopeSettings, MemoryProfileView
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import (
    AuditEventPreview,
    MemoryItemPreview,
    MemoryItemRefreshResult,
    MemoryNotePreview,
    MemoryStatusPreview,
    RangeSummaryResult,
    RuntimeSettingPreview,
    ScopeStatusPreview,
    SettingsChangePreview,
    SkippedMessagePreview,
    SummaryTemplatePreview,
    SystemPromptTemplatePreview,
)
from pzp_ai_bot.telegram import safe_api as safe_api_module
from pzp_ai_bot.telegram.commands import COMMAND_DESCRIPTIONS
from pzp_ai_bot.telegram.dispatcher import build_dispatcher
from pzp_ai_bot.telegram.polling import run_polling
from pzp_ai_bot.telegram.router import export_documents
from pzp_ai_bot.telegram.safe_api import safe_answer, safe_answer_document
from pzp_ai_bot.telegram.webhook import create_webhook_app


class RecordingTelegramSession(BaseSession):
    def __init__(self, *, chat_admin_ids: set[int] | None = None) -> None:
        super().__init__()
        self.chat_admin_ids = chat_admin_ids or set()
        self.calls: list[TelegramMethod[Any]] = []
        self.retry_after_methods: dict[str, int] = {}
        self.api_error_methods: set[str] = set()

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        api_method = method.__api_method__
        self.calls.append(method)
        if api_method in self.retry_after_methods:
            raise TelegramRetryAfter(method, "retry later", self.retry_after_methods[api_method])
        if api_method in self.api_error_methods:
            raise TelegramAPIError(method, "telegram failed")
        if api_method == "getChatAdministrators":
            return [
                SimpleNamespace(user=SimpleNamespace(id=user_id)) for user_id in self.chat_admin_ids
            ]
        if api_method in {"setWebhook", "deleteWebhook", "setMyCommands"}:
            return True
        if api_method in {"sendMessage", "sendDocument"}:
            text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
            return SimpleNamespace(
                message_id=9000 + len(self.calls),
                chat=SimpleNamespace(id=getattr(method, "chat_id", 0)),
                text=text,
            )
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if False:
            yield b""

    def call_names(self) -> list[str]:
        return [method.__api_method__ for method in self.calls]

    def sent_texts(self) -> list[str]:
        return [
            str(getattr(method, "text", ""))
            for method in self.calls
            if method.__api_method__ == "sendMessage"
        ]


class AllowingAdmins:
    def __init__(self, *, allow: bool = True, bot_admin_ids: set[int] | None = None) -> None:
        self.allow = allow
        self.bot_admin_ids = bot_admin_ids or {111}

    def is_bot_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.bot_admin_ids

    async def is_chat_admin(self, bot: Any, chat_id: int, user_id: int | None) -> bool:
        return self.allow

    async def can_administer(self, bot: Any, chat_id: int, user_id: int | None) -> bool:
        return self.allow or self.is_bot_admin(user_id)


class RouterMemoryDouble:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Scope | None]] = []

    def _record(self, name: str, scope: Scope | None = None) -> None:
        self.calls.append((name, scope))

    def settings(self) -> EffectiveScopeSettings:
        return EffectiveScopeSettings(
            retrieval_top_k=6,
            summary_batch_size=2,
            summary_model="fake-chat",
            system_prompt_template="scoped",
            provider_profile="default",
            chat_model="fake-chat",
            embedding_model="fake-embedding",
            reasoning_level="medium",
            raw_message_retention_days=None,
            knowledge_retention_days=None,
            review_before_compact=False,
        )

    async def ask(self, scope: Scope, question: str, **kwargs: Any) -> str:
        self._record("ask", scope)
        return "answer"

    async def search(self, scope: Scope, query: str, **kwargs: Any) -> list[Any]:
        self._record("search", scope)
        return []

    async def export_memory(self, scope: Scope | None = None, fmt: str = "md") -> str:
        self._record("export_memory", scope)
        return "# export\n"

    async def stats(self) -> dict[str, int]:
        self._record("stats")
        return {"messages": 0, "learned": 0, "skipped": 0}

    async def memory_status(self, scope: Scope | None = None) -> MemoryStatusPreview:
        self._record("memory_status", scope)
        return MemoryStatusPreview(
            scope, scope.key if scope else "all scopes", 1, 1, 0, 1, 1, 0, 0, 1, 0, 2, {"ask": 1}
        )

    async def known_scopes(self, root_scope: Scope | None = None) -> list[ScopeStatusPreview]:
        self._record("known_scopes", root_scope)
        now = datetime(2026, 6, 23, tzinfo=UTC)
        scope = root_scope or Scope(-10012345, None)
        return [ScopeStatusPreview(scope, scope.key, True, False, 1, 1, 0, 1, now)]

    async def skipped_messages(
        self, scope: Scope, *, limit: int = 10, telegram_message_id: int | None = None
    ) -> list[SkippedMessagePreview]:
        self._record("skipped_messages", scope)
        now = datetime(2026, 6, 23, tzinfo=UTC)
        return [
            SkippedMessagePreview(
                scope,
                telegram_message_id or 1,
                "command",
                "Telegram commands are not learned as memory.",
                now,
            )
        ]

    async def set_learning(self, scope: Scope, enabled: bool, updated_by: int | None) -> None:
        self._record("set_learning", scope)

    async def learn_status(self, scope: Scope) -> dict[str, bool | int | str | None]:
        self._record("learn_status", scope)
        return {"scope": scope.key, "learning_enabled": True, "allow_admins": False}

    async def get_scope_settings(self, scope: Scope) -> EffectiveScopeSettings:
        self._record("get_scope_settings", scope)
        return self.settings()

    async def set_scope_settings(
        self, scope: Scope, updates: dict[str, Any], updated_by: int | None
    ) -> EffectiveScopeSettings:
        self._record("set_scope_settings", scope)
        return self.settings()

    async def preview_scope_settings(
        self, scope: Scope, updates: dict[str, Any], *, user_id: int
    ) -> SettingsChangePreview:
        self._record("preview_scope_settings", scope)
        now = datetime(2026, 6, 23, tzinfo=UTC)
        return SettingsChangePreview(
            scope, dict(updates), dict.fromkeys(updates), "token", now, False
        )

    async def confirm_scope_settings(
        self, scope: Scope, *, user_id: int, confirm: str
    ) -> SettingsChangePreview:
        self._record("confirm_scope_settings", scope)
        if confirm == "bad":
            raise ValueError("No matching pending settings confirmation.")
        return SettingsChangePreview(
            scope, {"retrieval_top_k": 7}, {"retrieval_top_k": 6}, None, None, True
        )

    async def prune_retention(self, scope: Scope) -> dict[str, int]:
        self._record("prune_retention", scope)
        return {"messages_deleted": 0, "embeddings_deleted": 0, "knowledge_deleted": 0}

    async def set_user_policy(
        self, scope: Scope, target_id: int, allowed: bool, updated_by: int | None
    ) -> None:
        self._record("set_user_policy", scope)

    async def set_allow_admins(self, scope: Scope, enabled: bool, updated_by: int | None) -> None:
        self._record("set_allow_admins", scope)

    async def remember(self, scope: Scope, text: str, message_id: int, **kwargs: Any) -> int:
        self._record("remember", scope)
        return 42

    async def list_notes(self, scope: Scope, tag: str | None = None) -> list[MemoryNotePreview]:
        self._record("list_notes", scope)
        return []

    async def edit_note(self, scope: Scope, item_id: int, **kwargs: Any) -> MemoryNotePreview:
        self._record("edit_note", scope)
        return MemoryNotePreview(item_id, scope, "updated", [], None, None, [])

    async def delete_note(self, scope: Scope, item_id: int) -> None:
        self._record("delete_note", scope)

    async def browse_items(
        self, scope: Scope | None = None, *, kind: str | None = None, limit: int = 20
    ) -> list[MemoryItemPreview]:
        self._record("browse_items", scope)
        now = datetime(2026, 6, 23, tzinfo=UTC)
        item_scope = scope or Scope(-10012345, None)
        return [
            MemoryItemPreview(1, item_scope, kind or "manual_note", "memory item", [], {}, now, now)
        ]

    async def delete_memory_item(
        self, scope: Scope, item_id: int, *, deleted_by: int | None = None
    ) -> MemoryItemPreview:
        self._record("delete_memory_item", scope)
        now = datetime(2026, 6, 23, tzinfo=UTC)
        return MemoryItemPreview(item_id, scope, "manual_note", "deleted", [], {}, now, now)

    async def refresh_memory_item(
        self, scope: Scope, item_id: int, *, mode: str = "embed", updated_by: int | None = None
    ) -> MemoryItemRefreshResult:
        self._record("refresh_memory_item", scope)
        return MemoryItemRefreshResult(item_id, scope, "manual_note", mode, False, 1)

    async def summarize_now(self, scope: Scope, **kwargs: Any) -> int:
        self._record("summarize_now", scope)
        return 0

    async def pending_reviews(self, scope: Scope | None = None) -> list[Any]:
        self._record("pending_reviews", scope)
        return []

    async def approve_review(self, review_id: int, *, reviewed_by: int | None = None) -> int:
        self._record("approve_review")
        return 43

    async def reject_review(self, review_id: int, *, reviewed_by: int | None = None) -> None:
        self._record("reject_review")

    async def list_memory_profiles(self) -> list[MemoryProfileView]:
        self._record("list_memory_profiles")
        return []

    async def set_memory_profile(
        self, name: str, sources: list[Scope], **kwargs: Any
    ) -> MemoryProfileView:
        self._record("set_memory_profile", sources[0] if sources else None)
        return MemoryProfileView(name, "", sources)

    async def allow_memory_profile(
        self, *, destination: Scope, profile_name: str, **kwargs: Any
    ) -> MemoryProfileView:
        self._record("allow_memory_profile", destination)
        return MemoryProfileView(profile_name, "", [destination])

    async def list_summary_templates(self) -> list[SummaryTemplatePreview]:
        self._record("list_summary_templates")
        return []

    async def set_summary_template(
        self, name: str, prompt: str, *, updated_by: int | None = None
    ) -> SummaryTemplatePreview:
        self._record("set_summary_template")
        return SummaryTemplatePreview(name, prompt, False)

    async def list_system_prompt_templates(self) -> list[SystemPromptTemplatePreview]:
        self._record("list_system_prompt_templates")
        return []

    async def set_system_prompt_template(
        self, name: str, prompt: str, *, updated_by: int | None = None
    ) -> SystemPromptTemplatePreview:
        self._record("set_system_prompt_template")
        return SystemPromptTemplatePreview(name, "test", prompt, False)

    async def summarize_range(self, scope: Scope, **kwargs: Any) -> RangeSummaryResult:
        self._record("summarize_range", scope)
        now = datetime(2026, 6, 23, tzinfo=UTC)
        return RangeSummaryResult(44, [1, 2], now, now, "general")

    async def forget(self, scope: Scope, user_id: int, confirm: str | None) -> tuple[bool, str]:
        self._record("forget", scope)
        return True, f"Forgot scope {scope.key}: 0 messages, 0 knowledge items, 0 embeddings."

    async def audit_events(
        self, *, limit: int = 50, event_type: str | None = None, scope: Scope | None = None
    ) -> list[AuditEventPreview]:
        self._record("audit_events", scope)
        now = datetime(2026, 6, 23, tzinfo=UTC)
        return [AuditEventPreview(1, event_type or "test", scope, 111, {"ok": True}, now)]

    async def refresh_runtime_settings(self) -> dict[str, Any]:
        self._record("refresh_runtime_settings")
        return {}

    async def set_runtime_settings(
        self, updates: dict[str, Any], *, updated_by: int | None = None
    ) -> list[RuntimeSettingPreview]:
        self._record("set_runtime_settings")
        return await self.list_runtime_settings()

    async def list_runtime_settings(self) -> list[RuntimeSettingPreview]:
        self._record("list_runtime_settings")
        now = datetime(2026, 6, 23, tzinfo=UTC)
        return [RuntimeSettingPreview("retrieval_top_k", 6, 6, None, 111, now, 1)]

    async def ingest(self, incoming: Any) -> Any:
        self._record("ingest", incoming.scope)
        return SimpleNamespace(learned=True, reason="allowed")


class RaisingTelegramMessage:
    chat = SimpleNamespace(id=-10012345)
    message_thread_id = 77
    message_id = 42

    async def answer(self, text: str, **kwargs: Any) -> None:
        raise TelegramRetryAfter(SendMessage(chat_id=self.chat.id, text=text), "retry later", 3)

    async def answer_document(
        self, *, document: BufferedInputFile, caption: str | None = None, **kwargs: Any
    ) -> None:
        raise TelegramAPIError(
            SendDocument(chat_id=self.chat.id, document=document, caption=caption), "failed"
        )


class FakePollingBot:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.session = SimpleNamespace(close=self.close)

    async def delete_webhook(self, *, drop_pending_updates: bool) -> bool:
        self.events.append(f"delete_webhook:{drop_pending_updates}")
        return True

    async def set_my_commands(self, commands: Any) -> None:
        self.events.append("set_my_commands")

    async def close(self) -> None:
        self.events.append("close")


class FakePollingDispatcher:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start_polling(self, bot: Any) -> None:
        self.events.append("start_polling")


def message_update(
    text: str | None = None,
    *,
    update_id: int = 1,
    message_id: int = 1,
    chat_id: int = -10012345,
    user_id: int = 222,
    is_bot: bool = False,
    is_topic_message: bool = False,
    thread_id: int | None = None,
    caption: str | None = None,
    reply_to_message_id: int | None = None,
    photo: bool = False,
    document_file_name: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": message_id,
        "date": 1_782_175_600,
        "chat": {"id": chat_id, "type": "supergroup", "title": "Test Group"},
        "from": {
            "id": user_id,
            "is_bot": is_bot,
            "first_name": "User",
            "username": f"user{user_id}",
        },
    }
    if text is not None:
        message["text"] = text
    if caption is not None:
        message["caption"] = caption
    if photo:
        message["photo"] = [
            {"file_id": "file", "file_unique_id": "unique", "width": 1, "height": 1}
        ]
    if document_file_name:
        message["document"] = {
            "file_id": "doc",
            "file_unique_id": "doc_unique",
            "file_name": document_file_name,
        }
    if is_topic_message:
        message["is_topic_message"] = True
        message["message_thread_id"] = thread_id or 77
    elif thread_id is not None:
        message["message_thread_id"] = thread_id
    if reply_to_message_id is not None:
        message["reply_to_message"] = {
            "message_id": reply_to_message_id,
            "date": 1_782_175_500,
            "chat": message["chat"],
            "from": {"id": 333, "is_bot": False, "first_name": "Reply"},
            "text": "original",
        }
    return {"update_id": update_id, "message": message}


async def feed(dispatcher: Dispatcher, bot: Bot, update_payload: dict[str, Any]) -> Any:
    update = Update.model_validate(update_payload, context={"bot": bot})
    return await dispatcher.feed_update(bot, update)


def command_for(name: str) -> str:
    return {
        "ask": "/ask what happened",
        "memory_search": "/memory_search roadmap",
        "memory_export": "/memory_export md",
        "memory_status": "/memory_status",
        "memory_scopes": "/memory_scopes",
        "memory_debug": "/memory_debug limit=1",
        "memory_items": "/memory_items limit=1",
        "memory_item_delete": "/memory_item_delete 1",
        "memory_item_refresh": "/memory_item_refresh 1 mode=embed",
        "memory_settings": "/memory_settings",
        "settings": "/settings top_k=7",
        "audit_events": "/audit_events limit=1",
        "runtime_settings": "/runtime_settings retrieval_top_k=6",
        "memory_prune": "/memory_prune",
        "memory_reviews": "/memory_reviews",
        "memory_approve": "/memory_approve 1",
        "memory_reject": "/memory_reject 1",
        "memory_notes": "/memory_notes",
        "memory_note_edit": "/memory_note_edit 1 updated note",
        "memory_note_delete": "/memory_note_delete 1",
        "memory_profiles": "/memory_profiles",
        "memory_profile_set": "/memory_profile_set team sources=this_chat description=Team",
        "memory_profile_allow": "/memory_profile_allow team",
        "memory_templates": "/memory_templates",
        "memory_template_set": "/memory_template_set concise Keep it short",
        "system_prompts": "/system_prompts",
        "system_prompt_set": "/system_prompt_set direct Be direct",
        "summarize_range": "/summarize_range start=1 end=2",
        "stats": "/stats",
        "learn_on": "/learn_on",
        "learn_off": "/learn_off",
        "learn_status": "/learn_status",
        "allow_chat": "/allow_chat",
        "deny_chat": "/deny_chat",
        "allow_topic": "/allow_topic",
        "deny_topic": "/deny_topic",
        "allow_user": "/allow_user 222",
        "deny_user": "/deny_user 222",
        "allow_admins": "/allow_admins on",
        "remember": "/remember Durable note",
        "summarize_now": "/summarize_now",
        "forget": "/forget confirm=token",
        "help": "/help",
    }[name]


@pytest.mark.asyncio
async def test_dispatcher_feeds_command_and_normal_message_updates(context: AppContext) -> None:
    session = RecordingTelegramSession(chat_admin_ids={111})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(dispatcher, bot, message_update("/learn_on", update_id=1, message_id=1, user_id=111))
    await feed(
        dispatcher, bot, message_update("Telegram dispatcher fact", update_id=2, message_id=2)
    )

    stats = await context.memory.stats()
    assert stats["messages"] == 1
    assert stats["learned"] == 1
    assert "sendMessage" in session.call_names()
    assert "getChatAdministrators" in session.call_names()


@pytest.mark.asyncio
async def test_bot_api_mock_records_admin_document_and_delete_webhook(context: AppContext) -> None:
    session = RecordingTelegramSession(chat_admin_ids={222})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(dispatcher, bot, message_update("/learn_on", update_id=1, message_id=1, user_id=222))
    await feed(
        dispatcher, bot, message_update("/memory_export md", update_id=2, message_id=2, user_id=111)
    )
    await bot.delete_webhook(drop_pending_updates=False)

    names = session.call_names()
    assert "getChatAdministrators" in names
    assert "sendMessage" in names
    assert "sendDocument" in names
    assert "deleteWebhook" in names


def test_webhook_accepts_valid_update_and_invokes_dispatcher(context: AppContext) -> None:
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    app = create_webhook_app(context, bot=bot)

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            json=message_update("/help", update_id=10, message_id=10, user_id=111),
            headers={"X-Telegram-Bot-Api-Secret-Token": context.settings.webhook_secret},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert {"setMyCommands", "setWebhook", "sendMessage"}.issubset(set(session.call_names()))


@pytest.mark.asyncio
async def test_polling_deletes_webhook_before_starting_and_uses_router(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pzp_ai_bot.telegram.polling as polling_module

    events: list[str] = []
    dispatcher = FakePollingDispatcher(events)

    def fake_build_dispatcher(app_context: AppContext) -> FakePollingDispatcher:
        assert app_context is context
        events.append("build_dispatcher")
        return dispatcher

    monkeypatch.setattr(polling_module, "build_dispatcher", fake_build_dispatcher)
    monkeypatch.setattr(polling_module, "start_idle_summarizer", lambda app_context: "idle")
    monkeypatch.setattr(
        polling_module, "start_runtime_settings_refresher", lambda app_context: "settings"
    )

    async def fake_stop_task(task: Any) -> None:
        events.append(f"stop_task:{task}")

    monkeypatch.setattr(polling_module, "stop_task", fake_stop_task)

    await run_polling(context, bot=cast(Bot, FakePollingBot(events)))

    assert events == [
        "build_dispatcher",
        "delete_webhook:False",
        "set_my_commands",
        "start_polling",
        "stop_task:idle",
        "stop_task:settings",
    ]


@pytest.mark.parametrize("command_name", [name for name, _ in COMMAND_DESCRIPTIONS])
@pytest.mark.asyncio
async def test_all_commands_have_success_dispatch_paths(
    context: AppContext, command_name: str
) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=True)
    session = RecordingTelegramSession(chat_admin_ids={111})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher,
        bot,
        message_update(command_for(command_name), update_id=100, message_id=100, user_id=111),
    )

    assert {"sendMessage", "sendDocument"}.intersection(session.call_names())


@pytest.mark.parametrize(
    "command, expected_fragment",
    [
        ("/ask", "Usage: /ask"),
        ("/memory_export zip", "Usage: /memory_export"),
        ("/summarize_range start=nope end=2", "invalid literal"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_command_arguments_return_usage_or_error(
    context: AppContext, command: str, expected_fragment: str
) -> None:
    test_context = cast(Any, context)
    test_context.memory = RouterMemoryDouble()
    test_context.admins = AllowingAdmins(allow=True)
    session = RecordingTelegramSession(chat_admin_ids={111})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(dispatcher, bot, message_update(command, update_id=200, message_id=200, user_id=111))

    assert any(expected_fragment in text for text in session.sent_texts())


@pytest.mark.asyncio
async def test_unauthorized_admin_command_is_rejected(context: AppContext) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=False, bot_admin_ids={111})
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher, bot, message_update("/learn_on", update_id=300, message_id=300, user_id=222)
    )

    assert not memory.calls
    assert any("requires a bot admin" in text for text in session.sent_texts())


@pytest.mark.asyncio
async def test_non_admin_cannot_export_foreign_scope(context: AppContext) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=False, bot_admin_ids=set())
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher,
        bot,
        message_update(
            "/memory_export md scope=-100999", update_id=310, message_id=310, user_id=222
        ),
    )

    assert not memory.calls
    assert any("another chat scope" in text for text in session.sent_texts())


@pytest.mark.parametrize(
    "command",
    [
        "/forget scope=-100999 confirm=token",
        "/settings scope=-100999 top_k=7",
        "/memory_settings scope=-100999 top_k=8",
        "/memory_item_delete 1 scope=-100999",
    ],
)
@pytest.mark.asyncio
async def test_chat_admin_cannot_mutate_foreign_scope(context: AppContext, command: str) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=True, bot_admin_ids={111})
    session = RecordingTelegramSession(chat_admin_ids={222})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(dispatcher, bot, message_update(command, update_id=320, message_id=320, user_id=222))

    assert not memory.calls
    assert any("another chat scope" in text for text in session.sent_texts())


@pytest.mark.parametrize(
    "command",
    [
        "/memory_profile_set steal sources=-100901",
        "/memory_profile_allow steal scope=-10012345",
        "/runtime_settings retrieval_top_k=7",
        "/memory_template_set concise Keep it short",
        "/system_prompt_set direct Be direct",
    ],
)
@pytest.mark.asyncio
async def test_chat_admin_cannot_change_global_profile_runtime_or_prompt_state(
    context: AppContext, command: str
) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=True, bot_admin_ids={111})
    session = RecordingTelegramSession(chat_admin_ids={222})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(dispatcher, bot, message_update(command, update_id=330, message_id=330, user_id=222))

    assert not memory.calls
    assert any("configured bot admin" in text for text in session.sent_texts())


@pytest.mark.asyncio
async def test_bot_admin_can_target_foreign_scope(context: AppContext) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=False, bot_admin_ids={111})
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher,
        bot,
        message_update(
            "/forget scope=-100999 confirm=token", update_id=340, message_id=340, user_id=111
        ),
    )

    assert ("forget", Scope(-100999, None)) in memory.calls


@pytest.mark.asyncio
async def test_chat_admin_can_target_current_chat_scope(context: AppContext) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=True, bot_admin_ids={111})
    session = RecordingTelegramSession(chat_admin_ids={222})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher,
        bot,
        message_update(
            "/memory_status scope=-10012345", update_id=350, message_id=350, user_id=222
        ),
    )

    assert ("memory_status", Scope(-10012345, None)) in memory.calls


@pytest.mark.asyncio
async def test_topic_command_uses_topic_scope(context: AppContext) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=True)
    session = RecordingTelegramSession(chat_admin_ids={111})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher,
        bot,
        message_update(
            "/allow_topic",
            update_id=400,
            message_id=400,
            user_id=111,
            is_topic_message=True,
            thread_id=99,
        ),
    )

    assert ("set_learning", Scope(-10012345, 99)) in memory.calls


@pytest.mark.asyncio
async def test_ingestion_update_edges_match_scope_and_skip_rules(context: AppContext) -> None:
    session = RecordingTelegramSession(chat_admin_ids={111})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)
    chat_scope = Scope(-10012345, None)
    await context.memory.set_learning(chat_scope, True, 111)

    updates = [
        message_update(
            "topic text", update_id=1, message_id=1, is_topic_message=True, thread_id=10
        ),
        message_update("non topic text", update_id=2, message_id=2, thread_id=11),
        message_update(caption="caption text", update_id=3, message_id=3, reply_to_message_id=2),
        message_update("bot text", update_id=4, message_id=4, is_bot=True),
        message_update("/unknown", update_id=5, message_id=5),
        message_update("   ", update_id=6, message_id=6),
        message_update(update_id=7, message_id=7, photo=True),
        message_update(update_id=8, message_id=8, document_file_name="runbook.pdf"),
    ]
    for payload in updates:
        await feed(dispatcher, bot, payload)

    async with session_scope(context.session_factory) as session_db:
        rows = list(
            (await session_db.scalars(select(Message).order_by(Message.telegram_message_id))).all()
        )

    assert [
        (row.telegram_message_id, row.message_thread_id, row.learned, row.skip_reason)
        for row in rows
    ] == [
        (1, 10, True, None),
        (2, None, True, None),
        (3, None, True, None),
        (4, None, False, "bot_message"),
        (5, None, False, "command"),
        (6, None, False, "empty_text"),
        (8, None, True, None),
    ]
    assert rows[2].text == "caption text"
    assert rows[2].reply_to_message_id == 2
    assert rows[6].text == "Attachment: runbook.pdf"


@pytest.mark.asyncio
async def test_telegram_rate_limit_does_not_crash_dispatch(context: AppContext) -> None:
    session = RecordingTelegramSession()
    session.retry_after_methods["sendMessage"] = 3
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(dispatcher, bot, message_update("/help", update_id=500, message_id=500, user_id=111))

    assert "sendMessage" in session.call_names()


@pytest.mark.asyncio
async def test_export_document_api_failure_sends_fallback_without_crashing(
    context: AppContext,
) -> None:
    session = RecordingTelegramSession()
    session.api_error_methods.add("sendDocument")
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher,
        bot,
        message_update("/memory_export md", update_id=600, message_id=600, user_id=111),
    )

    assert "sendDocument" in session.call_names()
    assert "sendDocument" in session.call_names()
    assert any("could not be sent" in text for text in session.sent_texts())


def test_export_documents_chunk_below_telegram_document_limit() -> None:
    documents = export_documents("abcdef", filename="memory.md", max_bytes=2)

    assert [document.filename for document in documents] == [
        "memory.part001of003.md",
        "memory.part002of003.md",
        "memory.part003of003.md",
    ]
    assert len(documents) == 3

    with pytest.raises(ValueError, match="Telegram document upload limit"):
        export_documents("payload", filename="memory.md", max_bytes=60 * 1024 * 1024)


@pytest.mark.asyncio
async def test_safe_answer_logs_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[tuple[str, dict[str, Any]]] = []

    def record_warning(event: str, **metadata: Any) -> None:
        warnings.append((event, metadata))

    monkeypatch.setattr(safe_api_module.logger, "warning", record_warning)

    ok = await safe_answer(cast(TelegramMessage, RaisingTelegramMessage()), "hello")

    assert not ok
    assert warnings[0][0] == "telegram_rate_limited"
    assert warnings[0][1]["retry_after"] == 3


@pytest.mark.asyncio
async def test_safe_answer_document_logs_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[tuple[str, dict[str, Any]]] = []

    def record_warning(event: str, **metadata: Any) -> None:
        warnings.append((event, metadata))

    monkeypatch.setattr(safe_api_module.logger, "warning", record_warning)

    ok = await safe_answer_document(
        cast(TelegramMessage, RaisingTelegramMessage()),
        document=BufferedInputFile(b"payload", filename="memory.md"),
        caption="export",
    )

    assert not ok
    assert warnings[0][0] == "telegram_api_error"
    assert warnings[0][1]["chat_id"] == -10012345


@pytest.mark.parametrize(
    ("command", "expected_call", "expected_fragment"),
    [
        ("/settings", "get_scope_settings", "retrieval_top_k: 6"),
        ("/memory_settings top_k=8", "set_scope_settings", "Updated memory settings"),
        ("/settings top_k=7", "preview_scope_settings", "Preview only"),
        ("/settings confirm=token", "confirm_scope_settings", "Applied and audited"),
        (
            "/settings confirm=bad",
            "confirm_scope_settings",
            "No matching pending settings confirmation",
        ),
    ],
)
@pytest.mark.asyncio
async def test_settings_command_view_edit_preview_confirm_and_reject(
    context: AppContext, command: str, expected_call: str, expected_fragment: str
) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=True)
    session = RecordingTelegramSession(chat_admin_ids={111})
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(dispatcher, bot, message_update(command, update_id=900, message_id=900, user_id=111))

    assert any(call_name == expected_call for call_name, _scope in memory.calls)
    assert any(expected_fragment in text for text in session.sent_texts())


@pytest.mark.asyncio
async def test_settings_command_rejects_unauthorized_admin(context: AppContext) -> None:
    memory = RouterMemoryDouble()
    test_context = cast(Any, context)
    test_context.memory = memory
    test_context.admins = AllowingAdmins(allow=False, bot_admin_ids={111})
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher,
        bot,
        message_update("/settings top_k=7", update_id=901, message_id=901, user_id=222),
    )

    assert not memory.calls
    assert any("requires a bot admin" in text for text in session.sent_texts())
