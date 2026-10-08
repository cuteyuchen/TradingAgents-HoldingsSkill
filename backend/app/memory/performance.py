"""Account P&L, advice market effects and counterfactuals have separate ledgers."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import utc_now_naive as utc_now
from ..history.time import CHINA_TZ, to_utc_naive
from ..market_engine_models import DailyBarCache
from ..market_models import TradingCalendar
from ..v2_models import PortfolioSnapshot
from .facts import ledger_facts_at
from .models import DecisionMemory, DecisionOutcome
from .outcomes import calculate_decision_outcome

PERFORMANCE_VERSION = "account-review-p4-v1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _day(value: datetime) -> date:
    return to_utc_naive(value).replace(tzinfo=UTC).astimezone(CHINA_TZ).date()


def _close(day: date) -> datetime:
    return datetime.combine(day, time(15), tzinfo=CHINA_TZ).astimezone(UTC).replace(tzinfo=None)


def _previous_day(db: Session, day: date) -> date | None:
    return db.scalar(select(TradingCalendar.trade_date).where(
        TradingCalendar.market == "CN", TradingCalendar.is_open.is_(True),
        TradingCalendar.trade_date < day,
    ).order_by(TradingCalendar.trade_date.desc()).limit(1))


def account_performance(db: Session, *, user_id: int, portfolio_id: int, trade_date: date,
                        as_of: datetime) -> dict[str, Any]:
    cutoff = to_utc_naive(as_of)
    previous_day = _previous_day(db, trade_date)
    snapshots = list(db.scalars(select(PortfolioSnapshot).where(
        PortfolioSnapshot.user_id == user_id, PortfolioSnapshot.portfolio_id == portfolio_id,
        PortfolioSnapshot.status == "confirmed", PortfolioSnapshot.created_at <= cutoff,
        PortfolioSnapshot.snapshot_time <= cutoff,
    ).order_by(PortfolioSnapshot.snapshot_time, PortfolioSnapshot.id)).all())
    snapshots = [row for row in snapshots if _day(row.snapshot_time) <= trade_date]
    opening = next((row for row in reversed(snapshots) if previous_day and
                    _day(row.snapshot_time) == previous_day and row.snapshot_time >= _close(previous_day)), None)
    closing = next((row for row in reversed(snapshots) if _day(row.snapshot_time) == trade_date
                    and row.snapshot_time >= _close(trade_date)), None)
    anchor = snapshots[0] if snapshots else None
    reasons: list[str] = []
    result: dict[str, Any] = {
        "status": "INCOMPLETE", "net_pnl": None, "return_rate": None,
        "opening_equity": _number(opening.total_assets) if opening else None,
        "closing_equity": _number(closing.total_assets) if closing else None,
        "external_flow": None, "realized_pnl": None, "unrealized_pnl": None,
        "total_pnl_since_anchor": None, "fees": 0.0, "taxes": 0.0,
        "dividends": 0.0, "cash_in": 0.0, "cash_out": 0.0,
        "cost_basis_start": anchor.snapshot_time.isoformat() if anchor else None,
        "cost_basis_method": "MOVING_WEIGHTED_AVERAGE",
        "pnl_scope": "COST_PNL_SINCE_ANCHOR; NET_PNL_BETWEEN_CLOSE_SNAPSHOTS",
        "return_method": "MODIFIED_DIETZ", "positions": [],
        "source_refs": {"anchor_snapshot_id": anchor.id if anchor else None,
                        "opening_snapshot_id": opening.id if opening else None,
                        "closing_snapshot_id": closing.id if closing else None},
        "reason_codes": reasons,
    }
    if not previous_day:
        reasons.append("PREVIOUS_TRADING_DAY_MISSING")
    if not opening or not closing:
        reasons.append("CLOSE_SNAPSHOT_PAIR_MISSING")
    if anchor is None:
        reasons.append("INITIAL_ACCOUNT_MISSING")
        return result
    # A snapshot supplies initial cost; no inferred trades fill quantity gaps.
    positions = {}
    for item in anchor.holdings:
        if not item.code:
            reasons.append("INITIAL_SECURITY_ID_MISSING")
            continue
        qty, cost = _number(item.qty), _number(item.cost)
        positions[item.code] = {"code": item.code, "quantity": qty, "average_cost": cost,
                               "realized_pnl": 0.0, "unrealized_pnl": None, "reason_codes": []}
        if qty is None or (qty > 0 and (cost is None or cost < 0)):
            reasons.append(f"INITIAL_COST_OR_QUANTITY_MISSING:{item.code}")
    end_time = closing.snapshot_time if closing else min(cutoff, _close(trade_date))
    entries = [entry for entry in ledger_facts_at(db, user_id=user_id, portfolio_id=portfolio_id, as_of=cutoff)
               if anchor.snapshot_time < entry.executed_at <= end_time]
    result["source_refs"]["ledger_entry_ids"] = [entry.id for entry in entries]
    flows = []
    for entry in entries:
        amount = _number(entry.net_amount)
        if amount is None:
            amount = _number(entry.gross_amount)
        if entry.currency != "CNY":
            reasons.append(f"CURRENCY_CONVERSION_MISSING:{entry.id}")
            continue
        if entry.entry_type == "TRADE":
            qty, price = _number(entry.quantity), _number(entry.price)
            fees, taxes = _number(entry.fees), _number(entry.taxes)
            if fees is None or taxes is None:
                reasons.append(f"TRADE_COSTS_MISSING:{entry.id}")
            fees, taxes = fees or 0.0, taxes or 0.0
            result["fees"] += fees
            result["taxes"] += taxes
            if not entry.security_code or qty is None or qty <= 0 or price is None or price <= 0:
                reasons.append(f"TRADE_FACT_MISSING:{entry.id}")
                continue
            row = positions.setdefault(entry.security_code, {"code": entry.security_code, "quantity": 0.0,
                "average_cost": 0.0, "realized_pnl": 0.0, "unrealized_pnl": None, "reason_codes": []})
            old_qty, old_cost = row["quantity"], row["average_cost"]
            if old_qty is None or old_cost is None:
                row["reason_codes"].append("COST_BASIS_UNKNOWN")
                continue
            if entry.side == "BUY":
                row["quantity"] = old_qty + qty
                row["average_cost"] = (old_qty * old_cost + qty * price + fees + taxes) / (old_qty + qty)
            elif entry.side == "SELL" and qty <= old_qty + 1e-9:
                row["quantity"] = old_qty - qty
                row["realized_pnl"] += qty * (price - old_cost) - fees - taxes
            else:
                row["reason_codes"].append("SELL_EXCEEDS_RECORDED_HOLDINGS")
                reasons.append(f"TRADE_QUANTITY_UNRECONCILED:{entry.id}")
        elif entry.entry_type in {"CASH_IN", "CASH_OUT", "TRANSFER_IN", "TRANSFER_OUT"}:
            if entry.security_code and _number(entry.quantity):
                reasons.append(f"SECURITY_TRANSFER_COST_MISSING:{entry.id}")
                continue
            if amount is None:
                reasons.append(f"EXTERNAL_FLOW_AMOUNT_MISSING:{entry.id}")
                continue
            direction = 1 if entry.entry_type in {"CASH_IN", "TRANSFER_IN"} else -1
            result["cash_in" if direction == 1 else "cash_out"] += abs(amount)
            flows.append((entry.executed_at, direction * abs(amount)))
        elif entry.entry_type in {"DIVIDEND", "FEE", "TAX"}:
            if amount is None:
                reasons.append(f"CASH_EVENT_AMOUNT_MISSING:{entry.id}")
                continue
            result[{"DIVIDEND": "dividends", "FEE": "fees", "TAX": "taxes"}[entry.entry_type]] += abs(amount)
        else:
            reasons.append(f"UNCLASSIFIED_ACCOUNT_EVENT:{entry.id}")
    daily_flows = [(stamp, value) for stamp, value in flows if opening and closing
                   and opening.snapshot_time < stamp <= closing.snapshot_time]
    if opening and closing:
        result["external_flow"] = sum(value for _, value in daily_flows)
        if result["opening_equity"] is None or result["closing_equity"] is None:
            reasons.append("ACCOUNT_EQUITY_MISSING")
        elif not any(code.startswith(("EXTERNAL_FLOW", "CURRENCY", "SECURITY_TRANSFER")) for code in reasons):
            result["net_pnl"] = result["closing_equity"] - result["opening_equity"] - result["external_flow"]
            duration = (closing.snapshot_time - opening.snapshot_time).total_seconds()
            capital = result["opening_equity"] + sum(value * (closing.snapshot_time - stamp).total_seconds() / duration
                                                    for stamp, value in daily_flows) if duration > 0 else None
            result["return_rate"] = result["net_pnl"] / capital if capital and capital > 0 else None
    latest = snapshots[-1]
    latest_qty = {item.code: _number(item.qty) for item in latest.holdings if item.code}
    marks = {item.code: _number(item.screenshot_price) for item in closing.holdings} if closing else {}
    # Raw prices are required for real-money cost P&L; QFQ is never mixed with a cash fill.
    bars = db.scalars(select(DailyBarCache).where(
        DailyBarCache.market == "CN", DailyBarCache.trade_date == trade_date,
        DailyBarCache.adjustment.in_(("NONE", "RAW", "UNADJUSTED")),
        DailyBarCache.available_at <= cutoff,
        DailyBarCache.quality_status.in_(("VALID", "DEGRADED")),
    ).order_by(DailyBarCache.available_at, DailyBarCache.id)).all()
    for bar in bars:
        marks[bar.code] = _number(bar.close)
    result["source_refs"]["raw_bar_ids"] = [bar.id for bar in bars if bar.code in positions]
    for code, row in positions.items():
        mark, qty, cost = marks.get(code), row["quantity"], row["average_cost"]
        row["mark_price"] = mark
        # New confirmed snapshots are independent reconciliation evidence.
        if latest.snapshot_time == end_time and abs((latest_qty.get(code) or 0) - (qty or 0)) > 1e-8:
            row["reason_codes"].append("SNAPSHOT_LEDGER_QUANTITY_MISMATCH")
        row["unrealized_pnl"] = 0.0 if qty == 0 else (qty * (mark - cost)
            if qty is not None and cost is not None and mark is not None and mark > 0 else None)
        if row["unrealized_pnl"] is None:
            row["reason_codes"].append("COST_OR_RAW_MARK_MISSING")
        reasons.extend(f"{reason}:{code}" for reason in row["reason_codes"])
        result["positions"].append(row)
    for code, qty in latest_qty.items():
        if latest.snapshot_time == end_time and code not in positions and qty:
            reasons.append(f"SNAPSHOT_LEDGER_QUANTITY_MISMATCH:{code}")
    cost_gaps = [code for code in reasons if code != "CLOSE_SNAPSHOT_PAIR_MISSING" and code != "PREVIOUS_TRADING_DAY_MISSING"]
    if not cost_gaps:
        result["realized_pnl"] = sum(row["realized_pnl"] for row in positions.values())
        result["unrealized_pnl"] = sum(row["unrealized_pnl"] or 0 for row in positions.values())
        standalone_costs = sum(abs(_number(entry.net_amount) or _number(entry.gross_amount) or 0)
                               for entry in entries if entry.entry_type in {"FEE", "TAX"})
        result["total_pnl_since_anchor"] = (result["realized_pnl"] + result["unrealized_pnl"]
                                            + result["dividends"] - standalone_costs)
    result["reason_codes"] = sorted(set(reasons))
    result["status"] = "VALID" if not reasons else "INCOMPLETE"
    return result


def condition_state(db: Session, memory: DecisionMemory, target: dict, *, as_of: datetime) -> str:
    source = target.get("source") or {}
    conditional = target.get("recommended_action") == "conditional_add" or any(
        source.get(key) for key in ("conditions", "trigger_conditions", "trigger_condition"))
    if not conditional:
        return "NOT_APPLICABLE"
    entries = ledger_facts_at(db, user_id=memory.user_id, portfolio_id=memory.portfolio_id, as_of=as_of)
    if any(entry.analysis_run_id == memory.analysis_run_id and entry.security_code == target.get("target_key")
           and entry.entry_type == "TRADE" and entry.executed_at >= memory.decision_at for entry in entries):
        return "EXECUTED"
    # P3 records explicit, timestamped observations from fresh provider quotes.
    from ..trigger_models import TriggerEvent, TriggerPlan
    plans = db.scalars(select(TriggerPlan).where(
        TriggerPlan.user_id == memory.user_id, TriggerPlan.portfolio_id == memory.portfolio_id,
        TriggerPlan.source_type == "DAILY_ANALYSIS", TriggerPlan.source_id == str(memory.analysis_run_id),
        TriggerPlan.target_key == target.get("target_key"), TriggerPlan.created_at <= as_of,
    )).all()
    observations = []
    for plan in plans:
        for review in (plan.metadata_json or {}).get("reviews", []):
            observation = review.get("condition_observation") or {}
            try:
                stamp = to_utc_naive(datetime.fromisoformat(str(observation.get("observed_at"))))
                reviewed = to_utc_naive(datetime.fromisoformat(str(review.get("reviewed_at"))))
            except ValueError:
                continue
            if stamp <= as_of and reviewed <= as_of and isinstance(observation.get("met"), bool):
                observations.append((stamp, observation["met"]))
    if any(met for _, met in observations):
        return "TRIGGERED"
    if observations:
        return "NOT_TRIGGERED"
    # Text cannot establish that a condition fired; use an explicit trigger event.
    event = db.scalar(select(TriggerEvent).where(
        TriggerEvent.user_id == memory.user_id, TriggerEvent.portfolio_id == memory.portfolio_id,
        TriggerEvent.analysis_run_id == memory.analysis_run_id,
        TriggerEvent.target_key == target.get("target_key"),
        TriggerEvent.confirmed_at.is_not(None), TriggerEvent.confirmed_at <= as_of,
        TriggerEvent.detected_at >= memory.decision_at, TriggerEvent.created_at <= as_of,
    ).limit(1))
    return "TRIGGERED" if event else "CONDITION_UNOBSERVED"


def review_dimensions(db: Session, memories: list[DecisionMemory], *, as_of: datetime) -> dict[str, Any]:
    items = []
    for memory in memories:
        for target in [*(memory.holding_decisions_json or []), *(memory.candidate_decisions_json or [])]:
            source = target.get("source") or {}
            condition = condition_state(db, memory, target, as_of=as_of)
            action = target.get("recommended_action")
            factual = "TRACEABLE" if memory.quality_status in {"VALID", "DEGRADED"} else "PENDING"
            logic = "TRACEABLE" if any(source.get(key) for key in ("reason", "reasoning", "rationale", "evidence")) else "PENDING"
            executable = "CONDITION_PENDING" if condition in {"CONDITION_UNOBSERVED", "NOT_TRIGGERED"} else (
                "NO_ACTION" if action in {"hold", "watch", "no_action"} else
                "PENDING" if factual == "PENDING" or not target.get("recommended_qty") else "RECORDED_ADVICE")
            items.append({"decision_memory_id": memory.id, "analysis_run_id": memory.analysis_run_id,
                          "trade_date": memory.trade_date.isoformat(), "code": target.get("target_key"),
                          "action": action, "fact_status": factual, "logic_status": logic,
                          "execution_status": executable, "condition_status": condition,
                          "market_result_status": "PENDING", "prediction_failed": None})
    return {"items": items, "traceable_does_not_mean_correct": True,
            "conditional_untriggered_is_not_failure": True}


def performance_report(db: Session, *, user_id: int, portfolio_id: int, trade_date: date | None = None,
                       as_of: datetime | None = None) -> dict[str, Any]:
    cutoff = to_utc_naive(as_of) or utc_now()
    day = trade_date or _day(cutoff)
    previous = _previous_day(db, day)
    memories = list(db.scalars(select(DecisionMemory).where(
        DecisionMemory.user_id == user_id, DecisionMemory.portfolio_id == portfolio_id,
        DecisionMemory.available_at <= cutoff, DecisionMemory.decision_at <= cutoff,
        DecisionMemory.trade_date <= day,
    ).order_by(DecisionMemory.decision_at, DecisionMemory.id)).all())
    today = [memory for memory in memories if memory.trade_date == day]
    yesterday = [memory for memory in memories if memory.trade_date == previous]
    def day_targets(rows):
        targets = {}
        for memory in rows:
            for target in [*(memory.holding_decisions_json or []), *(memory.candidate_decisions_json or [])]:
                targets[target["target_key"]] = {"code": target["target_key"], "action": target.get("recommended_action"),
                                                 "quantity": target.get("recommended_qty")}
        return targets
    current, prior = day_targets(today), day_targets(yesterday)
    changes = []
    for code in sorted(set(current) | set(prior)):
        new, old = current.get(code, {}), prior.get(code, {})
        changes.append({"code": code, "action": new.get("action"), "previous_action": old.get("action"),
                        "quantity": new.get("quantity"), "previous_quantity": old.get("quantity"),
                        "quantity_change": new.get("quantity") - old.get("quantity")
                        if new.get("quantity") is not None and old.get("quantity") is not None else None})
    outcome_rows = db.scalars(select(DecisionOutcome).where(
        DecisionOutcome.decision_memory_id.in_([memory.id for memory in memories]),
        DecisionOutcome.horizon_trading_days.in_((1, 5, 20)),
    ).order_by(DecisionOutcome.id)).all()
    by_id = {memory.id: memory for memory in memories}
    effect, simulation, seen = [], [], set()
    for outcome in outcome_rows:
        memory = by_id[outcome.decision_memory_id]
        key = (memory.trade_date, outcome.target_key, outcome.horizon_trading_days)
        if key in seen or outcome.target_type == "PORTFOLIO":
            continue
        seen.add(key)
        # Recalculate at the requested cutoff instead of exposing a later materialized result.
        values = calculate_decision_outcome(db, memory, outcome, calculation_as_of=cutoff)
        target = next((target for target in [*(memory.holding_decisions_json or []), *(memory.candidate_decisions_json or [])]
                       if target.get("target_key") == outcome.target_key), {})
        condition = condition_state(db, memory, target, as_of=cutoff)
        row = {"decision_memory_id": memory.id, "outcome_id": outcome.id, "code": outcome.target_key,
               "action": outcome.recommended_action, "trade_date": memory.trade_date.isoformat(),
               "horizon": outcome.horizon_trading_days, "target_trade_date": values["target_trade_date"].isoformat() if values.get("target_trade_date") else None,
               "status": values["status"], "market_return": values.get("raw_return"),
               "directional_return": values.get("directional_return"),
               "condition_status": condition, "is_actual_account_pnl": False,
               "prediction_failed": None if condition in {"CONDITION_UNOBSERVED", "NOT_TRIGGERED"} or values.get("directional_return") is None
                   else values["directional_return"] < 0}
        effect.append(row)
        if condition not in {"CONDITION_UNOBSERVED", "NOT_TRIGGERED"} and outcome.recommended_action in {"add", "new_position", "conditional_add"}:
            raw = values.get("raw_return")
            simulation.append({**row, "follow_advice_return": raw, "stay_in_cash_return": 0.0 if raw is not None else None,
                               "difference": raw, "costs_included": False})
    dimensions = review_dimensions(db, today, as_of=cutoff)
    weekly = review_dimensions(db, [memory for memory in memories if day - timedelta(days=6) <= memory.trade_date], as_of=cutoff)
    issues = Counter()
    unique = set()
    for item in weekly["items"]:
        for field in ("fact_status", "logic_status", "execution_status"):
            if item[field] in {"PENDING", "CONDITION_PENDING"}:
                key = (item["trade_date"], item["code"], field)
                if key not in unique:
                    issues[field] += 1
                    unique.add(key)
    from .learning import learning_summary
    return {"version": PERFORMANCE_VERSION, "as_of": cutoff.isoformat(), "trade_date": day.isoformat(),
            "previous_trade_date": previous.isoformat() if previous else None,
            "account_return": account_performance(db, user_id=user_id, portfolio_id=portfolio_id, trade_date=day, as_of=cutoff),
            "recommendation_effect": {"items": effect, "count": len(effect), "basis": "MARKET_EFFECT_NOT_ACCOUNT_PNL"},
            "simulation_comparison": {"items": simulation, "basis": "HYPOTHETICAL_LONG_VS_CASH_BEFORE_COSTS"},
            "day_comparison": {"today": list(current.values()), "yesterday": list(prior.values()), "changes": changes},
            "review_dimensions": dimensions,
            "weekly_review": {"start_date": (day - timedelta(days=6)).isoformat(), "end_date": day.isoformat(),
                              "repeated_issues": [{"dimension": key, "count": count} for key, count in issues.items() if count >= 2],
                              "dedupe_basis": "trade_date+security+dimension"},
            "learning": learning_summary(db, user_id=user_id, portfolio_id=portfolio_id, as_of=cutoff)}
