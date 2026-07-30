from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"


def test_frontend_workspace_declares_the_reviewed_frozen_toolchain() -> None:
    package_path = WEB_ROOT / "package.json"

    assert package_path.is_file(), "Plan 021 Phase 2 must provide web/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))

    assert (ROOT / ".node-version").read_text(encoding="utf-8").strip() == "24.18.0"
    assert package["packageManager"] == "pnpm@11.18.0"
    assert package["devEngines"] == {
        "runtime": {"name": "node", "version": "24.18.0", "onFail": "download"}
    }
    assert package["engines"] == {"node": "24.18.0"}
    assert package["dependencies"] == {
        "@nuxt/ui": "4.10.0",
        "nuxt": "4.5.1",
        "tailwindcss": "4.3.3",
        "vue": "3.5.40",
        "vue-router": "5.2.0",
    }
    assert package["devDependencies"] == {
        "@nuxt/test-utils": "4.1.0",
        "@playwright/test": "1.62.0",
        "@testing-library/vue": "8.1.0",
        "@types/node": "24.13.3",
        "@vue/test-utils": "2.4.11",
        "happy-dom": "20.11.1",
        "openapi-typescript": "7.13.0",
        "oxfmt": "0.61.0",
        "oxlint": "1.76.0",
        "oxlint-tsgolint": "7.0.2001",
        "typescript": "6.0.3",
        "vitest": "4.1.10",
        "vue-tsc": "3.3.8",
    }
    assert set(package["scripts"]) >= {
        "format:check",
        "lint",
        "typecheck",
        "test",
        "test:e2e",
        "generate",
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


def test_ci_runs_the_frozen_frontend_quality_and_generation_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for expected in [
        "pnpm/action-setup@8912a9102ac27614460f54aedde9e1e7f9aec20d",
        "version: 11.18.0",
        "pnpm install --frozen-lockfile",
        "pnpm format:check",
        "pnpm lint",
        "pnpm typecheck",
        "pnpm test",
        "pnpm openapi:check",
        "pnpm generate",
        "pnpm scan:generated",
    ]:
        assert expected in workflow


def test_docker_context_excludes_frontend_dependencies_and_generated_artifacts() -> None:
    ignored_paths = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert {
        "web/node_modules",
        "web/.nuxt",
        "web/.output",
        "web/playwright-report",
        "web/test-results",
    } <= set(ignored_paths)
