from __future__ import annotations

from collections.abc import Callable, Sequence
from contextvars import ContextVar

from pzp_ai_bot.ai.credential_pool import (
    CredentialLease,
    CredentialPoolService,
    ProviderCallError,
    ProviderPoolAttemptMetadata,
    ProviderPoolExhaustedError,
)
from pzp_ai_bot.ai.openai_compatible import OpenAICompatibleProvider
from pzp_ai_bot.ai.protocols import ChatMessage, ChatProgressCallback, ChatResult


class PooledOpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        profile_name: str,
        chat_model: str,
        embedding_model: str,
        pool: CredentialPoolService,
        provider_for_lease: Callable[[CredentialLease], OpenAICompatibleProvider],
    ) -> None:
        self.profile_name = profile_name
        self.model = chat_model
        self.embedding_model = embedding_model
        self.dimensions = 1536
        self.pool = pool
        self.provider_for_lease = provider_for_lease
        self._attempt_metadata: ContextVar[ProviderPoolAttemptMetadata | None] = ContextVar(
            f"pzp_provider_attempt_metadata_{id(self)}", default=None
        )

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        try:
            outcome = await self.pool.execute(
                self.profile_name,
                lambda lease: self.provider_for_lease(lease).generate(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                ),
            )
        except (ProviderCallError, ProviderPoolExhaustedError) as error:
            self._attempt_metadata.set(error.metadata)
            raise
        self._attempt_metadata.set(outcome.metadata)
        return outcome.value

    async def generate_stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        on_text: ChatProgressCallback,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        try:
            outcome = await self.pool.execute(
                self.profile_name,
                lambda lease: self.provider_for_lease(lease).generate_stream(
                    messages,
                    on_text=on_text,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                ),
            )
        except (ProviderCallError, ProviderPoolExhaustedError) as error:
            self._attempt_metadata.set(error.metadata)
            raise
        self._attempt_metadata.set(outcome.metadata)
        return outcome.value

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            outcome = await self.pool.execute(
                self.profile_name, lambda lease: self.provider_for_lease(lease).embed(texts)
            )
        except (ProviderCallError, ProviderPoolExhaustedError) as error:
            self._attempt_metadata.set(error.metadata)
            raise
        self._attempt_metadata.set(outcome.metadata)
        vectors = outcome.value
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    def consume_attempt_metadata(self) -> ProviderPoolAttemptMetadata | None:
        metadata = self._attempt_metadata.get()
        self._attempt_metadata.set(None)
        return metadata


__all__ = ["PooledOpenAICompatibleProvider"]
