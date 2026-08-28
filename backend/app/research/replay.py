"""Point-in-time, offline replay adapters.

All adapters below use persisted rows only.  They intentionally do not call
the provider layer, the model client, or live candidate/portfolio services.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..candidates.models import CandidateRun, CandidateScore
from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache, MarketMetricSnapshot, MarketScoreSnapshot
from ..memory.models import DecisionMemory, DecisionOutcome
from ..portfolio_models import PortfolioRiskSnapshot, TradeLedgerEntry
from ..v2_models import PortfolioSnapshot
from .config import BACKTEST_ENGINE_VERSION, normalise_replay_mode, normalise_scope

CHINA_TZ = ZoneInfo("Asia/Shanghai")
_NETWORK_POLICY_DEPTH = 0
MARKET_DAILY_CANONICAL_CLOSE = time(15, 0)
MARKET_DAILY_CANONICAL_CLOSE_START = time(14, 45)


class ReplayDataQualityError(ValueError):
    """Raised when a replay would violate an auditable historical constraint."""


class ReplayNetworkBlockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplayCase:
    trade_date: date
    as_of: datetime
    scope: str
    replay_mode: str
    entity_id: str
    facts: dict[str, Any]
    quality_status: str = "VALID"
    reason_codes: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    coverage: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "as_of": self.as_of.isoformat(),
            "scope": self.scope,
            "replay_mode": self.replay_mode,
            "entity_id": self.entity_id,
            "facts": self.facts,
            "quality_status": self.quality_status,
            "reason_codes": list(self.reason_codes),
            "source_ids": list(self.source_ids),
            "coverage": self.coverage,
        }


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(CHINA_TZ).date()
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_of(value: datetime | date | None, *, fallback: date | None = None) -> datetime:
    if isinstance(value, datetime):
        return _naive_utc(value) or datetime.now(UTC).replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time(23, 59, 59))
    if fallback is not None:
        return datetime.combine(fallback, time(23, 59, 59))
    return datetime.now(UTC).replace(tzinfo=None)


def _local_wall_time(value: datetime | None) -> time | None:
    if value is None:
        return None
    # SQLAlchemy DateTime columns are stored as UTC-naive values in this
    # application. Treating a naive timestamp as Shanghai wall time would
    # move the canonical 15:00 boundary by eight hours.
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(CHINA_TZ).time()


def _is_intraday_decision(value: datetime | None) -> bool:
    local_time = _local_wall_time(value)
    return local_time is not None and local_time < MARKET_DAILY_CANONICAL_CLOSE


def content_hash(value: Any) -> str:
    """Canonical SHA-256 for frozen inputs and reproducibility checks."""

    def default(item: Any):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if hasattr(item, "value"):
            return item.value
        return str(item)

    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_point_in_time(
    facts: Iterable[dict[str, Any]],
    as_of: datetime,
    *,
    timestamp_keys: tuple[str, ...] = ("timestamp", "captured_at", "effective_at", "source_timestamp"),
    available_keys: tuple[str, ...] = ("available_at", "source_available_at", "published_at", "effective_at"),
) -> dict[str, Any]:
    """Validate visibility and timestamp ordering without treating ingestion as publication."""

    cutoff = _naive_utc(as_of) or as_of
    checked = 0
    for fact in facts:
        checked += 1
        timestamp = next((fact.get(key) for key in timestamp_keys if fact.get(key) is not None), None)
        available = next((fact.get(key) for key in available_keys if fact.get(key) is not None), None)
        timestamp_dt = _naive_utc(_parse_datetime(timestamp))
        available_dt = _naive_utc(_parse_datetime(available))
        if available_dt is not None and timestamp_dt is not None and available_dt < timestamp_dt:
            raise ReplayDataQualityError("TIMESTAMP_INVERSION")
        if available_dt is not None and available_dt > cutoff:
            raise ReplayDataQualityError("LOOKAHEAD_DETECTED")
    return {"status": "PASS", "checked": checked, "as_of": cutoff.isoformat()}


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def assert_external_io_allowed() -> None:
    if _NETWORK_POLICY_DEPTH:
        raise ReplayNetworkBlockedError("historical_replay_external_io_blocked")


@contextlib.contextmanager
def historical_replay_network_policy():
    global _NETWORK_POLICY_DEPTH
    _NETWORK_POLICY_DEPTH += 1
    try:
        yield
    finally:
        _NETWORK_POLICY_DEPTH = max(0, _NETWORK_POLICY_DEPTH - 1)


class HistoricalReplayNetworkPolicy:
    """Class-style context manager kept for test and integration ergonomics."""

    def __enter__(self):
        self._context = historical_replay_network_policy()
        return self._context.__enter__()

    def __exit__(self, exc_type, exc, tb):
        return self._context.__exit__(exc_type, exc, tb)


def _row_payload(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    result = {}
    for field_name in fields:
        value = getattr(row, field_name, None)
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        result[field_name] = value
    return result


def _filter_range(rows: Iterable[Any], date_field: str, start_date: date | None, end_date: date | None) -> list[Any]:
    result = []
    for row in rows:
        value = _date(getattr(row, date_field, None))
        if value is None:
            continue
        if start_date is not None and value < start_date:
            continue
        if end_date is not None and value > end_date:
            continue
        result.append(row)
    return result


def _visible(rows: Iterable[Any], *, cutoff: datetime, timestamp_field: str) -> list[Any]:
    result = []
    for row in rows:
        timestamp = _naive_utc(getattr(row, timestamp_field, None))
        if timestamp is None or timestamp <= cutoff:
            result.append(row)
    return result


def load_replay_facts(
    db: Session,
    *,
    scope: str,
    replay_mode: str,
    start_date: date,
    end_date: date,
    as_of: datetime | None = None,
    decision_feature_cutoff: datetime | date | None = None,
    outcome_evaluation_cutoff: datetime | date | None = None,
    user_id: int | None = None,
    portfolio_id: int | None = None,
) -> dict[str, list[Any]]:
    """Load one frozen source set with bulk queries and strict date visibility."""

    scope = normalise_scope(scope)
    mode = normalise_replay_mode(replay_mode)
    cutoff = _as_of(as_of, fallback=end_date)
    feature_cutoff = (
        _as_of(decision_feature_cutoff, fallback=end_date)
        if decision_feature_cutoff is not None
        else cutoff
    )
    # A historical decision cohort may be evaluated after the requested
    # decision range.  This cutoff belongs to outcome maturity, not feature
    # visibility, and is intentionally independent from ``feature_cutoff``.
    outcome_cutoff = (
        _as_of(outcome_evaluation_cutoff)
        if outcome_evaluation_cutoff is not None
        else _as_of(as_of)
        if as_of is not None
        else _as_of(None)
    )
    if mode == "DETERMINISTIC_RECOMPUTE":
        # Recompute is permitted only when the caller supplies a fully PIT data
        # preparation set.  The current schema does not have that set.
        raise ReplayDataQualityError("DETERMINISTIC_RECOMPUTE_REQUIRES_EXPLICIT_PIT_DATASET")

    result: dict[str, list[Any]] = {}
    if scope == "MARKET":
        scores = list(db.execute(select(MarketScoreSnapshot).where(
            MarketScoreSnapshot.market == "CN",
            MarketScoreSnapshot.trade_date >= start_date,
            MarketScoreSnapshot.trade_date <= end_date,
            MarketScoreSnapshot.captured_at <= feature_cutoff,
        ).order_by(MarketScoreSnapshot.trade_date.asc(), MarketScoreSnapshot.captured_at.asc(), MarketScoreSnapshot.id.asc())).scalars())
        metrics = list(db.execute(select(MarketMetricSnapshot).where(
            MarketMetricSnapshot.market == "CN",
            MarketMetricSnapshot.trade_date >= start_date,
            MarketMetricSnapshot.trade_date <= end_date,
            MarketMetricSnapshot.captured_at <= feature_cutoff,
        ).order_by(MarketMetricSnapshot.trade_date.asc(), MarketMetricSnapshot.captured_at.asc(), MarketMetricSnapshot.id.asc())).scalars())
        index = list(db.execute(select(AllAMedianIndexDaily).where(
            AllAMedianIndexDaily.market == "CN",
            AllAMedianIndexDaily.trade_date >= start_date,
            AllAMedianIndexDaily.trade_date <= end_date,
            AllAMedianIndexDaily.available_at.is_not(None),
            AllAMedianIndexDaily.available_at <= feature_cutoff,
            AllAMedianIndexDaily.quality_status.in_(("VALID", "DEGRADED")),
        ).order_by(AllAMedianIndexDaily.trade_date.asc(), AllAMedianIndexDaily.id.asc())).scalars())
        result.update({"market_scores": scores, "market_metrics": metrics, "benchmarks": index})
    elif scope == "CANDIDATE":
        query = select(CandidateRun).where(
            CandidateRun.trade_date >= start_date,
            CandidateRun.trade_date <= end_date,
            CandidateRun.captured_at <= feature_cutoff,
        )
        if user_id is not None:
            query = query.where(CandidateRun.user_id == user_id)
        if portfolio_id is not None:
            query = query.where(CandidateRun.portfolio_id == portfolio_id)
        runs = list(db.execute(query.order_by(CandidateRun.trade_date.asc(), CandidateRun.captured_at.asc(), CandidateRun.id.asc())).scalars())
        run_ids = [row.id for row in runs]
        scores = list(db.execute(select(CandidateScore).where(CandidateScore.candidate_run_id.in_(run_ids))).scalars()) if run_ids else []
        result.update({"candidate_runs": runs, "candidate_scores": scores, "benchmarks": _benchmark_rows(db, start_date, end_date, feature_cutoff)})
    elif scope == "PORTFOLIO_DECISION":
        query = select(PortfolioSnapshot).where(
            PortfolioSnapshot.snapshot_time <= feature_cutoff,
            PortfolioSnapshot.status.in_(("confirmed", "CONFIRMED")),
        )
        if user_id is not None:
            query = query.where(PortfolioSnapshot.user_id == user_id)
        if portfolio_id is not None:
            query = query.where(PortfolioSnapshot.portfolio_id == portfolio_id)
        snapshots = [row for row in db.execute(query.order_by(PortfolioSnapshot.snapshot_time.asc(), PortfolioSnapshot.id.asc())).scalars() if start_date <= _date(row.snapshot_time) <= end_date]
        risk_query = select(PortfolioRiskSnapshot).where(PortfolioRiskSnapshot.as_of <= feature_cutoff)
        if user_id is not None:
            risk_query = risk_query.where(PortfolioRiskSnapshot.user_id == user_id)
        if portfolio_id is not None:
            risk_query = risk_query.where(PortfolioRiskSnapshot.portfolio_id == portfolio_id)
        risks = [row for row in db.execute(risk_query.order_by(PortfolioRiskSnapshot.as_of.asc(), PortfolioRiskSnapshot.id.asc())).scalars() if start_date <= _date(row.as_of) <= end_date]
        result.update({"portfolio_snapshots": snapshots, "portfolio_risks": risks})
    elif scope == "MEMORY_DECISION":
        query = select(DecisionMemory).where(
            DecisionMemory.trade_date >= start_date,
            DecisionMemory.trade_date <= end_date,
            DecisionMemory.available_at <= feature_cutoff,
        )
        if user_id is not None:
            query = query.where(DecisionMemory.user_id == user_id)
        if portfolio_id is not None:
            query = query.where(DecisionMemory.portfolio_id == portfolio_id)
        memories = list(db.execute(query.order_by(DecisionMemory.trade_date.asc(), DecisionMemory.decision_at.asc(), DecisionMemory.id.asc())).scalars())
        memory_ids = [row.id for row in memories]
        outcomes = list(db.execute(select(DecisionOutcome).where(
            DecisionOutcome.decision_memory_id.in_(memory_ids),
            DecisionOutcome.available_at.is_not(None),
            DecisionOutcome.available_at <= outcome_cutoff,
        ).order_by(DecisionOutcome.target_trade_date.asc(), DecisionOutcome.id.asc())).scalars()) if memory_ids else []
        result.update({"decision_memories": memories, "decision_outcomes": outcomes})
    elif scope == "BAR_FACTOR":
        bars = list(db.execute(select(DailyBarCache).where(
            DailyBarCache.market == "CN",
            DailyBarCache.trade_date >= start_date,
            DailyBarCache.trade_date <= end_date,
            DailyBarCache.adjustment == "QFQ",
            (DailyBarCache.available_at.is_(None) | (DailyBarCache.available_at <= feature_cutoff)),
        ).order_by(DailyBarCache.trade_date.asc(), DailyBarCache.code.asc(), DailyBarCache.id.asc())).scalars())
        result["daily_bars"] = bars
    return result


def _benchmark_rows(db: Session, start_date: date, end_date: date, cutoff: datetime) -> list[AllAMedianIndexDaily]:
    return list(db.execute(select(AllAMedianIndexDaily).where(
        AllAMedianIndexDaily.market == "CN",
        AllAMedianIndexDaily.trade_date >= start_date,
        AllAMedianIndexDaily.trade_date <= end_date,
        AllAMedianIndexDaily.available_at.is_not(None),
        AllAMedianIndexDaily.available_at <= cutoff,
        AllAMedianIndexDaily.quality_status.in_(("VALID", "DEGRADED")),
    ).order_by(AllAMedianIndexDaily.trade_date.asc(), AllAMedianIndexDaily.id.asc())).scalars())


def replay_market_cases(facts: dict[str, list[Any]], *, replay_mode: str) -> list[ReplayCase]:
    mode = normalise_replay_mode(replay_mode)
    benchmarks = {row.trade_date: row for row in facts.get("benchmarks", [])}
    result = []
    for row in canonical_market_score_rows(facts.get("market_scores", [])):
        score = row.display_score if row.display_score is not None else row.raw_score
        benchmark = benchmarks.get(row.trade_date)
        result.append(ReplayCase(
            trade_date=row.trade_date,
            as_of=_naive_utc(row.captured_at) or _as_of(row.trade_date),
            scope="MARKET",
            replay_mode=mode,
            entity_id=str(row.snapshot_id),
            facts={
                "market_score": score,
                "raw_score": row.raw_score,
                "display_score": row.display_score,
                "market_regime": row.regime,
                "confidence": row.confidence,
                "quality_status": row.quality_status,
                "benchmark_index": benchmark.index_value if benchmark else None,
                "benchmark_id": benchmark.id if benchmark else None,
                "score_config_version": row.score_config_version,
                "calculation_version": row.calculation_version,
                "canonical_observation": "CLOSE_BEFORE_15_00",
            },
            quality_status=str(row.quality_status or "MISSING"),
            reason_codes=() if benchmark else ("BENCHMARK_UNAVAILABLE",),
            source_ids=tuple(item for item in (str(row.snapshot_id), str(benchmark.id) if benchmark else None) if item),
        ))
    return result


def replay_candidate_cases(facts: dict[str, list[Any]], *, replay_mode: str) -> list[ReplayCase]:
    mode = normalise_replay_mode(replay_mode)
    runs = {row.id: row for row in facts.get("candidate_runs", [])}
    benchmarks = {row.trade_date: row for row in facts.get("benchmarks", [])}
    result = []
    for row in facts.get("candidate_scores", []):
        run = runs.get(row.candidate_run_id)
        if run is None:
            continue
        benchmark = benchmarks.get(run.trade_date)
        lineage = row.lineage_json if isinstance(row.lineage_json, dict) else {}
        run_metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
        market = run_metadata.get("market") if isinstance(run_metadata.get("market"), dict) else {}
        # ``entry_json`` is model-derived analysis output. It must never be a
        # source of the research reference price.
        reference_price = _server_owned_price(lineage, run.metadata_json)
        result.append(ReplayCase(
            trade_date=run.trade_date,
            as_of=_naive_utc(run.as_of) or _as_of(run.trade_date),
            scope="CANDIDATE",
            replay_mode=mode,
            entity_id=f"{run.id}:{row.code}",
            facts={
                "code": row.code,
                "name": row.name,
                "security_type": row.security_type,
                "etf_category": row.etf_category,
                "stage": row.stage,
                "score": row.score,
                "opportunity_score": row.opportunity_score,
                "entry_score": row.entry_score,
                "portfolio_fit_score": row.portfolio_fit_score,
                "action_score": row.action_score,
                "decision_edge": row.decision_edge,
                "risk_reward_ratio": row.risk_reward_ratio,
                "coverage": row.data_coverage,
                "confidence": row.confidence,
                "reference_price": reference_price,
                "reference_price_basis": _server_owned_basis(lineage, run.metadata_json),
                "quote_snapshot_id": run.quote_snapshot_id,
                "benchmark_index": benchmark.index_value if benchmark else None,
                "market_regime": market.get("regime"),
                "candidate_run_id": run.id,
                "candidate_score_id": row.id,
                "censored_sample": True,
                "market_available": bool(market.get("available", False)),
                "market_quality": market.get("quality_status") or "MISSING",
                "market_frozen": bool(market.get("is_frozen")),
                "funding_mode": getattr(row, "funding_mode", None),
                "quote_quality": lineage.get("quote_quality") or lineage.get("quality_status") or "MISSING",
                "quote_is_proxy": bool(
                    lineage.get("quote_is_proxy")
                    or lineage.get("source") == "daily_bar_cache_close_proxy"
                ),
                "limit_up": "PRICE_LIMIT_UP" in (getattr(row, "risk_flags_json", None) or []),
                "limit_down": "PRICE_LIMIT_DOWN" in (getattr(row, "risk_flags_json", None) or []),
                "edge_vs_no_action": getattr(row, "edge_vs_no_action", None),
                "edge_vs_current_holdings": getattr(row, "edge_vs_current_holdings", None),
                "components": getattr(row, "components_json", None) or {},
                "portfolio_fit_components": getattr(row, "portfolio_fit_json", None) or {},
                "hard_cap_violation": bool((getattr(row, "portfolio_fit_json", None) or {}).get("hard_cap_violation")) if isinstance(getattr(row, "portfolio_fit_json", None), dict) else False,
                "held_baseline": (getattr(row, "comparison_json", None) or {}).get("held_baseline") if isinstance(getattr(row, "comparison_json", None), dict) else None,
                "intraday": _is_intraday_decision(getattr(run, "as_of", None)),
            },
            quality_status=str(row.quality_status or run.quality_status or "MISSING"),
            reason_codes=("CENSORED_PRODUCTION_SAMPLE",) + (() if benchmark else ("BENCHMARK_UNAVAILABLE",)),
            source_ids=(str(run.id), str(row.id), str(run.quote_snapshot_id)) if run.quote_snapshot_id else (str(run.id), str(row.id)),
            coverage=row.data_coverage,
        ))
    return result


def canonical_market_score_rows(rows: Iterable[Any]) -> list[Any]:
    """Choose one reliable close checkpoint per trade date for daily studies."""

    grouped: dict[date, list[Any]] = {}
    for row in rows:
        trade_date = _date(getattr(row, "trade_date", None))
        if trade_date is None:
            continue
        grouped.setdefault(trade_date, []).append(row)
    selected: list[Any] = []
    for trade_date, candidates in grouped.items():
        reliable = [
            row for row in candidates
            if str(getattr(row, "quality_status", "VALID") or "VALID").upper() in {"VALID", "DEGRADED"}
        ] or candidates
        close_window = [
            row for row in reliable
            if MARKET_DAILY_CANONICAL_CLOSE_START <= (_local_wall_time(getattr(row, "captured_at", None)) or time.min) <= MARKET_DAILY_CANONICAL_CLOSE
        ]
        # Daily regime calibration is a close-checkpoint study. Only snapshots
        # inside the 14:45-15:00 window are canonical; morning or after-close
        # checkpoints are never promoted into the daily cohort.
        if not close_window:
            continue
        pool = close_window
        selected.append(max(
            pool,
            key=lambda row: (
                _naive_utc(getattr(row, "captured_at", None)) or datetime.min,
                int(getattr(row, "id", 0) or 0),
            ),
        ))
    return sorted(selected, key=lambda row: (_date(getattr(row, "trade_date", None)) or date.min, int(getattr(row, "id", 0) or 0)))


def replay_memory_cases(facts: dict[str, list[Any]], *, replay_mode: str) -> list[ReplayCase]:
    mode = normalise_replay_mode(replay_mode)
    memories = {row.id: row for row in facts.get("decision_memories", [])}
    result = []
    for outcome in facts.get("decision_outcomes", []):
        memory = memories.get(outcome.decision_memory_id)
        if memory is None:
            continue
        features = memory.decision_features_json if isinstance(memory.decision_features_json, dict) else {}
        result.append(ReplayCase(
            trade_date=memory.trade_date,
            as_of=_naive_utc(memory.available_at) or _as_of(memory.trade_date),
            scope="MEMORY_DECISION",
            replay_mode=mode,
            entity_id=f"{memory.id}:{outcome.id}",
            facts={
                "decision_memory_id": memory.id,
                "analysis_run_id": memory.analysis_run_id,
                "target_type": outcome.target_type,
                "target_key": outcome.target_key,
                "recommended_action": outcome.recommended_action,
                "horizon": outcome.horizon_trading_days,
                "market_regime": features.get("market_regime"),
                "market_score": features.get("market_score"),
                "quality_status": outcome.quality_status,
                "raw_return": outcome.raw_return,
                "benchmark_return": outcome.benchmark_return,
                "excess_return": outcome.excess_return,
                "mfe": outcome.mfe,
                "mae": outcome.mae,
                "directional_return": outcome.directional_return,
                "execution_alignment": outcome.execution_alignment,
                "coverage": features.get("coverage"),
                "outcome_id": outcome.id,
            },
            quality_status=str(outcome.quality_status or "MISSING"),
            reason_codes=(),
            source_ids=(str(memory.id), str(outcome.id)),
            coverage=features.get("coverage"),
        ))
    return result


def replay_bar_cases(facts: dict[str, list[Any]], *, replay_mode: str = "BAR_ONLY_DIAGNOSTIC") -> list[ReplayCase]:
    mode = normalise_replay_mode(replay_mode)
    return [ReplayCase(
        trade_date=row.trade_date,
        as_of=_naive_utc(row.available_at) or _as_of(row.trade_date),
        scope="BAR_FACTOR",
        replay_mode=mode,
        entity_id=f"{row.code}:{row.trade_date.isoformat()}",
        facts={
            "code": row.code,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "prev_close": row.prev_close,
            "volume": row.volume,
            "amount": row.amount,
            "adjustment": row.adjustment,
            "provider": row.provider,
            "quality_status": row.quality_status,
            "metadata": row.metadata_json or {},
        },
        quality_status=str(row.quality_status or "MISSING"),
        reason_codes=("BAR_ONLY_DIAGNOSTIC",),
        source_ids=(str(row.id),),
    ) for row in facts.get("daily_bars", [])]


def _server_owned_price(*values: Any) -> float | None:
    """Extract only explicitly server-owned quote fields, never model prose."""

    for value in values:
        if not isinstance(value, dict):
            continue
        ownership = str(
            value.get("owner")
            or value.get("ownership")
            or value.get("provenance")
            or value.get("source")
            or value.get("basis")
            or ""
        ).upper()
        # Candidate persistence also records a server-owned quote as lineage
        # fields (quote_snapshot_id + quote_price), without an ``owner`` key.
        lineage_quote = bool(
            value.get("quote_snapshot_id")
            and any(value.get(key) is not None for key in ("quote_price", "quote_price_basis", "quote_provider", "quote_provenance"))
        )
        if not lineage_quote and (not ownership or not any(token in ownership for token in ("SERVER", "TRUSTED", "QUOTE", "SNAPSHOT"))):
            continue
        for key in ("server_owned_price", "quote_price", "price", "reference_price"):
            candidate = value.get(key)
            try:
                number = float(candidate)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
        nested = value.get("quote") or value.get("quote_snapshot") or value.get("server_quote")
        if isinstance(nested, dict):
            nested_price = _server_owned_price(nested)
            if nested_price is not None:
                return nested_price
    return None


def _server_owned_basis(*values: Any) -> str | None:
    """Return a price basis only when it belongs to the trusted quote."""

    for value in values:
        if not isinstance(value, dict):
            continue
        ownership = str(
            value.get("owner")
            or value.get("ownership")
            or value.get("provenance")
            or value.get("source")
            or value.get("basis")
            or ""
        ).upper()
        lineage_quote = bool(
            value.get("quote_snapshot_id")
            and any(value.get(key) is not None for key in ("quote_price", "quote_price_basis", "quote_provider", "quote_provenance"))
        )
        if lineage_quote or (ownership and any(token in ownership for token in ("SERVER", "TRUSTED", "QUOTE", "SNAPSHOT"))):
            basis = value.get("price_basis") or value.get("reference_price_basis") or value.get("quote_price_basis")
            if basis:
                return str(basis)
            nested = value.get("quote") or value.get("quote_snapshot") or value.get("server_quote")
            if isinstance(nested, dict):
                nested_basis = _server_owned_basis(nested)
                if nested_basis:
                    return nested_basis
    return None


__all__ = [
    "ReplayCase",
    "ReplayDataQualityError",
    "ReplayNetworkBlockedError",
    "HistoricalReplayNetworkPolicy",
    "historical_replay_network_policy",
    "assert_external_io_allowed",
    "content_hash",
    "validate_point_in_time",
    "load_replay_facts",
    "canonical_market_score_rows",
    "replay_market_cases",
    "replay_candidate_cases",
    "replay_memory_cases",
    "replay_bar_cases",
]
