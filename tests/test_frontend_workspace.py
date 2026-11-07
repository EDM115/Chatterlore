from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"


def test_frontend_workspace_declares_the_reviewed_frozen_toolchain() -> None:
    package_path = WEB_ROOT / "package.json"

    assert package_path.is_file(), "Plan 021 Phase 2 must provide web/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))

    assert (ROOT / ".node-version").read_text(encoding="utf-8").strip() == "26"
    assert package["packageManager"] == "pnpm@11.28.11"
    assert package["devEngines"] == {
        "runtime": {"name": "node", "version": "26", "onFail": "download"}
    }
    assert package["engines"] == {"node": "^26"}
    assert set(package["dependencies"]) == {"@nuxt/ui", "nuxt", "tailwindcss", "vue", "vue-router"}
    assert {"@nuxt/test-utils", "@playwright/test", "oxfmt", "oxlint", "vitest", "vue-tsc"} <= set(
        package["devDependencies"]
    )
    importer = yaml.safe_load((WEB_ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8"))[
        "importers"
    ]["."]
    for group in ("dependencies", "devDependencies"):
        for name, specifier in package[group].items():
            assert importer[group][name]["specifier"] == specifier
    assert importer["devDependencies"]["node"]["specifier"] == "runtime:26"
    assert set(package["scripts"]) >= {
        "build",
        "format:check",
        "lint",
        "smoke:ssr",
        "start",
        "typecheck",
        "test",
        "test:e2e",
        "openapi:types",
        "openapi:check",
        "scan:generated",
    }
    assert (WEB_ROOT / "pnpm-lock.yaml").is_file()


def test_control_plane_openapi_snapshot_matches_the_fastapi_exporter() -> None:
    schema_path = WEB_ROOT / "openapi" / "control-plane.openapi.json"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "export_control_plane_openapi.py"),
        "--check",
        str(schema_path),
    ]

    result = subprocess.run(  # noqa: S603 - command contains only repository-owned absolute paths
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_ci_runs_the_frozen_frontend_quality_and_ssr_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for expected in [
        "pnpm/setup@v3",
        "working-directory: web",
        "pnpm install --frozen-lockfile",
        "pnpm format:check",
        "pnpm lint",
        "pnpm typecheck",
        "pnpm test",
        "pnpm openapi:check",
        "pnpm build",
        "pnpm scan:generated",
        "pnpm smoke:ssr",
    ]:
        assert expected in workflow
    assert "pnpm generate" not in workflow


def test_docker_context_excludes_frontend_dependencies_and_generated_artifacts() -> None:
    ignored_paths = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert {
        "web/node_modules",
        "web/.nuxt",
        "web/.output",
        "web/playwright-report",
        "web/test-results",
    } <= set(ignored_paths)
