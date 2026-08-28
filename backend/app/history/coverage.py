"""Historical data coverage and availability reporting.

Coverage is deliberately conservative.  A row proves only that a fact exists;
it does not prove that every security/date was captured.  Daily tables use the
persisted trading calendar as the expected denominator.  Event tables do not
claim FULL when the universe denominator is unknown.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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

_DAILY_TYPES = {
    "trading_status",
    "st_classification",
    "valuation",
    "price_basis",
}


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


def _daily_item(
    db: Session,
    *,
    data_type: str,
    model: type,
    date_column,
    start_date: date | None,
    end_date: date | None,
    market: str,
) -> dict[str, Any]:
    clauses = [getattr(model, "market") == str(market or "CN").upper()]
    if start_date is not None:
        clauses.append(getattr(model, date_column) >= start_date)
    if end_date is not None:
        clauses.append(getattr(model, date_column) <= end_date)
    row_count = int(db.execute(select(func.count(model.id)).where(*clauses)).scalar() or 0)
    known_dates = sorted(
        db.execute(
            select(getattr(model, date_column))
            .where(*clauses)
            .distinct()
        ).scalars()
    )
    known_count = len(known_dates)
    expected = _open_calendar_dates(
        db, start_date=start_date, end_date=end_date, market=market
    )
    expected_count = len(expected)
    coverage = (known_count / expected_count) if expected_count else None
    if row_count == 0:
        status = "DATA_GAP"
        reason = "no_rows_in_requested_range"
    elif expected_count and known_count >= expected_count:
        status = "FULL"
        reason = None
    else:
        status = "PARTIAL"
        reason = "historical_trade_date_coverage_is_incomplete"
    return {
        "data_type": data_type,
        "semantics": "DAILY",
        "status": status,
        "reason": reason,
        "row_count": row_count,
        "expected_dates": expected_count,
        "known_dates": known_count,
        "coverage": round(min(1.0, coverage), 6) if coverage is not None else None,
        "earliest_supported_at": _iso(known_dates[0] if known_dates else None),
        "latest_supported_at": _iso(known_dates[-1] if known_dates else None),
        "sources": _distinct_sources(db, model),
    }


def _event_item(
    db: Session,
    *,
    data_type: str,
    model: type,
    date_column,
    start_date: date | None,
    end_date: date | None,
    market: str,
) -> dict[str, Any]:
    clauses = [getattr(model, "market") == str(market or "CN").upper()]
    if end_date is not None:
        clauses.append(getattr(model, date_column) <= end_date)
    row_count = int(db.execute(select(func.count(model.id)).where(*clauses)).scalar() or 0)
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
) -> dict[str, Any]:
    clauses = [FundamentalReport.market == str(market or "CN").upper()]
    if end_date is not None:
        clauses.append(FundamentalReport.published_at < end_date + timedelta(days=1))
    row_count = int(db.execute(select(func.count(FundamentalReport.id)).where(*clauses)).scalar() or 0)
    known_publications = int(
        db.execute(
            select(func.count(FundamentalReport.id))
            .where(*clauses, FundamentalReport.published_at.is_not(None))
        ).scalar() or 0
    )
    missing_publications = max(0, row_count - known_publications)
    earliest = db.execute(
        select(func.min(FundamentalReport.published_at)).where(*clauses)
    ).scalar()
    latest = db.execute(
        select(func.max(FundamentalReport.published_at)).where(*clauses)
    ).scalar()
    if row_count == 0:
        status = "DATA_GAP"
        reason = "no_rows_in_requested_range"
    elif missing_publications:
        status = "PARTIAL"
        reason = "MISSING_PUBLICATION_TIME"
    else:
        status = "FULL"
        reason = None
    return {
        "data_type": "fundamentals",
        "semantics": "PUBLICATION",
        "status": status,
        "reason": reason,
        "row_count": row_count,
        "known_publications": known_publications,
        "missing_publications": missing_publications,
        "expected_dates": None,
        "known_dates": None,
        "coverage": round(known_publications / row_count, 6) if row_count else None,
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
    """Return honest coverage for each requested Phase L data type."""

    start = date.fromisoformat(str(start_date)) if start_date else None
    end = date.fromisoformat(str(end_date)) if end_date else None
    if start is not None and end is not None and start > end:
        raise ValueError("start_date_must_not_exceed_end_date")
    selected = normalise_data_type(data_type)
    specs = [
        ("security_lifecycle", SecurityLifecycleEvent, "effective_date", "EVENT"),
        ("trading_status", SecurityTradingStatusDaily, "trade_date", "DAILY"),
        ("st_classification", SecurityClassificationDaily, "trade_date", "DAILY"),
        ("valuation", SecurityValuationDaily, "trade_date", "DAILY"),
        ("fundamentals", FundamentalReport, "published_at", "PUBLICATION"),
        ("etf_metadata", EtfMetadataHistory, "effective_date", "EVENT"),
        ("price_basis", PriceBasisMetadata, "trade_date", "DAILY"),
    ]
    items: list[dict[str, Any]] = []
    for name, model, date_column, semantics in specs:
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
            )
        elif semantics == "PUBLICATION":
            item = _publication_item(
                db, start_date=start, end_date=end, market=market
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
