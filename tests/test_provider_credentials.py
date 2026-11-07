from __future__ import annotations

import base64
import importlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.session import (
    create_schema,
    create_session_factory,
    session_scope,
    upgrade_schema,
)

NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def provider_settings(tmp_path: Path) -> Settings:
    master_key = base64.b64encode(bytes([7]) * 32).decode()
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        bot_admins="111",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'providers.db'}",
        memory_dir=tmp_path / "memory",
        control_plane_enabled=True,
        control_plane_public_origin="https://control.example.test",
        telegram_oidc_client_id="123456789",
        telegram_oidc_client_secret="oidc-secret",  # noqa: S106
        control_plane_master_keys=f'{{"primary":"{master_key}"}}',
        control_plane_active_master_key_id="primary",
    )


def repository_api():
    try:
        module = importlib.import_module("pzp_ai_bot.db.repos.provider_credentials")
    except ModuleNotFoundError:
        pytest.fail("provider credential repositories are not implemented")
    return module.ProviderCredentialRepository, module.ProviderProfileRepository


def test_revision_0015_creates_provider_pool_and_admin_alert_schema(tmp_path: Path) -> None:
    settings = provider_settings(tmp_path)

    upgrade_schema(settings)

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
        credential_columns = {
            row[1] for row in connection.execute("pragma table_info(ai_provider_credentials)")
        }

    assert revision == ("0019_embedding_spaces",)
    assert {
        "ai_provider_profiles",
        "ai_provider_credentials",
        "ai_provider_pool_state",
        "admin_alerts",
    } <= tables
    assert {
        "uq_ai_provider_profiles_name",
        "ix_ai_provider_credentials_profile_position",
        "ix_ai_provider_credentials_profile_state",
        "ix_ai_provider_credentials_fingerprint",
        "uq_ai_provider_pool_state_profile_id",
        "uq_admin_alerts_dedupe_key",
        "ix_admin_alerts_delivery_due",
        "ix_admin_alerts_profile_created",
    } <= indexes
    assert {
        "secret_ciphertext",
        "secret_nonce",
        "wrapped_dek",
        "wrap_nonce",
        "master_key_id",
        "fingerprint",
        "last_four",
        "version",
    } <= credential_columns


@pytest.mark.asyncio
async def test_repositories_enforce_versions_and_crypto_delete_credentials(tmp_path: Path) -> None:
    ProviderCredentialRepository, ProviderProfileRepository = repository_api()  # noqa: N806
    settings = provider_settings(tmp_path)
    await create_schema(settings)

    async with session_scope(create_session_factory(settings)) as session:
        profiles = ProviderProfileRepository(session)
        credentials = ProviderCredentialRepository(session)
        profile = await profiles.create(
            name="primary",
            provider_kind="openai_compatible",
            base_url="https://api.openai.com/v1",
            chat_model="gpt-5.6-terra",
            embedding_model="text-embedding-3-small",
            chat_capable=True,
            embedding_capable=True,
            reasoning_capable=True,
            local=False,
            redact_before_ai=True,
            redaction_patterns=[],
            fake_embedding_dimensions=64,
            actor_user_id=111,
            now=NOW,
        )
        assert profile.version == 1
        assert (await profiles.get_by_name("primary")).id == profile.id
        assert (await profiles.get_pool_state(profile.id)).version == 1

        updated = await profiles.update(
            profile.id,
            expected_version=1,
            updates={"chat_model": "gpt-5.5"},
            actor_user_id=111,
            now=NOW,
        )
        assert updated is not None
        assert updated.chat_model == "gpt-5.5"
        assert updated.version == 2
        assert (
            await profiles.update(
                profile.id,
                expected_version=1,
                updates={"chat_model": "stale"},
                actor_user_id=111,
                now=NOW,
            )
            is None
        )

        stored = await credentials.create_provisioning(
            profile_id=profile.id, label="primary-key", position=0, actor_user_id=111, now=NOW
        )
        stored = await credentials.store_encrypted(
            stored.id,
            ciphertext=b"ciphertext",
            secret_nonce=b"secret-nonce",
            wrapped_dek=b"wrapped-dek",
            wrap_nonce=b"wrap-nonce",
            master_key_id="primary",
            fingerprint="fingerprint",
            last_four="last",
        )
        assert stored.lifecycle_state == "enabled"
        assert stored.version == 1

        changed = await credentials.update(
            stored.id,
            expected_version=1,
            updates={"label": "renamed", "position": 2, "lifecycle_state": "disabled"},
            actor_user_id=111,
            now=NOW,
        )
        assert changed is not None
        assert changed.version == 2
        assert changed.lifecycle_state == "disabled"
        assert (
            await credentials.update(
                stored.id,
                expected_version=1,
                updates={"label": "stale"},
                actor_user_id=111,
                now=NOW,
            )
            is None
        )

        tombstone = await credentials.tombstone(
            stored.id, expected_version=2, actor_user_id=111, now=NOW
        )
        assert tombstone is not None
        assert tombstone.lifecycle_state == "deleted"
        assert tombstone.secret_ciphertext is None
        assert tombstone.secret_nonce is None
        assert tombstone.wrapped_dek is None
        assert tombstone.wrap_nonce is None
        assert tombstone.master_key_id is None
        assert tombstone.environment_variable is None
        assert tombstone.version == 3
