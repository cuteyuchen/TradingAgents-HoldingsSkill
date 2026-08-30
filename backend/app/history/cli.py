"""Operator CLI for Phase L historical data preparation.

Usage:
    python -m app.history.cli coverage --start-date 2025-01-01 --end-date 2025-12-31
    python -m app.history.cli sync --data-type security_lifecycle --import-json facts.json
    python -m app.history.cli availability
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Any

from ..database import SessionLocal
from .availability import history_manifest_items
from .coverage import historical_data_coverage
from .sync import run_history_sync


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _coverage(args: argparse.Namespace) -> None:
    with SessionLocal() as db:
        _print(
            historical_data_coverage(
                db,
                start_date=args.start_date,
                end_date=args.end_date,
                data_type=args.data_type,
                market=args.market,
            )
        )


def _availability(args: argparse.Namespace) -> None:
    with SessionLocal() as db:
        _print(
            history_manifest_items(
                db,
                start_date=args.start_date,
                end_date=args.end_date,
                market=args.market,
            )
            or {"status": "LEAKAGE_BLOCKED", "reason": "historical_pit_tables_unavailable"}
        )


def _sync(args: argparse.Namespace) -> None:
    rows: list[dict[str, Any]] = []
    if args.import_json:
        with open(args.import_json, encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            rows = payload["rows"]
        else:
            raise SystemExit("import_json must be a list or {'rows': [...]}")
    with SessionLocal() as db:
        _print(
            run_history_sync(
                db,
                data_type=args.data_type,
                start_date=args.start_date,
                end_date=args.end_date,
                market=args.market,
                provider=args.provider,
                source=args.source,
                rows=rows,
                dry_run=args.dry_run,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase L historical data CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    coverage = subparsers.add_parser("coverage", help="Report historical data coverage")
    coverage.add_argument("--start-date", type=_date)
    coverage.add_argument("--end-date", type=_date)
    coverage.add_argument("--data-type")
    coverage.add_argument("--market", default="CN")
    coverage.set_defaults(func=_coverage)

    availability = subparsers.add_parser("availability", help="Report replay availability")
    availability.add_argument("--start-date", type=_date)
    availability.add_argument("--end-date", type=_date)
    availability.add_argument("--market", default="CN")
    availability.set_defaults(func=_availability)

    sync = subparsers.add_parser("sync", help="Run an idempotent historical sync/import")
    sync.add_argument("--data-type", required=True)
    sync.add_argument("--start-date", type=_date)
    sync.add_argument("--end-date", type=_date)
    sync.add_argument("--market", default="CN")
    sync.add_argument("--provider")
    sync.add_argument("--source")
    sync.add_argument("--import-json")
    sync.add_argument("--dry-run", action="store_true")
    sync.set_defaults(func=_sync)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()


__all__ = ["main"]
