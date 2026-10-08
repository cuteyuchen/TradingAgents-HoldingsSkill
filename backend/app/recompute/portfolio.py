"""Historical EOD Portfolio Decision Gate deterministic recompute.

The portfolio layer consumes only the frozen PIT snapshot plus the recomputed
Market/Candidate outputs.  It never calls ``calculate_portfolio_risk`` because
that production helper may fall back to live quotes.  Cash is fail-closed: an
absent broker cash figure never becomes a guessed balance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from ..market.codes import normalize_security_code
from ..portfolio.constraints import build_portfolio_constraints
from ..portfolio.decision_gate import apply_portfolio_decision_gate
from ..portfolio.snapshot_diff import snapshot_reserve_assets
from ..v2_models import PortfolioSnapshot
from .config import RecomputeCapability
from .dataset import RecomputePitDataset, eod_cutoff


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _market_state(result: Mapping[str, Any] | None) -> dict[str, Any]:
    if result is None:
        return {
            "available": False,
            "is_frozen": False,
            "quality_status": "MISSING",
            "regime": None,
            "confidence": 0.0,
        }
    quality = str(result.get("quality_status") or "MISSING").upper()
    return {
        "available": quality in {"VALID", "DEGRADED"},
        "snapshot_id": None,
        "trade_date": result.get("trade_date"),
        "display_score": result.get("display_score"),
        "regime": result.get("regime"),
        "confidence": result.get("confidence"),
        "quality_status": quality,
        "is_frozen": bool(result.get("is_frozen")),
        "freeze_reason": result.get("freeze_reason"),
    }


def build_historical_portfolio_state(
    dataset: RecomputePitDataset,
    *,
    day: date,
    cutoff: datetime,
    snapshot: PortfolioSnapshot,
    market_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build one server-owned historical portfolio state from PIT facts only."""

    quotes = {row["code"]: row for row in dataset.quote_rows(day, cutoff)}
    states = dataset.lifecycle_states(day, cutoff)
    etf_metadata = dataset.etf_metadata_by_code(day, cutoff)
    holdings = dataset.holdings_for(snapshot.id)
    positions: list[dict[str, Any]] = []
    risk_flags: list[str] = []
    accepted_quotes = 0
    for item in holdings:
        code = normalize_security_code(item.code)
        if not code:
            continue
        state = states.get(code) or {}
        security_type = str(state.get("security_type") or "UNKNOWN").upper()
        etf_category = (etf_metadata.get(code) or {}).get("category")
        quote = quotes.get(code)
        quote_quality = str((quote or {}).get("quality_status") or "MISSING").upper()
        price = _number((quote or {}).get("price")) if quote is not None else None
        qty = _number(item.qty)
        market_value = _number(item.market_value)
        if market_value is None and qty is not None and price is not None:
            market_value = qty * price
        if quote_quality in {"VALID", "DEGRADED"} and price is not None:
            accepted_quotes += 1
        else:
            risk_flags.append(f"QUOTE_{quote_quality}:{code}")
        if security_type == "UNKNOWN":
            risk_flags.append("SECURITY_CLASSIFICATION_UNKNOWN")
        positions.append({
            "code": code,
            "name": item.name or state.get("name"),
            "security_type": security_type,
            "etf_category": etf_category,
            "qty": item.qty,
            "available_qty": item.available_qty,
            "current_price": price,
            "quote_quality": quote_quality,
            "market_value": market_value,
            "snapshot_market_value": item.market_value,
            "cost": item.cost,
            "flags": list(dict.fromkeys(risk_flags[-1:])),
        })

    reserve_assets = snapshot_reserve_assets(snapshot)
    cash = _number(snapshot.broker_available_cash)
    valued = [float(row["market_value"]) for row in positions if row.get("market_value") is not None]
    complete_valuation = len(valued) == len(positions)
    market_value = sum(valued) if complete_valuation else None
    snapshot_total = _number(snapshot.total_assets)
    total_assets = snapshot_total if snapshot_total is not None and snapshot_total > 0 else (
        market_value + reserve_assets
        if market_value is not None and reserve_assets is not None
        else market_value
    )
    cash_ratio = cash / total_assets if cash is not None and total_assets and total_assets > 0 else None
    reserve_ratio = reserve_assets / total_assets if reserve_assets is not None and total_assets and total_assets > 0 else None
    for row in positions:
        row["weight"] = (
            float(row["market_value"]) / total_assets
            if row.get("market_value") is not None and total_assets and total_assets > 0
            else None
        )
    gross_exposure = sum(float(row["weight"] or 0.0) for row in positions) if total_assets else None
    if cash is None:
        risk_flags.append("INSUFFICIENT_CASH_DATA")
    risk_flags.append("HISTORICAL_SNAPSHOT_VALUATION")
    if positions and accepted_quotes == 0:
        quality = "DEGRADED"
    elif cash is None or not complete_valuation:
        quality = "BLOCKED" if cash is None else "DEGRADED"
    else:
        quality = "DEGRADED"
    state = {
        "portfolio_id": snapshot.portfolio_id,
        "user_id": snapshot.user_id,
        "snapshot_id": snapshot.id,
        "snapshot_time": snapshot.snapshot_time.isoformat(),
        "total_assets": total_assets,
        "snapshot_total_assets": snapshot_total,
        "current_estimated_total_assets": market_value + reserve_assets
        if market_value is not None and reserve_assets is not None
        else total_assets,
        "total_market_value": market_value,
        "cash": cash,
        "spendable_cash": cash,
        "reserve_assets": reserve_assets,
        "cash_ratio": cash_ratio,
        "reserve_ratio": reserve_ratio,
        "gross_exposure": gross_exposure,
        "position_count": len(positions),
        "positions": positions,
        "quote_coverage": accepted_quotes / len(positions) if positions else 1.0,
        "classification_coverage": sum(
            1 for row in positions if str(row.get("security_type") or "UNKNOWN") != "UNKNOWN"
        ) / len(positions) if positions else 1.0,
        "risk_flags": list(dict.fromkeys(risk_flags)),
        "quality_status": quality,
        "data_quality": {
            "quote_coverage": accepted_quotes / len(positions) if positions else 1.0,
            "missing_quote_count": max(0, len(positions) - accepted_quotes),
            "classification_coverage": sum(
                1 for row in positions if str(row.get("security_type") or "UNKNOWN") != "UNKNOWN"
            ) / len(positions) if positions else 1.0,
        },
    }
    market_state = _market_state(market_result)
    constraints = build_portfolio_constraints(state, market_state)
    portfolio_quality = "BLOCKED" if cash is None else quality
    return {
        **state,
        "market_state": market_state,
        "market_regime": market_state.get("regime"),
        "market_state_available": market_state.get("available"),
        "market_quality_status": market_state.get("quality_status"),
        "market_state_frozen": market_state.get("is_frozen"),
        "position_constraints": constraints.get("positions") or [],
        "constraints": constraints,
        "portfolio_quality": portfolio_quality,
        "portfolio_confidence": round(
            100.0 * (
                0.70 * (accepted_quotes / len(positions) if positions else 1.0)
                + 0.15 * float(market_state.get("confidence") or 0.0) / 100.0
                + 0.15
            ),
            2,
        ),
        "cash_known": cash is not None,
        "hhi": sum(float(row["weight"] or 0.0) ** 2 for row in positions),
    }


@dataclass
class HistoricalPortfolioRecomputeResult:
    trade_date: date
    as_of: datetime
    capability: str
    status: str
    portfolio_snapshot_id: int | None
    portfolio_context: dict[str, Any]
    candidate_actions: list[dict[str, Any]]
    final_action: str
    blocking_reasons: list[str]
    decision_gate: dict[str, Any]
    coverage: float
    quality_status: str
    source_ids: tuple[str, ...] = field(default_factory=tuple)
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trade_date"] = self.trade_date.isoformat()
        payload["as_of"] = self.as_of.isoformat()
        return payload


def recompute_portfolio_dates(
    dataset: RecomputePitDataset,
    *,
    dates: Iterable[date],
    market_results: Mapping[date, Any] | Iterable[Any],
    candidate_results: Mapping[date, Any] | Iterable[Any],
    parameter_snapshot: Mapping[str, Any] | None,
) -> list[HistoricalPortfolioRecomputeResult]:
    """Re-run the production decision gate for every requested EOD date."""

    market_by_date = {
        result.trade_date: result
        for result in (market_results.values() if isinstance(market_results, Mapping) else market_results)
    }
    candidate_by_date = {
        result.trade_date: result
        for result in (candidate_results.values() if isinstance(candidate_results, Mapping) else candidate_results)
    }
    results: list[HistoricalPortfolioRecomputeResult] = []
    for day in dates:
        cutoff = eod_cutoff(day)
        market_result = market_by_date.get(day)
        snapshot = dataset.latest_snapshot(day, cutoff)
        if snapshot is None:
            results.append(HistoricalPortfolioRecomputeResult(
                trade_date=day,
                as_of=cutoff,
                capability=RecomputeCapability.DATA_GAP,
                status="NO_ACTION",
                portfolio_snapshot_id=None,
                portfolio_context={},
                candidate_actions=[],
                final_action="NO_ACTION",
                blocking_reasons=["HISTORICAL_PORTFOLIO_SNAPSHOT_MISSING"],
                decision_gate={},
                coverage=0.0,
                quality_status="MISSING",
                source_ids=tuple(dataset.source_ids()),
                limitations=["historical confirmed portfolio snapshot is unavailable for this date"],
            ))
            continue
        state = build_historical_portfolio_state(
            dataset,
            day=day,
            cutoff=cutoff,
            snapshot=snapshot,
            market_result=market_result.as_dict() if market_result is not None else None,
        )
        candidate_result = candidate_by_date.get(day)
        candidate_rows = list((candidate_result.as_dict() if candidate_result is not None else {}).get("candidates") or [])
        action_rows = [
            row for row in candidate_rows
            if str(row.get("stage") or "").upper() == "ACTION"
        ]
        gate_input = {
            "holdings": [],
            "candidates": [
                {
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "candidate_engine_stage": "ACTION",
                    "stage": "ACTION",
                    "portfolio_fit": row.get("portfolio_fit") or {},
                    "funding_mode": row.get("funding_mode"),
                }
                for row in action_rows
            ],
        }
        gated = apply_portfolio_decision_gate(gate_input, portfolio_context=state)
        decision_gate = gated.get("decision_gate") or {}
        raw_action = str(decision_gate.get("portfolio_action") or "NO_ACTION").upper()
        final_action = "ACTION" if raw_action == "ACTION" else "NO_ACTION"
        candidate_actions = [
            {
                "code": row.get("code"),
                "stage": row.get("stage"),
                "gate_status": row.get("portfolio_gate") or "NOT_EVALUATED_V3",
                "portfolio_gate_reasons": row.get("portfolio_gate_reasons") or [],
            }
            for row in action_rows
        ]
        blocking_reasons = list(dict.fromkeys(
            (decision_gate.get("blocking_reasons") or [])
            + ([] if state.get("cash_known") else ["INSUFFICIENT_CASH_DATA"])
        ))
        quality = str(state.get("portfolio_quality") or "DEGRADED").upper()
        capability = RecomputeCapability.PARTIAL_PIT_RECOMPUTE
        limitations = ["historical portfolio valuation is snapshot-based and cash is fail-closed"]
        if not state.get("cash_known"):
            limitations.append("historical broker cash is unavailable; portfolio remains fail-closed")
        results.append(HistoricalPortfolioRecomputeResult(
            trade_date=day,
            as_of=cutoff,
            capability=str(capability),
            status=final_action,
            portfolio_snapshot_id=snapshot.id,
            portfolio_context={
                "snapshot_id": state.get("snapshot_id"),
                "total_assets": state.get("total_assets"),
                "cash_ratio": state.get("cash_ratio"),
                "spendable_cash": state.get("spendable_cash"),
                "position_count": state.get("position_count"),
                "quote_coverage": state.get("quote_coverage"),
                "portfolio_quality": quality,
                "portfolio_confidence": state.get("portfolio_confidence"),
            },
            candidate_actions=candidate_actions,
            final_action=final_action,
            blocking_reasons=blocking_reasons,
            decision_gate=decision_gate,
            coverage=float(state.get("quote_coverage") or 0.0),
            quality_status=quality,
            source_ids=tuple(dataset.source_ids()),
            limitations=limitations,
        ))
    return results


__all__ = [
    "HistoricalPortfolioRecomputeResult",
    "build_historical_portfolio_state",
    "recompute_portfolio_dates",
]
