from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from pzp_ai_bot.ai.factory import ProviderRegistry
from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.secrets.service import (
    ProviderSecretConflictError,
    ProviderSecretService,
)
from pzp_ai_bot.db.models import BotEvent
from pzp_ai_bot.db.repos.provider_credentials import (
    ProviderCredentialRepository,
    ProviderPoolStateRepository,
    ProviderProfileRepository,
)
from pzp_ai_bot.db.session import create_schema, create_session_factory, session_scope

NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def service_settings(tmp_path: Path, **overrides: object) -> Settings:
    master_key = base64.b64encode(bytes([5]) * 32).decode()
    values: dict[str, object] = {
        "telegram_bot_token": "123:fake",
        "bot_admins": "111",
        "db_url": f"sqlite+aiosqlite:///{tmp_path / 'service.db'}",
        "memory_dir": tmp_path / "memory",
        "ai_provider": "openai_compatible",
        "openai_api_key": "environment-secret",
        "chat_model": "gpt-5.4-mini",
        "embedding_model": "text-embedding-3-small",
        "control_plane_enabled": True,
        "control_plane_public_origin": "https://control.example.test",
        "telegram_oidc_client_id": "123456789",
        "telegram_oidc_client_secret": "oidc-secret",
        "control_plane_master_keys": json.dumps({"primary": master_key}),
        "control_plane_active_master_key_id": "primary",
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.mark.asyncio
async def test_managed_profile_overlay_preserves_environment_fallback_without_caching_managed_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PZP_MANAGED_TEST_API_KEY", "explicit-endpoint-secret")
    settings = service_settings(
        tmp_path,
        ai_provider_profiles=json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "base_url": "https://managed.example.test/v1",
                    "api_key_env": "PZP_MANAGED_TEST_API_KEY",
                }
            }
        ),
    )
    await create_schema(settings)
    registry = ProviderRegistry(settings)
    service = ProviderSecretService(settings, create_session_factory(settings), registry)

    await service.refresh_registry()
    environment = registry.select("default")
    assert environment.profile.openai_api_key == "explicit-endpoint-secret"
    assert environment.profile.openai_api_key != settings.openai_api_key

    profile = await service.create_profile(
        {
            "name": "DEFAULT",
            "provider_kind": "openai_compatible",
            "base_url": "https://managed.example.test/v1",
            "chat_model": "gpt-5.6-terra",
            "embedding_model": "text-embedding-3-large",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": True,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )
    assert profile.name == "default"
    managed_without_key = registry.select("default")
    assert managed_without_key.profile.chat_model == "gpt-5.6-terra"
    assert managed_without_key.profile.openai_api_key == "explicit-endpoint-secret"
    assert managed_without_key.profile.openai_api_key_env == "PZP_MANAGED_TEST_API_KEY"
    assert managed_without_key is not environment

    credential = await service.create_credential(
        "default", label="Primary Key", value="managed-secret-1234", actor_user_id=111, now=NOW
    )
    assert isinstance(credential.id, int)
    assert credential.revealable is True
    assert credential.last_four == "1234"
    assert "managed-secret" not in json.dumps(credential.as_dict())
    managed_with_key = registry.select("default")
    assert managed_with_key.profile.openai_api_key == "explicit-endpoint-secret"
    assert managed_with_key.profile.openai_api_key != "managed-secret-1234"
    assert managed_with_key.profile == registry.profile_by_name("default")

    summaries = await service.list_credentials("default")
    assert [summary.source for summary in summaries] == ["environment", "managed"]
    assert summaries[0].revealable is False
    assert summaries[1].revealable is True
    environment_credential = summaries[0]
    assert isinstance(environment_credential.id, int)
    with pytest.raises(ValueError, match="environment provider credentials are immutable"):
        await service.update_credential(
            "default",
            environment_credential.id,
            expected_version=environment_credential.version,
            updates={"label": "renamed bootstrap key"},
            actor_user_id=111,
            now=NOW,
        )
    with pytest.raises(ValueError, match="environment provider credentials are immutable"):
        await service.delete_credential(
            "default",
            environment_credential.id,
            expected_version=environment_credential.version,
            actor_user_id=111,
            now=NOW,
        )
    assert (
        await service.reveal_credential("default", credential.id, actor_user_id=111)
        == "managed-secret-1234"
    )

    async with session_scope(create_session_factory(settings)) as session:
        events = list(
            (
                await session.scalars(
                    select(BotEvent).where(BotEvent.event_type.like("control_plane.provider.%"))
                )
            ).all()
        )
    serialized = json.dumps([event.payload for event in events])
    assert events
    assert "managed-secret" not in serialized
    assert "environment-secret" not in serialized
    assert "explicit-endpoint-secret" not in serialized


@pytest.mark.asyncio
async def test_foreign_managed_profile_does_not_inherit_default_environment_key(
    tmp_path: Path,
) -> None:
    settings = service_settings(tmp_path)
    await create_schema(settings)
    registry = ProviderRegistry(settings)
    service = ProviderSecretService(settings, create_session_factory(settings), registry)
    await service.create_profile(
        {
            "name": "default",
            "provider_kind": "openai_compatible",
            "base_url": "https://managed.example.test/v1",
            "chat_model": "foreign-chat",
            "embedding_model": "foreign-embedding",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": False,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )

    profile = registry.profile_by_name("default")
    assert profile.openai_api_key == ""
    assert profile.openai_api_key_env is None
    assert await service.list_credentials("default") == []
    with pytest.raises(ValueError, match="requires an API key"):
        registry.select("default")

    await service.create_credential(
        "default",
        label="Foreign endpoint key",
        value="foreign-managed-secret",
        actor_user_id=111,
        now=NOW,
    )
    selected = registry.profile_by_name("default")
    assert selected.openai_api_key == ""
    assert selected.openai_api_key_env is None
    summaries = await service.list_credentials("default")
    assert [summary.source for summary in summaries] == ["managed"]
    serialized = json.dumps([summary.as_dict() for summary in summaries])
    assert "environment-secret" not in serialized
    assert "foreign-managed-secret" not in serialized


@pytest.mark.asyncio
async def test_versioned_mutations_limits_and_tombstone_are_safe(tmp_path: Path) -> None:
    settings = service_settings(tmp_path, control_plane_max_credentials_per_profile=1)
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = ProviderSecretService(settings, factory, ProviderRegistry(settings))
    profile = await service.create_profile(
        {
            "name": "managed",
            "provider_kind": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "chat_model": "gpt-5.4",
            "embedding_model": "text-embedding-3-small",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": True,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )
    updated = await service.update_profile(
        "managed",
        expected_version=profile.version,
        updates={"chat_model": "gpt-5.5"},
        actor_user_id=111,
        now=NOW,
    )
    assert updated.version == 2
    with pytest.raises(ProviderSecretConflictError):
        await service.update_profile(
            "managed",
            expected_version=1,
            updates={"chat_model": "stale"},
            actor_user_id=111,
            now=NOW,
        )

    credential = await service.create_credential(
        "managed", label="first", value="secret-one", actor_user_id=111, now=NOW
    )
    assert isinstance(credential.id, int)
    with pytest.raises(ValueError, match="credential limit"):
        await service.create_credential(
            "managed", label="second", value="secret-two", actor_user_id=111, now=NOW
        )
    async with session_scope(create_session_factory(settings)) as session:
        automatically_disabled = await ProviderCredentialRepository(session).apply_failure(
            credential.id,
            expected_version=credential.version,
            health_state="disabled",
            classification="authentication",
            cooldown_until=None,
            now=NOW,
        )
        assert automatically_disabled is not None
    reenabled = await service.update_credential(
        "managed",
        credential.id,
        expected_version=credential.version + 1,
        updates={"state": "enabled"},
        actor_user_id=111,
        now=NOW,
    )
    assert reenabled.state == "enabled"
    assert reenabled.health_state == "unknown"
    assert reenabled.last_error_classification is None
    async with session_scope(factory) as session:
        stored_profile = await ProviderProfileRepository(session).get_by_name("managed")
        assert stored_profile is not None
        states = ProviderPoolStateRepository(session)
        pool_state = await states.get(stored_profile.id)
        assert pool_state is not None
        assert (
            await states.compare_and_swap(
                stored_profile.id,
                expected_version=pool_state.version,
                active_credential_id=credential.id,
            )
            is not None
        )
    assert (await service.list_credentials("managed"))[0].active is True
    disabled = await service.update_credential(
        "managed",
        credential.id,
        expected_version=reenabled.version,
        updates={"state": "disabled", "position": 3},
        actor_user_id=111,
        now=NOW,
    )
    assert disabled.state == "disabled"
    assert disabled.health_state == "disabled"
    assert disabled.last_error_classification == "manual"
    assert disabled.position == 3
    async with session_scope(factory) as session:
        stored_profile = await ProviderProfileRepository(session).get_by_name("managed")
        assert stored_profile is not None
        pool_state = await ProviderPoolStateRepository(session).get(stored_profile.id)
        assert pool_state is not None
        assert pool_state.active_credential_id is None
    reenabled_for_deletion = await service.update_credential(
        "managed",
        credential.id,
        expected_version=disabled.version,
        updates={"state": "enabled"},
        actor_user_id=111,
        now=NOW,
    )
    async with session_scope(factory) as session:
        stored_profile = await ProviderProfileRepository(session).get_by_name("managed")
        assert stored_profile is not None
        states = ProviderPoolStateRepository(session)
        pool_state = await states.get(stored_profile.id)
        assert pool_state is not None
        assert (
            await states.compare_and_swap(
                stored_profile.id,
                expected_version=pool_state.version,
                active_credential_id=credential.id,
            )
            is not None
        )
    await service.delete_credential(
        "managed",
        credential.id,
        expected_version=reenabled_for_deletion.version,
        actor_user_id=111,
        now=NOW,
    )
    async with session_scope(factory) as session:
        stored_profile = await ProviderProfileRepository(session).get_by_name("managed")
        assert stored_profile is not None
        pool_state = await ProviderPoolStateRepository(session).get(stored_profile.id)
        assert pool_state is not None
        assert pool_state.active_credential_id is None
    with pytest.raises(ValueError, match="not found"):
        await service.reveal_credential("managed", credential.id, actor_user_id=111)


@pytest.mark.asyncio
async def test_rewrap_verifies_every_credential_before_any_database_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = base64.b64encode(bytes([5]) * 32).decode()
    next_key = base64.b64encode(bytes([6]) * 32).decode()
    settings = service_settings(
        tmp_path, control_plane_master_keys=json.dumps({"primary": primary, "next": next_key})
    )
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = ProviderSecretService(settings, factory, ProviderRegistry(settings))
    await service.create_profile(
        {
            "name": "managed",
            "provider_kind": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "chat_model": "gpt-5.4",
            "embedding_model": "text-embedding-3-small",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": True,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )
    for label, value in (("first", "secret-one"), ("second", "secret-two")):
        await service.create_credential(
            "managed", label=label, value=value, actor_user_id=111, now=NOW
        )

    async def key_ids() -> list[str | None]:
        async with session_scope(factory) as session:
            stored = await ProviderCredentialRepository(session).list_encrypted()
            return [item.master_key_id for item in stored]

    assert await key_ids() == ["primary", "primary"]
    original_rewrap = service.vault.rewrap
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("simulated interruption")
        return original_rewrap(*args, **kwargs)

    monkeypatch.setattr(service.vault, "rewrap", fail_second)
    with pytest.raises(ValueError, match="simulated interruption"):
        await service.rewrap("next", actor_user_id=111)
    assert await key_ids() == ["primary", "primary"]

    monkeypatch.setattr(service.vault, "rewrap", original_rewrap)
    assert await service.rewrap("next", actor_user_id=111) == 2
    assert await key_ids() == ["next", "next"]
    assert await service.reveal_credential("managed", 1, actor_user_id=111) == "secret-one"


@pytest.mark.asyncio
async def test_provider_profile_accepts_exactly_ten_credentials_and_rejects_the_eleventh(
    tmp_path: Path,
) -> None:
    settings = service_settings(
        tmp_path, control_plane_max_credentials_per_profile=10, openai_api_key=""
    )
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = ProviderSecretService(settings, factory, ProviderRegistry(settings))
    await service.create_profile(
        {
            "name": "bounded",
            "provider_kind": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "chat_model": "gpt-5.6-terra",
            "embedding_model": "text-embedding-3-small",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": True,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )

    for index in range(10):
        await service.create_credential(
            "bounded", label=f"key-{index}", value=f"secret-{index}", actor_user_id=111, now=NOW
        )
    assert len(await service.list_credentials("bounded")) == 10
    with pytest.raises(ValueError, match="credential limit"):
        await service.create_credential(
            "bounded", label="key-10", value="secret-10", actor_user_id=111, now=NOW
        )

    await factory.kw["bind"].dispose()
