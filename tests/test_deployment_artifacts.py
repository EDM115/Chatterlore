from __future__ import annotations

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


def test_dockerignore_excludes_local_state_and_tests_from_image_context() -> None:
    entries = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {".env", "data", "tests", ".venv", ".git"}.issubset(entries)


def test_github_actions_ci_runs_quality_migration_and_docker_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for expected in [
        "uv sync --extra dev",
        "uv run ruff format --check .",
        "uv run ruff check .",
        "uv run ty check",
        "uv run pytest",
        "uv run alembic upgrade head",
        "uv run pzp-bot --help",
        "uv run pzp-bot doctor",
        "docker build --tag pzp-ai:ci .",
        "docker run --rm",
    ]:
        assert expected in workflow


def test_alembic_root_prefers_runtime_working_directory(monkeypatch, tmp_path) -> None:
    from pzp_ai_bot.db.session import alembic_project_root

    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nscript_location = alembic\n", encoding="utf-8"
    )
    (tmp_path / "alembic").mkdir()
    monkeypatch.chdir(tmp_path)

    assert alembic_project_root() == tmp_path
