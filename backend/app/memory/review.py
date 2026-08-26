"""Deterministic Daily Review and descriptive Memory statistics."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..market_engine_models import MarketScoreSnapshot
from ..portfolio_models import TradeLedgerEntry, TradeLedgerRevision
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from .config import DAILY_REVIEW_VERSION, MINIMUM_STATS_SAMPLE
from .execution import refresh_execution_alignments
from .models import DailyReviewRun, DecisionMemory, DecisionOutcome
from .outcomes import refresh_due_decision_outcomes


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _local_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(CHINA_TZ).date()


def _outcome_payload(row: DecisionOutcome) -> dict[str, Any]:
    return {
        "id": row.id,
        "decision_memory_id": row.decision_memory_id,
        "target_type": row.target_type,
        "target_key": row.target_key,
        "recommended_action": row.recommended_action,
        "horizon_trading_days": row.horizon_trading_days,
        "raw_return": row.raw_return,
        "benchmark_return": row.benchmark_return,
        "excess_return": row.excess_return,
        "mfe": row.mfe,
        "mae": row.mae,
        "directional_return": row.directional_return,
        "directional_excess_return": row.directional_excess_return,
        "execution_alignment": row.execution_alignment,
        "status": row.status,
        "quality_status": row.quality_status,
        "available_at": _json_value(row.available_at),
    }


def _memory_alignment(rows: list[DecisionOutcome]) -> str:
    values = {str(row.execution_alignment or "UNRESOLVED") for row in rows}
    for value in ("OPPOSITE", "PARTIAL", "IGNORED", "UNRESOLVED", "FOLLOWED", "NOT_APPLICABLE"):
        if value in values:
            return value
    return "UNRESOLVED"


def _stats_rows(rows: list[tuple[DecisionOutcome, DecisionMemory]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, Any, Any], list[DecisionOutcome]] = defaultdict(list)
    for row, memory in rows:
        regime = (memory.decision_features_json or {}).get("market_regime")
        groups[(row.horizon_trading_days, row.recommended_action, regime)].append(row)
    output: list[dict[str, Any]] = []
    for (horizon, action, regime), group in sorted(groups.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        excess = [float(row.excess_return) for row in group if row.excess_return is not None]
        directional_excess = [float(row.directional_excess_return) for row in group if row.directional_excess_return is not None]
        mfe = [float(row.mfe) for row in group if row.mfe is not None]
        mae = [float(row.mae) for row in group if row.mae is not None]
        sample_count = len(excess) or len(directional_excess)
        output.append({
            "horizon_trading_days": horizon,
            "action_type": action,
            "market_regime": regime,
            "sample_count": sample_count,
            "status": "INSUFFICIENT_SAMPLE" if sample_count < MINIMUM_STATS_SAMPLE else "DESCRIPTIVE",
            "mean_excess_return": mean(excess) if excess else None,
            "mean_directional_excess": mean(directional_excess) if directional_excess else None,
            "median_excess_return": median(excess) if excess else None,
            "median_directional_excess": median(directional_excess) if directional_excess else None,
            "median_mfe": median(mfe) if mfe else None,
            "median_mae": median(mae) if mae else None,
            "positive_directional_excess_ratio": (
                sum(value > 0 for value in directional_excess) / len(directional_excess)
                if directional_excess else None
            ),
            "distribution": {
                "min": min(excess) if excess else None,
                "max": max(excess) if excess else None,
                "count": len(excess),
            },
        })
    return output


def memory_stats(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    horizon: int | None = None,
    action_type: str | None = None,
    security_type: str | None = None,
    market_regime: str | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    cutoff = _utc_naive(as_of) or _now()
    query = select(DecisionOutcome, DecisionMemory).join(
        DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id
    ).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
        DecisionMemory.available_at <= cutoff,
        DecisionOutcome.status.in_(("VALID", "DEGRADED")),
        DecisionOutcome.available_at.is_not(None),
        DecisionOutcome.available_at <= cutoff,
    )
    if horizon is not None:
        query = query.where(DecisionOutcome.horizon_trading_days == horizon)
    if action_type:
        query = query.where(DecisionOutcome.recommended_action == action_type)
    rows = db.execute(query).all()
    filtered: list[tuple[DecisionOutcome, DecisionMemory]] = []
    for outcome, memory in rows:
        if security_type:
            targets = [*(memory.holding_decisions_json or []), *(memory.candidate_decisions_json or [])]
            if not any(str(target.get("security_type") or "").upper() == security_type.upper() for target in targets if isinstance(target, dict)):
                continue
        if market_regime and str((memory.decision_features_json or {}).get("market_regime") or "").upper() != market_regime.upper():
            continue
        filtered.append((outcome, memory))
    stats = _stats_rows(filtered)
    return {
        "status": "available",
        "as_of": cutoff,
        "sample_count": len(filtered),
        "aggregate_status": "INSUFFICIENT_SAMPLE" if len(filtered) < MINIMUM_STATS_SAMPLE else "DESCRIPTIVE",
        "statistics": stats,
        "rules": {
            "minimum_sample": MINIMUM_STATS_SAMPLE,
            "descriptive_only": True,
            "no_recommended_strategy": True,
            "no_weight_mutation": True,
        },
    }


def build_daily_review(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    trade_date: date,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    cutoff = _utc_naive(as_of) or _now()
    today_decisions = db.execute(select(DecisionMemory).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
        DecisionMemory.trade_date == trade_date,
        DecisionMemory.available_at <= cutoff,
    ).order_by(DecisionMemory.decision_at.asc(), DecisionMemory.id.asc())).scalars().all()
    memories = today_decisions
    memory_ids = [memory.id for memory in memories]
    outcomes = db.execute(select(DecisionOutcome).where(
        DecisionOutcome.decision_memory_id.in_(memory_ids or [-1]),
    ).order_by(DecisionOutcome.target_trade_date.asc(), DecisionOutcome.id.asc())).scalars().all()
    outcomes_by_memory: dict[int, list[DecisionOutcome]] = defaultdict(list)
    for row in outcomes:
        outcomes_by_memory[row.decision_memory_id].append(row)
    alignment_counts = Counter()
    decision_summaries: list[dict[str, Any]] = []
    candidate_action_count = 0
    actual_execution_count = 0
    for memory in memories:
        memory_outcomes = outcomes_by_memory.get(memory.id, [])
        alignment = _memory_alignment(memory_outcomes)
        alignment_counts[alignment] += 1
        candidates = memory.candidate_decisions_json or []
        if candidates:
            candidate_action_count += 1
        if any(
            row.actual_executed_qty is not None
            or bool(((row.source_refs_json or {}).get("execution") or {}).get("ledger_entry_ids"))
            for row in memory_outcomes
        ):
            actual_execution_count += 1
        decision_summaries.append({
            "decision_memory_id": memory.id,
            "analysis_run_id": memory.analysis_run_id,
            "decision_at": _json_value(memory.decision_at),
            "decision_type": memory.decision_type,
            "final_rating": memory.final_rating,
            "portfolio_action": memory.portfolio_action,
            "quality_status": memory.quality_status,
            "execution_alignment": alignment,
            "candidate_action_count": len(candidates),
        })
    # Review today's decisions and today's matured outcomes as two separate
    # facts.  A decision from an earlier trade date can mature today and must
    # still be visible in the current review.
    matured_candidates = db.execute(select(DecisionOutcome, DecisionMemory).join(
        DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id
    ).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
        DecisionMemory.available_at <= cutoff,
        DecisionOutcome.status.in_(("VALID", "DEGRADED")),
        DecisionOutcome.available_at.is_not(None),
        DecisionOutcome.available_at <= cutoff,
    ).order_by(DecisionOutcome.available_at.asc(), DecisionOutcome.id.asc())).all()
    matured = [
        row
        for row, _memory in matured_candidates
        if _local_date(row.available_at) == trade_date
    ]
    market = db.execute(select(MarketScoreSnapshot).where(
        MarketScoreSnapshot.market == "CN",
        MarketScoreSnapshot.trade_date == trade_date,
        MarketScoreSnapshot.captured_at <= cutoff,
    ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    market_summary = {
        "snapshot_id": market.snapshot_id if market else None,
        "display_score": market.display_score if market else None,
        "regime": market.regime if market else None,
        "confidence": market.confidence if market else None,
        "quality_status": market.quality_status if market else "MISSING",
        "is_frozen": market.is_frozen if market else None,
    }
    reason_codes: list[str] = []
    if not memories:
        reason_codes.append("NO_DECISIONS")
    if memories and all(memory.decision_type in {"NO_ACTION", "HOLD_ONLY"} for memory in memories):
        reason_codes.append("NO_ACTION_DOMINATED")
    if candidate_action_count:
        reason_codes.append("ACTION_CANDIDATE_PRESENT")
    if market and market.is_frozen:
        reason_codes.append("MARKET_RISK_HIGH")
    for alignment, code in {
        "FOLLOWED": "EXECUTION_FOLLOWED",
        "PARTIAL": "EXECUTION_PARTIAL",
        "IGNORED": "EXECUTION_IGNORED",
        "OPPOSITE": "EXECUTION_OPPOSITE",
        "UNRESOLVED": "EXECUTION_UNRESOLVED",
    }.items():
        if alignment_counts.get(alignment):
            reason_codes.append(code)
    if matured:
        reason_codes.append("OUTCOME_MATURED")
    if any(row.status in {"WAITING_DATA", "BLOCKED", "UNAVAILABLE"} for row in outcomes):
        reason_codes.append("OUTCOME_DATA_MISSING")
    if any(row.status == "PENDING" for row in outcomes):
        reason_codes.append("OUTCOME_PENDING")
    revisions = db.execute(select(TradeLedgerRevision, TradeLedgerEntry).join(
        TradeLedgerEntry, TradeLedgerRevision.ledger_entry_id == TradeLedgerEntry.id
    ).where(
        TradeLedgerEntry.user_id == user_id,
        TradeLedgerEntry.portfolio_id == portfolio_id,
        TradeLedgerRevision.created_at <= cutoff,
    )).all()
    execution_record_revised = any(
        _local_date(revision.created_at) == trade_date
        for revision, _entry in revisions
    )
    if execution_record_revised:
        reason_codes.append("EXECUTION_RECORD_REVISED")
    all_valid_outcomes = [row for row in outcomes if row.status in {"VALID", "DEGRADED"} and row.available_at and _utc_naive(row.available_at) <= cutoff]
    memory_by_id = {memory.id: memory for memory in memories}
    stats = _stats_rows([
        (row, memory_by_id[row.decision_memory_id])
        for row in all_valid_outcomes
        if row.decision_memory_id in memory_by_id
    ])
    if any(row.excess_return is not None and row.excess_return > 0 for row in all_valid_outcomes):
        reason_codes.append("OUTCOME_POSITIVE_EXCESS")
    if any(row.excess_return is not None and row.excess_return < 0 for row in all_valid_outcomes):
        reason_codes.append("OUTCOME_NEGATIVE_EXCESS")
    if any(row.get("status") == "INSUFFICIENT_SAMPLE" for row in stats):
        reason_codes.append("INSUFFICIENT_SAMPLE")
    confidence_values = [float(memory.confidence or 0.0) for memory in memories]
    quality = "VALID"
    if not memories or market_summary["quality_status"] in {"MISSING", "BLOCKED"} or any(memory.quality_status == "BLOCKED" for memory in memories):
        quality = "DEGRADED"
    if any(row.status in {"BLOCKED", "UNAVAILABLE"} for row in outcomes):
        quality = "DEGRADED"
    return {
        "status": "COMPLETED",
        "user_id": user_id,
        "portfolio_id": portfolio_id,
        "trade_date": trade_date,
        "decision_count": len(memories),
        "no_action_count": sum(memory.decision_type in {"NO_ACTION", "HOLD_ONLY"} for memory in memories),
        "action_decision_count": sum(memory.decision_type in {"PORTFOLIO_ACTION", "NEW_POSITION_ACTION", "MIXED_ACTION"} for memory in memories),
        "candidate_action_count": candidate_action_count,
        "actual_execution_count": actual_execution_count,
        "execution_followed_count": alignment_counts.get("FOLLOWED", 0),
        "execution_partial_count": alignment_counts.get("PARTIAL", 0),
        "execution_ignored_count": alignment_counts.get("IGNORED", 0),
        "execution_opposite_count": alignment_counts.get("OPPOSITE", 0),
        "execution_unresolved_count": alignment_counts.get("UNRESOLVED", 0),
        "outcomes_matured_count": len(matured),
        "market_summary": market_summary,
        "decision_summary": {
            "decisions": decision_summaries,
            "outcomes_matured_today": [_outcome_payload(row) for row in matured],
        },
        "execution_summary": {
            "alignment_counts": dict(alignment_counts),
            "actual_execution_count": actual_execution_count,
            "execution_record_revised": execution_record_revised,
        },
        "outcome_summary": {
            "outcomes_matured_today": [_outcome_payload(row) for row in matured],
            "statistics": stats,
            "sample_count": len(all_valid_outcomes),
        },
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "quality_status": quality,
        "confidence": sum(confidence_values) / len(confidence_values) if confidence_values else 0.0,
        "review_version": DAILY_REVIEW_VERSION,
        "as_of": cutoff,
    }


def _apply_review(row: DailyReviewRun, payload: dict[str, Any], *, refreshed: bool = False) -> None:
    row.status = payload["status"]
    row.decision_count = payload["decision_count"]
    row.no_action_count = payload["no_action_count"]
    row.action_decision_count = payload["action_decision_count"]
    row.candidate_action_count = payload["candidate_action_count"]
    row.actual_execution_count = payload["actual_execution_count"]
    row.execution_followed_count = payload["execution_followed_count"]
    row.execution_partial_count = payload["execution_partial_count"]
    row.execution_ignored_count = payload["execution_ignored_count"]
    row.execution_opposite_count = payload["execution_opposite_count"]
    row.execution_unresolved_count = payload["execution_unresolved_count"]
    row.outcomes_matured_count = payload["outcomes_matured_count"]
    row.market_summary_json = payload["market_summary"]
    row.decision_summary_json = payload["decision_summary"]
    row.execution_summary_json = payload["execution_summary"]
    row.outcome_summary_json = payload["outcome_summary"]
    row.reason_codes_json = payload["reason_codes"]
    row.quality_status = payload["quality_status"]
    row.confidence = payload["confidence"]
    row.review_stale = False
    # The counter is incremented with a SQL expression in run_daily_review so
    # two concurrent refreshers cannot overwrite one another's count.
    if not refreshed:
        row.refresh_count = int(row.refresh_count or 0)
    row.review_version = DAILY_REVIEW_VERSION
    row.completed_at = _now()
    row.last_refreshed_at = row.completed_at


def run_daily_review(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    trade_date: date,
    as_of: datetime | None = None,
    force: bool = False,
) -> DailyReviewRun | None:
    calendar = TradingCalendarService(db)
    if not calendar.is_trading_day(trade_date):
        return None
    existing = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == user_id,
        DailyReviewRun.portfolio_id == portfolio_id,
        DailyReviewRun.trade_date == trade_date,
        DailyReviewRun.review_version == DAILY_REVIEW_VERSION,
    )).scalar_one_or_none()
    if existing is not None and existing.status == "COMPLETED" and not force:
        return existing
    if existing is None:
        existing = DailyReviewRun(
            user_id=user_id,
            portfolio_id=portfolio_id,
            trade_date=trade_date,
            status="RUNNING",
            review_version=DAILY_REVIEW_VERSION,
        )
        db.add(existing)
        db.flush()
    else:
        existing.status = "RUNNING"
        db.flush()
    try:
        refresh_due_decision_outcomes(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            calculation_as_of=as_of,
            persist=False,
        )
        refresh_execution_alignments(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            calculation_as_of=as_of,
            persist=False,
        )
        payload = build_daily_review(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            trade_date=trade_date,
            as_of=as_of,
        )
        refreshed = force and existing.completed_at is not None
        _apply_review(existing, payload, refreshed=refreshed)
        if refreshed:
            db.execute(
                update(DailyReviewRun)
                .where(DailyReviewRun.id == existing.id)
                .values(
                    refresh_count=func.coalesce(DailyReviewRun.refresh_count, 0) + 1,
                    last_refreshed_at=existing.completed_at,
                )
            )
        db.commit()
        db.refresh(existing)
        return existing
    except Exception as exc:  # maintenance failure is persisted and retryable
        db.rollback()
        failed = db.execute(select(DailyReviewRun).where(
            DailyReviewRun.user_id == user_id,
            DailyReviewRun.portfolio_id == portfolio_id,
            DailyReviewRun.trade_date == trade_date,
            DailyReviewRun.review_version == DAILY_REVIEW_VERSION,
        )).scalar_one_or_none()
        if failed is None:
            failed = DailyReviewRun(
                user_id=user_id,
                portfolio_id=portfolio_id,
                trade_date=trade_date,
                review_version=DAILY_REVIEW_VERSION,
            )
            db.add(failed)
        failed.status = "FAILED"
        failed.quality_status = "BLOCKED"
        failed.reason_codes_json = ["MAINTENANCE_FAILED", str(exc)[:300]]
        failed.completed_at = None
        db.commit()
        return failed


def serialize_daily_review(row: DailyReviewRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "portfolio_id": row.portfolio_id,
        "trade_date": row.trade_date,
        "status": row.status,
        "decision_count": row.decision_count,
        "no_action_count": row.no_action_count,
        "action_decision_count": row.action_decision_count,
        "candidate_action_count": row.candidate_action_count,
        "actual_execution_count": row.actual_execution_count,
        "execution_followed_count": row.execution_followed_count,
        "execution_partial_count": row.execution_partial_count,
        "execution_ignored_count": row.execution_ignored_count,
        "execution_opposite_count": row.execution_opposite_count,
        "execution_unresolved_count": row.execution_unresolved_count,
        "outcomes_matured_count": row.outcomes_matured_count,
        "market_summary": row.market_summary_json or {},
        "decision_summary": row.decision_summary_json or {},
        "execution_summary": row.execution_summary_json or {},
        "outcome_summary": row.outcome_summary_json or {},
        "reason_codes": row.reason_codes_json or [],
        "quality_status": row.quality_status,
        "confidence": row.confidence,
        "review_stale": row.review_stale,
        "last_refreshed_at": row.last_refreshed_at,
        "refresh_count": row.refresh_count,
        "review_version": row.review_version,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


__all__ = [
    "build_daily_review",
    "memory_stats",
    "run_daily_review",
    "serialize_daily_review",
]
