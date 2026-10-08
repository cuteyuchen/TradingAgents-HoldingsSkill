"""Fuyao-backed evidence/context endpoints for the daily workbench."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, SecretStr, model_validator
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
from ..security import encrypt_secret
from ..services.market_provider_settings import can_manage_market_settings, fuyao_config_status
from ..v2_dependencies import get_current_user
from ..v2_models import HoldingItem, MarketProviderSetting, Portfolio, PortfolioSnapshot, User


router = APIRouter(prefix="/api/v3/fuyao", tags=["v3-fuyao"])


class FuyaoConfigUpdate(BaseModel):
    api_key: SecretStr

    @model_validator(mode="before")
    @classmethod
    def validate_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("api_key"), str):
            # FastAPI's default validation response includes the raw input.
            # Reject malformed credential payloads without reflecting secrets.
            raise HTTPException(status_code=422, detail="请以文本格式提供 API Key。")
        return value


@router.get("/config")
def read_fuyao_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return fuyao_config_status(db, current_user.id)


def _require_config_owner(db: Session, user: User) -> None:
    if not can_manage_market_settings(db, user.id):
        raise HTTPException(status_code=403, detail="只有实例管理员（首个注册账户）可以修改共享行情密钥。")


@router.put("/config")
def update_fuyao_config(
    payload: FuyaoConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_config_owner(db, current_user)
    key = payload.api_key.get_secret_value().strip()
    if not key or len(key) > 4096 or any(char.isspace() or ord(char) < 32 for char in key):
        # Do not include submitted credential values in validation errors.
        raise HTTPException(status_code=422, detail="请输入有效的 API Key（不含空白，长度不超过 4096 个字符）。")
    row = db.get(MarketProviderSetting, "fuyao")
    if row is None:
        row = MarketProviderSetting(provider="fuyao", updated_by=current_user.id)
        db.add(row)
    row.encrypted_api_key = encrypt_secret(key)
    row.updated_by = current_user.id
    db.commit()
    return fuyao_config_status(db, current_user.id)


@router.delete("/config")
def reset_fuyao_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_config_owner(db, current_user)
    row = db.get(MarketProviderSetting, "fuyao")
    if row is not None:
        db.delete(row)
        db.commit()
    return fuyao_config_status(db, current_user.id)


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
