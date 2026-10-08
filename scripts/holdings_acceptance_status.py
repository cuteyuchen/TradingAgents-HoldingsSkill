"""Read-only P5 evidence inventory. Never seed a real account or declare user acceptance."""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REAL_SOURCES = {"fuyao", "eastmoney", "sina", "akshare", "tushare", "ths", "xtquant"}


def inventory(database: Path) -> dict:
    # mode=ro is essential: an incorrect path must not create a fresh database.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"analysis_runs", "portfolio_snapshots", "decision_memories", "learning_hypotheses", "learning_validations", "learning_context_references", "trade_ledger_entries"}
        counts = {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in sorted(required & tables)}
        days, excluded = set(), 0
        if {"analysis_runs", "portfolio_snapshots"} <= tables:
            runs = connection.execute("SELECT r.structured_result_json, r.status, s.status AS snapshot_status FROM analysis_runs r JOIN portfolio_snapshots s ON s.id=r.portfolio_snapshot_id")
            for row in runs:
                saved = json.loads(row["structured_result_json"] or "{}")
                market = saved.get("market_snapshot") or {}
                quotes = list((market.get("quotes") or {}).values())
                workflow = saved.get("workflow") or {}
                stamp = market.get("final_quote_refresh_at")
                normal = (row["status"] == "completed" and row["snapshot_status"] == "confirmed"
                    and workflow.get("learning_context") is not None and saved.get("result", {}).get("learning_usage_report") is not None
                    and market.get("final_quote_refresh_status") == "ok" and quotes
                    and all(str(quote.get("source") or quote.get("provider") or "").lower() in REAL_SOURCES
                        and quote.get("quality_status") in {"VALID", "DEGRADED"} and not quote.get("stale") for quote in quotes))
                try:
                    instant = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    instant = instant if instant.tzinfo else instant.replace(tzinfo=UTC)
                    day = instant.astimezone(ZoneInfo("Asia/Shanghai")).date()
                except (TypeError, ValueError):
                    normal = False
                if normal and day.weekday() < 5:
                    # Calendar verification distinguishes a normal weekday from a market holiday.
                    calendar = connection.execute("SELECT is_open FROM trading_calendar WHERE market='CN' AND trade_date=? LIMIT 1", (day.isoformat(),)).fetchone() if "trading_calendar" in tables else None
                    if calendar and calendar[0]:
                        days.add(day.isoformat())
                        continue
                excluded += 1
        return {"status": "PENDING", "database_mode": "READ_ONLY", "normal_observation_days": len(days),
            "observation_dates": sorted(days), "minimum_trading_days": 10,
            "observation_status": "READY_FOR_REVIEW" if len(days) >= 10 else "PENDING",
            "excluded_or_unproven_runs": excluded, "record_counts": counts, "missing_tables": sorted(required-tables),
            "account_reconciliation": "REQUIRES_REAL_ACCOUNT_CONFIRMATION", "user_daily_use_acceptance": "REQUIRES_USER_ACCEPTANCE",
            "learning_effect": "CHECK_INDEPENDENT_PAIRED_VALIDATIONS; COUNTS_ALONE_ARE_NOT_PROOF"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "backend/data/advisor.db")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(inventory(args.db), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
