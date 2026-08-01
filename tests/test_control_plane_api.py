from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

from aiogram import Bot, Dispatcher
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from pzp_ai_bot.app_context import AppContext, build_context
from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.app import create_control_plane_app
from pzp_ai_bot.control_plane.auth.identity import TelegramIdentity
from pzp_ai_bot.db.models import BotEvent, ControlPlaneSession, ControlPlaneUser
from pzp_ai_bot.db.repos.alerts import AdminAlertRepository
from pzp_ai_bot.db.repos.control_plane import ControlPlaneAccessRepository
from pzp_ai_bot.db.session import create_schema, session_scope
from pzp_ai_bot.memory.contracts import IncomingMessage
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.telegram.webhook import create_webhook_app

NOW = datetime.now(UTC).replace(microsecond=0)
ORIGIN = "https://control.example.test"
BOT_TOKEN = "123456:telegram-secret"  # noqa: S105


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": BOT_TOKEN,
        "bot_admins": "111",
        "db_url": f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        "memory_dir": tmp_path / "memory",
        "control_plane_enabled": True,
        "control_plane_public_origin": ORIGIN,
        "telegram_oidc_client_id": "123456789",
        "telegram_oidc_client_secret": "oidc-secret",
        "control_plane_master_keys": '{"test":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        "control_plane_active_master_key_id": "test",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def signed_init_data(user_id: int = 887_758_628) -> str:
    fields = {
        "auth_date": str(int(NOW.timestamp())),
        "query_id": f"query-{user_id}",
        "user": json.dumps(
            {
                "id": user_id,
                "is_bot": False,
                "first_name": f"User {user_id}",
                "username": f"user{user_id}",
            },
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeOIDCClient:
    def __init__(self, user_id: int = 887_758_628) -> None:
        self.user_id = user_id

    async def authorization_url(self, transaction) -> str:
        return f"https://oauth.telegram.org/auth?{urlencode({'state': transaction.state})}"

    async def exchange_code(self, **kwargs) -> TelegramIdentity:
        assert kwargs["code"] == "telegram-code"
        assert kwargs["redirect_uri"] == f"{ORIGIN}/auth/telegram/callback"
        return TelegramIdentity(
            telegram_user_id=self.user_id,
            first_name=f"User {self.user_id}",
            last_name=None,
            username=f"user{self.user_id}",
            language_code="en",
            photo_url=None,
            is_bot=False,
            is_premium=None,
            authenticated_at=NOW,
        )

    async def aclose(self) -> None:
        return None


class ControlPlaneHealthBotDouble:
    def __init__(self) -> None:
        self.session = SimpleNamespace(close=self.close)

    async def set_my_commands(self, commands: Any) -> None:
        return None

    async def set_webhook(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def close(self) -> None:
        return None


class ControlPlaneManagementBotDouble:
    def __init__(self) -> None:
        self.saved_buttons: list[tuple[int, Any]] = []
        self.sent_messages: list[tuple[int, str, Any]] = []
        self.chat_members: dict[tuple[int, int], Any] = {}
        self.left_chats: list[int] = []

    async def save_prepared_keyboard_button(self, user_id: int, button: Any) -> Any:
        self.saved_buttons.append((user_id, button))
        return SimpleNamespace(id="prepared-phase6-chat")

    async def send_message(self, user_id: int, text: str, reply_markup: Any) -> None:
        self.sent_messages.append((user_id, text, reply_markup))

    async def get_chat_member(self, chat_id: int, user_id: int) -> Any:
        return self.chat_members[(chat_id, user_id)]

    async def leave_chat(self, chat_id: int) -> bool:
        self.left_chats.append(chat_id)
        return True


def build_test_app(tmp_path: Path, **overrides: object):
    settings = settings_for(tmp_path, **overrides)
    asyncio.run(create_schema(settings))
    context = asyncio.run(build_context(settings, ensure_schema=False))
    return context, create_control_plane_app(context, oidc_client=FakeOIDCClient())


def test_mini_app_session_csrf_logout_headers_and_audit_are_fail_closed(tmp_path: Path) -> None:
    context, app = build_test_app(tmp_path)

    with TestClient(app, base_url=ORIGIN) as client:
        missing_origin = client.post(
            "/api/control/v1/auth/mini-app", json={"init_data": signed_init_data()}
        )
        assert missing_origin.status_code == 403

        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data()},
        )
        assert login.status_code == 200
        assert login.json()["user"]["telegram_user_id"] == 887_758_628
        csrf = login.json()["csrf_token"]
        cookie = login.headers["set-cookie"]
        assert "pzp_control_session=" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie
        assert login.headers["cache-control"] == "no-store"
        assert "https://telegram.org" in login.headers["content-security-policy"]

        session = client.get("/api/control/v1/session")
        assert session.status_code == 200
        assert session.json()["user"]["telegram_user_id"] == 887_758_628
        assert session.json()["csrf_token"] != csrf
        csrf = session.json()["csrf_token"]

        no_csrf = client.post("/api/control/v1/auth/logout", headers={"Origin": ORIGIN})
        assert no_csrf.status_code == 403
        logout = client.post(
            "/api/control/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
        assert logout.status_code == 204
        assert client.get("/api/control/v1/session").status_code == 401

    async def inspect() -> None:
        async with session_scope(context.session_factory) as database:
            events = list(
                (
                    await database.scalars(
                        select(BotEvent).where(BotEvent.event_type.like("control_plane.auth.%"))
                    )
                ).all()
            )
            serialized = json.dumps([event.payload for event in events])
            assert "init_data" not in serialized
            assert "telegram-secret" not in serialized
            assert events

    asyncio.run(inspect())


def test_oidc_callback_consumes_state_on_error_and_mints_the_shared_session_model(
    tmp_path: Path,
) -> None:
    context, app = build_test_app(tmp_path)

    with TestClient(app, base_url=ORIGIN) as client:
        start = client.get("/auth/telegram/start?return_to=/settings", follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        missing_code = client.get(f"/auth/telegram/callback?state={state}", follow_redirects=False)
        assert missing_code.status_code == 400
        replay = client.get(
            f"/auth/telegram/callback?state={state}&code=telegram-code", follow_redirects=False
        )
        assert replay.status_code == 400

        second_start = client.get(
            "/auth/telegram/start?return_to=/settings", follow_redirects=False
        )
        second_state = parse_qs(urlsplit(second_start.headers["location"]).query)["state"][0]
        callback = client.get(
            f"/auth/telegram/callback?state={second_state}&code=telegram-code",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/settings"
        assert (
            client.get("/api/control/v1/session").json()["user"]["telegram_user_id"] == 887_758_628
        )

        mini_app = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data()},
        )
        assert mini_app.status_code == 200

    async def inspect() -> None:
        async with session_scope(context.session_factory) as database:
            assert await database.scalar(select(func.count(ControlPlaneUser.id))) == 1
            assert await database.scalar(select(func.count(ControlPlaneSession.id))) == 2

    asyncio.run(inspect())


def test_browser_step_up_uses_a_new_oidc_transaction_bound_to_the_action(tmp_path: Path) -> None:
    context, app = build_test_app(tmp_path)

    with TestClient(app, base_url=ORIGIN) as client:
        start = client.get("/auth/telegram/start", follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        assert (
            client.get(
                f"/auth/telegram/callback?state={state}&code=telegram-code", follow_redirects=False
            ).status_code
            == 303
        )
        step_up_start = client.get(
            "/auth/telegram/step-up/start?action=credential:reveal&return_to=/providers",
            follow_redirects=False,
        )
        assert step_up_start.status_code == 307
        step_up_state = parse_qs(urlsplit(step_up_start.headers["location"]).query)["state"][0]
        callback = client.get(
            f"/auth/telegram/callback?state={step_up_state}&code=telegram-code",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/providers"

    async def inspect() -> None:
        async with session_scope(context.session_factory) as database:
            stored = await database.scalar(
                select(ControlPlaneSession).order_by(ControlPlaneSession.created_at.desc())
            )
            assert stored is not None
            assert stored.step_up_action == "credential:reveal"
            assert stored.step_up_at is not None

    asyncio.run(inspect())


def test_invalid_auth_never_falls_back_and_step_up_requires_the_same_identity(
    tmp_path: Path,
) -> None:
    context, app = build_test_app(tmp_path)

    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data()},
        )
        csrf = login.json()["csrf_token"]
        invalid = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data() + "tampered"},
        )
        assert invalid.status_code == 401
        assert "user" not in invalid.json()
        assert (
            client.get("/api/control/v1/session").json()["user"]["telegram_user_id"] == 887_758_628
        )
        csrf = client.get("/api/control/v1/session").json()["csrf_token"]

        wrong_identity = client.post(
            "/api/control/v1/auth/step-up/mini-app",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={"init_data": signed_init_data(999)},
        )
        assert wrong_identity.status_code == 403
        right_identity = client.post(
            "/api/control/v1/auth/step-up/mini-app",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={"init_data": signed_init_data()},
        )
        assert right_identity.status_code == 200
        assert right_identity.json()["step_up"] is True

        asyncio.run(_grant_bot_admin(context, 887_758_628))
        assert "bot.admin" in client.get("/api/control/v1/session").json()["capabilities"]
        asyncio.run(_revoke_bot_admin(context, 887_758_628))
        assert "bot.admin" not in client.get("/api/control/v1/session").json()["capabilities"]


def test_auth_entry_points_have_independent_rate_limit_buckets(tmp_path: Path) -> None:
    _, app = build_test_app(
        tmp_path, control_plane_auth_start_rate_limit=1, control_plane_mini_app_rate_limit=1
    )

    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/auth/telegram/start", follow_redirects=False).status_code == 307
        assert (
            client.post(
                "/api/control/v1/auth/mini-app",
                headers={"Origin": ORIGIN},
                json={"init_data": signed_init_data()},
            ).status_code
            == 200
        )
        assert client.get("/auth/telegram/start", follow_redirects=False).status_code == 429
        assert (
            client.post(
                "/api/control/v1/auth/mini-app",
                headers={"Origin": ORIGIN},
                json={"init_data": signed_init_data()},
            ).status_code
            == 429
        )


def test_provider_credentials_are_bot_admin_only_action_bound_and_never_listed_raw(
    tmp_path: Path,
) -> None:
    context, app = build_test_app(tmp_path)

    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data(111)},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
        profile = client.post(
            "/api/control/v1/providers",
            headers=headers,
            json={
                "name": "Primary",
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
        )
        assert profile.status_code == 200
        assert profile.json()["name"] == "primary"
        assert profile.json()["audit_event_id"] > 0

        rejected = client.post(
            "/api/control/v1/providers/primary/credentials",
            headers=headers,
            json={"label": "Main", "value": "sk-phase4-secret"},
        )
        assert rejected.status_code == 403
        step_up = client.post(
            "/api/control/v1/auth/step-up/mini-app",
            headers=headers,
            json={"init_data": signed_init_data(111), "action": "credential:create"},
        )
        assert step_up.status_code == 200
        assert step_up.json()["action"] == "credential:create"
        created = client.post(
            "/api/control/v1/providers/primary/credentials",
            headers=headers,
            json={"label": "Main", "value": "sk-phase4-secret"},
        )
        assert created.status_code == 200
        credential_id = created.json()["id"]
        assert created.json()["last_four"] == "cret"
        assert "value" not in created.json()

        listed = client.get("/api/control/v1/providers/primary/credentials")
        assert listed.status_code == 200
        serialized = json.dumps(listed.json())
        assert "sk-phase4-secret" not in serialized
        assert "ciphertext" not in serialized

        wrong_action = client.post(
            f"/api/control/v1/providers/primary/credentials/{credential_id}/reveal", headers=headers
        )
        assert wrong_action.status_code == 403
        reveal_step_up = client.post(
            "/api/control/v1/auth/step-up/mini-app",
            headers=headers,
            json={"init_data": signed_init_data(111), "action": "credential:reveal"},
        )
        assert reveal_step_up.status_code == 200
        revealed = client.post(
            f"/api/control/v1/providers/primary/credentials/{credential_id}/reveal", headers=headers
        )
        assert revealed.status_code == 200
        assert revealed.json()["value"] == "sk-phase4-secret"
        assert revealed.headers["cache-control"] == "no-store, private"
        assert revealed.headers["pragma"] == "no-cache"

        changed = client.patch(
            f"/api/control/v1/providers/primary/credentials/{credential_id}",
            headers=headers,
            json={"version": 1, "state": "disabled"},
        )
        assert changed.status_code == 200
        stale = client.patch(
            f"/api/control/v1/providers/primary/credentials/{credential_id}",
            headers=headers,
            json={"version": 1, "state": "enabled"},
        )
        assert stale.status_code == 409
        assert stale.json()["current"]["version"] == 2

    async def inspect_reveal_audit() -> None:
        async with session_scope(context.session_factory) as database:
            events = list(
                (
                    await database.scalars(
                        select(BotEvent).where(
                            BotEvent.event_type == "control_plane.provider.credential_revealed"
                        )
                    )
                ).all()
            )
            assert len(events) == 1
            assert "secret" not in json.dumps(events[0].payload)

    asyncio.run(inspect_reveal_audit())


def test_provider_pool_health_manual_checks_and_alert_history_are_bot_admin_only(
    tmp_path: Path,
) -> None:
    context, app = build_test_app(tmp_path, control_plane_provider_test_rate_limit=2)
    calls: list[tuple[str, str]] = []

    def client_factory(api_key: str, _base_url: str):
        async def chat_create(**_kwargs):
            calls.append((api_key, "chat"))
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
            )

        async def embedding_create(**_kwargs):
            calls.append((api_key, "embedding"))
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])])

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
            embeddings=SimpleNamespace(create=embedding_create),
        )

    context.memory.provider_registry.openai_client_factory = client_factory

    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data(111)},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        created_profile = client.post(
            "/api/control/v1/providers",
            headers=headers,
            json={
                "name": "Primary",
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
        )
        assert created_profile.status_code == 200
        assert (
            client.post(
                "/api/control/v1/auth/step-up/mini-app",
                headers=headers,
                json={"init_data": signed_init_data(111), "action": "credential:create"},
            ).status_code
            == 200
        )
        created = client.post(
            "/api/control/v1/providers/primary/credentials",
            headers=headers,
            json={"label": "Primary key", "value": "sk-manual-test-secret"},
        )
        assert created.status_code == 200
        credential_id = created.json()["id"]

        initial_pool = client.get("/api/control/v1/providers/primary/pool")
        assert initial_pool.status_code == 200
        assert initial_pool.json()["active_credential_fingerprint"] is None
        checked = client.post(
            f"/api/control/v1/providers/primary/credentials/{credential_id}/test",
            headers=headers,
            json={"mode": "chat"},
        )
        assert checked.status_code == 200
        assert checked.json()["ok"] is True
        assert checked.json()["mode"] == "chat"
        assert checked.json()["model"] == "gpt-5.6-terra"
        assert checked.json()["credential_label"] == "Primary key"
        assert "usage charges" in checked.json()["warning"]
        assert "secret" not in json.dumps(checked.json())
        embedded = client.post(
            f"/api/control/v1/providers/primary/credentials/{credential_id}/test",
            headers=headers,
            json={"mode": "embedding"},
        )
        assert embedded.status_code == 200
        assert embedded.json()["dimensions"] == 3
        assert (
            client.post(
                f"/api/control/v1/providers/primary/credentials/{credential_id}/test",
                headers=headers,
                json={"mode": "chat"},
            ).status_code
            == 429
        )
        active_pool = client.get("/api/control/v1/providers/primary/pool")
        assert active_pool.json()["active_credential_label"] == "Primary key"
        listed = client.get("/api/control/v1/providers/primary/credentials").json()
        assert listed[0]["active"] is True
        assert listed[0]["health_state"] == "healthy"
        assert listed[0]["success_count"] == 2

        async def enqueue_alert() -> int:
            async with session_scope(context.session_factory) as database:
                alert = await AdminAlertRepository(database).enqueue_intent(
                    alert_type="pool_exhausted",
                    profile_name="primary",
                    credential_fingerprint=None,
                    credential_label=None,
                    health_epoch=12,
                    payload={"classification": "exhausted", "unsafe": "sk-private"},
                    now=NOW,
                )
                return alert.id

        alert_id = asyncio.run(enqueue_alert())
        alerts = client.get("/api/control/v1/alerts")
        assert alerts.status_code == 200
        assert alerts.json()["items"][0]["id"] == alert_id
        assert alerts.json()["next_cursor"] is None
        assert alerts.json()["has_more"] is False
        assert "payload" not in alerts.json()["items"][0]
        assert "sk-private" not in json.dumps(alerts.json())
        retried = client.post(f"/api/control/v1/alerts/{alert_id}/retry", headers=headers)
        assert retried.status_code == 200
        assert retried.json()["state"] == "pending"

    assert calls == [("sk-manual-test-secret", "chat"), ("sk-manual-test-secret", "embedding")]

    _, unauthorized_app = build_test_app(tmp_path / "unauthorized")
    with TestClient(unauthorized_app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data(999)},
        )
        assert login.status_code == 200
        assert client.get("/api/control/v1/alerts").status_code == 403
        assert client.get("/api/control/v1/providers/default/pool").status_code == 403


def test_admin_alert_delivery_is_started_but_is_not_a_critical_health_dependency(
    tmp_path: Path,
) -> None:
    context, _ = build_test_app(tmp_path)
    context.settings.webhook_url = "https://control.example.test/telegram/webhook"
    context.settings.webhook_secret = "phase-five-webhook-secret"  # noqa: S105
    app = create_webhook_app(
        context,
        bot=cast(Bot, ControlPlaneHealthBotDouble()),
        dispatcher=cast(Dispatcher, SimpleNamespace()),
    )

    with TestClient(app) as client:
        alert_handle = app.state.background_tasks["pzp-admin-alert-delivery"]
        assert alert_handle.healthy is True
        cast(Any, client).portal.call(alert_handle.task.cancel)
        cast(Any, client).portal.call(asyncio.sleep, 0)
        response = client.get("/health")

    assert response.status_code == 200
    assert "pzp-admin-alert-delivery" not in response.json()["tasks"]


def test_phase6_bot_admin_runtime_settings_and_session_invalidation_api(tmp_path: Path) -> None:
    _context, app = build_test_app(tmp_path)

    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data(111)},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        listed = client.get("/api/control/v1/bot-settings/admins")
        assert listed.status_code == 200
        assert listed.json() == [
            {
                "telegram_user_id": 111,
                "source": "bootstrap",
                "version": None,
                "granted_by": None,
                "granted_at": None,
            }
        ]
        created = client.post(
            "/api/control/v1/bot-settings/admins", headers=headers, json={"telegram_user_id": 222}
        )
        assert created.status_code == 200
        assert created.json()["version"] == 1

        runtime = client.get("/api/control/v1/bot-settings/runtime")
        assert runtime.status_code == 200
        assert all(item["key"] != "openai_api_key" for item in runtime.json())
        changed = client.patch(
            "/api/control/v1/bot-settings/runtime",
            headers=headers,
            json={"updates": {"retrieval_top_k": 11}},
        )
        assert changed.status_code == 200
        assert (
            next(item for item in changed.json() if item["key"] == "retrieval_top_k")["value"] == 11
        )
        unsafe = client.patch(
            "/api/control/v1/bot-settings/runtime",
            headers=headers,
            json={"updates": {"openai_api_key": "never"}},
        )
        assert unsafe.status_code == 422

        assert (
            client.post(
                "/api/control/v1/auth/step-up/mini-app",
                headers=headers,
                json={"init_data": signed_init_data(111), "action": "bot_admin:delete"},
            ).status_code
            == 200
        )
        removed = client.request(
            "DELETE",
            "/api/control/v1/bot-settings/admins/222",
            headers=headers,
            json={"version": 1},
        )
        assert removed.status_code == 200
        assert removed.json()["version"] == 2


def test_phase6_managed_chat_admin_and_scope_settings_api_enforce_chat_boundaries(
    tmp_path: Path,
) -> None:
    context, app = build_test_app(tmp_path)
    bot = ControlPlaneManagementBotDouble()
    bot.chat_members[(-1001, 333)] = SimpleNamespace(
        status="member", user=SimpleNamespace(id=333, is_bot=False)
    )
    app.state.telegram_bot = bot

    async def seed() -> None:
        assert context.control_plane is not None
        await context.control_plane.management.enroll_chat(
            chat_id=-1001, title="Engineering", username="eng", actor_user_id=111, now=NOW
        )
        await context.memory.set_learning(Scope(-1001, None), True, updated_by=111)
        await context.memory.ingest(
            IncomingMessage(
                telegram_message_id=901,
                scope=Scope(-1001, None),
                user_id=111,
                username="owner",
                text="Memory retained until an explicit forget action.",
                created_at=NOW,
            )
        )

    asyncio.run(seed())

    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data(111)},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        chats = client.get("/api/control/v1/chats")
        assert chats.status_code == 200
        assert chats.json()[0]["chat_id"] == -1001
        delegated = client.post(
            "/api/control/v1/chats/-1001/admins", headers=headers, json={"telegram_user_id": 333}
        )
        assert delegated.status_code == 200
        assert delegated.json()["version"] == 1
        settings = client.patch(
            "/api/control/v1/chats/-1001/settings?thread_id=7",
            headers=headers,
            json={
                "settings": {"retrieval_top_k": 4, "reasoning_level": "high"},
                "learning_enabled": True,
                "allow_admins": True,
            },
        )
        assert settings.status_code == 200
        assert settings.json()["effective"]["retrieval_top_k"] == 4
        assert settings.json()["overrides"]["reasoning_level"] == "high"
        paused = client.patch(
            "/api/control/v1/chats/-1001/status",
            headers=headers,
            json={"status": "paused", "version": 1},
        )
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"
        stale = client.patch(
            "/api/control/v1/chats/-1001/status",
            headers=headers,
            json={"status": "active", "version": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["current"]["version"] == 2

        enrollment = client.post(
            "/api/control/v1/chats/enrollment-requests",
            headers=headers,
            json={"delivery": "mini_app"},
        )
        assert enrollment.status_code == 200
        assert enrollment.json()["prepared_button_id"] == "prepared-phase6-chat"
        request_id = enrollment.json()["request_id"]
        pending = client.get(f"/api/control/v1/chats/enrollment-requests/{request_id}")
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending"

        resumed = client.patch(
            "/api/control/v1/chats/-1001/status",
            headers=headers,
            json={"status": "active", "version": 2},
        )
        assert resumed.status_code == 200
        assert (
            client.post(
                "/api/control/v1/auth/step-up/mini-app",
                headers=headers,
                json={"init_data": signed_init_data(111), "action": "chat:leave"},
            ).status_code
            == 200
        )
        left = client.post(
            "/api/control/v1/chats/-1001/leave",
            headers=headers,
            json={"version": 3, "confirmation": "LEAVE -1001"},
        )
        assert left.status_code == 200
        assert left.json()["status"] == "detached"
        assert bot.left_chats == [-1001]
        assert asyncio.run(context.memory.memory_status(Scope(-1001, None))).messages == 1

        assert (
            client.post(
                "/api/control/v1/auth/step-up/mini-app",
                headers=headers,
                json={"init_data": signed_init_data(111), "action": "chat:forget"},
            ).status_code
            == 200
        )
        forgotten = client.post(
            "/api/control/v1/chats/-1001/forget",
            headers=headers,
            json={"version": 4, "confirmation": "FORGET -1001"},
        )
        assert forgotten.status_code == 200
        assert forgotten.json()["messages"] == 1
        assert asyncio.run(context.memory.memory_status(Scope(-1001, None))).messages == 0

        delegated_login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data(333)},
        )
        assert delegated_login.status_code == 200
        assert delegated_login.json()["chat_capabilities"] == {}
        assert client.get("/api/control/v1/chats").json() == []


async def _seed_phase6_scoped_authorization_matrix(context: AppContext) -> None:
    async with session_scope(context.session_factory) as session:
        repository = ControlPlaneAccessRepository(session)
        for chat_id, title in (
            (-1001, "Owned chat"),
            (-1002, "Foreign chat"),
            (-1003, "Detach chat"),
        ):
            await repository.upsert_managed_chat(
                chat_id=chat_id,
                title=title,
                username=None,
                status="active",
                actor_user_id=111,
                now=NOW,
            )
        await repository.grant_chat_admin(
            chat_id=-1001, telegram_user_id=333, actor_user_id=111, now=NOW
        )
        await repository.grant_chat_admin(
            chat_id=-1003, telegram_user_id=333, actor_user_id=111, now=NOW
        )


def test_phase6_scoped_authorization_matrix_matches_browser_and_mini_app_sessions(
    tmp_path: Path,
) -> None:
    for client_kind in ("browser", "mini_app"):
        case_path = tmp_path / client_kind
        case_path.mkdir()
        settings = settings_for(case_path)
        asyncio.run(create_schema(settings))
        context = asyncio.run(build_context(settings, ensure_schema=False))
        app = create_control_plane_app(context, oidc_client=FakeOIDCClient(user_id=333))

        asyncio.run(_seed_phase6_scoped_authorization_matrix(context))

        with TestClient(app, base_url=ORIGIN) as client:
            if client_kind == "mini_app":
                login = client.post(
                    "/api/control/v1/auth/mini-app",
                    headers={"Origin": ORIGIN},
                    json={"init_data": signed_init_data(333)},
                )
                assert login.status_code == 200
                csrf = login.json()["csrf_token"]
            else:
                start = client.get("/auth/telegram/start", follow_redirects=False)
                state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
                callback = client.get(
                    f"/auth/telegram/callback?state={state}&code=telegram-code",
                    follow_redirects=False,
                )
                assert callback.status_code == 303
                csrf = client.get("/api/control/v1/session").json()["csrf_token"]
            headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}

            visible_chat_ids = {
                item["chat_id"] for item in client.get("/api/control/v1/chats").json()
            }
            assert visible_chat_ids == {-1001, -1003}
            assert (
                client.get("/api/control/v1/chats/-1001/settings?thread_id=77").status_code == 200
            )
            assert (
                client.get("/api/control/v1/chats/-1002/settings?thread_id=77").status_code == 403
            )

            revoked = client.request(
                "DELETE",
                "/api/control/v1/chats/-1001/admins/333",
                headers=headers,
                json={"version": 1},
            )
            assert revoked.status_code == 200
            assert (
                client.get("/api/control/v1/chats/-1001/settings?thread_id=77").status_code == 403
            )

            detached = client.patch(
                "/api/control/v1/chats/-1003/status",
                headers=headers,
                json={"status": "detached", "version": 1},
            )
            assert detached.status_code == 200
            assert client.get("/api/control/v1/chats/-1003/settings").status_code == 403
            assert client.get("/api/control/v1/chats").json() == []


async def _grant_bot_admin(context, user_id: int) -> None:
    async with session_scope(context.session_factory) as session:
        await ControlPlaneAccessRepository(session).grant_bot_admin(
            telegram_user_id=user_id, actor_user_id=111, now=NOW
        )


async def _revoke_bot_admin(context, user_id: int) -> None:
    async with session_scope(context.session_factory) as session:
        await ControlPlaneAccessRepository(session).revoke_bot_admin(
            telegram_user_id=user_id, actor_user_id=111, now=NOW
        )
