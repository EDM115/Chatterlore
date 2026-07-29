from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_is_multi_stage_non_root_and_health_checked() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim AS builder" in dockerfile
    assert "FROM python:3.12-slim AS runtime" in dockerfile
    assert "COPY --from=builder" in dockerfile
    assert "USER pzp" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "pzp-bot doctor" in dockerfile
    assert "COPY ." not in dockerfile


def test_dockerfile_installs_the_frozen_production_lock() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml uv.lock README.md ./" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/venv" in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "uv pip install" not in dockerfile
    assert "pip install --no-cache-dir uv\n" not in dockerfile


def test_dockerignore_excludes_local_state_and_tests_from_image_context() -> None:
    entries = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {".env", "data", "tests", ".venv", ".git"}.issubset(entries)


def test_github_actions_ci_runs_quality_migration_and_docker_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for expected in [
        "uv lock --check",
        "uv sync --frozen --extra dev",
        "uv run ruff format --check .",
        "uv run ruff check .",
        "uv run ty check",
        "uv run pytest --cov=pzp_ai_bot --cov-branch --cov-report=term-missing",
        "uv run alembic upgrade head",
        "uv run pzp-bot --help",
        "uv run pzp-bot doctor",
        "docker build --tag pzp-ai:ci .",
        "docker run --rm",
    ]:
        assert expected in workflow
    assert "\n        run: uv run pytest\n" not in workflow
    assert (
        """      - name: Migration check
        env:
          DB_URL: sqlite+aiosqlite:///${{ runner.temp }}/pzp-ai-migration-check.db
        run: |
          uv run alembic upgrade head
          uv run alembic check
"""
        in workflow
    )


def test_ci_and_docker_pin_the_same_uv_version() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    docker_match = re.search(r"^ARG UV_VERSION=(\S+)$", dockerfile, re.MULTILINE)
    workflow_match = re.search(r"^\s+version: \"([^\"]+)\"$", workflow, re.MULTILINE)
    assert docker_match is not None
    assert workflow_match is not None
    assert docker_match.group(1) == workflow_match.group(1) == "0.12.0"


def test_cli_reference_covers_every_registered_typer_command() -> None:
    from typer.main import get_command

    from pzp_ai_bot.main import app

    command = get_command(app)
    commands = getattr(command, "commands", {})
    docs = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")
    missing = sorted(name for name in commands if f"`pzp-bot {name}" not in docs)

    assert not missing, f"CLI reference is missing registered commands: {', '.join(missing)}"


def test_alembic_root_prefers_runtime_working_directory(monkeypatch, tmp_path) -> None:
    from pzp_ai_bot.db.session import alembic_project_root

    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nscript_location = alembic\n", encoding="utf-8"
    )
    (tmp_path / "alembic").mkdir()
    monkeypatch.chdir(tmp_path)

    assert alembic_project_root() == tmp_path
