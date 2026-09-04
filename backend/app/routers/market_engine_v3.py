"""Authenticated read-only/query endpoints for Phase C market state."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..market.providers.factory import build_kline_provider
from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache, MarketMetricSnapshot, MarketScoreSnapshot
from ..market_models import SecurityMaster, TradingCalendar
from ..services.daily_bar_cache import sync_daily_bar_cache
from ..services.market_engine import MarketEngine
from ..v2_dependencies import get_current_user, require_daily_bar_sync
from ..v2_models import User

router = APIRouter(prefix="/api/v3/market", tags=["v3-market-engine"])
CHINA_TZ = ZoneInfo("Asia/Shanghai")
_daily_bar_sync_lock = Lock()
_daily_bar_sync_state: dict[str, Any] = {"status": "idle"}


class MarketCalculateRequest(BaseModel):
    # Market identity, quote, and history rows are server-owned inputs.  The
    # service still accepts injected rows for deterministic/unit tests, but a
    # public API caller may only trigger a calculation over canonical backend
    # data; accepting arbitrary rows here would let any JWT user persist a
    # forged shared market state.  Derived quality and provenance facts are
    # also forbidden and are always recomputed by ``MarketEngine``.
    model_config = ConfigDict(extra="forbid")

    trade_date: date | None = None
    captured_at: datetime | None = None
    persist: bool = True


class DailyBarSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codes: list[str] = Field(default_factory=list, max_length=100_000)
    as_of: date | None = None
    limit: int = Field(default=260, ge=20, le=500)


def _server_now() -> datetime:
    return datetime.now(CHINA_TZ)


def _accepted_trade_date(db: Session, moment: datetime) -> date:
    day = moment.astimezone(CHINA_TZ).date()
    current = db.execute(
        select(TradingCalendar).where(
            TradingCalendar.market == "CN",
            TradingCalendar.trade_date == day,
        )
    ).scalar_one_or_none()
    if current is not None and current.is_open:
        return day
    previous = db.execute(
        select(TradingCalendar.trade_date)
        .where(
            TradingCalendar.market == "CN",
            TradingCalendar.trade_date < day,
            TradingCalendar.is_open.is_(True),
        )
        .order_by(TradingCalendar.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if previous is None:
        raise HTTPException(status_code=503, detail="calendar_not_initialized")
    return previous


def _run_daily_bar_sync(codes: list[str], as_of: date, limit: int) -> None:
    with _daily_bar_sync_lock:
        _daily_bar_sync_state.update(
            status="running",
            requested_codes=len(codes),
            as_of=as_of.isoformat(),
            started_at=_server_now().isoformat(),
        )
    db = SessionLocal()
    try:
        result = sync_daily_bar_cache(
            db,
            build_kline_provider(),
            codes,
            as_of=as_of,
            bootstrap_limit=limit,
        )
        with _daily_bar_sync_lock:
            _daily_bar_sync_state.update(result, completed_at=_server_now().isoformat())
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        with _daily_bar_sync_lock:
            _daily_bar_sync_state.update(
                status="failed",
                error=exc.__class__.__name__,
                completed_at=_server_now().isoformat(),
            )
    finally:
        db.close()


def _score_payload(row: MarketScoreSnapshot, metric: MarketMetricSnapshot | None = None) -> dict[str, Any]:
    metadata = row.metadata_json or {}
    universe = metadata.get("universe") or {}
    core = metadata.get("core_metrics") or (metric.metrics_json if metric else {}) or {}
    components = {
        "breadth": row.breadth_score,
        "trend": row.trend_score,
        "liquidity": row.liquidity_score,
        "profitability": row.profitability_score,
        "diffusion": row.diffusion_score,
        "crowding": row.crowding_score,
        "tail_risk": row.tail_risk_score,
    }
    return {
        "snapshot_id": row.snapshot_id,
        "metric_snapshot_id": row.metric_snapshot_id,
        "trade_date": row.trade_date,
        "captured_at": row.captured_at,
        "raw_score": row.raw_score,
        "display_score": row.display_score,
        "regime": row.regime,
        "confidence": row.confidence,
        "quality_status": row.quality_status,
        "status": "FROZEN" if row.is_frozen else row.quality_status,
        "is_frozen": row.is_frozen,
        "freeze_reason": row.freeze_reason,
        "components": components,
        "core_metrics": core,
        "universe": universe,
        "positive_drivers": row.positive_drivers_json or [],
        "negative_drivers": row.negative_drivers_json or [],
        "calculation_version": row.calculation_version,
        "score_config_version": row.score_config_version,
        "universe_rule_version": row.universe_rule_version,
    }


@router.get("/state")
def get_market_state(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = db.execute(
        select(MarketScoreSnapshot).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="market_state_unavailable")
    metric = db.execute(
        select(MarketMetricSnapshot).where(MarketMetricSnapshot.snapshot_id == row.metric_snapshot_id)
    ).scalar_one_or_none()
    return _score_payload(row, metric)


@router.get("/state/history")
def get_market_state_history(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    statement = select(MarketScoreSnapshot)
    if start_date:
        statement = statement.where(MarketScoreSnapshot.trade_date >= start_date)
    if end_date:
        statement = statement.where(MarketScoreSnapshot.trade_date <= end_date)
    rows = db.execute(statement.order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(limit)).scalars().all()
    return [_score_payload(row) for row in rows]


@router.get("/metrics")
def get_market_metrics(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = db.execute(
        select(MarketMetricSnapshot).order_by(MarketMetricSnapshot.captured_at.desc(), MarketMetricSnapshot.id.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="market_metrics_unavailable")
    return {
        "snapshot_id": row.snapshot_id,
        "market_snapshot_id": row.market_snapshot_id,
        "trade_date": row.trade_date,
        "captured_at": row.captured_at,
        "universe": {
            "total": row.universe_total,
            "included": row.included_count,
            "excluded": row.excluded_count,
            "coverage": row.coverage,
            "exclusion_counts": row.exclusion_counts_json or {},
            "rule_version": row.universe_rule_version,
        },
        "core_metrics": row.metrics_json or {},
        "components": {
            "breadth": row.breadth_metrics_json,
            "trend": row.trend_metrics_json,
            "liquidity": row.liquidity_metrics_json,
            "profitability": row.profitability_metrics_json,
            "diffusion": row.diffusion_metrics_json,
            "crowding": row.crowding_metrics_json,
            "tail_risk": row.tail_risk_metrics_json,
        },
        "quality_status": row.quality_status,
        "confidence": row.confidence,
        "calculation_version": row.calculation_version,
    }


@router.get("/median-index")
def get_median_index(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=250, ge=1, le=5000),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    statement = select(AllAMedianIndexDaily)
    if start_date:
        statement = statement.where(AllAMedianIndexDaily.trade_date >= start_date)
    if end_date:
        statement = statement.where(AllAMedianIndexDaily.trade_date <= end_date)
    rows = db.execute(statement.order_by(AllAMedianIndexDaily.trade_date.desc()).limit(limit)).scalars().all()
    return [
        {
            "trade_date": row.trade_date,
            "median_return": row.median_return,
            "index_value": row.index_value,
            "eligible_count": row.eligible_count,
            "quality_status": row.quality_status,
            "calculation_version": row.calculation_version,
            "available_at": row.available_at,
        }
        for row in reversed(rows)
    ]


@router.post("/calculate")
def calculate_market_state_endpoint(
    payload: MarketCalculateRequest,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    server_now = _server_now()
    accepted_day = _accepted_trade_date(db, server_now)
    requested_day = payload.trade_date or accepted_day
    if requested_day > accepted_day:
        raise HTTPException(status_code=422, detail="future_trade_date_not_allowed")
    if payload.persist and requested_day != accepted_day:
        raise HTTPException(status_code=422, detail="historical_persist_requires_archived_snapshot")
    if payload.captured_at is not None:
        capture = payload.captured_at
        if capture.tzinfo is None:
            capture = capture.replace(tzinfo=CHINA_TZ)
        capture_local = capture.astimezone(CHINA_TZ)
        if not payload.persist and capture_local.date() != requested_day:
            raise HTTPException(status_code=422, detail="captured_at_trade_date_mismatch")
        if capture > server_now + timedelta(minutes=5):
            raise HTTPException(status_code=422, detail="future_capture_not_allowed")
    try:
        return MarketEngine(db).calculate(
            trade_date=requested_day,
            captured_at=server_now if payload.persist else payload.captured_at,
            persist=payload.persist,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail="market_engine_calculation_failed") from exc


@router.get("/daily-bars/status")
def daily_bar_cache_status(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row_count = db.scalar(select(func.count()).select_from(DailyBarCache)) or 0
    latest = db.scalar(select(func.max(DailyBarCache.trade_date)))
    with _daily_bar_sync_lock:
        runtime = dict(_daily_bar_sync_state)
    return {
        **runtime,
        "row_count": int(row_count),
        "latest_trade_date": latest.isoformat() if latest else None,
        "initialized": bool(row_count),
    }


@router.post("/daily-bars/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_daily_bars_endpoint(
    payload: DailyBarSyncRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_daily_bar_sync),
) -> dict[str, Any]:
    """Explicit operator bootstrap/incremental refresh of the local QFQ cache."""

    codes = list(dict.fromkeys(payload.codes))
    if not codes:
        codes = list(
            db.execute(
                select(SecurityMaster.code).where(
                    SecurityMaster.market == "CN",
                    SecurityMaster.exchange.in_(("SSE", "SZSE")),
                    SecurityMaster.security_type == "STOCK",
                    SecurityMaster.status == "ACTIVE",
                )
            ).scalars()
        )
    if not codes:
        raise HTTPException(status_code=409, detail="security_master_not_initialized")
    with _daily_bar_sync_lock:
        if _daily_bar_sync_state.get("status") in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="daily_bar_sync_already_running")
        _daily_bar_sync_state.clear()
        _daily_bar_sync_state.update(status="queued", requested_codes=len(codes))
    day = payload.as_of or _server_now().date()
    background_tasks.add_task(_run_daily_bar_sync, codes, day, payload.limit)
    return {"status": "queued", "requested_codes": len(codes), "as_of": day.isoformat()}


__all__ = ["router", "MarketCalculateRequest", "DailyBarSyncRequest"]
