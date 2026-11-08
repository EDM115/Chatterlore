from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendRichMessageDraft
from aiogram.types import Chat, MessageGenerationStopped
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from pzp_ai_bot.ai.protocols import ChatResult
from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.models import AIUsage
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.telegram.dispatcher import build_dispatcher
from pzp_ai_bot.telegram.webhook import create_webhook_app
from test_telegram_integration import RecordingTelegramSession, feed, message_update
from test_telegram_presentation import PresentationSession, incoming


def streaming_api() -> Any:
    try:
        return importlib.import_module("pzp_ai_bot.telegram.streaming")
    except ModuleNotFoundError:
        pytest.fail("request-owned Telegram streaming delivery is not implemented")


def private_message(bot: Bot):
    return (
        incoming(bot)
        .model_copy(
            update={
                "chat": Chat(id=111, type="private"),
                "business_connection_id": None,
                "is_topic_message": False,
                "message_thread_id": None,
            }
        )
        .as_(bot)
    )


@pytest.mark.asyncio
async def test_private_drafts_animate_one_id_then_persist_the_complete_rich_answer() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    clock = {"now": 0.0}
    delivery = streaming_api().AnswerDelivery(private_message(bot), clock=lambda: clock["now"])

    await delivery.update("**Copper")
    clock["now"] = 1.1
    await delivery.update("**Copper 1234**")
    assert await delivery.finish("**Copper 1234**\n[source:17]")

    assert [method.__api_method__ for method in session.calls] == [
        "sendRichMessageDraft",
        "sendRichMessageDraft",
        "sendRichMessage",
    ]
    first, second, final = session.calls
    assert first.draft_id == second.draft_id and first.draft_id != 0
    assert first.chat_id == second.chat_id == final.chat_id == 111
    assert first.can_stop and not first.keep_on_stop
    assert "<strong>Copper 1234</strong>" in final.rich_message.html
    assert "[source:17]" in final.rich_message.html


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["#", "## ", "```python\n", "- "])
async def test_formatting_only_prefix_does_not_send_an_empty_draft_or_throttle_next_text(
    prefix: str,
) -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    delivery = streaming_api().AnswerDelivery(private_message(bot), clock=lambda: 0.0)
    await delivery.update(prefix)
    assert session.calls == []
    await delivery.update("# Visible answer")
    assert len(session.calls) == 1
    draft = session.calls[0]
    assert draft.__api_method__ == "sendRichMessageDraft"
    assert "Visible answer" in draft.rich_message.html
    assert draft.can_stop is True
    await bot.session.close()


@pytest.mark.asyncio
async def test_group_stream_throttles_edits_of_one_message_and_preserves_its_topic() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    clock = {"now": 0.0}
    delivery = streaming_api().AnswerDelivery(incoming(bot), clock=lambda: clock["now"])

    await delivery.update("One")
    clock["now"] = 1.0
    await delivery.update("One two")
    assert len(session.calls) == 1
    clock["now"] = 3.1
    await delivery.update("One two three")
    assert await delivery.finish("**Complete**\n[source:17]")

    assert [method.__api_method__ for method in session.calls] == [
        "sendRichMessage",
        "editMessageText",
        "editMessageText",
    ]
    initial, progress, final = session.calls
    assert initial.message_thread_id == 3
    assert (
        initial.business_connection_id
        == progress.business_connection_id
        == final.business_connection_id
        == "business-sentinel"
    )
    assert progress.message_id == final.message_id == 901
    assert progress.chat_id == final.chat_id == -100123456
    assert "Generating" in initial.rich_message.html and "Generating" not in final.rich_message.html
    assert (
        "<strong>Complete</strong>" in final.rich_message.html
        and "[source:17]" in final.rich_message.html
    )


@pytest.mark.asyncio
async def test_failed_private_preview_does_not_prevent_a_single_persisted_final_reply() -> None:
    class BrokenDraftSession(PresentationSession):
        async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
            if method.__api_method__ == "sendRichMessageDraft":
                self.calls.append(method)
                raise TelegramRetryAfter(method, "retry", 2)
            return await super().make_request(bot, method, timeout=timeout)

    session = BrokenDraftSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    clock = {"now": 0.0}
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock["now"] += seconds

    delivery = streaming_api().AnswerDelivery(
        private_message(bot), clock=lambda: clock["now"], sleep=sleep
    )
    await delivery.update("Partial")
    await delivery.update("Another delta before retry-after")
    assert await delivery.finish("Complete")

    assert waits == [2.0]
    assert [method.__api_method__ for method in session.calls] == [
        "sendRichMessageDraft",
        "sendRichMessage",
    ]


@pytest.mark.asyncio
async def test_ambiguous_initial_group_send_is_not_duplicated_by_finalization() -> None:
    session = PresentationSession("network")
    bot = Bot("123456:TEST_TOKEN", session=session)
    delivery = streaming_api().AnswerDelivery(incoming(bot))

    await delivery.update("Partial")
    assert not await delivery.finish("Completed")

    assert [method.__api_method__ for method in session.calls] == ["sendRichMessage"]


@pytest.mark.asyncio
async def test_rich_rejected_group_progress_finishes_in_place_and_preserves_long_literal_text() -> (
    None
):
    session = PresentationSession("format")
    bot = Bot("123456:TEST_TOKEN", session=session)
    delivery = streaming_api().AnswerDelivery(incoming(bot))

    await delivery.update("Partial")
    final_text = "😀_literal " * 700 + "\n[source:17]"
    assert await delivery.finish(final_text)

    assert session.calls[0].__api_method__ == "sendRichMessage"
    assert session.calls[1].__api_method__ == "sendMessage"
    final_parts = session.calls[2:]
    assert final_parts[0].__api_method__ == "editMessageText"
    assert final_parts[0].message_id == 902
    assert "".join(method.text for method in final_parts) == final_text
    assert all(method.parse_mode is None for method in final_parts)


@pytest.mark.asyncio
async def test_stop_update_cancels_only_its_private_chat_thread_and_draft_then_forgets_it() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    registry = streaming_api().AnswerRegistry()
    ready = asyncio.Event()
    holder: dict[str, Any] = {}

    async def generate() -> None:
        delivery = registry.start(private_message(bot))
        holder["delivery"] = delivery
        try:
            await delivery.update("Partial")
            ready.set()
            await asyncio.Event().wait()
        finally:
            registry.discard(delivery)

    task = asyncio.create_task(generate())
    await asyncio.wait_for(ready.wait(), timeout=2)
    draft_id = holder["delivery"].draft_id
    for chat_id, kind, thread, draft in [
        (222, "private", None, draft_id),
        (111, "private", 5, draft_id),
        (111, "private", None, draft_id + 1),
        (111, "supergroup", None, draft_id),
    ]:
        event = MessageGenerationStopped(
            chat=Chat(id=chat_id, type=kind), message_thread_id=thread, draft_id=draft
        )
        assert not registry.stop(bot.id, event)
        assert not task.done()
    matching = MessageGenerationStopped(chat=Chat(id=111, type="private"), draft_id=draft_id)
    assert registry.stop(bot.id, matching)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not registry.stop(bot.id, matching)


@pytest.mark.asyncio
async def test_real_dispatcher_ask_wires_streaming_and_keeps_final_sources_after_3900_characters(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-10012345, 3)
    await context.memory.remember(scope, "Copper marker is Copper 1234")
    long_answer = "**Copper 1234**\n\n" + "Complete output. " * 400

    async def stream(messages: Any, *, on_text: Any, **kwargs: Any) -> ChatResult:
        await on_text("**Copper")
        return ChatResult(long_answer, "fake-chat", 10, 700)

    monkeypatch.setattr(context.memory.chat_provider, "generate_stream", stream, raising=False)
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)

    await feed(
        dispatcher,
        bot,
        message_update("/ask Copper marker", user_id=111, is_topic_message=True, thread_id=3),
    )

    names = session.call_names()
    assert "sendRichMessageDraft" not in names
    assert names.count("sendRichMessage") == 1 and names.count("editMessageText") == 1
    final = session.calls[-1]
    assert final.__api_method__ == "editMessageText"
    payload = final.model_dump(exclude_none=True)["rich_message"]["html"]
    assert payload.count("Complete output.") == 400
    assert "<strong>Copper 1234</strong>" in payload
    assert "Sources:" in payload or "[source:" in payload or "[item:" in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_dispatcher_reports_interruption_instead_of_silently_replacing_partial_output(
    context: AppContext, monkeypatch: pytest.MonkeyPatch, private: bool
) -> None:
    chat_id = 111 if private else -10012345
    await context.memory.remember(Scope(chat_id, None), "Copper marker is Copper 1234")

    async def stream(messages: Any, *, on_text: Any, **kwargs: Any) -> ChatResult:
        await on_text("PRIVATE_PARTIAL_OUTPUT")
        raise RuntimeError("PRIVATE_STREAM_ERROR")

    monkeypatch.setattr(context.memory.chat_provider, "generate_stream", stream, raising=False)
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)
    update = message_update("/ask Copper marker", user_id=111, chat_id=chat_id)
    if private:
        update["message"]["chat"]["type"] = "private"

    await feed(dispatcher, bot, update)

    final = session.calls[-1]
    payload = str(final.model_dump(exclude_none=True)["rich_message"])
    assert "interrupted" in payload.lower()
    assert "From scoped memory" not in payload
    assert "PRIVATE_PARTIAL_OUTPUT" not in payload and "PRIVATE_STREAM_ERROR" not in payload
    async with session_scope(context.session_factory) as db:
        [usage] = list((await db.scalars(select(AIUsage).where(AIUsage.operation == "ask"))).all())
        assert usage.error is not None
        assert usage.metadata_json is not None
        assert usage.metadata_json["stream_interrupted"] is True
        assert "PRIVATE_PARTIAL_OUTPUT" not in str(usage.metadata_json)


@pytest.mark.asyncio
async def test_superseding_one_scope_cancels_old_work_without_removing_the_new_owner() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    registry = streaming_api().AnswerRegistry()
    ready = asyncio.Event()

    async def old_generation() -> None:
        old = registry.start(private_message(bot))
        try:
            ready.set()
            await asyncio.Event().wait()
        finally:
            registry.discard(old)

    task = asyncio.create_task(old_generation())
    await asyncio.wait_for(ready.wait(), timeout=2)
    current = registry.start(private_message(bot))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert current.draft_id != 0
    registry.discard(current)


@pytest.mark.asyncio
async def test_dispatcher_stop_update_closes_provider_and_sends_only_a_stopped_final_reply(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await context.memory.remember(Scope(111, None), "Copper marker is Copper 1234")
    ready = asyncio.Event()
    closed = False

    async def stream(messages: Any, *, on_text: Any, **kwargs: Any) -> ChatResult:
        nonlocal closed
        try:
            await on_text("PRIVATE_PARTIAL_OUTPUT")
            ready.set()
            await asyncio.Event().wait()
        finally:
            closed = True
        raise AssertionError("stopped stream cannot complete")

    monkeypatch.setattr(context.memory.chat_provider, "generate_stream", stream, raising=False)
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)
    update = message_update("/ask Copper marker", user_id=111, chat_id=111)
    update["message"]["chat"]["type"] = "private"
    task = asyncio.create_task(feed(dispatcher, bot, update))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        draft = next(method for method in session.calls if isinstance(method, SendRichMessageDraft))
        await feed(
            dispatcher,
            bot,
            {
                "update_id": 88,
                "stopped_message_generation": {
                    "chat": {"id": 111, "type": "private"},
                    "draft_id": draft.draft_id,
                },
            },
        )
        await asyncio.wait_for(task, timeout=5)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert closed
    assert session.sent_texts() == ["Generation stopped."]
    async with session_scope(context.session_factory) as db:
        [usage] = list((await db.scalars(select(AIUsage).where(AIUsage.operation == "ask"))).all())
        assert usage.error is not None and "CancelledError" in usage.error
        assert "PRIVATE_PARTIAL_OUTPUT" not in str(usage.metadata_json)


@pytest.mark.asyncio
@pytest.mark.parametrize("command_field", ["text", "caption"])
async def test_webhook_acknowledges_stream_before_next_stop_update(
    context: AppContext, monkeypatch: pytest.MonkeyPatch, command_field: str
) -> None:
    await context.memory.remember(Scope(111, None), "Copper marker is Copper 1234")
    ready = asyncio.Event()
    closed = asyncio.Event()
    calls = 0

    async def stream(messages: Any, *, on_text: Any, **kwargs: Any) -> ChatResult:
        nonlocal calls
        calls += 1
        try:
            await on_text("PRIVATE_PARTIAL_OUTPUT")
            ready.set()
            await asyncio.Event().wait()
        finally:
            closed.set()
        raise AssertionError("stopped stream cannot complete")

    monkeypatch.setattr(context.memory.chat_provider, "generate_stream", stream, raising=False)
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    app = create_webhook_app(context, bot=bot)
    headers = {"X-Telegram-Bot-Api-Secret-Token": context.settings.webhook_secret}
    update = message_update("/ask Copper marker", user_id=111, chat_id=111)
    update["message"]["chat"]["type"] = "private"
    if command_field == "caption":
        update["message"]["caption"] = update["message"].pop("text")
        update["message"]["caption_entities"] = [{"type": "bot_command", "offset": 0, "length": 4}]
        update["message"]["photo"] = [
            {"file_id": "synthetic", "file_unique_id": "synthetic", "width": 1, "height": 1}
        ]
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="https://test") as client,
    ):
        response = await asyncio.wait_for(
            client.post("/telegram/webhook", json=update, headers=headers), timeout=2
        )
        assert response.status_code == 200
        await asyncio.wait_for(ready.wait(), timeout=5)
        duplicate = await client.post("/telegram/webhook", json=update, headers=headers)
        assert duplicate.status_code == 200 and calls == 1
        draft = next(method for method in session.calls if isinstance(method, SendRichMessageDraft))
        stop = {
            "update_id": 88,
            "stopped_message_generation": {
                "chat": {"id": 111, "type": "private"},
                "draft_id": draft.draft_id,
            },
        }
        response = await client.post("/telegram/webhook", json=stop, headers=headers)
        assert response.status_code == 200
        await asyncio.wait_for(closed.wait(), timeout=5)
        await asyncio.gather(*app.state.webhook_answer_tasks.values())
        replay = await client.post("/telegram/webhook", json=update, headers=headers)
        assert replay.status_code == 200 and calls == 1
    assert session.sent_texts() == ["Generation stopped."]


@pytest.mark.asyncio
async def test_webhook_answer_queue_is_bounded_and_shutdown_closes_stream(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pzp_ai_bot.telegram.webhook as webhook_module

    monkeypatch.setattr(webhook_module, "MAX_WEBHOOK_ANSWER_TASKS", 1, raising=False)
    await context.memory.remember(Scope(111, None), "Copper marker is Copper 1234")
    ready = asyncio.Event()
    closed = asyncio.Event()

    async def stream(messages: Any, *, on_text: Any, **kwargs: Any) -> ChatResult:
        try:
            await on_text("PRIVATE_PARTIAL_OUTPUT")
            ready.set()
            await asyncio.Event().wait()
        finally:
            closed.set()
        raise AssertionError("shutdown stream cannot complete")

    monkeypatch.setattr(context.memory.chat_provider, "generate_stream", stream, raising=False)
    bot = Bot(context.settings.telegram_bot_token, session=RecordingTelegramSession())
    app = create_webhook_app(context, bot=bot)
    headers = {"X-Telegram-Bot-Api-Secret-Token": context.settings.webhook_secret}
    update = message_update("/ask Copper marker", user_id=111, chat_id=111)
    update["message"]["chat"]["type"] = "private"
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="https://test") as client,
    ):
        response = await asyncio.wait_for(
            client.post("/telegram/webhook", json=update, headers=headers), timeout=2
        )
        assert response.status_code == 200
        await asyncio.wait_for(ready.wait(), timeout=5)
        update["update_id"] += 1
        update["message"]["message_id"] += 1
        overloaded = await client.post("/telegram/webhook", json=update, headers=headers)
        assert overloaded.status_code == 503
        assert not closed.is_set()
    assert closed.is_set()
    assert not app.state.webhook_answer_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["   ", "/askew"])
async def test_webhook_non_answer_updates_keep_synchronous_handling(
    context: AppContext, text: str
) -> None:
    bot = Bot(context.settings.telegram_bot_token, session=RecordingTelegramSession())
    app = create_webhook_app(context, bot=bot)
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        response = await client.post(
            "/telegram/webhook",
            json=message_update(text),
            headers={"X-Telegram-Bot-Api-Secret-Token": context.settings.webhook_secret},
        )
    assert response.status_code == 200
    assert not app.state.webhook_answer_tasks


@pytest.mark.asyncio
async def test_dispatcher_shutdown_cancels_inflight_generation_and_clears_ownership(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await context.memory.remember(Scope(-10012345, None), "Copper marker is Copper 1234")
    ready = asyncio.Event()
    closed = False

    async def stream(messages: Any, *, on_text: Any, **kwargs: Any) -> ChatResult:
        nonlocal closed
        try:
            await on_text("Partial")
            ready.set()
            await asyncio.Event().wait()
        finally:
            closed = True
        raise AssertionError("shutdown stream cannot complete")

    monkeypatch.setattr(context.memory.chat_provider, "generate_stream", stream, raising=False)
    session = RecordingTelegramSession()
    bot = Bot(context.settings.telegram_bot_token, session=session)
    dispatcher = build_dispatcher(context)
    task = asyncio.create_task(
        feed(dispatcher, bot, message_update("/ask Copper marker", user_id=111))
    )
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        await dispatcher.emit_shutdown(bot=bot)
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert closed
    assert session.call_names().count("sendRichMessage") == 1
