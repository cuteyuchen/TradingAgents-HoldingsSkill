"""Deterministic MARKET-1 A-share market facts.

This module deliberately sits above the existing TradingCalendar,
SecurityMaster, normalized quote snapshot, provider-health, and historical
aggregate contracts.  It does not import the Analysis Engine, make per-stock
requests, or introduce a second identity/calendar/provider registry.
"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from statistics import fmean, median
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot, MarketScoreSnapshot
from ..market_models import SecurityMaster
from ..services.market_snapshot_service import get_cached_all_a_share_quote_snapshot
from ..services.security_master import get_market_universe
from .codes import canonical_security_code, normalize_security_code
from .engine.median_index import next_median_index
from .engine.metrics import is_price_limit
from .providers.fuyao import FuyaoDataProvider
from .quality import _worst_grade
from .session import MarketDataBasis, MarketSession, MarketSessionResolution, MarketSessionService


logger = logging.getLogger(__name__)

SYSTEMIC_MARKET_VERSION = "systemic-market-v1"
ALL_A_UNIVERSE_VERSION = "all-a-foundation-v1"
MAJOR_INDEX_SOURCE = "fuyao"


MAJOR_INDEX_DEFINITIONS: tuple[dict[str, str], ...] = (
    {"code": "000001.SH", "name": "上证指数", "exchange": "SSE"},
    {"code": "399001.SZ", "name": "深证成指", "exchange": "SZSE"},
    {"code": "399006.SZ", "name": "创业板指", "exchange": "SZSE"},
    {"code": "000300.SH", "name": "沪深300", "exchange": "SSE"},
    {"code": "000852.SH", "name": "中证1000", "exchange": "SSE"},
    {"code": "000688.SH", "name": "科创50", "exchange": "SSE"},
)

_USABLE_QUOTE_QUALITIES = frozenset({"VALID", "DEGRADED"})
_DELISTED_STATUSES = frozenset({"DELISTED", "DELISTING", "TERMINATED", "RETIRED", "INACTIVE"})


def _value(row: object, *keys: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        for key in keys:
            if key in row:
                return row[key]
        return default
    for key in keys:
        if hasattr(row, key):
            return getattr(row, key)
    return default


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _serial(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _serial(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serial(item) for item in value]
    return value


def _quality_grade(status: Any, *, fallback: bool = False, coverage: float | None = None) -> str:
    normalized = str(getattr(status, "value", status) or "MISSING").upper()
    grade = {
        "VALID": "A",
        "DEGRADED": "B",
        "STALE": "C",
        "MISSING": "F",
        "INVALID": "F",
        "CONFLICT": "F",
    }.get(normalized, "F")
    if fallback and grade == "A":
        grade = "B"
    if coverage is not None and coverage < 0.95 and grade in {"A", "B"}:
        grade = "C"
    return grade


def _metric_status(value: Any) -> str:
    return "available" if value is not None else "unavailable"


def _percentile(current: float | None, values: Iterable[float | None], *, minimum: int = 250) -> float | None:
    if current is None:
        return None
    samples = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if len(samples) < minimum:
        return None
    return sum(value <= current for value in samples) / len(samples)


def _quote_rows(snapshot: Any) -> list[Any]:
    values = _value(snapshot, "quotes", "items", default=snapshot)
    if isinstance(values, Mapping):
        return list(values.values())
    if isinstance(values, Iterable) and not isinstance(values, (str, bytes)):
        return list(values)
    return []


def _quote_quality(row: Any) -> str:
    return str(getattr(_value(row, "quality_status", "quality", default="MISSING"), "value", _value(row, "quality_status", "quality", default="MISSING"))).upper()


def _quote_as_of(snapshot: Any, rows: list[Any], *, fallback: datetime) -> datetime:
    for value in (
        _value(snapshot, "completed_at", "captured_at", "fetched_at"),
        *(_value(row, "source_timestamp", "fetched_at", "captured_at") for row in rows),
    ):
        parsed = _as_datetime(value)
        if parsed is not None:
            return parsed
    return fallback.astimezone(UTC)


def _is_b_share(row: SecurityMaster) -> bool:
    metadata = row.raw_metadata_json if isinstance(row.raw_metadata_json, Mapping) else {}
    subtype = str(
        metadata.get("security_subtype")
        or metadata.get("asset_type")
        or metadata.get("share_class")
        or ""
    ).upper()
    return bool(metadata.get("is_b_share")) or subtype in {"B", "B_SHARE", "BSHARE"} or row.code.startswith(("200", "900"))


def _is_suspended(identity: SecurityMaster, quote: Any | None) -> bool:
    return bool(identity.is_suspended or _value(quote, "is_suspended", "suspended", default=False))


def _index_code(value: Any) -> str:
    text = str(value or "").strip().upper().replace("SSE", "SH").replace("SZSE", "SZ")
    if text.endswith(".SH") or text.endswith(".SZ") or text.endswith(".BJ"):
        return text
    code = normalize_security_code(text)
    if not code:
        return ""
    if text.startswith("39"):
        return f"{code}.SZ"
    return f"{code}.SH"


def _rows_from_response(response: Any) -> list[dict[str, Any]]:
    data = _value(response, "data", default=response)
    if isinstance(data, Mapping):
        for key in ("items", "rows", "list", "data", "result"):
            candidate = data.get(key)
            if isinstance(candidate, Mapping):
                candidate = list(candidate.values())
            if isinstance(candidate, Iterable) and not isinstance(candidate, (str, bytes)):
                return [dict(item) for item in candidate if isinstance(item, Mapping)]
        return [dict(data)] if any(key in data for key in ("thscode", "code", "symbol", "ticker")) else []
    if isinstance(data, Iterable) and not isinstance(data, (str, bytes)):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    return []


class MarketFoundationService:
    """Build one coherent market overview from one batch snapshot.

    Callers may inject loaders in tests. Runtime defaults use the existing
    all-A snapshot service and the existing Fuyao index facade, both in a
    bounded batch form.
    """

    def __init__(
        self,
        db: Session,
        *,
        quote_snapshot_loader: Callable[..., Mapping[str, Any]] | None = None,
        index_snapshot_loader: Callable[..., Any] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.session_service = MarketSessionService(db)
        self.quote_snapshot_loader = quote_snapshot_loader
        self.index_snapshot_loader = index_snapshot_loader
        self.now = now

    def _now(self, value: datetime | None) -> datetime:
        moment = value or (self.now() if self.now else datetime.now(UTC))
        return moment if moment.tzinfo else moment.replace(tzinfo=UTC)

    def _all_a_universe(self) -> list[SecurityMaster]:
        rows = get_market_universe(
            self.db,
            security_type="STOCK",
            include_suspended=True,
            include_inactive=False,
        )
        result = [
            row
            for row in rows
            if str(row.exchange or "").upper() in {"SSE", "SZSE", "BSE"}
            and str(row.status or "").upper() not in _DELISTED_STATUSES
            and not _is_b_share(row)
        ]
        return sorted(result, key=lambda row: (str(row.exchange or ""), row.code, row.id))

    def _load_quote_snapshot(self, *, trade_date: date, session: MarketSessionResolution) -> Mapping[str, Any]:
        if self.quote_snapshot_loader is not None:
            return self.quote_snapshot_loader(trade_date=trade_date, session=session)
        ttl = 5.0 if session.data_basis == MarketDataBasis.LIVE.value else 300.0
        return get_cached_all_a_share_quote_snapshot(
            self.db,
            trade_date=trade_date,
            include_bse=True,
            include_suspended=True,
            max_age_seconds=ttl,
            failure_ttl_seconds=5.0,
        )

    def _load_major_index_rows(self) -> tuple[list[dict[str, Any]], str | None]:
        try:
            if self.index_snapshot_loader is not None:
                return _rows_from_response(self.index_snapshot_loader(codes=[item["code"] for item in MAJOR_INDEX_DEFINITIONS])), None
            response = FuyaoDataProvider().get_index_snapshot(item["code"] for item in MAJOR_INDEX_DEFINITIONS)
            return _rows_from_response(response), None
        except Exception as exc:  # noqa: BLE001 - preserve partial market facts.
            return [], exc.__class__.__name__

    def _major_indices(self, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        by_code: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            code = _index_code(_value(row, "thscode", "code", "symbol", "ticker"))
            if code:
                by_code.setdefault(code, row)

        identity_rows = list(
            self.db.execute(
                select(SecurityMaster).where(
                    SecurityMaster.market == "CN",
                    SecurityMaster.security_type.in_(("INDEX", "INDICES")),
                )
            ).scalars()
        )
        identity_by_code = {
            canonical_security_code(row.code, row.exchange): row
            for row in identity_rows
            if canonical_security_code(row.code, row.exchange)
        }
        output: list[dict[str, Any]] = []
        for definition in MAJOR_INDEX_DEFINITIONS:
            raw = by_code.get(definition["code"])
            identity = identity_by_code.get(definition["code"])
            quality = _quote_quality(raw) if raw is not None else "MISSING"
            last = _number(_value(raw, "last", "last_price", "price", "close"))
            prev_close = _number(_value(raw, "prev_close", "pre_close", "previous_close"))
            change = _number(_value(raw, "change", "price_change", "change_value"))
            if change is None and last is not None and prev_close is not None:
                change = last - prev_close
            change_pct = _number(_value(raw, "change_pct", "pct_change", "price_change_ratio_pct"))
            if change_pct is None and last is not None and prev_close not in (None, 0):
                change_pct = (last / prev_close - 1.0) * 100.0
            as_of = _as_datetime(_value(raw, "as_of", "source_timestamp", "fetched_at", "captured_at"))
            item = {
                    "instrument_id": str(identity.id) if identity is not None else None,
                    "code": definition["code"],
                    "name": str(_value(raw, "name", "index_name", "ticker", default=definition["name"]) or definition["name"]),
                    "last": last,
                    "change": change,
                    "change_pct": change_pct,
                    "open": _number(_value(raw, "open", "open_price")),
                    "high": _number(_value(raw, "high", "high_price")),
                    "low": _number(_value(raw, "low", "low_price")),
                    "prev_close": prev_close,
                    "volume": _number(_value(raw, "volume")),
                    "turnover": _number(_value(raw, "turnover", "amount")),
                    "as_of": as_of,
                    "source": str(_value(raw, "source", "provider", default=MAJOR_INDEX_SOURCE) or MAJOR_INDEX_SOURCE) if raw is not None else None,
                    "quality": quality,
                    "status": "available" if last is not None and quality in _USABLE_QUOTE_QUALITIES else "unavailable",
                    "fallback": bool(_integer(_value(raw, "fallback_level", default=0)) or 0),
                }
            item["missing_fields"] = [
                name for name in ("last", "change", "change_pct", "open", "high", "low", "prev_close", "volume", "turnover")
                if item[name] is None
            ]
            output.append(item)
        return output

    def _foundation_history(self, through: date) -> list[MarketMetricSnapshot]:
        latest = (
            select(
                MarketMetricSnapshot.trade_date.label("trade_date"),
                func.max(MarketMetricSnapshot.captured_at).label("captured_at"),
            )
            .where(
                MarketMetricSnapshot.market == "CN",
                MarketMetricSnapshot.calculation_version == SYSTEMIC_MARKET_VERSION,
                MarketMetricSnapshot.trade_date < through,
            )
            .group_by(MarketMetricSnapshot.trade_date)
            .subquery()
        )
        return list(
            self.db.execute(
                select(MarketMetricSnapshot)
                .join(
                    latest,
                    (MarketMetricSnapshot.trade_date == latest.c.trade_date)
                    & (MarketMetricSnapshot.captured_at == latest.c.captured_at),
                )
                .where(
                    MarketMetricSnapshot.market == "CN",
                    MarketMetricSnapshot.calculation_version == SYSTEMIC_MARKET_VERSION,
                )
                .order_by(MarketMetricSnapshot.trade_date.asc())
            ).scalars()
        )

    def _median_history(self, through: date) -> list[AllAMedianIndexDaily]:
        return list(
            self.db.execute(
                select(AllAMedianIndexDaily)
                .where(
                    AllAMedianIndexDaily.market == "CN",
                    AllAMedianIndexDaily.calculation_version == SYSTEMIC_MARKET_VERSION,
                    AllAMedianIndexDaily.trade_date < through,
                )
                .order_by(AllAMedianIndexDaily.trade_date.asc())
            ).scalars()
        )

    @staticmethod
    def _history_payload(row: MarketMetricSnapshot) -> Mapping[str, Any]:
        metrics = row.metrics_json if isinstance(row.metrics_json, Mapping) else {}
        foundation = metrics.get("foundation") if isinstance(metrics.get("foundation"), Mapping) else {}
        return foundation

    def _historical_comparisons(
        self,
        *,
        trading_date: date,
        median_value: float | None,
        concentration_ratio: float | None,
        total_turnover: float | None,
    ) -> dict[str, Any]:
        metric_rows = self._foundation_history(trading_date)
        histories = [self._history_payload(row) for row in metric_rows]
        median_rows = self._median_history(trading_date)

        median_values = [float(row.index_value) for row in median_rows if row.index_value is not None]
        current_median_values = [*median_values, median_value] if median_value is not None else median_values
        median_trend = None
        if median_value is not None and len(median_values) >= 20 and median_values[-20] != 0:
            median_trend = median_value / median_values[-20] - 1.0

        concentration_history = [
            _number(_value(item.get("turnover_concentration", {}), "ratio"))
            for item in histories
            if isinstance(item, Mapping)
        ]
        concentration_history = [value for value in concentration_history if value is not None]
        concentration_avg = fmean(concentration_history[-20:]) if len(concentration_history) >= 20 else None

        turnover_history = [
            _number(_value(item.get("total_turnover", {}), "value"))
            for item in histories
            if isinstance(item, Mapping)
        ]
        turnover_history = [value for value in turnover_history if value is not None]
        turnover_avg = fmean(turnover_history[-20:]) if len(turnover_history) >= 20 else None
        return {
            "median_trend_20d": median_trend,
            "median_percentile_250d": _percentile(median_value, current_median_values),
            "concentration_avg_20d": concentration_avg,
            "concentration_percentile_250d": _percentile(
                concentration_ratio,
                [*concentration_history, concentration_ratio],
            ),
            "turnover_avg_20d": turnover_avg,
        }

    def _aggregate_all_a(
        self,
        snapshot: Mapping[str, Any],
        *,
        session: MarketSessionResolution,
        now: datetime,
    ) -> tuple[dict[str, Any], datetime, list[str]]:
        rows = _quote_rows(snapshot)
        quote_as_of = _quote_as_of(snapshot, rows, fallback=now)
        quote_by_code: dict[str, Any] = {}
        for row in rows:
            code = normalize_security_code(_value(row, "code", "symbol"))
            if code:
                quote_by_code.setdefault(code, row)

        snapshot_quality = str(_value(snapshot, "quality_status", default="MISSING") or "MISSING").upper()
        fallback_used = bool(_integer(_value(snapshot, "fallback_level", default=0)) or 0)
        coverage = _number(_value(snapshot, "coverage_ratio"))
        quality_grade = _quality_grade(snapshot_quality, fallback=fallback_used, coverage=coverage)
        quality_flags: list[str] = []
        if snapshot_quality != "VALID":
            quality_flags.append(f"SNAPSHOT_{snapshot_quality}")
        if fallback_used:
            quality_flags.append("PROVIDER_FALLBACK")
        if bool(_value(_value(snapshot, "metadata", default={}), "market_foundation_cache_hit", default=False)):
            quality_flags.append("CACHE_HIT")

        universe = self._all_a_universe()
        universe_total = len(universe)
        excluded_counts: dict[str, int] = {}
        returns: list[float] = []
        turnover_rows: list[tuple[float, str]] = []
        advancers = decliners = unchanged = suspended = limit_up = limit_down = 0
        for identity in universe:
            quote = quote_by_code.get(identity.code)
            if _is_suspended(identity, quote):
                suspended += 1
                continue
            if quote is None:
                excluded_counts["quote_missing"] = excluded_counts.get("quote_missing", 0) + 1
                continue
            if _quote_quality(quote) not in _USABLE_QUOTE_QUALITIES:
                excluded_counts["quote_quality_invalid"] = excluded_counts.get("quote_quality_invalid", 0) + 1
                continue
            price = _number(_value(quote, "price", "last", "close"))
            prev_close = _number(_value(quote, "prev_close", "previous_close", "pre_close"))
            if price is None or prev_close is None or price <= 0 or prev_close <= 0:
                excluded_counts["invalid_price_or_prev_close"] = excluded_counts.get("invalid_price_or_prev_close", 0) + 1
                continue
            individual_return = price / prev_close - 1.0
            if not math.isfinite(individual_return) or abs(individual_return) > 0.60:
                excluded_counts["implausible_return"] = excluded_counts.get("implausible_return", 0) + 1
                continue
            returns.append(individual_return)
            if individual_return > 0:
                advancers += 1
            elif individual_return < 0:
                decliners += 1
            else:
                unchanged += 1
            if is_price_limit(
                individual_return * 100.0,
                identity.code,
                board=identity.board,
                is_st=bool(identity.is_st),
                direction="up",
            ):
                limit_up += 1
            if is_price_limit(
                individual_return * 100.0,
                identity.code,
                board=identity.board,
                is_st=bool(identity.is_st),
                direction="down",
            ):
                limit_down += 1
            amount = _number(_value(quote, "amount", "turnover"))
            if amount is not None and amount >= 0:
                turnover_rows.append((amount, identity.code))

        eligible_count = len(returns)
        excluded_count = sum(excluded_counts.values())
        daily_median_return = float(median(returns)) if returns else None
        turnover_rows.sort(key=lambda item: (-item[0], item[1]))
        turnover_eligible_count = len(turnover_rows)
        top_n = int(math.ceil(turnover_eligible_count * 0.05)) if turnover_eligible_count else 0
        total_turnover = sum(amount for amount, _ in turnover_rows)
        top_turnover = sum(amount for amount, _ in turnover_rows[:top_n])
        concentration_ratio = top_turnover / total_turnover if total_turnover > 0 else None
        if turnover_eligible_count and total_turnover <= 0:
            quality_flags.append("TOTAL_TURNOVER_NON_POSITIVE")

        median_history = self._median_history(session.latest_valid_trading_date or session.trading_date)
        previous_index = median_history[-1].index_value if median_history else None
        median_index = next_median_index(previous_index, daily_median_return) if daily_median_return is not None else None
        comparisons = self._historical_comparisons(
            trading_date=session.latest_valid_trading_date or session.trading_date,
            median_value=median_index,
            concentration_ratio=concentration_ratio,
            total_turnover=total_turnover if total_turnover > 0 else None,
        )
        source_status = snapshot_quality
        base = {
            "quality_grade": quality_grade,
            "quality_flags": sorted(set(quality_flags)),
            "source_status": source_status,
            "as_of": quote_as_of,
            "trading_date": session.latest_valid_trading_date or session.trading_date,
            "calculation_version": SYSTEMIC_MARKET_VERSION,
        }
        all_a_median = {
            "status": _metric_status(median_index),
            "current_value": median_index,
            "daily_median_return": daily_median_return,
            "trend_20d": comparisons["median_trend_20d"],
            "percentile_250d": comparisons["median_percentile_250d"],
            "eligible_count": eligible_count,
            "universe_total": universe_total,
            "excluded_count": excluded_count,
            "suspended_count": suspended,
            "universe_version": ALL_A_UNIVERSE_VERSION,
            **base,
        }
        all_a_median["missing_fields"] = [
            name for name in ("current_value", "daily_median_return", "trend_20d", "percentile_250d")
            if all_a_median[name] is None
        ]
        concentration = {
            "status": _metric_status(concentration_ratio),
            "ratio": concentration_ratio,
            "avg_20d": comparisons["concentration_avg_20d"],
            "delta_vs_20d": (
                concentration_ratio - comparisons["concentration_avg_20d"]
                if concentration_ratio is not None and comparisons["concentration_avg_20d"] is not None
                else None
            ),
            "trend": (
                "rising"
                if concentration_ratio is not None and comparisons["concentration_avg_20d"] is not None and concentration_ratio > comparisons["concentration_avg_20d"]
                else "falling"
                if concentration_ratio is not None and comparisons["concentration_avg_20d"] is not None and concentration_ratio < comparisons["concentration_avg_20d"]
                else "flat"
                if concentration_ratio is not None and comparisons["concentration_avg_20d"] is not None
                else "unavailable"
            ),
            "percentile_250d": comparisons["concentration_percentile_250d"],
            "top_n": top_n,
            "eligible_count": turnover_eligible_count,
            "total_turnover": total_turnover if total_turnover > 0 else None,
            **base,
        }
        concentration["missing_fields"] = [
            name for name in ("ratio", "avg_20d", "delta_vs_20d", "percentile_250d")
            if concentration[name] is None
        ]
        breadth = {
            "status": "available" if eligible_count else "unavailable",
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
            "suspended": suspended,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total": eligible_count + suspended,
            "eligible_count": eligible_count,
            "advance_decline_ratio": advancers / decliners if decliners else None,
            **base,
        }
        breadth["missing_fields"] = ["advance_decline_ratio"] if breadth["advance_decline_ratio"] is None else []
        turnover = {
            "status": _metric_status(total_turnover if total_turnover > 0 else None),
            "value": total_turnover if total_turnover > 0 else None,
            "avg_20d": comparisons["turnover_avg_20d"],
            "delta_vs_20d": (
                total_turnover - comparisons["turnover_avg_20d"]
                if total_turnover > 0 and comparisons["turnover_avg_20d"] is not None
                else None
            ),
            "unit": "CNY",
            **base,
        }
        turnover["missing_fields"] = [
            name for name in ("value", "avg_20d", "delta_vs_20d") if turnover[name] is None
        ]
        return {
            "all_a_median": all_a_median,
            "turnover_concentration": concentration,
            "breadth": breadth,
            "total_turnover": turnover,
            "universe_counts": {
                "universe_total": universe_total,
                "eligible_count": eligible_count,
                "excluded_count": excluded_count,
                "suspended_count": suspended,
                "exclusion_counts": excluded_counts,
            },
        }, quote_as_of, quality_flags

    def _risk_snapshot(
        self,
        *,
        all_a_median: Mapping[str, Any],
        concentration: Mapping[str, Any],
        breadth: Mapping[str, Any],
        total_turnover: Mapping[str, Any],
        major_indices: list[dict[str, Any]],
        trading_date: date,
        quality_flags: list[str],
    ) -> dict[str, Any]:
        risk_factors: list[str] = []
        median_return = _number(all_a_median.get("daily_median_return"))
        if median_return is not None and median_return < 0:
            risk_factors.append("MEDIAN_STOCK_WEAK")
        if int(breadth.get("advancers") or 0) < int(breadth.get("decliners") or 0):
            risk_factors.append("BREADTH_WEAK")
        ratio = _number(concentration.get("ratio"))
        avg_ratio = _number(concentration.get("avg_20d"))
        if ratio is not None and avg_ratio is not None and ratio > avg_ratio:
            risk_factors.append("TURNOVER_CONCENTRATED")
        amount = _number(total_turnover.get("value"))
        avg_amount = _number(total_turnover.get("avg_20d"))
        if amount is not None and avg_amount is not None and amount < avg_amount:
            risk_factors.append("LIQUIDITY_LOW")
        if int(breadth.get("limit_down") or 0) > int(breadth.get("limit_up") or 0):
            risk_factors.append("LIMIT_DOWN_EXPANSION")
        available_changes = [
            _number(row.get("change_pct"))
            for row in major_indices
            if row.get("status") == "available"
        ]
        available_changes = [value for value in available_changes if value is not None]
        if available_changes and min(available_changes) < 0 < max(available_changes):
            risk_factors.append("INDEX_DIVERGENCE")

        critical_unavailable = any(
            metric.get("status") != "available"
            for metric in (all_a_median, concentration, breadth, total_turnover)
        )
        if critical_unavailable:
            quality_flags.append("CRITICAL_MARKET_DATA_UNAVAILABLE")
        score_row = self.db.execute(
            select(MarketScoreSnapshot)
            .where(
                MarketScoreSnapshot.market == "CN",
                MarketScoreSnapshot.trade_date == trading_date,
                MarketScoreSnapshot.quality_status.in_(("VALID", "DEGRADED")),
                MarketScoreSnapshot.is_frozen.is_(False),
            )
            .order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        regime_to_risk = {
            "STRONG_RISK_OFF": "EXTREME",
            "RISK_OFF": "HIGH",
            "NEUTRAL": "MEDIUM",
            "RISK_ON": "LOW",
            "STRONG_RISK_ON": "LOW",
        }
        if critical_unavailable or score_row is None:
            risk_level = "UNKNOWN"
            risk_score = None
            if score_row is None:
                quality_flags.append("CURRENT_MARKET_SCORE_UNAVAILABLE")
        else:
            risk_level = regime_to_risk.get(str(score_row.regime or "").upper(), "UNKNOWN")
            risk_score = score_row.display_score
        if quality_flags:
            risk_factors.append("DATA_DEGRADED")
        missing_fields = [
            name
            for name, metric in (
                ("median_return", all_a_median),
                ("turnover_concentration", concentration),
                ("breadth", breadth),
                ("total_turnover", total_turnover),
            )
            if metric.get("status") != "available"
        ]
        if risk_score is None:
            missing_fields.append("risk_score")
        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "median_return": all_a_median,
            "turnover_concentration": concentration,
            "breadth": breadth,
            "total_turnover": total_turnover,
            "major_indices": major_indices,
            "risk_factors": sorted(set(risk_factors)),
            "data_quality": _worst_grade(
                str(all_a_median.get("quality_grade") or "F"),
                str(concentration.get("quality_grade") or "F"),
                str(breadth.get("quality_grade") or "F"),
                str(total_turnover.get("quality_grade") or "F"),
            ),
            "quality_flags": sorted(set(quality_flags)),
            "missing_fields": missing_fields,
            "source_status": "MARKET_SCORE" if score_row is not None else "MISSING",
            "as_of": all_a_median.get("as_of"),
            "trading_date": trading_date,
            "calculation_version": SYSTEMIC_MARKET_VERSION,
        }

    def build_from_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        now: datetime | None = None,
        session: MarketSessionResolution | None = None,
        major_index_rows: Iterable[Mapping[str, Any]] | None = None,
        persist_daily: bool = False,
    ) -> dict[str, Any]:
        """Build facts from an already-batched snapshot without a new quote call."""

        started = time.perf_counter()
        moment = self._now(now)
        resolved = session or self.session_service.resolve_session(moment)
        aggregate, quote_as_of, quality_flags = self._aggregate_all_a(snapshot, session=resolved, now=moment)
        index_error = None
        if major_index_rows is None:
            raw_indices, index_error = self._load_major_index_rows()
        else:
            raw_indices = [dict(row) for row in major_index_rows]
        major_indices = self._major_indices(raw_indices)
        if index_error is not None:
            quality_flags.append("MAJOR_INDICES_SOURCE_UNAVAILABLE")
        if any(row["status"] == "unavailable" for row in major_indices):
            quality_flags.append("MAJOR_INDEX_UNAVAILABLE")
            for metric in (aggregate["all_a_median"], aggregate["turnover_concentration"], aggregate["breadth"], aggregate["total_turnover"]):
                if metric["quality_grade"] == "A":
                    metric["quality_grade"] = "B"
                metric["quality_flags"] = sorted(set([*metric["quality_flags"], "MAJOR_INDEX_UNAVAILABLE"]))
        trading_date = aggregate["all_a_median"]["trading_date"]
        risk = self._risk_snapshot(
            all_a_median=aggregate["all_a_median"],
            concentration=aggregate["turnover_concentration"],
            breadth=aggregate["breadth"],
            total_turnover=aggregate["total_turnover"],
            major_indices=major_indices,
            trading_date=trading_date,
            quality_flags=quality_flags,
        )
        overview = {
            "session": resolved.to_dict(),
            "major_indices": major_indices,
            "systemic_risk": risk,
            "breadth": aggregate["breadth"],
            "all_a_median": aggregate["all_a_median"],
            "turnover_concentration": aggregate["turnover_concentration"],
            "total_turnover": aggregate["total_turnover"],
            "quote_as_of": quote_as_of,
            "_universe_counts": aggregate["universe_counts"],
        }
        if persist_daily:
            self.persist_daily_snapshot(overview, snapshot=snapshot)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "market_overview_build_ms=%s session_resolution_ms=%s universe_count=%s eligible_count=%s provider=%s quality=%s cache_hit=%s",
            duration_ms,
            0,
            aggregate["universe_counts"]["universe_total"],
            aggregate["universe_counts"]["eligible_count"],
            _value(snapshot, "provider", default="unknown"),
            risk["data_quality"],
            bool(_value(_value(snapshot, "metadata", default={}), "market_foundation_cache_hit", default=False)),
        )
        return overview

    def _unique_capture(self, *, trade_date: date, captured_at: datetime, existing: MarketMetricSnapshot | None) -> datetime:
        candidate = captured_at
        if candidate.tzinfo is not None:
            candidate = candidate.astimezone(UTC).replace(tzinfo=None)
        for offset in range(0, 1000):
            value = candidate + timedelta(microseconds=offset)
            collision_id = self.db.execute(
                select(MarketMetricSnapshot.id).where(
                    MarketMetricSnapshot.market == "CN",
                    MarketMetricSnapshot.trade_date == trade_date,
                    MarketMetricSnapshot.captured_at == value,
                )
            ).scalar_one_or_none()
            if collision_id is None or (existing is not None and collision_id == existing.id):
                return value
        raise RuntimeError("market_metric_capture_collision")

    def persist_daily_snapshot(self, overview: Mapping[str, Any], *, snapshot: Mapping[str, Any]) -> None:
        """Persist one compact, versioned end-of-session aggregate.

        This reuses existing metric and median-index tables. It writes no raw
        quote rows and keeps the version distinct from the frozen Market Score
        universe/algorithm.
        """

        session = overview.get("session") if isinstance(overview.get("session"), Mapping) else {}
        if session.get("data_basis") != MarketDataBasis.SESSION_CLOSE.value or session.get("session") != MarketSession.CLOSED.value:
            return
        median_metric = overview.get("all_a_median") if isinstance(overview.get("all_a_median"), Mapping) else {}
        turnover_metric = overview.get("turnover_concentration") if isinstance(overview.get("turnover_concentration"), Mapping) else {}
        breadth = overview.get("breadth") if isinstance(overview.get("breadth"), Mapping) else {}
        total_turnover = overview.get("total_turnover") if isinstance(overview.get("total_turnover"), Mapping) else {}
        if any(metric.get("status") != "available" for metric in (median_metric, turnover_metric, breadth, total_turnover)):
            return
        trade_date = _as_date(median_metric.get("trading_date"))
        captured_at = _as_datetime(overview.get("quote_as_of"))
        if trade_date is None or captured_at is None:
            return
        existing = self.db.execute(
            select(MarketMetricSnapshot)
            .where(
                MarketMetricSnapshot.market == "CN",
                MarketMetricSnapshot.trade_date == trade_date,
                MarketMetricSnapshot.calculation_version == SYSTEMIC_MARKET_VERSION,
            )
            .order_by(MarketMetricSnapshot.captured_at.desc(), MarketMetricSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        capture = self._unique_capture(trade_date=trade_date, captured_at=captured_at, existing=existing)
        universe_counts = overview.get("_universe_counts") if isinstance(overview.get("_universe_counts"), Mapping) else {}
        grade = str(overview.get("systemic_risk", {}).get("data_quality") if isinstance(overview.get("systemic_risk"), Mapping) else "F")
        quality_status = "VALID" if grade == "A" else "DEGRADED" if grade in {"B", "C"} else "MISSING"
        values = {
            "market_snapshot_id": str(_value(snapshot, "snapshot_id", default="") or "") or None,
            "market": "CN",
            "trade_date": trade_date,
            "captured_at": capture,
            "universe_rule_version": ALL_A_UNIVERSE_VERSION,
            "calculation_version": SYSTEMIC_MARKET_VERSION,
            "score_config_version": SYSTEMIC_MARKET_VERSION,
            "universe_total": int(universe_counts.get("universe_total") or 0),
            "included_count": int(universe_counts.get("eligible_count") or 0),
            "excluded_count": int(universe_counts.get("excluded_count") or 0),
            "coverage": _number(_value(snapshot, "coverage_ratio")) or 0.0,
            "median_return": _number(median_metric.get("daily_median_return")),
            "advance_ratio": (
                int(breadth.get("advancers") or 0) / int(breadth.get("eligible_count") or 0)
                if int(breadth.get("eligible_count") or 0)
                else None
            ),
            "top5_concentration": _number(turnover_metric.get("ratio")),
            "total_amount": _number(total_turnover.get("value")),
            "quality_status": quality_status,
            "confidence": {"A": 100.0, "B": 80.0, "C": 60.0}.get(grade, 0.0),
            "metrics_json": {"foundation": _serial({key: value for key, value in overview.items() if key != "session"})},
            "breadth_metrics_json": _serial(breadth),
            "trend_metrics_json": _serial(median_metric),
            "liquidity_metrics_json": _serial(total_turnover),
            "profitability_metrics_json": None,
            "diffusion_metrics_json": None,
            "crowding_metrics_json": _serial(turnover_metric),
            "tail_risk_metrics_json": _serial(overview.get("systemic_risk")),
            "exclusion_counts_json": _serial(universe_counts.get("exclusion_counts") or {}),
        }
        if existing is None:
            row = MarketMetricSnapshot(snapshot_id=f"systemic-{uuid4()}", **values)
            self.db.add(row)
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        median_return = _number(median_metric.get("daily_median_return"))
        median_index = _number(median_metric.get("current_value"))
        if median_return is None or median_index is None:
            self.db.flush()
            return
        daily = self.db.execute(
            select(AllAMedianIndexDaily).where(
                AllAMedianIndexDaily.market == "CN",
                AllAMedianIndexDaily.trade_date == trade_date,
                AllAMedianIndexDaily.calculation_version == SYSTEMIC_MARKET_VERSION,
            )
        ).scalar_one_or_none()
        if daily is None:
            daily = AllAMedianIndexDaily(
                market="CN",
                trade_date=trade_date,
                median_return=median_return,
                index_value=median_index,
                eligible_count=int(median_metric.get("eligible_count") or 0),
                quality_status=quality_status,
                calculation_version=SYSTEMIC_MARKET_VERSION,
                available_at=capture,
            )
            self.db.add(daily)
        else:
            daily.median_return = median_return
            daily.index_value = median_index
            daily.eligible_count = int(median_metric.get("eligible_count") or 0)
            daily.quality_status = quality_status
            daily.available_at = capture
        self.db.flush()

    def _empty_overview(self, *, session: MarketSessionResolution) -> dict[str, Any]:
        common = {
            "quality_grade": "F",
            "quality_flags": ["MARKET_DATA_UNAVAILABLE"],
            "source_status": "MISSING",
            "as_of": None,
            "trading_date": session.latest_valid_trading_date,
            "calculation_version": SYSTEMIC_MARKET_VERSION,
        }
        all_a_median = {
            "status": "unavailable", "current_value": None, "daily_median_return": None,
            "trend_20d": None, "percentile_250d": None, "eligible_count": 0,
            "universe_total": 0, "excluded_count": 0, "suspended_count": 0,
            "universe_version": ALL_A_UNIVERSE_VERSION, **common,
        }
        all_a_median["missing_fields"] = ["current_value", "daily_median_return", "trend_20d", "percentile_250d"]
        concentration = {
            "status": "unavailable", "ratio": None, "avg_20d": None, "delta_vs_20d": None,
            "trend": "unavailable", "percentile_250d": None, "top_n": 0, "eligible_count": 0,
            "total_turnover": None, **common,
        }
        concentration["missing_fields"] = ["ratio", "avg_20d", "delta_vs_20d", "percentile_250d"]
        breadth = {
            "status": "unavailable", "advancers": 0, "decliners": 0, "unchanged": 0,
            "suspended": 0, "limit_up": 0, "limit_down": 0, "total": 0, "eligible_count": 0,
            "advance_decline_ratio": None, **common,
        }
        breadth["missing_fields"] = ["advance_decline_ratio"]
        turnover = {
            "status": "unavailable", "value": None, "avg_20d": None, "delta_vs_20d": None,
            "unit": "CNY", **common,
        }
        turnover["missing_fields"] = ["value", "avg_20d", "delta_vs_20d"]
        indices = self._major_indices([])
        risk = self._risk_snapshot(
            all_a_median=all_a_median,
            concentration=concentration,
            breadth=breadth,
            total_turnover=turnover,
            major_indices=indices,
            trading_date=session.latest_valid_trading_date or session.trading_date,
            quality_flags=["MARKET_DATA_UNAVAILABLE", "MAJOR_INDEX_UNAVAILABLE"],
        )
        return {
            "session": session.to_dict(), "major_indices": indices, "systemic_risk": risk,
            "breadth": breadth, "all_a_median": all_a_median,
            "turnover_concentration": concentration, "total_turnover": turnover,
            "quote_as_of": None,
        }

    def _latest_persisted_overview(self, *, through: date, session: MarketSessionResolution) -> dict[str, Any] | None:
        row = self.db.execute(
            select(MarketMetricSnapshot)
            .where(
                MarketMetricSnapshot.market == "CN",
                MarketMetricSnapshot.calculation_version == SYSTEMIC_MARKET_VERSION,
                MarketMetricSnapshot.trade_date <= through,
            )
            .order_by(MarketMetricSnapshot.trade_date.desc(), MarketMetricSnapshot.captured_at.desc(), MarketMetricSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        payload = self._history_payload(row)
        if not payload:
            return None
        result = deepcopy(dict(payload))
        result["session"] = session.to_dict()
        result.pop("_universe_counts", None)
        return result

    def overview(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Resolve data basis first, then issue at most one all-A batch request."""

        started = time.perf_counter()
        moment = self._now(now)
        session_started = time.perf_counter()
        preliminary = self.session_service.resolve_session(moment)
        session_resolution_ms = round((time.perf_counter() - session_started) * 1000, 2)
        if preliminary.data_basis == MarketDataBasis.PREVIOUS_SESSION_CLOSE.value:
            source_date = preliminary.previous_trading_date if preliminary.is_trading_day else preliminary.latest_valid_trading_date
            persisted = self._latest_persisted_overview(through=source_date, session=preliminary) if source_date else None
            result = persisted or self._empty_overview(
                session=self.session_service.resolve_session(moment, data_status="unavailable")
            )
            logger.info(
                "market_overview_build_ms=%s session_resolution_ms=%s universe_count=%s eligible_count=%s provider=%s quality=%s cache_hit=%s",
                round((time.perf_counter() - started) * 1000, 2), session_resolution_ms, 0, 0,
                "persisted", result["systemic_risk"]["data_quality"], False,
            )
            return result

        trade_date = preliminary.latest_valid_trading_date or preliminary.trading_date
        try:
            snapshot = self._load_quote_snapshot(trade_date=trade_date, session=preliminary)
            result = self.build_from_snapshot(snapshot, now=moment, session=preliminary)
        except Exception as exc:  # noqa: BLE001 - the session itself remains useful.
            logger.warning("market overview snapshot failed: %s", exc.__class__.__name__)
            return self._empty_overview(session=self.session_service.resolve_session(moment, data_status="abnormal"))

        critical_unavailable = any(
            result[key]["status"] != "available"
            for key in ("all_a_median", "turnover_concentration", "breadth", "total_turnover")
        )
        grade = result["systemic_risk"]["data_quality"]
        data_status = "abnormal" if critical_unavailable else "degraded" if grade != "A" else "ok"
        resolved = self.session_service.resolve_session(moment, data_status=data_status)
        result["session"] = resolved.to_dict()
        if resolved.session == MarketSession.CLOSED.value and result["systemic_risk"]["data_quality"] in {"A", "B", "C"}:
            try:
                self.persist_daily_snapshot(result, snapshot=snapshot)
                self.db.commit()
            except Exception:  # noqa: BLE001 - do not turn a readable overview into a 500.
                self.db.rollback()
                logger.exception("market foundation daily persistence failed")
        return result

    def major_indices(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = self._now(now)
        session = self.session_service.resolve_session(moment)
        if session.data_basis == MarketDataBasis.PREVIOUS_SESSION_CLOSE.value:
            source_date = session.previous_trading_date if session.is_trading_day else session.latest_valid_trading_date
            persisted = self._latest_persisted_overview(through=source_date, session=session) if source_date else None
            if persisted is not None:
                indices = persisted.get("major_indices")
                if isinstance(indices, list):
                    return indices
            return self._major_indices([])
        rows, _error = self._load_major_index_rows()
        return self._major_indices(rows)


__all__ = [
    "ALL_A_UNIVERSE_VERSION",
    "MAJOR_INDEX_DEFINITIONS",
    "MarketFoundationService",
    "SYSTEMIC_MARKET_VERSION",
]
