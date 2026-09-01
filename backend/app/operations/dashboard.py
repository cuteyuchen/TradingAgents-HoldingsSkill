"""Read-only Daily Investment Workbench aggregation."""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..clock import utc_now
from ..candidates.models import CandidateRun, CandidateScore
from ..candidates.service import latest_candidate_context
from ..config import settings
from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache, MarketMetricSnapshot, MarketScoreSnapshot
from ..market_models import SecurityMaster, TradingCalendar
from ..market_runtime_models import ProviderHealth
from ..memory.models import DailyReviewRun, DecisionMemory, DecisionOutcome
from ..portfolio_models import PortfolioRiskSnapshot, TradeLedgerEntry
from ..portfolio.snapshot_diff import snapshot_reserve_assets
from ..services.realtime_monitor import get_realtime_monitor
from ..services import scheduler as scheduler_service
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from ..trigger_models import TriggerEvent
from ..v2_models import AnalysisJob, AnalysisRun, HoldingItem, Portfolio, PortfolioSnapshot
from .config import FRESHNESS_LIMITS_SECONDS
from .notifications import list_operating_notifications
from .timeline import china_time, derive_workflow_state
from .workflow import JOB_STATUS_MAP, operational_timeline
from .models import DailyOperationalRun

logger = logging.getLogger(__name__)


HEALTH_ORDER = {"OK": 0, "DEGRADED": 1, "UNKNOWN": 2, "BLOCKED": 3}


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _iso(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_iso(item) for item in value]
    return value


def _cutoff(as_of: datetime | None) -> datetime:
    now = utc_now().replace(tzinfo=None)
    value = _utc_naive(as_of) or now
    if value > now + timedelta(minutes=1):
        raise ValueError("as_of_cannot_be_in_the_future")
    return value


def _section(builder: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = builder()
        payload.setdefault("status", "AVAILABLE")
        return payload
    except Exception as exc:  # dashboard sections fail independently
        logger.warning("dashboard section failed", exc_info=True)
        return {"status": "ERROR", "error": str(exc)[:300]}


def _freshness(captured_at: datetime | None, *, cutoff: datetime, limit_seconds: float) -> str:
    if captured_at is None:
        return "MISSING"
    age = max(0.0, (cutoff - _utc_naive(captured_at)).total_seconds())
    return "FRESH" if age <= limit_seconds else "STALE"


def _local(cutoff: datetime) -> datetime:
    return cutoff.replace(tzinfo=UTC).astimezone(CHINA_TZ)


def _day_start(local_date: date) -> datetime:
    return datetime.combine(local_date, datetime.min.time()).replace(tzinfo=CHINA_TZ).astimezone(UTC).replace(tzinfo=None)


def _review_as_of_conditions(cutoff: datetime) -> tuple[Any, ...]:
    """Keep a historical dashboard from exposing a review completed later."""

    return (
        DailyReviewRun.created_at <= cutoff,
        or_(DailyReviewRun.completed_at.is_(None), DailyReviewRun.completed_at <= cutoff),
        or_(DailyReviewRun.last_refreshed_at.is_(None), DailyReviewRun.last_refreshed_at <= cutoff),
    )


def _job_status_as_of(job: AnalysisJob, cutoff: datetime) -> str:
    """Project a job's persisted status onto the requested historical cutoff."""

    finished_at = _utc_naive(job.finished_at)
    if finished_at is not None and finished_at > cutoff:
        return "RUNNING"
    return JOB_STATUS_MAP.get(str(job.status).lower(), str(job.status).upper())


def _health_from_freshness(freshness: str | None, *, mandatory: bool = False, market_open: bool = False) -> str:
    value = str(freshness or "MISSING").upper()
    if value == "FRESH":
        return "OK"
    if value == "FROZEN":
        return "BLOCKED" if mandatory and market_open else "DEGRADED"
    if value == "MISSING":
        return "BLOCKED" if mandatory and market_open else "UNKNOWN"
    return "DEGRADED"


def _severity(status: str, *, mandatory: bool = False) -> str:
    raw = str(status or "").upper()
    if raw in {"VALID", "HEALTHY", "AVAILABLE", "RUNNING", "OK", "FRESH", "COMPLETED"}:
        return "OK"
    if raw in {"DEGRADED", "STALE", "RECOVERING", "LUNCH", "PAUSED_LUNCH"}:
        return "DEGRADED"
    if raw in {"BLOCKED", "CIRCUIT_OPEN", "MISSING"}:
        return "BLOCKED" if mandatory else "DEGRADED"
    return "BLOCKED" if mandatory else "UNKNOWN"


def _overall(components: list[dict[str, Any]]) -> str:
    values = [_severity(item.get("status"), mandatory=bool(item.get("mandatory"))) for item in components]
    return max(values, key=lambda value: HEALTH_ORDER[value], default="UNKNOWN")


def _health_status(
    status: Any,
    *,
    mandatory: bool = False,
    market_open: bool = False,
) -> str:
    """Normalize component health without changing a section's raw status."""

    value = str(status or "UNKNOWN").upper()
    if value in {"OK", "DEGRADED", "BLOCKED", "UNKNOWN"}:
        return value
    if value in {"FRESH", "VALID", "HEALTHY", "AVAILABLE", "RUNNING", "SUCCESS", "COMPLETED"}:
        return "OK"
    if value in {"STALE", "RECOVERING", "PAUSED", "PAUSED_LUNCH", "LUNCH", "FROZEN"}:
        if value == "FROZEN" and mandatory and market_open:
            return "BLOCKED"
        return "DEGRADED"
    if value in {"MISSING", "UNAVAILABLE", "NOT_AVAILABLE"}:
        return "BLOCKED" if mandatory and market_open else "UNKNOWN"
    if value in {"FAILED", "CIRCUIT_OPEN"}:
        return "BLOCKED" if mandatory else "DEGRADED"
    return "UNKNOWN"


def _market_section(db: Session, *, cutoff: datetime) -> dict[str, Any]:
    local = _local(cutoff)
    current_query = select(MarketScoreSnapshot).where(
        MarketScoreSnapshot.market == "CN",
        MarketScoreSnapshot.captured_at <= cutoff,
    )
    if local.time() < time(9, 30):
        current_query = current_query.where(MarketScoreSnapshot.trade_date < local.date())
    else:
        current_query = current_query.where(MarketScoreSnapshot.trade_date == local.date())
    row = db.execute(current_query.order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        # Before today's first snapshot, use the last persisted fact explicitly
        # as a previous-close fallback rather than implying live data exists.
        row = db.execute(select(MarketScoreSnapshot).where(
            MarketScoreSnapshot.market == "CN",
            MarketScoreSnapshot.captured_at <= cutoff,
        ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        return {
            "status": "MISSING",
            "freshness": "MISSING",
            "quality_status": "MISSING",
            "score": None,
            "regime": None,
            "market_mode": "PRE_MARKET" if local.time() < time(9, 30) else "INTRADAY",
            "market_score_source": "PREVIOUS_CLOSE" if local.time() < time(9, 30) else "PERSISTED_SNAPSHOT",
        }
    metric = None
    if row.metric_snapshot_id:
        metric = db.execute(select(MarketMetricSnapshot).where(MarketMetricSnapshot.snapshot_id == row.metric_snapshot_id)).scalar_one_or_none()
    # ``delta_15m`` is a fixed-time comparison, not a comparison with the
    # previous polling row.  Keep the baseline on the same trading date and
    # exclude frozen/invalid observations from the read model.
    current_captured_at = _utc_naive(row.captured_at)
    baseline = None
    if current_captured_at is not None:
        target_at = current_captured_at - timedelta(minutes=15)
        tolerance = timedelta(minutes=max(1, settings.TRIGGER_MARKET_SCORE_BASELINE_TOLERANCE_MINUTES))
        baseline_rows = db.execute(select(MarketScoreSnapshot).where(
            MarketScoreSnapshot.market == row.market,
            MarketScoreSnapshot.trade_date == row.trade_date,
            MarketScoreSnapshot.captured_at >= target_at - tolerance,
            MarketScoreSnapshot.captured_at <= min(target_at + tolerance, current_captured_at),
            MarketScoreSnapshot.quality_status.in_(("VALID", "DEGRADED")),
            MarketScoreSnapshot.is_frozen.is_(False),
            MarketScoreSnapshot.display_score.is_not(None),
        ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc())).scalars().all()
        baseline = min(
            baseline_rows,
            key=lambda candidate: (
                abs((_utc_naive(candidate.captured_at) - target_at).total_seconds()),
                -_utc_naive(candidate.captured_at).timestamp(),
            ),
            default=None,
        )
    open_row = db.execute(select(MarketScoreSnapshot).where(
        MarketScoreSnapshot.market == "CN",
        MarketScoreSnapshot.trade_date == row.trade_date,
        MarketScoreSnapshot.captured_at <= cutoff,
    ).order_by(MarketScoreSnapshot.captured_at.asc(), MarketScoreSnapshot.id.asc()).limit(1)).scalar_one_or_none()
    freshness = "FROZEN" if row.is_frozen else _freshness(row.captured_at, cutoff=cutoff, limit_seconds=FRESHNESS_LIMITS_SECONDS["market"])
    is_pre_market = local.time() < time(9, 30)
    is_previous_close = row.trade_date != local.date()
    if is_pre_market:
        market_mode = "PRE_MARKET"
    elif local.time() >= time(15, 0):
        market_mode = "POST_CLOSE"
    else:
        market_mode = "INTRADAY"
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    return {
        "status": "FROZEN" if row.is_frozen else str(row.quality_status or "MISSING").upper(),
        "quality_status": str(row.quality_status or "MISSING").upper(),
        "freshness": freshness,
        "captured_at": row.captured_at,
        "trade_date": row.trade_date,
        "snapshot_id": row.snapshot_id,
        "score": row.display_score,
        "raw_score": row.raw_score,
        "regime": row.regime,
        "confidence": row.confidence,
        "is_frozen": row.is_frozen,
        "freeze_reason": row.freeze_reason,
        "market_mode": market_mode,
        "market_score_source": "PREVIOUS_CLOSE" if is_previous_close else "PERSISTED_SNAPSHOT",
        "health_status": _health_status(freshness, mandatory=True, market_open=not is_pre_market),
        "source_lineage_status": str(metadata.get("source_lineage_status") or metadata.get("lineage_status") or "AVAILABLE").upper(),
        "delta_15m": (row.display_score - baseline.display_score) if row.display_score is not None and baseline and baseline.display_score is not None else None,
        "delta_from_open": (row.display_score - open_row.display_score) if row.display_score is not None and open_row and open_row.display_score is not None else None,
        "components": {
            "breadth": row.breadth_score,
            "trend": row.trend_score,
            "liquidity": row.liquidity_score,
            "profitability": row.profitability_score,
            "diffusion": row.diffusion_score,
            "crowding": row.crowding_score,
            "tail_risk": row.tail_risk_score,
            "top5_turnover_concentration": metric.top5_concentration if metric else None,
        },
        "all_a_median": _all_a_median(db, cutoff=cutoff, local_date=local.date()),
    }


def _all_a_median(db: Session, *, cutoff: datetime, local_date: date | None = None) -> dict[str, Any]:
    effective_date = local_date or _local(cutoff).date()
    row = db.execute(select(AllAMedianIndexDaily).where(
        AllAMedianIndexDaily.market == "CN",
        or_(
            AllAMedianIndexDaily.available_at <= cutoff,
            AllAMedianIndexDaily.available_at.is_(None) & (AllAMedianIndexDaily.created_at <= cutoff),
        ),
        AllAMedianIndexDaily.trade_date <= effective_date,
    ).order_by(AllAMedianIndexDaily.trade_date.desc(), AllAMedianIndexDaily.id.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        return {"status": "MISSING", "index_value": None}
    return {
        "status": str(row.quality_status or "MISSING").upper(),
        "trade_date": row.trade_date,
        "index_value": row.index_value,
        "captured_at": row.available_at or row.created_at,
        "health_status": _health_status(row.quality_status, mandatory=False),
    }


def _portfolio_section(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> dict[str, Any]:
    snapshot = db.execute(select(PortfolioSnapshot).where(
        PortfolioSnapshot.user_id == user_id,
        PortfolioSnapshot.portfolio_id == portfolio_id,
        PortfolioSnapshot.status == "confirmed",
        PortfolioSnapshot.snapshot_time <= cutoff,
    ).order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    if snapshot is None:
        return {"status": "MISSING", "snapshot_id": None, "holdings": [], "hard_cap_breaches": []}
    risk = db.execute(select(PortfolioRiskSnapshot).where(
        PortfolioRiskSnapshot.user_id == user_id,
        PortfolioRiskSnapshot.portfolio_id == portfolio_id,
        PortfolioRiskSnapshot.portfolio_snapshot_id == snapshot.id,
        PortfolioRiskSnapshot.as_of <= cutoff,
    ).order_by(PortfolioRiskSnapshot.as_of.desc(), PortfolioRiskSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    holdings = db.execute(select(HoldingItem).where(HoldingItem.snapshot_id == snapshot.id).order_by(HoldingItem.weight.desc().nullslast(), HoldingItem.id.asc())).scalars().all()
    risk_flags = list(risk.risk_flags_json or []) if risk else []
    total_assets = snapshot.total_assets
    reserve_assets = snapshot_reserve_assets(snapshot)
    position_count = len(holdings)
    return {
        "status": str(risk.quality_status if risk else "DEGRADED").upper(),
        "quality_status": str(risk.quality_status if risk else "DEGRADED").upper(),
        "freshness": _freshness(snapshot.snapshot_time, cutoff=cutoff, limit_seconds=FRESHNESS_LIMITS_SECONDS["portfolio"]),
        "snapshot_id": snapshot.id,
        "snapshot_time": snapshot.snapshot_time,
        "total_assets": total_assets,
        "market_value": snapshot.total_market_value,
        "spendable_cash": snapshot.broker_available_cash,
        "reserve_assets": reserve_assets,
        "reserve_ratio": reserve_assets / total_assets if reserve_assets is not None and total_assets else None,
        "cash_ratio": risk.cash_ratio if risk else None,
        "gross_exposure": risk.gross_exposure if risk else None,
        "top1_weight": risk.top1_weight if risk else None,
        "top3_weight": risk.top3_weight if risk else None,
        "top5_weight": risk.top5_weight if risk else None,
        "hhi": risk.hhi if risk else None,
        "portfolio_vol_20": risk.portfolio_vol_20 if risk else None,
        "portfolio_vol_60": risk.portfolio_vol_60 if risk else None,
        "weighted_correlation": risk.weighted_average_correlation if risk else None,
        "max_pairwise_correlation": risk.max_pairwise_correlation if risk else None,
        "position_count": position_count,
        "hard_cap_breaches": [flag for flag in risk_flags if "CAP" in str(flag).upper()],
        "risk_flags": risk_flags,
        "holdings": [{
            "code": item.code,
            "name": item.name,
            "qty": item.qty,
            "available_qty": item.available_qty,
            "price": item.screenshot_price,
            "market_value": item.market_value,
            "weight": item.weight,
            "pnl_ratio": item.pnl_ratio,
            "quote_quality": (item.extra_json or {}).get("quote_quality") if isinstance(item.extra_json, dict) else None,
            "keep_score": (item.extra_json or {}).get("keep_score") if isinstance(item.extra_json, dict) else None,
            "opportunity_reference": (item.extra_json or {}).get("opportunity_reference") if isinstance(item.extra_json, dict) else None,
            "hard_cap": (item.extra_json or {}).get("hard_cap") if isinstance(item.extra_json, dict) else None,
            "headroom": (item.extra_json or {}).get("headroom") if isinstance(item.extra_json, dict) else None,
            "holding_action": (item.extra_json or {}).get("holding_action") if isinstance(item.extra_json, dict) else None,
            "risk_flags": (item.extra_json or {}).get("risk_flags", []) if isinstance(item.extra_json, dict) else [],
        } for item in holdings],
    }


def _candidate_section(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime, portfolio: dict[str, Any]) -> dict[str, Any]:
    latest_run = db.execute(select(CandidateRun).where(
        CandidateRun.user_id == user_id,
        CandidateRun.portfolio_id == portfolio_id,
        CandidateRun.as_of <= cutoff,
    ).order_by(CandidateRun.as_of.desc(), CandidateRun.id.desc()).limit(1)).scalar_one_or_none()
    scan_in_progress = bool(latest_run is not None and str(latest_run.status).upper() not in {"COMPLETED", "SUCCESS"})
    reliable_run = db.execute(select(CandidateRun).where(
        CandidateRun.user_id == user_id,
        CandidateRun.portfolio_id == portfolio_id,
        CandidateRun.as_of <= cutoff,
        func.upper(CandidateRun.status) == "COMPLETED",
    ).order_by(CandidateRun.as_of.desc(), CandidateRun.id.desc()).limit(1)).scalar_one_or_none()
    context_as_of = reliable_run.as_of if reliable_run is not None else cutoff
    context = latest_candidate_context(db, user_id=user_id, portfolio_id=portfolio_id, as_of=context_as_of)
    run = dict(context.get("run") or {})
    run_id = context.get("run_id") or run.get("id") or run.get("run_id")
    if not run_id:
        return {
            "status": "MISSING",
            "freshness": "MISSING",
            "watchlist": [],
            "ready": [],
            "action": [],
            "counts": {"watchlist": 0, "ready": 0, "action": 0},
            "scan_in_progress": scan_in_progress,
            "in_progress_run_id": latest_run.id if scan_in_progress and latest_run else None,
        }
    run_snapshot = context.get("portfolio_snapshot_id") or run.get("portfolio_snapshot_id")
    stale_portfolio = bool(run_snapshot and portfolio.get("snapshot_id") and run_snapshot != portfolio.get("snapshot_id"))
    scores = list(context.get("scores") or [])
    freshness = "STALE" if stale_portfolio else ("STALE" if (context.get("freshness_seconds") or 0) > FRESHNESS_LIMITS_SECONDS["candidate"] else "FRESH")
    stale_run = freshness == "STALE"
    if stale_portfolio or stale_run:
        persisted_stages = {
            row.code: str(row.stage or "").upper()
            for row in db.execute(select(CandidateScore).where(CandidateScore.candidate_run_id == run_id)).scalars().all()
        }
        for score in scores:
            score["candidate_engine_stage"] = persisted_stages.get(str(score.get("code") or "")) or score.get("candidate_engine_stage") or score.get("stage")
            score["display_stage"] = "STALE"
            score["actionable"] = False
            score["buyable"] = False
            reason = "PORTFOLIO_SNAPSHOT_CHANGED" if stale_portfolio else "CANDIDATE_RUN_STALE"
            if reason not in score.setdefault("blocking_reasons", []):
                score["blocking_reasons"].append(reason)
    pools = {
        stage.lower(): [
            score for score in scores
            if not stale_portfolio and not stale_run and str(score.get("stage") or "").upper() == stage
        ]
        for stage in ("WATCHLIST", "READY", "ACTION")
    }
    return {
        "status": "STALE" if stale_portfolio or stale_run else str(context.get("quality_status") or run.get("quality_status") or "MISSING").upper(),
        "quality_status": str(context.get("quality_status") or run.get("quality_status") or "MISSING").upper(),
        "freshness": freshness,
        "age_seconds": context.get("freshness_seconds"),
        "run_id": run_id,
        "captured_at": run.get("captured_at") or run.get("as_of"),
        "portfolio_snapshot_id": run_snapshot,
        "market_score_snapshot_id": run.get("market_score_snapshot_id"),
        "counts": {**{key: len(value) for key, value in pools.items()}, "stale": len(scores) if stale_portfolio or stale_run else 0},
        "watchlist": pools["watchlist"],
        "ready": pools["ready"],
        "action": pools["action"],
        "stale": scores if stale_portfolio or stale_run else [],
        "candidate_authority": "deterministic_candidate_engine",
        "late_session_review_only": china_time(cutoff).time() >= datetime.min.time().replace(hour=14, minute=55),
        "scan_in_progress": scan_in_progress,
        "in_progress_run_id": latest_run.id if scan_in_progress and latest_run else None,
    }


def _trigger_section(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> dict[str, Any]:
    local_date = cutoff.replace(tzinfo=UTC).astimezone(CHINA_TZ).date()
    rows = db.execute(select(TriggerEvent).where(
        TriggerEvent.portfolio_id == portfolio_id,
        or_(TriggerEvent.user_id == user_id, TriggerEvent.user_id.is_(None)),
        TriggerEvent.detected_at >= datetime.combine(local_date, datetime.min.time()).replace(tzinfo=CHINA_TZ).astimezone(UTC).replace(tzinfo=None),
        TriggerEvent.detected_at <= cutoff,
    ).order_by(TriggerEvent.detected_at.desc(), TriggerEvent.id.desc())).scalars().all()
    return {"status": "AVAILABLE", "today_count": len(rows), "counts_by_status": {status: sum(1 for row in rows if row.status == status) for status in ("ACTIVE", "CONFIRMED", "ANALYZING", "RESOLVED")}, "items": [{
        "id": row.id, "status": row.status, "priority": row.priority, "trigger_type": row.trigger_type, "target": row.target_key,
        "reason": (row.evidence_json or {}).get("reason_code") or row.trigger_type, "detected_at": row.detected_at, "confirmed_at": row.confirmed_at,
        "analysis_job_id": row.analysis_job_id, "analysis_run_id": row.analysis_run_id, "resolution": row.resolution,
        "semantic": "需要重新分析，不是交易信号",
    } for row in rows]}


def _analysis_section(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> dict[str, Any]:
    local_date = _local(cutoff).date()
    start = _day_start(local_date)
    jobs = db.execute(select(AnalysisJob).where(
        AnalysisJob.user_id == user_id, AnalysisJob.portfolio_id == portfolio_id,
        AnalysisJob.created_at >= start, AnalysisJob.created_at <= cutoff,
    ).order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc())).scalars().all()
    runs = db.execute(select(AnalysisRun).join(AnalysisJob, AnalysisRun.job_id == AnalysisJob.id).where(
        AnalysisRun.user_id == user_id,
        AnalysisJob.portfolio_id == portfolio_id,
        AnalysisJob.status == "succeeded",
        AnalysisJob.finished_at.is_not(None),
        AnalysisJob.finished_at <= cutoff,
        AnalysisRun.created_at >= start,
        AnalysisRun.created_at <= cutoff,
    ).order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc()).limit(20)).scalars().all()
    latest = runs[0] if runs else None
    result = (latest.structured_result_json or {}).get("result", {}) if latest else {}
    decision_gate = result.get("decision_gate") if isinstance(result.get("decision_gate"), dict) else {}
    action_rows = result.get("candidates") or []
    in_progress = [job for job in jobs if _job_status_as_of(job, cutoff) == "RUNNING"]
    latest_payload = None
    if latest is not None:
        latest_payload = {
            "analysis_job_id": latest.job_id,
            "analysis_run_id": latest.id,
            "mode": latest.job.mode if latest.job else None,
            "started_at": latest.job.started_at if latest.job else None,
            "finished_at": latest.job.finished_at if latest.job else None,
            "status": "SUCCESS",
            "final_rating": latest.final_rating,
            "portfolio_action": result.get("portfolio_action") or decision_gate.get("portfolio_action"),
            "quality": latest.data_quality_grade,
            "confidence": latest.confidence,
            "candidate_action_count": len([
                row for row in action_rows
                if isinstance(row, dict) and str(row.get("stage") or "").upper() == "ACTION"
            ]),
        }
    return {
        "status": "AVAILABLE" if jobs or latest else "MISSING",
        "jobs": [{
            "id": job.id,
            "checkpoint": job.checkpoint,
            "mode": job.mode,
            "status": _job_status_as_of(job, cutoff),
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "run_id": job.run.id if job.run and _utc_naive(job.run.created_at) <= cutoff and job.finished_at is not None and _utc_naive(job.finished_at) <= cutoff else None,
            "trigger_linked": job.trigger_type == "realtime_trigger",
        } for job in jobs],
        "latest": latest_payload,
        "last_analysis": latest_payload,
        "analysis_in_progress": bool(in_progress),
        "running_jobs": [{
            "id": job.id,
            "checkpoint": job.checkpoint,
            "mode": job.mode,
            "status": "RUNNING",
            "started_at": job.started_at,
        } for job in in_progress],
        "quality_status": latest.data_quality_grade if latest else None,
    }


def _decision_section(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> dict[str, Any]:
    local_date = _local(cutoff).date()
    start = _day_start(local_date)
    rows = db.execute(select(DecisionMemory).join(
        AnalysisRun, DecisionMemory.analysis_run_id == AnalysisRun.id,
    ).join(
        AnalysisJob, AnalysisRun.job_id == AnalysisJob.id,
    ).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
        AnalysisJob.status == "succeeded",
        AnalysisJob.finished_at.is_not(None),
        AnalysisJob.finished_at <= cutoff,
        AnalysisRun.created_at <= cutoff,
        DecisionMemory.created_at <= cutoff,
        DecisionMemory.available_at <= cutoff,
        DecisionMemory.decision_at >= start,
        DecisionMemory.decision_at <= cutoff,
    ).order_by(DecisionMemory.decision_at.desc(), DecisionMemory.id.desc())).scalars().all()
    latest = rows[0] if rows else None
    latest_payload = None
    if latest is not None:
        action = str(latest.portfolio_action or latest.final_rating or "no_action").strip().upper()
        conclusion = "NO_ACTION" if action in {"NO_ACTION", "HOLD", "HOLD_ONLY", "WATCH", "WATCH_ONLY"} else action
        if str(latest.quality_status or "").upper() in {"BLOCKED", "MISSING"}:
            conclusion = "BLOCKED"
        latest_payload = {
            "id": latest.id,
            "decision_at": latest.decision_at,
            "decision_type": latest.decision_type,
            "final_rating": latest.final_rating,
            "portfolio_action": latest.portfolio_action,
            "conclusion": conclusion,
            "holding_actions": latest.holding_decisions_json or [],
            "candidate_actions": latest.candidate_decisions_json or [],
            "quality": latest.quality_status,
            "confidence": latest.confidence,
            "analysis_run_id": latest.analysis_run_id,
        }
    return {
        "status": "AVAILABLE" if latest else "MISSING",
        "today_count": len(rows),
        "latest": latest_payload,
        "final_action": latest_payload["conclusion"] if latest_payload else "NO_ACTION",
    }


def _execution_section(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> dict[str, Any]:
    local_date = _local(cutoff).date()
    rows = db.execute(select(TradeLedgerEntry).where(
        TradeLedgerEntry.user_id == user_id, TradeLedgerEntry.portfolio_id == portfolio_id, TradeLedgerEntry.trade_date == local_date,
        TradeLedgerEntry.status == "CONFIRMED",
        TradeLedgerEntry.executed_at <= cutoff,
        TradeLedgerEntry.available_at <= cutoff,
    ).order_by(TradeLedgerEntry.executed_at.desc(), TradeLedgerEntry.id.desc())).scalars().all()
    run_ids = {row.analysis_run_id for row in rows if row.analysis_run_id is not None}
    outcome_map: dict[tuple[int, str], DecisionOutcome] = {}
    if run_ids:
        outcome_rows = db.execute(select(DecisionOutcome, DecisionMemory).join(
            DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id,
        ).where(
            DecisionMemory.user_id == user_id,
            DecisionMemory.portfolio_id == portfolio_id,
            DecisionMemory.analysis_run_id.in_(run_ids),
            DecisionOutcome.available_at.is_not(None),
            DecisionOutcome.available_at <= cutoff,
        ).order_by(DecisionOutcome.horizon_trading_days.asc(), DecisionOutcome.id.asc())).all()
        for outcome, memory in outcome_rows:
            key = (memory.analysis_run_id, str(outcome.target_key or ""))
            outcome_map.setdefault(key, outcome)
    items = []
    for row in rows:
        outcome = outcome_map.get((row.analysis_run_id, str(row.security_code or ""))) if row.analysis_run_id else None
        evidence = (outcome.source_refs_json or {}).get("execution") if outcome else {}
        items.append({
            "id": row.id,
            "code": row.security_code,
            "name": row.security_name,
            "side": row.side,
            "qty": row.quantity,
            "price": row.price,
            "fees": row.fees,
            "taxes": row.taxes,
            "executed_at": row.executed_at,
            "linked_decision": row.analysis_run_id,
            "execution_alignment": outcome.execution_alignment if outcome else "UNRESOLVED",
            "actual_execution_return": outcome.actual_execution_return if outcome else None,
            "net_execution_return": outcome.net_execution_return if outcome else None,
            "alignment_reason_codes": evidence.get("alignment_reason_codes", []) if isinstance(evidence, dict) else [],
        })
    return {"status": "AVAILABLE", "today_count": len(rows), "items": items}


def _memory_section(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> dict[str, Any]:
    local_date = _local(cutoff).date()
    review = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == user_id,
        DailyReviewRun.portfolio_id == portfolio_id,
        DailyReviewRun.trade_date <= local_date,
        *_review_as_of_conditions(cutoff),
    ).order_by(DailyReviewRun.trade_date.desc(), DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
    decisions = db.execute(select(DecisionMemory).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
        DecisionMemory.trade_date == local_date,
        DecisionMemory.created_at <= cutoff,
        DecisionMemory.available_at <= cutoff,
        DecisionMemory.decision_at <= cutoff,
    ).order_by(DecisionMemory.decision_at.desc(), DecisionMemory.id.desc()).limit(200)).scalars().all()
    outcomes = db.execute(select(DecisionOutcome).join(DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id).where(
        DecisionMemory.user_id == user_id, DecisionMemory.portfolio_id == portfolio_id,
        DecisionMemory.created_at <= cutoff,
        DecisionMemory.available_at <= cutoff,
        DecisionOutcome.available_at.is_not(None), DecisionOutcome.available_at <= cutoff,
    )).scalars().all()
    matured_today = [row for row in outcomes if row.available_at and _local(row.available_at).date() == local_date]
    analogue_count = db.scalar(select(func.count(DecisionMemory.id)).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
        DecisionMemory.trade_date < local_date,
        DecisionMemory.created_at <= cutoff,
        DecisionMemory.available_at <= cutoff,
    )) or 0
    return {
        "status": "AVAILABLE" if review or decisions or matured_today else "MISSING",
        "review": ({
            "id": review.id,
            "trade_date": review.trade_date,
            "status": review.status,
            "quality": review.quality_status,
            "decision_count": review.decision_count,
            "actual_execution_count": review.actual_execution_count,
            "outcomes_matured_today": len(matured_today),
            "completed_at": review.completed_at,
            "review_stale": bool(review.review_stale),
            "last_refreshed_at": review.last_refreshed_at,
            "refresh_count": review.refresh_count,
        } if review else None),
        "today_decisions": len(decisions),
        "today_decision_items": [{
            "id": row.id,
            "decision_at": row.decision_at,
            "decision_type": row.decision_type,
            "final_rating": row.final_rating,
            "portfolio_action": row.portfolio_action,
            "quality": row.quality_status,
            "confidence": row.confidence,
            "analysis_run_id": row.analysis_run_id,
        } for row in decisions],
        "outcomes_matured": len(matured_today),
        "outcomes_matured_today": [{
            "id": row.id,
            "decision_memory_id": row.decision_memory_id,
            "target_key": row.target_key,
            "horizon_trading_days": row.horizon_trading_days,
            "status": row.status,
            "quality_status": row.quality_status,
            "available_at": row.available_at,
            "excess_return": row.excess_return,
        } for row in matured_today],
        "historical_analogue_count": int(analogue_count),
    }


def _health_section(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    cutoff: datetime,
    portfolio: dict[str, Any],
    market: dict[str, Any],
    candidate: dict[str, Any],
    memory: dict[str, Any],
) -> dict[str, Any]:
    local = _local(cutoff)
    local_date = local.date()
    calendar_service = TradingCalendarService(db)
    calendar_row = db.execute(select(TradingCalendar).where(
        TradingCalendar.market == "CN",
        TradingCalendar.trade_date == local_date,
    )).scalar_one_or_none()
    market_open = bool(calendar_row and calendar_row.is_open and calendar_service.is_market_session(local))
    security_count = int(db.scalar(select(func.count(SecurityMaster.id))) or 0)
    securities_status = "OK" if security_count else "UNKNOWN"
    providers = db.execute(select(ProviderHealth).where(
        ProviderHealth.data_type == "quote",
    ).order_by(ProviderHealth.updated_at.desc(), ProviderHealth.id.desc())).scalars().all()
    primary_name = getattr(settings, "MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER", "")
    fallback_names = tuple(getattr(settings, "MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS", ()) or ())
    provider_by_name = {row.provider_name: row for row in providers}
    primary = provider_by_name.get(primary_name)
    fallback_rows = [provider_by_name.get(name) for name in fallback_names]

    def is_healthy(row: ProviderHealth | None) -> bool:
        return row is not None and str(row.status).upper() == "HEALTHY"

    if primary is None and not any(row is not None for row in fallback_rows):
        provider_status = "UNKNOWN"
    elif is_healthy(primary) and all(row is None or is_healthy(row) for row in fallback_rows):
        provider_status = "OK"
    elif is_healthy(primary) or any(is_healthy(row) for row in fallback_rows):
        provider_status = "DEGRADED"
    else:
        provider_status = "BLOCKED"

    monitor = get_realtime_monitor().status()
    monitor_runtime_status = str(monitor.get("status") or "UNKNOWN").upper()
    in_session = time(9, 30) <= local.time() < time(11, 30) or time(13, 0) <= local.time() < time(15, 0)
    monitor_status = "OK" if not in_session or monitor_runtime_status in {"RUNNING", "STARTED"} else "DEGRADED"
    operational_run = db.execute(select(DailyOperationalRun).where(
        DailyOperationalRun.user_id == user_id,
        DailyOperationalRun.portfolio_id == portfolio_id,
        DailyOperationalRun.trade_date == local_date,
        DailyOperationalRun.last_tick_at <= cutoff,
    ).order_by(DailyOperationalRun.last_tick_at.desc(), DailyOperationalRun.id.desc()).limit(1)).scalar_one_or_none()
    scheduler_running = scheduler_service.scheduler_running()
    scheduler_status = "OK" if scheduler_running or (operational_run is not None and settings.SCHEDULER_ENABLED) else "DEGRADED" if settings.SCHEDULER_ENABLED else "UNKNOWN"
    daily_bar = db.execute(select(DailyBarCache).where(
        DailyBarCache.market == "CN",
        DailyBarCache.available_at.is_not(None),
        DailyBarCache.available_at <= cutoff,
    ).order_by(DailyBarCache.trade_date.desc(), DailyBarCache.available_at.desc(), DailyBarCache.id.desc()).limit(1)).scalar_one_or_none()
    daily_bar_freshness = _freshness(
        daily_bar.available_at if daily_bar else None,
        cutoff=cutoff,
        limit_seconds=7 * 24 * 60 * 60,
    ) if daily_bar else "MISSING"
    review = memory.get("review") or {}
    review_raw_status = "DEGRADED" if review.get("review_stale") else review.get("status", "MISSING")
    try:
        from ..governance.service import governance_health

        governance = governance_health(db)
    except Exception:  # noqa: BLE001
        governance = {"status": "BLOCKED", "reasons": ["GOVERNANCE_HEALTH_UNAVAILABLE"], "active": None}
    components = [
        {
            "name": "TradingCalendar",
            "status": _health_status("OK" if calendar_row else "BLOCKED", mandatory=True, market_open=market_open),
            "mandatory": True,
            "detail": {
                "market": "CN",
                "trade_date": local_date,
                "is_open": bool(calendar_row and calendar_row.is_open),
                "previous_trading_day": calendar_service.previous_trading_day(local_date),
                "next_trading_day": calendar_service.next_trading_day(local_date),
            },
        },
        {"name": "SecurityMaster", "status": _health_status(securities_status, mandatory=True, market_open=market_open), "mandatory": True, "detail": {"security_count": security_count}},
        {
            "name": "Primary/Fallback Provider",
            "status": _health_status(provider_status, mandatory=True, market_open=market_open),
            "mandatory": True,
            "primary": primary_name,
            "fallback": list(fallback_names),
            "items": [{
                "provider": row.provider_name,
                "data_type": row.data_type,
                "status": str(row.status).upper(),
                "last_error": row.last_error,
                "last_success_at": row.last_success_at,
                "last_failure_at": row.last_failure_at,
                "updated_at": row.updated_at,
            } for row in providers],
        },
        {"name": "Market Score", "status": _health_status(market.get("health_status") or market.get("freshness"), mandatory=True, market_open=market_open), "mandatory": True, "detail": {"freshness": market.get("freshness"), "quality_status": market.get("quality_status"), "source": market.get("market_score_source")}},
        {"name": "Portfolio Snapshot", "status": _health_status(portfolio.get("freshness"), mandatory=True, market_open=market_open), "mandatory": True, "detail": {"snapshot_id": portfolio.get("snapshot_id"), "snapshot_time": portfolio.get("snapshot_time")}},
        {"name": "CandidateRun", "status": _health_status(candidate.get("status") if candidate.get("status") == "STALE" else candidate.get("freshness"), mandatory=False), "mandatory": False, "detail": {"run_id": candidate.get("run_id"), "age_seconds": candidate.get("age_seconds")}},
        {"name": "Scheduler", "status": _health_status(scheduler_status, mandatory=False), "mandatory": False, "detail": {"enabled": settings.SCHEDULER_ENABLED, "running": scheduler_running, "last_tick_at": operational_run.last_tick_at if operational_run else None}},
        {"name": "Realtime Monitor", "status": _health_status(monitor_status, mandatory=False), "mandatory": False, "detail": {**monitor, "expected_in_session": in_session}},
        {"name": "Daily Review", "status": _health_status(review_raw_status, mandatory=False), "mandatory": False, "detail": {"id": review.get("id"), "review_stale": review.get("review_stale", False), "refresh_count": review.get("refresh_count", 0)}},
        {"name": "DailyBar", "status": _health_status(daily_bar_freshness, mandatory=False), "mandatory": False, "detail": {"trade_date": daily_bar.trade_date if daily_bar else None, "available_at": daily_bar.available_at if daily_bar else None}},
        {"name": "Parameter Governance", "status": _health_status(governance.get("status"), mandatory=False), "mandatory": False, "detail": {"reasons": governance.get("reasons") or [], "active_version": (governance.get("active") or {}).get("version"), "active_version_id": (governance.get("active") or {}).get("id")}},
    ]
    overall = _overall(components)
    return {
        "status": overall,
        "overall": overall,
        "as_of": local,
        "market_open": market_open,
        "components": components,
        "severity_values": ["OK", "DEGRADED", "BLOCKED", "UNKNOWN"],
    }


def build_daily_dashboard(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> dict[str, Any]:
    portfolio_row = db.execute(select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)).scalar_one_or_none()
    if portfolio_row is None:
        raise ValueError("portfolio_not_found")
    cutoff = _cutoff(as_of)
    local = cutoff.replace(tzinfo=UTC).astimezone(CHINA_TZ)
    calendar = TradingCalendarService(db)
    current_review = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == user_id,
        DailyReviewRun.portfolio_id == portfolio_id,
        DailyReviewRun.trade_date == local.date(),
        *_review_as_of_conditions(cutoff),
    ).order_by(DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
    state = derive_workflow_state(
        db,
        as_of=local,
        review_complete=bool(current_review and current_review.status == "COMPLETED"),
        review_stale=bool(current_review and current_review.review_stale),
    )
    portfolio = _section(lambda: _portfolio_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    market = _section(lambda: _market_section(db, cutoff=cutoff))
    candidates = _section(lambda: _candidate_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff, portfolio=portfolio))
    triggers = _section(lambda: _trigger_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    analysis = _section(lambda: _analysis_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    decisions = _section(lambda: _decision_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    executions = _section(lambda: _execution_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    memory = _section(lambda: _memory_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    timeline = _section(lambda: operational_timeline(db, portfolio_id=portfolio_id, user_id=user_id, as_of=local))
    health = _section(lambda: _health_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff, portfolio=portfolio, market=market, candidate=candidates, memory=memory))
    final_action = (
        (decisions.get("latest") or {}).get("conclusion")
        or (analysis.get("latest") or {}).get("portfolio_action")
        or (analysis.get("latest") or {}).get("final_rating")
        or "NO_ACTION"
    )
    if str(final_action).lower() in {"no_action", "hold", "hold_only", "watch", "watch_only"}:
        final_action = "NO_ACTION"
    elif str(final_action).lower() in {"blocked", "block"}:
        final_action = "BLOCKED"
    notification_state = db.execute(select(DailyOperationalRun.notification_state_json).where(
        DailyOperationalRun.user_id == user_id, DailyOperationalRun.portfolio_id == portfolio_id, DailyOperationalRun.trade_date == local.date(),
    ).order_by(DailyOperationalRun.id.desc()).limit(1)).scalar_one_or_none()
    notifications = _section(lambda: list_operating_notifications(
        db,
        user_id=user_id,
        portfolio_id=portfolio_id,
        as_of=cutoff,
        limit=20,
    ))
    return _iso({
        "as_of": local,
        "trade_date": local.date(),
        "market_open": bool(calendar.is_trading_day(local.date()) and calendar.is_market_session(local)),
        "workflow_state": state.value,
        "market": market,
        "portfolio": portfolio,
        "candidates": candidates,
        "triggers": triggers,
        "analysis": analysis,
        "decisions": {**decisions, "final_action": final_action or "NO_ACTION"},
        "executions": executions,
        "memory": memory,
        "data_health": health,
        "timeline": timeline,
        "notifications": {**notifications, "state": notification_state or {}},
    })


def build_dashboard_timeline(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> dict[str, Any]:
    _require_portfolio(db, user_id=user_id, portfolio_id=portfolio_id)
    cutoff = _cutoff(as_of)
    return _iso(operational_timeline(db, user_id=user_id, portfolio_id=portfolio_id, as_of=cutoff))


def build_dashboard_health(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> dict[str, Any]:
    _require_portfolio(db, user_id=user_id, portfolio_id=portfolio_id)
    cutoff = _cutoff(as_of)
    portfolio = _section(lambda: _portfolio_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    market = _section(lambda: _market_section(db, cutoff=cutoff))
    candidates = _section(lambda: _candidate_section(
        db,
        user_id=user_id,
        portfolio_id=portfolio_id,
        cutoff=cutoff,
        portfolio=portfolio,
    ))
    memory = _section(lambda: _memory_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    return _iso(_health_section(
        db,
        user_id=user_id,
        portfolio_id=portfolio_id,
        cutoff=cutoff,
        portfolio=portfolio,
        market=market,
        candidate=candidates,
        memory=memory,
    ))


def _require_portfolio(db: Session, *, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.execute(select(Portfolio).where(
        Portfolio.id == portfolio_id,
        Portfolio.user_id == user_id,
    )).scalar_one_or_none()
    if row is None:
        raise ValueError("portfolio_not_found")
    return row


def build_dashboard_diagnostics(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Return concise read-only diagnostics for operators and support tooling."""

    _require_portfolio(db, user_id=user_id, portfolio_id=portfolio_id)
    cutoff = _cutoff(as_of)
    local = _local(cutoff)
    builders = {
        "market": lambda: _market_section(db, cutoff=cutoff),
        "portfolio": lambda: _portfolio_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff),
        "candidates": lambda: _candidate_section(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            cutoff=cutoff,
            portfolio=_portfolio_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff),
        ),
        "analysis": lambda: _analysis_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff),
        "decisions": lambda: _decision_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff),
        "executions": lambda: _execution_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff),
        "memory": lambda: _memory_section(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff),
        "timeline": lambda: operational_timeline(db, user_id=user_id, portfolio_id=portfolio_id, as_of=cutoff),
        "notifications": lambda: list_operating_notifications(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            as_of=cutoff,
            limit=20,
        ),
    }
    sections: dict[str, dict[str, Any]] = {}
    for name, builder in builders.items():
        try:
            payload = builder()
            sections[name] = {"status": payload.get("status", "AVAILABLE")} if isinstance(payload, dict) else {"status": "OK"}
        except Exception as exc:  # diagnostics must expose partial failures
            sections[name] = {"status": "ERROR", "error": str(exc)[:300]}
    health = build_dashboard_health(db, user_id=user_id, portfolio_id=portfolio_id, as_of=cutoff)
    issues = [
        {"component": item.get("name"), "status": item.get("status"), "detail": item.get("detail")}
        for item in health.get("components", [])
        if item.get("status") != "OK"
    ]
    return _iso({
        "as_of": local,
        "trade_date": local.date(),
        "workflow_state": derive_workflow_state(db, as_of=local).value,
        "read_only": True,
        "no_lookahead": True,
        "health": health,
        "sections": sections,
        "issues": issues,
    })


__all__ = ["build_daily_dashboard", "build_dashboard_diagnostics", "build_dashboard_health", "build_dashboard_timeline"]
