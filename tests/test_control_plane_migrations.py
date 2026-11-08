from __future__ import annotations

import base64
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chatterlore.app_context import build_context
from chatterlore.config import Settings
from chatterlore.db.session import upgrade_schema
from chatterlore.memory.scope import Scope

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
LEGACY_CHAT_ID = -100_414_801_9329
LEGACY_TOPIC_ID = 77


def migration_settings(tmp_path: Path) -> Settings:
    master_key = base64.b64encode(bytes([17]) * 32).decode()
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        bot_admins="111",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}",
        memory_dir=tmp_path / "memory",
        ai_provider="fake",
        ai_provider_profile="legacy",
        ai_provider_profiles=json.dumps(
            {
                "legacy": {
                    "provider": "fake",
                    "chat_model": "legacy-chat",
                    "embedding_model": "legacy-embedding",
                }
            }
        ),
        chat_model="fake-chat",
        embedding_model="fake-embedding",
        control_plane_enabled=True,
        control_plane_public_origin="https://control.example.test",
        telegram_oidc_client_id="123456789",
        telegram_oidc_client_secret="oidc-secret",  # noqa: S106
        control_plane_master_keys=json.dumps({"primary": master_key}),
        control_plane_active_master_key_id="primary",
    )


@pytest.mark.asyncio
async def test_seeded_pre_control_plane_database_upgrades_to_head_and_loads_through_services(
    tmp_path: Path,
) -> None:
    settings = migration_settings(tmp_path)
    upgrade_schema(settings, "0012_telemetry_scope_indexes")
    assert settings.sqlite_path is not None
    timestamp = NOW.isoformat()
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute(
            """
            insert into messages (
                telegram_message_id, chat_id, message_thread_id, user_id, username,
                chat_title, chat_username, topic_title, message_permalink, text,
                normalized_text, learned, skip_reason, reply_to_message_id,
                created_at, ingested_at, summarized_at, summary_claim_token,
                summary_claimed_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, null, null, ?, ?, null, null, null)
            """,
            (
                501,
                LEGACY_CHAT_ID,
                LEGACY_TOPIC_ID,
                111,
                "legacy_admin",
                "Legacy migration group",
                "legacy_migration",
                "Legacy topic",
                "https://t.me/legacy_migration/501",
                "The legacy deployment codename is kestrel.",
                "the legacy deployment codename is kestrel.",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            insert into knowledge_items (
                chat_id, message_thread_id, kind, content, source_message_ids,
                metadata_json, created_at, updated_at
            ) values (?, ?, 'note', ?, ?, ?, ?, ?)
            """,
            (
                LEGACY_CHAT_ID,
                LEGACY_TOPIC_ID,
                "Legacy durable note.",
                json.dumps([501]),
                json.dumps({"source": "legacy"}),
                timestamp,
                timestamp,
            ),
        )
        connection.executemany(
            """
            insert into scope_metadata (
                chat_id, message_thread_id, chat_title, topic_title,
                chat_username, updated_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    LEGACY_CHAT_ID,
                    None,
                    "Legacy migration group",
                    None,
                    "legacy_migration",
                    timestamp,
                ),
                (
                    LEGACY_CHAT_ID,
                    LEGACY_TOPIC_ID,
                    "Legacy migration group",
                    "Legacy topic",
                    "legacy_migration",
                    timestamp,
                ),
            ],
        )
        connection.execute(
            """
            insert into scope_settings (
                chat_id, message_thread_id, retrieval_top_k, system_prompt_template,
                provider_profile, chat_model, embedding_model, reasoning_level,
                updated_by, updated_at
            ) values (?, ?, 8, 'legacy-ops', 'legacy', 'legacy-chat',
                      'legacy-embedding', 'high', 111, ?)
            """,
            (LEGACY_CHAT_ID, LEGACY_TOPIC_ID, timestamp),
        )
        connection.execute(
            """
            insert into runtime_settings (
                key, value, previous_value, updated_by, updated_at, version
            ) values ('retrieval_top_k', ?, null, 111, ?, 1)
            """,
            (json.dumps({"value": 7}), timestamp),
        )
        connection.execute(
            """
            insert into system_prompt_templates (
                name, prompt, version, created_by, created_at, updated_at
            ) values ('legacy-ops', 'Answer from migrated evidence.', 'v1', 111, ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            insert into memory_profiles (
                name, description, created_by, created_at, updated_at
            ) values ('legacy-profile', 'Migrated profile', 111, ?, ?)
            """,
            (timestamp, timestamp),
        )
        profile_id = connection.execute(
            "select id from memory_profiles where name = 'legacy-profile'"
        ).fetchone()[0]
        connection.execute(
            """
            insert into memory_profile_scopes (profile_id, chat_id, message_thread_id)
            values (?, ?, ?)
            """,
            (profile_id, LEGACY_CHAT_ID, LEGACY_TOPIC_ID),
        )
        connection.commit()

    upgrade_schema(settings)

    with sqlite3.connect(settings.sqlite_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
        scope_version = connection.execute(
            "select version from scope_settings where chat_id = ? and message_thread_id = ?",
            (LEGACY_CHAT_ID, LEGACY_TOPIC_ID),
        ).fetchone()
        migrated_tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'")
        }
    assert revision == ("0019_embedding_spaces",)
    assert scope_version == (1,)
    assert {
        "control_plane_users",
        "managed_chats",
        "managed_chat_enrollment_requests",
        "ai_provider_profiles",
        "ai_provider_credentials",
        "admin_alerts",
    } <= migrated_tables

    context = await build_context(settings, ensure_schema=False)
    try:
        scope = Scope(LEGACY_CHAT_ID, LEGACY_TOPIC_ID)
        status = await context.memory.memory_status(scope)
        effective = await context.memory.get_scope_settings(scope)
        runtime = {item.key: item.value for item in await context.memory.list_runtime_settings()}
        profiles = await context.memory.list_memory_profiles()
        provider = context.memory.provider_registry.select("legacy")

        assert status.messages == 1
        assert status.knowledge_items == 1
        assert effective.retrieval_top_k == 8
        assert effective.system_prompt_template == "legacy-ops"
        assert effective.provider_profile == "legacy"
        assert effective.chat_model == "legacy-chat"
        assert effective.embedding_model == "legacy-embedding"
        assert effective.reasoning_level == "high"
        assert runtime["retrieval_top_k"] == 7
        assert [(item.name, item.source_scopes) for item in profiles] == [
            ("legacy-profile", [scope])
        ]
        assert provider.profile.chat_model == "legacy-chat"
        assert context.admins.is_bot_admin(111)
        assert context.control_plane is not None
        assert await context.control_plane.management.list_managed_chats() == []
    finally:
        assert context.control_plane is not None
        await context.control_plane.oidc.aclose()
        await context.session_factory.kw["bind"].dispose()
