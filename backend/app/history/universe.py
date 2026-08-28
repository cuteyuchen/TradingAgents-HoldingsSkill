"""Point-in-time universe reconstruction from persisted historical facts.

The resolver deliberately ignores current ``SecurityMaster`` lifecycle flags.
Identity seeds are allowed, but active/ST/suspension/delisting decisions come
only from the historical tables.  Unknown states are never promoted to
eligible; they are reported in ``unknown_count`` and ``exclusions``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market.codes import exchange_for_code, normalize_security_code
from ..market_models import TradingCalendar
from ..v2_models import HoldingItem, PortfolioSnapshot
from .models import (
    EtfMetadataHistory,
    FundamentalReport,
    SecurityClassificationDaily,
    SecurityLifecycleEvent,
    SecurityTradingStatusDaily,
    SecurityValuationDaily,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")

UNIVERSE_VERSION = "pit-universe-v1"
ACTIVE_EVENTS = {"LISTED", "RELISTED"}
INACTIVE_EVENTS = {"DELIST_PENDING", "DELISTED"}
INVALID_STATUSES = {"SUSPENDED", "HALTED", "DELISTED", "PAUSED"}
ST_CLASSIFICATIONS = {"ST", "STAR_ST", "DELIST_RISK"}
UNKNOWN = "UNKNOWN"


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).replace("/", "-")[:10])
    except ValueError:
        return None


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _visible(value: datetime | None, cutoff: datetime) -> bool:
    parsed = _naive_utc(value)
    return parsed is None or parsed <= cutoff


def _end_of_day(value: date | datetime) -> datetime:
    day = _date(value) or date.today()
    return datetime.combine(day, time(23, 59, 59)).replace(tzinfo=None)


def _code(value: Any) -> str:
    return normalize_security_code(value)


def _calendar_dates(db: Session, *, as_of: date, market: str = "CN") -> set[date]:
    rows = db.execute(
        select(TradingCalendar.trade_date).where(
            TradingCalendar.market == str(market or "CN").upper(),
            TradingCalendar.trade_date <= as_of,
            TradingCalendar.is_open.is_(True),
        )
    ).scalars().all()
    return set(rows)


def trading_days_between(
    listing_date: date | None,
    as_of: date,
    calendar_dates: Iterable[date],
) -> int:
    if listing_date is None:
        return 0
    days = set(calendar_dates)
    return sum(1 for day in days if listing_date <= day <= as_of)


def _lifecycle_states(
    db: Session,
    *,
    as_of: date,
    cutoff: datetime,
    market: str = "CN",
) -> dict[str, dict[str, Any]]:
    rows = list(db.execute(select(SecurityLifecycleEvent).where(
        SecurityLifecycleEvent.market == str(market or "CN").upper(),
        SecurityLifecycleEvent.effective_date <= as_of,
    ).order_by(
        SecurityLifecycleEvent.effective_date.asc(),
        SecurityLifecycleEvent.id.asc(),
    )).scalars())
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _visible(row.source_available_at, cutoff):
            continue
        code = _code(row.code)
        if not code:
            continue
        state = states.setdefault(code, {
            "code": code,
            "market": row.market,
            "exchange": row.exchange,
            "security_type": None,
            "name": None,
            "listed_date": None,
            "delisted_date": None,
            "active": False,
            "events": [],
        })
        event_type = str(row.event_type or "").upper()
        if event_type == "LISTED":
            state["listed_date"] = row.effective_date
            state["active"] = True
            state["delisted_date"] = None
        elif event_type == "RELISTED":
            state["active"] = True
            state["delisted_date"] = None
        elif event_type in INACTIVE_EVENTS:
            state["active"] = False
            state["delisted_date"] = row.effective_date
        if row.security_type:
            state["security_type"] = row.security_type.upper()
        if row.security_name:
            state["name"] = row.security_name
        if row.exchange:
            state["exchange"] = row.exchange.upper()
        state["events"].append(
            {
                "id": row.id,
                "event_type": event_type,
                "effective_date": row.effective_date.isoformat(),
                "source": row.source,
                "source_ref": row.source_ref,
                "source_available_at": row.source_available_at.isoformat() if row.source_available_at else None,
            }
        )
    return states


def _latest_by_code(
    db: Session,
    model: type,
    *,
    as_of: date,
    cutoff: datetime,
    date_column: str,
    status_column: str,
    market: str = "CN",
) -> dict[str, Any]:
    rows = list(db.execute(
        select(model).where(
            getattr(model, "market") == str(market or "CN").upper(),
            getattr(model, date_column) <= as_of,
        ).order_by(
            getattr(model, date_column).asc(),
            getattr(model, "id").asc(),
        )
    ).scalars())
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if not _visible(row.source_available_at, cutoff):
            continue
        code = _code(row.code)
        if code:
            grouped[code].append(row)
    result: dict[str, Any] = {}
    for code, values in grouped.items():
        if not values:
            continue
        latest = values[-1]
        result[code] = {
            "status": str(getattr(latest, status_column) or UNKNOWN).upper(),
            "row_id": latest.id,
            "trade_date": getattr(latest, date_column),
            "source": latest.source,
            "source_ref": latest.source_ref,
            "source_available_at": latest.source_available_at,
            "quality_status": str(getattr(latest, "quality_status") or "VALID").upper(),
            "is_name_derived": bool(getattr(latest, "is_name_derived", False)),
        }
    return result


def _classification_by_code(
    db: Session,
    *,
    as_of: date,
    cutoff: datetime,
    market: str = "CN",
) -> dict[str, dict[str, Any]]:
    return _latest_by_code(
        db,
        SecurityClassificationDaily,
        as_of=as_of,
        cutoff=cutoff,
        date_column="trade_date",
        status_column="classification",
        market=market,
    )


def _trading_status_by_code(
    db: Session,
    *,
    as_of: date,
    cutoff: datetime,
    market: str = "CN",
) -> dict[str, dict[str, Any]]:
    return _latest_by_code(
        db,
        SecurityTradingStatusDaily,
        as_of=as_of,
        cutoff=cutoff,
        date_column="trade_date",
        status_column="status",
        market=market,
    )


def resolve_security_state(
    db: Session,
    code: str,
    as_of: date | datetime | str,
    *,
    market: str = "CN",
) -> dict[str, Any]:
    """Resolve one security's point-in-time lifecycle and daily state."""

    normalized = _code(code)
    day = _date(as_of)
    if not normalized or day is None:
        raise ValueError("code_and_as_of_required")
    cutoff = _end_of_day(day)
    states = _lifecycle_states(db, as_of=day, cutoff=cutoff, market=market)
    state = states.get(normalized)
    if state is None:
        return {
            "code": normalized,
            "as_of": day.isoformat(),
            "status": UNKNOWN,
            "lifecycle_known": False,
            "classification": UNKNOWN,
            "trading_status": UNKNOWN,
            "eligible": False,
            "reason_codes": ["UNKNOWN_LIFECYCLE"],
        }
    classification = _classification_by_code(
        db, as_of=day, cutoff=cutoff, market=market
    ).get(normalized)
    trading = _trading_status_by_code(
        db, as_of=day, cutoff=cutoff, market=market
    ).get(normalized)
    lifecycle_known = state["listed_date"] is not None
    status_value = (
        "ACTIVE"
        if state["active"]
        else "DELISTED"
        if lifecycle_known
        else UNKNOWN
    )
    return {
        "code": normalized,
        "as_of": day.isoformat(),
        "status": status_value,
        "lifecycle_known": lifecycle_known,
        "listed_date": state["listed_date"],
        "delisted_date": state["delisted_date"],
        "security_type": state["security_type"] or UNKNOWN,
        "name": state["name"],
        "classification": (classification or {}).get("status", UNKNOWN),
        "classification_quality": (classification or {}).get("quality_status", UNKNOWN),
        "classification_name_derived": bool((classification or {}).get("is_name_derived")),
        "trading_status": (trading or {}).get("status", UNKNOWN),
        "trading_quality": (trading or {}).get("quality_status", UNKNOWN),
        "eligible": False,
        "reason_codes": [] if lifecycle_known else ["UNKNOWN_LIFECYCLE"],
    }


def resolve_special_treatment(
    db: Session,
    code: str,
    trade_date: date | datetime | str,
    *,
    market: str = "CN",
) -> dict[str, Any]:
    """Return the classification fact visible on the requested trade date."""

    day = _date(trade_date)
    if day is None:
        raise ValueError("trade_date_required")
    row = _classification_by_code(
        db, as_of=day, cutoff=_end_of_day(day), market=market
    ).get(_code(code))
    if row is None:
        return {"classification": UNKNOWN, "known": False, "source": None}
    return {
        "classification": row["status"],
        "known": row["status"] != UNKNOWN,
        "source": row["source"],
        "source_ref": row["source_ref"],
        "quality_status": row["quality_status"],
        "is_name_derived": row["is_name_derived"],
    }


def resolve_valuation(
    db: Session,
    code: str,
    trade_date: date | datetime | str,
    *,
    market: str = "CN",
) -> dict[str, Any]:
    """Return the latest valuation visible on or before the trade date."""

    day = _date(trade_date)
    if day is None:
        raise ValueError("trade_date_required")
    cutoff = _end_of_day(day)
    normalized = _code(code)
    rows = list(db.execute(select(SecurityValuationDaily).where(
        SecurityValuationDaily.market == str(market or "CN").upper(),
        SecurityValuationDaily.code == normalized,
        SecurityValuationDaily.trade_date <= day,
    ).order_by(
        SecurityValuationDaily.trade_date.asc(),
        SecurityValuationDaily.id.asc(),
    )).scalars())
    visible = [row for row in rows if _visible(row.source_available_at, cutoff)]
    if not visible:
        return {
            "code": normalized,
            "as_of": day.isoformat(),
            "available": False,
            "reason": "NO_HISTORICAL_VALUATION",
            "pe_ttm": None,
            "pb": None,
            "ps_ttm": None,
            "dividend_yield": None,
            "market_cap": None,
            "float_market_cap": None,
        }
    row = visible[-1]
    return {
        "code": normalized,
        "as_of": day.isoformat(),
        "available": True,
        "trade_date": row.trade_date.isoformat(),
        "pe_ttm": row.pe_ttm,
        "pb": row.pb,
        "ps_ttm": row.ps_ttm,
        "dividend_yield": row.dividend_yield,
        "market_cap": row.market_cap,
        "float_market_cap": row.float_market_cap,
        "source": row.source,
        "source_ref": row.source_ref,
        "source_available_at": row.source_available_at.isoformat() if row.source_available_at else None,
        "quality_status": row.quality_status,
    }


def resolve_fundamental(
    db: Session,
    code: str,
    as_of: date | datetime | str,
    *,
    market: str = "CN",
) -> dict[str, Any]:
    """Return the latest report version whose published_at is <= as_of."""

    day = _date(as_of)
    if day is None:
        raise ValueError("as_of_required")
    cutoff = _end_of_day(day)
    normalized = _code(code)
    rows = list(db.execute(select(FundamentalReport).where(
        FundamentalReport.market == str(market or "CN").upper(),
        FundamentalReport.code == normalized,
        FundamentalReport.published_at.is_not(None),
        FundamentalReport.published_at <= cutoff,
    ).order_by(
        FundamentalReport.published_at.asc(),
        FundamentalReport.revision_number.asc(),
        FundamentalReport.id.asc(),
    )).scalars())
    if not rows:
        return {
            "code": normalized,
            "as_of": day.isoformat(),
            "available": False,
            "reason": "MISSING_PUBLICATION_TIME" if db.execute(
                select(FundamentalReport.id).where(
                    FundamentalReport.market == str(market or "CN").upper(),
                    FundamentalReport.code == normalized,
                    FundamentalReport.published_at.is_(None),
                ).limit(1)
            ).scalar_one_or_none() else "NO_PUBLISHED_FUNDAMENTAL",
            "report_period": None,
            "revision_number": None,
        }
    row = rows[-1]
    return {
        "code": normalized,
        "as_of": day.isoformat(),
        "available": True,
        "report_period": row.report_period.isoformat(),
        "report_type": row.report_type,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "revision_number": row.revision_number,
        "is_restatement": row.is_restatement,
        "roe": row.roe,
        "revenue": row.revenue,
        "revenue_yoy": row.revenue_yoy,
        "net_profit": row.net_profit,
        "net_profit_yoy": row.net_profit_yoy,
        "gross_margin": row.gross_margin,
        "debt_ratio": row.debt_ratio,
        "operating_cash_flow": row.operating_cash_flow,
        "eps": row.eps,
        "source": row.source,
        "source_ref": row.source_ref,
        "source_available_at": row.source_available_at.isoformat() if row.source_available_at else None,
        "quality_status": row.quality_status,
    }


def resolve_etf_metadata(
    db: Session,
    code: str,
    as_of: date | datetime | str,
    *,
    market: str = "CN",
) -> dict[str, Any]:
    """Return the latest ETF metadata effective on or before as_of."""

    day = _date(as_of)
    if day is None:
        raise ValueError("as_of_required")
    cutoff = _end_of_day(day)
    normalized = _code(code)
    rows = list(db.execute(select(EtfMetadataHistory).where(
        EtfMetadataHistory.market == str(market or "CN").upper(),
        EtfMetadataHistory.code == normalized,
        EtfMetadataHistory.effective_date <= day,
    ).order_by(
        EtfMetadataHistory.effective_date.asc(),
        EtfMetadataHistory.id.asc(),
    )).scalars())
    visible = [row for row in rows if _visible(row.source_available_at, cutoff)]
    if not visible:
        return {
            "code": normalized,
            "as_of": day.isoformat(),
            "available": False,
            "reason": "NO_HISTORICAL_ETF_METADATA",
        }
    row = visible[-1]
    return {
        "code": normalized,
        "as_of": day.isoformat(),
        "available": True,
        "effective_date": row.effective_date.isoformat(),
        "category": row.category,
        "index_code": row.index_code,
        "benchmark_code": row.benchmark_code,
        "fund_type": row.fund_type,
        "sector_theme_json": row.sector_theme_json,
        "inception_date": row.inception_date.isoformat() if row.inception_date else None,
        "source": row.source,
        "source_ref": row.source_ref,
        "source_available_at": row.source_available_at.isoformat() if row.source_available_at else None,
        "quality_status": row.quality_status,
    }


def resolve_historical_holdings(
    db: Session,
    *,
    portfolio_id: int,
    as_of: date | datetime | str,
    user_id: int | None = None,
) -> set[str]:
    day = _date(as_of)
    if day is None:
        return set()
    cutoff = _end_of_day(day)
    query = (
        select(HoldingItem.code)
        .join(PortfolioSnapshot, PortfolioSnapshot.id == HoldingItem.snapshot_id)
        .where(
            PortfolioSnapshot.portfolio_id == portfolio_id,
            PortfolioSnapshot.snapshot_time <= cutoff,
            PortfolioSnapshot.status.in_(("confirmed", "CONFIRMED")),
        )
    )
    if user_id is not None:
        query = query.where(PortfolioSnapshot.user_id == user_id)
    rows = db.execute(
        query.order_by(PortfolioSnapshot.snapshot_time.desc(), HoldingItem.id.desc())
    ).scalars().all()
    return {normalize_security_code(value) for value in rows if normalize_security_code(value)}


@dataclass(frozen=True)
class UniverseResult:
    as_of_date: date
    purpose: str
    universe_version: str = UNIVERSE_VERSION
    eligible_codes: list[str] = field(default_factory=list)
    excluded_counts: dict[str, int] = field(default_factory=dict)
    exclusions: dict[str, list[str]] = field(default_factory=dict)
    total_count: int = 0
    known_count: int = 0
    unknown_count: int = 0
    coverage: float = 0.0
    status: str = "LEAKAGE_BLOCKED"
    source_lineage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "purpose": self.purpose,
            "universe_version": self.universe_version,
            "eligible_codes": list(self.eligible_codes),
            "excluded_counts": dict(self.excluded_counts),
            "exclusions": dict(self.exclusions),
            "total_count": self.total_count,
            "known_count": self.known_count,
            "unknown_count": self.unknown_count,
            "coverage": round(self.coverage, 6),
            "status": self.status,
            "source_lineage": self.source_lineage,
        }


def resolve_equity_universe(
    db: Session,
    as_of: date | datetime | str,
    *,
    purpose: str = "MARKET_SCORE",
    market: str = "CN",
    portfolio_id: int | None = None,
    user_id: int | None = None,
    held_codes: Iterable[str] = (),
    minimum_trading_days: int | None = None,
) -> UniverseResult:
    """Build the as-of universe from historical facts only."""

    day = _date(as_of)
    if day is None:
        raise ValueError("as_of_date_required")
    normalized_purpose = str(purpose or "MARKET_SCORE").upper()
    cutoff = _end_of_day(day)
    states = _lifecycle_states(db, as_of=day, cutoff=cutoff, market=market)
    if not states:
        return UniverseResult(
            as_of_date=day,
            purpose=normalized_purpose,
            status="LEAKAGE_BLOCKED",
            source_lineage={"lifecycle_events": 0},
        )
    classification = _classification_by_code(
        db, as_of=day, cutoff=cutoff, market=market
    )
    trading = _trading_status_by_code(
        db, as_of=day, cutoff=cutoff, market=market
    )
    calendar = _calendar_dates(db, as_of=day, market=market)
    if normalized_purpose in {"CANDIDATE_STOCK", "CANDIDATE_ETF"} and portfolio_id is not None:
        held = resolve_historical_holdings(
            db,
            portfolio_id=portfolio_id,
            as_of=day,
            user_id=user_id,
        )
    else:
        held = {normalize_security_code(value) for value in held_codes if normalize_security_code(value)}

    threshold = minimum_trading_days
    if threshold is None:
        threshold = 60 if normalized_purpose in {"CANDIDATE_STOCK", "CANDIDATE_ETF"} else 20
    is_stock_purpose = normalized_purpose in {"MARKET_SCORE", "CANDIDATE_STOCK", "RESEARCH"}
    is_etf_purpose = normalized_purpose == "CANDIDATE_ETF"

    eligible: list[str] = []
    exclusions: dict[str, list[str]] = {}
    counts: Counter[str] = Counter()
    known = 0
    unknown = 0

    for code, state in states.items():
        reasons: list[str] = []
        lifecycle_known = state["active"] is not None and state["listed_date"] is not None
        security_type = str(state["security_type"] or "").upper()
        exchange = str(state["exchange"] or exchange_for_code(code) or "").upper()
        if not lifecycle_known:
            reasons.append("UNKNOWN_LIFECYCLE")
        elif not state["active"]:
            reasons.append("UNIVERSE_DELISTED")
        if is_stock_purpose and security_type != "STOCK":
            reasons.append("UNIVERSE_NON_STOCK")
        if is_stock_purpose and exchange not in {"SSE", "SZSE"}:
            reasons.append("UNIVERSE_BSE" if exchange == "BSE" else "UNIVERSE_EXCHANGE")
        if is_etf_purpose and security_type != "ETF":
            reasons.append("UNIVERSE_NON_ETF")
        if is_etf_purpose and exchange not in {"SSE", "SZSE"}:
            reasons.append("UNIVERSE_EXCHANGE")

        class_row = classification.get(code)
        trading_row = trading.get(code)
        classification_status = (class_row or {}).get("status", UNKNOWN)
        trading_status = (trading_row or {}).get("status", UNKNOWN)
        if classification_status == UNKNOWN:
            reasons.append("UNKNOWN_CLASSIFICATION")
        elif classification_status in ST_CLASSIFICATIONS:
            reasons.append("UNIVERSE_ST")
        if trading_status == UNKNOWN:
            reasons.append("UNKNOWN_TRADING_STATUS")
        elif trading_status in INVALID_STATUSES:
            reasons.append("UNIVERSE_SUSPENDED" if trading_status in {"SUSPENDED", "HALTED", "PAUSED"} else "UNIVERSE_DELISTED")

        if code in held:
            reasons.append("UNIVERSE_HELD")
        if state["listed_date"] is not None:
            age = trading_days_between(state["listed_date"], day, calendar)
            if not calendar:
                reasons.append("CALENDAR_UNAVAILABLE")
            elif age < threshold:
                reasons.append("UNIVERSE_NEW_LISTING")
        else:
            reasons.append("UNKNOWN_LISTING_DATE")

        if not reasons:
            eligible.append(code)
        else:
            for reason in reasons:
                counts[reason] += 1
            exclusions[code] = reasons
        if reasons and any(
            "UNKNOWN" in reason
            or "MISSING" in reason
            or "UNAVAILABLE" in reason
            for reason in reasons
        ):
            unknown += 1
        else:
            known += 1

    total = len(states)
    coverage = known / total if total else 0.0
    status = "FULL" if unknown == 0 else "PARTIAL"
    return UniverseResult(
        as_of_date=day,
        purpose=normalized_purpose,
        eligible_codes=sorted(eligible),
        excluded_counts=dict(sorted(counts.items())),
        exclusions=exclusions,
        total_count=total,
        known_count=known,
        unknown_count=unknown,
        coverage=round(min(1.0, coverage), 6),
        status=status,
        source_lineage={
            "lifecycle_events": len(states),
            "classification_rows": len(classification),
            "trading_status_rows": len(trading),
            "calendar_dates": len(calendar),
        },
    )


__all__ = [
    "UNIVERSE_VERSION",
    "UniverseResult",
    "resolve_equity_universe",
    "resolve_etf_metadata",
    "resolve_fundamental",
    "resolve_historical_holdings",
    "resolve_security_state",
    "resolve_special_treatment",
    "resolve_valuation",
    "trading_days_between",
]
