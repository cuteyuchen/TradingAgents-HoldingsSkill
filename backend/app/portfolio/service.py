"""Portfolio Engine orchestration without network work inside write transactions."""
from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..market.session import MarketDataBasis, MarketSessionService
from ..services.trading_calendar import CHINA_TZ
from ..market_engine_models import MarketScoreSnapshot
from ..portfolio_models import PortfolioRiskSnapshot
from ..v2_models import PortfolioSnapshot
from .config import PORTFOLIO_ENGINE_VERSION, PORTFOLIO_RISK_VERSION
from .constraints import build_portfolio_constraints
from .risk import build_portfolio_state, calculate_risk_metrics, latest_confirmed_snapshot
from .snapshot_diff import calculate_snapshot_diff, reconcile_snapshot_diff_with_ledger


def _as_of(value: datetime | None) -> datetime:
    moment = value or datetime.now(UTC)
    moment = moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment
    if moment > datetime.now(UTC).replace(tzinfo=None):
        raise ValueError("as_of_cannot_be_in_the_future")
    return moment


def latest_market_state(db: Session, *, as_of: datetime) -> dict[str, Any]:
    moment = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of
    row = db.execute(select(MarketScoreSnapshot).where(
        MarketScoreSnapshot.market == "CN",
        MarketScoreSnapshot.captured_at <= moment,
    ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        return {"available": False, "is_frozen": False, "quality_status": "MISSING", "regime": None}
    local = moment.replace(tzinfo=UTC).astimezone(CHINA_TZ)
    session = MarketSessionService(db).resolve_session(local)
    expected_day = (session.previous_trading_date
                    if session.data_basis == MarketDataBasis.PREVIOUS_SESSION_CLOSE.value
                    else session.trading_date)
    max_age = max(settings.MARKET_SNAPSHOT_FRESHNESS_SECONDS, settings.MARKET_SCORE_INTERVAL_MINUTES * 120)
    reasons = []
    age = (moment - row.captured_at).total_seconds()
    if session.is_trading_day is None:
        reasons.append("MARKET_CALENDAR_UNAVAILABLE")
    if expected_day is None or row.trade_date != expected_day:
        reasons.append("MARKET_SCORE_TRADE_DATE_MISMATCH")
    if session.data_basis == MarketDataBasis.LIVE.value:
        captured = row.captured_at.replace(tzinfo=UTC).astimezone(CHINA_TZ)
        # Scheduled lunch contributes no live trading age, but never exempts
        # a stale morning score or a score from another trading date.
        lunch_start = datetime.combine(local.date(), time(11, 30), tzinfo=CHINA_TZ)
        lunch_end = datetime.combine(local.date(), time(13), tzinfo=CHINA_TZ)
        paused = max(0.0, (min(local, lunch_end) - max(captured, lunch_start)).total_seconds())
        age -= paused
        if age > max_age:
            reasons.append("MARKET_SCORE_STALE")
    elif expected_day is not None:
        close = datetime.combine(expected_day, time(15), tzinfo=CHINA_TZ).astimezone(UTC).replace(tzinfo=None)
        if row.captured_at < close:
            reasons.append("MARKET_SCORE_CLOSE_NOT_CONFIRMED")
    quality = str(row.quality_status or "MISSING").upper()
    available = not reasons and quality in {"VALID", "DEGRADED"} and not row.is_frozen
    return {
        "available": available,
        "snapshot_id": row.snapshot_id,
        "captured_at": row.captured_at.isoformat(),
        "trade_date": row.trade_date.isoformat(),
        "display_score": row.display_score,
        "regime": row.regime,
        "confidence": row.confidence,
        "quality_status": row.quality_status,
        "is_frozen": row.is_frozen,
        "freeze_reason": row.freeze_reason,
        "freshness": "FRESH" if not reasons else "STALE",
        "age_seconds": max(0.0, age),
        "max_age_seconds": max_age if session.data_basis == MarketDataBasis.LIVE.value else None,
        "data_basis": session.data_basis,
        "expected_trade_date": expected_day.isoformat() if expected_day else None,
        "reason_codes": reasons,
    }


def _calculation_key(
    snapshot_id: int,
    as_of: datetime,
    market_snapshot_id: str | None,
    account_version: str | None = None,
) -> str:
    return (
        f"{snapshot_id}:{as_of.replace(second=0, microsecond=0).isoformat()}:"
        f"{market_snapshot_id or 'none'}:{account_version or 'no-account-version'}:{PORTFOLIO_RISK_VERSION}"
    )


def _persist_risk_snapshot(
    db: Session,
    *,
    state: dict[str, Any],
    risk: dict[str, Any],
    constraints: dict[str, Any],
    market_state: dict[str, Any],
    as_of: datetime,
) -> PortfolioRiskSnapshot:
    calculation_key = _calculation_key(
        state["snapshot_id"], as_of, market_state.get("snapshot_id"), state.get("account_version")
    )
    row = db.execute(select(PortfolioRiskSnapshot).where(
        PortfolioRiskSnapshot.calculation_key == calculation_key
    )).scalar_one_or_none()
    values = {
        "user_id": state["user_id"],
        "portfolio_id": state["portfolio_id"],
        "portfolio_snapshot_id": state["snapshot_id"],
        "market_score_snapshot_id": market_state.get("snapshot_id"),
        "as_of": as_of,
        "total_assets": state.get("total_assets"),
        "market_value": state.get("total_market_value"),
        "cash_ratio": state.get("cash_ratio"),
        "gross_exposure": state.get("gross_exposure"),
        "top1_weight": risk.get("top1_weight"),
        "top3_weight": risk.get("top3_weight"),
        "top5_weight": risk.get("top5_weight"),
        "hhi": risk.get("hhi"),
        "portfolio_vol_20": risk.get("portfolio_vol_20"),
        "portfolio_vol_60": risk.get("portfolio_vol_60"),
        "weighted_average_correlation": risk.get("weighted_average_correlation"),
        "max_pairwise_correlation": risk.get("max_pairwise_correlation"),
        "unclassified_weight": sum(float(item.get("weight") or 0.0) for item in risk.get("positions") or [] if not item.get("security_type")),
        "risk_flags_json": list(dict.fromkeys([*(state.get("risk_flags") or []), *(constraints.get("risk_flags") or [])])),
        "position_metrics_json": risk.get("positions") or [],
        "correlation_summary_json": {
            "average_pairwise_correlation": risk.get("average_pairwise_correlation"),
            "weighted_average_correlation": risk.get("weighted_average_correlation"),
            "max_pairwise_correlation": risk.get("max_pairwise_correlation"),
            "high_correlation_pairs": risk.get("high_correlation_pairs") or [],
            "correlation_clusters": risk.get("correlation_clusters") or [],
            "etf_lookthrough_available": False,
        },
        "confidence": risk.get("confidence") or 0.0,
        "quality_status": risk.get("quality_status") or "DEGRADED",
        "calculation_version": PORTFOLIO_RISK_VERSION,
    }
    if row is None:
        row = PortfolioRiskSnapshot(calculation_key=calculation_key, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.flush()
    return row


def calculate_portfolio_risk(
    db: Session,
    *,
    portfolio_id: int,
    user_id: int,
    as_of: datetime | None = None,
    persist: bool = False,
    snapshot: PortfolioSnapshot | None = None,
    quote_loader: Any = None,
    quote_rows: Any = None,
) -> dict[str, Any]:
    """Calculate a bounded deterministic state from confirmed, server-owned facts."""

    requested_as_of = as_of is not None
    moment = _as_of(as_of)
    snapshot = snapshot or latest_confirmed_snapshot(db, portfolio_id=portfolio_id, as_of=moment)
    if snapshot is None or snapshot.user_id != user_id:
        raise ValueError("confirmed_snapshot_not_found")
    state = build_portfolio_state(
        db,
        portfolio_id=portfolio_id,
        as_of=moment,
        snapshot=snapshot,
        quote_loader=quote_loader,
        quote_rows=quote_rows,
        allow_live_quotes=not requested_as_of,
    )
    state["user_id"] = user_id
    market_state = latest_market_state(db, as_of=moment)
    risk = calculate_risk_metrics(db, state=state, as_of=moment)
    if not market_state.get("available"):
        risk["confidence"] = max(0.0, float(risk["confidence"]) - 15.0)
        if risk["quality_status"] == "VALID":
            risk["quality_status"] = "DEGRADED"
    elif market_state.get("is_frozen"):
        risk["confidence"] = max(0.0, float(risk["confidence"]) - 15.0)
    enriched_state = {**state, "positions": risk.get("positions") or []}
    constraints = build_portfolio_constraints(enriched_state, market_state)
    result = {
        "calculation_version": PORTFOLIO_ENGINE_VERSION,
        "state": enriched_state,
        "risk": risk,
        "constraints": constraints,
        "market_state": market_state,
    }
    if persist:
        persisted = _persist_risk_snapshot(
            db,
            state=state,
            risk=risk,
            constraints=constraints,
            market_state=market_state,
            as_of=moment,
        )
        result["risk_snapshot_id"] = persisted.id
    return result


def portfolio_context_for_analysis(
    db: Session,
    *,
    snapshot: PortfolioSnapshot,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Provide deterministic facts to the LLM without treating them as orders."""

    quote_rows = (market or {}).get("quotes") if isinstance(market, dict) else None
    calculated = calculate_portfolio_risk(
        db,
        portfolio_id=snapshot.portfolio_id,
        user_id=snapshot.user_id,
        as_of=None,
        persist=False,
        snapshot=snapshot,
        quote_rows=quote_rows,
    )
    state, risk, constraints, market_state = (
        calculated["state"], calculated["risk"], calculated["constraints"], calculated["market_state"]
    )
    previous = db.query(PortfolioSnapshot).filter(
        PortfolioSnapshot.portfolio_id == snapshot.portfolio_id,
        PortfolioSnapshot.status == "confirmed",
        PortfolioSnapshot.id != snapshot.id,
        PortfolioSnapshot.snapshot_time <= snapshot.snapshot_time,
    ).order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).first()
    snapshot_diff = None
    if previous is not None:
        raw_diff = calculate_snapshot_diff(previous, snapshot)
        raw_diff["reconciliation"] = reconcile_snapshot_diff_with_ledger(
            db, before=previous, after=snapshot, diff=raw_diff
        )
        snapshot_diff = raw_diff
    history_rows = db.query(PortfolioSnapshot).filter(
        PortfolioSnapshot.portfolio_id == snapshot.portfolio_id,
        PortfolioSnapshot.status == "confirmed",
        PortfolioSnapshot.snapshot_time <= snapshot.snapshot_time,
    ).order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).limit(5).all()
    return {
        "interpretation": "这是后端确定性组合风险事实和动作约束，不是交易指令；模型不得覆盖 hard_cap、max_additional_weight 或 max_sellable_qty。",
        "snapshot_id": state["snapshot_id"],
        "snapshot_total_assets": state.get("snapshot_total_assets"),
        "current_estimated_total_assets": state.get("current_estimated_total_assets"),
        "repo_or_standard_bond_value": state.get("repo_or_standard_bond_value"),
        "reserve_assets": state.get("reserve_assets"),
        "spendable_cash": state.get("cash"),
        "cash_ratio": state.get("cash_ratio"),
        "reserve_ratio": state.get("reserve_ratio"),
        "gross_exposure": state.get("gross_exposure"),
        "top1_weight": risk.get("top1_weight"),
        "top3_weight": risk.get("top3_weight"),
        "hard_cap_breaches": [flag for flag in constraints.get("risk_flags") or [] if str(flag).startswith("HARD_CAP_BREACH")],
        "high_correlation_pairs": risk.get("high_correlation_pairs") or [],
        "correlation_clusters": risk.get("correlation_clusters") or [],
        "position_constraints": constraints.get("positions") or [],
        "keep_scores": [
            {"code": row.get("code"), "keep_score": row.get("keep_score")}
            for row in risk.get("positions") or []
        ],
        "market_regime": market_state.get("regime"),
        "market_state_available": market_state.get("available"),
        "market_quality_status": market_state.get("quality_status"),
        "market_state_frozen": market_state.get("is_frozen"),
        "market_state_freshness": market_state.get("freshness"),
        "market_state_reason_codes": market_state.get("reason_codes") or [],
        "market_state_captured_at": market_state.get("captured_at"),
        "market_state_trade_date": market_state.get("trade_date"),
        "portfolio_quality": risk.get("quality_status"),
        "portfolio_confidence": risk.get("confidence"),
        "account_version": state.get("account_version"),
        "account_derivation": state.get("account_derivation"),
        "pending_sell_proceeds": state.get("pending_sell_proceeds"),
        "frozen_cash": state.get("frozen_cash"),
        "snapshot_diff": snapshot_diff,
        "snapshot_history": [
            {
                "snapshot_id": row.id,
                "snapshot_time": row.snapshot_time.isoformat(),
                "total_assets": row.total_assets,
                "cash": row.broker_available_cash if row.broker_available_cash is not None else row.corrected_unused_funds,
                "position_count": len(row.holdings),
            }
            for row in history_rows
        ],
    }


__all__ = ["calculate_portfolio_risk", "latest_market_state", "portfolio_context_for_analysis"]
