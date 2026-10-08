"""Offline production restore CLI. There is intentionally no HTTP endpoint."""

from __future__ import annotations

import argparse
import json
import sys

from .backup import RestoreError, offline_restore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline production restore. The application process must be stopped "
            "before this command is used."
        )
    )
    parser.add_argument("--backup", required=True, help="Backup id from the verified manifest.")
    parser.add_argument("--target", required=True, help="Production SQLite database path to replace.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Explicit confirmation. Without this flag the command refuses to run.",
    )
    args = parser.parse_args(argv)
    try:
        result = offline_restore(
            backup_id=args.backup,
            target=args.target,
            confirmed=args.yes,
        )
    except RestoreError as exc:
        print(f"RESTORE_BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("RESTORE_COMPLETE: run `python -m app.system.startup` before starting uvicorn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
