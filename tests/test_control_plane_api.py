from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from pzp_ai_bot.app_context import build_context
from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.app import create_control_plane_app
from pzp_ai_bot.control_plane.auth.identity import TelegramIdentity
from pzp_ai_bot.db.models import BotEvent, ControlPlaneSession, ControlPlaneUser
from pzp_ai_bot.db.repos.control_plane import ControlPlaneAccessRepository
from pzp_ai_bot.db.session import create_schema, session_scope

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
