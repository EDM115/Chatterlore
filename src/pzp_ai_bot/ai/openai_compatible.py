from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from pzp_ai_bot.ai.protocols import ChatMessage, ChatResult
from pzp_ai_bot.ai.redaction import redact_text


class OpenAICompatibleProvider:
    """OpenAI-compatible chat and embedding provider."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        redact: bool = True,
        redaction_custom_patterns: Sequence[str] = (),
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.5,
        client: Any | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI-compatible provider requires an API key")
        self.client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = chat_model
        self.embedding_model = embedding_model
        self.dimensions = 1536
        self.redact = redact
        self.redaction_custom_patterns = tuple(redaction_custom_patterns)
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    async def generate(
        self, messages: Sequence[ChatMessage], *, temperature: float = 0.2, max_tokens: int = 700
    ) -> ChatResult:
        payload = [
            cast(
                ChatCompletionMessageParam,
                {"role": message.role, "content": self._maybe_redact(message.content)},
            )
            for message in messages
        ]
        response = await retry_provider_call(
            lambda: self.client.chat.completions.create(
                model=self.model, messages=payload, temperature=temperature, max_tokens=max_tokens
            ),
            attempts=self.retry_attempts,
            delay=self.retry_backoff_seconds,
        )
        content = response.choices[0].message.content or ""
        usage = response.usage
        return ChatResult(
            content=content,
            model=self.model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        redacted = [self._maybe_redact(text) for text in texts]
        response = await retry_provider_call(
            lambda: self.client.embeddings.create(model=self.embedding_model, input=redacted),
            attempts=self.retry_attempts,
            delay=self.retry_backoff_seconds,
        )
        vectors = [item.embedding for item in response.data]
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    def _maybe_redact(self, text: str) -> str:
        if not self.redact:
            return text
        return redact_text(text, self.redaction_custom_patterns)


async def retry_provider_call[T](
    call_factory: Callable[[], Awaitable[T]], *, attempts: int = 3, delay: float = 0.5
) -> T:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await call_factory()
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            if delay > 0:
                await asyncio.sleep(delay * (2**attempt))
    assert last_error is not None
    raise last_error
