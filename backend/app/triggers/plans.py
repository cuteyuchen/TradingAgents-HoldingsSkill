"""Extract deterministic TriggerPlans from structured analysis output."""
from __future__ import annotations

from math import isfinite
from typing import Any

from sqlalchemy.orm import Session

from ..market.codes import normalize_security_code
from ..market_models import SecurityMaster
from ..trigger_models import TriggerPlan

_CONDITIONS = {
    "price_below": ("price", "LT", "PRICE_BELOW"),
    "price_above": ("price", "GT", "PRICE_ABOVE"),
    "pct_change_below": ("pct_change", "LT", "PCT_CHANGE_BELOW"),
    "pct_change_above": ("pct_change", "GT", "PCT_CHANGE_ABOVE"),
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _condition(value: Any) -> tuple[str, str, str] | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _CONDITIONS.get(text)


def _iter_holding_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("holdings") or []
    return [row for row in rows if isinstance(row, dict)]


def extract_trigger_plans_from_analysis_run(
    db: Session,
    analysis_run: Any,
    *,
    source_type: str = "ANALYSIS_RUN",
) -> list[TriggerPlan]:
    """Convert only explicit structured holding conditions into plans.

    Natural-language ``trigger`` strings are intentionally ignored.
    """
    payload = getattr(analysis_run, "structured_result_json", None) or {}
    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        return []
    user_id = getattr(analysis_run, "user_id", None)
    snapshot_id = getattr(analysis_run, "portfolio_snapshot_id", None)
    portfolio_id = getattr(getattr(analysis_run, "job", None), "portfolio_id", None)
    if portfolio_id is None:
        from ..v2_models import PortfolioSnapshot

        snapshot = db.get(PortfolioSnapshot, snapshot_id) if snapshot_id else None
        portfolio_id = snapshot.portfolio_id if snapshot else None
    plans: list[TriggerPlan] = []
    for row in _iter_holding_rows(result):
        code = normalize_security_code(row.get("code"))
        if not code:
            continue
        exists = db.query(SecurityMaster.id).filter(
            SecurityMaster.market == "CN", SecurityMaster.code == code, SecurityMaster.status == "ACTIVE"
        ).first()
        if exists is None:
            continue
        candidates: list[dict[str, Any]] = []
        trigger = row.get("trigger")
        if isinstance(trigger, dict):
            candidates.append(trigger)
        if isinstance(row.get("trigger_plan"), dict):
            candidates.append(row["trigger_plan"])
        if row.get("condition") is not None:
            candidates.append(row)
        for item in candidates:
            condition = _condition(item.get("condition") or item.get("operator"))
            threshold = _number(item.get("threshold", item.get("trigger_price", item.get("price"))))
            if condition is None or threshold is None:
                continue
            metric, operator, trigger_type = condition
            plans.append(TriggerPlan(
                user_id=user_id, portfolio_id=portfolio_id, scope="PORTFOLIO",
                target_type="HOLDING", target_key=code, trigger_type=trigger_type,
                metric=metric, operator=operator, threshold=threshold,
                priority=str(item.get("priority") or "P1").upper(),
                debounce_cycles=max(1, int(item.get("debounce_cycles") or 2)),
                debounce_seconds=max(0, int(item.get("debounce_seconds") or 180)),
                cooldown_seconds=max(0, int(item.get("cooldown_seconds") or 1800)),
                enabled=True, source_type=source_type, source_id=str(analysis_run.id),
                metadata_json={"action_context": item.get("action_context") or row.get("action")},
            ))
    return plans


def refresh_trigger_plans_from_run(db: Session, analysis_run: Any, *, mode: str = "standard") -> list[TriggerPlan]:
    if str(mode).lower() == "fast":
        return []
    portfolio_id = getattr(getattr(analysis_run, "job", None), "portfolio_id", None)
    if portfolio_id is None:
        return []
    old = db.query(TriggerPlan).filter(
        TriggerPlan.portfolio_id == portfolio_id, TriggerPlan.source_type == "ANALYSIS_RUN", TriggerPlan.enabled.is_(True)
    ).all()
    for plan in old:
        plan.enabled = False
    plans = extract_trigger_plans_from_analysis_run(db, analysis_run)
    db.add_all(plans)
    db.flush()
    return plans


__all__ = ["extract_trigger_plans_from_analysis_run", "refresh_trigger_plans_from_run"]
