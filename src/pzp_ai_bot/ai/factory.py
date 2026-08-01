from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pzp_ai_bot.ai.credential_pool import CredentialLease, CredentialPoolService
from pzp_ai_bot.ai.fake import FakeProvider
from pzp_ai_bot.ai.local import LocalSentenceTransformerEmbeddingProvider
from pzp_ai_bot.ai.openai_compatible import OpenAICompatibleProvider
from pzp_ai_bot.ai.pooled_openai import PooledOpenAICompatibleProvider
from pzp_ai_bot.ai.protocols import ChatMessage, ChatProvider, EmbeddingProvider
from pzp_ai_bot.ai.provider_call_policy import ProviderCallPolicy
from pzp_ai_bot.config import ProviderProfile, Settings


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    profile: ProviderProfile
    chat_provider: ChatProvider
    embedding_provider: EmbeddingProvider
    chat_model: str
    embedding_model: str


class ProviderRegistry:
    def __init__(
        self, settings: Settings, *, openai_client_factory: Callable[[str, str], Any] | None = None
    ) -> None:
        self.settings = settings
        self.openai_client_factory = openai_client_factory
        self._cache: dict[tuple[str, str, str, int], ProviderSelection] = {}
        self._managed_client_cache: dict[
            tuple[str, int, int, int, str, str], OpenAICompatibleProvider
        ] = {}
        self._managed_profiles: dict[str, ProviderProfile] = {}
        self._managed_revisions: dict[str, int] = {}
        self._credential_pool: CredentialPoolService | None = None

    def configure_credential_pool(self, pool: CredentialPoolService) -> None:
        self._credential_pool = pool
        self.invalidate()

    def replace_managed_profiles(
        self, profiles: dict[str, ProviderProfile], *, revisions: dict[str, int] | None = None
    ) -> None:
        self._managed_profiles = dict(profiles)
        self._managed_revisions = dict(revisions or {})
        self.invalidate()

    def invalidate(self, profile_name: str | None = None) -> None:
        if profile_name is None:
            self._cache.clear()
            self._managed_client_cache.clear()
            return
        clean = profile_name.strip().lower()
        self._cache = {key: selection for key, selection in self._cache.items() if key[0] != clean}
        self._managed_client_cache = {
            key: provider for key, provider in self._managed_client_cache.items() if key[0] != clean
        }

    def profile_by_name(self, name: str | None) -> ProviderProfile:
        selected = (name or self.settings.ai_provider_profile or "default").strip().lower()
        if selected in self._managed_profiles:
            return self._managed_profiles[selected]
        return self.settings.provider_profile_by_name(selected)

    def select(
        self,
        profile_name: str | None = None,
        *,
        chat_model: str | None = None,
        embedding_model: str | None = None,
    ) -> ProviderSelection:
        profile = self.profile_by_name(profile_name)
        clean_chat_model = self.settings.validate_chat_model(chat_model or profile.chat_model)
        clean_embedding_model = self.settings.validate_embedding_model(
            embedding_model or profile.embedding_model
        )
        revision = self._managed_revisions.get(profile.name, 0)
        key = (profile.name, clean_chat_model, clean_embedding_model, revision)
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
            if profile.name in self._managed_profiles and self._credential_pool is not None:
                provider = PooledOpenAICompatibleProvider(
                    profile_name=profile.name,
                    chat_model=chat_model,
                    embedding_model=embedding_model,
                    pool=self._credential_pool,
                    provider_for_lease=lambda lease: self._managed_provider_for_lease(
                        profile, lease, chat_model, embedding_model
                    ),
                )
                return ProviderSelection(profile, provider, provider, chat_model, embedding_model)
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
                client=(
                    self.openai_client_factory(profile.openai_api_key, profile.openai_base_url)
                    if self.openai_client_factory is not None
                    else None
                ),
            )
            return ProviderSelection(profile, provider, provider, chat_model, embedding_model)
        local = LocalSentenceTransformerEmbeddingProvider(embedding_model)
        return ProviderSelection(profile, local, local, chat_model, embedding_model)

    def _managed_provider_for_lease(
        self,
        profile: ProviderProfile,
        lease: CredentialLease,
        chat_model: str,
        embedding_model: str,
    ) -> OpenAICompatibleProvider:
        key = (
            profile.name,
            lease.profile_revision,
            lease.credential_id,
            lease.credential_revision,
            chat_model,
            embedding_model,
        )
        provider = self._managed_client_cache.get(key)
        if provider is not None:
            return provider
        secret = lease.secret_value()
        provider = OpenAICompatibleProvider(
            api_key=secret,
            base_url=profile.openai_base_url,
            chat_model=chat_model,
            embedding_model=embedding_model,
            redact=profile.redact_before_ai,
            redaction_custom_patterns=profile.redaction_custom_patterns,
            retry_attempts=1,
            retry_backoff_seconds=0,
            client=(
                self.openai_client_factory(secret, profile.openai_base_url)
                if self.openai_client_factory is not None
                else None
            ),
            credential_lease=lease,
            call_policy=ProviderCallPolicy(attempts=1, backoff_seconds=0),
        )
        self._managed_client_cache[key] = provider
        return provider

    async def test_managed_credential(
        self, profile_name: str, credential_id: int, *, mode: str
    ) -> dict[str, Any]:
        profile = self.profile_by_name(profile_name)
        pool = self._credential_pool
        if profile.name not in self._managed_profiles or pool is None:
            raise ValueError("managed provider profile is unavailable")
        clean_mode = mode.strip().lower()
        if clean_mode not in {"chat", "embedding"}:
            raise ValueError("provider credential test mode is invalid")

        async def run(lease: CredentialLease) -> dict[str, Any]:
            provider = self._managed_provider_for_lease(
                profile, lease, profile.chat_model, profile.embedding_model
            )
            if clean_mode == "chat":
                await provider.generate(
                    [ChatMessage(role="user", content="Reply with exactly OK.")],
                    max_tokens=8,
                    reasoning_effort="medium",
                )
                return {"model": profile.chat_model, "dimensions": None}
            vectors = await provider.embed(["pzp-ai credential connectivity check"])
            return {
                "model": profile.embedding_model,
                "dimensions": len(vectors[0]) if vectors else None,
            }

        outcome = await pool.execute_credential(profile.name, credential_id, run)
        return {
            "ok": True,
            "mode": clean_mode,
            **outcome.value,
            **outcome.metadata.as_dict(),
            "warning": (
                "This manual check may incur provider usage charges."
                if clean_mode == "chat"
                else "This manual embedding check may incur provider usage charges."
            ),
        }


def build_providers(settings: Settings) -> tuple[ChatProvider, EmbeddingProvider]:
    selection = ProviderRegistry(settings).select(settings.ai_provider_profile)
    return selection.chat_provider, selection.embedding_provider
