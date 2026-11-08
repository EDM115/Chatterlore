from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import APIConnectionError
from openai.types.chat import ChatCompletionChunk
from sqlalchemy import select

from chatterlore.ai import openai_compatible as provider_module
from chatterlore.ai.openai_compatible import OpenAICompatibleProvider
from chatterlore.ai.protocols import ChatMessage, ChatResult
from chatterlore.ai.provider_call_policy import ProviderLeaseCallError
from chatterlore.app_context import AppContext
from chatterlore.db.models import AIUsage
from chatterlore.db.session import session_scope
from chatterlore.memory.scope import Scope


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_empty_chat_response_is_not_a_completed_answer_and_preserves_token_usage(
    context: AppContext, monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    scope = Scope(-10012345, None)
    await context.memory.remember(scope, "Copper marker is Copper 1234")

    async def generate(*args: Any, **kwargs: Any) -> ChatResult:
        return ChatResult(" \n", "fake-chat", 13, 700)

    async def progress(text: str) -> None:
        return None

    monkeypatch.setattr(context.memory.chat_provider, "generate", generate)
    if streaming:
        monkeypatch.setattr(
            context.memory.chat_provider, "generate_stream", generate, raising=False
        )
    answer = await context.memory.ask(
        scope, "Copper marker", on_text=progress if streaming else None
    )
    assert "returned no answer text" in answer
    assert "Copper 1234" in answer
    async with session_scope(context.session_factory) as session:
        [usage] = list(
            (await session.scalars(select(AIUsage).where(AIUsage.operation == "ask"))).all()
        )
        assert usage.error is not None and "EmptyChatResponse" in usage.error
        assert usage.prompt_tokens == 13 and usage.completion_tokens == 700
        assert usage.metadata_json is not None
        assert usage.metadata_json["response_empty"] is True


def chunk(
    content: str | None = None,
    *,
    usage: bool = False,
    reasoning: str | None = None,
    finish: bool = False,
) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate(
        {
            "id": "completion-test",
            "object": "chat.completion.chunk",
            "created": 1794078000,
            "model": "gpt-6.1-terra",
            "choices": []
            if usage
            else [
                {
                    "index": 0,
                    "delta": {"content": content, "reasoning_content": reasoning},
                    "finish_reason": "stop" if finish else None,
                }
            ],
            "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}
            if usage
            else None,
        }
    )


class ControlledStream:
    def __init__(
        self,
        items: list[ChatCompletionChunk | Exception],
        *,
        wait_after: bool = False,
        close_error: Exception | None = None,
    ) -> None:
        self.items = items
        self.wait_after = wait_after
        self.close_error = close_error
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        for item in self.items:
            if isinstance(item, Exception):
                raise item
            yield item
        if self.wait_after:
            self.waiting.set()
            await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class StreamEndpoint:
    def __init__(self, streams: list[ControlledStream]) -> None:
        self.streams = streams
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> ControlledStream:
        self.requests.append(kwargs)
        return self.streams[len(self.requests) - 1]


def provider_for(
    endpoint: StreamEndpoint, *, model: str = "gpt-6.1-terra", dialect: str = "openai"
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        chat_model=model,
        embedding_model="embedding-test",
        retry_attempts=2,
        retry_backoff_seconds=0,
        api_dialect=dialect,
        client=SimpleNamespace(chat=SimpleNamespace(completions=endpoint)),
    )


@pytest.mark.asyncio
async def test_real_stream_reports_only_visible_cumulative_text_and_final_usage() -> None:
    stream = ControlledStream(
        [
            chunk(reasoning="PRIVATE_REASONING"),
            chunk("**Copper"),
            chunk(" 1234**"),
            chunk(finish=True),
            chunk(usage=True),
        ]
    )
    endpoint = StreamEndpoint([stream])
    provider = provider_for(endpoint)
    snapshots: list[str] = []

    async def progress(text: str) -> None:
        assert not stream.closed
        snapshots.append(text)

    result = await provider.generate_stream(
        [ChatMessage("user", "Contact demo@example.invalid")],
        on_text=progress,
        max_tokens=123,
        reasoning_effort="medium",
    )

    assert snapshots == ["**Copper", "**Copper 1234**"]
    assert result == ChatResult("**Copper 1234**", "gpt-6.1-terra", 13, 7)
    assert stream.closed
    [request] = endpoint.requests
    assert request["stream"] is True and request["stream_options"] == {"include_usage": True}
    assert request["max_completion_tokens"] == 123 and "temperature" not in request
    assert request["reasoning_effort"] == "medium"
    assert "demo@example.invalid" not in str(request["messages"])


@pytest.mark.asyncio
async def test_generic_stream_uses_only_compatible_budget_fields_and_keeps_unknown_usage() -> None:
    stream = ControlledStream([chunk("Result"), chunk(finish=True)])
    endpoint = StreamEndpoint([stream])
    provider = provider_for(endpoint, model="local-model", dialect="generic")

    async def progress(text: str) -> None:
        assert text == "Result"

    result = await provider.generate_stream(
        [ChatMessage("user", "test")], on_text=progress, max_tokens=91
    )

    [request] = endpoint.requests
    assert request["stream"] is True and request["max_tokens"] == 91
    assert "stream_options" not in request and "reasoning_effort" not in request
    assert result.prompt_tokens is None and result.completion_tokens is None
    assert stream.closed


@pytest.mark.asyncio
async def test_stream_can_retry_before_content_and_closes_the_failed_attempt() -> None:
    failure = APIConnectionError(request=httpx2.Request("POST", "https://example.invalid/v1"))
    failed = ControlledStream([failure])
    success = ControlledStream([chunk("One answer"), chunk(finish=True)])
    endpoint = StreamEndpoint([failed, success])
    provider = provider_for(endpoint)
    snapshots: list[str] = []

    async def progress(text: str) -> None:
        snapshots.append(text)

    result = await provider.generate_stream([ChatMessage("user", "test")], on_text=progress)

    assert result.content == "One answer" and snapshots == ["One answer"]
    assert len(endpoint.requests) == 2 and failed.closed and success.closed


@pytest.mark.asyncio
async def test_stream_never_replays_a_partial_answer_after_transport_failure() -> None:
    failure = APIConnectionError(request=httpx2.Request("POST", "https://example.invalid/v1"))
    failed = ControlledStream([chunk("Partial"), failure])
    endpoint = StreamEndpoint([failed, ControlledStream([chunk("Must not start another answer")])])
    provider = provider_for(endpoint)
    snapshots: list[str] = []

    async def progress(text: str) -> None:
        snapshots.append(text)

    with pytest.raises(ProviderLeaseCallError):
        await provider.generate_stream([ChatMessage("user", "test")], on_text=progress)

    assert snapshots == ["Partial"] and len(endpoint.requests) == 1 and failed.closed


@pytest.mark.asyncio
async def test_silent_stream_eof_without_a_finish_marker_is_not_a_successful_completion() -> None:
    stream = ControlledStream([chunk("Truncated answer")])
    endpoint = StreamEndpoint([stream])
    provider = provider_for(endpoint)

    async def progress(text: str) -> None:
        assert text == "Truncated answer"

    with pytest.raises(ProviderLeaseCallError):
        await provider.generate_stream([ChatMessage("user", "test")], on_text=progress)
    assert stream.closed and len(endpoint.requests) == 1


@pytest.mark.asyncio
async def test_stream_close_error_after_output_cannot_retry_the_generation() -> None:
    close_error = APIConnectionError(request=httpx2.Request("POST", "https://example.invalid/v1"))
    stream = ControlledStream([chunk("Shown"), chunk(finish=True)], close_error=close_error)
    endpoint = StreamEndpoint([stream])
    provider = provider_for(endpoint)

    async def progress(text: str) -> None:
        assert text == "Shown"

    with pytest.raises(ProviderLeaseCallError):
        await provider.generate_stream([ChatMessage("user", "test")], on_text=progress)
    assert stream.closed and len(endpoint.requests) == 1


@pytest.mark.asyncio
async def test_stream_close_error_does_not_replace_user_cancellation_with_a_retry() -> None:
    close_error = APIConnectionError(request=httpx2.Request("POST", "https://example.invalid/v1"))
    stream = ControlledStream([chunk("Partial")], wait_after=True, close_error=close_error)
    endpoint = StreamEndpoint([stream])
    provider = provider_for(endpoint)

    async def progress(text: str) -> None:
        assert text == "Partial"

    task = asyncio.create_task(
        provider.generate_stream([ChatMessage("user", "test")], on_text=progress)
    )
    await asyncio.wait_for(stream.waiting.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed and len(endpoint.requests) == 1


@pytest.mark.asyncio
async def test_stream_has_an_overall_deadline_even_when_no_more_tokens_arrive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_module, "CHAT_STREAM_TIMEOUT_SECONDS", 0.01, raising=False)
    stream = ControlledStream([chunk("Partial")], wait_after=True)
    provider = provider_for(StreamEndpoint([stream]))

    async def progress(text: str) -> None:
        assert text == "Partial"

    task = asyncio.create_task(
        provider.generate_stream([ChatMessage("user", "test")], on_text=progress)
    )
    try:
        await asyncio.wait_for(stream.waiting.wait(), timeout=2)
        await asyncio.wait({task}, timeout=0.2)
        assert task.done(), "the provider must impose its own full-stream deadline"
        with pytest.raises(TimeoutError):
            await task
        assert stream.closed
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelling_generation_closes_the_stream_and_releases_provider_shutdown() -> None:
    stream = ControlledStream([chunk("Partial")], wait_after=True)
    provider = provider_for(StreamEndpoint([stream]))

    async def progress(text: str) -> None:
        assert text == "Partial"

    task = asyncio.create_task(
        provider.generate_stream([ChatMessage("user", "test")], on_text=progress)
    )
    await asyncio.wait_for(stream.waiting.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(provider.aclose(), timeout=2)
    assert stream.closed


@pytest.mark.asyncio
async def test_stream_output_is_bounded_even_if_a_compatible_endpoint_ignores_token_budget() -> (
    None
):
    stream = ControlledStream([chunk("x" * 32769)])
    endpoint = StreamEndpoint([stream])
    provider = provider_for(endpoint)

    async def forbidden_progress(text: str) -> None:
        raise AssertionError("oversized content must not be published")

    with pytest.raises(ProviderLeaseCallError):
        await provider.generate_stream([ChatMessage("user", "test")], on_text=forbidden_progress)
    assert stream.closed and len(endpoint.requests) == 1


@pytest.mark.asyncio
async def test_ask_progress_failure_is_not_recorded_as_an_ai_failure(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-772, 3)
    await context.memory.remember(scope, "Copper marker is Copper 1234")

    async def generate_stream(messages: Any, *, on_text: Any, **kwargs: Any) -> ChatResult:
        await on_text("**Copper")
        return ChatResult("**Copper 1234**", "fake-chat", 13, 7)

    async def broken_presentation(text: str) -> None:
        raise RuntimeError("PRIVATE_PRESENTATION_DETAIL")

    monkeypatch.setattr(
        context.memory.chat_provider, "generate_stream", generate_stream, raising=False
    )
    answer = await context.memory.ask(scope, "Copper marker", on_text=broken_presentation)

    assert "**Copper 1234**" in answer
    async with session_scope(context.session_factory) as session:
        [usage] = list(
            (await session.scalars(select(AIUsage).where(AIUsage.operation == "ask"))).all()
        )
        assert usage.error is None and usage.message_thread_id == 3
        assert (usage.prompt_tokens, usage.completion_tokens) == (13, 7)


@pytest.mark.asyncio
async def test_cancelled_ask_retains_safe_usage_without_persisting_partial_content(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = Scope(-773, 5)
    await context.memory.remember(scope, "Copper marker is Copper 1234")

    async def generate_stream(messages: Any, *, on_text: Any, **kwargs: Any) -> ChatResult:
        await on_text("PRIVATE_PARTIAL_OUTPUT")
        raise AssertionError("cancelled generation must not return a completion")

    async def stop(text: str) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        context.memory.chat_provider, "generate_stream", generate_stream, raising=False
    )
    with pytest.raises(asyncio.CancelledError):
        await context.memory.ask(scope, "Copper marker", on_text=stop)
    async with session_scope(context.session_factory) as session:
        [usage] = list(
            (await session.scalars(select(AIUsage).where(AIUsage.operation == "ask"))).all()
        )
        assert usage.message_thread_id == 5 and usage.error is not None
        assert "CancelledError" in usage.error
        assert "PRIVATE_PARTIAL_OUTPUT" not in str(usage.metadata_json)


@pytest.mark.asyncio
async def test_nonstreaming_provider_keeps_normal_ask_behavior_without_fake_progress(
    context: AppContext,
) -> None:
    scope = Scope(-774, None)
    await context.memory.remember(scope, "Copper marker is Copper 1234")
    snapshots: list[str] = []

    async def progress(text: str) -> None:
        snapshots.append(text)

    answer = await context.memory.ask(scope, "Copper marker", on_text=progress)

    assert "Copper 1234" in answer and snapshots == []
