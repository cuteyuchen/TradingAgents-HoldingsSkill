"""Durable offline backtest runner."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache
from ..market_models import TradingCalendar
from ..v2_models import PortfolioSnapshot
from .availability import build_replay_availability_manifest
from .bootstrap import date_block_bootstrap
from .config import (
    BACKTEST_ENGINE_VERSION,
    BACKTEST_STATUSES,
    DEFAULT_HORIZONS,
    MAX_BOOTSTRAP_ITERATIONS,
    MAX_DATE_SPAN_DAYS,
    METRICS_ENGINE_VERSION,
    TransactionCostModel,
    current_production_config,
    current_config_version,
    current_transaction_cost_model,
    normalise_replay_mode,
    normalise_scope,
    validate_horizons,
)
from .metrics import candidate_metric_slices, market_metric_slices, summarise_values
from .models import BacktestMetricSlice, BacktestRun
from .outcomes import calculate_forward_outcome, calculate_market_forward_outcome
from .replay import (
    ReplayCase,
    ReplayDataQualityError,
    content_hash,
    historical_replay_network_policy,
    load_replay_facts,
    replay_bar_cases,
    replay_candidate_cases,
    replay_market_cases,
    replay_memory_cases,
)

LEASE_MINUTES = 10
WORKER_HEARTBEAT_SECONDS = 30
MAX_BACKTEST_WORKERS_PER_TICK = 4


class LeaseLostError(RuntimeError):
    """Raised when a worker generation can no longer own the run."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json(item) for item in value]
    return value


def _normalise_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _validate_date_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise ValueError("start_date_must_not_exceed_end_date")
    if (end_date - start_date).days > MAX_DATE_SPAN_DAYS:
        raise ValueError("backtest_date_range_too_large")


def _lease() -> datetime:
    return _now() + timedelta(minutes=LEASE_MINUTES)


_SOURCE_REFERENCE_FIELDS = (
    "snapshot_id",
    "market_snapshot_id",
    "metric_snapshot_id",
    "market_score_snapshot_id",
    "market_metric_snapshot_id",
    "portfolio_snapshot_id",
    "candidate_run_id",
    "analysis_run_id",
    "decision_memory_id",
    "outcome_id",
    "quote_snapshot_id",
    "id",
)


def _source_ids(facts: dict[str, Iterable[Any]]) -> list[str]:
    """Return typed, deterministic source references for a frozen run."""

    result: set[str] = set()
    for category, rows in facts.items():
        if rows is None:
            continue
        for row in rows:
            for field_name in _SOURCE_REFERENCE_FIELDS:
                value = getattr(row, field_name, None)
                if value is not None:
                    result.add(f"{category}:{field_name}:{value}")
    return sorted(result)


def _data_hash(*, manifest: dict[str, Any], source_ids: Iterable[str], start_date: date, end_date: date, config: dict[str, Any], scope: str, replay_mode: str) -> str:
    """Hash only stable frozen inputs; generated timestamps never participate."""

    return content_hash({
        "manifest_hash": manifest.get("data_hash"),
        "source_ids": sorted(set(str(item) for item in source_ids)),
        "range": [start_date, end_date],
        "scope": scope,
        "replay_mode": replay_mode,
        "config_hash": content_hash(config),
        "engine_version": BACKTEST_ENGINE_VERSION,
        "metrics_engine_version": METRICS_ENGINE_VERSION,
    })


def _calculation_key(*, scope: str, replay_mode: str, start_date: date, end_date: date, portfolio_id: int | None, user_id: int | None, config: dict[str, Any], data_hash: str, horizons: tuple[int, ...], experiment: dict[str, Any] | None) -> str:
    return content_hash({
        "scope": scope,
        "replay_mode": replay_mode,
        "start_date": start_date,
        "end_date": end_date,
        "portfolio_id": portfolio_id,
        "user_id": user_id,
        "config": config,
        "data_hash": data_hash,
        "horizons": horizons,
        "experiment": experiment or {},
        "engine_version": BACKTEST_ENGINE_VERSION,
    })


def _allow_update(row: BacktestRun | BacktestMetricSlice) -> None:
    setattr(row, "_allow_research_update", True)


def _run_transaction_cost_model(run: BacktestRun) -> TransactionCostModel:
    experiment = run.experiment_config_json if isinstance(run.experiment_config_json, dict) else {}
    snapshot = experiment.get("transaction_cost_model")
    return TransactionCostModel.from_dict(snapshot if isinstance(snapshot, dict) else None)


def create_backtest_run(
    db: Session,
    *,
    scope: str,
    replay_mode: str,
    start_date: date,
    end_date: date,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    horizons: Iterable[int] | None = None,
    experiment_config: dict[str, Any] | None = None,
    random_seed: int = 0,
    bootstrap_iterations: int = 500,
) -> BacktestRun:
    scope = normalise_scope(scope)
    replay_mode = normalise_replay_mode(replay_mode)
    start_date = _normalise_date(start_date)
    end_date = _normalise_date(end_date)
    _validate_date_range(start_date, end_date)
    horizon_values = validate_horizons(list(horizons or DEFAULT_HORIZONS), market=scope == "MARKET")
    if bootstrap_iterations <= 0 or bootstrap_iterations > MAX_BOOTSTRAP_ITERATIONS:
        raise ValueError("invalid_bootstrap_iterations")
    if scope == "PORTFOLIO_DECISION" and portfolio_id is None:
        raise ValueError("portfolio_id_required_for_portfolio_scope")
    from ..governance.service import lineage_fields, resolve_production_parameters

    governance_context = resolve_production_parameters(db)
    governance_lineage = lineage_fields(governance_context)
    config = governance_context["snapshot"]
    transaction_cost_model = current_transaction_cost_model()
    experiment_payload = {
        "horizons": list(horizon_values),
        "bootstrap_iterations": bootstrap_iterations,
        **(experiment_config or {}),
        # Experiments may describe a study, but cannot replace the
        # authoritative Phase E broker cost settings.
        "transaction_cost_model": transaction_cost_model.as_dict(),
    }
    manifest = build_replay_availability_manifest(
        db,
        start_date=start_date,
        end_date=end_date,
        portfolio_id=portfolio_id,
        user_id=user_id,
    )
    source_ids: list[str] = []
    try:
        _, _, source_ids = _load_replay_rows(
            db,
            scope=scope,
            replay_mode=replay_mode,
            start_date=start_date,
            end_date=end_date,
            horizons=horizon_values,
            user_id=user_id,
            portfolio_id=portfolio_id,
            as_of=None,
        )
    except (ReplayDataQualityError, SQLAlchemyError):
        # A run must still be creatable when the requested historical dataset is
        # absent.  The execution path will persist an auditable DATA_GAP or
        # LEAKAGE_BLOCKED result instead of manufacturing source facts.
        source_ids = []
    manifest["frozen_source_ids"] = source_ids
    manifest["frozen_source_set_hash"] = content_hash(source_ids)
    data_hash = _data_hash(
        manifest=manifest,
        source_ids=source_ids,
        start_date=start_date,
        end_date=end_date,
        config=config,
        scope=scope,
        replay_mode=replay_mode,
    )
    key = _calculation_key(
        scope=scope,
        replay_mode=replay_mode,
        start_date=start_date,
        end_date=end_date,
        portfolio_id=portfolio_id,
        user_id=user_id,
        config=config,
        data_hash=data_hash,
        horizons=horizon_values,
        experiment=experiment_payload,
    )
    existing = db.execute(select(BacktestRun).where(BacktestRun.calculation_key == key)).scalar_one_or_none()
    if existing is not None:
        return existing
    row = BacktestRun(
        user_id=user_id,
        portfolio_id=portfolio_id,
        scope=scope,
        replay_mode=replay_mode,
        start_date=start_date,
        end_date=end_date,
        status="QUEUED",
        progress_percent=0,
        current_stage="DATA_AUDIT",
        config_version=current_config_version(),
        engine_version=BACKTEST_ENGINE_VERSION,
        baseline_config_json=config,
        experiment_config_json=experiment_payload,
        data_manifest_json=manifest,
        data_hash=data_hash,
        calculation_key=key,
        random_seed=random_seed,
        horizons_json=list(horizon_values),
        known_limitations_json=manifest.get("known_limitations", []),
        lease_expires_at=None,
        attempt_count=1,
        parameter_set_version_id=governance_lineage["parameter_set_version_id"],
        parameter_set_version=governance_lineage["parameter_set_version"],
        parameter_set_hash=governance_lineage["parameter_set_hash"],
        governance_lineage_json=governance_lineage["governance_lineage_json"],
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.execute(select(BacktestRun).where(BacktestRun.calculation_key == key)).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    return row


def _cas_worker_write(
    db: Session,
    run: BacktestRun,
    generation: int,
    *,
    values: dict[str, Any],
) -> None:
    """Atomically write only when the current worker generation still owns the row."""

    updated = db.execute(
        update(BacktestRun)
        .where(
            BacktestRun.id == run.id,
            BacktestRun.status == "RUNNING",
            BacktestRun.attempt_count == generation,
        )
        .values(**values)
    ).rowcount
    if not updated:
        db.rollback()
        raise LeaseLostError("LEASE_LOST")
    db.refresh(run)


def _write_run_fields(
    db: Session,
    run: BacktestRun,
    *,
    values: dict[str, Any],
    generation: int | None = None,
) -> None:
    if generation is None:
        _allow_update(run)
        for key, value in values.items():
            setattr(run, key, value)
        db.flush()
        return
    _cas_worker_write(db, run, generation, values=values)


def _write_worker_failure(
    db: Session,
    run: BacktestRun,
    generation: int,
    *,
    values: dict[str, Any],
) -> bool:
    """Persist a failure only while this generation still owns the run."""

    try:
        _write_run_fields(db, run, values=values, generation=generation)
    except LeaseLostError:
        db.rollback()
        return False
    db.commit()
    return True


def _write_final_state(
    db: Session,
    run: BacktestRun,
    *,
    values: dict[str, Any],
    generation: int | None,
) -> bool:
    if generation is None:
        _write_run_fields(db, run, values=values)
        db.commit()
        return True
    return _write_worker_failure(db, run, generation, values=values)


def _set_stage(
    db: Session,
    run: BacktestRun,
    stage: str,
    progress: int,
    generation: int | None = None,
    extra_values: dict[str, Any] | None = None,
) -> None:
    values = {
        "current_stage": stage,
        "progress_percent": max(0, min(100, progress)),
        "last_heartbeat_at": _now(),
        "lease_expires_at": _lease(),
        **(extra_values or {}),
    }
    _write_run_fields(db, run, values=values, generation=generation)


def heartbeat_backtest_run(
    db: Session,
    *,
    run_id: int,
    generation: int | None = None,
    user_id: int | None = None,
) -> BacktestRun | None:
    row = db.get(BacktestRun, run_id)
    if row is None or (user_id is not None and row.user_id != user_id):
        return None
    if row.status != "RUNNING" or row.cancel_requested:
        return row
    if generation is not None:
        updated = db.execute(
            update(BacktestRun)
            .where(
                BacktestRun.id == run_id,
                BacktestRun.status == "RUNNING",
                BacktestRun.attempt_count == generation,
            )
            .values(last_heartbeat_at=_now(), lease_expires_at=_lease())
        ).rowcount
        if updated:
            db.refresh(row)
        return row
    _allow_update(row)
    row.last_heartbeat_at = _now()
    row.lease_expires_at = _lease()
    db.flush()
    return row


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def reclaim_stale_backtest_runs(db: Session, *, now: datetime | None = None) -> list[BacktestRun]:
    cutoff = _naive_utc(now) if now is not None else _now()
    rows = list(db.execute(select(BacktestRun).where(
        BacktestRun.status == "RUNNING",
        BacktestRun.lease_expires_at.is_not(None),
        BacktestRun.lease_expires_at < cutoff,
    ).order_by(BacktestRun.id.asc())).scalars())
    reclaimed: list[BacktestRun] = []
    for row in rows:
        generation = int(row.attempt_count or 0)
        updated = db.execute(
            update(BacktestRun)
            .where(
                BacktestRun.id == row.id,
                BacktestRun.status == "RUNNING",
                BacktestRun.attempt_count == generation,
                BacktestRun.lease_expires_at < cutoff,
            )
            .values(
                status="QUEUED",
                current_stage="DATA_AUDIT",
                lease_expires_at=None,
                last_heartbeat_at=None,
                attempt_count=generation + 1,
            )
        ).rowcount
        if updated:
            db.refresh(row)
            reclaimed.append(row)
    if reclaimed:
        db.flush()
    return reclaimed


def cancel_backtest_run(db: Session, *, run_id: int, user_id: int | None = None) -> BacktestRun | None:
    row = db.get(BacktestRun, run_id)
    if row is None or (user_id is not None and row.user_id != user_id):
        return None
    if row.status in {"COMPLETED", "FAILED", "CANCELLED", "INSUFFICIENT_DATA"}:
        return row
    _allow_update(row)
    row.cancel_requested = True
    row.status = "CANCELLED"
    row.current_stage = "FINALIZING"
    row.completed_at = _now()
    row.lease_expires_at = None
    db.flush()
    return row


def _mark_cancelled(db: Session, run: BacktestRun) -> None:
    _allow_update(run)
    run.cancel_requested = True
    run.status = "CANCELLED"
    run.current_stage = "FINALIZING"
    run.completed_at = _now()
    run.lease_expires_at = None
    run.last_heartbeat_at = _now()
    db.commit()


def _stop_if_cancelled(db: Session, run: BacktestRun, generation: int | None = None) -> bool:
    """Observe an external cancel request at durable stage boundaries."""

    db.refresh(run, attribute_names=["status", "cancel_requested"])
    if run.status == "CANCELLED" or run.cancel_requested:
        if generation is None:
            _mark_cancelled(db, run)
        return True
    return False


def _worker_lost_lease(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _invalidate_for_source_change(
    db: Session,
    run: BacktestRun,
    source_ids: Iterable[str],
    generation: int | None = None,
) -> BacktestRun:
    values = {
        "status": "INVALIDATED",
        "quality_status": "INVALIDATED",
        "leakage_status": "INVALIDATED",
        "error_code": "SOURCE_SET_CHANGED",
        "error_message": "Frozen replay source set changed before execution.",
        "known_limitations_json": sorted(set((run.known_limitations_json or []) + ["SOURCE_SET_CHANGED: run inputs were not frozen."])),
        "result_summary_json": {
            "no_production_write": True,
            "invalidated": True,
            "frozen_source_set_hash": (run.data_manifest_json or {}).get("frozen_source_set_hash"),
            "observed_source_set_hash": content_hash(sorted(set(source_ids))),
        },
        "completed_at": _now(),
        "lease_expires_at": None,
        "last_heartbeat_at": _now(),
    }
    _write_run_fields(db, run, values=values, generation=generation)
    db.commit()
    return run


def _calendar_dates(db: Session, start_date: date, end_date: date) -> list[date]:
    rows = db.execute(select(TradingCalendar.trade_date).where(
        TradingCalendar.market == "CN",
        TradingCalendar.trade_date >= start_date,
        TradingCalendar.trade_date <= end_date,
        TradingCalendar.is_open.is_(True),
    ).order_by(TradingCalendar.trade_date.asc())).scalars().all()
    return list(rows)


def _future_dates(calendar_dates: list[date], day: date, horizon: int) -> list[date]:
    return [value for value in calendar_dates if value > day][:horizon]


def _load_future_bars(db: Session, *, codes: Iterable[str], start_date: date, end_date: date, horizon: int) -> list[DailyBarCache]:
    code_values = sorted({str(code) for code in codes if code})
    if not code_values:
        return []
    return list(db.execute(select(DailyBarCache).where(
        DailyBarCache.market == "CN",
        DailyBarCache.code.in_(code_values),
        DailyBarCache.trade_date >= start_date,
        DailyBarCache.trade_date <= end_date + timedelta(days=max(370, horizon * 3)),
        DailyBarCache.adjustment == "QFQ",
        DailyBarCache.quality_status.in_(("VALID", "DEGRADED")),
    ).order_by(DailyBarCache.code.asc(), DailyBarCache.trade_date.asc(), DailyBarCache.id.asc())).scalars())


def _load_future_benchmark(db: Session, *, start_date: date, end_date: date, horizon: int) -> list[AllAMedianIndexDaily]:
    return list(db.execute(select(AllAMedianIndexDaily).where(
        AllAMedianIndexDaily.market == "CN",
        AllAMedianIndexDaily.trade_date >= start_date,
        AllAMedianIndexDaily.trade_date <= end_date + timedelta(days=max(370, horizon * 3)),
        AllAMedianIndexDaily.quality_status.in_(("VALID", "DEGRADED")),
    ).order_by(AllAMedianIndexDaily.trade_date.asc(), AllAMedianIndexDaily.id.asc())).scalars())


def _market_outcome_rows(cases: list[ReplayCase], benchmark_rows: list[AllAMedianIndexDaily], horizons: tuple[int, ...], calendar_dates: list[date], cutoff: datetime | None) -> list[dict[str, Any]]:
    result = []
    for case in cases:
        score = case.facts.get("market_score")
        for horizon in horizons:
            outcome = calculate_market_forward_outcome(
                as_of_date=case.trade_date,
                horizon=horizon,
                benchmark_rows=benchmark_rows,
                as_of=cutoff,
            )
            result.append({
                "trade_date": case.trade_date,
                "entity_id": case.entity_id,
                "horizon": horizon,
                "market_score": score,
                "market_regime": case.facts.get("market_regime"),
                "canonical_observation": case.facts.get("canonical_observation"),
                "quality_status": case.quality_status,
                "coverage": case.coverage,
                "forward_return": outcome.get("forward_return"),
                "excess_return": outcome.get("forward_return"),
                "mfe": None,
                "mae": outcome.get("max_drawdown"),
                "max_drawdown": outcome.get("max_drawdown"),
                "status": outcome.get("status"),
                "reason_codes": list(case.reason_codes) + list(outcome.get("reason_codes", [])),
                "source_ids": list(case.source_ids),
            })
    return result


def _candidate_outcome_rows(
    cases: list[ReplayCase],
    bars: list[DailyBarCache],
    benchmark_rows: list[AllAMedianIndexDaily],
    horizons: tuple[int, ...],
    calendar_dates: list[date],
    cutoff: datetime | None,
    *,
    transaction_cost_model: TransactionCostModel | None = None,
) -> list[dict[str, Any]]:
    bars_by_code: dict[str, list[DailyBarCache]] = defaultdict(list)
    for row in bars:
        bars_by_code[row.code].append(row)
    transaction_cost_model = transaction_cost_model or current_transaction_cost_model()
    result = []
    for case in cases:
        code = str(case.facts.get("code") or "")
        code_bars = bars_by_code.get(code, [])
        for horizon in horizons:
            intraday = bool(case.facts.get("intraday"))
            future_dates = _future_dates(calendar_dates, case.trade_date, max(0, horizon - 1 if intraday else horizon))
            target_dates = [case.trade_date, *future_dates] if intraday else future_dates
            outcome = calculate_forward_outcome(
                decision_date=case.trade_date,
                horizon=horizon,
                reference_price=case.facts.get("reference_price"),
                reference_price_basis=case.facts.get("reference_price_basis"),
                bars=code_bars,
                benchmark_rows=benchmark_rows,
                action="BUY" if case.facts.get("stage") == "ACTION" else "WATCH",
                execution_basis="INTRADAY_REFERENCE_QUOTE" if intraday else "NEXT_OPEN_PROXY",
                target_dates=target_dates,
                as_of=cutoff,
                transaction_cost_model=transaction_cost_model,
                intraday=intraday,
            )
            result.append({
                "trade_date": case.trade_date,
                "entity_id": case.entity_id,
                "intraday": intraday,
                "horizon": horizon,
                "code": code,
                "security_type": case.facts.get("security_type"),
                "etf_category": case.facts.get("etf_category"),
                "stage": case.facts.get("stage"),
                "score": case.facts.get("score"),
                "opportunity_score": case.facts.get("opportunity_score"),
                "entry_score": case.facts.get("entry_score"),
                "portfolio_fit_score": case.facts.get("portfolio_fit_score"),
                "action_score": case.facts.get("action_score"),
                "decision_edge": case.facts.get("decision_edge"),
                "risk_reward_ratio": case.facts.get("risk_reward_ratio"),
                "coverage": case.coverage,
                "market_regime": case.facts.get("market_regime"),
                "candidate_quality_status": case.quality_status,
                "market_available": case.facts.get("market_available", True),
                "market_quality": case.facts.get("market_quality", "VALID"),
                "market_frozen": case.facts.get("market_frozen", False),
                "funding_mode": case.facts.get("funding_mode"),
                "quote_is_proxy": case.facts.get("quote_is_proxy", False),
                "limit_up": case.facts.get("limit_up", False),
                "limit_down": case.facts.get("limit_down", False),
                "edge_vs_current_holdings": case.facts.get("edge_vs_current_holdings"),
                "held_baseline": case.facts.get("held_baseline"),
                "components": case.facts.get("components") or {},
                "reference_price_basis": case.facts.get("reference_price_basis"),
                "censored_sample": True,
                **outcome,
                "source_ids": list(case.source_ids) + [str(outcome["target_bar_id"])] if outcome.get("target_bar_id") else list(case.source_ids),
            })
    return result


def _bar_metric_rows(cases: list[ReplayCase], bars: list[DailyBarCache], calendar_dates: list[date], horizons: tuple[int, ...]) -> list[dict[str, Any]]:
    by_code: dict[str, list[DailyBarCache]] = defaultdict(list)
    for row in bars:
        by_code[row.code].append(row)
    result = []
    for case in cases:
        code = str(case.facts.get("code") or "")
        code_bars = by_code.get(code, [])
        for horizon in horizons:
            dates = _future_dates(calendar_dates, case.trade_date, horizon)
            future = [row for row in code_bars if row.trade_date in set(dates)]
            start = case.facts.get("close")
            end = future[-1].close if future else None
            value = end / start - 1.0 if start and end else None
            result.append({
                "trade_date": case.trade_date,
                "horizon": horizon,
                "code": code,
                "security_type": "UNKNOWN",
                "stage": "BAR_FACTOR",
                "score": None,
                "market_regime": None,
                "coverage": 1.0 if value is not None else 0.0,
                "raw_return": value,
                "excess_return": None,
                "mfe": None,
                "mae": None,
                "directional_return": value,
                "status": "DIAGNOSTIC_ONLY" if value is not None else "BLOCKED",
                "reason_codes": ["BAR_ONLY_DIAGNOSTIC", "BENCHMARK_UNAVAILABLE"],
                "source_ids": list(case.source_ids),
            })
    return result


def _load_replay_rows(
    db: Session,
    *,
    scope: str,
    replay_mode: str,
    start_date: date,
    end_date: date,
    horizons: Iterable[int],
    user_id: int | None,
    portfolio_id: int | None,
    as_of: datetime | None,
    decision_feature_cutoff: datetime | date | None = None,
    outcome_evaluation_cutoff: datetime | date | None = None,
    transaction_cost_model: TransactionCostModel | None = None,
) -> tuple[dict[str, list[Any]], list[dict[str, Any]], list[str]]:
    """Load one complete, bulk-fetched replay input set and its outcome rows."""

    horizon_values = tuple(horizons)
    facts = load_replay_facts(
        db,
        scope=scope,
        replay_mode=replay_mode,
        start_date=start_date,
        end_date=end_date,
        as_of=as_of,
        decision_feature_cutoff=decision_feature_cutoff,
        outcome_evaluation_cutoff=outcome_evaluation_cutoff,
        user_id=user_id,
        portfolio_id=portfolio_id,
    )
    calendar_dates = _calendar_dates(
        db,
        start_date,
        end_date + timedelta(days=max(370, max(horizon_values) * 3)),
    )
    cutoff = (
        as_of.replace(tzinfo=None)
        if as_of and as_of.tzinfo is None
        else as_of.astimezone(UTC).replace(tzinfo=None)
        if as_of
        else None
    )
    source_facts: dict[str, Iterable[Any]] = dict(facts)
    if scope == "MARKET":
        cases = replay_market_cases(facts, replay_mode=replay_mode)
        benchmark_rows = _load_future_benchmark(
            db, start_date=start_date, end_date=end_date, horizon=max(horizon_values)
        )
        rows = _market_outcome_rows(cases, benchmark_rows, horizon_values, calendar_dates, cutoff)
        source_facts["future_benchmarks"] = benchmark_rows
    elif scope == "CANDIDATE":
        cases = replay_candidate_cases(facts, replay_mode=replay_mode)
        codes = [case.facts.get("code") for case in cases]
        bars = _load_future_bars(
            db, codes=codes, start_date=start_date, end_date=end_date, horizon=max(horizon_values)
        )
        benchmark_rows = _load_future_benchmark(
            db, start_date=start_date, end_date=end_date, horizon=max(horizon_values)
        )
        rows = _candidate_outcome_rows(
            cases,
            bars,
            benchmark_rows,
            horizon_values,
            calendar_dates,
            cutoff,
            transaction_cost_model=transaction_cost_model,
        )
        source_facts["future_bars"] = bars
        source_facts["future_benchmarks"] = benchmark_rows
    elif scope == "MEMORY_DECISION":
        rows = _memory_rows(replay_memory_cases(facts, replay_mode=replay_mode))
    elif scope == "BAR_FACTOR":
        cases = replay_bar_cases(facts, replay_mode=replay_mode)
        codes = [case.facts.get("code") for case in cases]
        bars = _load_future_bars(
            db, codes=codes, start_date=start_date, end_date=end_date, horizon=max(horizon_values)
        )
        rows = _bar_metric_rows(cases, bars, calendar_dates, horizon_values)
        source_facts["future_bars"] = bars
    else:
        snapshots = facts.get("portfolio_snapshots", [])
        rows = [{
            "trade_date": item.snapshot_time.date(),
            "portfolio_snapshot_id": item.id,
            "total_assets": item.total_assets,
            "market_value": item.total_market_value,
            "status": item.status,
            "excess_return": None,
            "coverage": 1.0,
            "source_ids": [f"portfolio_snapshots:id:{item.id}"],
        } for item in snapshots]

    source_ids = set(_source_ids(source_facts))
    for row in rows:
        source_ids.update(str(item) for item in row.get("source_ids", []))
    return facts, rows, sorted(source_ids)


def load_backtest_rows_with_sources(
    db: Session,
    *,
    run: BacktestRun,
    as_of: datetime | None = None,
    decision_feature_cutoff: datetime | date | None = None,
    outcome_evaluation_cutoff: datetime | date | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rebuild server-owned evaluation rows plus their current source set."""

    horizons = validate_horizons(list(run.horizons_json or DEFAULT_HORIZONS), market=run.scope == "MARKET")
    _, rows, source_ids = _load_replay_rows(
        db,
        scope=run.scope,
        replay_mode=run.replay_mode,
        start_date=run.start_date,
        end_date=run.end_date,
        horizons=horizons,
        user_id=run.user_id,
        portfolio_id=run.portfolio_id,
        as_of=as_of,
        decision_feature_cutoff=decision_feature_cutoff,
        outcome_evaluation_cutoff=outcome_evaluation_cutoff,
        transaction_cost_model=_run_transaction_cost_model(run),
    )
    return rows, source_ids


def load_backtest_rows(
    db: Session,
    *,
    run: BacktestRun,
    as_of: datetime | None = None,
    decision_feature_cutoff: datetime | date | None = None,
    outcome_evaluation_cutoff: datetime | date | None = None,
) -> list[dict[str, Any]]:
    """Rebuild server-owned evaluation rows without accepting client facts."""

    rows, _ = load_backtest_rows_with_sources(
        db,
        run=run,
        as_of=as_of,
        decision_feature_cutoff=decision_feature_cutoff,
        outcome_evaluation_cutoff=outcome_evaluation_cutoff,
    )
    return rows


def _memory_rows(cases: list[ReplayCase]) -> list[dict[str, Any]]:
    result = []
    for case in cases:
        result.append({"trade_date": case.trade_date, **case.facts, "source_ids": list(case.source_ids)})
    return result


def _slice_payloads(scope: str, rows: list[dict[str, Any]], *, horizons: tuple[int, ...], bootstrap_iterations: int, seed: int) -> list[dict[str, Any]]:
    if scope == "MARKET":
        return market_metric_slices(rows, horizons=horizons, bootstrap_iterations=bootstrap_iterations, seed=seed)
    if scope == "CANDIDATE":
        return candidate_metric_slices(rows, bootstrap_iterations=bootstrap_iterations, seed=seed)
    if scope == "MEMORY_DECISION":
        grouped = []
        for dimensions in (("recommended_action", "market_regime", "horizon"), ("execution_alignment", "horizon")):
            grouped.extend({"metric_family": "MEMORY_OUTCOME", **item} for item in _aggregate(rows, dimensions, bootstrap_iterations, seed))
        return grouped
    if scope == "BAR_FACTOR":
        return [{"metric_family": "BAR_FACTOR_DIAGNOSTIC", **item} for item in _aggregate(rows, ("horizon", "security_type"), bootstrap_iterations, seed)]
    return [{"metric_family": "PORTFOLIO_SNAPSHOT_DIAGNOSTIC", "metrics": summarise_values(rows, value_key="excess_return", bootstrap_iterations=bootstrap_iterations, seed=seed)}]


def _aggregate(rows: list[dict[str, Any]], dimensions: tuple[str, ...], bootstrap_iterations: int, seed: int) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(key) for key in dimensions)].append(row)
    result = []
    for key, group in sorted(groups.items(), key=lambda item: tuple(str(value) for value in item[0])):
        result.append({
            **{name: value for name, value in zip(dimensions, key)},
            "sample_count": len(group),
            "trade_date_count": len({str(row.get("trade_date"))[:10] for row in group if row.get("trade_date")}),
            "metrics": summarise_values(group, bootstrap_iterations=bootstrap_iterations, seed=seed),
        })
    return result


def _persist_slices(db: Session, run: BacktestRun, slices: list[dict[str, Any]], *, limitations: list[str]) -> None:
    for index, payload in enumerate(slices):
        key = content_hash({"run_id": run.id, "index": index, "payload": _json(payload)})
        row = db.execute(select(BacktestMetricSlice).where(
            BacktestMetricSlice.run_id == run.id,
            BacktestMetricSlice.slice_key == key,
        )).scalar_one_or_none()
        if row is None:
            row = BacktestMetricSlice(
                run_id=run.id,
                slice_key=key,
                metric_family=str(payload.get("metric_family") or "UNKNOWN"),
                security_type=payload.get("security_type"),
                market_regime=payload.get("market_regime"),
                stage=payload.get("stage"),
                score_bucket=payload.get("score_bucket"),
                horizon=payload.get("horizon"),
                parameter_variant=str(payload.get("parameter_variant")) if payload.get("parameter_variant") is not None else None,
                sample_count=int(payload.get("sample_count") or payload.get("metrics", {}).get("sample_count") or 0),
                trade_date_count=int(payload.get("trade_date_count") or payload.get("metrics", {}).get("unique_trade_dates") or 0),
                coverage=payload.get("metrics", {}).get("coverage"),
                metrics_json=_json(payload.get("metrics", {})),
                confidence_interval_json=_json(payload.get("metrics", {}).get("confidence_interval", {})),
                quality_status="VALID" if int(payload.get("metrics", {}).get("sample_count") or 0) >= 20 else "INSUFFICIENT",
                limitations_json=limitations,
            )
            db.add(row)


def _serialize(row: BacktestRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "portfolio_id": row.portfolio_id,
        "scope": row.scope,
        "replay_mode": row.replay_mode,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
        "status": row.status,
        "progress_percent": row.progress_percent,
        "current_stage": row.current_stage,
        "config_version": row.config_version,
        "engine_version": row.engine_version,
        "data_hash": row.data_hash,
        "data_manifest": row.data_manifest_json,
        "sample_count": row.sample_count,
        "unique_trade_dates": row.unique_trade_dates,
        "quality_status": row.quality_status,
        "leakage_status": row.leakage_status,
        "result_summary": row.result_summary_json,
        "failure_counts": row.failure_counts_json,
        "horizons": row.horizons_json,
        "known_limitations": row.known_limitations_json,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
        "last_heartbeat_at": row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None,
        "attempt_count": row.attempt_count,
        "cancel_requested": row.cancel_requested,
        "metric_slices": [
            {
                "id": item.id,
                "metric_family": item.metric_family,
                "security_type": item.security_type,
                "market_regime": item.market_regime,
                "stage": item.stage,
                "score_bucket": item.score_bucket,
                "horizon": item.horizon,
                "parameter_variant": item.parameter_variant,
                "sample_count": item.sample_count,
                "trade_date_count": item.trade_date_count,
                "coverage": item.coverage,
                "metrics": item.metrics_json,
                "confidence_interval": item.confidence_interval_json,
                "quality_status": item.quality_status,
                "limitations": item.limitations_json,
            }
            for item in row.metric_slices
        ],
    }


def serialize_backtest_run(row: BacktestRun) -> dict[str, Any]:
    return _serialize(row)


def execute_backtest_run(
    db: Session,
    *,
    run: BacktestRun,
    as_of: datetime | None = None,
    generation: int | None = None,
    stop_event: threading.Event | None = None,
) -> BacktestRun:
    if run.status in {"COMPLETED", "FAILED", "CANCELLED", "INSUFFICIENT_DATA", "INVALIDATED"}:
        return run
    if run.cancel_requested:
        if generation is None:
            _mark_cancelled(db, run)
        return run
    _write_run_fields(db, run, values={
        "status": "RUNNING",
        "started_at": run.started_at or _now(),
        "current_stage": "DATA_AUDIT",
        "lease_expires_at": _lease(),
        "last_heartbeat_at": _now(),
    }, generation=generation)
    db.commit()
    try:
        horizons = validate_horizons(list(run.horizons_json or DEFAULT_HORIZONS), market=run.scope == "MARKET")
        experiment = run.experiment_config_json or {}
        bootstrap_iterations = int(experiment.get("bootstrap_iterations") or 500)
        with historical_replay_network_policy():
            _set_stage(db, run, "LOADING", 15, generation=generation)
            db.commit()
            if _stop_if_cancelled(db, run, generation=generation) or _worker_lost_lease(stop_event):
                db.rollback()
                return run
            facts, rows, source_ids = _load_replay_rows(
                db,
                scope=run.scope,
                replay_mode=run.replay_mode,
                start_date=run.start_date,
                end_date=run.end_date,
                horizons=horizons,
                user_id=run.user_id,
                portfolio_id=run.portfolio_id,
                as_of=as_of,
                transaction_cost_model=_run_transaction_cost_model(run),
            )
            frozen_source_ids = (run.data_manifest_json or {}).get("frozen_source_ids")
            if frozen_source_ids is not None and sorted(frozen_source_ids) != source_ids:
                return _invalidate_for_source_change(db, run, source_ids, generation=generation)
            data_hash = _data_hash(
                manifest=run.data_manifest_json or {},
                source_ids=source_ids,
                start_date=run.start_date,
                end_date=run.end_date,
                config=run.baseline_config_json or current_production_config(),
                scope=run.scope,
                replay_mode=run.replay_mode,
            )
            _set_stage(db, run, "REPLAY", 35, generation=generation, extra_values={"data_hash": data_hash})
            db.commit()
            if _stop_if_cancelled(db, run, generation=generation) or _worker_lost_lease(stop_event):
                db.rollback()
                return run
            _set_stage(db, run, "OUTCOME", 60, generation=generation)
            db.commit()
            if _stop_if_cancelled(db, run, generation=generation) or _worker_lost_lease(stop_event):
                db.rollback()
                return run
            slices = _slice_payloads(run.scope, rows, horizons=horizons, bootstrap_iterations=bootstrap_iterations, seed=run.random_seed)
            _set_stage(db, run, "METRICS", 78, generation=generation)
            db.commit()
            if _stop_if_cancelled(db, run, generation=generation) or _worker_lost_lease(stop_event):
                db.rollback()
                return run
            limitations = list(run.known_limitations_json or [])
            if run.replay_mode == "BAR_ONLY_DIAGNOSTIC" or run.scope == "BAR_FACTOR":
                limitations.append("BAR_ONLY_DIAGNOSTIC: this is not a full Candidate Engine backtest.")
            if run.scope == "CANDIDATE":
                limitations.append("CENSORED_PRODUCTION_SAMPLE: persisted candidates are a top-subset, not the full universe.")
            if run.replay_mode == "DETERMINISTIC_RECOMPUTE":
                limitations.append("DETERMINISTIC_RECOMPUTE was blocked unless an explicit PIT preparation set exists.")
            _persist_slices(db, run, slices, limitations=sorted(set(limitations)))
            valid_rows = [row for row in rows if row.get("excess_return") is not None or row.get("forward_return") is not None]
            dates = {str(row.get("trade_date"))[:10] for row in rows if row.get("trade_date") is not None}
            failure_counts: dict[str, int] = defaultdict(int)
            for row in rows:
                for reason in row.get("reason_codes", []):
                    failure_counts[str(reason)] += 1
            if not rows:
                quality_status = "INSUFFICIENT_DATA"
                run_status = "INSUFFICIENT_DATA"
            elif run.replay_mode == "BAR_ONLY_DIAGNOSTIC" or run.scope == "BAR_FACTOR":
                quality_status = "DIAGNOSTIC_ONLY"
                run_status = "COMPLETED"
            else:
                quality_status = "VALID" if valid_rows else "DEGRADED"
                run_status = "COMPLETED"
            final_values = {
                "status": run_status,
                "progress_percent": 100,
                "current_stage": "FINALIZING",
                "sample_count": len(valid_rows),
                "unique_trade_dates": len(dates),
                "quality_status": quality_status,
                "leakage_status": "PASS" if run.replay_mode == "PRODUCTION_REPLAY" else "LEAKAGE_BLOCKED",
                "failure_counts_json": dict(failure_counts),
                "known_limitations_json": sorted(set(limitations)),
                "result_summary_json": {
                    "metric_slice_count": len(slices),
                    "case_count": len(rows),
                    "valid_case_count": len(valid_rows),
                    "sample_count": len(valid_rows),
                    "unique_trade_dates": len(dates),
                    "source_lineage": {
                        "source_ids": source_ids,
                        "source_set_hash": content_hash(source_ids),
                    },
                    "replay_mode": run.replay_mode,
                    "scope": run.scope,
                    "execution_basis": run.data_manifest_json.get("execution_model") if isinstance(run.data_manifest_json, dict) else None,
                    "transaction_cost_assumption": _run_transaction_cost_model(run).as_dict(),
                    "no_production_write": True,
                },
                "completed_at": _now(),
                "lease_expires_at": None,
                "last_heartbeat_at": _now(),
            }
            _write_run_fields(db, run, values=final_values, generation=generation)
            db.commit()
            return run
    except LeaseLostError:
        db.rollback()
        return run
    except ReplayDataQualityError as exc:
        db.rollback()
        failure_values = {
            "status": "INSUFFICIENT_DATA",
            "quality_status": "LEAKAGE_BLOCKED" if "RECOMPUTE" in str(exc) or "PIT" in str(exc) else "INSUFFICIENT_DATA",
            "leakage_status": "LEAKAGE_BLOCKED",
            "error_code": str(exc),
            "error_message": str(exc),
            "known_limitations_json": sorted(set((run.known_limitations_json or []) + [str(exc)])),
            "completed_at": _now(),
            "lease_expires_at": None,
            "last_heartbeat_at": _now(),
        }
        if not _write_final_state(db, run, values=failure_values, generation=generation):
            return run
        return run
    except Exception as exc:
        db.rollback()
        failure_values = {
            "status": "FAILED",
            "quality_status": "FAILED",
            "error_code": type(exc).__name__,
            "error_message": str(exc),
            "completed_at": _now(),
            "lease_expires_at": None,
            "last_heartbeat_at": _now(),
        }
        if not _write_final_state(db, run, values=failure_values, generation=generation):
            return run
        raise


def enqueue_backtest_run(
    db: Session,
    *,
    scope: str,
    replay_mode: str,
    start_date: date,
    end_date: date,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    horizons: Iterable[int] | None = None,
    experiment_config: dict[str, Any] | None = None,
    random_seed: int = 0,
    bootstrap_iterations: int = 500,
) -> BacktestRun:
    """Create a durable queued run for the server-owned research worker."""

    run = create_backtest_run(
        db,
        scope=scope,
        replay_mode=replay_mode,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        portfolio_id=portfolio_id,
        horizons=horizons,
        experiment_config=experiment_config,
        random_seed=random_seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    db.commit()
    return run


def claim_queued_backtest_run(db: Session, *, run_id: int) -> BacktestRun | None:
    """CAS-claim one queued run so two scheduler ticks cannot double-dispatch it."""

    now = _now()
    claimed = db.execute(
        update(BacktestRun)
        .where(
            BacktestRun.id == run_id,
            BacktestRun.status == "QUEUED",
            BacktestRun.cancel_requested.is_(False),
        )
        .values(
            status="RUNNING",
            current_stage="DATA_AUDIT",
            started_at=now,
            last_heartbeat_at=now,
            lease_expires_at=now + timedelta(minutes=LEASE_MINUTES),
        )
    ).rowcount
    if not claimed:
        return None
    db.commit()
    return db.get(BacktestRun, run_id)


def _server_backtest_heartbeat(
    run_id: int,
    generation: int,
    stop_event: threading.Event,
) -> None:
    """Renew only the current attempt generation's worker lease."""

    from ..database import SessionLocal

    while True:
        heartbeat_db = SessionLocal()
        row = None
        try:
            row = heartbeat_backtest_run(heartbeat_db, run_id=run_id, generation=generation)
            if row is not None:
                heartbeat_db.commit()
                heartbeat_db.refresh(row)
            else:
                heartbeat_db.rollback()
        except Exception:
            heartbeat_db.rollback()
            row = None
        finally:
            heartbeat_db.close()
        if (
            row is None
            or row.status != "RUNNING"
            or int(row.attempt_count or 0) != generation
        ):
            stop_event.set()
            return
        if stop_event.wait(WORKER_HEARTBEAT_SECONDS):
            return


def run_backtest_worker(run_id: int) -> None:
    """Execute one already claimed run in a server-owned session."""

    from ..database import SessionLocal
    from ..system.logging import bind_worker_context
    from ..system.workers import register_worker, unregister_worker

    db = SessionLocal()
    stop_event = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    register_worker("backtest", run_id, stop_event)
    bind_worker_context(backtest_run_id=run_id)
    try:
        run = db.get(BacktestRun, run_id)
        if run is None:
            return
        if run.status == "QUEUED":
            run = claim_queued_backtest_run(db, run_id=run_id)
        if run is None or run.status != "RUNNING":
            return
        generation = int(run.attempt_count or 1)
        bind_worker_context(
            backtest_run_id=run_id,
            parameter_set_version=run.parameter_set_version,
        )
        verified = heartbeat_backtest_run(db, run_id=run_id, generation=generation)
        if (
            verified is None
            or verified.status != "RUNNING"
            or int(verified.attempt_count or 0) != generation
        ):
            return
        db.commit()
        heartbeat_thread = threading.Thread(
            target=_server_backtest_heartbeat,
            args=(run_id, generation, stop_event),
            name=f"backtest-heartbeat-{run_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        execute_backtest_run(db, run=run, generation=generation, stop_event=stop_event)
    except Exception:
        # execute_backtest_run persists FAILED before re-raising.  A worker
        # thread must not take down the scheduler that owns it.
        return
    finally:
        stop_event.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
        unregister_worker("backtest", run_id)
        db.close()


def dispatch_queued_backtest_runs(
    db: Session,
    *,
    limit: int = MAX_BACKTEST_WORKERS_PER_TICK,
    start_workers: bool = True,
) -> list[int]:
    """Reclaim stale work and dispatch each run through a CAS claim."""

    if limit <= 0:
        return []
    reclaimed = reclaim_stale_backtest_runs(db)
    if reclaimed:
        db.commit()
    queued_ids = list(db.execute(
        select(BacktestRun.id)
        .where(BacktestRun.status == "QUEUED", BacktestRun.cancel_requested.is_(False))
        .order_by(BacktestRun.id.asc())
        .limit(limit)
    ).scalars())
    dispatched: list[int] = []
    for run_id in queued_ids:
        if claim_queued_backtest_run(db, run_id=run_id) is None:
            continue
        dispatched.append(int(run_id))
        if start_workers:
            threading.Thread(
                target=run_backtest_worker,
                args=(int(run_id),),
                name=f"backtest-worker-{run_id}",
                daemon=True,
            ).start()
    return dispatched


def run_backtest(
    db: Session,
    *,
    scope: str,
    replay_mode: str,
    start_date: date,
    end_date: date,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    horizons: Iterable[int] | None = None,
    experiment_config: dict[str, Any] | None = None,
    random_seed: int = 0,
    bootstrap_iterations: int = 500,
) -> BacktestRun:
    """Synchronous compatibility helper for direct research callers/tests.

    HTTP requests use :func:`enqueue_backtest_run` and the server worker;
    keeping this explicit helper avoids changing the established Python API.
    """

    run = enqueue_backtest_run(
        db,
        scope=scope,
        replay_mode=replay_mode,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        portfolio_id=portfolio_id,
        horizons=horizons,
        experiment_config=experiment_config,
        random_seed=random_seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    if run.status == "QUEUED":
        execute_backtest_run(db, run=run)
    return run


def get_backtest_run(db: Session, *, run_id: int, user_id: int | None = None) -> BacktestRun | None:
    row = db.get(BacktestRun, run_id)
    if row is None or (user_id is not None and row.user_id != user_id):
        return None
    return row


def list_backtest_runs(db: Session, *, user_id: int | None = None, portfolio_id: int | None = None, limit: int = 50) -> list[BacktestRun]:
    statement = select(BacktestRun)
    if user_id is not None:
        statement = statement.where(BacktestRun.user_id == user_id)
    if portfolio_id is not None:
        statement = statement.where(BacktestRun.portfolio_id == portfolio_id)
    return list(db.execute(statement.order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()).limit(limit)).scalars())


__all__ = [
    "create_backtest_run",
    "enqueue_backtest_run",
    "claim_queued_backtest_run",
    "run_backtest_worker",
    "dispatch_queued_backtest_runs",
    "execute_backtest_run",
    "run_backtest",
    "load_backtest_rows",
    "load_backtest_rows_with_sources",
    "get_backtest_run",
    "list_backtest_runs",
    "cancel_backtest_run",
    "heartbeat_backtest_run",
    "reclaim_stale_backtest_runs",
    "serialize_backtest_run",
]
