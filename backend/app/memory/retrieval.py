"""Local deterministic historical analogue retrieval."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import (
    RETRIEVAL_DEFAULT_LIMIT,
    RETRIEVAL_LOOKBACK_DAYS,
    RETRIEVAL_MAX_LIMIT,
    RETRIEVAL_MIN_COVERAGE,
    RETRIEVAL_PREFILTER_LIMIT,
    RETRIEVAL_VERSION,
)
from .models import DecisionMemory, DecisionOutcome

FEATURE_WEIGHTS: dict[str, float] = {
    "market_regime": 20.0,
    "action_type": 20.0,
    "security_type": 10.0,
    "market_score": 10.0,
    "hhi": 10.0,
    "cash_ratio": 10.0,
    "opportunity_score": 8.0,
    "entry_score": 6.0,
    "portfolio_fit": 6.0,
}
NUMERIC_SCALES = {
    "market_score": 100.0,
    "hhi": 1.0,
    "cash_ratio": 1.0,
    "opportunity_score": 100.0,
    "entry_score": 100.0,
    "portfolio_fit": 100.0,
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _feature_value(features: dict[str, Any], key: str) -> Any:
    aliases = {
        "security_type": ("security_type", "target_security_type"),
        "action_type": ("action_type", "target_action", "recommended_action"),
        "opportunity_score": ("opportunity_score", "candidate_best_opportunity"),
        "entry_score": ("entry_score", "candidate_best_entry"),
        "portfolio_fit": ("portfolio_fit", "candidate_best_fit"),
    }
    for candidate in aliases.get(key, (key,)):
        if features.get(candidate) is not None:
            return features[candidate]
    return None


def normalize_retrieval_features(features: dict[str, Any] | None) -> dict[str, Any]:
    features = features if isinstance(features, dict) else {}
    normalized: dict[str, Any] = {}
    for key in FEATURE_WEIGHTS:
        value = _feature_value(features, key)
        if value is None:
            continue
        if key in {"market_regime", "action_type", "security_type"}:
            normalized[key] = str(value).strip().upper()
        else:
            normalized[key] = _number(value)
    return {key: value for key, value in normalized.items() if value is not None}


def compute_similarity(
    current_features: dict[str, Any] | None,
    historical_features: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare state snapshots only; outcomes never enter this calculation."""

    current = normalize_retrieval_features(current_features)
    historical = normalize_retrieval_features(historical_features)
    total_weight = sum(FEATURE_WEIGHTS.values())
    available_weight = 0.0
    weighted_score = 0.0
    similarities: list[str] = []
    differences: list[str] = []
    for key, weight in FEATURE_WEIGHTS.items():
        left = current.get(key)
        right = historical.get(key)
        if left is None or right is None:
            continue
        available_weight += weight
        if key in {"market_regime", "action_type", "security_type"}:
            score = 1.0 if left == right else 0.0
        else:
            scale = NUMERIC_SCALES[key]
            score = max(0.0, 1.0 - min(abs(float(left) - float(right)) / scale, 1.0))
        weighted_score += weight * score
        if score >= 0.8:
            similarities.append(key)
        elif score < 0.5:
            differences.append(key)
    coverage = available_weight / total_weight if total_weight else 0.0
    similarity = weighted_score / available_weight * 100.0 if available_weight else 0.0
    return {
        "similarity_score": round(similarity, 4),
        "similarity_coverage": round(coverage, 4),
        "key_similarities": similarities,
        "key_differences": differences,
        "available_weight": available_weight,
    }


def _outcome_payload(row: DecisionOutcome) -> dict[str, Any]:
    return {
        "id": row.id,
        "target_type": row.target_type,
        "target_key": row.target_key,
        "recommended_action": row.recommended_action,
        "horizon_trading_days": row.horizon_trading_days,
        "reference_trade_date": _json_value(row.reference_trade_date),
        "target_trade_date": _json_value(row.target_trade_date),
        "raw_return": row.raw_return,
        "benchmark_return": row.benchmark_return,
        "excess_return": row.excess_return,
        "mfe": row.mfe,
        "mae": row.mae,
        "directional_return": row.directional_return,
        "directional_excess_return": row.directional_excess_return,
        "actual_execution_price": row.actual_execution_price,
        "actual_execution_return": row.actual_execution_return,
        "execution_alignment": row.execution_alignment,
        "status": row.status,
        "quality_status": row.quality_status,
        "confidence": row.confidence,
        "available_at": _json_value(row.available_at),
    }


def _memory_payload(memory: DecisionMemory, outcomes: Iterable[DecisionOutcome], similarity: dict[str, Any]) -> dict[str, Any]:
    targets = [*(memory.holding_decisions_json or []), *(memory.candidate_decisions_json or [])]
    target = targets[0] if targets else {"target_key": "PORTFOLIO", "recommended_action": memory.portfolio_action}
    return {
        "decision_memory_id": memory.id,
        "decision_at": _json_value(memory.decision_at),
        "trade_date": _json_value(memory.trade_date),
        "market_regime": (memory.decision_features_json or {}).get("market_regime"),
        "decision_type": memory.decision_type,
        "target": {
            "target_type": target.get("target_type"),
            "target_key": target.get("target_key"),
            "security_type": target.get("security_type"),
        },
        "action": target.get("recommended_action") or memory.portfolio_action,
        "similarity_score": similarity["similarity_score"],
        "similarity_coverage": similarity["similarity_coverage"],
        "key_similarities": similarity["key_similarities"],
        "key_differences": similarity["key_differences"],
        "outcomes": [_outcome_payload(row) for row in outcomes],
        "execution_alignment": list(dict.fromkeys(row.execution_alignment for row in outcomes if row.execution_alignment)),
        "quality_status": memory.quality_status,
    }


def retrieve_historical_analogues(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    current_features: dict[str, Any] | None,
    as_of: datetime | None = None,
    limit: int = RETRIEVAL_DEFAULT_LIMIT,
    include_data_quality_cases: bool = False,
    exclude_memory_id: int | None = None,
) -> dict[str, Any]:
    cutoff = _utc_naive(as_of) or datetime.now(UTC).replace(tzinfo=None)
    limit = max(1, min(int(limit), RETRIEVAL_MAX_LIMIT))
    lower_bound = cutoff - timedelta(days=RETRIEVAL_LOOKBACK_DAYS)
    query = select(DecisionMemory).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
        DecisionMemory.available_at <= cutoff,
        DecisionMemory.decision_at >= lower_bound,
        DecisionMemory.decision_at <= cutoff,
    ).order_by(DecisionMemory.decision_at.desc(), DecisionMemory.id.desc()).limit(RETRIEVAL_PREFILTER_LIMIT)
    if exclude_memory_id is not None:
        query = query.where(DecisionMemory.id != exclude_memory_id)
    memories = db.execute(query).scalars().all()
    outcome_rows = db.execute(select(DecisionOutcome).join(
        DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id
    ).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
        DecisionOutcome.status.in_(("VALID", "DEGRADED")),
        DecisionOutcome.available_at.is_not(None),
        DecisionOutcome.available_at <= cutoff,
    )).scalars().all()
    outcomes_by_memory: dict[int, list[DecisionOutcome]] = {}
    for row in outcome_rows:
        outcomes_by_memory.setdefault(row.decision_memory_id, []).append(row)
    current = normalize_retrieval_features(current_features)
    normal: list[dict[str, Any]] = []
    data_quality: list[dict[str, Any]] = []
    for memory in memories:
        outcomes = outcomes_by_memory.get(memory.id, [])
        if not outcomes:
            continue
        quality = str(memory.quality_status or "").upper()
        score = compute_similarity(current, memory.decision_features_json or {})
        if score["similarity_coverage"] < RETRIEVAL_MIN_COVERAGE:
            continue
        payload = _memory_payload(memory, outcomes, score)
        if quality == "BLOCKED":
            if include_data_quality_cases:
                data_quality.append(payload)
            continue
        normal.append(payload)
    normal.sort(key=lambda item: (-float(item["similarity_score"]), -float(item["similarity_coverage"]), item["decision_at"], item["decision_memory_id"]))
    data_quality.sort(key=lambda item: (-float(item["similarity_score"]), item["decision_memory_id"]))
    return {
        "status": "available",
        "retrieval_version": RETRIEVAL_VERSION,
        "as_of": cutoff.isoformat(),
        "analogue_count": min(limit, len(normal)),
        "analogues": normal[:limit],
        "data_quality_cases": data_quality[:limit] if include_data_quality_cases else [],
        "rules": {
            "historical_memory_is_secondary": True,
            "must_not_override_current_gates": True,
            "outcome_not_used_in_similarity": True,
        },
    }


def memory_context_for_analysis(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    as_of: datetime | None,
    current_features: dict[str, Any] | None,
    limit: int = RETRIEVAL_DEFAULT_LIMIT,
) -> dict[str, Any]:
    try:
        retrieved = retrieve_historical_analogues(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            current_features=current_features,
            as_of=as_of,
            limit=limit,
        )
        return {
            **retrieved,
            "memory_context_version": RETRIEVAL_VERSION,
            "status": retrieved.get("status", "available"),
        }
    except Exception as exc:  # callers must fail open for advisory memory
        return {
            "status": "unavailable",
            "retrieval_version": RETRIEVAL_VERSION,
            "analogue_count": 0,
            "analogues": [],
            "error": str(exc)[:300],
            "rules": {
                "historical_memory_is_secondary": True,
                "must_not_override_current_gates": True,
            },
        }


__all__ = [
    "FEATURE_WEIGHTS",
    "compute_similarity",
    "memory_context_for_analysis",
    "normalize_retrieval_features",
    "retrieve_historical_analogues",
]
