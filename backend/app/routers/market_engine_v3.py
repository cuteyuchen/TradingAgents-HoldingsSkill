"""Authenticated read-only/query endpoints for Phase C market state."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..market.engine.history import LegacyMarketDataHistoryProvider
from ..market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot, MarketScoreSnapshot
from ..services.market_engine import MarketEngine
from ..v2_dependencies import get_current_user
from ..v2_models import User

router = APIRouter(prefix="/api/v3/market", tags=["v3-market-engine"])


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
    market_snapshot_id: str | None = Field(default=None, max_length=64)
    persist: bool = True


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
    try:
        return MarketEngine(db, history_provider=LegacyMarketDataHistoryProvider()).calculate(
            trade_date=payload.trade_date,
            captured_at=payload.captured_at,
            market_snapshot_id=payload.market_snapshot_id,
            persist=payload.persist,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail="market_engine_calculation_failed") from exc


__all__ = ["router", "MarketCalculateRequest"]
