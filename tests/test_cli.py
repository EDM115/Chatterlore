from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import chatterlore.main as main_module
from chatterlore.ai.factory import ProviderRegistry
from chatterlore.app_context import build_context
from chatterlore.config import Settings, get_settings
from chatterlore.control_plane.secrets.service import ProviderSecretService
from chatterlore.db.repos.provider_credentials import ProviderCredentialRepository
from chatterlore.db.session import create_session_factory, session_scope, upgrade_schema
from chatterlore.main import app
from chatterlore.memory.scope import Scope
from chatterlore.memory.service import IncomingMessage, MemoryService

runner = CliRunner()


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def configure_cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("CONTROL_PLANE_ENABLED", "false")
    return db_path


def test_control_plane_dev_command_builds_the_dedicated_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_env(monkeypatch, tmp_path)
    monkeypatch.setenv("CONTROL_PLANE_DEV_USER_ID", "887758628")
    calls: list[Settings] = []

    async def run_development(settings: Settings) -> None:
        calls.append(settings)

    monkeypatch.setattr(
        main_module, "run_control_plane_development", run_development, raising=False
    )

    result = runner.invoke(app, ["control-plane-dev"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0].control_plane_enabled is False
    assert "887758628" not in result.output
    assert "123:fake" not in result.output


def test_control_plane_dev_command_reports_missing_uid_without_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_env(monkeypatch, tmp_path)
    monkeypatch.delenv("CONTROL_PLANE_DEV_USER_ID", raising=False)

    result = runner.invoke(app, ["control-plane-dev"])

    assert result.exit_code != 0
    assert "CONTROL_PLANE_DEV_USER_ID" in result.output
    assert "123:fake" not in result.output


def seed_cli_message(scope: Scope, text: str, *, message_id: int = 1) -> None:
    async def seed() -> None:
        context = await build_context(get_settings())
        await context.memory.set_learning(scope, True, updated_by=111)
        await context.memory.ingest(
            IncomingMessage(
                telegram_message_id=message_id,
                scope=scope,
                user_id=222,
                username="cli-user",
                text=text,
                created_at=datetime(2026, 6, 24, tzinfo=UTC),
            )
        )

    asyncio.run(seed())


def test_migrate_uses_alembic_revision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = configure_cli_env(monkeypatch, tmp_path)

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 0, result.output
    with sqlite3.connect(db_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
        telegram_identifier_types = {
            (table, column): next(
                row[2]
                for row in connection.execute(f"pragma table_info({table})")
                if row[1] == column
            )
            for table, column in (
                ("bot_events", "chat_id"),
                ("bot_events", "user_id"),
                ("embeddings", "chat_id"),
                ("knowledge_items", "chat_id"),
                ("learning_policies", "chat_id"),
                ("learning_policies", "user_id"),
                ("learning_policies", "updated_by"),
                ("messages", "chat_id"),
                ("messages", "user_id"),
            )
        }
        logical_index_sql = connection.execute(
            """
            select sql from sqlite_master
            where type = 'index' and name = 'uq_messages_logical_scope'
            """
        ).fetchone()
        canonical_snapshot_columns = {
            row[1] for row in connection.execute("pragma table_info(canonical_scope_snapshots)")
        }
    assert revision[0] == "0019_embedding_spaces"
    assert {
        "scope_key",
        "chat_id",
        "message_thread_id",
        "item_fingerprint",
        "entries_json",
        "provider",
        "provider_profile",
        "model",
        "reasoning_level",
        "prompt_version",
        "updated_at",
    } <= canonical_snapshot_columns
    assert set(telegram_identifier_types.values()) == {"BIGINT"}
    assert logical_index_sql is not None
    assert "COALESCE(message_thread_id, -1)" in logical_index_sql[0]
    with sqlite3.connect(db_path) as connection:
        message_columns = {row[1] for row in connection.execute("pragma table_info(messages)")}
        claim_index = connection.execute(
            """
            select name from sqlite_master
            where type = 'index' and name = 'ix_messages_summary_claim'
            """
        ).fetchone()
        permission_columns = {
            row[1] for row in connection.execute("pragma table_info(memory_scope_permissions)")
        }
    assert {"summary_claim_token", "summary_claimed_at"}.issubset(message_columns)
    assert claim_index == ("ix_messages_summary_claim",)
    assert "can_query" in permission_columns
    assert "can_learn" not in permission_columns


def test_profile_permission_migration_preserves_query_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = configure_cli_env(monkeypatch, tmp_path)
    settings = get_settings()
    upgrade_schema(settings, "0009_summary_claims")
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            insert into memory_scope_permissions (
                destination_chat_id, destination_thread_id,
                source_chat_id, source_thread_id,
                can_query, can_learn, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (-100800, 1, -100801, 2, 1, 1, "2026-07-29 12:00:00"),
                (-100800, 1, -100802, 3, 0, 1, "2026-07-29 12:00:00"),
            ],
        )
        connection.commit()

    upgrade_schema(settings)

    with sqlite3.connect(db_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
        permission_columns = {
            row[1] for row in connection.execute("pragma table_info(memory_scope_permissions)")
        }
        permissions = connection.execute(
            """
            select source_chat_id, can_query
            from memory_scope_permissions
            order by source_chat_id
            """
        ).fetchall()
    assert revision[0] == "0019_embedding_spaces"
    assert "can_learn" not in permission_columns
    assert permissions == [(-100802, 0), (-100801, 1)]


def test_telemetry_scope_migration_backfills_supported_legacy_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = configure_cli_env(monkeypatch, tmp_path)
    settings = get_settings()
    upgrade_schema(settings, "0011_canonical_scope_snapshots")
    metadata_values = [
        {"scope": "-100910:main"},
        {"scope": "-100910:7"},
        {"scope": "all:main"},
        {},
        {"scope": "not-a-scope"},
    ]
    with sqlite3.connect(db_path) as connection:
        for index, metadata in enumerate(metadata_values, start=1):
            connection.execute(
                """
                insert into ai_usage (
                    id, provider, model, operation, latency_ms, metadata_json, created_at
                ) values (?, 'fake', 'fake-chat', 'ask', ?, ?, '2026-07-29 12:00:00')
                """,
                (index, index * 10, json.dumps(metadata)),
            )
        connection.commit()

    upgrade_schema(settings)

    with sqlite3.connect(db_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
        scopes = connection.execute(
            """
            select chat_id, message_thread_id
            from ai_usage
            order by id
            """
        ).fetchall()
        indexes = {
            row[0]
            for row in connection.execute(
                """
                select name from sqlite_master
                where type = 'index' and (
                    tbl_name = 'ai_usage' or tbl_name = 'bot_events'
                )
                """
            )
        }

    assert revision[0] == "0019_embedding_spaces"
    assert scopes == [(-100910, None), (-100910, 7), (None, None), (None, None), (None, None)]
    assert {
        "ix_ai_usage_scope_created_at",
        "ix_ai_usage_operation_created_at",
        "ix_ai_usage_error_created_at",
        "ix_bot_events_type_scope_created_at",
        "ix_bot_events_scope_created_at",
    } <= indexes


def test_message_uniqueness_migration_reconciles_main_chat_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = configure_cli_env(monkeypatch, tmp_path)
    settings = get_settings()
    upgrade_schema(settings, "0007_align_telegram_identifier_types")
    with sqlite3.connect(db_path) as connection:
        message_values = (
            700,
            -100700,
            None,
            222,
            "duplicate",
            "duplicate",
            1,
            "2026-07-29 12:00:00",
            "2026-07-29 12:00:00",
        )
        for message_id in (10, 11):
            connection.execute(
                """
                insert into messages (
                    id, telegram_message_id, chat_id, message_thread_id, user_id,
                    text, normalized_text, learned, created_at, ingested_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, *message_values),
            )
        embedding_values = [
            (20, 10, "provider-a", "model-a"),
            (21, 11, "provider-a", "model-a"),
            (22, 11, "provider-b", "model-b"),
        ]
        for embedding_id, target_id, provider, model in embedding_values:
            connection.execute(
                """
                insert into embeddings (
                    id, target_type, target_id, chat_id, message_thread_id,
                    provider, model, dimension, vector, created_at
                ) values (?, 'message', ?, -100700, null, ?, ?, 1, ?, ?)
                """,
                (
                    embedding_id,
                    target_id,
                    provider,
                    model,
                    b"\x00\x00\x00\x00",
                    "2026-07-29 12:00:00",
                ),
            )
        connection.commit()

    upgrade_schema(settings)

    with sqlite3.connect(db_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
        messages = connection.execute(
            """
            select id from messages
            where chat_id = -100700
              and message_thread_id is null
              and telegram_message_id = 700
            """
        ).fetchall()
        embeddings = connection.execute(
            """
            select target_id, provider, model from embeddings
            where target_type = 'message'
            order by provider, model
            """
        ).fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into messages (
                    telegram_message_id, chat_id, message_thread_id, user_id,
                    text, normalized_text, learned, created_at, ingested_at
                ) values (700, -100700, null, 222, 'duplicate', 'duplicate', 1, ?, ?)
                """,
                ("2026-07-29 12:00:00", "2026-07-29 12:00:00"),
            )

    assert revision[0] == "0019_embedding_spaces"
    assert messages == [(10,)]
    assert embeddings == [(10, "provider-a", "model-a"), (10, "provider-b", "model-b")]


def test_export_memory_rejects_invalid_format_and_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_env(monkeypatch, tmp_path)

    bad_format = runner.invoke(app, ["export-memory", "--format", "pdf"])
    bad_scope = runner.invoke(app, ["export-memory", "--scope", "bad:scope"])

    assert bad_format.exit_code != 0
    assert "format must be md or jsonl" in bad_format.output
    assert bad_scope.exit_code != 0
    assert "invalid scope: bad:scope" in bad_scope.output


def test_import_jsonl_rejects_empty_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_cli_env(monkeypatch, tmp_path)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    result = runner.invoke(app, ["import-jsonl", "--file", str(empty), "--chat-id", "-100"])

    assert result.exit_code != 0
    assert "import file contains no memory items" in result.output


def test_memory_status_and_telegram_json_import_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_env(monkeypatch, tmp_path)
    export = tmp_path / "telegram.json"
    export.write_text(
        json.dumps(
            {
                "name": "CLI Group",
                "messages": [
                    {
                        "id": 10,
                        "type": "message",
                        "date": "2026-06-23T10:00:00",
                        "from_id": "user222",
                        "from": "Dana",
                        "text": "CLI import keeps history.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    import_result = runner.invoke(
        app, ["import-telegram-json", "--file", str(export), "--chat-id", "-100900"]
    )
    status_result = runner.invoke(app, ["memory-status", "--scope", "-100900"])

    assert import_result.exit_code == 0, import_result.output
    assert "Imported 1 Telegram JSON messages" in import_result.output
    assert status_result.exit_code == 0, status_result.output
    assert "messages: 1" in status_result.output
    assert "learned: 1" in status_result.output


@pytest.mark.parametrize("command", ["import-telegram", "import-telegram-html"])
def test_telegram_html_import_cli_uses_the_shared_topic_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    configure_cli_env(monkeypatch, tmp_path)
    export = tmp_path / "messages.html"
    export.write_text(
        '<html><body><div class="page_header"><div class="text bold">CLI Group</div></div><div class="page_body chat_page"><div class="history"><div class="message default" id="message10"><div class="body"><div class="date details" title="07.11.2026 19:18:44 UTC+01:00">19:18</div><div class="from_name">Dana</div><div class="text">HTML-only CLI archive</div></div></div></div></div></body></html>',  # noqa: E501
        encoding="utf-8",
    )

    result = runner.invoke(
        app, [command, "--file", str(export), "--chat-id", "-100901", "--thread-id", "5"]
    )
    status = runner.invoke(app, ["memory-status", "--scope", "-100901:5"])

    assert result.exit_code == 0, result.output
    assert "Imported 1 Telegram" in result.output
    assert "-100901:5" in result.output
    assert status.exit_code == 0, status.output
    assert "messages: 1" in status.output


def test_runtime_audit_and_db_maintenance_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_env(monkeypatch, tmp_path)

    runtime_result = runner.invoke(app, ["runtime-settings", "--set", "retrieval_top_k=7"])
    audit_result = runner.invoke(
        app, ["audit-events", "--event-type", "runtime_settings_update", "--format", "jsonl"]
    )
    stats_result = runner.invoke(app, ["db-stats"])
    integrity_result = runner.invoke(app, ["db-integrity"])
    backup_path = tmp_path / "backup.db"
    backup_result = runner.invoke(app, ["db-backup", "--out", str(backup_path)])

    assert runtime_result.exit_code == 0, runtime_result.output
    assert "retrieval_top_k: 7" in runtime_result.output
    assert audit_result.exit_code == 0, audit_result.output
    assert "runtime_settings_update" in audit_result.output
    assert stats_result.exit_code == 0, stats_result.output
    assert "table_runtime_settings" in stats_result.output
    assert integrity_result.exit_code == 0, integrity_result.output
    assert "integrity_check: ok" in integrity_result.output
    assert backup_result.exit_code == 0, backup_result.output
    assert backup_path.exists()


def test_cli_redacts_database_url_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credentialed_url = "postgresql+asyncpg://user:pass123@example.invalid:5432/chatterlore"
    monkeypatch.setenv("DB_URL", credentialed_url)
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("CONTROL_PLANE_ENABLED", "false")
    monkeypatch.setattr(main_module, "upgrade_schema", lambda settings: None)

    show_config = runner.invoke(app, ["show-config"])
    doctor = runner.invoke(app, ["doctor"])
    migrate = runner.invoke(app, ["migrate"])

    assert show_config.exit_code == 0, show_config.output
    assert doctor.exit_code == 0, doctor.output
    assert migrate.exit_code == 0, migrate.output
    combined = show_config.output + doctor.output + migrate.output
    assert "pass123" not in combined
    assert "user:" not in combined
    assert "postgresql+asyncpg://[REDACTED]@example.invalid:5432/chatterlore" in combined


def test_secret_status_and_atomic_rewrap_report_counts_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_env(monkeypatch, tmp_path)
    primary = base64.b64encode(bytes([1]) * 32).decode()
    next_key = base64.b64encode(bytes([2]) * 32).decode()
    monkeypatch.setenv(
        "CONTROL_PLANE_MASTER_KEYS", json.dumps({"primary": primary, "next": next_key})
    )
    monkeypatch.setenv("CONTROL_PLANE_ACTIVE_MASTER_KEY_ID", "primary")
    get_settings.cache_clear()

    async def seed() -> None:
        settings = get_settings()
        upgrade_schema(settings)
        service = ProviderSecretService(
            settings, create_session_factory(settings), ProviderRegistry(settings)
        )
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
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )
        await service.create_credential(
            "managed",
            label="main",
            value="cli-secret-value",
            actor_user_id=111,
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )

    asyncio.run(seed())
    status = runner.invoke(app, ["secrets", "status"])
    rewrap = runner.invoke(app, ["secrets", "rewrap", "--to", "next", "--yes"])

    assert status.exit_code == 0, status.output
    assert "configured_key_ids: next,primary" in status.output
    assert "encrypted_credentials: 1" in status.output
    assert "key_primary: 1" in status.output
    assert rewrap.exit_code == 0, rewrap.output
    assert "Back up the database" in rewrap.output
    assert "Rewrapped 1 encrypted credentials to key next." in rewrap.output
    combined = status.output + rewrap.output
    assert "cli-secret-value" not in combined
    assert primary not in combined
    assert next_key not in combined

    async def inspect() -> None:
        settings = get_settings()
        async with session_scope(create_session_factory(settings)) as session:
            credentials = await ProviderCredentialRepository(session).list_encrypted()
            assert [credential.master_key_id for credential in credentials] == ["next"]

    asyncio.run(inspect())


def test_successful_cli_memory_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_cli_env(monkeypatch, tmp_path)
    scope = Scope(-100701, None)
    seed_cli_message(scope, "Decision: CLI summarize-now covers successful command execution.")
    import_path = tmp_path / "items.jsonl"
    import_path.write_text(
        json.dumps({"content": "CLI imported memory item.", "kind": "manual_note"}) + "\n",
        encoding="utf-8",
    )
    export_path = tmp_path / "memory.jsonl"

    summarize_result = runner.invoke(app, ["summarize-now", "--scope", scope.key])
    import_result = runner.invoke(
        app, ["import-jsonl", "--file", str(import_path), "--chat-id", str(scope.chat_id)]
    )

    async def reject_full_export(
        self: MemoryService, scope: Scope | None = None, fmt: str = "md"
    ) -> str:
        raise AssertionError("CLI export must consume incremental chunks")

    monkeypatch.setattr(MemoryService, "export_memory", reject_full_export)
    export_result = runner.invoke(
        app, ["export-memory", "--format", "jsonl", "--scope", scope.key, "--out", str(export_path)]
    )
    config_result = runner.invoke(app, ["show-config"])

    assert summarize_result.exit_code == 0, summarize_result.output
    assert "Summarized 1 pending messages" in summarize_result.output
    assert import_result.exit_code == 0, import_result.output
    assert "Imported 1 memory items" in import_result.output
    assert export_result.exit_code == 0, export_result.output
    assert export_path.exists()
    assert "CLI imported memory item" in export_path.read_text(encoding="utf-8")
    assert config_result.exit_code == 0, config_result.output
    assert "ai_provider: fake" in config_result.output
    assert "admin_cache_ttl_seconds: 300" in config_result.output
    assert "123:fake" not in config_result.output
