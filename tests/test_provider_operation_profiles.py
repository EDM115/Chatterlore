from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any, cast

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI

from chatterlore.ai.credential_pool import CredentialLease, CredentialPoolService
from chatterlore.ai.factory import ProviderRegistry, ProviderSelection
from chatterlore.ai.openai_compatible import OpenAICompatibleProvider
from chatterlore.ai.protocols import ChatMessage, ChatResult
from chatterlore.ai.provider_call_policy import ProviderLeaseCallError
from chatterlore.config import Settings
from chatterlore.control_plane.schemas import ProviderProfileCreateRequest
from chatterlore.control_plane.secrets.crypto import SecretVaultError
from chatterlore.control_plane.secrets.service import ProviderSecretService
from chatterlore.db.models import EmbeddingIndexJob, EmbeddingSpace
from chatterlore.db.repos.embedding_indexes import EmbeddingIndexRepository
from chatterlore.db.session import session_scope
from chatterlore.memory.provider_operations import ProviderOperations


def settings_with_profile(profile: dict, **kwargs) -> Settings:
    return Settings(_env_file=None, ai_provider_profiles=json.dumps({"route": profile}), **kwargs)


def test_profile_endpoint_and_no_auth_never_inherit_default_remote_key() -> None:
    settings = settings_with_profile(
        {"provider": "openai_compatible", "base_url": "https://api.deepseek.com/v1"},
        openai_api_key="remote-bootstrap-secret",
    )
    assert settings.provider_profile_by_name("route").openai_api_key == ""
    local = settings_with_profile(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "local": True,
            "auth_mode": "none",
        },
        openai_api_key="remote-bootstrap-secret",
    ).provider_profile_by_name("route")
    assert local.openai_api_key == ""
    assert local.openai_api_key_env is None


def test_profile_keeps_disabled_empty_model_and_typed_local_options() -> None:
    profile = settings_with_profile(
        {
            "provider": "local",
            "chat_model": "owner/tiny-model",
            "embedding_model": "",
            "embedding_capable": False,
            "local_chat_options": {"local_files_only": True, "max_new_tokens": 32},
        }
    ).provider_profile_by_name("route")
    assert profile.embedding_model == ""
    assert profile.embedding_capable is False
    assert profile.local_chat_options["max_new_tokens"] == 32
    payload = asdict(profile)
    assert payload["api_dialect"] == "auto"
    assert payload["auth_mode"] == "api_key"


def test_chat_only_profile_does_not_require_embedding_model() -> None:
    registry = ProviderRegistry(
        settings_with_profile(
            {
                "provider": "fake",
                "chat_model": "fake-chat-only",
                "embedding_model": "",
                "embedding_capable": False,
            }
        )
    )
    selection = registry.select("route")
    assert selection.chat_model == "fake-chat-only"
    assert registry.profile_capabilities("route") == frozenset({"chat"})


def test_managed_metadata_refresh_preserves_unchanged_provider_instances() -> None:
    settings = settings_with_profile(
        {"provider": "fake", "chat_model": "one", "embedding_model": "embedding"}
    )
    profile = settings.provider_profile_by_name("route")
    registry = ProviderRegistry(settings)
    registry.replace_managed_profiles(
        {"route": profile},
        revisions={"route": 1},
        capabilities={"route": frozenset({"chat", "embedding"})},
    )
    selection = registry.select("route")
    registry.replace_managed_profiles(
        {"route": replace(profile)},
        revisions={"route": 1},
        capabilities={"route": frozenset({"chat", "embedding"})},
    )
    assert registry.select("route") is selection
    registry.replace_managed_profiles(
        {"route": profile, "other": replace(profile, name="other")},
        revisions={"route": 1, "other": 1},
        capabilities={
            "route": frozenset({"chat", "embedding"}),
            "other": frozenset({"chat", "embedding"}),
        },
    )
    assert registry.select("route") is selection


@pytest.mark.parametrize(
    ("dialect", "model", "effort", "expected"),
    [
        (
            "openai",
            "gpt-6.1-sol",
            "medium",
            {"max_completion_tokens": 17, "reasoning_effort": "medium"},
        ),
        (
            "deepseek",
            "deepseek-flash",
            "medium",
            {"max_tokens": 17, "thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        ),
        (
            "deepseek",
            "deepseek-flash",
            "low",
            {"max_tokens": 17, "thinking": {"type": "enabled"}, "reasoning_effort": "low"},
        ),
        (
            "deepseek",
            "deepseek-flash",
            "high",
            {"max_tokens": 17, "thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        ),
        ("generic", "gpt-6-local", "medium", {"max_tokens": 17, "temperature": 0.2}),
        ("openai", "gpt-4.1", "high", {"max_tokens": 17, "temperature": 0.2}),
    ],
)
async def test_dialect_serializes_real_sdk_http_contract(dialect, model, effort, expected) -> None:
    bodies: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 1,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport,
        AsyncOpenAI(
            api_key="test-key", base_url="https://provider.test/v1", http_client=transport
        ) as client,
    ):
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://provider.test/v1",
            chat_model=model,
            embedding_model="",
            api_dialect=dialect,
            client=client,
        )
        profile = settings_with_profile(
            {
                "provider": "openai_compatible",
                "chat_model": model,
                "embedding_model": "",
                "embedding_capable": False,
                "api_dialect": dialect,
            }
        ).provider_profile_by_name("route")
        execution = await ProviderOperations().execute_chat(
            ProviderSelection(profile, provider, provider, model, ""),
            [ChatMessage(role="user", content="hello")],
            operation="ask",
            metadata={},
            temperature=0.2,
            max_tokens=17,
            reasoning_effort=effort,
        )
    assert execution.result is not None
    assert execution.result.content == "ok"
    telemetry = dict(execution.telemetry.metadata)
    assert telemetry["requested_reasoning_level"] == effort
    assert telemetry["effective_reasoning_level"] == expected.get("reasoning_effort")
    assert bodies == [
        {"model": model, "messages": [{"role": "user", "content": "hello"}], **expected}
    ]


async def test_no_auth_local_server_uses_transport_placeholder_not_remote_secret() -> None:
    observed: list[tuple[str, str]] = []
    settings = settings_with_profile(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "local": True,
            "auth_mode": "none",
            "chat_model": "gpt-6-local",
            "embedding_model": "",
            "embedding_capable": False,
        },
        openai_api_key="remote-bootstrap-secret",
        local_server_origins="http://localhost:11434",
    )
    registry = ProviderRegistry(
        settings, openai_client_factory=lambda key, url: observed.append((key, url)) or object()
    )
    selection = registry.select("route")
    assert selection.profile.auth_mode == "none"
    assert observed == [("local-no-auth", "http://localhost:11434/v1")]
    await registry.aclose()


def test_local_server_origin_must_be_explicitly_allowlisted() -> None:
    registry = ProviderRegistry(
        settings_with_profile(
            {
                "provider": "openai_compatible",
                "base_url": "http://untrusted.test/v1",
                "local": True,
                "auth_mode": "none",
            },
            local_server_origins="http://localhost:11434",
        )
    )
    with pytest.raises(ValueError, match="allowlist"):
        registry.select("route")


def test_default_local_cache_cannot_be_bypassed_with_null_option(tmp_path) -> None:
    settings = Settings(_env_file=None, memory_dir=tmp_path / "memory")
    assert settings.local_runtime_options({"cache_dir": None})["cache_dir"] == str(
        (tmp_path / "models" / "cache").resolve()
    )


@pytest.mark.parametrize(
    "origin", ["http://localhost:11434/v1", "http://localhost:11434/#fragment"]
)
def test_local_server_allowlist_requires_bare_origins(origin) -> None:
    settings = Settings(_env_file=None, local_server_origins=origin)
    with pytest.raises(ValueError, match="origin"):
        settings.validate_local_server_url("http://localhost:11434/v1")


async def test_managed_profile_roundtrip_preserves_operation_configuration(context) -> None:
    secret_settings = context.settings.model_copy(
        update={
            "control_plane_master_keys": json.dumps(
                {"test": base64.b64encode(bytes([7]) * 32).decode()}
            ),
            "control_plane_active_master_key_id": "test",
        }
    )
    service = ProviderSecretService(
        secret_settings, context.session_factory, context.memory.provider_registry
    )
    request = ProviderProfileCreateRequest(
        name="embedded",
        provider_kind="local",
        base_url="http://localhost/v1",
        chat_model="owner/tiny-model",
        embedding_model="",
        embedding_capable=False,
        local=True,
        api_dialect="generic",
        auth_mode="none",
        local_chat_options={"max_new_tokens": 32, "local_files_only": True},
    )
    created = await service.create_profile(
        request.model_dump(), actor_user_id=111, now=datetime.now(UTC)
    )
    assert created.as_dict()["local_chat_options"]["max_new_tokens"] == 32
    assert service.registry.profile_capabilities("embedded") == frozenset({"chat"})
    updated = await service.update_profile(
        "embedded",
        expected_version=created.version,
        updates={"local_chat_options": {"max_new_tokens": 16}, "api_dialect": "auto"},
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    assert updated.local_chat_options["max_new_tokens"] == 16
    assert service.registry.profile_by_name("embedded").api_dialect == "auto"


def secret_service(context) -> ProviderSecretService:
    settings = context.settings.model_copy(
        update={
            "control_plane_master_keys": json.dumps(
                {"test": base64.b64encode(bytes([7]) * 32).decode()}
            ),
            "control_plane_active_master_key_id": "test",
        }
    )
    return ProviderSecretService(
        settings, context.session_factory, context.memory.provider_registry
    )


@pytest.mark.parametrize(
    "action", ["delete", "disable", "embedding_model", "local_embedding_options"]
)
async def test_embedding_space_reference_blocks_destructive_profile_changes(
    context, action
) -> None:
    service = secret_service(context)
    created = await service.create_profile(
        ProviderProfileCreateRequest(
            name="protected",
            provider_kind="fake",
            base_url="https://provider.test/v1",
            chat_model="fake-chat",
            embedding_model="fake-embedding",
        ).model_dump(),
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    async with session_scope(context.session_factory) as session:
        session.add(
            EmbeddingSpace(
                key="a" * 64,
                provider_profile="protected",
                provider_kind="fake",
                model="fake-embedding",
                dimension=64,
                config_hash="b" * 64,
                config_json={},
                created_at=datetime.now(UTC),
            )
        )
    with pytest.raises(ValueError, match=r"embedding_migration_required|profile_in_use"):
        if action == "delete":
            await service.delete_profile(
                "protected", expected_version=created.version, actor_user_id=111
            )
        else:
            updates = (
                {"enabled": False}
                if action == "disable"
                else {
                    action: {"normalize_embeddings": False}
                    if action == "local_embedding_options"
                    else "changed-embedding"
                }
            )
            await service.update_profile(
                "protected",
                expected_version=created.version,
                updates=updates,
                actor_user_id=111,
                now=datetime.now(UTC),
            )
    unchanged = await service.get_profile("protected")
    assert unchanged.version == created.version
    assert unchanged.embedding_model == "fake-embedding"


async def test_pending_embedding_job_protects_profile_but_text_changes_remain_allowed(
    context,
) -> None:
    service = secret_service(context)
    created = await service.create_profile(
        ProviderProfileCreateRequest(
            name="pending",
            provider_kind="fake",
            base_url="https://provider.test/v1",
            chat_model="fake-chat",
            embedding_model="fake-embedding",
        ).model_dump(),
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    async with session_scope(context.session_factory) as session:
        session.add(
            EmbeddingIndexJob(
                provider_profile="pending",
                model="fake-embedding",
                config_hash="c" * 64,
                config_json={},
                settings_snapshot="d" * 64,
                status="queued",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    updated = await service.update_profile(
        "pending",
        expected_version=created.version,
        updates={"chat_model": "changed-chat", "local_chat_options": {"max_new_tokens": 32}},
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    assert updated.chat_model == "changed-chat"
    with pytest.raises(ValueError, match="embedding_migration_required"):
        await service.update_profile(
            "pending",
            expected_version=updated.version,
            updates={"embedding_capable": False},
            actor_user_id=111,
            now=datetime.now(UTC),
        )


async def test_empty_profile_embedding_edit_advances_epoch_but_chat_edit_does_not(context) -> None:
    service = secret_service(context)
    created = await service.create_profile(
        ProviderProfileCreateRequest(
            name="empty",
            provider_kind="fake",
            base_url="https://provider.test/v1",
            chat_model="fake-chat",
            embedding_model="fake-embedding",
        ).model_dump(),
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    async with session_scope(context.session_factory) as session:
        before = await EmbeddingIndexRepository(session).current_epoch()
    chat_changed = await service.update_profile(
        "empty",
        expected_version=created.version,
        updates={"chat_model": "other-chat"},
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingIndexRepository(session).current_epoch() == before
    await service.update_profile(
        "empty",
        expected_version=chat_changed.version,
        updates={"embedding_model": "other-embedding"},
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    async with session_scope(context.session_factory) as session:
        assert await EmbeddingIndexRepository(session).current_epoch() == before + 1


@pytest.mark.parametrize(
    "options", [{}, {"revision": "main"}, {"revision": "a" * 40, "tokenizer_revision": "main"}]
)
async def test_unprepared_embedded_embedding_route_fails_before_loading(
    monkeypatch, options
) -> None:
    from chatterlore.ai import local

    class MustNotLoad:
        name = "local"
        model = "owner/not-prepared"
        embedding_model = model
        dimensions = 3
        loaded = False

        def __init__(self, *args, **kwargs):
            pass

        async def embed(self, texts):
            raise AssertionError("Unpinned model reached the inference loader")

    monkeypatch.setattr(local, "LocalSentenceTransformerEmbeddingProvider", MustNotLoad)
    registry = ProviderRegistry(
        settings_with_profile(
            {
                "provider": "local",
                "chat_capable": False,
                "chat_model": "",
                "embedding_model": "owner/not-prepared",
                "local_embedding_options": options,
            }
        )
    )
    with pytest.raises(ValueError, match="local_model_preparation_required"):
        await registry.select("route").embedding_provider.embed(["do not download"])


async def test_environment_profile_overlay_cannot_replace_referenced_embedding_identity(
    context,
) -> None:
    service = secret_service(context)
    async with session_scope(context.session_factory) as session:
        session.add(
            EmbeddingSpace(
                key="e" * 64,
                provider_profile="default",
                provider_kind="fake",
                model="fake-embedding",
                dimension=64,
                config_hash="f" * 64,
                config_json={},
                created_at=datetime.now(UTC),
            )
        )
    with pytest.raises(ValueError, match="embedding_migration_required"):
        await service.create_profile(
            ProviderProfileCreateRequest(
                name="default",
                provider_kind="fake",
                base_url=context.settings.openai_base_url,
                chat_model="fake-chat",
                embedding_model="changed-embedding",
            ).model_dump(),
            actor_user_id=111,
            now=datetime.now(UTC),
        )


async def test_local_profile_metadata_can_be_managed_without_cloud_secret_vault(context) -> None:
    service = ProviderSecretService(
        context.settings, context.session_factory, context.memory.provider_registry
    )
    created = await service.create_profile(
        ProviderProfileCreateRequest(
            name="offline",
            provider_kind="local",
            local=True,
            auth_mode="none",
            base_url="http://localhost/v1",
            chat_model="owner/tiny",
            embedding_model="",
            embedding_capable=False,
        ).model_dump(),
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    updated = await service.update_profile(
        "offline",
        expected_version=created.version,
        updates={"local_chat_options": {"revision": "a" * 40}},
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    assert updated.local_chat_options["revision"] == "a" * 40
    with pytest.raises(SecretVaultError, match="encryption is not configured"):
        _ = service.vault


async def test_retired_managed_selection_cannot_send_new_revision_key_to_old_endpoint() -> None:
    settings = settings_with_profile(
        {
            "provider": "openai_compatible",
            "base_url": "https://old.test/v1",
            "api_key": "old-secret",
            "chat_model": "gpt-6.1-sol",
            "embedding_model": "e",
        }
    )
    old = settings.provider_profile_by_name("route")
    lease = CredentialLease(
        profile_id=1,
        profile_name="route",
        profile_revision=2,
        credential_id=1,
        credential_revision=1,
        label="test",
        fingerprint="safe",
        source="managed",
        _secret="new-endpoint-secret",  # noqa: S106
    )

    class Pool:
        async def execute(self, name, operation):
            return await operation(lease)

    def forbidden_client(key, url):
        raise AssertionError("A retired route attempted to create an outbound client")

    registry = ProviderRegistry(settings, openai_client_factory=forbidden_client)
    registry.configure_credential_pool(cast(CredentialPoolService, Pool()))
    registry.replace_managed_profiles({"route": old}, revisions={"route": 1})
    retired = registry.select("route")
    registry.replace_managed_profiles(
        {"route": replace(old, openai_base_url="https://new.test/v1")}, revisions={"route": 2}
    )
    with pytest.raises(ValueError, match="profile changed"):
        await retired.chat_provider.generate([ChatMessage(role="user", content="hello")])


async def test_managed_endpoint_change_detaches_old_bootstrap_environment_credential(
    context,
) -> None:
    settings = secret_service(context).settings.model_copy(
        update={"ai_provider": "openai_compatible", "openai_api_key": "bootstrap-remote-secret"}
    )
    service = ProviderSecretService(settings, context.session_factory, ProviderRegistry(settings))
    created = await service.create_profile(
        ProviderProfileCreateRequest(
            name="default",
            provider_kind="openai_compatible",
            base_url=settings.openai_base_url,
            chat_model="fake-chat",
            embedding_model="fake-embedding",
        ).model_dump(),
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    assert len(await service.list_credentials("default")) == 1
    await service.update_profile(
        "default",
        expected_version=created.version,
        updates={"base_url": "https://another-endpoint.test/v1"},
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    assert await service.list_credentials("default") == []
    assert service.registry.profile_by_name("default").openai_api_key == ""


async def test_local_registry_evicts_idle_models_and_restores_prepared_snapshot(
    monkeypatch, tmp_path
) -> None:
    from chatterlore.ai import local

    class Backend:
        loaded = False
        busy = False

        def __init__(self, model, *, options):
            self.model = model
            self.path = "not-prepared"
            self.closed = False

        async def prepare(
            self,
            *,
            allow_download=False,
            model_path: str | None = None,
            tokenizer_path: str | None = None,
        ):
            assert not self.closed
            if model_path is not None:
                self.path = model_path
            self.loaded = True

        async def generate(self, messages, **kwargs):
            assert not self.closed
            self.loaded = True
            return ChatResult(content=self.path, model=self.model)

        async def aclose(self):
            assert not self.busy
            self.closed = True
            self.loaded = False

    monkeypatch.setattr(local, "LocalTransformersChatProvider", Backend)
    settings = Settings(
        _env_file=None,
        memory_dir=tmp_path / "memory",
        local_max_loaded_models=1,
        ai_provider_profiles=json.dumps(
            {
                name: {
                    "provider": "local",
                    "chat_model": name,
                    "embedding_model": "",
                    "embedding_capable": False,
                }
                for name in ("one", "two")
            }
        ),
    )
    registry = ProviderRegistry(settings)
    first = cast(Any, registry.select("one").chat_provider)
    second = cast(Any, registry.select("two").chat_provider)
    await first.prepare(model_path="safe-pinned-snapshot")
    assert (await first.generate([])).content == "safe-pinned-snapshot"
    await second.generate([])
    assert not first.loaded
    assert second.loaded
    assert (await first.generate([])).content == "safe-pinned-snapshot"
    assert not second.loaded
    first.provider.busy = True
    with pytest.raises(RuntimeError, match="all models are busy"):
        await second.generate([])
    first.provider.busy = False
    await registry.aclose()


async def test_retiring_remote_provider_waits_for_inflight_request_before_close() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def respond(request):
        started.set()
        await release.wait()
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-6.1-sol",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "finished"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    client = AsyncOpenAI(
        api_key="test-key", base_url="https://provider.test/v1", http_client=transport
    )
    settings = settings_with_profile(
        {
            "provider": "openai_compatible",
            "api_key": "test-key",
            "base_url": "https://provider.test/v1",
            "chat_model": "gpt-6.1-sol",
            "embedding_model": "",
            "embedding_capable": False,
        }
    )
    registry = ProviderRegistry(settings, openai_client_factory=lambda key, url: client)
    task = asyncio.create_task(
        registry.select("route").chat_provider.generate([ChatMessage(role="user", content="hello")])
    )
    await started.wait()
    registry.invalidate("route")
    await asyncio.sleep(0)
    assert not transport.is_closed
    release.set()
    assert (await task).content == "finished"
    await registry.aclose()
    assert transport.is_closed


async def test_local_no_auth_real_sdk_never_follows_redirect_or_leaks_bootstrap_key(
    monkeypatch,
) -> None:
    from chatterlore.ai import openai_compatible

    requests: list[httpx.Request] = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://remote.test/v1/chat/completions"})

    monkeypatch.setattr(
        openai_compatible,
        "DefaultAsyncHttpxClient",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs),
    )
    settings = settings_with_profile(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "local": True,
            "auth_mode": "none",
            "chat_model": "gpt-6-local",
            "embedding_model": "",
            "embedding_capable": False,
        },
        openai_api_key="remote-bootstrap-secret",
        provider_retry_attempts=1,
    )
    registry = ProviderRegistry(settings)
    profile = settings.provider_profile_by_name("route")

    class ForbiddenPool:
        async def execute(self, *args):
            raise AssertionError("No-auth requests must bypass the credential pool")

    registry.configure_credential_pool(cast(CredentialPoolService, ForbiddenPool()))
    registry.replace_managed_profiles({"route": profile}, revisions={"route": 1})
    with pytest.raises(ProviderLeaseCallError):
        await registry.select("route").chat_provider.generate(
            [ChatMessage(role="user", content="hello")], max_tokens=17, reasoning_effort="high"
        )
    assert len(requests) == 1
    assert requests[0].url.host == "localhost"
    assert requests[0].headers["authorization"] == "Bearer local-no-auth"
    body = json.loads(requests[0].content)
    assert body == {
        "model": "gpt-6-local",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 17,
        "temperature": 0.2,
    }
    await registry.aclose()


async def test_real_sdk_does_not_multiply_policy_retry_budget(monkeypatch) -> None:
    from chatterlore.ai import openai_compatible

    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            500,
            json={
                "error": {"message": "synthetic", "type": "server_error", "code": "server_error"}
            },
        )

    monkeypatch.setattr(
        openai_compatible,
        "DefaultAsyncHttpxClient",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs),
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://provider.test/v1",
        chat_model="gpt-6.1-sol",
        embedding_model="",
        retry_attempts=3,
        retry_backoff_seconds=0,
    )
    try:
        with pytest.raises(ProviderLeaseCallError):
            await provider.generate([ChatMessage(role="user", content="hello")])
        assert attempts == 3
        assert provider.client.timeout == 60.0
    finally:
        await provider.aclose()
