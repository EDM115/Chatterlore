from __future__ import annotations

from dataclasses import dataclass

from pzp_ai_bot.ai.fake import FakeProvider
from pzp_ai_bot.ai.local import LocalSentenceTransformerEmbeddingProvider
from pzp_ai_bot.ai.openai_compatible import OpenAICompatibleProvider
from pzp_ai_bot.ai.protocols import ChatProvider, EmbeddingProvider
from pzp_ai_bot.config import ProviderProfile, Settings


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    profile: ProviderProfile
    chat_provider: ChatProvider
    embedding_provider: EmbeddingProvider
    chat_model: str
    embedding_model: str


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[tuple[str, str | None, str | None], ProviderSelection] = {}

    def select(
        self,
        profile_name: str | None = None,
        *,
        chat_model: str | None = None,
        embedding_model: str | None = None,
    ) -> ProviderSelection:
        profile = self.settings.provider_profile_by_name(profile_name)
        clean_chat_model = self.settings.validate_chat_model(chat_model or profile.chat_model)
        clean_embedding_model = self.settings.validate_embedding_model(
            embedding_model or profile.embedding_model
        )
        key = (profile.name, clean_chat_model, clean_embedding_model)
        if key not in self._cache:
            self._cache[key] = self._build(profile, clean_chat_model, clean_embedding_model)
        return self._cache[key]

    def _build(
        self, profile: ProviderProfile, chat_model: str, embedding_model: str
    ) -> ProviderSelection:
        if profile.provider == "fake":
            provider = FakeProvider(
                chat_model=chat_model,
                embedding_model=embedding_model,
                dimensions=profile.fake_embedding_dimensions,
            )
            return ProviderSelection(profile, provider, provider, chat_model, embedding_model)
        if profile.provider == "openai_compatible":
            if not profile.openai_api_key:
                raise ValueError(
                    f"provider profile {profile.name!r} requires an API key. "
                    "Set OPENAI_API_KEY or configure openai_api_key/api_key_env "
                    "in AI_PROVIDER_PROFILES."
                )
            provider = OpenAICompatibleProvider(
                api_key=profile.openai_api_key,
                base_url=profile.openai_base_url,
                chat_model=chat_model,
                embedding_model=embedding_model,
                redact=profile.redact_before_ai,
                redaction_custom_patterns=profile.redaction_custom_patterns,
                retry_attempts=self.settings.provider_retry_attempts,
                retry_backoff_seconds=self.settings.provider_retry_backoff_seconds,
            )
            return ProviderSelection(profile, provider, provider, chat_model, embedding_model)
        local = LocalSentenceTransformerEmbeddingProvider(embedding_model)
        return ProviderSelection(profile, local, local, chat_model, embedding_model)


def build_providers(settings: Settings) -> tuple[ChatProvider, EmbeddingProvider]:
    selection = ProviderRegistry(settings).select(settings.ai_provider_profile)
    return selection.chat_provider, selection.embedding_provider
