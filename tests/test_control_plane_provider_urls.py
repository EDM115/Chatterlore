from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from chatterlore.app_context import AppContext
from chatterlore.config import Settings
from chatterlore.control_plane.app import create_control_plane_app
from chatterlore.control_plane.auth.identity import TelegramIdentity
from chatterlore.control_plane.middleware import SESSION_COOKIE_NAME

ORIGIN = "https://control.example.test"


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        bot_admins="111",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'providers.db'}",
        memory_dir=tmp_path / "memory",
        ai_provider="fake",
        control_plane_enabled=True,
        control_plane_public_origin=ORIGIN,
        telegram_oidc_client_id="123456789",
        telegram_oidc_client_secret="oidc-secret",  # noqa: S106
        control_plane_master_keys='{"test":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        control_plane_active_master_key_id="test",
    )


@pytest.fixture
async def provider_client(context: AppContext) -> AsyncIterator[AsyncClient]:
    assert context.control_plane is not None
    now = datetime.now(UTC)
    issued = await context.control_plane.sessions.issue(
        TelegramIdentity(
            telegram_user_id=111,
            first_name="Owner",
            last_name=None,
            username=None,
            language_code=None,
            photo_url=None,
            is_bot=False,
            is_premium=None,
            authenticated_at=now,
        ),
        client_kind="browser",
        now=now,
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_control_plane_app(context)),
        base_url=ORIGIN,
        cookies={SESSION_COOKIE_NAME: issued.session_token},
        headers={"Origin": ORIGIN, "X-CSRF-Token": issued.csrf_token},
    ) as client:
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_kind", ["local", "fake"])
async def test_non_http_profiles_can_be_created_and_edited_without_an_endpoint(
    provider_client: AsyncClient, provider_kind: str
) -> None:
    created = await provider_client.post(
        "/api/control/v1/providers",
        json={
            "name": "offline",
            "provider_kind": provider_kind,
            "base_url": "",
            "chat_model": "example/instruct",
            "embedding_capable": False,
            "local": True,
            "auth_mode": "none",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["base_url"] == ""

    updated = await provider_client.patch(
        "/api/control/v1/providers/offline",
        json={"version": created.json()["version"], "base_url": "", "chat_model": "example/new"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["chat_model"] == "example/new"

    switched = await provider_client.patch(
        "/api/control/v1/providers/offline",
        json={"version": updated.json()["version"], "provider_kind": "openai_compatible"},
    )
    assert switched.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["", "not-a-url", "https://user:secret@provider.example/v1"])
async def test_http_profiles_still_require_a_valid_endpoint(
    provider_client: AsyncClient, base_url: str
) -> None:
    invalid = await provider_client.post(
        "/api/control/v1/providers",
        json={"name": "http", "provider_kind": "openai_compatible", "base_url": base_url},
    )
    assert invalid.status_code == 422

    created = await provider_client.post(
        "/api/control/v1/providers",
        json={
            "name": "http",
            "provider_kind": "openai_compatible",
            "base_url": "https://provider.example/v1",
            "chat_model": "chat",
            "embedding_model": "embed",
        },
    )
    assert created.status_code == 200, created.text
    invalid_update = await provider_client.patch(
        "/api/control/v1/providers/http",
        json={"version": created.json()["version"], "base_url": base_url},
    )
    assert invalid_update.status_code == 422
