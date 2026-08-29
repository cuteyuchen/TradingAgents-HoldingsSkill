"""Historical data capability audit and replay availability manifest.

The manifest is intentionally conservative.  A persisted row is not enough to
claim point-in-time correctness: the report also records which timestamp and
lineage fields made the row usable, and where current-only facts block replay.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..candidates.models import CandidateRun, CandidateScore
from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache, MarketMetricSnapshot, MarketScoreSnapshot
from ..market_models import SecurityMaster, TradingCalendar
from ..market_runtime_models import MarketSnapshot, SourceLineage
from ..memory.models import DecisionMemory, DecisionOutcome
from ..portfolio_models import PortfolioRiskSnapshot, TradeLedgerEntry
from ..system.tables import table_exists as _session_table_exists
from ..v2_models import PortfolioSnapshot
from .config import REPLAY_AVAILABILITY_STATUSES


def _history_manifest_items(db, *, start_date, end_date, market):
    try:
        from ..history.availability import history_manifest_items as build_items
    except Exception:  # noqa: BLE001
        return None
    try:
        return build_items(
            db,
            start_date=start_date,
            end_date=end_date,
            market=market,
        )
    except Exception:  # noqa: BLE001
        return None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _table_exists(db: Session, model: type) -> bool:
    return _session_table_exists(db, model.__tablename__)


def _date_filter(column, start_date: date | None, end_date: date | None):
    clauses = []
    if start_date is not None:
        clauses.append(column >= start_date)
    if end_date is not None:
        clauses.append(column <= end_date)
    return clauses


def _timestamp_filter(column, start_date: date | None, end_date: date | None):
    """Bound timestamp-only tables to the requested local calendar window."""

    clauses = []
    if start_date is not None:
        clauses.append(column >= datetime.combine(start_date, time.min))
    if end_date is not None:
        clauses.append(column < datetime.combine(end_date + timedelta(days=1), time.min))
    return clauses


def _date_window(start_date: date | None, end_date: date | None) -> tuple[date | None, date | None]:
    if start_date is None or end_date is None:
        return start_date, end_date
    if start_date > end_date:
        raise ValueError("start_date_must_not_exceed_end_date")
    return start_date, end_date


def _count_dates(db: Session, model: type, date_column, *, start_date: date | None, end_date: date | None, extra=()) -> int:
    if not _table_exists(db, model):
        return 0
    try:
        statement = select(func.count(func.distinct(date_column))).select_from(model).where(*extra)
        statement = statement.where(*_date_filter(date_column, start_date, end_date))
        return int(db.execute(statement).scalar() or 0)
    except SQLAlchemyError:
        return 0


def _open_calendar_dates(db: Session, *, start_date: date | None, end_date: date | None, market: str) -> int:
    if not _table_exists(db, TradingCalendar):
        return 0
    try:
        statement = select(func.count(TradingCalendar.id)).where(
            TradingCalendar.market == market,
            TradingCalendar.is_open.is_(True),
            *_date_filter(TradingCalendar.trade_date, start_date, end_date),
        )
        return int(db.execute(statement).scalar() or 0)
    except SQLAlchemyError:
        return 0


def _summary(
    db: Session,
    *,
    name: str,
    model: type,
    date_column=None,
    timestamp_column=None,
    start_date: date | None,
    end_date: date | None,
    market: str,
    status: str,
    reason: str | None = None,
    capabilities: dict[str, str] | None = None,
    extra=(),
    source_fields: list[str] | None = None,
    availability_field: str | None = None,
    lineage_field: str | None = None,
) -> dict[str, Any]:
    row_count = 0
    earliest_date = latest_date = None
    earliest_timestamp = latest_timestamp = None
    distinct_dates = 0
    table_exists = _table_exists(db, model)
    if table_exists:
        try:
            statement = select(func.count(model.id))
            if date_column is not None:
                statement = statement.add_columns(func.min(date_column), func.max(date_column))
            if timestamp_column is not None:
                statement = statement.add_columns(func.min(timestamp_column), func.max(timestamp_column))
            statement = statement.select_from(model).where(*extra)
            if date_column is not None:
                statement = statement.where(*_date_filter(date_column, start_date, end_date))
            elif timestamp_column is not None:
                statement = statement.where(*_timestamp_filter(timestamp_column, start_date, end_date))
            result = db.execute(statement).one()
            row_count = int(result[0] or 0)
            offset = 1
            if date_column is not None:
                earliest_date, latest_date = result[offset], result[offset + 1]
                offset += 2
                distinct_dates = _count_dates(
                    db, model, date_column, start_date=start_date, end_date=end_date, extra=extra
                )
            if timestamp_column is not None:
                earliest_timestamp, latest_timestamp = result[offset], result[offset + 1]
        except SQLAlchemyError:
            row_count = 0

    open_dates = _open_calendar_dates(db, start_date=start_date, end_date=end_date, market=market)
    coverage = None
    if open_dates > 0:
        coverage = round(min(1.0, distinct_dates / open_dates), 6)
    elif row_count > 0:
        coverage = 1.0

    item: dict[str, Any] = {
        "name": name,
        "status": status if status in REPLAY_AVAILABILITY_STATUSES else "PARTIAL",
        "row_count": row_count,
        "distinct_trade_dates": distinct_dates,
        "earliest_supported_at": _iso(earliest_timestamp or earliest_date),
        "latest_supported_at": _iso(latest_timestamp or latest_date),
        "coverage": coverage,
        "market": market,
        "source_fields": source_fields or [],
        "availability_field": availability_field,
        "lineage_field": lineage_field,
        "capabilities": capabilities or {},
    }
    if reason:
        item["reason"] = reason
    if start_date is not None or end_date is not None:
        item["requested_range"] = {"start_date": _iso(start_date), "end_date": _iso(end_date)}
    if not table_exists:
        item["status"] = "DATA_GAP"
        item["reason"] = reason or "table_unavailable"
    elif row_count == 0 and item["status"] in {"FULL", "PARTIAL", "DIAGNOSTIC_ONLY"}:
        item["status"] = "DATA_GAP"
        item["reason"] = item.get("reason") or "no_rows_in_requested_range"
    if coverage is not None and coverage < 1.0 and item["status"] == "FULL":
        item["status"] = "PARTIAL"
        item["reason"] = item.get("reason") or "historical_trade_date_coverage_is_incomplete"
    return item


def _stable_source_hash(manifest: dict[str, Any]) -> str:
    source = {
        key: {
            "status": value.get("status"),
            "row_count": value.get("row_count"),
            "earliest_supported_at": value.get("earliest_supported_at"),
            "latest_supported_at": value.get("latest_supported_at"),
            "coverage": value.get("coverage"),
        }
        for key, value in sorted(manifest.items())
        if isinstance(value, dict) and "status" in value
    }
    return hashlib.sha256(json.dumps(source, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_replay_availability_manifest(
    db: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    market: str = "CN",
    portfolio_id: int | None = None,
    user_id: int | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Audit all Phase I inputs without fetching or mutating external data."""

    start_date, end_date = _date_window(start_date, end_date)
    market = str(market or "CN").upper()
    generated = generated_at or datetime.now(UTC)
    manifest: dict[str, Any] = {
        "manifest_version": "replay-availability-v1",
        "generated_at": generated.isoformat(),
        "requested_range": {"start_date": _iso(start_date), "end_date": _iso(end_date)},
        "market": market,
    }

    manifest["trading_calendar"] = _summary(
        db, name="TradingCalendar", model=TradingCalendar, date_column=TradingCalendar.trade_date,
        start_date=start_date, end_date=end_date, market=market, status="FULL" if _table_exists(db, TradingCalendar) else "DATA_GAP",
        reason=None if _table_exists(db, TradingCalendar) else "trading_calendar_table_unavailable",
        capabilities={"trading_day_horizon": "FULL"}, source_fields=["trade_date", "is_open", "previous_trade_date", "next_trade_date"],
    )
    manifest["market_score"] = _summary(
        db, name="MarketScoreSnapshot", model=MarketScoreSnapshot, date_column=MarketScoreSnapshot.trade_date,
        timestamp_column=MarketScoreSnapshot.captured_at, start_date=start_date, end_date=end_date, market=market,
        status="FULL", reason="production_snapshots_are_replayable_facts; deterministic_recompute_requires_source_PIT_inputs",
        capabilities={"PRODUCTION_REPLAY": "FULL", "DETERMINISTIC_RECOMPUTE": "PARTIAL", "BAR_ONLY_DIAGNOSTIC": "UNSUPPORTED"},
        extra=(MarketScoreSnapshot.market == market,), source_fields=["trade_date", "captured_at", "snapshot_id", "calculation_version", "score_config_version"],
        availability_field="captured_at", lineage_field="market_snapshot_id",
    )
    manifest["market_metrics"] = _summary(
        db, name="MarketMetricSnapshot", model=MarketMetricSnapshot, date_column=MarketMetricSnapshot.trade_date,
        timestamp_column=MarketMetricSnapshot.captured_at, start_date=start_date, end_date=end_date, market=market,
        status="FULL", reason="production_metric_snapshots_are_replayable_facts",
        capabilities={"PRODUCTION_REPLAY": "FULL", "DETERMINISTIC_RECOMPUTE": "PARTIAL"},
        extra=(MarketMetricSnapshot.market == market,), source_fields=["trade_date", "captured_at", "snapshot_id", "calculation_version"],
        availability_field="captured_at", lineage_field="market_snapshot_id",
    )
    manifest["market_snapshots"] = _summary(
        db, name="MarketSnapshot", model=MarketSnapshot, date_column=MarketSnapshot.trade_date,
        timestamp_column=MarketSnapshot.completed_at, start_date=start_date, end_date=end_date, market=market,
        status="PARTIAL", reason="snapshot_metadata_exists_but_quote_payload_is_not_persisted",
        capabilities={"PRODUCTION_REPLAY": "PARTIAL", "DETERMINISTIC_RECOMPUTE": "UNSUPPORTED"},
        extra=(MarketSnapshot.market == market,), source_fields=["trade_date", "started_at", "completed_at", "snapshot_id", "provider"],
        availability_field="completed_at", lineage_field="provider",
    )
    manifest["all_a_median_index"] = _summary(
        db, name="AllAMedianIndexDaily", model=AllAMedianIndexDaily, date_column=AllAMedianIndexDaily.trade_date,
        timestamp_column=AllAMedianIndexDaily.available_at, start_date=start_date, end_date=end_date, market=market,
        status="FULL", reason="persisted_benchmark_is_used_as_a_fact; current_constituent_recompute_is_forbidden",
        capabilities={"PRODUCTION_REPLAY": "FULL", "DETERMINISTIC_RECOMPUTE": "PARTIAL"},
        extra=(AllAMedianIndexDaily.market == market,), source_fields=["trade_date", "available_at", "index_value", "calculation_version"],
        availability_field="available_at", lineage_field="calculation_version",
    )
    daily_bar_status = "DIAGNOSTIC_ONLY"
    daily_bar_reason = "available_at_semantics_are_ingestion_or_local_persistence_time; use only explicitly labelled bar-only diagnostics"
    manifest["daily_bars"] = _summary(
        db, name="DailyBarCache", model=DailyBarCache, date_column=DailyBarCache.trade_date,
        timestamp_column=DailyBarCache.available_at, start_date=start_date, end_date=end_date, market=market,
        status=daily_bar_status, reason=daily_bar_reason,
        capabilities={"BAR_ONLY_DIAGNOSTIC": "FULL", "PRODUCTION_REPLAY": "PARTIAL", "DETERMINISTIC_RECOMPUTE": "PARTIAL"},
        extra=(DailyBarCache.market == market,), source_fields=["trade_date", "available_at", "fetched_at", "provider", "adjustment", "quality_status"],
        availability_field="available_at", lineage_field="provider",
    )
    manifest["daily_bar"] = manifest["daily_bars"]
    manifest["security_master"] = _summary(
        db, name="SecurityMaster", model=SecurityMaster, date_column=None, timestamp_column=SecurityMaster.source_updated_at,
        start_date=None, end_date=None, market=market, status="LEAKAGE_BLOCKED",
        reason="current_security_master_has_no_effective_from/to_lifecycle_history",
        capabilities={"listing_age": "PARTIAL", "historical_universe": "LEAKAGE_BLOCKED"},
        extra=(SecurityMaster.market == market,), source_fields=["listing_date", "delisting_date", "status", "is_st", "is_suspended", "source_updated_at"],
        availability_field="source_updated_at", lineage_field="source",
    )
    manifest["security_lifecycle"] = manifest["security_master"]

    candidate_extra = [CandidateRun.trade_date >= start_date] if start_date else []
    if end_date:
        candidate_extra.append(CandidateRun.trade_date <= end_date)
    if portfolio_id is not None:
        candidate_extra.append(CandidateRun.portfolio_id == portfolio_id)
    if user_id is not None:
        candidate_extra.append(CandidateRun.user_id == user_id)
    manifest["candidate_runs"] = _summary(
        db, name="CandidateRun", model=CandidateRun, date_column=CandidateRun.trade_date,
        timestamp_column=CandidateRun.captured_at, start_date=start_date, end_date=end_date, market=market,
        status="PARTIAL", reason="persisted_candidate_scores contain only Watchlist/Ready/Action top subset",
        capabilities={"PRODUCTION_REPLAY": "PARTIAL", "full_universe_factor_calibration": "CENSORED_PRODUCTION_SAMPLE", "DETERMINISTIC_RECOMPUTE": "PARTIAL"},
        extra=tuple(candidate_extra), source_fields=["trade_date", "as_of", "captured_at", "calculation_key", "quote_snapshot_id", "calculation_version"],
        availability_field="captured_at", lineage_field="quote_snapshot_id",
    )
    score_scope = []
    if start_date is not None:
        score_scope.append(CandidateRun.trade_date >= start_date)
    if end_date is not None:
        score_scope.append(CandidateRun.trade_date <= end_date)
    if portfolio_id is not None:
        score_scope.append(CandidateRun.portfolio_id == portfolio_id)
    if user_id is not None:
        score_scope.append(CandidateRun.user_id == user_id)
    score_extra = []
    if score_scope:
        score_extra.append(CandidateScore.candidate_run_id.in_(select(CandidateRun.id).where(*score_scope)))
    manifest["candidate_scores"] = _summary(
        db, name="CandidateScore", model=CandidateScore, date_column=None, timestamp_column=CandidateScore.created_at,
        start_date=None, end_date=None, market=market, status="PARTIAL",
        reason="only persisted top subset is available; reject rows are not present",
        capabilities={"READY_ACTION_EVALUATION": "FULL", "FULL_UNIVERSE_WEIGHT_CALIBRATION": "CENSORED_PRODUCTION_SAMPLE"},
        source_fields=["stage", "score", "opportunity_score", "entry_score", "portfolio_fit_score", "decision_edge", "lineage_json"],
        availability_field="created_at", lineage_field="lineage_json",
    )

    portfolio_extra = []
    if portfolio_id is not None:
        portfolio_extra.append(PortfolioSnapshot.portfolio_id == portfolio_id)
    if user_id is not None:
        portfolio_extra.append(PortfolioSnapshot.user_id == user_id)
    manifest["portfolio_snapshots"] = _summary(
        db, name="PortfolioSnapshot", model=PortfolioSnapshot, date_column=None, timestamp_column=PortfolioSnapshot.snapshot_time,
        start_date=None, end_date=None, market=market, status="FULL" if _table_exists(db, PortfolioSnapshot) else "DATA_GAP",
        reason=None if _table_exists(db, PortfolioSnapshot) else "portfolio_snapshot_table_unavailable",
        capabilities={"PORTFOLIO_DECISION": "FULL", "portfolio_specific_replay": "FULL"}, extra=tuple(portfolio_extra),
        source_fields=["snapshot_time", "status", "portfolio_id", "user_id"], availability_field="snapshot_time", lineage_field="source",
    )
    risk_extra = []
    if portfolio_id is not None:
        risk_extra.append(PortfolioRiskSnapshot.portfolio_id == portfolio_id)
    if user_id is not None:
        risk_extra.append(PortfolioRiskSnapshot.user_id == user_id)
    manifest["portfolio_risk_snapshots"] = _summary(
        db, name="PortfolioRiskSnapshot", model=PortfolioRiskSnapshot, date_column=None, timestamp_column=PortfolioRiskSnapshot.as_of,
        start_date=None, end_date=None, market=market, status="FULL" if _table_exists(db, PortfolioRiskSnapshot) else "DATA_GAP",
        reason=None if _table_exists(db, PortfolioRiskSnapshot) else "portfolio_risk_snapshot_table_unavailable",
        capabilities={"PORTFOLIO_DECISION": "FULL"}, extra=tuple(risk_extra), source_fields=["as_of", "calculation_version", "quality_status"],
        availability_field="as_of", lineage_field="calculation_version",
    )
    memory_extra = []
    if portfolio_id is not None:
        memory_extra.append(DecisionMemory.portfolio_id == portfolio_id)
    if user_id is not None:
        memory_extra.append(DecisionMemory.user_id == user_id)
    manifest["decision_memory"] = _summary(
        db, name="DecisionMemory", model=DecisionMemory, date_column=DecisionMemory.trade_date,
        timestamp_column=DecisionMemory.available_at, start_date=start_date, end_date=end_date, market=market,
        status="FULL" if _table_exists(db, DecisionMemory) else "DATA_GAP",
        reason=None if _table_exists(db, DecisionMemory) else "decision_memory_table_unavailable",
        capabilities={"MEMORY_DECISION": "FULL", "PRODUCTION_REPLAY": "FULL", "LLM_REPLAY": "FORBIDDEN"}, extra=tuple(memory_extra),
        source_fields=["trade_date", "decision_at", "available_at", "analysis_run_id", "calculation_version"],
        availability_field="available_at", lineage_field="source_refs_json",
    )
    outcome_extra = []
    manifest["decision_outcomes"] = _summary(
        db, name="DecisionOutcome", model=DecisionOutcome, date_column=DecisionOutcome.target_trade_date,
        timestamp_column=DecisionOutcome.available_at, start_date=start_date, end_date=end_date, market=market,
        status="PARTIAL", reason="outcomes are only available for previously captured DecisionMemory targets",
        capabilities={"MEMORY_DECISION": "FULL", "forward_outcome": "FULL"}, extra=tuple(outcome_extra),
        source_fields=["target_trade_date", "available_at", "reference_price_basis", "calculation_version", "source_refs_json"],
        availability_field="available_at", lineage_field="source_refs_json",
    )
    ledger_extra = []
    if portfolio_id is not None:
        ledger_extra.append(TradeLedgerEntry.portfolio_id == portfolio_id)
    if user_id is not None:
        ledger_extra.append(TradeLedgerEntry.user_id == user_id)
    manifest["trade_ledger"] = _summary(
        db, name="TradeLedgerEntry", model=TradeLedgerEntry, date_column=TradeLedgerEntry.trade_date,
        timestamp_column=TradeLedgerEntry.available_at, start_date=start_date, end_date=end_date, market=market,
        status="PARTIAL", reason="ledger is real execution evidence only and cannot be used as simulated research output",
        capabilities={"execution_alignment": "FULL", "simulated_trade_source": "FORBIDDEN"}, extra=tuple(ledger_extra),
        source_fields=["trade_date", "executed_at", "available_at", "analysis_run_id", "status"],
        availability_field="available_at", lineage_field="analysis_run_id",
    )

    manifest["source_lineage"] = _summary(
        db, name="SourceLineage", model=SourceLineage, date_column=SourceLineage.trade_date,
        timestamp_column=SourceLineage.fetched_at, start_date=start_date, end_date=end_date, market=market,
        status="PARTIAL", reason="lineage is snapshot-level and does not establish source publication time for every fact",
        capabilities={"provider_lineage": "PARTIAL", "source_publication_time": "UNSUPPORTED"},
        source_fields=["entity_type", "entity_key", "provider", "source_timestamp", "fetched_at", "trade_date", "quality_status"],
        availability_field="source_timestamp", lineage_field="provider",
    )

    history_items = _history_manifest_items(
        db, start_date=start_date, end_date=end_date, market=market
    )
    if history_items:
        manifest.update(history_items)
    else:
        for key, reason in {
            "fundamentals": "no report_period plus published_at/available_at point-in-time fundamental table exists",
            "valuation": "no historical PE/PB/dividend-yield table with point-in-time availability exists",
            "industry": "only current SecurityMaster classification is available; historical classification is not persisted",
            "etf_constituents": "historical ETF constituent snapshots are not persisted",
            "historical_universe": "ST, suspension, delisting and active status lack effective-dated history",
        }.items():
            manifest[key] = {
                "name": key,
                "status": "UNSUPPORTED" if key in {"fundamentals", "valuation", "etf_constituents"} else "LEAKAGE_BLOCKED",
                "row_count": 0,
                "distinct_trade_dates": 0,
                "earliest_supported_at": None,
                "latest_supported_at": None,
                "coverage": 0.0,
                "reason": reason,
                "capabilities": {"DETERMINISTIC_RECOMPUTE": "UNSUPPORTED"},
            }

        manifest["survivorship"] = {
            "status": "LEAKAGE_BLOCKED",
            "survivorship_status": "CURRENT_UNIVERSE_ONLY",
            "reason": "historical security lifecycle and delisted/suspended membership are unavailable",
        }
        manifest["point_in_time_universe"] = "PRODUCTION_SNAPSHOT_ONLY"
        manifest["factor_point_in_time"] = {
            "production_snapshots": "PARTIAL",
            "fundamentals": "UNSUPPORTED",
            "valuation": "UNSUPPORTED",
            "security_lifecycle": "LEAKAGE_BLOCKED",
        }
        manifest["price_basis"] = {"daily_bars": "QFQ_DECLARED", "source_availability": "INGESTION_SEMANTICS_UNCONFIRMED"}
    manifest["benchmark_basis"] = {"default": "ALL_A_MEDIAN_INDEX_DAILY", "intraday": "UNAVAILABLE_WITHOUT_INTRADAY_HISTORY"}
    manifest["execution_model"] = "NEXT_OPEN_PROXY_FOR_EOD_DIAGNOSTIC_ONLY"
    manifest["transaction_cost_model"] = "phase-e-portfolio-ledger-v1"
    manifest["slippage_model"] = {
        "status": "NOT_MODELED",
        "slippage_bps": None,
        "excluded_from_return": True,
    }
    if history_items:
        manifest["known_limitations"] = [
            "Historical PIT foundation tables exist; coverage is reported per data type.",
            "Candidate Score remains a censored top-subset sample.",
            "DailyBarCache.available_at is not asserted to be source publication time.",
            "Industry/flow historical recompute remains unsupported.",
            "Research output never enters DecisionMemory, TradeLedger, or production snapshots.",
        ]
    else:
        manifest["known_limitations"] = [
            "No historical effective-dated SecurityMaster lifecycle facts.",
            "Fundamental and valuation point-in-time replay is unsupported.",
            "Persisted CandidateScore is a censored top-subset sample.",
            "DailyBarCache.available_at is not asserted to be source publication time.",
            "Research output never enters DecisionMemory, TradeLedger, or production snapshots.",
        ]
    manifest["data_hash"] = _stable_source_hash(manifest)
    return manifest


__all__ = ["build_replay_availability_manifest"]
