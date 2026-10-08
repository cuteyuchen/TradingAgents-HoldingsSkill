"""Fuyao-backed evidence/context endpoints for the daily workbench."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..market.fuyao_analytics import (
    FuyaoAnalyticsService,
    calculate_portfolio_contributions,
    probe_capabilities,
)
from ..market.providers.factory import build_critical_quote_provider
from ..market_engine_models import MarketMetricSnapshot, MarketScoreSnapshot
from ..services.holding_identity import RESOLVED, audit_holding_item
from ..v2_dependencies import get_current_user
from ..v2_models import HoldingItem, Portfolio, PortfolioSnapshot, User


router = APIRouter(prefix="/api/v3/fuyao", tags=["v3-fuyao"])


def _portfolio(db: Session, *, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.execute(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return row


def _score_context(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(MarketScoreSnapshot)
        .order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {}
    metric = db.execute(
        select(MarketMetricSnapshot).where(MarketMetricSnapshot.snapshot_id == row.metric_snapshot_id)
    ).scalar_one_or_none()
    core = metric.metrics_json if metric is not None else {}
    return {
        "snapshot_id": row.snapshot_id,
        "trade_date": row.trade_date,
        "captured_at": row.captured_at,
        "display_score": row.display_score,
        "raw_score": row.raw_score,
        "regime": row.regime,
        "quality_status": row.quality_status,
        "core_metrics": core or {},
        "universe": {
            "total": metric.universe_total if metric is not None else None,
            "included": metric.included_count if metric is not None else None,
            "coverage": metric.coverage if metric is not None else None,
        },
    }


def _resolved_holding_rows(db: Session, holdings: list[HoldingItem]) -> list[dict[str, Any]]:
    """Build quote inputs only from holdings with verified identity authority."""

    rows: list[dict[str, Any]] = []
    for row in holdings:
        audit = audit_holding_item(db, row)
        if audit.get("status") != RESOLVED or not audit.get("code"):
            continue
        rows.append(
            {
                "code": audit["code"],
                "name": audit.get("display_name") or row.name,
                "qty": row.qty,
                "market_value": row.market_value,
                "cost": row.cost,
            }
        )
    return rows


@router.get("/status")
def fuyao_status(
    probe: bool = Query(default=False),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Expose configured/capability state without ever returning the API key."""

    return probe_capabilities(probe=probe)


@router.get("/market-brief")
def market_brief(
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    brief = FuyaoAnalyticsService().market_brief(_score_context(db), force_refresh=refresh)
    return {
        "brief": brief.to_dict(),
        "score": _score_context(db),
        "production_score_changed": False,
        "all_a_median_definition": "eligible_all_a_daily_pct_return_median_compound_from_1000",
        "top5_definition": "ceil(eligible_universe_count*0.05)_turnover_share",
    }


@router.get("/securities/{code}")
def security_context(
    code: str,
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return FuyaoAnalyticsService().security_context(code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/contribution")
def portfolio_contribution(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    snapshot = db.execute(
        select(PortfolioSnapshot)
        .where(
            PortfolioSnapshot.portfolio_id == portfolio_id,
            PortfolioSnapshot.user_id == current_user.id,
            PortfolioSnapshot.status == "confirmed",
        )
        .order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="confirmed_snapshot_not_found")
    holdings = db.execute(
        select(HoldingItem).where(HoldingItem.snapshot_id == snapshot.id).order_by(HoldingItem.id.asc())
    ).scalars().all()
    holding_rows = _resolved_holding_rows(db, holdings)
    codes = [str(row["code"]) for row in holding_rows]
    provider = build_critical_quote_provider()
    quotes = provider.get_quotes(codes) if codes else {}
    calculated = calculate_portfolio_contributions(holding_rows, quotes)
    run_metadata = getattr(provider, "get_run_metadata", lambda: {})()
    return {
        "portfolio_id": portfolio_id,
        "snapshot_id": snapshot.id,
        "snapshot_time": snapshot.snapshot_time,
        "confirmed": True,
        "provider": run_metadata.get("provider") or getattr(provider, "name", None),
        "provider_attempts": run_metadata.get("provider_attempts") or [],
        **calculated,
    }


__all__ = ["router"]
