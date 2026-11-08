from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from typing import Any

from chatterlore.ai.openai_compatible import resolve_api_dialect, supports_reasoning_effort
from chatterlore.config import Settings

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
        if profile.chat_capable and profile.chat_model:
            capabilities.append("chat")
        if profile.embedding_capable and profile.embedding_model:
            capabilities.append("embedding")
        dialect = resolve_api_dialect(
            profile.api_dialect, base_url=profile.base_url, model=profile.chat_model, local=is_local
        )
        if (
            profile.reasoning_capable
            and profile.provider_kind == "openai_compatible"
            and "chat" in capabilities
            and (
                dialect == "deepseek"
                or (dialect == "openai" and supports_reasoning_effort(profile.chat_model))
            )
        ):
            capabilities.append("reasoning")

        local_state: dict[str, bool] | None = None
        pool: dict[str, int] | None = None
        if not profile.enabled:
            availability = "disabled"
        elif is_local and profile.provider_kind == "local":
            chat_dependency = all(
                importlib.util.find_spec(name) is not None for name in ("torch", "transformers")
            )
            embedding_dependency = importlib.util.find_spec("sentence_transformers") is not None
            dependency_available = (not profile.chat_capable or chat_dependency) and (
                not profile.embedding_capable or embedding_dependency
            )
            loaded = self.secrets.registry.local_state(profile.name)
            model_loaded = (not profile.chat_capable or loaded["chat_model_loaded"]) and (
                not profile.embedding_capable or loaded["embedding_model_loaded"]
            )
            local_state = {
                "dependency_available": dependency_available,
                "model_loaded": model_loaded,
                "chat_dependency_available": chat_dependency,
                "embedding_dependency_available": embedding_dependency,
                **loaded,
                "server_configured": False,
            }
            availability = (
                ("ready" if model_loaded else "preparation_required")
                if dependency_available
                else "unavailable"
            )
        elif is_local and profile.auth_mode == "none":
            try:
                self.settings.validate_local_server_url(profile.base_url)
            except ValueError:
                configured = False
            else:
                configured = True
            local_state = {
                "dependency_available": True,
                "model_loaded": False,
                "server_configured": configured,
            }
            availability = "configured" if configured else "unavailable"
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
            "local": is_local,
            "api_dialect": profile.api_dialect,
            "auth_mode": profile.auth_mode,
            "capabilities": capabilities,
            "availability": availability,
            "local_state": local_state,
            "pool": pool,
        }
