from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from chatterlore.app_context import AppContext
from chatterlore.control_plane.schemas import ProviderProfileCreateRequest
from chatterlore.control_plane.secrets.service import ProviderSecretService
from chatterlore.db.repos.settings import RuntimeSettingRepository
from chatterlore.db.session import session_scope
from chatterlore.memory.embedding_settings import EmbeddingMigrationRequiredError
from chatterlore.memory.scope import Scope


async def create_managed_profile(context: AppContext) -> ProviderSecretService:
    service = ProviderSecretService(
        context.settings, context.session_factory, context.memory.provider_registry
    )
    await service.create_profile(
        ProviderProfileCreateRequest(
            name="managed",
            provider_kind="fake",
            base_url="https://provider.test/v1",
            chat_model="managed-chat",
            embedding_model="managed-vector",
        ).model_dump(),
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    return service


@pytest.mark.parametrize("versioned", [False, True])
async def test_runtime_default_can_select_and_reload_managed_profile(
    context: AppContext, versioned: bool
) -> None:
    await create_managed_profile(context)
    if versioned:
        await context.memory.set_runtime_setting_versioned(
            {"provider_profile": "managed"}, expected_version=0, updated_by=111
        )
    else:
        await context.memory.set_runtime_settings({"provider_profile": "managed"}, updated_by=111)

    reloaded = await context.memory.refresh_runtime_settings()
    effective = await context.memory.get_scope_settings(Scope(-2401, None))

    assert reloaded["provider_profile"] == "managed"
    assert effective.chat_model == "managed-chat"
    assert effective.summary_model == "managed-chat"
    assert effective.embedding_model == "managed-vector"
    assert context.memory._select_chat_provider(effective).profile.name == "managed"
    assert context.memory._select_embedding_provider(effective).profile.name == "managed"


@pytest.mark.parametrize(
    "key",
    [
        "provider_profile",
        "chat_provider_profile",
        "summary_provider_profile",
        "embedding_provider_profile",
    ],
)
@pytest.mark.parametrize("disabled", [False, True], ids=["missing", "disabled"])
async def test_runtime_provider_references_reject_unavailable_profiles_without_persisting(
    context: AppContext, key: str, disabled: bool
) -> None:
    if disabled:
        service = await create_managed_profile(context)
        await service.update_profile(
            "managed",
            expected_version=1,
            updates={"enabled": False},
            actor_user_id=111,
            now=datetime.now(UTC),
        )

    with pytest.raises(ValueError, match="provider profile"):
        await context.memory.set_runtime_setting_versioned(
            {key: "managed"}, expected_version=0, updated_by=111
        )

    async with session_scope(context.session_factory) as session:
        assert key not in await RuntimeSettingRepository(session).values()
    effective = await context.memory.get_scope_settings(Scope(-2401, None))
    assert effective.provider_profile == "default"


async def test_managed_runtime_default_still_requires_migration_for_populated_memory(
    context: AppContext,
) -> None:
    await create_managed_profile(context)
    scope = Scope(-2401, None)
    await context.memory.remember(scope, "The original embedding space must remain active")

    with pytest.raises(EmbeddingMigrationRequiredError):
        await context.memory.set_runtime_setting_versioned(
            {"provider_profile": "managed"}, expected_version=0, updated_by=111
        )

    async with session_scope(context.session_factory) as session:
        assert "provider_profile" not in await RuntimeSettingRepository(session).values()
    effective = await context.memory.get_scope_settings(scope)
    assert effective.provider_profile == "default"
    assert effective.embedding_model == "fake-embedding"


@pytest.mark.parametrize("action", ["disable", "delete"])
@pytest.mark.parametrize("source", ["runtime", "scope"])
@pytest.mark.parametrize(
    "key",
    [
        "provider_profile",
        "chat_provider_profile",
        "summary_provider_profile",
        "embedding_provider_profile",
    ],
)
async def test_selected_managed_profile_cannot_be_removed_from_active_routes(
    context: AppContext, key: str, source: str, action: str
) -> None:
    service = await create_managed_profile(context)
    if source == "runtime":
        await context.memory.set_runtime_settings({key: "managed"}, updated_by=111)
    else:
        await context.memory.set_scope_settings(Scope(-2401, None), {key: "managed"})

    with pytest.raises(ValueError, match="profile_in_use"):
        if action == "disable":
            await service.update_profile(
                "managed",
                expected_version=1,
                updates={"enabled": False},
                actor_user_id=111,
                now=datetime.now(UTC),
            )
        else:
            await service.delete_profile("managed", expected_version=1, actor_user_id=111)

    await context.memory.refresh_runtime_settings()
    effective = await context.memory.get_scope_settings(Scope(-2401, None))
    assert getattr(effective, key) == "managed"
    assert (await service.get_profile("managed")).enabled is True
    if source == "runtime" and key == "provider_profile":
        from chatterlore.app_context import build_context

        restarted = await build_context(context.settings, ensure_schema=False)
        assert (await restarted.memory.get_scope_settings(Scope(-2401, None))).provider_profile == (
            "managed"
        )


async def test_profile_can_be_disabled_after_resetting_its_runtime_route(
    context: AppContext,
) -> None:
    service = await create_managed_profile(context)
    await context.memory.set_runtime_settings({"chat_provider_profile": "managed"}, updated_by=111)
    await context.memory.set_runtime_settings({"chat_provider_profile": None}, updated_by=111)

    disabled = await service.update_profile(
        "managed",
        expected_version=1,
        updates={"enabled": False},
        actor_user_id=111,
        now=datetime.now(UTC),
    )

    assert disabled.enabled is False
    await context.memory.refresh_runtime_settings()
    effective = await context.memory.get_scope_settings(Scope(-2401, None))
    assert effective.chat_provider_profile is None


async def test_deleting_selected_overlay_can_restore_available_bootstrap_profile(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        context.settings,
        "ai_provider_profiles",
        json.dumps(
            {
                "managed": {
                    "provider": "fake",
                    "chat_model": "bootstrap-chat",
                    "embedding_model": "bootstrap-vector",
                }
            }
        ),
    )
    service = await create_managed_profile(context)
    await context.memory.set_runtime_settings({"provider_profile": "managed"}, updated_by=111)

    await service.delete_profile("managed", expected_version=1, actor_user_id=111)

    await context.memory.refresh_runtime_settings()
    effective = await context.memory.get_scope_settings(Scope(-2401, None))
    assert effective.provider_profile == "managed"
    assert effective.chat_model == "bootstrap-chat"
    assert effective.embedding_model == "bootstrap-vector"


async def test_disabling_startup_provider_requires_changing_deployment_default(
    context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = await create_managed_profile(context)
    monkeypatch.setattr(context.settings, "ai_provider_profile", "managed")
    await context.memory.set_runtime_settings({"provider_profile": "default"}, updated_by=111)

    with pytest.raises(ValueError, match="AI_PROVIDER_PROFILE"):
        await service.update_profile(
            "managed",
            expected_version=1,
            updates={"enabled": False},
            actor_user_id=111,
            now=datetime.now(UTC),
        )


@pytest.mark.parametrize("superseded", [False, True])
async def test_only_effective_deployment_operation_route_prevents_disabling(
    context: AppContext, monkeypatch: pytest.MonkeyPatch, superseded: bool
) -> None:
    service = await create_managed_profile(context)
    monkeypatch.setattr(context.settings, "ai_chat_provider_profile", "managed")
    if superseded:
        await context.memory.set_runtime_settings(
            {"chat_provider_profile": "default"}, updated_by=111
        )
        disabled = await service.update_profile(
            "managed",
            expected_version=1,
            updates={"enabled": False},
            actor_user_id=111,
            now=datetime.now(UTC),
        )
        assert disabled.enabled is False
    else:
        with pytest.raises(ValueError, match="AI_CHAT_PROVIDER_PROFILE"):
            await service.update_profile(
                "managed",
                expected_version=1,
                updates={"enabled": False},
                actor_user_id=111,
                now=datetime.now(UTC),
            )
