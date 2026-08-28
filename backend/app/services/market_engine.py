"""Orchestration and persistence for the deterministic Phase C Market Engine."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, time as datetime_time
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..market.engine import (
    build_market_score_universe,
    build_market_score_snapshot,
    calculate_all_components,
    calculate_cross_section_metrics,
    calculate_ma_breadth,
    calculate_ma_trend_metrics,
    calculate_new_high_low,
    next_median_index,
)
from ..market.engine.config import (
    COMPONENT_WEIGHTS,
    MARKET_ENGINE_VERSION,
    PERCENTILE_LOOKBACK_DAYS,
    SCORE_CONFIG_VERSION,
    SNAPSHOT_CAPTURE_SPAN_FULL_CONFIDENCE_SECONDS,
    UNIVERSE_RULE_VERSION,
)
from ..market.engine.models import ComponentScore
from ..market.engine.score import calculate_confidence
from ..market.codes import normalize_security_code
from ..market_models import SecurityMaster, TradingCalendar
from .trading_calendar import CHINA_TZ
from .market_snapshot_service import get_all_a_share_quote_snapshot, persist_snapshot
from .daily_bar_cache import load_daily_bars
from ..market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot, MarketScoreSnapshot

logger = logging.getLogger(__name__)


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


def _dt(value: Any, *, default: datetime | None = None) -> datetime | None:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return default
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _round_score(value: Any, digits: int = 1) -> float | None:
    number = None if value is None else float(value)
    if number is None:
        return None
    return round(number, digits)


def _serial(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serial(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _serial(value.to_dict())
    return value


def _value(row: object, key: str, default: Any = None) -> Any:
    return row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)


def _normalize_rows(values: Any) -> list[Any]:
    if values is None:
        return []
    if isinstance(values, Mapping):
        if "quotes" in values:
            values = values.get("quotes")
        elif "items" in values:
            values = values.get("items")
        elif all(isinstance(k, str) for k in values):
            values = list(values.values())
    if isinstance(values, (str, bytes)):
        return []
    try:
        return list(values)
    except TypeError:
        return []


def _quote_quality(row: object) -> str:
    return str(getattr(_value(row, "quality_status", "VALID"), "value", _value(row, "quality_status", "VALID"))).upper()


def _capture_from_quotes(rows: list[Any]) -> datetime | None:
    for row in rows:
        value = _value(row, "captured_at") or _value(row, "fetched_at")
        parsed = _dt(value)
        if parsed:
            return parsed
    return None


def _capture_span_seconds(snapshot: Mapping[str, Any] | None, rows: list[Any]) -> float | None:
    """Return the provider's cross-sectional capture span when available."""

    source: Mapping[str, Any] = snapshot or {}
    started = _dt(source.get("started_at"))
    completed = _dt(source.get("completed_at"))
    if started is None or completed is None:
        starts = [_dt(_value(row, "started_at")) for row in rows]
        completes = [_dt(_value(row, "completed_at")) for row in rows]
        starts = [value for value in starts if value is not None]
        completes = [value for value in completes if value is not None]
        if starts and completes:
            started, completed = min(starts), max(completes)
    if started is None or completed is None:
        return None
    return max(0.0, (completed - started).total_seconds())


def _source_provenance(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not snapshot:
        return {}
    metadata = snapshot.get("metadata") or {}
    return _serial({
        "provider": snapshot.get("provider"),
        "requested_route": metadata.get("requested_route"),
        "fallback_level": snapshot.get("fallback_level"),
        "quality_status": snapshot.get("quality_status"),
        "started_at": snapshot.get("started_at"),
        "completed_at": snapshot.get("completed_at"),
        "provider_counts": metadata.get("provider_counts") or {},
        "provider_endpoints": metadata.get("provider_endpoints") or {},
        "provider_source_timestamps": metadata.get("provider_source_timestamps") or {},
        "provider_quality_statuses": metadata.get("provider_quality_statuses") or {},
    })


def _quote_trade_dates(rows: Iterable[Any]) -> set[date]:
    dates: set[date] = set()
    for row in rows:
        value = _date(_value(row, "trade_date", _value(row, "date")))
        if value is not None:
            dates.add(value)
    return dates


def _included_quality(rows: Iterable[Any], included_codes: set[str]) -> str:
    """Aggregate quote quality only over the actual MarketScoreUniverse."""

    statuses = {
        _quote_quality(row)
        for row in rows
        if normalize_security_code(_value(row, "code", _value(row, "symbol"))) in included_codes
    }
    if statuses & {"INVALID", "CONFLICT", "STALE", "MISSING", "FROZEN"}:
        if "CONFLICT" in statuses:
            return "CONFLICT"
        if "INVALID" in statuses:
            return "INVALID"
        if "STALE" in statuses:
            return "STALE"
        return "MISSING"
    if "DEGRADED" in statuses:
        return "DEGRADED"
    return "VALID" if statuses else "MISSING"


def _calendar_for(db: Session, trade_date: date, rows: Iterable[Any] | None) -> list[Any]:
    if rows is not None:
        return list(rows)
    start = trade_date - timedelta(days=4000)
    return list(
        db.execute(
            select(TradingCalendar).where(
                TradingCalendar.market == "CN",
                TradingCalendar.trade_date >= start,
                TradingCalendar.trade_date <= trade_date,
            )
        ).scalars()
    )


def _identities_for(db: Session, rows: Iterable[Any] | None) -> list[Any]:
    if rows is not None:
        return list(rows)
    return list(db.execute(select(SecurityMaster).where(SecurityMaster.market == "CN")).scalars())


def _identity_map(rows: Iterable[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for row in rows:
        code = str(_value(row, "code", ""))
        if code:
            output[code] = row
    return output


def _history_rows(
    db: Session,
    codes: Iterable[str],
    history: Any,
    *,
    trade_date: date,
    captured_at: datetime,
    history_provider: Any | None,
) -> list[Any]:
    if history is not None:
        if isinstance(history, Mapping):
            flattened: list[Any] = []
            for code, bars in history.items():
                for bar in bars or []:
                    if isinstance(bar, Mapping) and not bar.get("code"):
                        flattened.append(dict(bar) | {"code": code})
                    else:
                        flattened.append(bar)
            return flattened
        return _normalize_rows(history)
    # Calculation is intentionally cache-only.  A provider is reserved for an
    # explicit bootstrap/sync job and must never turn one Score request into a
    # 5,000-name network fan-out.
    try:
        return load_daily_bars(
            db,
            codes,
            trade_date=trade_date,
            available_at=captured_at,
            limit=260,
        )
    except Exception:
        return []


def _history_coverage_codes(
    rows: Iterable[Any],
    *,
    universe_codes: set[str],
    trade_date: date,
    available_at: datetime,
) -> set[str]:
    """Count unique securities with at least one admissible historical bar."""

    codes: set[str] = set()
    for row in rows:
        code = normalize_security_code(_value(row, "code", _value(row, "symbol")))
        if not code or code not in universe_codes or _quote_quality(row) not in {"VALID", "DEGRADED"}:
            continue
        bar_date = _date(_value(row, "trade_date", _value(row, "date")))
        if bar_date is None or bar_date > trade_date:
            continue
        bar_available = _dt(_value(row, "available_at") or _value(row, "fetched_at"))
        if bar_available is not None and bar_available > available_at:
            continue
        if str(_value(row, "adjustment", "QFQ") or "QFQ").upper() != "QFQ":
            continue
        if _value(row, "close", _value(row, "price")) is None:
            continue
        codes.add(code)
    return codes


def _component_json(component: ComponentScore | None) -> dict[str, Any] | None:
    return _serial(component.to_dict()) if component else None


_COMPONENT_HISTORY_COLUMNS = {
    "breadth": "breadth_metrics_json",
    "trend": "trend_metrics_json",
    "liquidity": "liquidity_metrics_json",
    "profitability": "profitability_metrics_json",
    "diffusion": "diffusion_metrics_json",
    "crowding": "crowding_metrics_json",
    "tail_risk": "tail_risk_metrics_json",
}


def _component_histories(
    db: Session,
    *,
    trade_date: date,
    captured_at: datetime,
) -> dict[str, dict[str, list[float]]]:
    """Load one reliable metric sample per prior trading day without look-ahead."""

    lookback = int(PERCENTILE_LOOKBACK_DAYS["3y"])
    latest_capture = (
        select(
            MarketMetricSnapshot.trade_date.label("trade_date"),
            func.max(MarketMetricSnapshot.captured_at).label("captured_at"),
        )
        .where(
            MarketMetricSnapshot.market == "CN",
            MarketMetricSnapshot.trade_date < trade_date,
            MarketMetricSnapshot.captured_at <= captured_at,
            MarketMetricSnapshot.quality_status.in_(("VALID", "DEGRADED")),
        )
        .group_by(MarketMetricSnapshot.trade_date)
        .order_by(MarketMetricSnapshot.trade_date.desc())
        .limit(lookback)
        .subquery()
    )
    rows = list(
        db.execute(
            select(MarketMetricSnapshot)
            .join(
                latest_capture,
                and_(
                    MarketMetricSnapshot.trade_date == latest_capture.c.trade_date,
                    MarketMetricSnapshot.captured_at == latest_capture.c.captured_at,
                ),
            )
            .where(MarketMetricSnapshot.market == "CN")
            .order_by(MarketMetricSnapshot.trade_date.asc())
        ).scalars()
    )
    histories: dict[str, dict[str, list[float]]] = {
        component_name: {} for component_name in _COMPONENT_HISTORY_COLUMNS
    }
    for row in rows:
        common_metrics = row.metrics_json if isinstance(row.metrics_json, Mapping) else {}
        for component_name, column_name in _COMPONENT_HISTORY_COLUMNS.items():
            component_payload = getattr(row, column_name) or {}
            raw_metrics = (
                component_payload.get("raw_metrics", {})
                if isinstance(component_payload, Mapping)
                else {}
            )
            source = dict(common_metrics)
            if isinstance(raw_metrics, Mapping):
                source.update(raw_metrics)
            component_history = histories[component_name]
            for metric_name, raw_value in source.items():
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if value != value or abs(value) == float("inf"):
                    continue
                component_history.setdefault(str(metric_name), []).append(value)
    return histories


def _upsert_capture(db: Session, model: Any, *, market: str, trade_date: date, captured_at: datetime) -> Any | None:
    """Return an existing same-capture row so repeated calculations are idempotent."""
    return db.execute(
        select(model).where(
            model.market == market,
            model.trade_date == trade_date,
            model.captured_at == captured_at,
        )
    ).scalar_one_or_none()


def _copy_model_values(target: Any, source: Any) -> None:
    for column in source.__table__.columns:
        if column.name in {"id", "created_at"}:
            continue
        setattr(target, column.name, getattr(source, column.name))


def _drivers(components: Mapping[str, ComponentScore]) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    negative: list[str] = []
    for name, component in components.items():
        if component.score is None:
            continue
        if component.score >= 60:
            positive.append(f"{name}_strong")
        elif component.score <= 40:
            negative.append(f"{name}_weak")
    return positive, negative


def _component_confidence(components: Mapping[str, ComponentScore]) -> float:
    return round(
        sum(
            COMPONENT_WEIGHTS.get(name, 0.0) * max(0.0, min(100.0, component.confidence))
            for name, component in components.items()
        ),
        2,
    )


def _upsert_median_index(
    db: Session,
    *,
    trade_date: date,
    median_return: float | None,
    eligible_count: int,
    quality_status: str,
    captured_at: datetime,
) -> AllAMedianIndexDaily | None:
    if median_return is None:
        return None
    previous = db.execute(
        select(AllAMedianIndexDaily)
        .where(
            AllAMedianIndexDaily.market == "CN",
            AllAMedianIndexDaily.trade_date < trade_date,
            AllAMedianIndexDaily.calculation_version == MARKET_ENGINE_VERSION,
        )
        .order_by(AllAMedianIndexDaily.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    value = next_median_index(previous.index_value if previous else None, median_return)
    row = db.execute(
        select(AllAMedianIndexDaily).where(
            AllAMedianIndexDaily.market == "CN",
            AllAMedianIndexDaily.trade_date == trade_date,
            AllAMedianIndexDaily.calculation_version == MARKET_ENGINE_VERSION,
        )
    ).scalar_one_or_none()
    if row is None:
        row = AllAMedianIndexDaily(
            market="CN",
            trade_date=trade_date,
            median_return=median_return,
            index_value=value,
            eligible_count=eligible_count,
            quality_status=quality_status,
            calculation_version=MARKET_ENGINE_VERSION,
            available_at=captured_at,
        )
        db.add(row)
    else:
        row.median_return = median_return
        row.index_value = value
        row.eligible_count = eligible_count
        row.quality_status = quality_status
        row.available_at = captured_at
    db.flush()
    return row


def _median_index_trend_metrics(
    db: Session,
    *,
    trade_date: date,
    current_index: float | None,
) -> dict[str, Any]:
    """Attach Median Index trend facts without requiring per-name history."""

    if current_index is None:
        return {}
    rows = list(
        db.execute(
            select(AllAMedianIndexDaily)
            .where(
                AllAMedianIndexDaily.market == "CN",
                AllAMedianIndexDaily.trade_date < trade_date,
                AllAMedianIndexDaily.calculation_version == MARKET_ENGINE_VERSION,
            )
            .order_by(AllAMedianIndexDaily.trade_date.desc())
            .limit(20)
        ).scalars()
    )
    result: dict[str, Any] = {"median_index": current_index}
    if not rows:
        return result
    previous = rows[0]
    oldest = rows[-1]
    result["median_index_prev"] = previous.index_value
    if oldest.index_value:
        result["median_index_return_20"] = current_index / float(oldest.index_value) - 1.0
    return result


def _preview_median_index(
    db: Session,
    *,
    trade_date: date,
    median_return: float | None,
) -> float | None:
    """Calculate a median-index value without mutating the session."""

    if median_return is None:
        return None
    # Intraday preview is always derived from the prior finalized day and the
    # latest median return.  A same-day Daily row is deliberately ignored so a
    # later 14:30 run cannot be stuck on an old 09:35 value.
    previous = db.execute(
        select(AllAMedianIndexDaily)
        .where(
            AllAMedianIndexDaily.market == "CN",
            AllAMedianIndexDaily.trade_date < trade_date,
            AllAMedianIndexDaily.calculation_version == MARKET_ENGINE_VERSION,
        )
        .order_by(AllAMedianIndexDaily.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return next_median_index(previous.index_value if previous else None, median_return)


class MarketEngine:
    """Deterministic calculation boundary; persistence is intentionally compact."""

    def __init__(self, db: Session, *, history_provider: Any | None = None) -> None:
        self.db = db
        self.history_provider = history_provider
        from ..governance.service import lineage_fields, resolve_production_parameters
        from ..governance.registry import market_regime_settings

        self.parameter_context = resolve_production_parameters(db)
        self.parameter_lineage = lineage_fields(self.parameter_context)
        self.market_regime = market_regime_settings(self.parameter_context["snapshot"])

    def calculate(
        self,
        *,
        trade_date: date | str | None = None,
        captured_at: datetime | str | None = None,
        securities: Iterable[Any] | None = None,
        trading_calendar: Iterable[Any] | None = None,
        quotes: Any | None = None,
        history: Any | None = None,
        previous_display_score: float | None = None,
        previous_regime: str | None = None,
        last_reliable_score: float | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        server_now = datetime.now(CHINA_TZ)
        default_now = server_now.astimezone(UTC)
        now = _dt(captured_at, default=default_now) or default_now
        day = _date(trade_date) or now.astimezone(CHINA_TZ).date()
        identity_rows = _identities_for(self.db, securities)
        calendar_rows = _calendar_for(self.db, day, trading_calendar)
        universe = build_market_score_universe(
            identity_rows,
            trade_date=day,
            trading_calendar=calendar_rows,
        )

        server_owned_snapshot = quotes is None
        raw_snapshot: Mapping[str, Any] | None = quotes if isinstance(quotes, Mapping) and "quotes" in quotes else None
        quote_rows = _normalize_rows(quotes)
        if quotes is None:
            try:
                raw_snapshot = get_all_a_share_quote_snapshot(self.db, trade_date=day)
                quote_rows = _normalize_rows(raw_snapshot)
            except Exception:
                raw_snapshot = {"quotes": [], "quality_status": "MISSING", "expected_count": universe.included_count}
                quote_rows = []
        if raw_snapshot is not None:
            quote_rows = _normalize_rows(raw_snapshot)
        source_snapshot_id = (
            str(raw_snapshot.get("snapshot_id"))
            if server_owned_snapshot and raw_snapshot and raw_snapshot.get("snapshot_id")
            else None
        )
        source_provenance = _source_provenance(raw_snapshot) if server_owned_snapshot else {}
        quote_trade_dates = _quote_trade_dates(quote_rows)
        if quote_trade_dates and quote_trade_dates != {day}:
            raise ValueError("quote_trade_date_mismatch")
        quote_capture = _capture_from_quotes(quote_rows)
        if quote_capture and captured_at is None:
            now = quote_capture
        elif quote_capture and captured_at is not None:
            now = _dt(captured_at, default=quote_capture) or quote_capture

        code_set = set(universe.included_codes)
        identity_map = _identity_map(identity_rows)
        cross = calculate_cross_section_metrics(
            quote_rows,
            universe_codes=code_set,
            captured_at=quote_capture if quote_capture else None,
            snapshot_id=None,
            identity_by_code=identity_map,
        )
        history_rows = _history_rows(
            self.db,
            code_set,
            history,
            trade_date=day,
            captured_at=now,
            history_provider=self.history_provider,
        )
        ma = calculate_ma_breadth(
            history_rows,
            as_of=day,
            available_at=now,
            universe_codes=code_set,
            current_prices=quote_rows,
        )
        trend = calculate_ma_trend_metrics(history_rows, as_of=day, available_at=now, universe_codes=code_set)
        nhnl = calculate_new_high_low(
            history_rows,
            as_of=day,
            available_at=now,
            universe_codes=code_set,
            current_prices=quote_rows,
        )
        metrics = dict(cross) | ma | trend | nhnl
        metrics["active_ratio"] = (cross.get("return_eligible_count") or 0) / max(cross.get("coherent_count") or 1, 1)
        metrics["market_volatility"] = cross.get("cross_section_return_std")
        metrics["new_low_60_ratio"] = nhnl.get("new_low_60_ratio")
        metrics["above_ma20_ratio"] = ma.get("above_ma20_ratio")
        metrics["above_ma60_ratio"] = ma.get("above_ma60_ratio")
        metrics["median_return"] = cross.get("all_a_median_return")
        median_index = _preview_median_index(
            self.db,
            trade_date=day,
            median_return=metrics.get("all_a_median_return"),
        )
        if median_index is not None:
            metrics.update(_median_index_trend_metrics(self.db, trade_date=day, current_index=median_index))

        component_histories = _component_histories(
            self.db,
            trade_date=day,
            captured_at=now,
        )
        components = calculate_all_components(metrics, histories=component_histories)
        for component_name, component in components.items():
            component.historical_sample_count = max(
                (len(samples) for samples in component_histories.get(component_name, {}).values()),
                default=0,
            )
        expected = universe.included_count
        received = int(cross.get("coherent_count") or 0)
        coverage = received / expected if expected else 0.0
        quality = _included_quality(quote_rows, code_set)
        raw_quality = str(getattr((raw_snapshot or {}).get("quality_status"), "value", (raw_snapshot or {}).get("quality_status") or "")).upper()
        if raw_quality == "DEGRADED" and quality == "VALID":
            quality = "DEGRADED"
        if coverage < 0.95:
            quality = "MISSING"
        elif coverage < 0.98 and quality in {"VALID", "DEGRADED"}:
            quality = "DEGRADED"

        previous = self.db.execute(
            select(MarketScoreSnapshot)
            .where(MarketScoreSnapshot.market == "CN", MarketScoreSnapshot.captured_at < now)
            .order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if previous_display_score is None and previous is not None:
            previous_display_score = previous.display_score
        if previous_regime is None and previous is not None:
            previous_regime = previous.regime
        if last_reliable_score is None:
            reliable = self.db.execute(
                select(MarketScoreSnapshot)
                .where(
                    MarketScoreSnapshot.market == "CN",
                    # Historical/replay calculations must never borrow a
                    # reliable snapshot that was captured after this run.
                    # Without this bound, an outage at an earlier point in
                    # time could leak a future score into the frozen state.
                    MarketScoreSnapshot.captured_at < now,
                    MarketScoreSnapshot.is_frozen.is_(False),
                    MarketScoreSnapshot.display_score.is_not(None),
                )
                .order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            last_reliable_score = reliable.display_score if reliable else None

        history_codes = _history_coverage_codes(
            history_rows,
            universe_codes=code_set,
            trade_date=day,
            available_at=now,
        )
        history_coverage = len(history_codes) / max(len(code_set), 1) * 100
        confidence = calculate_confidence(
            universe_coverage=coverage * 100,
            quote_freshness=100 if quality in {"VALID", "DEGRADED"} else 0,
            historical_coverage=min(100.0, history_coverage),
            component_availability=_component_confidence(components),
            provider_quality=100 if quality == "VALID" else 60 if quality == "DEGRADED" else 0,
            conflict_quality=100 if quality != "CONFLICT" else 0,
        )
        capture_span_seconds = _capture_span_seconds(raw_snapshot, quote_rows)
        if (
            capture_span_seconds is not None
            and capture_span_seconds > SNAPSHOT_CAPTURE_SPAN_FULL_CONFIDENCE_SECONDS
        ):
            confidence = round(
                confidence * SNAPSHOT_CAPTURE_SPAN_FULL_CONFIDENCE_SECONDS / capture_span_seconds,
                2,
            )
        lower_bounds, hysteresis = self.market_regime
        score = build_market_score_snapshot(
            components,
            trade_date=day,
            previous_display_score=previous_display_score,
            previous_regime=previous_regime,
            coverage=coverage,
            quality_status=quality,
            confidence=confidence,
            last_reliable_score=last_reliable_score,
            lower_bounds=lower_bounds,
            hysteresis=hysteresis,
        )
        raw_score = _round_score(score.raw_score)
        display_score = _round_score(score.display_score)
        confidence_value = _round_score(score.confidence) or 0.0
        score_delta = None
        if display_score is not None and previous_display_score is not None:
            score_delta = _round_score(display_score - float(previous_display_score))
        china_capture = now.astimezone(CHINA_TZ)
        median_finalized = (
            persist
            and china_capture.date() == day
            and china_capture.time() >= datetime_time(15, 0)
            and not score.is_frozen
            and score.quality_status in {"VALID", "DEGRADED"}
        )
        if median_finalized:
            median_row = _upsert_median_index(
                self.db,
                trade_date=day,
                median_return=metrics.get("all_a_median_return"),
                eligible_count=int(metrics.get("return_eligible_count") or 0),
                quality_status=score.quality_status,
                captured_at=now,
            )
            if median_row is not None:
                median_index = median_row.index_value
                metrics["median_index"] = median_index

        metric_id = str(uuid4())
        score_id = str(uuid4())
        market_snapshot_id = None
        positive, negative = _drivers(components)
        if persist:
            if server_owned_snapshot and source_snapshot_id and raw_snapshot is not None:
                market_snapshot_id = persist_snapshot(self.db, raw_snapshot).snapshot_id
            existing_metric = _upsert_capture(
                self.db,
                MarketMetricSnapshot,
                market="CN",
                trade_date=day,
                captured_at=now,
            )
            if existing_metric is not None:
                metric_id = existing_metric.snapshot_id
            metric_row = MarketMetricSnapshot(
                snapshot_id=metric_id,
                market_snapshot_id=market_snapshot_id,
                market="CN",
                trade_date=day,
                captured_at=now,
                universe_rule_version=UNIVERSE_RULE_VERSION,
                calculation_version=MARKET_ENGINE_VERSION,
                score_config_version=SCORE_CONFIG_VERSION,
                universe_total=universe.universe_total,
                included_count=universe.included_count,
                excluded_count=universe.excluded_count,
                coverage=coverage,
                median_return=metrics.get("all_a_median_return"),
                advance_ratio=metrics.get("advance_ratio"),
                top5_concentration=metrics.get("top5_concentration"),
                total_amount=metrics.get("total_amount"),
                quality_status=score.quality_status,
                confidence=confidence_value,
                metrics_json=_serial(metrics),
                breadth_metrics_json=_component_json(components.get("breadth")),
                trend_metrics_json=_component_json(components.get("trend")),
                liquidity_metrics_json=_component_json(components.get("liquidity")),
                profitability_metrics_json=_component_json(components.get("profitability")),
                diffusion_metrics_json=_component_json(components.get("diffusion")),
                crowding_metrics_json=_component_json(components.get("crowding")),
                tail_risk_metrics_json=_component_json(components.get("tail_risk")),
                exclusion_counts_json=_serial(universe.exclusion_counts),
            )
            existing_score = _upsert_capture(
                self.db,
                MarketScoreSnapshot,
                market="CN",
                trade_date=day,
                captured_at=now,
            )
            if existing_score is not None:
                score_id = existing_score.snapshot_id
            score_row = MarketScoreSnapshot(
                snapshot_id=score_id,
                metric_snapshot_id=metric_id,
                market="CN",
                trade_date=day,
                captured_at=now,
                raw_score=raw_score,
                display_score=display_score,
                regime=score.regime,
                confidence=confidence_value,
                quality_status=score.quality_status,
                is_frozen=score.is_frozen,
                freeze_reason=score.freeze_reason,
                previous_display_score=score.previous_display_score,
                available_component_weight=score.available_component_weight,
                breadth_score=_round_score(components["breadth"].score) if components.get("breadth") else None,
                trend_score=_round_score(components["trend"].score) if components.get("trend") else None,
                liquidity_score=_round_score(components["liquidity"].score) if components.get("liquidity") else None,
                profitability_score=_round_score(components["profitability"].score) if components.get("profitability") else None,
                diffusion_score=_round_score(components["diffusion"].score) if components.get("diffusion") else None,
                crowding_score=_round_score(components["crowding"].score) if components.get("crowding") else None,
                tail_risk_score=_round_score(components["tail_risk"].score) if components.get("tail_risk") else None,
                positive_drivers_json=positive,
                negative_drivers_json=negative,
                metadata_json={
                    "universe": _serial(universe.to_dict()),
                    "core_metrics": _serial(metrics),
                    "score_delta": score_delta,
                    "capture_span_seconds": capture_span_seconds,
                    "median_index_preview": median_index,
                    "median_index_finalized": median_finalized,
                    "market_snapshot_id": market_snapshot_id,
                    "source_provenance": source_provenance,
                },
                calculation_version=MARKET_ENGINE_VERSION,
                score_config_version=SCORE_CONFIG_VERSION,
                universe_rule_version=UNIVERSE_RULE_VERSION,
                parameter_set_version_id=self.parameter_lineage["parameter_set_version_id"],
                parameter_set_version=self.parameter_lineage["parameter_set_version"],
                parameter_set_hash=self.parameter_lineage["parameter_set_hash"],
                governance_lineage_json=self.parameter_lineage["governance_lineage_json"],
            )
            if existing_metric is None:
                self.db.add(metric_row)
            else:
                _copy_model_values(existing_metric, metric_row)
            if existing_score is None:
                self.db.add(score_row)
            else:
                _copy_model_values(existing_score, score_row)
            self.db.commit()
        duration_ms = round((time.perf_counter() - started) * 1000)
        logger.info(
            "market_engine universe=%s coverage=%.1f%% raw=%s display=%s regime=%s confidence=%s duration=%sms quality=%s frozen=%s",
            universe.included_count,
            coverage * 100,
            raw_score,
            display_score,
            score.regime,
            confidence_value,
            duration_ms,
            score.quality_status,
            score.is_frozen,
        )
        return {
            "snapshot_id": score_id,
            "metric_snapshot_id": metric_id,
            "market_snapshot_id": market_snapshot_id,
            "source_snapshot_id": source_snapshot_id,
            "source_provenance": source_provenance,
            "trade_date": day.isoformat(),
            "captured_at": now.isoformat(),
            "raw_score": raw_score,
            "display_score": display_score,
            "regime": score.regime,
            "confidence": confidence_value,
            "quality_status": score.quality_status,
            "status": score.status,
            "is_frozen": score.is_frozen,
            "freeze_reason": score.freeze_reason,
            "score_delta": score_delta,
            "components": {name: component.to_dict() for name, component in components.items()},
            "core_metrics": _serial(metrics),
            "universe": _serial(universe.to_dict()),
            "positive_drivers": positive,
            "negative_drivers": negative,
            "median_index": median_index,
            "median_index_preview": median_index,
            "median_index_finalized": median_finalized,
            "capture_span_seconds": capture_span_seconds,
            "calculation_version": MARKET_ENGINE_VERSION,
            "score_config_version": SCORE_CONFIG_VERSION,
            "universe_rule_version": UNIVERSE_RULE_VERSION,
            "parameter_set_version_id": self.parameter_lineage["parameter_set_version_id"],
            "parameter_set_version": self.parameter_lineage["parameter_set_version"],
            "parameter_set_hash": self.parameter_lineage["parameter_set_hash"],
            "governance_lineage": self.parameter_lineage["governance_lineage_json"],
        }


def calculate_market_state(db: Session, **kwargs: Any) -> dict[str, Any]:
    return MarketEngine(db).calculate(**kwargs)


__all__ = ["MarketEngine", "calculate_market_state"]
