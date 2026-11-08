from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"


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
