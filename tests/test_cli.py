from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pzp_ai_bot.config import get_settings
from pzp_ai_bot.main import app

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
    return db_path


def test_migrate_uses_alembic_revision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = configure_cli_env(monkeypatch, tmp_path)

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 0, result.output
    with sqlite3.connect(db_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
    assert revision == ("0006_phase6_product_completeness",)


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


def test_phase6_memory_status_and_telegram_json_import_commands(
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
    assert "Imported 1 Telegram Desktop messages" in import_result.output
    assert status_result.exit_code == 0, status_result.output
    assert "messages: 1" in status_result.output
    assert "learned: 1" in status_result.output


def test_phase5_runtime_audit_and_db_maintenance_commands(
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
