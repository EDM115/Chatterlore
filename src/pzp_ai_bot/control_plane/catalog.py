from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from typing import Any

from pzp_ai_bot.config import Settings

from .secrets.service import ProviderProfileSummary, ProviderSecretService


class ProviderCapabilityCatalog:
    def __init__(self, settings: Settings, secrets: ProviderSecretService) -> None:
        self.settings = settings
        self.secrets = secrets

    async def for_scope(self, scope_key: str) -> dict[str, Any]:
        profiles = await self.secrets.list_profiles()
        chat_models = self.settings.allowed_chat_model_names or {
            profile.chat_model for profile in profiles if profile.chat_capable
        }
        embedding_models = self.settings.allowed_embedding_model_names or {
            profile.embedding_model for profile in profiles if profile.embedding_capable
        }
        return {
            "scope": scope_key,
            "chat_models": sorted(chat_models),
            "embedding_models": sorted(embedding_models),
            "reasoning_levels": sorted(self.settings.allowed_reasoning_level_names),
            "profiles": [await self._profile_payload(profile) for profile in profiles],
        }

    async def _profile_payload(self, profile: ProviderProfileSummary) -> dict[str, Any]:
        is_local = profile.local or profile.provider_kind == "local"
        capabilities: list[str] = []
        if profile.chat_capable and not is_local:
            capabilities.append("chat")
        if profile.embedding_capable:
            capabilities.append("embedding")
        if profile.reasoning_capable and "chat" in capabilities:
            capabilities.append("reasoning")

        local_state: dict[str, bool] | None = None
        pool: dict[str, int] | None = None
        if not profile.enabled:
            availability = "disabled"
        elif is_local:
            dependency_available = importlib.util.find_spec("sentence_transformers") is not None
            model_loaded = False
            if dependency_available:
                try:
                    selection = self.secrets.registry.select(
                        profile.name,
                        chat_model=profile.chat_model,
                        embedding_model=profile.embedding_model,
                    )
                    model_loaded = bool(getattr(selection.embedding_provider, "loaded", False))
                except ValueError:
                    dependency_available = False
            local_state = {
                "dependency_available": dependency_available,
                "model_loaded": model_loaded,
            }
            availability = "ready" if dependency_available else "unavailable"
        elif profile.provider_kind == "fake":
            availability = "ready"
        else:
            credentials = await self.secrets.list_credentials(profile.name)
            now = datetime.now(UTC)
            eligible = sum(
                1
                for item in credentials
                if item.state == "enabled"
                and item.health_state != "disabled"
                and (item.cooldown_until is None or item.cooldown_until <= now)
            )
            pool = {"eligible": eligible, "total": len(credentials)}
            availability = "ready" if eligible else "unavailable"

        return {
            "name": profile.name,
            "provider_kind": profile.provider_kind,
            "source": profile.source,
            "enabled": profile.enabled,
            "chat_model": profile.chat_model,
            "embedding_model": profile.embedding_model,
            "capabilities": capabilities,
            "availability": availability,
            "local_state": local_state,
            "pool": pool,
        }
