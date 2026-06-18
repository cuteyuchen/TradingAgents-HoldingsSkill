"""Alpha computation: raw return of a holding minus CSI 300 return over the same window.

Called from the upload path: for each holding, compare this run's advice price
against the previous same-code snapshot's price, then subtract the benchmark
(CSI 300) move over the same calendar span.
"""
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BenchmarkPrice, HoldingSnapshot, Run


def _prev_snapshot(db: Session, code: str, run_timestamp) -> HoldingSnapshot | None:
    """Most recent same-code snapshot strictly before this run."""
    return (
        db.query(HoldingSnapshot)
        .join(Run, HoldingSnapshot.run_id == Run.id)
        .filter(HoldingSnapshot.code == code)
        .filter(Run.timestamp < run_timestamp)
        .order_by(Run.timestamp.desc(), HoldingSnapshot.id.desc())
        .first()
    )


def _benchmark_return(db: Session, from_date: str, to_date: str) -> float | None:
    """CSI 300 return between two YYYY-MM-DD dates (closest available closes)."""
    from_row = (
        db.query(BenchmarkPrice)
        .filter(BenchmarkPrice.date <= from_date)
        .order_by(BenchmarkPrice.date.desc())
        .first()
    )
    to_row = (
        db.query(BenchmarkPrice)
        .filter(BenchmarkPrice.date <= to_date)
        .order_by(BenchmarkPrice.date.desc())
        .first()
    )
    if not from_row or not to_row or from_row.close in (None, 0):
        return None
    return (to_row.close - from_row.close) / from_row.close


def compute_alpha_for_holding(
    db: Session,
    code: str,
    current_price: float | None,
    run_timestamp,
) -> dict:
    """Return {raw_return, benchmark_return, alpha}. Missing data → None fields."""
    result = {"raw_return": None, "benchmark_return": None, "alpha": None}
    if current_price is None:
        return result

    prev = _prev_snapshot(db, code, run_timestamp)
    # Exclude the just-inserted snapshot: _prev_snapshot runs before insert in the upload flow.
    # Guard: if prev.price equals current_price and same run, skip.
    if not prev or prev.price in (None, 0):
        return result

    raw_return = (current_price - prev.price) / prev.price
    result["raw_return"] = raw_return

    prev_date = prev.run.timestamp.strftime("%Y-%m-%d") if prev.run else None
    curr_date = run_timestamp.strftime("%Y-%m-%d") if hasattr(run_timestamp, "strftime") else str(run_timestamp)[:10]
    bench = None
    if prev_date and curr_date:
        bench = _benchmark_return(db, prev_date, curr_date)
    result["benchmark_return"] = bench

    if bench is not None:
        result["alpha"] = raw_return - bench
    else:
        # Benchmark missing → mark per alpha_window_fallback; alpha unknown.
        result["alpha"] = None

    return result
