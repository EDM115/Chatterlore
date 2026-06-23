from __future__ import annotations

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
    assert revision == ("0001_initial",)


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
