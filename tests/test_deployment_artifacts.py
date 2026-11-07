from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTAINER_ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - asserted Compose listener contract


def load_compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


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
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
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
        "uv run pytest",
        "uv run alembic upgrade head",
        "uv run pzp-bot --help",
        "uv run pzp-bot doctor",
        "docker build --tag pzp-ai:ci .",
        "docker build --tag pzp-ai-web:ci ./web",
        "docker run --rm",
    ]:
        assert expected in workflow
    assert "--cov" not in workflow
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


def test_ci_and_docker_use_latest_uv_with_frozen_project_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "https://astral.sh/uv/install.sh" in dockerfile
    assert "UV_VERSION" not in dockerfile
    setup_uv = next(
        step
        for step in yaml.safe_load(workflow)["jobs"]["quality"]["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert "version" not in setup_uv.get("with", {})


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


def test_compose_exposes_only_caddy_and_keeps_upstreams_independent() -> None:
    services = load_compose()["services"]

    assert set(services) == {"bot", "frontend", "proxy"}
    assert services["bot"]["command"] == ["pzp-bot", "webhook"]
    assert services["bot"]["expose"] == [8080]
    assert "ports" not in services["bot"]
    assert services["frontend"]["build"] == {"context": "./web", "dockerfile": "Dockerfile"}
    assert services["frontend"]["expose"] == [3000]
    assert "ports" not in services["frontend"]
    assert services["frontend"]["environment"] == {
        "NITRO_HOST": CONTAINER_ALL_INTERFACES,
        "NITRO_PORT": 3000,
        "NODE_ENV": "production",
    }
    assert services["proxy"]["image"] == "caddy:2-alpine"
    assert services["proxy"]["ports"] == [
        "${PZP_HTTP_PORT:-80}:80",
        "${PZP_HTTPS_PORT:-443}:443",
        "${PZP_HTTPS_PORT:-443}:443/udp",
    ]
    assert services["proxy"]["depends_on"] == {
        "bot": {"condition": "service_started"},
        "frontend": {"condition": "service_started"},
    }
    assert all(
        "restart" not in dependency for dependency in services["proxy"]["depends_on"].values()
    )


def test_frontend_image_is_frozen_multi_stage_and_non_root() -> None:
    dockerfile = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
    ignored = set((ROOT / "web" / ".dockerignore").read_text(encoding="utf-8").splitlines())
    package = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "FROM node:26-bookworm-slim AS dependencies" in dockerfile
    assert "FROM dependencies AS build" in dockerfile
    assert "FROM node:26-bookworm-slim AS runtime" in dockerfile
    assert "https://get.pnpm.io/install.sh" in dockerfile
    assert "PNPM_VERSION=11.28.11" in dockerfile
    assert "NODE_OPTIONS=--max-old-space-size=1024" in dockerfile
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "RUN pnpm build" in dockerfile
    assert "COPY --from=build --chown=node:node /app/.output ./.output" in dockerfile
    assert "USER node" in dockerfile
    assert 'CMD ["node", ".output/server/index.mjs"]' in dockerfile
    assert {".env", ".nuxt", ".output", "node_modules", "test-results"} <= ignored
    assert package["scripts"]["format:check"] == "oxfmt --check ."
    assert package["packageManager"] == "pnpm@11.28.11"
    assert package["devEngines"]["runtime"]["version"] == "26"
    assert "          working-directory: web" in workflow


def test_default_tests_do_not_install_or_configure_code_coverage() -> None:
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert not any(
        dependency.startswith("pytest-cov")
        for dependency in project["project"]["optional-dependencies"]["dev"]
    )
    assert "coverage" not in project["tool"]


def test_caddy_reserves_backend_paths_and_strips_frontend_credentials() -> None:
    caddyfile = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    proxy_environment = load_compose()["services"]["proxy"]["environment"]

    assert "{$CADDY_SITE_ADDRESS:https://localhost}" in caddyfile
    assert proxy_environment["CADDY_SITE_ADDRESS"] == (
        "${CADDY_SITE_ADDRESS:-${CONTROL_PLANE_PUBLIC_ORIGIN:-https://localhost}}"
    )
    assert 'header >Strict-Transport-Security "max-age=31536000; includeSubDomains"' in caddyfile
    assert "@insecure_forwarded header X-Forwarded-Proto http" in caddyfile
    assert "redir @insecure_forwarded https://{host}{uri} permanent" in caddyfile
    assert (
        "@backend path /api/control /api/control/* /auth /auth/* /telegram/webhook /health"
        in caddyfile
    )
    assert "@backend path /api /api/*" not in caddyfile
    assert "handle @backend" in caddyfile
    assert "reverse_proxy bot:8080" in caddyfile
    assert "reverse_proxy frontend:3000" in caddyfile
    assert "header_up -Cookie" in caddyfile
    assert "header_up -Authorization" in caddyfile
    assert "header_up -X-CSRF-Token" in caddyfile
    assert caddyfile.index("handle @backend") < caddyfile.index("reverse_proxy frontend:3000")
    assert "log {" not in caddyfile
