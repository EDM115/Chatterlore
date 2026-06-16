from __future__ import annotations

from pzp_ai_bot.ai.fake import FakeProvider
from pzp_ai_bot.ai.local import LocalSentenceTransformerEmbeddingProvider
from pzp_ai_bot.ai.openai_compatible import OpenAICompatibleProvider
from pzp_ai_bot.ai.protocols import ChatProvider, EmbeddingProvider
from pzp_ai_bot.config import Settings


def build_providers(settings: Settings) -> tuple[ChatProvider, EmbeddingProvider]:
    if settings.ai_provider == "fake":
        provider = FakeProvider(
            chat_model=settings.chat_model,
            embedding_model=settings.embedding_model,
            dimensions=settings.fake_embedding_dimensions,
        )
        return provider, provider
    if settings.ai_provider == "openai_compatible":
        provider = OpenAICompatibleProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            chat_model=settings.chat_model,
            embedding_model=settings.embedding_model,
            redact=settings.redact_before_ai,
        )
        return provider, provider
    local = LocalSentenceTransformerEmbeddingProvider(settings.embedding_model)
    return local, local
