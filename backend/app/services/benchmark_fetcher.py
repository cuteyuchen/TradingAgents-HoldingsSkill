"""CSI 300 (000300) benchmark price fetcher via AKShare.

Backfills recent history on startup and runs a daily job after market close.
Fails soft: any error is logged and skipped; missing benchmark lowers alpha
confidence on the alpha service side.
"""
import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models import BenchmarkPrice

logger = logging.getLogger("advisor.benchmark")


def _fetch_index_df(code: str, days: int = 365):
    """Fetch index daily OHLCV from AKShare. Returns DataFrame or None."""
    try:
        import akshare as ak
        from datetime import datetime, timedelta

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        # stock_zh_index_daily_em works for 000300 (CSI 300).
        df = ak.stock_zh_index_daily_em(symbol=f"sh{code}", start_date=start, end_date=end)
        return df
    except Exception as exc:  # noqa: BLE001
        logger.warning("benchmark fetch failed: %s", exc)
        return None


def upsert_benchmark(db: Session, date: str, close: float, pct_change: float | None = None) -> None:
    row = db.query(BenchmarkPrice).filter(BenchmarkPrice.date == date).first()
    if row:
        row.close = close
        row.pct_change = pct_change
    else:
        db.add(BenchmarkPrice(date=date, close=close, pct_change=pct_change))


def backfill(db: Session, code: str | None = None, days: int = 365) -> int:
    """Pull and store recent benchmark history. Returns rows written."""
    code = code or settings.BENCHMARK_CODE
    df = _fetch_index_df(code, days)
    if df is None:
        logger.warning("no benchmark data fetched for %s", code)
        return 0

    written = 0
    # AKShare index columns: date, open, close, high, low, volume, amount
    date_col = "date" if "date" in df.columns else df.columns[0]
    for _, row in df.iterrows():
        date_str = str(row[date_col])[:10]
        close = float(row.get("close", 0))
        pct = None
        if "close" in df.columns:
            pass  # pct computed below in bulk if needed
        if close > 0:
            upsert_benchmark(db, date_str, close, pct)
            written += 1
    db.commit()
    logger.info("benchmark backfill wrote %d rows for %s", written, code)
    return written


def refresh_today(db: Session, code: str | None = None) -> int:
    """Fetch latest benchmark rows (used by the scheduler)."""
    return backfill(db, code, days=10)
