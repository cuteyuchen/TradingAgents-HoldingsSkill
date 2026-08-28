"""Local daily-bar cache lifecycle for the deterministic Market Engine.

Network providers are used only by explicit bootstrap/sync jobs.  Calculation
requests read this cache and never fan out one HTTP request per security.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market.engine.history import NormalizedDailyBar
from ..market_engine_models import DailyBarCache
from ..market_models import TradingCalendar


def load_daily_bars(
    db: Session,
    codes: Iterable[str],
    *,
    trade_date: date | None = None,
    available_at: datetime | None = None,
    limit: int = 260,
    adjustment: str = "QFQ",
) -> list[dict[str, Any]]:
    """Read normalized bars from SQLite, bounded by replay time."""

    requested = list(dict.fromkeys(str(code) for code in codes if code))
    if not requested:
        return []
    filters = [
        DailyBarCache.market == "CN",
        DailyBarCache.code.in_(requested),
        DailyBarCache.adjustment == adjustment.upper(),
        DailyBarCache.quality_status.in_(("VALID", "DEGRADED")),
    ]
    if trade_date is not None:
        filters.append(DailyBarCache.trade_date <= trade_date)
    if available_at is not None:
        filters.append(
            (DailyBarCache.available_at.is_(None)) | (DailyBarCache.available_at <= available_at)
        )
    ranked = (
        select(
            DailyBarCache.id.label("bar_id"),
            func.row_number()
            .over(
                partition_by=DailyBarCache.code,
                order_by=DailyBarCache.trade_date.desc(),
            )
            .label("row_number"),
        )
        .where(*filters)
        .subquery()
    )
    rows = db.execute(
        select(DailyBarCache)
        .join(ranked, ranked.c.bar_id == DailyBarCache.id)
        .where(ranked.c.row_number <= max(1, int(limit)))
        .order_by(DailyBarCache.code.asc(), DailyBarCache.trade_date.desc())
    ).scalars().all()
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append(
            {
                "code": row.code,
                "market": row.market,
                "exchange": row.exchange,
                "trade_date": row.trade_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "prev_close": row.prev_close,
                "volume": row.volume,
                "amount": row.amount,
                "turnover_rate": row.turnover_rate,
                "adjustment": row.adjustment,
                "provider": row.provider,
                "fetched_at": row.fetched_at,
                "available_at": row.available_at,
                "quality_status": row.quality_status,
                "metadata": row.metadata_json or {},
            }
        )
    output.sort(key=lambda item: (item["trade_date"], item["code"]))
    return output


def upsert_daily_bars(
    db: Session,
    bars: Iterable[NormalizedDailyBar | dict[str, Any]],
    *,
    source: str | None = None,
) -> int:
    """Persist normalized provider output idempotently."""

    count = 0
    for raw in bars:
        bar = raw if isinstance(raw, NormalizedDailyBar) else NormalizedDailyBar.from_mapping(raw)
        if bar.trade_date == date.min or not bar.code or bar.close is None:
            continue
        row = db.execute(
            select(DailyBarCache).where(
                DailyBarCache.market == bar.market,
                DailyBarCache.code == bar.code,
                DailyBarCache.trade_date == bar.trade_date,
                DailyBarCache.adjustment == bar.adjustment,
            )
        ).scalar_one_or_none()
        values = {
            "market": bar.market,
            "exchange": bar.exchange,
            "code": bar.code,
            "trade_date": bar.trade_date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "prev_close": bar.prev_close,
            "volume": bar.volume,
            "amount": bar.amount,
            "turnover_rate": bar.turnover_rate,
            "adjustment": bar.adjustment,
            "provider": bar.provider or source or "",
            "fetched_at": bar.fetched_at,
            "available_at": bar.available_at,
            "quality_status": bar.quality_status,
            "metadata_json": bar.metadata,
        }
        if row is None:
            db.add(DailyBarCache(**values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
        count += 1
    db.flush()
    return count


def cache_latest_trade_date(db: Session, *, code: str, adjustment: str = "QFQ") -> date | None:
    return db.execute(
        select(DailyBarCache.trade_date)
        .where(
            DailyBarCache.market == "CN",
            DailyBarCache.code == code,
            DailyBarCache.adjustment == adjustment.upper(),
        )
        .order_by(DailyBarCache.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def sync_daily_bar_cache(
    db: Session,
    provider: Any,
    codes: Iterable[str],
    *,
    as_of: date,
    available_at: datetime | None = None,
    bootstrap_limit: int = 260,
    adjustment: str = "QFQ",
) -> dict[str, Any]:
    """Explicitly bootstrap or incrementally refresh the local cache."""

    requested = list(dict.fromkeys(str(code) for code in codes if code))
    fetched_codes = skipped_codes = failed_codes = persisted_rows = 0
    cutoff = available_at or datetime.now(UTC)
    for code in requested:
        latest = cache_latest_trade_date(db, code=code, adjustment=adjustment)
        missing_open_days = 0
        if latest is not None and latest >= as_of:
            skipped_codes += 1
            continue
        if latest is None:
            request_limit = bootstrap_limit
        else:
            missing_open_days = db.scalar(
                select(func.count()).select_from(TradingCalendar).where(
                    TradingCalendar.market == "CN",
                    TradingCalendar.is_open.is_(True),
                    TradingCalendar.trade_date > latest,
                    TradingCalendar.trade_date <= as_of,
                )
            ) or 0
            request_limit = max(1, min(bootstrap_limit, int(missing_open_days) + 2))
        try:
            rows = provider.get_kline(code, limit=request_limit) or []
        except Exception:
            failed_codes += 1
            continue
        if not rows:
            failed_codes += 1
            continue
        fetched_codes += 1
        normalized: list[NormalizedDailyBar] = []
        previous_close: float | None = None
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            bar = NormalizedDailyBar.from_mapping(
                raw,
                code=code,
                provider=getattr(provider, "name", ""),
                adjustment=adjustment,
            )
            if bar.prev_close is None:
                bar.prev_close = previous_close
            previous_close = bar.close
            if bar.trade_date > as_of or (bar.available_at and bar.available_at > cutoff):
                continue
            if latest is not None and bar.trade_date <= latest:
                continue
            if bar.quality_status not in {"VALID", "DEGRADED"}:
                continue
            normalized.append(bar)
        if not normalized and (latest is None or missing_open_days > 0):
            failed_codes += 1
            continue
        persisted_rows += upsert_daily_bars(db, normalized, source=getattr(provider, "name", None))
        # Keep a multi-hour bootstrap restartable instead of holding one giant
        # SQLite write transaction across thousands of network calls.
        db.commit()
    return {
        "status": "ready" if failed_codes == 0 else "degraded",
        "requested_codes": len(requested),
        "fetched_codes": fetched_codes,
        "skipped_codes": skipped_codes,
        "failed_codes": failed_codes,
        "persisted_rows": persisted_rows,
        "as_of": as_of.isoformat(),
        "provider": getattr(provider, "name", provider.__class__.__name__),
    }


__all__ = [
    "load_daily_bars",
    "upsert_daily_bars",
    "cache_latest_trade_date",
    "sync_daily_bar_cache",
]
