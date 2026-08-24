"""Pure deterministic trigger evaluation rules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any

from ..config import settings
from ..market.engine.config import REGIME_ORDER

RULE_VERSION = "trigger-engine-v1"
VALID_OPERATORS = frozenset({"GT", "GTE", "LT", "LTE", "CROSS_ABOVE", "CROSS_BELOW"})
QUALITY_BLOCKING = frozenset({"STALE", "CONFLICT", "INVALID", "MISSING", "FROZEN"})


@dataclass(frozen=True, slots=True)
class TriggerDetection:
    trigger_type: str
    target_type: str
    target_key: str
    priority: str
    metric: str | None
    current_value: float | None
    previous_value: float | None
    threshold: float | None
    reason_code: str
    dedupe_key: str
    rule_id: str
    evidence: dict[str, Any] = field(default_factory=dict)
    trigger_plan_id: int | None = None
    user_id: int | None = None
    portfolio_id: int | None = None
    portfolio_snapshot_id: int | None = None
    market_snapshot_id: str | None = None
    market_score_snapshot_id: str | None = None
    debounce_cycles: int = 2
    debounce_seconds: int = 180
    cooldown_seconds: int = 1800
    rule_version: str = RULE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_values(operator: str, current: float, threshold: float, previous: float | None = None) -> bool:
    op = str(operator or "").upper()
    if op not in VALID_OPERATORS or not all(isfinite(value) for value in (current, threshold)):
        return False
    if op == "GT":
        return current > threshold
    if op == "GTE":
        return current >= threshold
    if op == "LT":
        return current < threshold
    if op == "LTE":
        return current <= threshold
    if previous is None or not isfinite(previous):
        return False
    if op == "CROSS_ABOVE":
        return previous <= threshold < current
    return previous >= threshold > current


def evaluate_holding_plan(
    plan: Any,
    quote: Any,
    *,
    previous_value: float | None = None,
    portfolio_snapshot_id: int | None = None,
) -> TriggerDetection | None:
    quality = str(getattr(getattr(quote, "quality_status", "MISSING"), "value", getattr(quote, "quality_status", "MISSING"))).upper()
    if quality in QUALITY_BLOCKING:
        return TriggerDetection(
            trigger_type="DATA_QUALITY",
            target_type="DATA_QUALITY",
            target_key=str(plan.target_key),
            priority="P1" if quality in {"CONFLICT", "INVALID"} else "P2",
            metric="quote_quality",
            current_value=None,
            previous_value=None,
            threshold=None,
            reason_code=f"HOLDING_QUOTE_{quality}",
            dedupe_key=f"DATA_QUALITY:{plan.portfolio_id}:{plan.target_key}:{quality}",
            rule_id="holding_quote_quality",
            evidence={"quality_status": quality, "provider": getattr(quote, "provider", None), "errors": list(getattr(quote, "errors", []) or [])},
            trigger_plan_id=plan.id,
            user_id=plan.user_id,
            portfolio_id=plan.portfolio_id,
            portfolio_snapshot_id=portfolio_snapshot_id,
            debounce_cycles=1,
            debounce_seconds=0,
            cooldown_seconds=plan.cooldown_seconds,
        )
    metric = str(plan.metric or "").lower()
    if metric == "price":
        value = getattr(quote, "price", None)
    elif metric in {"pct_change", "change_pct"}:
        value = getattr(quote, "pct_change", None)
    else:
        return None
    try:
        current = float(value)
        threshold = float(plan.threshold)
    except (TypeError, ValueError):
        return None
    if not compare_values(plan.operator, current, threshold, previous_value):
        return None
    trigger_type = str(plan.trigger_type or "CONDITION_MET").upper()
    reason_code = f"HOLDING_{trigger_type}"
    return TriggerDetection(
        trigger_type="HOLDING",
        target_type="HOLDING",
        target_key=str(plan.target_key),
        priority=str(plan.priority or "P1").upper(),
        metric=metric,
        current_value=current,
        previous_value=previous_value,
        threshold=threshold,
        reason_code=reason_code,
        dedupe_key=f"HOLDING:{plan.portfolio_id}:{plan.target_key}:{trigger_type}:{plan.operator}:{threshold:g}",
        rule_id=f"holding_plan_{plan.id}",
        evidence={
            "reason_code": reason_code,
            "operator": plan.operator,
            "provider": getattr(quote, "provider", None),
            "source_timestamp": getattr(quote, "source_timestamp", None),
            "analysis_required": True,
            "trade_decision": False,
        },
        trigger_plan_id=plan.id,
        user_id=plan.user_id,
        portfolio_id=plan.portfolio_id,
        portfolio_snapshot_id=portfolio_snapshot_id,
        debounce_cycles=plan.debounce_cycles,
        debounce_seconds=plan.debounce_seconds,
        cooldown_seconds=plan.cooldown_seconds,
    )


def _regime_distance(previous: str | None, current: str | None) -> int:
    try:
        return abs(REGIME_ORDER.index(str(current)) - REGIME_ORDER.index(str(previous)))
    except ValueError:
        return 0


def evaluate_market_scores(current: Any, previous: Any | None) -> list[TriggerDetection]:
    if current is None:
        return []
    current_quality = str(current.quality_status or "").upper()
    previous_quality = str(previous.quality_status or "").upper() if previous is not None else None
    if bool(current.is_frozen) or current_quality == "FROZEN":
        if previous is None or not bool(previous.is_frozen):
            return [TriggerDetection(
                trigger_type="DATA_QUALITY",
                target_type="DATA_QUALITY",
                target_key="CN",
                priority="P0",
                metric="market_score_quality",
                current_value=None,
                previous_value=None,
                threshold=None,
                reason_code="DATA_QUALITY_FROZEN",
                dedupe_key=f"DATA_QUALITY:CN:FROZEN:{current.freeze_reason or current_quality}",
                rule_id="market_data_quality_frozen",
                evidence={
                    "reason_code": "DATA_QUALITY_FROZEN",
                    "previous_quality": previous_quality,
                    "current_quality": current_quality,
                    "freeze_reason": current.freeze_reason,
                    "analysis_required": False,
                },
                market_score_snapshot_id=current.snapshot_id,
                debounce_cycles=1,
                debounce_seconds=0,
                cooldown_seconds=settings.TRIGGER_DEFAULT_COOLDOWN_SECONDS,
            )]
        return []
    if previous is None or bool(previous.is_frozen):
        return []
    detections: list[TriggerDetection] = []
    if current.display_score is not None and previous.display_score is not None:
        delta = float(current.display_score) - float(previous.display_score)
        magnitude = abs(delta)
        if magnitude >= settings.TRIGGER_MARKET_SCORE_DELTA_HARD:
            threshold = settings.TRIGGER_MARKET_SCORE_DELTA_HARD
            level = "HARD"
            priority = "P0"
        elif magnitude >= settings.TRIGGER_MARKET_SCORE_DELTA_SOFT:
            threshold = settings.TRIGGER_MARKET_SCORE_DELTA_SOFT
            level = "SOFT"
            priority = "P1"
        else:
            threshold = None
            level = None
            priority = None
        if threshold is not None and level is not None and priority is not None:
            direction = "DOWN" if delta < 0 else "UP"
            reason = f"MARKET_SCORE_DELTA_{level}"
            detections.append(TriggerDetection(
                trigger_type="MARKET",
                target_type="MARKET",
                target_key="CN",
                priority=priority,
                metric="market_score_delta_15m",
                current_value=float(current.display_score),
                previous_value=float(previous.display_score),
                threshold=threshold,
                reason_code=reason,
                dedupe_key=f"MARKET:CN:score_delta_15m:{direction}:threshold_{threshold:g}",
                rule_id=f"market_score_delta_15m_{level.lower()}",
                evidence={
                    "reason_code": reason,
                    "delta": delta,
                    "direction": direction,
                    "window_minutes": settings.TRIGGER_MARKET_SCORE_WINDOW_MINUTES,
                    "previous_regime": previous.regime,
                    "current_regime": current.regime,
                    "analysis_required": True,
                    "trade_decision": False,
                },
                market_score_snapshot_id=current.snapshot_id,
                debounce_cycles=settings.TRIGGER_DEFAULT_DEBOUNCE_CYCLES,
                debounce_seconds=settings.TRIGGER_DEFAULT_DEBOUNCE_SECONDS,
                cooldown_seconds=settings.TRIGGER_DEFAULT_COOLDOWN_SECONDS,
            ))
    if current.regime and previous.regime and current.regime != previous.regime:
        distance = _regime_distance(previous.regime, current.regime)
        priority = "P0" if distance >= 2 else "P1"
        detections.append(TriggerDetection(
            trigger_type="MARKET",
            target_type="MARKET",
            target_key="CN",
            priority=priority,
            metric="market_regime",
            current_value=float(REGIME_ORDER.index(current.regime)),
            previous_value=float(REGIME_ORDER.index(previous.regime)),
            threshold=2.0 if distance >= 2 else 1.0,
            reason_code="MARKET_REGIME_CHANGE",
            dedupe_key=f"MARKET:CN:regime:{previous.regime}:{current.regime}",
            rule_id="market_regime_change",
            evidence={
                "reason_code": "MARKET_REGIME_CHANGE",
                "previous_regime": previous.regime,
                "current_regime": current.regime,
                "regime_distance": distance,
                "analysis_required": True,
                "trade_decision": False,
            },
            market_score_snapshot_id=current.snapshot_id,
            debounce_cycles=settings.TRIGGER_DEFAULT_DEBOUNCE_CYCLES,
            debounce_seconds=settings.TRIGGER_DEFAULT_DEBOUNCE_SECONDS,
            cooldown_seconds=settings.TRIGGER_DEFAULT_COOLDOWN_SECONDS,
        ))
    return detections
