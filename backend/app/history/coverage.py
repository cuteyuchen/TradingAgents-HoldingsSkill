"""Historical data coverage and availability reporting.

Coverage is deliberately conservative and security-level.  A row proves only
that one fact exists; it does not prove that every security/date was captured.
Daily tables are measured as known security-state/days over the expected
security-state/days reconstructed from the historical lifecycle.  A trading
date with a single row is never FULL for a 5000-security universe.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market.codes import normalize_security_code
from ..market_engine_models import DailyBarCache
from ..market_models import TradingCalendar
from .models import (
    EtfMetadataHistory,
    FundamentalReport,
    HistoricalDataSyncRun,
    PriceBasisMetadata,
    SecurityClassificationDaily,
    SecurityLifecycleEvent,
    SecurityTradingStatusDaily,
    SecurityValuationDaily,
)
from .time import shanghai_end_of_day_to_utc_naive, visible

HISTORY_DATA_TYPES = (
    "security_lifecycle",
    "trading_status",
    "st_classification",
    "valuation",
    "fundamentals",
    "etf_metadata",
    "price_basis",
)

_DATA_TYPE_ALIASES = {
    "lifecycle": "security_lifecycle",
    "tradingstatus": "trading_status",
    "st": "st_classification",
    "classification": "st_classification",
    "fundamental": "fundamentals",
    "etf": "etf_metadata",
    "pricebasis": "price_basis",
}

_ACTIVE_EVENTS = {"LISTED", "RELISTED"}
_INACTIVE_EVENTS = {"DELIST_PENDING", "DELISTED"}


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def normalise_data_type(value: str | None) -> str | None:
    if value is None:
        return None
    key = str(value).strip().lower().replace("-", "").replace("_", "")
    if key in {"all", ""}:
        return None
    if key in _DATA_TYPE_ALIASES:
        return _DATA_TYPE_ALIASES[key]
    if key in {item.replace("_", "") for item in HISTORY_DATA_TYPES}:
        return next(item for item in HISTORY_DATA_TYPES if item.replace("_", "") == key)
    raise ValueError(f"unsupported_history_data_type:{value}")


def _open_calendar_dates(
    db: Session,
    *,
    start_date: date | None,
    end_date: date | None,
    market: str,
) -> list[date]:
    statement = select(TradingCalendar.trade_date).where(
        TradingCalendar.market == str(market or "CN").upper(),
        TradingCalendar.is_open.is_(True),
    )
    if start_date is not None:
        statement = statement.where(TradingCalendar.trade_date >= start_date)
    if end_date is not None:
        statement = statement.where(TradingCalendar.trade_date <= end_date)
    return list(db.execute(statement.order_by(TradingCalendar.trade_date.asc())).scalars())


def _normalise_code(value: Any) -> str | None:
    return normalize_security_code(value)


def _add_interval(
    start: date,
    end: date,
    code: str,
    security_type: str | None,
    calendar_dates: list[date],
    expected_all: dict[date, set[str]],
    expected_stock: dict[date, set[str]],
) -> None:
    if start > end:
        return
    left = bisect_left(calendar_dates, start)
    right = bisect_right(calendar_dates, end)
    for index in range(left, right):
        day = calendar_dates[index]
        expected_all[day].add(code)
        if security_type == "STOCK":
            expected_stock[day].add(code)


def _expected_universe_by_date(
    db: Session,
    *,
    calendar_dates: list[date],
    end_date: date,
    market: str,
) -> tuple[dict[date, set[str]], dict[date, set[str]]]:
    rows = list(db.execute(
        select(SecurityLifecycleEvent).where(
            SecurityLifecycleEvent.market == str(market or "CN").upper(),
            SecurityLifecycleEvent.effective_date <= end_date,
            SecurityLifecycleEvent.source_available_at.is_not(None),
        ).order_by(
            SecurityLifecycleEvent.effective_date.asc(),
            SecurityLifecycleEvent.id.asc(),
        )
    ).scalars())
    by_code: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        code = _normalise_code(row.code)
        if code:
            by_code[code].append(row)
    expected_all: dict[date, set[str]] = defaultdict(set)
    expected_stock: dict[date, set[str]] = defaultdict(set)
    for code, events in by_code.items():
        active = False
        interval_start: date | None = None
        security_type: str | None = None
        for event in events:
            event_type = str(event.event_type or "").upper()
            if event.security_type:
                security_type = str(event.security_type).upper()
            if event_type in _ACTIVE_EVENTS:
                if active and interval_start is not None:
                    _add_interval(
                        interval_start,
                        event.effective_date - timedelta(days=1),
                        code,
                        security_type,
                        calendar_dates,
                        expected_all,
                        expected_stock,
                    )
                active = True
                interval_start = event.effective_date
            elif event_type in _INACTIVE_EVENTS:
                if active and interval_start is not None:
                    _add_interval(
                        interval_start,
                        event.effective_date - timedelta(days=1),
                        code,
                        security_type,
                        calendar_dates,
                        expected_all,
                        expected_stock,
                    )
                active = False
                interval_start = None
        if active and interval_start is not None:
            _add_interval(
                interval_start,
                end_date,
                code,
                security_type,
                calendar_dates,
                expected_all,
                expected_stock,
            )
    return expected_all, expected_stock


def _daily_known_by_date(
    db: Session,
    *,
    model: type,
    date_column: str,
    start_date: date | None,
    end_date: date | None,
    market: str,
) -> tuple[dict[date, set[str]], int, int]:
    clauses = [getattr(model, "market") == str(market or "CN").upper()]
    if start_date is not None:
        clauses.append(getattr(model, date_column) >= start_date)
    if end_date is not None:
        clauses.append(getattr(model, date_column) <= end_date)
    rows = db.execute(
        select(
            getattr(model, date_column),
            getattr(model, "code"),
            getattr(model, "source_available_at"),
        ).where(*clauses)
    ).all()
    known: dict[date, set[str]] = defaultdict(set)
    unavailable = 0
    for trade_date, code, available_at in rows:
        day = trade_date if isinstance(trade_date, date) else trade_date.date()
        normalized = _normalise_code(code)
        if not normalized:
            continue
        if available_at is None:
            unavailable += 1
            continue
        if visible(available_at, shanghai_end_of_day_to_utc_naive(day)):
            known[day].add(normalized)
    return known, len(rows), unavailable


def _daily_item(
    db: Session,
    *,
    data_type: str,
    model: type,
    date_column: str,
    start_date: date | None,
    end_date: date | None,
    market: str,
    calendar_dates: list[date],
    expected_by_date: dict[date, set[str]],
) -> dict[str, Any]:
    known, row_count, unavailable = _daily_known_by_date(
        db,
        model=model,
        date_column=date_column,
        start_date=start_date,
        end_date=end_date,
        market=market,
    )
    expected_total = sum(len(expected_by_date.get(day, set())) for day in calendar_dates)
    known_total = sum(len(known.get(day, set())) for day in calendar_dates)
    known_dates = sorted(day for day in calendar_dates if known.get(day))
    coverage = (known_total / expected_total) if expected_total else None
    if row_count == 0:
        status = "DATA_GAP"
        reason = "no_rows_in_requested_range"
    elif expected_total == 0:
        status = "DATA_GAP"
        reason = "expected_security_universe_unknown"
    elif known_total == 0:
        status = "DATA_GAP"
        reason = "MISSING_AVAILABILITY_TIME" if unavailable else "security_coverage_incomplete"
    elif known_total >= expected_total:
        status = "FULL"
        reason = None
    else:
        status = "PARTIAL"
        reason = (
            "MISSING_AVAILABILITY_TIME"
            if unavailable
            else "security_level_coverage_incomplete"
        )
    return {
        "data_type": data_type,
        "semantics": "DAILY",
        "status": status,
        "reason": reason,
        "row_count": row_count,
        "unavailable_rows": unavailable,
        "expected_dates": len(calendar_dates),
        "expected_security_dates": expected_total,
        "known_security_dates": known_total,
        "known_dates": [_iso(day) for day in known_dates],
        "coverage": round(min(1.0, coverage), 6) if coverage is not None else None,
        "earliest_supported_at": _iso(known_dates[0] if known_dates else None),
        "latest_supported_at": _iso(known_dates[-1] if known_dates else None),
        "sources": _distinct_sources(db, model),
    }


def _bar_codes_by_date(
    db: Session,
    *,
    start_date: date | None,
    end_date: date | None,
    market: str,
) -> dict[date, set[str]]:
    clauses = [DailyBarCache.market == str(market or "CN").upper()]
    if start_date is not None:
        clauses.append(DailyBarCache.trade_date >= start_date)
    if end_date is not None:
        clauses.append(DailyBarCache.trade_date <= end_date)
    rows = db.execute(
        select(DailyBarCache.trade_date, DailyBarCache.code).where(*clauses)
    ).all()
    result: dict[date, set[str]] = defaultdict(set)
    for trade_date, code in rows:
        day = trade_date if isinstance(trade_date, date) else trade_date.date()
        normalized = _normalise_code(code)
        if normalized:
            result[day].add(normalized)
    return result


def _event_item(
    db: Session,
    *,
    data_type: str,
    model: type,
    date_column: str,
    start_date: date | None,
    end_date: date | None,
    market: str,
) -> dict[str, Any]:
    clauses = [getattr(model, "market") == str(market or "CN").upper()]
    if end_date is not None:
        clauses.append(getattr(model, date_column) <= end_date)
    row_count = int(db.execute(select(func.count(model.id)).where(*clauses)).scalar() or 0)
    usable_count = int(
        db.execute(
            select(func.count(model.id)).where(
                *clauses,
                getattr(model, "source_available_at").is_not(None),
            )
        ).scalar() or 0
    )
    earliest = db.execute(
        select(func.min(getattr(model, date_column))).where(*clauses)
    ).scalar()
    latest = db.execute(
        select(func.max(getattr(model, date_column))).where(*clauses)
    ).scalar()
    distinct_codes = db.execute(
        select(func.count(func.distinct(getattr(model, "code")))).where(*clauses)
    ).scalar() or 0
    if row_count == 0:
        status = "DATA_GAP"
        reason = "no_rows_in_requested_range"
    elif usable_count == 0:
        status = "DATA_GAP"
        reason = "MISSING_AVAILABILITY_TIME"
    else:
        # An event table cannot prove FULL coverage without a known universe
        # denominator; PARTIAL is the conservative, honest default.
        status = "PARTIAL"
        reason = "event_coverage_requires_known_denominator"
    return {
        "data_type": data_type,
        "semantics": "EVENT",
        "status": status,
        "reason": reason,
        "row_count": row_count,
        "usable_row_count": usable_count,
        "distinct_codes": int(distinct_codes),
        "expected_dates": None,
        "known_dates": None,
        "coverage": None,
        "earliest_supported_at": _iso(earliest),
        "latest_supported_at": _iso(latest),
        "sources": _distinct_sources(db, model),
    }


def _publication_item(
    db: Session,
    *,
    start_date: date | None,
    end_date: date | None,
    market: str,
    calendar_dates: list[date],
    expected_by_date: dict[date, set[str]],
) -> dict[str, Any]:
    clauses = [FundamentalReport.market == str(market or "CN").upper()]
    if end_date is not None:
        clauses.append(
            FundamentalReport.published_at
            <= shanghai_end_of_day_to_utc_naive(end_date)
        )
    rows = db.execute(
        select(FundamentalReport.code, FundamentalReport.published_at).where(*clauses)
    ).all()
    row_count = len(rows)
    missing_publications = sum(1 for _, published_at in rows if published_at is None)
    published_by_code: dict[str, list[datetime]] = defaultdict(list)
    for code, published_at in rows:
        normalized = _normalise_code(code)
        if normalized and published_at is not None:
            published_by_code[normalized].append(published_at)
    for values in published_by_code.values():
        values.sort()
    expected_total = sum(len(expected_by_date.get(day, set())) for day in calendar_dates)
    known_total = 0
    for day in calendar_dates:
        cutoff = shanghai_end_of_day_to_utc_naive(day)
        for code in expected_by_date.get(day, set()):
            times = published_by_code.get(code)
            if times and times[0] <= cutoff:
                known_total += 1
    coverage = (known_total / expected_total) if expected_total else None
    earliest = db.execute(
        select(func.min(FundamentalReport.published_at)).where(*clauses)
    ).scalar()
    latest = db.execute(
        select(func.max(FundamentalReport.published_at)).where(*clauses)
    ).scalar()
    if row_count == 0:
        status = "DATA_GAP"
        reason = "no_rows_in_requested_range"
    elif expected_total == 0:
        status = "DATA_GAP"
        reason = "expected_stock_universe_unknown"
    elif missing_publications:
        status = "PARTIAL"
        reason = "MISSING_PUBLICATION_TIME"
    elif known_total < expected_total:
        status = "PARTIAL"
        reason = "security_level_coverage_incomplete"
    else:
        status = "FULL"
        reason = None
    return {
        "data_type": "fundamentals",
        "semantics": "PUBLICATION",
        "status": status,
        "reason": reason,
        "row_count": row_count,
        "known_publications": int(
            db.execute(
                select(func.count(FundamentalReport.id)).where(
                    *clauses,
                    FundamentalReport.published_at.is_not(None),
                )
            ).scalar() or 0
        ),
        "missing_publications": missing_publications,
        "expected_dates": len(calendar_dates),
        "expected_security_dates": expected_total,
        "known_security_dates": known_total,
        "known_dates": None,
        "coverage": round(min(1.0, coverage), 6) if coverage is not None else None,
        "earliest_supported_at": _iso(earliest),
        "latest_supported_at": _iso(latest),
        "sources": _distinct_sources(db, FundamentalReport),
    }


def _distinct_sources(db: Session, model: type) -> list[str]:
    return sorted(
        str(item)
        for item in db.execute(select(func.distinct(getattr(model, "source")))).scalars()
        if item
    )


def _last_sync(db: Session, data_type: str) -> dict[str, Any] | None:
    row = db.execute(
        select(HistoricalDataSyncRun)
        .where(
            HistoricalDataSyncRun.data_type == data_type,
            HistoricalDataSyncRun.status == "COMPLETED",
        )
        .order_by(HistoricalDataSyncRun.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "run_id": row.id,
        "status": row.status,
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "inserted_count": row.inserted_count,
        "updated_count": row.updated_count,
        "skipped_count": row.skipped_count,
        "failed_count": row.failed_count,
        "provider": row.provider,
        "source": row.source,
    }


def historical_data_coverage(
    db: Session,
    *,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    data_type: str | None = None,
    market: str = "CN",
) -> dict[str, Any]:
    """Return honest security-level coverage for each Phase L data type."""

    start = date.fromisoformat(str(start_date)) if start_date else None
    end = date.fromisoformat(str(end_date)) if end_date else None
    if start is not None and end is not None and start > end:
        raise ValueError("start_date_must_not_exceed_end_date")
    selected = normalise_data_type(data_type)
    calendar_dates = _open_calendar_dates(
        db, start_date=start, end_date=end, market=market
    )
    universe_end = end or (calendar_dates[-1] if calendar_dates else date.today())
    expected_all, expected_stock = _expected_universe_by_date(
        db,
        calendar_dates=calendar_dates,
        end_date=universe_end,
        market=market,
    )
    bar_codes = _bar_codes_by_date(
        db, start_date=start, end_date=end, market=market
    )
    specs = [
        ("security_lifecycle", SecurityLifecycleEvent, "effective_date", "EVENT", None),
        ("trading_status", SecurityTradingStatusDaily, "trade_date", "DAILY", expected_all),
        ("st_classification", SecurityClassificationDaily, "trade_date", "DAILY", expected_stock),
        ("valuation", SecurityValuationDaily, "trade_date", "DAILY", expected_stock),
        ("fundamentals", FundamentalReport, "published_at", "PUBLICATION", expected_stock),
        ("etf_metadata", EtfMetadataHistory, "effective_date", "EVENT", None),
        ("price_basis", PriceBasisMetadata, "trade_date", "DAILY", bar_codes),
    ]
    items: list[dict[str, Any]] = []
    for name, model, date_column, semantics, expected_by_date in specs:
        if selected is not None and name != selected:
            continue
        if semantics == "DAILY":
            item = _daily_item(
                db,
                data_type=name,
                model=model,
                date_column=date_column,
                start_date=start,
                end_date=end,
                market=market,
                calendar_dates=calendar_dates,
                expected_by_date=expected_by_date or {},
            )
        elif semantics == "PUBLICATION":
            item = _publication_item(
                db,
                start_date=start,
                end_date=end,
                market=market,
                calendar_dates=calendar_dates,
                expected_by_date=expected_by_date or {},
            )
        else:
            item = _event_item(
                db,
                data_type=name,
                model=model,
                date_column=date_column,
                start_date=start,
                end_date=end,
                market=market,
            )
        item["last_sync"] = _last_sync(db, name)
        items.append(item)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "requested_range": {
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
        },
        "market": str(market or "CN").upper(),
        "items": items,
    }


__all__ = [
    "HISTORY_DATA_TYPES",
    "historical_data_coverage",
    "normalise_data_type",
]
