from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from openai.types.chat import ChatCompletionMessageParam

from pzp_ai_bot.ai.protocols import ChatMessage, ChatResult
from pzp_ai_bot.ai.provider_call_policy import OpaqueCredentialLease, ProviderCallPolicy
from pzp_ai_bot.ai.redaction import redact_text


@dataclass(frozen=True, slots=True)
class _BootstrapCredentialLease:
    profile_revision: int = 0
    label: str = "bootstrap"
    fingerprint: str = ""


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
        credential_lease: OpaqueCredentialLease | None = None,
        call_policy: ProviderCallPolicy | None = None,
        api_dialect: str = "auto",
        auth_mode: str = "api_key",
        local: bool = False,
        reasoning_capable: bool = True,
    ) -> None:
        if auth_mode not in {"api_key", "none"}:
            raise ValueError("provider auth mode is invalid")
        if auth_mode == "none" and not local:
            raise ValueError("auth_mode=none requires an explicitly local provider")
        if not api_key and auth_mode != "none":
            raise ValueError("OpenAI-compatible provider requires an API key")
        self.client = client or AsyncOpenAI(
            api_key=api_key if auth_mode == "api_key" else "local-no-auth",
            base_url=base_url,
            http_client=DefaultAsyncHttpxClient(follow_redirects=False),
            max_retries=0,
            timeout=60.0,
        )
        self.api_dialect = resolve_api_dialect(
            api_dialect, base_url=base_url, model=chat_model, local=local
        )
        self.reasoning_capable = reasoning_capable
        self._closed = False
        self._inflight = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self.model = chat_model
        self.embedding_model = embedding_model
        self.dimensions = 1536
        self.redact = redact
        self.redaction_custom_patterns = tuple(redaction_custom_patterns)
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.credential_lease = credential_lease or _BootstrapCredentialLease()
        self.call_policy = call_policy or ProviderCallPolicy(
            attempts=retry_attempts, backoff_seconds=retry_backoff_seconds
        )

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        payload = [
            cast(
                ChatCompletionMessageParam,
                {"role": message.role, "content": self._maybe_redact(message.content)},
            )
            for message in messages
        ]
        request: dict[str, Any] = {"model": self.model, "messages": payload}
        if not self.model:
            raise ValueError("chat model is not configured")
        reasoning_model = self.api_dialect == "openai" and supports_reasoning_effort(self.model)
        request["max_completion_tokens" if reasoning_model else "max_tokens"] = max_tokens
        if self.api_dialect == "deepseek":
            effective_effort = reasoning_effort if self.reasoning_capable else None
            request["extra_body"] = {
                "thinking": {"type": "enabled" if effective_effort else "disabled"}
            }
            if effective_effort is not None:
                if effective_effort not in {"low", "medium", "high"}:
                    raise ValueError("unsupported DeepSeek reasoning effort")
                request["reasoning_effort"] = (
                    "high" if effective_effort == "medium" else effective_effort
                )
            else:
                request["temperature"] = temperature
        elif reasoning_model:
            if reasoning_effort is not None and self.reasoning_capable:
                request["reasoning_effort"] = reasoning_effort
        else:
            request["temperature"] = temperature
        outcome = await self._execute(lambda: self.client.chat.completions.create(**request))
        response = outcome.value
        content = response.choices[0].message.content or ""
        usage = response.usage
        return ChatResult(
            content=content,
            model=self.model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.embedding_model:
            raise ValueError("embedding model is not configured")
        redacted = [self._maybe_redact(text) for text in texts]
        outcome = await self._execute(
            lambda: self.client.embeddings.create(model=self.embedding_model, input=redacted)
        )
        response = outcome.value
        vectors = [item.embedding for item in response.data]
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    async def _execute(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        if self._closed:
            raise RuntimeError("provider has been closed")
        self._inflight += 1
        self._idle.clear()
        try:
            return await self.call_policy.execute(self.credential_lease, lambda _lease: operation())
        finally:
            self._inflight -= 1
            if self._inflight == 0:
                self._idle.set()

    async def aclose(self) -> None:
        self._closed = True
        await self._idle.wait()
        close = getattr(self.client, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    def _maybe_redact(self, text: str) -> str:
        if not self.redact:
            return text
        return redact_text(text, self.redaction_custom_patterns)


def supports_reasoning_effort(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


def resolve_api_dialect(dialect: str, *, base_url: str, model: str, local: bool = False) -> str:
    if dialect not in {"auto", "openai", "deepseek", "generic"}:
        raise ValueError("provider API dialect is invalid")
    if dialect != "auto":
        return dialect
    if local:
        return "generic"
    hostname = (urlsplit(base_url).hostname or "").lower()
    if hostname == "api.deepseek.com":
        return "deepseek"
    if hostname == "api.openai.com" or supports_reasoning_effort(model):
        return "openai"
    return "generic"
