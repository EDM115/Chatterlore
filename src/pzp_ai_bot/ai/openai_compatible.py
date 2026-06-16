from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from pzp_ai_bot.ai.protocols import ChatMessage, ChatResult

SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)")


def redact_text(text: str) -> str:
    return SECRET_RE.sub(r"\1=[REDACTED]", text)


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
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key or "missing", base_url=base_url)
        self.model = chat_model
        self.embedding_model = embedding_model
        self.dimensions = 1536
        self.redact = redact

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
        response = await self.client.chat.completions.create(
            model=self.model, messages=payload, temperature=temperature, max_tokens=max_tokens
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
        response = await self.client.embeddings.create(model=self.embedding_model, input=redacted)
        vectors = [item.embedding for item in response.data]
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    def _maybe_redact(self, text: str) -> str:
        if not self.redact:
            return text
        return redact_text(text)


async def retry_provider_call(coro_factory, attempts: int = 3, delay: float = 0.5):
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:  # pragma: no cover - exercised via real providers
            last_error = exc
            if attempt + 1 >= attempts:
                break
            await asyncio.sleep(delay * (2**attempt))
    assert last_error is not None
    raise last_error
