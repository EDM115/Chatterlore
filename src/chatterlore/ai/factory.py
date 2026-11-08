from __future__ import annotations

import asyncio
import inspect
import re
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chatterlore.ai.credential_pool import CredentialLease, CredentialPoolService
from chatterlore.ai.fake import FakeProvider
from chatterlore.ai.openai_compatible import (
    OpenAICompatibleProvider,
    resolve_api_dialect,
    supports_reasoning_effort,
)
from chatterlore.ai.pooled_openai import PooledOpenAICompatibleProvider
from chatterlore.ai.protocols import ChatMessage, ChatProvider, ChatResult, EmbeddingProvider
from chatterlore.ai.provider_call_policy import ProviderCallPolicy
from chatterlore.config import ProviderProfile, Settings


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    profile: ProviderProfile
    chat_provider: ChatProvider
    embedding_provider: EmbeddingProvider
    chat_model: str
    embedding_model: str
    route_epoch: int | None = None
    managed_profile: bool = False
    profile_revision: int = 0


class _UnavailableProvider:
    name = "unavailable"
    model = ""
    dimensions = 0

    def __init__(self, operation: str) -> None:
        self.operation = operation

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        raise ValueError(f"provider profile does not support {self.operation}")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ValueError(f"provider profile does not support {self.operation}")


class LocalModelPreparationRequiredError(ValueError):
    code = "local_model_preparation_required"

    def __init__(self) -> None:
        super().__init__(
            "local_model_preparation_required: prepare and pin local embeddings before inference"
        )


def _embedding_revision_is_pinned(options: dict[str, Any]) -> bool:
    revision = str(options.get("revision") or "")
    tokenizer_revision = str(options.get("tokenizer_revision") or revision)
    return all(
        re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64}|sha256:[0-9a-fA-F]{64})", value)
        is not None
        for value in (revision, tokenizer_revision)
    )


class _ManagedLocalProvider:
    name = "local"

    def __init__(
        self,
        registry: ProviderRegistry,
        create: Callable[[], Any],
        *,
        preparation_required: bool = False,
    ) -> None:
        self.registry = registry
        self.create = create
        self.provider = create()
        self.model = self.provider.model
        self.embedding_model = getattr(self.provider, "embedding_model", "")
        self._active = 0
        self._closed = False
        self._idle = asyncio.Event()
        self._idle.set()
        self._prepared_kwargs: dict[str, Any] | None = None
        self._needs_prepare = False
        self.preparation_required = preparation_required

    @property
    def dimensions(self) -> int:
        return getattr(self.provider, "dimensions", 0)

    @dimensions.setter
    def dimensions(self, value: int) -> None:
        self.provider.dimensions = value

    @property
    def loaded(self) -> bool:
        return bool(getattr(self.provider, "loaded", False))

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        return await self._call(
            "generate",
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._call("embed", texts)

    async def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._call("embed_query", texts)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._call("embed_documents", texts)

    async def prepare(
        self,
        *,
        allow_download: bool = False,
        model_path: str | Path | None = None,
        tokenizer_path: str | Path | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"allow_download": allow_download}
        if model_path is not None:
            kwargs["model_path"] = model_path
        if tokenizer_path is not None:
            kwargs["tokenizer_path"] = tokenizer_path
        await self._call("prepare", **kwargs)
        self._prepared_kwargs = kwargs
        self._needs_prepare = False

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if method != "prepare" and self.preparation_required:
            raise LocalModelPreparationRequiredError()
        await self.registry._acquire_local(self)
        try:
            if method != "prepare" and self._needs_prepare and self._prepared_kwargs is not None:
                await self.provider.prepare(**self._prepared_kwargs)
                self._needs_prepare = False
            return await getattr(self.provider, method)(*args, **kwargs)
        finally:
            self._active -= 1
            if self._active == 0:
                self._idle.set()

    async def aclose(self) -> None:
        self._closed = True
        await self._idle.wait()
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result


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
        self._managed_capabilities: dict[str, frozenset[str]] = {}
        self._credential_pool: CredentialPoolService | None = None
        self._disabled_profiles: set[str] = set()
        self._retired: list[Any] = []
        self._retirement_tasks: set[asyncio.Task[None]] = set()
        self._local_residents: OrderedDict[_ManagedLocalProvider, None] = OrderedDict()
        self._local_lock = asyncio.Lock()

    def configure_credential_pool(self, pool: CredentialPoolService) -> None:
        self._credential_pool = pool
        self.invalidate()

    def replace_managed_profiles(
        self,
        profiles: dict[str, ProviderProfile],
        *,
        revisions: dict[str, int] | None = None,
        capabilities: dict[str, frozenset[str]] | None = None,
        disabled: set[str] | None = None,
    ) -> None:
        new_profiles = dict(profiles)
        new_revisions = dict(revisions or {})
        new_capabilities = dict(capabilities or {})
        new_disabled = set(disabled or ())
        names = (
            set(self._managed_profiles) | set(new_profiles) | self._disabled_profiles | new_disabled
        )
        changed = {
            name
            for name in names
            if self._managed_profiles.get(name) != new_profiles.get(name)
            or self._managed_revisions.get(name) != new_revisions.get(name)
            or self._managed_capabilities.get(name) != new_capabilities.get(name)
            or (name in self._disabled_profiles) != (name in new_disabled)
        }
        for name in changed:
            self.invalidate(name)
        self._managed_profiles = new_profiles
        self._managed_revisions = new_revisions
        self._managed_capabilities = new_capabilities
        self._disabled_profiles = new_disabled

    def invalidate(self, profile_name: str | None = None) -> None:
        retired: dict[int, Any] = {}
        for key, selection in self._cache.items():
            if profile_name is None or key[0] == profile_name.strip().lower():
                for provider in (selection.chat_provider, selection.embedding_provider):
                    retired[id(provider)] = provider
        for key, provider in self._managed_client_cache.items():
            if profile_name is None or key[0] == profile_name.strip().lower():
                retired[id(provider)] = provider
        for provider in retired.values():
            self._retire(provider)
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
        if selected in self._disabled_profiles:
            raise ValueError(f"provider profile {selected!r} is disabled")
        if selected in self._managed_profiles:
            return self._managed_profiles[selected]
        return self.settings.provider_profile_by_name(selected)

    def is_managed(self, name: str | None) -> bool:
        selected = (name or self.settings.ai_provider_profile or "default").strip().lower()
        return selected in self._managed_profiles or selected in self._disabled_profiles

    def profile_capabilities(self, name: str | None) -> frozenset[str]:
        profile = self.profile_by_name(name)
        managed = self._managed_capabilities.get(profile.name)
        if managed is not None:
            return managed
        capabilities: set[str] = set()
        if profile.chat_capable and profile.chat_model:
            capabilities.add("chat")
        if profile.embedding_capable and profile.embedding_model:
            capabilities.add("embedding")
        dialect = resolve_api_dialect(
            profile.api_dialect,
            base_url=profile.openai_base_url,
            model=profile.chat_model,
            local=profile.local,
        )
        if (
            profile.provider == "openai_compatible"
            and profile.reasoning_capable
            and "chat" in capabilities
            and (
                dialect == "deepseek"
                or (dialect == "openai" and supports_reasoning_effort(profile.chat_model))
            )
        ):
            capabilities.add("reasoning")
        return frozenset(capabilities)

    def profile_models(self) -> dict[str, tuple[str, str]]:
        profiles = {**self.settings.provider_profiles, **self._managed_profiles}
        return {
            name: (profile.chat_model, profile.embedding_model)
            for name, profile in profiles.items()
            if name not in self._disabled_profiles
        }

    def select(
        self,
        profile_name: str | None = None,
        *,
        chat_model: str | None = None,
        embedding_model: str | None = None,
    ) -> ProviderSelection:
        profile = self.profile_by_name(profile_name)
        clean_chat_model = (
            self.settings.validate_chat_model(chat_model or profile.chat_model)
            if profile.chat_capable
            else ""
        )
        clean_embedding_model = (
            self.settings.validate_embedding_model(embedding_model or profile.embedding_model)
            if profile.embedding_capable
            else ""
        )
        revision = self._managed_revisions.get(profile.name, 0)
        key = (profile.name, clean_chat_model, clean_embedding_model, revision)
        if key not in self._cache:
            selection = self._build(profile, clean_chat_model, clean_embedding_model)
            self._cache[key] = ProviderSelection(
                selection.profile,
                selection.chat_provider if profile.chat_capable else _UnavailableProvider("chat"),
                selection.embedding_provider
                if profile.embedding_capable
                else _UnavailableProvider("embedding"),
                clean_chat_model,
                clean_embedding_model,
                managed_profile=profile.name in self._managed_profiles,
                profile_revision=revision,
            )
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
            if profile.local:
                self.settings.validate_local_server_url(profile.openai_base_url)
            if profile.auth_mode == "none" and not profile.local:
                raise ValueError("auth_mode=none requires an explicitly local provider")
            if (
                profile.name in self._managed_profiles
                and self._credential_pool is not None
                and profile.auth_mode != "none"
            ):
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
            if not profile.openai_api_key and profile.auth_mode != "none":
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
                api_dialect=profile.api_dialect,
                auth_mode=profile.auth_mode,
                local=profile.local,
                reasoning_capable=profile.reasoning_capable,
                client=(
                    self.openai_client_factory(
                        profile.openai_api_key
                        if profile.auth_mode == "api_key"
                        else "local-no-auth",
                        profile.openai_base_url,
                    )
                    if self.openai_client_factory is not None
                    else None
                ),
            )
            return ProviderSelection(profile, provider, provider, chat_model, embedding_model)
        from chatterlore.ai.local import (
            LocalSentenceTransformerEmbeddingProvider,
            LocalTransformersChatProvider,
        )
        from chatterlore.local_models import LocalModelOptions

        chat: Any = _UnavailableProvider("chat")
        embedding: Any = _UnavailableProvider("embedding")
        if profile.chat_capable:
            self.settings.validate_local_paths(chat_model, profile.local_chat_options)
            chat = _ManagedLocalProvider(
                self,
                lambda: LocalTransformersChatProvider(
                    chat_model,
                    options=LocalModelOptions.model_validate(
                        self.settings.local_runtime_options(profile.local_chat_options)
                    ),
                ),
            )
        if profile.embedding_capable:
            self.settings.validate_local_paths(embedding_model, profile.local_embedding_options)
            embedding = _ManagedLocalProvider(
                self,
                lambda: LocalSentenceTransformerEmbeddingProvider(
                    embedding_model,
                    options=LocalModelOptions.model_validate(
                        self.settings.local_runtime_options(profile.local_embedding_options)
                    ),
                ),
                preparation_required=not _embedding_revision_is_pinned(
                    profile.local_embedding_options
                ),
            )
        return ProviderSelection(profile, chat, embedding, chat_model, embedding_model)

    async def _acquire_local(self, provider: _ManagedLocalProvider) -> None:
        async with self._local_lock:
            if provider._closed:
                raise RuntimeError("local provider has been retired, resolve the route again")
            if provider not in self._local_residents:
                if len(self._local_residents) >= self.settings.local_max_loaded_models:
                    idle = next(
                        (
                            item
                            for item in self._local_residents
                            if item._active == 0 and not getattr(item.provider, "busy", False)
                        ),
                        None,
                    )
                    if idle is None:
                        raise RuntimeError(
                            "local model resident limit reached while all models are busy"
                        )
                    self._local_residents.pop(idle)
                    close = getattr(idle.provider, "aclose", None)
                    if close is not None:
                        result = close()
                        if inspect.isawaitable(result):
                            await result
                    idle.provider = idle.create()
                    idle._needs_prepare = idle._prepared_kwargs is not None
                self._local_residents[provider] = None
            self._local_residents.move_to_end(provider)
            provider._active += 1
            provider._idle.clear()

    def _retire(self, provider: Any) -> None:
        if not hasattr(provider, "aclose"):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._retired.append(provider)
            return
        task = loop.create_task(provider.aclose())
        self._retirement_tasks.add(task)
        task.add_done_callback(self._retirement_tasks.discard)

    async def aclose(self) -> None:
        self.invalidate()
        retired, self._retired = self._retired, []
        for provider in retired:
            await provider.aclose()
        if self._retirement_tasks:
            await asyncio.gather(*self._retirement_tasks)
        self._local_residents.clear()

    def local_state(self, name: str) -> dict[str, bool]:
        selections = [selection for key, selection in self._cache.items() if key[0] == name]
        return {
            "chat_model_loaded": any(
                bool(getattr(item.chat_provider, "loaded", False)) for item in selections
            ),
            "embedding_model_loaded": any(
                bool(getattr(item.embedding_provider, "loaded", False)) for item in selections
            ),
        }

    def _managed_provider_for_lease(
        self,
        profile: ProviderProfile,
        lease: CredentialLease,
        chat_model: str,
        embedding_model: str,
    ) -> OpenAICompatibleProvider:
        if (
            self._managed_profiles.get(profile.name) != profile
            or self._managed_revisions.get(profile.name, 0) != lease.profile_revision
        ):
            raise ValueError(
                "provider profile changed, resolve the route again before using credentials"
            )
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
        if lease.source == "environment" and (
            not profile.openai_api_key or profile.openai_api_key != secret
        ):
            raise ValueError("environment provider credential is not authorized for this endpoint")
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
            api_dialect=profile.api_dialect,
            auth_mode=profile.auth_mode,
            local=profile.local,
            reasoning_capable=profile.reasoning_capable,
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
            vectors = await provider.embed(["chatterlore credential connectivity check"])
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
