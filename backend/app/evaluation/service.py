"""Point-in-time evaluation, replay, and forward observation services.

The service is intentionally read-oriented.  It freezes references to existing
facts and appends evaluation evidence; it never rewrites Decision Memory,
Candidate, Trigger, Portfolio Gate, or Analysis rows.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..candidates.models import CandidateRun, CandidateScore
from ..decision_contract import CONTRACT_VERSION, decision_contract_payload
from ..market_engine_models import DailyBarCache, MarketMetricSnapshot, MarketScoreSnapshot
from ..memory.models import DecisionMemory
from ..portfolio_models import PortfolioRiskSnapshot
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from ..trigger_models import TriggerEvent
from ..v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot
from .models import (
    EVALUATION_SCHEMA_VERSION,
    CandidateEvaluation,
    DecisionEpisode,
    DecisionEvaluationOutcome,
    EvaluationRun,
    EvaluationSnapshot,
    PaperObservation,
    PaperObservationRun,
    TriggerEvaluation,
)

EVALUATION_HORIZONS = (1, 3, 5, 10, 20)
OUTCOME_CALCULATION_VERSION = "evaluation-outcome-v1"
REPLAY_MODES = frozenset({"FACT_REPLAY", "DETERMINISTIC_LOGIC_REPLAY", "MODEL_RECOMPUTE"})
PAPER_OBSERVATION_MODE = "REAL_TIME_PAPER_OBSERVATION"
LOW_SAMPLE_THRESHOLD = 5
EARLY_SAMPLE_THRESHOLD = 30
_REPLAY_POLICY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "historical_replay_network_policy", default="ALLOW_EXTERNAL_IO"
)


class EvaluationDataQualityError(ValueError):
    """Raised when an evaluation input violates point-in-time discipline."""


class ReplayNetworkBlockedError(RuntimeError):
    """Raised when a historical replay attempts external I/O."""


class HistoricalReplayNetworkPolicy:
    """Context manager used by replay callers to deny provider/model/broker I/O."""

    def __init__(self, policy: str = "DENY_EXTERNAL_IO") -> None:
        self.policy = policy
        self._token: contextvars.Token[str] | None = None

    def __enter__(self) -> "HistoricalReplayNetworkPolicy":
        self._token = _REPLAY_POLICY.set(self.policy)
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self._token is not None:
            _REPLAY_POLICY.reset(self._token)

    @staticmethod
    def assert_external_io_allowed() -> None:
        if _REPLAY_POLICY.get() == "DENY_EXTERNAL_IO":
            raise ReplayNetworkBlockedError("historical_replay_external_io_denied")


def assert_external_io_allowed() -> None:
    """Public guard for provider/model/broker adapters used during replay tests."""

    HistoricalReplayNetworkPolicy.assert_external_io_allowed()


def _utc_naive(value: datetime | None, *, assume_utc: bool = True) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value if assume_utc else None
    return value.astimezone(UTC).replace(tzinfo=None)


def _aware(value: datetime | None) -> datetime | None:
    normalized = _utc_naive(value)
    return normalized.replace(tzinfo=UTC) if normalized is not None else None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (_aware(value) or value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _json_default(value: Any) -> str:
    text = _iso(value)
    return text if text is not None else str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    """Convert ORM/JSON values to the same stable representation used for hashing."""
    if isinstance(value, (datetime, date)):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _row_payload(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    columns = getattr(row, "__table__", None)
    if columns is None:
        return dict(row) if isinstance(row, Mapping) else {"value": str(row)}
    result: dict[str, Any] = {}
    for column in columns.columns:
        result[column.name] = _json_safe(getattr(row, column.name, None))
    return result


def _row_version(row: Any) -> str | None:
    for key in ("calculation_version", "rule_version", "review_version", "mode"):
        value = getattr(row, key, None)
        if value:
            return str(value)
    return None


def _source_entry(
    *,
    input_type: str,
    row: Any | None,
    source_id: str | int | None,
    snapshot_id: str | int | None = None,
    timestamp: datetime | None = None,
    available_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _row_payload(row) if row is not None else {"missing": True, "input_type": input_type}
    return {
        "input_type": input_type,
        "source_id": str(source_id) if source_id is not None else None,
        "snapshot_id": str(snapshot_id) if snapshot_id is not None else None,
        "version": _row_version(row),
        "timestamp": _iso(timestamp),
        "available_at": _iso(available_at),
        "content_hash": content_hash(payload),
        "payload": payload,
        "metadata": metadata or {},
    }


def validate_point_in_time(entries: Iterable[Mapping[str, Any]], decision_time: datetime) -> dict[str, Any]:
    """Validate ``available_at <= decision_time`` for every frozen input."""

    cutoff = _utc_naive(decision_time)
    if cutoff is None:
        raise EvaluationDataQualityError("decision_time_required")
    violations: list[dict[str, Any]] = []
    inversions: list[dict[str, Any]] = []
    missing: list[str] = []
    for entry in entries:
        kind = str(entry.get("input_type") or "unknown")
        available = _utc_naive(_parse_datetime(entry.get("available_at")))
        timestamp = _utc_naive(_parse_datetime(entry.get("timestamp")))
        if available is None:
            missing.append(kind)
        elif available > cutoff:
            violations.append({"input_type": kind, "available_at": _iso(available), "decision_time": _iso(cutoff)})
        if timestamp is not None and available is not None and available < timestamp:
            inversions.append({"input_type": kind, "timestamp": _iso(timestamp), "available_at": _iso(available)})
    if violations:
        raise EvaluationDataQualityError(
            "LOOKAHEAD_DETECTED:" + canonical_json(violations)
        )
    if inversions:
        raise EvaluationDataQualityError(
            "TIMESTAMP_INVERSION:" + canonical_json(inversions)
        )
    return {
        "status": "PASS" if not inversions else "BLOCKED",
        "violations": violations,
        "timestamp_inversions": inversions,
        "missing_available_at": sorted(set(missing)),
    }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _analysis_context(memory: DecisionMemory, db: Session) -> dict[str, Any]:
    run = db.get(AnalysisRun, memory.analysis_run_id)
    job = db.get(AnalysisJob, memory.analysis_job_id)
    candidate = db.get(CandidateRun, memory.candidate_run_id) if memory.candidate_run_id else None
    trigger = db.get(TriggerEvent, memory.trigger_event_id) if memory.trigger_event_id else None
    portfolio_snapshot = db.get(PortfolioSnapshot, memory.portfolio_snapshot_id) if memory.portfolio_snapshot_id else None
    risk = db.get(PortfolioRiskSnapshot, memory.portfolio_risk_snapshot_id) if memory.portfolio_risk_snapshot_id else None
    market_score = None
    if memory.market_score_snapshot_id:
        market_score = db.execute(select(MarketScoreSnapshot).where(
            MarketScoreSnapshot.snapshot_id == memory.market_score_snapshot_id,
        )).scalar_one_or_none()
    market_metric = None
    if memory.market_metric_snapshot_id:
        market_metric = db.execute(select(MarketMetricSnapshot).where(
            MarketMetricSnapshot.snapshot_id == memory.market_metric_snapshot_id,
        )).scalar_one_or_none()
    return {
        "run": run,
        "job": job,
        "candidate": candidate,
        "trigger": trigger,
        "portfolio_snapshot": portfolio_snapshot,
        "risk": risk,
        "market_score": market_score,
        "market_metric": market_metric,
    }


def _target_rows(memory: DecisionMemory) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target_type, values in (
        ("HELD_POSITION", memory.holding_decisions_json),
        ("NEW_POSITION", memory.candidate_decisions_json),
    ):
        for item in values or []:
            if isinstance(item, dict):
                rows.append({"target_type": target_type, **item})
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("code") or row.get("target_key") or "").strip()
        if key:
            unique.setdefault(key, row)
    return list(unique.values()) or [{"target_type": "PORTFOLIO", "code": "__PORTFOLIO__"}]


def _gate_result(memory: DecisionMemory) -> str | None:
    context = memory.portfolio_context_json if isinstance(memory.portfolio_context_json, dict) else {}
    for key in ("portfolio_gate_result", "gate_status", "quality_status", "status"):
        value = context.get(key)
        if value:
            return str(value).upper()
    return None


def _source_entries(db: Session, memory: DecisionMemory, decision_time: datetime) -> list[dict[str, Any]]:
    context = _analysis_context(memory, db)
    run = context["run"]
    job = context["job"]
    # The output/memory records are frozen at decision_time for evaluation
    # purposes; their persistence time is not an input to the decision.
    entries = [
        _source_entry(
            input_type="market",
            row=context["market_score"] or context["market_metric"],
            source_id=memory.market_snapshot_id or memory.market_score_snapshot_id or memory.market_metric_snapshot_id,
            snapshot_id=memory.market_snapshot_id or memory.market_score_snapshot_id or memory.market_metric_snapshot_id,
            timestamp=getattr(context["market_score"] or context["market_metric"], "captured_at", None),
            available_at=getattr(context["market_score"] or context["market_metric"], "captured_at", None),
        ),
        _source_entry(
            input_type="portfolio",
            row=context["portfolio_snapshot"],
            source_id=getattr(context["portfolio_snapshot"], "id", None),
            snapshot_id=getattr(context["portfolio_snapshot"], "id", None),
            timestamp=getattr(context["portfolio_snapshot"], "snapshot_time", None),
            available_at=getattr(context["portfolio_snapshot"], "snapshot_time", None),
        ),
        _source_entry(
            input_type="portfolio_risk",
            row=context["risk"],
            source_id=getattr(context["risk"], "id", None),
            timestamp=getattr(context["risk"], "calculated_at", None),
            available_at=getattr(context["risk"], "calculated_at", None),
        ),
        _source_entry(
            input_type="candidate",
            row=context["candidate"],
            source_id=getattr(context["candidate"], "id", None),
            snapshot_id=getattr(context["candidate"], "market_snapshot_id", None),
            timestamp=getattr(context["candidate"], "captured_at", None),
            available_at=getattr(context["candidate"], "captured_at", None),
        ),
        _source_entry(
            input_type="trigger",
            row=context["trigger"],
            source_id=getattr(context["trigger"], "id", None),
            snapshot_id=getattr(context["trigger"], "market_snapshot_id", None),
            timestamp=getattr(context["trigger"], "detected_at", None),
            available_at=getattr(context["trigger"], "detected_at", None),
        ),
        _source_entry(
            input_type="analysis",
            row=run,
            source_id=getattr(run, "id", None) or memory.analysis_run_id,
            snapshot_id=getattr(run, "id", None) or memory.analysis_run_id,
            timestamp=getattr(run, "created_at", None) or getattr(job, "started_at", None),
            # Missing persisted availability must remain missing so the episode
            # is marked insufficient rather than silently filled with the
            # decision timestamp.
            available_at=getattr(run, "created_at", None) or getattr(job, "started_at", None),
        ),
        _source_entry(
            input_type="decision_output",
            row=memory,
            source_id=memory.id,
            snapshot_id=memory.id,
            timestamp=decision_time,
            available_at=decision_time,
            metadata={"decision_contract_version": CONTRACT_VERSION},
        ),
    ]
    return entries


def _episode_identifier(portfolio_id: int, decision_run_id: int, symbol: str) -> str:
    return "ep_" + hashlib.sha256(f"{portfolio_id}:{decision_run_id}:{symbol}".encode()).hexdigest()[:28]


def capture_decision_episodes(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    analysis_run_id: int,
    source_mode: str = "FACT_REPLAY",
    code_version: str | None = None,
    now: datetime | None = None,
) -> list[DecisionEpisode]:
    """Freeze one or more target episodes from an existing immutable DecisionMemory."""

    memory = db.execute(select(DecisionMemory).where(
        DecisionMemory.id.is_not(None),
        DecisionMemory.analysis_run_id == analysis_run_id,
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
    )).scalar_one_or_none()
    if memory is None:
        return []
    portfolio = db.execute(select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)).scalar_one_or_none()
    if portfolio is None:
        return []
    decision_time = _utc_naive(memory.decision_at)
    if decision_time is None:
        raise EvaluationDataQualityError("decision_time_missing")
    entries = _source_entries(db, memory, decision_time)
    pit = validate_point_in_time(entries, decision_time)
    evidence_status = "READY" if not pit["missing_available_at"] else "INSUFFICIENT_HISTORICAL_EVIDENCE"
    manifest_hash = content_hash(entries)
    frozen_at = _utc_naive(now) or datetime.now(UTC).replace(tzinfo=None)
    context = _analysis_context(memory, db)
    candidates_by_code = {
        str(item.get("code")): item for item in memory.candidate_decisions_json or [] if isinstance(item, dict) and item.get("code")
    }
    episodes: list[DecisionEpisode] = []
    for target in _target_rows(memory):
        symbol = str(target.get("code") or "__PORTFOLIO__")
        episode_id = _episode_identifier(portfolio_id, analysis_run_id, symbol)
        existing = db.execute(select(DecisionEpisode).where(DecisionEpisode.episode_id == episode_id)).scalar_one_or_none()
        if existing is not None:
            episodes.append(existing)
            continue
        episode = DecisionEpisode(
            episode_id=episode_id,
            user_id=user_id,
            portfolio_id=portfolio_id,
            symbol=symbol,
            decision_time=decision_time,
            trading_date=decision_time.replace(tzinfo=UTC).astimezone(CHINA_TZ).date(),
            timezone="Asia/Shanghai",
            decision_run_id=analysis_run_id,
            decision_memory_id=memory.id,
            market_snapshot_id=memory.market_snapshot_id or memory.market_score_snapshot_id,
            portfolio_snapshot_id=memory.portfolio_snapshot_id,
            candidate_snapshot_id=memory.candidate_run_id,
            trigger_snapshot_id=memory.trigger_event_id,
            analysis_snapshot_id=memory.analysis_run_id,
            decision_snapshot_id=memory.id,
            candidate_stage=str((candidates_by_code.get(symbol) or {}).get("stage") or "") or None,
            decision_type=str(memory.decision_type or "NO_ACTION").upper(),
            portfolio_gate_result=_gate_result(memory),
            no_action_reason=(memory.no_action_context_json or {}).get("reason") if isinstance(memory.no_action_context_json, dict) else None,
            source_data_cutoff=decision_time,
            source_mode=source_mode,
            evidence_status=evidence_status,
            status="FROZEN",
            manifest_hash=manifest_hash,
            decision_contract_version=CONTRACT_VERSION,
            evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
            code_version=code_version or os.getenv("GIT_COMMIT"),
            frozen_at=frozen_at,
        )
        db.add(episode)
        db.flush()
        for entry in entries:
            db.add(EvaluationSnapshot(
                episode_id=episode.id,
                input_type=entry["input_type"],
                source_id=entry["source_id"],
                snapshot_id=entry["snapshot_id"],
                version=entry["version"],
                timestamp=_utc_naive(_parse_datetime(entry["timestamp"])),
                available_at=_utc_naive(_parse_datetime(entry["available_at"])),
                content_hash=entry["content_hash"],
                payload_json=entry["payload"],
                metadata_json=entry["metadata"],
            ))
        episodes.append(episode)
    db.commit()
    return episodes


def capture_decision_episode(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    analysis_run_id: int,
    symbol: str | None = None,
    source_mode: str = "FACT_REPLAY",
    code_version: str | None = None,
    now: datetime | None = None,
) -> DecisionEpisode | None:
    episodes = capture_decision_episodes(
        db,
        user_id=user_id,
        portfolio_id=portfolio_id,
        analysis_run_id=analysis_run_id,
        source_mode=source_mode,
        code_version=code_version,
        now=now,
    )
    if symbol is None:
        return episodes[0] if episodes else None
    return next((episode for episode in episodes if episode.symbol == symbol), None)


def _episode_manifest(db: Session, episode_id: int) -> list[EvaluationSnapshot]:
    return db.execute(select(EvaluationSnapshot).where(EvaluationSnapshot.episode_id == episode_id).order_by(EvaluationSnapshot.id.asc())).scalars().all()


def _episode_outcomes(db: Session, episode_id: int) -> list[DecisionEvaluationOutcome]:
    return db.execute(select(DecisionEvaluationOutcome).where(DecisionEvaluationOutcome.episode_id == episode_id).order_by(DecisionEvaluationOutcome.horizon_trading_days.asc())).scalars().all()


def serialize_episode(db: Session, episode: DecisionEpisode, *, include_payload: bool = False) -> dict[str, Any]:
    manifest = _episode_manifest(db, episode.id)
    outcomes = _episode_outcomes(db, episode.id)
    return {
        "id": episode.id,
        "episode_id": episode.episode_id,
        "user_id": episode.user_id,
        "portfolio_id": episode.portfolio_id,
        "symbol": episode.symbol,
        "decision_time": _iso(episode.decision_time),
        "trading_date": episode.trading_date.isoformat(),
        "timezone": episode.timezone,
        "decision_run_id": episode.decision_run_id,
        "market_snapshot_id": episode.market_snapshot_id,
        "portfolio_snapshot_id": episode.portfolio_snapshot_id,
        "candidate_snapshot_id": episode.candidate_snapshot_id,
        "trigger_snapshot_id": episode.trigger_snapshot_id,
        "analysis_snapshot_id": episode.analysis_snapshot_id,
        "decision_snapshot_id": episode.decision_snapshot_id,
        "candidate_stage": episode.candidate_stage,
        "decision_type": episode.decision_type,
        "portfolio_gate_result": episode.portfolio_gate_result,
        "no_action_reason": episode.no_action_reason,
        "source_data_cutoff": _iso(episode.source_data_cutoff),
        "source_mode": episode.source_mode,
        "evidence_status": episode.evidence_status,
        "status": episode.status,
        "manifest_hash": episode.manifest_hash,
        "decision_contract_version": episode.decision_contract_version,
        "evaluation_schema_version": episode.evaluation_schema_version,
        "code_version": episode.code_version,
        "frozen_at": _iso(episode.frozen_at),
        "created_at": _iso(episode.created_at),
        "manifest": [
            {
                "id": row.id,
                "input_type": row.input_type,
                "source_id": row.source_id,
                "snapshot_id": row.snapshot_id,
                "version": row.version,
                "timestamp": _iso(row.timestamp),
                "available_at": _iso(row.available_at),
                "content_hash": row.content_hash,
                **({"payload": row.payload_json, "metadata": row.metadata_json} if include_payload else {}),
            }
            for row in manifest
        ],
        "outcomes": [serialize_outcome(row) for row in outcomes],
    }


def serialize_outcome(row: DecisionEvaluationOutcome) -> dict[str, Any]:
    return {
        "id": row.id,
        "episode_id": row.episode_id,
        "target_type": row.target_type,
        "target_key": row.target_key,
        "horizon_trading_days": row.horizon_trading_days,
        "reference_trade_date": _iso(row.reference_trade_date),
        "target_trade_date": _iso(row.target_trade_date),
        "start_price": row.start_price,
        "end_price": row.end_price,
        "high": row.high,
        "low": row.low,
        "raw_return": row.raw_return,
        "directional_return": row.directional_return,
        "benchmark_return": row.benchmark_return,
        "sector_return": row.sector_return,
        "mfe": row.mfe,
        "mae": row.mae,
        "max_drawdown": row.max_drawdown,
        "price_adjustment_method": row.price_adjustment_method,
        "status": row.status,
        "quality_status": row.quality_status,
        "observation_complete": row.observation_complete,
        "available_at": _iso(row.available_at),
        "source_refs": row.source_refs_json,
        "calculation_version": row.calculation_version,
        "recalculation_count": row.recalculation_count,
        "computed_at": _iso(row.computed_at),
    }


def _trading_date_after(calendar: TradingCalendarService, start: date, horizon: int) -> date | None:
    current = start
    for _ in range(horizon):
        current = calendar.next_trading_day(current)
        if current is None:
            return None
    return current


def _bars_for(db: Session, *, symbol: str, start: date, end: date, cutoff: datetime) -> list[DailyBarCache]:
    return db.execute(select(DailyBarCache).where(
        DailyBarCache.code == symbol,
        DailyBarCache.trade_date >= start,
        DailyBarCache.trade_date <= end,
        DailyBarCache.available_at.is_not(None),
        DailyBarCache.available_at <= cutoff,
        DailyBarCache.quality_status.in_(("VALID", "OK", "FRESH")),
    ).order_by(DailyBarCache.trade_date.asc(), DailyBarCache.adjustment.asc(), DailyBarCache.id.asc())).scalars().all()


def _choose_bar(rows: list[DailyBarCache], trade_date: date) -> DailyBarCache | None:
    candidates = [row for row in rows if row.trade_date == trade_date and row.close is not None and float(row.close) > 0]
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (0 if str(row.adjustment).upper() == "QFQ" else 1, row.id))[0]


def _drawdown(bars: list[DailyBarCache], start_price: float) -> float | None:
    peak = start_price
    worst = 0.0
    for row in bars:
        high = float(row.high or row.close or 0)
        low = float(row.low or row.close or 0)
        peak = max(peak, high)
        if peak > 0 and low > 0:
            worst = min(worst, low / peak - 1.0)
    return worst


def _action_sign(episode: DecisionEpisode) -> int:
    if episode.decision_type.upper() in {"PORTFOLIO_ACTION", "MIXED_ACTION", "NEW_POSITION_ACTION"}:
        return 1
    text = f"{episode.candidate_stage or ''} {episode.no_action_reason or ''}".lower()
    if any(token in text for token in ("reduce", "sell", "exit", "减仓", "卖出")):
        return -1
    return 1


def _outcome_values(rows: list[DailyBarCache], start: DailyBarCache, end: DailyBarCache) -> dict[str, float | None]:
    start_price = float(start.close or 0)
    end_price = float(end.close or 0)
    if start_price <= 0 or end_price <= 0:
        return {}
    highs = [float(row.high or row.close or 0) for row in rows if row.high or row.close]
    lows = [float(row.low or row.close or 0) for row in rows if row.low or row.close]
    return {
        "start_price": start_price,
        "end_price": end_price,
        "high": max(highs) if highs else None,
        "low": min(lows) if lows else None,
        "raw_return": end_price / start_price - 1.0,
        "mfe": (max(highs) / start_price - 1.0) if highs else None,
        "mae": (min(lows) / start_price - 1.0) if lows else None,
        "max_drawdown": _drawdown(rows, start_price),
    }


def observe_episode_outcomes(
    db: Session,
    *,
    episode: DecisionEpisode | int,
    as_of: datetime | None = None,
    horizons: Iterable[int] = EVALUATION_HORIZONS,
    commit: bool = True,
) -> list[DecisionEvaluationOutcome]:
    """Append/update pending forward observations using only persisted bars."""

    row = episode if isinstance(episode, DecisionEpisode) else db.get(DecisionEpisode, episode)
    if row is None:
        return []
    cutoff = _utc_naive(as_of) or datetime.now(UTC).replace(tzinfo=None)
    if row.symbol == "__PORTFOLIO__":
        results: list[DecisionEvaluationOutcome] = []
        for horizon in horizons:
            results.append(_upsert_missing_outcome(db, row, int(horizon), cutoff, "PORTFOLIO_PRICE_NOT_MODELED"))
        if commit:
            db.commit()
        return results
    calendar = TradingCalendarService(db)
    results = []
    for horizon in sorted({int(value) for value in horizons if int(value) > 0}):
        target_date = _trading_date_after(calendar, row.trading_date, horizon)
        existing = db.execute(select(DecisionEvaluationOutcome).where(
            DecisionEvaluationOutcome.episode_id == row.id,
            DecisionEvaluationOutcome.target_key == row.symbol,
            DecisionEvaluationOutcome.horizon_trading_days == horizon,
            DecisionEvaluationOutcome.calculation_version == OUTCOME_CALCULATION_VERSION,
        )).scalar_one_or_none()
        if target_date is None:
            outcome = _upsert_missing_outcome(db, row, horizon, cutoff, "MISSING_TRADING_CALENDAR", existing=existing)
            results.append(outcome)
            continue
        bars = _bars_for(db, symbol=row.symbol, start=row.trading_date, end=target_date, cutoff=cutoff)
        start_bar = _choose_bar(bars, row.trading_date)
        end_bar = _choose_bar(bars, target_date)
        if start_bar is None or end_bar is None:
            outcome = _upsert_missing_outcome(db, row, horizon, cutoff, "MISSING_PRICE", existing=existing, target_date=target_date)
            results.append(outcome)
            continue
        values = _outcome_values(bars, start_bar, end_bar)
        quality = "ADJUSTMENT_UNCERTAIN" if str(start_bar.adjustment or "").upper() not in {"RAW", "QFQ", "HFQ", "ADJUSTED"} else "OK"
        source_refs = {
            "start_bar_id": start_bar.id,
            "end_bar_id": end_bar.id,
            "provider": end_bar.provider,
            "available_at": _iso(end_bar.available_at),
            "price_basis": str(end_bar.adjustment or "UNKNOWN").upper(),
        }
        outcome = existing or DecisionEvaluationOutcome(
            episode_id=row.id,
            target_type="SYMBOL",
            target_key=row.symbol,
            horizon_trading_days=horizon,
            calculation_version=OUTCOME_CALCULATION_VERSION,
            recalculation_count=0,
        )
        if existing is not None and existing.source_refs_json != source_refs:
            outcome.recalculation_count = int(existing.recalculation_count or 0) + 1
            outcome.last_source_change_at = cutoff
        outcome.reference_trade_date = row.trading_date
        outcome.target_trade_date = target_date
        outcome.start_price = values["start_price"]
        outcome.end_price = values["end_price"]
        outcome.high = values["high"]
        outcome.low = values["low"]
        outcome.raw_return = values["raw_return"]
        outcome.directional_return = (values["raw_return"] or 0.0) * _action_sign(row)
        outcome.mfe = values["mfe"]
        outcome.mae = values["mae"]
        outcome.max_drawdown = values["max_drawdown"]
        outcome.price_adjustment_method = str(end_bar.adjustment or "UNKNOWN").upper()
        outcome.status = "COMPLETE"
        outcome.quality_status = quality
        outcome.observation_complete = True
        outcome.available_at = _utc_naive(end_bar.available_at)
        outcome.source_refs_json = source_refs
        outcome.computed_at = cutoff
        if existing is None:
            db.add(outcome)
        results.append(outcome)
    if commit:
        db.commit()
    return results


def _upsert_missing_outcome(
    db: Session,
    episode: DecisionEpisode,
    horizon: int,
    cutoff: datetime,
    quality: str,
    *,
    existing: DecisionEvaluationOutcome | None = None,
    target_date: date | None = None,
) -> DecisionEvaluationOutcome:
    outcome = existing or DecisionEvaluationOutcome(
        episode_id=episode.id,
        target_type="SYMBOL" if episode.symbol != "__PORTFOLIO__" else "PORTFOLIO",
        target_key=episode.symbol,
        horizon_trading_days=horizon,
        calculation_version=OUTCOME_CALCULATION_VERSION,
    )
    outcome.reference_trade_date = episode.trading_date
    outcome.target_trade_date = target_date
    outcome.status = "PENDING"
    outcome.quality_status = quality
    outcome.observation_complete = False
    outcome.available_at = cutoff
    outcome.source_refs_json = {"quality": quality, "observed_at": _iso(cutoff)}
    outcome.computed_at = cutoff
    if existing is None:
        db.add(outcome)
    return outcome


def observe_pending_outcomes(
    db: Session,
    *,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    as_of: datetime | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    query = select(DecisionEpisode).where(DecisionEpisode.status == "FROZEN")
    if user_id is not None:
        query = query.where(DecisionEpisode.user_id == user_id)
    if portfolio_id is not None:
        query = query.where(DecisionEpisode.portfolio_id == portfolio_id)
    episodes = db.execute(query.order_by(DecisionEpisode.decision_time.asc()).limit(max(1, min(limit, 5000)))).scalars().all()
    complete = 0
    pending = 0
    for episode in episodes:
        rows = observe_episode_outcomes(db, episode=episode, as_of=as_of, commit=False)
        complete += sum(1 for row in rows if row.observation_complete)
        pending += sum(1 for row in rows if not row.observation_complete)
    db.commit()
    return {"episodes": len(episodes), "complete_outcomes": complete, "pending_outcomes": pending}


def _run_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _git_commit() -> str | None:
    return os.getenv("GIT_COMMIT") or os.getenv("GITHUB_SHA")


def _start_evaluation_run(
    db: Session,
    *,
    run_type: str,
    user_id: int | None,
    portfolio_id: int | None,
    data_cutoff: datetime | None,
) -> EvaluationRun:
    run = EvaluationRun(
        run_id=_run_id("eval"),
        run_type=run_type,
        user_id=user_id,
        portfolio_id=portfolio_id,
        code_version=_git_commit(),
        git_commit=_git_commit(),
        decision_contract_version=CONTRACT_VERSION,
        evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
        data_cutoff=_utc_naive(data_cutoff),
    )
    db.add(run)
    db.flush()
    return run


def _finish_evaluation_run(db: Session, run: EvaluationRun, result: Any, *, status: str = "COMPLETED", error: str | None = None) -> None:
    run.status = status
    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    run.result_hash = content_hash(result)
    run.error_summary = error[:1000] if error else None
    db.commit()


def replay_episode(
    db: Session,
    *,
    episode_id: str | int,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    mode: str = "FACT_REPLAY",
) -> dict[str, Any]:
    mode = str(mode).upper()
    if mode not in REPLAY_MODES:
        raise ValueError(f"unsupported_replay_mode:{mode}")
    query = select(DecisionEpisode).where(DecisionEpisode.episode_id == str(episode_id))
    if str(episode_id).isdigit():
        query = select(DecisionEpisode).where((DecisionEpisode.episode_id == str(episode_id)) | (DecisionEpisode.id == int(episode_id)))
    if user_id is not None:
        query = query.where(DecisionEpisode.user_id == user_id)
    if portfolio_id is not None:
        query = query.where(DecisionEpisode.portfolio_id == portfolio_id)
    episode = db.execute(query).scalar_one_or_none()
    if episode is None:
        raise ValueError("episode_not_found")
    run = _start_evaluation_run(
        db,
        run_type=mode,
        user_id=episode.user_id,
        portfolio_id=episode.portfolio_id,
        data_cutoff=episode.source_data_cutoff,
    )
    manifest = _episode_manifest(db, episode.id)
    try:
        with HistoricalReplayNetworkPolicy():
            pit = validate_point_in_time(
                ({
                    "input_type": row.input_type,
                    "timestamp": row.timestamp,
                    "available_at": row.available_at,
                } for row in manifest),
                episode.decision_time,
            )
            result: dict[str, Any] = {
                "episode_id": episode.episode_id,
                "mode": mode,
                "decision_type": episode.decision_type,
                "portfolio_gate_result": episode.portfolio_gate_result,
                "manifest_hash": episode.manifest_hash,
                "point_in_time": pit,
                "historical": mode != "MODEL_RECOMPUTE",
                "eligible_for_historical_metrics": mode != "MODEL_RECOMPUTE",
            }
            if mode == "FACT_REPLAY":
                result["replay_label"] = "HISTORICAL_DECISION" if episode.evidence_status == "READY" else "INSUFFICIENT_HISTORICAL_EVIDENCE"
            elif mode == "DETERMINISTIC_LOGIC_REPLAY":
                result["contract_version"] = CONTRACT_VERSION
                result["drift_detected"] = episode.decision_contract_version != CONTRACT_VERSION
                result["replay_label"] = "DETERMINISTIC_LOGIC_REPLAY"
            else:
                result["replay_label"] = "RECOMPUTED_WITH_CURRENT_MODEL"
                result["eligible_for_historical_metrics"] = False
            run.input_hash = content_hash([row.content_hash for row in manifest])
            _finish_evaluation_run(db, run, result)
            return {"run_id": run.run_id, **result}
    except Exception as exc:
        _finish_evaluation_run(db, run, {"error": str(exc)}, status="BLOCKED", error=str(exc))
        raise


def replay_date_range(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    start: date,
    end: date,
    mode: str = "FACT_REPLAY",
    limit: int = 1000,
) -> dict[str, Any]:
    if end < start:
        raise ValueError("invalid_date_range")
    if (end - start).days > 370:
        raise ValueError("date_range_too_large")
    episodes = db.execute(select(DecisionEpisode).where(
        DecisionEpisode.user_id == user_id,
        DecisionEpisode.portfolio_id == portfolio_id,
        DecisionEpisode.trading_date >= start,
        DecisionEpisode.trading_date <= end,
    ).order_by(DecisionEpisode.decision_time.asc()).limit(max(1, min(limit, 5000)))).scalars().all()
    results = [replay_episode(db, episode_id=row.episode_id, user_id=user_id, portfolio_id=portfolio_id, mode=mode) for row in episodes]
    return {"start": start.isoformat(), "end": end.isoformat(), "mode": mode, "episodes": len(results), "results": results}


def capture_candidate_evaluations(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    as_of: datetime | None = None,
    candidate_run_id: int | None = None,
) -> list[CandidateEvaluation]:
    cutoff = _utc_naive(as_of) or datetime.now(UTC).replace(tzinfo=None)
    query = select(CandidateRun).where(
        CandidateRun.user_id == user_id,
        CandidateRun.portfolio_id == portfolio_id,
        CandidateRun.captured_at <= cutoff,
        CandidateRun.status == "COMPLETED",
    )
    if candidate_run_id is not None:
        query = query.where(CandidateRun.id == candidate_run_id)
    runs = db.execute(query.order_by(CandidateRun.captured_at.asc(), CandidateRun.id.asc())).scalars().all()
    result: list[CandidateEvaluation] = []
    for run in runs:
        scores = db.execute(select(CandidateScore).where(CandidateScore.candidate_run_id == run.id).order_by(CandidateScore.rank.asc())).scalars().all()
        for score in scores:
            existing = db.execute(select(CandidateEvaluation).where(CandidateEvaluation.candidate_run_id == run.id, CandidateEvaluation.code == score.code)).scalar_one_or_none()
            if existing is not None:
                result.append(existing)
                continue
            result.append(CandidateEvaluation(
                user_id=user_id,
                portfolio_id=portfolio_id,
                candidate_run_id=run.id,
                code=score.code,
                stage=str(score.stage or "WATCHLIST").upper(),
                stage_entered_at=run.captured_at,
                observed_at=run.captured_at,
                source_data_cutoff=run.captured_at,
                quality_status=str(score.quality_status or run.quality_status or "PENDING").upper(),
                outcome_summary_json={"candidate_stage": str(score.stage or "WATCHLIST").upper(), "candidate_run_id": run.id},
            ))
            db.add(result[-1])
    db.commit()
    return result


def capture_trigger_evaluations(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    as_of: datetime | None = None,
    limit: int = 500,
) -> list[TriggerEvaluation]:
    cutoff = _utc_naive(as_of) or datetime.now(UTC).replace(tzinfo=None)
    events = db.execute(select(TriggerEvent).where(
        TriggerEvent.user_id == user_id,
        TriggerEvent.portfolio_id == portfolio_id,
        TriggerEvent.detected_at <= cutoff,
    ).order_by(TriggerEvent.detected_at.asc(), TriggerEvent.id.asc()).limit(max(1, min(limit, 5000)))).scalars().all()
    result: list[TriggerEvaluation] = []
    for event in events:
        existing = db.execute(select(TriggerEvaluation).where(TriggerEvaluation.trigger_event_id == event.id)).scalar_one_or_none()
        if existing is not None:
            result.append(existing)
            continue
        analysis_refreshed = event.analysis_run_id is not None or event.analysis_job_id is not None
        episode = None
        if event.analysis_run_id:
            episode = db.execute(select(DecisionEpisode).where(
                DecisionEpisode.decision_run_id == event.analysis_run_id,
                DecisionEpisode.user_id == user_id,
                DecisionEpisode.portfolio_id == portfolio_id,
            ).order_by(DecisionEpisode.id.asc()).limit(1)).scalar_one_or_none()
        previous_episode = None
        if episode is not None:
            previous_episode = db.execute(select(DecisionEpisode).where(
                DecisionEpisode.user_id == user_id,
                DecisionEpisode.portfolio_id == portfolio_id,
                DecisionEpisode.decision_time < event.detected_at,
            ).order_by(DecisionEpisode.decision_time.desc(), DecisionEpisode.id.desc()).limit(1)).scalar_one_or_none()
        row = TriggerEvaluation(
            user_id=user_id,
            portfolio_id=portfolio_id,
            trigger_event_id=event.id,
            episode_id=episode.id if episode else None,
            trigger_type=event.trigger_type,
            priority=event.priority,
            trigger_status=event.status,
            analysis_refreshed=analysis_refreshed,
            decision_changed=(
                previous_episode is not None and previous_episode.decision_type != episode.decision_type
            ) if episode is not None else None,
            resulting_decision_type=episode.decision_type if episode else None,
            quality_status="OK" if analysis_refreshed else "PENDING",
            observed_at=event.detected_at,
            source_data_cutoff=event.detected_at,
            metadata_json={"resolution": event.resolution, "analysis_run_id": event.analysis_run_id},
        )
        db.add(row)
        result.append(row)
    db.commit()
    return result


def _sample_status(count: int) -> str:
    if count < LOW_SAMPLE_THRESHOLD:
        return "INSUFFICIENT_SAMPLE"
    if count < EARLY_SAMPLE_THRESHOLD:
        return "EARLY_EVIDENCE"
    return "MATURE_SAMPLE"


def _metric(values: list[float | None]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"median": None, "mean": None, "n": 0, "status": "INSUFFICIENT_SAMPLE"}
    return {"median": statistics.median(clean), "mean": statistics.fmean(clean), "n": len(clean), "status": _sample_status(len(clean)), "distribution": clean}


def evaluation_summary(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> dict[str, Any]:
    cutoff = _utc_naive(as_of)
    query = select(DecisionEpisode).where(DecisionEpisode.user_id == user_id, DecisionEpisode.portfolio_id == portfolio_id)
    if cutoff is not None:
        query = query.where(DecisionEpisode.decision_time <= cutoff)
    episodes = db.execute(query.order_by(DecisionEpisode.decision_time.asc())).scalars().all()
    outcomes = db.execute(select(DecisionEvaluationOutcome).join(DecisionEpisode, DecisionEvaluationOutcome.episode_id == DecisionEpisode.id).where(
        DecisionEpisode.user_id == user_id, DecisionEpisode.portfolio_id == portfolio_id,
    )).scalars().all()
    decision_distribution = Counter(row.decision_type for row in episodes)
    no_action = sum(1 for row in episodes if row.decision_type in {"NO_ACTION", "HOLD_ONLY"} or row.no_action_reason)
    horizon_metrics = {
        str(horizon): _metric([row.raw_return for row in outcomes if row.horizon_trading_days == horizon and row.observation_complete])
        for horizon in EVALUATION_HORIZONS
    }
    return {
        "status": "OK",
        "portfolio_id": portfolio_id,
        "episodes": len(episodes),
        "decision_distribution": dict(decision_distribution),
        "no_action_count": no_action,
        "no_action_rate": (no_action / len(episodes)) if episodes else None,
        "candidate_stage_distribution": dict(Counter(row.candidate_stage for row in episodes if row.candidate_stage)),
        "trigger_count": sum(1 for row in episodes if row.trigger_snapshot_id is not None),
        "outcomes": len(outcomes),
        "coverage": {
            "complete_outcomes": sum(1 for row in outcomes if row.observation_complete),
            "missing_outcomes": sum(1 for row in outcomes if not row.observation_complete),
            "episodes_with_ready_evidence": sum(1 for row in episodes if row.evidence_status == "READY"),
        },
        "horizons": horizon_metrics,
        "mfe": _metric([row.mfe for row in outcomes if row.observation_complete]),
        "mae": _metric([row.mae for row in outcomes if row.observation_complete]),
        "drawdown": _metric([row.max_drawdown for row in outcomes if row.observation_complete]),
        "data_quality_failures": dict(Counter(row.quality_status for row in outcomes if row.quality_status not in {"OK", "PENDING"})),
        "evaluation_schema_version": EVALUATION_SCHEMA_VERSION,
        "decision_contract_version": CONTRACT_VERSION,
    }


def evaluation_coverage(db: Session, *, user_id: int, portfolio_id: int) -> dict[str, Any]:
    episodes = db.execute(select(DecisionEpisode).where(DecisionEpisode.user_id == user_id, DecisionEpisode.portfolio_id == portfolio_id)).scalars().all()
    outcomes = db.execute(select(DecisionEvaluationOutcome).join(DecisionEpisode, DecisionEvaluationOutcome.episode_id == DecisionEpisode.id).where(
        DecisionEpisode.user_id == user_id, DecisionEpisode.portfolio_id == portfolio_id,
    )).scalars().all()
    papers = db.execute(select(PaperObservation).join(PaperObservationRun, PaperObservation.run_id == PaperObservationRun.id).where(
        PaperObservationRun.user_id == user_id, PaperObservationRun.portfolio_id == portfolio_id,
    )).scalars().all()
    return {
        "episodes": len(episodes),
        "episodes_ready": sum(1 for row in episodes if row.evidence_status == "READY"),
        "episodes_insufficient_evidence": sum(1 for row in episodes if row.evidence_status != "READY"),
        "outcomes": len(outcomes),
        "outcomes_complete": sum(1 for row in outcomes if row.observation_complete),
        "outcomes_pending": sum(1 for row in outcomes if not row.observation_complete),
        "paper_observations": len(papers),
        "paper_observations_captured": sum(1 for row in papers if row.status == "CAPTURED"),
        "paper_observations_missing": sum(1 for row in papers if row.status != "CAPTURED"),
        "status": "OK" if episodes else "INSUFFICIENT_HISTORICAL_EVIDENCE",
    }


def verify_snapshot_hashes(
    db: Session, *, user_id: int | None = None, portfolio_id: int | None = None
) -> dict[str, Any]:
    """Verify persisted EvaluationSnapshot payloads without mutating evidence."""
    query = select(EvaluationSnapshot).join(DecisionEpisode, EvaluationSnapshot.episode_id == DecisionEpisode.id)
    if user_id is not None:
        query = query.where(DecisionEpisode.user_id == user_id)
    if portfolio_id is not None:
        query = query.where(DecisionEpisode.portfolio_id == portfolio_id)
    rows = db.execute(query.order_by(EvaluationSnapshot.id.asc())).scalars().all()
    mismatches = []
    for row in rows:
        actual = content_hash(row.payload_json or {})
        if actual != row.content_hash:
            mismatches.append({"snapshot_id": row.id, "episode_id": row.episode_id, "expected": row.content_hash, "actual": actual})
    return {"checked": len(rows), "mismatches": mismatches, "status": "PASS" if not mismatches else "BLOCKED"}


def list_evaluation_episodes(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    as_of: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = select(DecisionEpisode).where(DecisionEpisode.user_id == user_id, DecisionEpisode.portfolio_id == portfolio_id)
    cutoff = _utc_naive(as_of)
    if cutoff is not None:
        query = query.where(DecisionEpisode.decision_time <= cutoff)
    rows = db.execute(query.order_by(DecisionEpisode.decision_time.desc(), DecisionEpisode.id.desc()).limit(max(1, min(limit, 1000)))).scalars().all()
    return [serialize_episode(db, row) for row in rows]


def get_evaluation_episode(db: Session, *, user_id: int, portfolio_id: int, episode_id: str) -> dict[str, Any] | None:
    row = db.execute(select(DecisionEpisode).where(
        DecisionEpisode.episode_id == episode_id,
        DecisionEpisode.user_id == user_id,
        DecisionEpisode.portfolio_id == portfolio_id,
    )).scalar_one_or_none()
    return serialize_episode(db, row, include_payload=True) if row else None


def paper_observation_status(db: Session, *, user_id: int, portfolio_id: int) -> dict[str, Any]:
    runs = db.execute(select(PaperObservationRun).where(
        PaperObservationRun.user_id == user_id,
        PaperObservationRun.portfolio_id == portfolio_id,
    ).order_by(PaperObservationRun.observation_date.desc(), PaperObservationRun.id.desc()).limit(20)).scalars().all()
    return {
        "status": "READY" if any(row.status == "CAPTURED" for row in runs) else "OBSERVATION_MISSING",
        "runs": [
            {
                "run_id": row.run_id,
                "observation_date": row.observation_date.isoformat(),
                "status": row.status,
                "source_data_cutoff": _iso(row.source_data_cutoff),
                "missing_reason": row.missing_reason,
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.finished_at),
            }
            for row in runs
        ],
    }


def capture_realtime_paper_observation(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    now: datetime | None = None,
    analysis_run_id: int | None = None,
) -> dict[str, Any]:
    moment = _utc_naive(now) or datetime.now(UTC).replace(tzinfo=None)
    local_date = moment.replace(tzinfo=UTC).astimezone(CHINA_TZ).date()
    paper_run = PaperObservationRun(
        run_id=_run_id("paper"),
        user_id=user_id,
        portfolio_id=portfolio_id,
        observation_date=local_date,
        status="RUNNING",
        source_data_cutoff=moment,
        code_version=_git_commit(),
        decision_contract_version=CONTRACT_VERSION,
        evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
    )
    db.add(paper_run)
    db.flush()
    if analysis_run_id is None:
        analysis_run = db.execute(select(AnalysisRun).join(AnalysisJob, AnalysisRun.job_id == AnalysisJob.id).where(
            AnalysisRun.user_id == user_id,
            AnalysisJob.portfolio_id == portfolio_id,
            AnalysisJob.status == "succeeded",
            AnalysisRun.created_at <= moment,
        ).order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc()).limit(1)).scalar_one_or_none()
    else:
        analysis_run = db.get(AnalysisRun, analysis_run_id)
    if analysis_run is None or _utc_naive(analysis_run.created_at) is None or _utc_naive(analysis_run.created_at).replace(tzinfo=UTC).astimezone(CHINA_TZ).date() != local_date:
        paper_run.status = "OBSERVATION_MISSING"
        paper_run.missing_reason = "MISSED_DECISION_CAPTURE"
        paper_run.finished_at = moment
        db.add(PaperObservation(
            observation_id=_run_id("obs"),
            run_id=paper_run.id,
            status="OBSERVATION_MISSING",
            captured_at=moment,
            source_data_cutoff=moment,
            missing_reason="MISSED_DECISION_CAPTURE",
        ))
        db.commit()
        return {"run_id": paper_run.run_id, "status": paper_run.status, "missing_reason": paper_run.missing_reason}
    episodes = capture_decision_episodes(
        db,
        user_id=user_id,
        portfolio_id=portfolio_id,
        analysis_run_id=analysis_run.id,
        source_mode=PAPER_OBSERVATION_MODE,
        now=moment,
    )
    if not episodes:
        paper_run.status = "OBSERVATION_MISSING"
        paper_run.missing_reason = "DECISION_MEMORY_NOT_CAPTURED"
        paper_run.finished_at = moment
        db.add(PaperObservation(
            observation_id=_run_id("obs"), run_id=paper_run.id, status="OBSERVATION_MISSING",
            captured_at=moment, source_data_cutoff=moment, missing_reason=paper_run.missing_reason,
        ))
    else:
        paper_run.status = "CAPTURED"
        paper_run.finished_at = moment
        for episode in episodes:
            db.add(PaperObservation(
                observation_id=_run_id("obs"), run_id=paper_run.id, episode_id=episode.id,
                status="CAPTURED", captured_at=moment, source_data_cutoff=episode.source_data_cutoff,
                freeze_hash=episode.manifest_hash,
                metadata_json={"mode": PAPER_OBSERVATION_MODE, "decision_run_id": episode.decision_run_id},
            ))
    db.commit()
    return {"run_id": paper_run.run_id, "status": paper_run.status, "episode_ids": [row.episode_id for row in episodes], "missing_reason": paper_run.missing_reason}


def freeze_paper_observation(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return capture_realtime_paper_observation(*args, **kwargs)


__all__ = [
    "EVALUATION_HORIZONS",
    "EVALUATION_SCHEMA_VERSION",
    "EvaluationDataQualityError",
    "HistoricalReplayNetworkPolicy",
    "PAPER_OBSERVATION_MODE",
    "REPLAY_MODES",
    "assert_external_io_allowed",
    "canonical_json",
    "capture_candidate_evaluations",
    "capture_decision_episode",
    "capture_decision_episodes",
    "capture_realtime_paper_observation",
    "capture_trigger_evaluations",
    "content_hash",
    "evaluation_coverage",
    "evaluation_summary",
    "freeze_paper_observation",
    "get_evaluation_episode",
    "list_evaluation_episodes",
    "observe_episode_outcomes",
    "observe_pending_outcomes",
    "paper_observation_status",
    "replay_date_range",
    "replay_episode",
    "serialize_episode",
    "serialize_outcome",
    "validate_point_in_time",
    "verify_snapshot_hashes",
]
