from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiogram import Bot, Dispatcher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from chatterlore.app_context import AppContext, build_context
from chatterlore.config import Settings
from chatterlore.control_plane.app import (
    CONTROL_PLANE_DEV_AUTH_PATH,
    CONTROL_PLANE_DEV_STEP_UP_PATH,
    DevelopmentAuthConfig,
    create_control_plane_app,
    create_control_plane_schema_app,
)
from chatterlore.control_plane.auth.identity import TelegramIdentity
from chatterlore.control_plane.development import (
    ControlPlaneDevelopmentConfig,
    build_control_plane_development_settings,
    create_control_plane_development_app,
    load_or_create_development_master_key,
)
from chatterlore.db.models import BotEvent, ControlPlaneSession
from chatterlore.db.session import create_schema, session_scope


def base_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "control_plane_enabled": False,
        "control_plane_public_origin": "",
        "control_plane_master_keys": "",
        "control_plane_active_master_key_id": "",
        "telegram_bot_token": "",
        "bot_admins": "424242",
        "telegram_oidc_client_id": "",
        "telegram_oidc_client_secret": "",
        "webhook_url": "",
        "db_url": f"sqlite+aiosqlite:///{tmp_path / 'development.db'}",
        "memory_dir": tmp_path / "memory",
        "ai_provider": "fake",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_development_settings_use_values_loaded_from_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "CONTROL_PLANE_DEV_USER_ID",
        "CONTROL_PLANE_DEV_ORIGIN",
        "CONTROL_PLANE_DEV_API_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "CONTROL_PLANE_DEV_USER_ID=887758628\n"
        "CONTROL_PLANE_DEV_ORIGIN=http://127.0.0.1:3100\n"
        "CONTROL_PLANE_DEV_API_PORT=8100\n",
        encoding="utf-8",
    )

    settings, config = build_control_plane_development_settings(
        Settings(_env_file=dotenv_path), key_path=tmp_path / "dev-key"
    )

    assert config.user_id == 887_758_628
    assert config.origin == "http://127.0.0.1:3100"
    assert config.api_port == 8100
    assert settings.control_plane_public_origin == config.origin


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "https://localhost:3000",
        "http://127.0.0.1:3000",
        "http://[::1]:3000",
    ],
)
def test_development_settings_accept_only_explicit_loopback_origins(
    tmp_path: Path, origin: str
) -> None:
    settings, config = build_control_plane_development_settings(
        base_settings(tmp_path),
        environment={"CONTROL_PLANE_DEV_USER_ID": "887758628", "CONTROL_PLANE_DEV_ORIGIN": origin},
        key_path=tmp_path / "dev-key",
    )

    assert config.user_id == 887_758_628
    assert config.origin == origin
    assert config.api_port == 8000
    assert config.telegram_connected is False
    assert settings.control_plane_enabled is True
    assert settings.control_plane_public_origin == origin
    assert settings.bot_admin_ids == {424_242}


@pytest.mark.parametrize("user_id", ["", "0", "-1", "1.5", "admin"])
def test_development_settings_require_a_positive_integer_user_id(
    tmp_path: Path, user_id: str
) -> None:
    with pytest.raises(ValueError, match="CONTROL_PLANE_DEV_USER_ID"):
        build_control_plane_development_settings(
            base_settings(tmp_path),
            environment={"CONTROL_PLANE_DEV_USER_ID": user_id},
            key_path=tmp_path / "dev-key",
        )


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://example.test:3000",
        "https://192.168.1.20:3000",
        "ftp://localhost:3000",
        "http://user@localhost:3000",
        "http://localhost:3000/",
        "http://localhost:3000/control",
        "http://localhost:3000?debug=1",
        "http://localhost:3000#fragment",
    ],
)
def test_development_settings_reject_non_loopback_or_structured_origins(
    tmp_path: Path, origin: str
) -> None:
    with pytest.raises(ValueError, match="CONTROL_PLANE_DEV_ORIGIN"):
        build_control_plane_development_settings(
            base_settings(tmp_path),
            environment={"CONTROL_PLANE_DEV_USER_ID": "111", "CONTROL_PLANE_DEV_ORIGIN": origin},
            key_path=tmp_path / "dev-key",
        )


@pytest.mark.parametrize("port", ["0", "65536", "-1", "80.5", "http"])
def test_development_settings_reject_invalid_api_ports(tmp_path: Path, port: str) -> None:
    with pytest.raises(ValueError, match="CONTROL_PLANE_DEV_API_PORT"):
        build_control_plane_development_settings(
            base_settings(tmp_path),
            environment={"CONTROL_PLANE_DEV_USER_ID": "111", "CONTROL_PLANE_DEV_API_PORT": port},
            key_path=tmp_path / "dev-key",
        )


def test_development_master_key_is_stable_and_decodes_to_32_bytes(tmp_path: Path) -> None:
    key_path = tmp_path / "nested" / "dev-key"

    first = load_or_create_development_master_key(key_path)
    second = load_or_create_development_master_key(key_path)

    assert first == second
    assert key_path.read_text(encoding="ascii") == first
    assert len(base64.b64decode(first, validate=True)) == 32


def test_development_settings_prefer_a_complete_configured_keyring(tmp_path: Path) -> None:
    configured = base64.b64encode(bytes([7]) * 32).decode("ascii")
    key_path = tmp_path / "dev-key"

    settings, _ = build_control_plane_development_settings(
        base_settings(
            tmp_path,
            control_plane_master_keys=json.dumps({"configured": configured}),
            control_plane_active_master_key_id="configured",
        ),
        environment={"CONTROL_PLANE_DEV_USER_ID": "111"},
        key_path=key_path,
    )

    assert settings.control_plane_master_keys == json.dumps({"configured": configured})
    assert settings.control_plane_active_master_key_id == "configured"
    assert not key_path.exists()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not-base64",
        base64.b64encode(b"too-short").decode("ascii"),
        base64.b64encode(bytes([1]) * 33).decode("ascii"),
    ],
)
def test_development_master_key_never_replaces_corrupt_material(
    tmp_path: Path, content: str
) -> None:
    key_path = tmp_path / "dev-key"
    key_path.write_text(content, encoding="ascii")

    with pytest.raises(ValueError, match="development master key file is invalid") as error:
        load_or_create_development_master_key(key_path)

    assert content == key_path.read_text(encoding="ascii")
    if content:
        assert content not in str(error.value)


def test_webhook_url_alone_marks_development_as_telegram_connected(tmp_path: Path) -> None:
    _, config = build_control_plane_development_settings(
        base_settings(tmp_path, webhook_url="https://hooks.example.test/telegram/webhook"),
        environment={"CONTROL_PLANE_DEV_USER_ID": "111"},
        key_path=tmp_path / "dev-key",
    )

    assert config.telegram_connected is True


def build_development_app(tmp_path: Path) -> tuple[AppContext, FastAPI, str, int]:
    origin = "http://localhost:3000"
    settings, config = build_control_plane_development_settings(
        base_settings(tmp_path),
        environment={"CONTROL_PLANE_DEV_USER_ID": "887758628", "CONTROL_PLANE_DEV_ORIGIN": origin},
        key_path=tmp_path / "dev-key",
    )
    asyncio.run(create_schema(settings))
    context = asyncio.run(build_context(settings, ensure_schema=False))
    context.admins.bot_admin_ids.add(config.user_id)
    app = create_control_plane_app(
        context, development_auth=DevelopmentAuthConfig(telegram_user_id=config.user_id)
    )
    return context, app, origin, config.user_id


def build_development_context(
    tmp_path: Path, **overrides: object
) -> tuple[AppContext, ControlPlaneDevelopmentConfig]:
    settings, config = build_control_plane_development_settings(
        base_settings(tmp_path, **overrides),
        environment={
            "CONTROL_PLANE_DEV_USER_ID": "887758628",
            "CONTROL_PLANE_DEV_ORIGIN": "http://localhost:3000",
        },
        key_path=tmp_path / "dev-key",
    )
    asyncio.run(create_schema(settings))
    return asyncio.run(build_context(settings, ensure_schema=False)), config


def test_development_login_issues_a_real_bot_admin_session_with_loopback_cookie(
    tmp_path: Path,
) -> None:
    context, app, origin, user_id = build_development_app(tmp_path)

    with TestClient(app, base_url=origin) as client:
        missing_origin = client.post(CONTROL_PLANE_DEV_AUTH_PATH)
        assert missing_origin.status_code == 403
        identity_input = client.post(
            CONTROL_PLANE_DEV_AUTH_PATH, headers={"Origin": origin}, json={"telegram_user_id": 999}
        )
        assert identity_input.status_code == 422
        login = client.post(CONTROL_PLANE_DEV_AUTH_PATH, headers={"Origin": origin})
        assert login.status_code == 200
        assert login.json()["user"] == {
            "telegram_user_id": user_id,
            "first_name": "Development administrator",
            "last_name": None,
            "username": "chatterlore_control_plane_dev",
            "language_code": "en",
            "photo_url": None,
            "is_premium": None,
        }
        assert login.json()["client_kind"] == "browser"
        assert login.json()["capabilities"] == ["bot.admin"]
        first_csrf = login.json()["csrf_token"]
        cookie = login.headers["set-cookie"]
        assert "chatterlore_control_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Secure" not in cookie

        restored = client.get("/api/control/v1/session")
        assert restored.status_code == 200
        assert restored.json()["csrf_token"] == first_csrf
        logout = client.post(
            "/api/control/v1/auth/logout",
            headers={"Origin": origin, "X-CSRF-Token": restored.json()["csrf_token"]},
        )
        assert logout.status_code == 204
        assert "Secure" not in logout.headers["set-cookie"]

    async def inspect() -> None:
        async with session_scope(context.session_factory) as session:
            events = list(
                (
                    await session.scalars(
                        select(BotEvent).where(BotEvent.event_type == "control_plane.auth.success")
                    )
                ).all()
            )
            assert [event.payload for event in events] == [
                {"method": "development", "outcome": "success", "reason": "authenticated"},
                {"method": "browser", "outcome": "success", "reason": "logout"},
            ]

    asyncio.run(inspect())


def test_development_step_up_requires_session_csrf_identity_and_exact_action(
    tmp_path: Path,
) -> None:
    context, app, origin, user_id = build_development_app(tmp_path)

    with TestClient(app, base_url=origin) as client:
        login = client.post(CONTROL_PLANE_DEV_AUTH_PATH, headers={"Origin": origin})
        csrf = login.json()["csrf_token"]
        assert (
            client.post(
                CONTROL_PLANE_DEV_STEP_UP_PATH,
                headers={"Origin": origin},
                json={"action": "credential:reveal"},
            ).status_code
            == 403
        )
        unknown = client.post(
            CONTROL_PLANE_DEV_STEP_UP_PATH,
            headers={"Origin": origin, "X-CSRF-Token": csrf},
            json={"action": "generic"},
        )
        assert unknown.status_code == 400
        verified = client.post(
            CONTROL_PLANE_DEV_STEP_UP_PATH,
            headers={"Origin": origin, "X-CSRF-Token": csrf},
            json={"action": "credential:reveal"},
        )
        assert verified.status_code == 200
        assert verified.json()["step_up"] is True
        assert verified.json()["action"] == "credential:reveal"

    async def inspect() -> None:
        async with session_scope(context.session_factory) as session:
            stored = await session.scalar(
                select(ControlPlaneSession).order_by(ControlPlaneSession.id.desc())
            )
            assert stored is not None
            assert stored.step_up_action == "credential:reveal"
            assert stored.step_up_at is not None

    asyncio.run(inspect())

    runtime = context.control_plane
    assert runtime is not None

    async def issue_other_identity() -> tuple[str, str]:
        issued = await runtime.sessions.issue(
            TelegramIdentity(
                telegram_user_id=user_id + 1,
                first_name="Other",
                last_name=None,
                username=None,
                language_code="en",
                photo_url=None,
                is_bot=False,
                is_premium=None,
                authenticated_at=datetime.now(UTC),
            ),
            client_kind="browser",
            now=datetime.now(UTC),
        )
        return issued.session_token, issued.csrf_token

    other_token, other_csrf = asyncio.run(issue_other_identity())
    with TestClient(app, base_url=origin) as client:
        client.cookies.set("chatterlore_control_session", other_token)
        mismatch = client.post(
            CONTROL_PLANE_DEV_STEP_UP_PATH,
            headers={"Origin": origin, "X-CSRF-Token": other_csrf},
            json={"action": "credential:reveal"},
        )
        assert mismatch.status_code == 403


def test_development_auth_routes_are_absent_from_normal_apps_and_schema(tmp_path: Path) -> None:
    settings, _ = build_control_plane_development_settings(
        base_settings(tmp_path),
        environment={"CONTROL_PLANE_DEV_USER_ID": "111"},
        key_path=tmp_path / "dev-key",
    )
    asyncio.run(create_schema(settings))
    context = asyncio.run(build_context(settings, ensure_schema=False))
    normal_app = create_control_plane_app(context)

    assert CONTROL_PLANE_DEV_AUTH_PATH not in normal_app.openapi()["paths"]
    assert CONTROL_PLANE_DEV_STEP_UP_PATH not in normal_app.openapi()["paths"]
    schema_paths = create_control_plane_schema_app().openapi()["paths"]
    assert CONTROL_PLANE_DEV_AUTH_PATH not in schema_paths
    assert CONTROL_PLANE_DEV_STEP_UP_PATH not in schema_paths


class RecordingTelegramBot:
    def __init__(self, *, explode: bool = False) -> None:
        self.explode = explode
        self.calls: list[tuple[str, object]] = []
        self.session = SimpleNamespace(close=self.close)

    def _record(self, name: str, value: object) -> None:
        if self.explode:
            raise AssertionError(f"offline development contacted Telegram through {name}")
        self.calls.append((name, value))

    async def delete_my_commands(self) -> None:
        self._record("delete_my_commands", None)

    async def set_webhook(self, url: str, **kwargs: Any) -> bool:
        self._record("set_webhook", {"url": url, **kwargs})
        return True

    async def close(self) -> None:
        self._record("close", None)


class RecordingDispatcher:
    def __init__(self) -> None:
        self.updates = 0

    async def feed_update(self, bot: object, update: object) -> None:
        assert bot is not None
        assert update is not None
        self.updates += 1

    def resolve_used_update_types(self) -> list[str]:
        return ["message", "stopped_message_generation"]

    async def emit_shutdown(self, **kwargs: Any) -> None:
        return None


def test_offline_development_runtime_performs_zero_telegram_calls_and_is_healthy(
    tmp_path: Path,
) -> None:
    context, config = build_development_context(
        tmp_path,
        telegram_bot_token="123:configured-but-offline",  # noqa: S106
        webhook_url="",
    )
    bot = RecordingTelegramBot(explode=True)
    app = create_control_plane_development_app(
        context, config, bot=cast(Bot, bot), dispatcher=cast(Dispatcher, RecordingDispatcher())
    )

    with TestClient(app, base_url=config.origin) as client:
        health = client.get("/health")
        login = client.post(CONTROL_PLANE_DEV_AUTH_PATH, headers={"Origin": config.origin})
        assert "/telegram/webhook" not in app.openapi()["paths"]
        assert "chatterlore-admin-alert-delivery" not in app.state.background_tasks

    assert health.status_code == 200
    assert health.json() == {
        "ok": True,
        "tasks": {
            "chatterlore-idle-summarizer": True,
            "chatterlore-runtime-settings-refresher": True,
        },
    }
    assert login.status_code == 200
    assert login.json()["capabilities"] == ["bot.admin"]
    assert bot.calls == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "webhook_url": "https://hooks.example.test/telegram/webhook",
                "telegram_bot_token": "",
                "webhook_secret": "safe-development-secret",
            },
            "TELEGRAM_BOT_TOKEN",
        ),
        (
            {
                "webhook_url": "https://hooks.example.test/telegram/webhook",
                "telegram_bot_token": "123:token",
                "webhook_secret": "change-me",
            },
            "WEBHOOK_SECRET",
        ),
        (
            {
                "webhook_url": "http://localhost:9000/telegram/webhook",
                "telegram_bot_token": "123:token",
                "webhook_secret": "safe-development-secret",
            },
            "WEBHOOK_URL",
        ),
    ],
)
def test_connected_development_fails_before_telegram_contact_when_incomplete(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    context, config = build_development_context(tmp_path, **overrides)
    bot = RecordingTelegramBot(explode=True)

    with pytest.raises(RuntimeError, match=message):
        create_control_plane_development_app(
            context, config, bot=cast(Bot, bot), dispatcher=cast(Dispatcher, RecordingDispatcher())
        )

    assert bot.calls == []


def test_connected_development_registers_real_webhook_and_optional_alert_delivery(
    tmp_path: Path,
) -> None:
    webhook_url = "https://hooks.example.test/telegram/webhook"
    webhook_secret = "safe-development-secret"  # noqa: S105
    context, config = build_development_context(
        tmp_path,
        webhook_url=webhook_url,
        telegram_bot_token="123:token",  # noqa: S106
        webhook_secret=webhook_secret,
    )
    bot = RecordingTelegramBot()
    dispatcher = RecordingDispatcher()
    app = create_control_plane_development_app(
        context, config, bot=cast(Bot, bot), dispatcher=cast(Dispatcher, dispatcher)
    )

    with TestClient(app, base_url=config.origin) as client:
        assert "chatterlore-admin-alert-delivery" in app.state.background_tasks
        health = client.get("/health")
        rejected = client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 1},
        )

    assert health.status_code == 200
    assert "chatterlore-admin-alert-delivery" not in health.json()["tasks"]
    assert rejected.status_code == 401
    assert [name for name, _ in bot.calls] == ["delete_my_commands", "set_webhook"]
    assert bot.calls[1] == (
        "set_webhook",
        {
            "url": webhook_url,
            "secret_token": webhook_secret,
            "drop_pending_updates": False,
            "allowed_updates": ["message", "stopped_message_generation"],
        },
    )
