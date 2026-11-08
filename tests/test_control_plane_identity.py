from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from chatterlore.config import Settings
from chatterlore.db.session import upgrade_schema


def control_plane_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:fake",
        "bot_admins": "111",
        "db_url": f"sqlite+aiosqlite:///{tmp_path / 'control-plane.db'}",
        "memory_dir": tmp_path / "memory",
        "control_plane_enabled": True,
        "control_plane_public_origin": "https://control.example.test",
        "telegram_oidc_client_id": "123456789",
        "telegram_oidc_client_secret": "oidc-secret",
        "control_plane_master_keys": '{"test":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        "control_plane_active_master_key_id": "test",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_control_plane_settings_fail_closed_and_expose_only_safe_metadata(tmp_path: Path) -> None:
    assert Settings().control_plane_enabled is False

    with pytest.raises(ValidationError, match="HTTPS public origin"):
        control_plane_settings(tmp_path, control_plane_public_origin="http://control.example.test")

    with pytest.raises(ValidationError, match="OIDC client secret"):
        control_plane_settings(tmp_path, telegram_oidc_client_secret="")

    settings = control_plane_settings(
        tmp_path,
        control_plane_public_origin="https://control.example.test/",
        control_plane_master_keys='{"k1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        control_plane_active_master_key_id="k1",
    )

    assert settings.control_plane_public_origin == "https://control.example.test"
    assert settings.control_plane_callback_url == (
        "https://control.example.test/auth/telegram/callback"
    )
    assert settings.control_plane_master_key_ids == ("k1",)
    assert settings.control_plane_safe_config == {
        "enabled": True,
        "public_origin": "https://control.example.test",
        "callback_url": "https://control.example.test/auth/telegram/callback",
        "oidc_client_configured": True,
        "master_key_ids": ["k1"],
        "active_master_key_id": "k1",
        "master_keys_configured": True,
        "session_store": "database",
        "provider_pools_enabled": True,
        "provider_pool_max_credentials": 10,
        "alert_worker_enabled": False,
        "auth_max_age_seconds": 300,
        "step_up_max_age_seconds": 300,
        "session_idle_seconds": 43_200,
        "session_absolute_seconds": 604_800,
    }
    rendered = repr(settings.control_plane_safe_config)
    assert "oidc-secret" not in rendered
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" not in rendered


def test_revision_0013_creates_control_plane_identity_and_access_schema(tmp_path: Path) -> None:
    settings = control_plane_settings(tmp_path)

    upgrade_schema(settings, "0013_control_plane_identity_access")

    assert settings.sqlite_path is not None
    with sqlite3.connect(settings.sqlite_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'index'"
            ).fetchall()
        }
        user_id_type = next(
            row[2]
            for row in connection.execute("pragma table_info(control_plane_users)")
            if row[1] == "telegram_user_id"
        )

    assert revision is not None
    assert revision[0] == "0013_control_plane_identity_access"
    assert {
        "control_plane_users",
        "control_plane_sessions",
        "oidc_auth_transactions",
        "bot_admin_grants",
        "chat_admin_grants",
        "managed_chats",
    } <= tables
    assert {
        "uq_control_plane_users_telegram_user_id",
        "uq_control_plane_sessions_token_hash",
        "ix_control_plane_sessions_user_active",
        "uq_oidc_auth_transactions_state_hash",
        "ix_oidc_auth_transactions_expiry",
        "uq_bot_admin_grants_telegram_user_id",
        "uq_chat_admin_grants_scope_user",
        "uq_managed_chats_chat_id",
    } <= indexes
    assert user_id_type == "BIGINT"
