from __future__ import annotations

import argparse
import json
from pathlib import Path

from chatterlore.control_plane.app import create_control_plane_schema_app


def control_plane_openapi_json() -> str:
    schema = create_control_plane_schema_app().openapi()
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the deterministic control-plane OpenAPI schema."
    )
    parser.add_argument("--check", action="store_true", help="Fail when the target differs.")
    parser.add_argument("target", type=Path)
    args = parser.parse_args()

    expected = control_plane_openapi_json()
    if args.check:
        if not args.target.is_file() or args.target.read_text(encoding="utf-8") != expected:
            print(f"{args.target} is not up to date")
            return 1
        return 0

    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
