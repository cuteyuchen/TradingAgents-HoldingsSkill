"""Real Phase M performance benchmark: 500 symbols x 60 decision dates.

Seeds a private SQLite database with a full PIT fact set and runs one
DETERMINISTIC_RECOMPUTE cohort.  It records SQL query count, wall time and
the process working-set peak so the acceptance report never relies on
invented numbers.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import Base  # noqa: E402
from app.history.models import (  # noqa: E402
    EtfMetadataHistory,
    FundamentalReport,
    PriceBasisMetadata,
    SecurityClassificationDaily,
    SecurityLifecycleEvent,
    SecurityTradingStatusDaily,
    SecurityValuationDaily,
)
from app.market_engine_models import AllAMedianIndexDaily, DailyBarCache  # noqa: E402
from app.market_models import TradingCalendar  # noqa: E402


def business_days(start: date, count: int) -> list[date]:
    result: list[date] = []
    day = start
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day += timedelta(days=1)
    return result


def at07(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 7, 0)


def seed_pit_dataset(db: Session, *, stock_count: int, decision_dates: int, warmup_days: int) -> dict[str, int]:
    days = business_days(date(2023, 1, 2), warmup_days)
    cohort = days[-decision_dates:]
    codes = [f"{600000 + index:06d}" for index in range(stock_count)]
    listed = days[0] - timedelta(days=700)

    db.add_all([
        TradingCalendar(
            market="CN",
            trade_date=day,
            is_open=True,
            previous_trade_date=days[index - 1] if index else None,
            next_trade_date=days[index + 1] if index + 1 < len(days) else None,
        )
        for index, day in enumerate(days)
    ])
    db.add_all([
        SecurityLifecycleEvent(
            market="CN",
            exchange="SSE",
            code=code,
            security_type="STOCK",
            security_name=f"BENCH-{code}",
            event_type="LISTED",
            effective_date=listed,
            source="benchmark-import",
            source_ref=f"bench-lifecycle-{code}",
            source_available_at=at07(days[0]),
            quality_status="VALID",
        )
        for code in codes
    ])
    db.add_all([
        AllAMedianIndexDaily(
            market="CN",
            trade_date=day,
            median_return=0.001,
            index_value=1000.0 + index,
            eligible_count=stock_count,
            quality_status="VALID",
            calculation_version="market-engine-v1",
            available_at=at07(day),
        )
        for index, day in enumerate(days)
    ])

    calendar_rows: list[dict] = []
    for day in cohort:
        for code_index, code in enumerate(codes):
            calendar_rows.append({
                "market": "CN",
                "code": code,
                "trade_date": day,
                "classification": "NORMAL",
                "source": "benchmark-import",
                "source_ref": f"bench-class-{code}-{day.isoformat()}",
                "source_available_at": at07(day),
                "quality_status": "VALID",
            })
    db.execute(insert(SecurityClassificationDaily), calendar_rows)

    status_rows: list[dict] = []
    valuation_rows: list[dict] = []
    basis_rows: list[dict] = []
    for day in cohort:
        for code_index, code in enumerate(codes):
            status_rows.append({
                "market": "CN",
                "code": code,
                "trade_date": day,
                "status": "TRADING",
                "source": "benchmark-import",
                "source_ref": f"bench-status-{code}-{day.isoformat()}",
                "source_available_at": at07(day),
                "quality_status": "VALID",
            })
            valuation_rows.append({
                "market": "CN",
                "code": code,
                "trade_date": day,
                "pe_ttm": 15.0 + (code_index % 10),
                "pb": 1.5 + (code_index % 5) * 0.2,
                "dividend_yield": 0.01 + (code_index % 3) * 0.01,
                "source": "benchmark-import",
                "source_ref": f"bench-val-{code}-{day.isoformat()}",
                "source_available_at": at07(day),
                "quality_status": "VALID",
            })
            basis_rows.append({
                "market": "CN",
                "code": code,
                "trade_date": day,
                "basis": "QFQ",
                "source": "benchmark-import",
                "source_ref": f"bench-basis-{code}-{day.isoformat()}",
                "source_available_at": at07(day),
                "quality_status": "VALID",
            })
    db.execute(insert(SecurityTradingStatusDaily), status_rows)
    db.execute(insert(SecurityValuationDaily), valuation_rows)
    db.execute(insert(PriceBasisMetadata), basis_rows)

    bar_rows: list[dict] = []
    bar_count = 0
    for day_index, day in enumerate(days):
        for code_index, code in enumerate(codes):
            base = 10.0 + code_index * 0.3
            close = base * (1.0 + 0.0008 * day_index)
            prev_close = base * (1.0 + 0.0008 * max(0, day_index - 1))
            bar_rows.append({
                "market": "CN",
                "exchange": "SSE",
                "code": code,
                "trade_date": day,
                "open": close * 0.995,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "prev_close": prev_close,
                "volume": 1_000_000.0 + code_index * 10_000.0,
                "amount": close * (1_000_000.0 + code_index * 10_000.0),
                "turnover_rate": 0.01 + code_index * 0.0001,
                "adjustment": "QFQ",
                "provider": "benchmark",
                "available_at": at07(day),
                "quality_status": "VALID",
            })
            bar_count += 1
            if len(bar_rows) >= 50_000:
                db.execute(insert(DailyBarCache), bar_rows)
                bar_rows = []
    if bar_rows:
        db.execute(insert(DailyBarCache), bar_rows)

    db.add_all([
        FundamentalReport(
            market="CN",
            code=code,
            report_period=date(2024, 12, 31),
            report_type="ANNUAL",
            published_at=at07(cohort[0]),
            source_available_at=at07(cohort[0]),
            revision_number=0,
            source="benchmark-import",
            source_ref=f"bench-fund-{code}-v0",
            roe=0.08 + code_index * 0.001,
            revenue_yoy=0.05 + code_index * 0.002,
            net_profit_yoy=0.03 + code_index * 0.003,
            operating_cash_flow=10_000.0 + code_index * 100.0,
            gross_margin=0.2 + code_index * 0.001,
            net_profit=100.0 + code_index,
            quality_status="VALID",
        )
        for code_index, code in enumerate(codes)
    ])
    db.flush()
    return {
        "calendar_days": len(days),
        "cohort_days": len(cohort),
        "symbols": len(codes),
        "classification_rows": len(calendar_rows),
        "status_rows": len(status_rows),
        "valuation_rows": len(valuation_rows),
        "basis_rows": len(basis_rows),
        "bar_rows": bar_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=int, default=500)
    parser.add_argument("--dates", type=int, default=60)
    parser.add_argument("--warmup-days", type=int, default=750)
    parser.add_argument("--output", default=os.path.join(BACKEND_ROOT, "benchmarks", "phase_m_recompute_500x60.json"))
    parser.add_argument("--scope", default="CANDIDATE")
    args = parser.parse_args()

    import app.candidates.models  # noqa: F401
    import app.governance.models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.portfolio_models  # noqa: F401
    import app.research.models  # noqa: F401
    import app.trigger_models  # noqa: F401

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    print(f"[benchmark] seeding {args.symbols} symbols x {args.dates} dates "
          f"(warmup {args.warmup_days} trading days)...", file=sys.stderr, flush=True)
    seed_start = time.perf_counter()
    counts = seed_pit_dataset(
        db,
        stock_count=args.symbols,
        decision_dates=args.dates,
        warmup_days=args.warmup_days,
    )
    db.commit()
    seed_seconds = time.perf_counter() - seed_start
    print(f"[benchmark] seed done in {seed_seconds:.2f}s", file=sys.stderr, flush=True)

    from app.research.replay import load_replay_facts

    calendar = db.execute(
        select(TradingCalendar.trade_date)
        .where(TradingCalendar.market == "CN", TradingCalendar.is_open.is_(True))
        .order_by(TradingCalendar.trade_date.asc())
    ).scalars().all()
    cohort = list(calendar[-args.dates:])
    select_statements: list[str] = []

    def count_select(conn, cursor, statement, parameters, context, executemany):
        text = str(statement).lower().lstrip()
        if text.startswith("select"):
            select_statements.append(text)

    event.listen(engine, "before_cursor_execute", count_select)
    print("[benchmark] running deterministic recompute...", file=sys.stderr, flush=True)
    rss_peak_mb: list[float] = [0.0]
    stop_sampler = threading.Event()

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def memory_sampler() -> None:
        try:
            psapi = ctypes.windll.psapi
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ProcessMemoryCounters),
                ctypes.c_size_t,
            ]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            handle = kernel32.GetCurrentProcess()
            counters = ProcessMemoryCounters()
            while not stop_sampler.is_set():
                counters.cb = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    rss_peak_mb[0] = max(rss_peak_mb[0], counters.WorkingSetSize / 1024 / 1024)
                stop_sampler.wait(0.05)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return

    sampler_thread = threading.Thread(target=memory_sampler, daemon=True)
    sampler_thread.start()
    recompute_start = time.perf_counter()
    facts = load_replay_facts(
        db,
        scope=args.scope,
        replay_mode="DETERMINISTIC_RECOMPUTE",
        start_date=cohort[0],
        end_date=cohort[-1],
        parameter_snapshot=None,
        parameter_set_version="LEGACY_PRE_GOVERNANCE",
        config_hash="benchmark-legacy-config-hash",
    )
    wall_seconds = time.perf_counter() - recompute_start
    print(f"[benchmark] recompute done in {wall_seconds:.2f}s", file=sys.stderr, flush=True)
    stop_sampler.set()
    sampler_thread.join(timeout=1.0)
    try:
        psapi = ctypes.windll.psapi
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_size_t,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            rss_peak_mb[0] = max(rss_peak_mb[0], counters.WorkingSetSize / 1024 / 1024)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    event.remove(engine, "before_cursor_execute", count_select)

    summary = facts.get("recompute_summary") or {}
    manifest = facts.get("recompute_manifest") or {}
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": args.scope,
        "symbols": args.symbols,
        "decision_dates": args.dates,
        "warmup_trading_days": args.warmup_days,
        "seed_row_counts": counts,
        "seed_seconds": round(seed_seconds, 3),
        "wall_seconds": round(wall_seconds, 3),
        "rss_peak_mb": round(rss_peak_mb[0], 2),
        "sql_select_count": len(select_statements),
        "dataset_query_count": summary.get("query_count"),
        "date_count": summary.get("date_count"),
        "capability": summary.get("capability") or manifest.get("capability"),
        "deterministic_hash": summary.get("deterministic_hash"),
        "missing_inputs": manifest.get("missing_inputs") or [],
        "partial_inputs": manifest.get("partial_inputs") or [],
        "limitations": manifest.get("limitations") or [],
    }
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
