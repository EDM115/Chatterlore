from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import pzp_ai_bot.main as main_module
from pzp_ai_bot.main import app, provider_health_checks
from test_control_plane_api import ORIGIN, build_test_app, signed_init_data

runner = CliRunner()


def test_provider_credential_replace_is_atomic_versioned_and_secret_safe(tmp_path) -> None:
    context, app = build_test_app(tmp_path)
    profile_payload = {
        "name": "primary",
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
    }

    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data(111)},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        assert (
            client.post(
                "/api/control/v1/providers", headers=headers, json=profile_payload
            ).status_code
            == 200
        )
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
            json={"label": "Main", "value": "sk-old-provider-secret"},
        )
        assert created.status_code == 200
        credential_id = created.json()["id"]
        old_fingerprint = created.json()["fingerprint"]
        version = created.json()["version"]

        assert (
            client.post(
                "/api/control/v1/auth/step-up/mini-app",
                headers=headers,
                json={"init_data": signed_init_data(111), "action": "credential:replace"},
            ).status_code
            == 200
        )
        replaced = client.post(
            f"/api/control/v1/providers/primary/credentials/{credential_id}/replace",
            headers=headers,
            json={"version": version, "label": "Rotated main", "value": "sk-new-provider-secret"},
        )
        stale = client.post(
            f"/api/control/v1/providers/primary/credentials/{credential_id}/replace",
            headers=headers,
            json={"version": version, "value": "sk-stale-provider-secret"},
        )

    assert replaced.status_code == 200
    assert replaced.json()["label"] == "Rotated main"
    assert replaced.json()["version"] == version + 1
    assert replaced.json()["fingerprint"] != old_fingerprint
    assert replaced.json()["last_four"] == "cret"
    assert replaced.json()["health_state"] == "unknown"
    assert replaced.json()["success_count"] == 0
    assert replaced.json()["failure_count"] == 0
    assert stale.status_code == 409

    async def verify_secret_and_audit() -> None:
        assert context.control_plane is not None
        value = await context.control_plane.secrets.reveal_credential(
            "primary", credential_id, actor_user_id=111
        )
        assert value == "sk-new-provider-secret"
        events = await context.memory.audit_events(
            event_type="control_plane.provider.credential_replaced", limit=10
        )
        assert len(events) == 1
        assert "secret" not in str(events[0].payload).lower()
        assert events[0].payload["credential_id"] == credential_id

    asyncio.run(verify_secret_and_audit())


def test_control_plane_safe_config_doctor_and_show_config_cover_runtime_readiness(
    monkeypatch, tmp_path
) -> None:
    context, _app = build_test_app(tmp_path)
    settings = context.settings.model_copy(
        update={"runtime_mode": "webhook", "control_plane_max_credentials_per_profile": 10}
    )
    raw_master_config = settings.control_plane_master_keys
    oidc_secret = settings.telegram_oidc_client_secret
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    safe = settings.control_plane_safe_config
    assert safe["session_store"] == "database"
    assert safe["provider_pools_enabled"] is True
    assert safe["provider_pool_max_credentials"] == 10
    assert safe["alert_worker_enabled"] is True
    assert safe["oidc_client_configured"] is True
    assert safe["master_keys_configured"] is True

    checks = provider_health_checks(settings)
    names = {name for name, _ok, _detail in checks}
    assert {
        "control_plane",
        "control_plane_oidc",
        "control_plane_master_keys",
        "control_plane_session_store",
        "provider_pools",
        "alert_worker",
    }.issubset(names)
    assert all(ok for name, ok, _detail in checks if name.startswith("control_plane"))

    shown = runner.invoke(app, ["show-config"])
    diagnosed = runner.invoke(app, ["doctor"])
    assert shown.exit_code == 0, shown.output
    assert diagnosed.exit_code == 0, diagnosed.output
    combined = shown.output + diagnosed.output
    assert "session_store" in combined
    assert "provider_pools" in combined
    assert "alert_worker" in combined
    assert oidc_secret not in combined
    assert raw_master_config not in combined
