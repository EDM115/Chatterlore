from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from openai import AsyncOpenAI
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
        reasoning_model = supports_reasoning_effort(self.model)
        request["max_completion_tokens" if reasoning_model else "max_tokens"] = max_tokens
        if reasoning_model:
            if reasoning_effort is not None:
                request["reasoning_effort"] = reasoning_effort
        else:
            request["temperature"] = temperature
        outcome = await self.call_policy.execute(
            self.credential_lease, lambda _lease: self.client.chat.completions.create(**request)
        )
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
        redacted = [self._maybe_redact(text) for text in texts]
        outcome = await self.call_policy.execute(
            self.credential_lease,
            lambda _lease: self.client.embeddings.create(
                model=self.embedding_model, input=redacted
            ),
        )
        response = outcome.value
        vectors = [item.embedding for item in response.data]
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    def _maybe_redact(self, text: str) -> str:
        if not self.redact:
            return text
        return redact_text(text, self.redaction_custom_patterns)


def supports_reasoning_effort(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))
