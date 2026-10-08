"""Versioned advisory hypotheses, with forward-only counterfactual validation.

This learns inspectable advisory hypotheses, never model weights or live
strategy parameters. Frozen paired replays and legacy cash simulations are
distinct from real account profits and causal model A/B evidence.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import utc_now_naive as utc_now
from ..history.time import CHINA_TZ, to_utc_naive
from ..v2_models import AnalysisJob
from .models import DecisionMemory, DecisionOutcome, LearningContextReference, LearningHypothesis, LearningValidation
from .outcomes import calculate_decision_outcome

LEARNING_VERSION = "learning-p4-v2"
CONTEXT_VERSION = "learning-context-p4-v1"
ROLES = ("portfolio_manager", "risk_manager", "trader", "candidate_analyst", "bull_researcher", "bear_researcher",
         "market_analyst", "news_analyst", "fundamentals_analyst", "policy_analyst", "capital_flow_analyst", "sentiment_analyst", "lockup_supply_analyst")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _local_day(stamp: datetime):
    return to_utc_naive(stamp).replace(tzinfo=UTC).astimezone(CHINA_TZ).date()


def _latest(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime) -> list[LearningHypothesis]:
    rows = db.scalars(select(LearningHypothesis).where(
        LearningHypothesis.user_id == user_id, LearningHypothesis.portfolio_id == portfolio_id,
        LearningHypothesis.available_at <= as_of,
    ).order_by(LearningHypothesis.revision, LearningHypothesis.id)).all()
    return list({row.hypothesis_key: row for row in rows}.values())


def _serialize(row: LearningHypothesis) -> dict[str, Any]:
    return {"id": row.id, "hypothesis_key": row.hypothesis_key, "revision": row.revision,
            "status": row.status, "statement": row.statement, "scope": row.scope_json,
            "support": row.support_json, "counterexamples": row.counterexamples_json,
            "validation": row.validation_json, "weight": row.weight,
            "extraction_cutoff": row.extraction_cutoff.isoformat(), "available_at": row.available_at.isoformat(),
            "reason": row.reason, "version": row.calculation_version}


def learning_summary(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> dict[str, Any]:
    cutoff = to_utc_naive(as_of) or utc_now()
    rows = _latest(db, user_id=user_id, portfolio_id=portfolio_id, as_of=cutoff)
    return {"version": LEARNING_VERSION, "as_of": cutoff.isoformat(), "hypotheses": [_serialize(row) for row in rows],
            "status_counts": {status: sum(row.status == status for row in rows)
                              for status in ("pending", "reference", "verified", "disabled")},
            "validation_basis": "PER_HYPOTHESIS_FROZEN_PAIRED_REPLAY_OR_LEGACY_CASH_SIMULATION",
            "model_weights_changed": False, "strategy_changes_require_governance": True}


def _samples(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> list[dict]:
    rows = db.execute(select(DecisionMemory, DecisionOutcome).join(
        DecisionOutcome, DecisionOutcome.decision_memory_id == DecisionMemory.id,
    ).where(DecisionMemory.user_id == user_id, DecisionMemory.portfolio_id == portfolio_id,
            DecisionMemory.available_at <= cutoff, DecisionMemory.decision_at <= cutoff,
            DecisionOutcome.horizon_trading_days.in_((1, 5, 20)),
            DecisionOutcome.target_type != "PORTFOLIO",
            DecisionOutcome.recommended_action.in_(("add", "new_position", "conditional_add", "reduce", "sell", "exit", "hold", "no_action", "watch_only")),
    ).order_by(DecisionMemory.decision_at, DecisionMemory.id, DecisionOutcome.id)).all()
    seen, samples = set(), []
    from .performance import condition_state, review_dimensions
    dimensions = {(item["decision_memory_id"], item["code"]): item for item in review_dimensions(
        db, list({memory.id: memory for memory, _ in rows}.values()), as_of=cutoff)["items"]}
    for memory, outcome in rows:
        key = (memory.trade_date, outcome.target_key, outcome.horizon_trading_days)
        if key in seen:
            continue
        # Repeated analyses of the same security/day contribute one earliest decision.
        target = next((target for target in [*(memory.holding_decisions_json or []), *(memory.candidate_decisions_json or [])]
                       if target.get("target_key") == outcome.target_key), {})
        if condition_state(db, memory, target, as_of=cutoff) in {"CONDITION_UNOBSERVED", "NOT_TRIGGERED"}:
            continue
        values = calculate_decision_outcome(db, memory, outcome, calculation_as_of=cutoff)
        if (values["status"] not in {"VALID", "DEGRADED"} or values.get("raw_return") is None
                or values.get("available_at") is None or values["available_at"] > cutoff):
            continue
        seen.add(key)
        scope = {"action": outcome.recommended_action,
                 "market_regime": (memory.market_context_json or {}).get("regime")
                    or (memory.decision_features_json or {}).get("market_regime") or "UNKNOWN",
                 "security_type": target.get("security_type") or "UNKNOWN", "horizon": outcome.horizon_trading_days,
                 "roles": list(ROLES), "policy": "ADVISORY_REVIEW_PAIRED_REPLAY"}
        samples.append({"decision_memory_id": memory.id, "outcome_id": outcome.id,
                        "trade_date": memory.trade_date.isoformat(), "code": outcome.target_key,
                        "decision_at": memory.decision_at, "available_at": values["available_at"],
                        "return": values.get("directional_return") if values.get("directional_return") is not None else values["raw_return"],
                        "market_return": values["raw_return"], "public_reason": target.get("source", {}).get("reason"), "scope": scope,
                        "review_dimensions": dimensions.get((memory.id, outcome.target_key), {}),
                        "source_hash": _hash({"return": values["raw_return"], "source_refs": values.get("source_refs")})})
    return samples


def _sample_ref(sample: dict) -> dict:
    return {key: sample[key] for key in ("decision_memory_id", "outcome_id", "trade_date", "code", "return", "source_hash")}


def refresh_learning(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> dict[str, Any]:
    cutoff = to_utc_naive(as_of) or utc_now()
    # Running a historical review today does not manufacture a past availability date.
    available = max(utc_now(), cutoff)
    samples = _samples(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff)
    grouped = defaultdict(list)
    for sample in samples:
        grouped[_hash(sample["scope"])[:24]].append(sample)
    existing = {row.hypothesis_key: row for row in _latest(db, user_id=user_id, portfolio_id=portfolio_id, as_of=available)}
    from .learning_review import paired_replay, propose_hypothesis
    model_groups = 0
    # Rotate a bounded model budget across groups using last recorded availability.
    for key, group in sorted(grouped.items(), key=lambda pair: existing[pair[0]].available_at if pair[0] in existing else datetime.min):
        row = existing.get(key)
        if row is None:
            support = [sample for sample in group if sample["return"] < 0]
            if not support:
                continue
            counters = [sample for sample in group if sample["return"] >= 0]
            scope = group[0]["scope"]
            proposed = None
            if scope["policy"] == "ADVISORY_REVIEW_PAIRED_REPLAY" and model_groups < 3:
                model_groups += 1
                try:
                    proposed = propose_hypothesis(db, user_id=user_id, samples=group, roles=ROLES)
                except Exception:
                    # Review continues with an explicitly labelled observation, never a fabricated model explanation.
                    proposed = None
            statement = (proposed or {}).get("statement") or (
                f"{scope['market_regime']} / {scope['security_type']} 的 {scope['action']} 建议在 {scope['horizon']} 交易日窗口存在不利结果；"
                "复核原始证据、执行约束和反例，结果不利本身不能证明当时判断错误。")
            if proposed:
                scope = {**scope, "roles": proposed["roles"], "dimension": proposed["dimension"]}
                support = [sample for sample in group if sample["outcome_id"] in proposed["support_ids"]]
                counters = [sample for sample in group if sample["outcome_id"] in proposed["counterexample_ids"]]
            row = LearningHypothesis(user_id=user_id, portfolio_id=portfolio_id, hypothesis_key=key, revision=1,
                status="reference" if len(support) >= 3 and len(support) > len(counters) else "pending",
                statement=statement,
                scope_json=scope, support_json=[_sample_ref(sample) for sample in support],
                counterexamples_json=[_sample_ref(sample) for sample in counters],
                validation_json={"sample_count": 0, "basis": "AWAITING_INDEPENDENT_FORWARD_REPLAY",
                                 "extraction_method": "STRUCTURED_MODEL_REVIEW" if proposed else "OBSERVED_OUTCOME_PATTERN",
                                 "limitations": (proposed or {}).get("limitations", "统计观察不能确定事实或逻辑错误")},
                weight=0.25, extraction_cutoff=cutoff, available_at=available, reason="EXTRACTED_AWAITING_FORWARD_VALIDATION", calculation_version=LEARNING_VERSION)
            db.add(row)
            db.flush()
            continue
        if row.status == "disabled":
            continue
        original = db.scalar(select(LearningHypothesis).where(
            LearningHypothesis.portfolio_id == portfolio_id, LearningHypothesis.hypothesis_key == key,
            LearningHypothesis.revision == 1,
        ))
        previous_validations = list(db.scalars(select(LearningValidation).where(
            LearningValidation.portfolio_id == portfolio_id, LearningValidation.hypothesis_key == key,
        ).order_by(LearningValidation.trade_date, LearningValidation.id)).all())
        known = {(item.trade_date.isoformat(), item.target_key) for item in previous_validations}
        samples_by_outcome = {sample["outcome_id"]: sample for sample in samples}
        source_changed = any(item.outcome_id in samples_by_outcome and
                             (item.source_refs_json or {}).get("source_hash") != samples_by_outcome[item.outcome_id]["source_hash"]
                             for item in previous_validations)
        pending_reason = None
        for sample in reversed(group):
            if (sample["decision_at"] <= original.available_at or
                    sample["trade_date"] <= _local_day(original.extraction_cutoff).isoformat() or
                    (sample["trade_date"], sample["code"]) in known):
                continue
            baseline = float(sample["return"])
            paired = None
            if original.scope_json.get("policy") == "ADVISORY_REVIEW_PAIRED_REPLAY":
                if model_groups >= 3:
                    continue
                try:
                    def started():
                        nonlocal model_groups
                        model_groups += 1
                    paired = paired_replay(db, user_id=user_id, sample=sample, hypothesis=original, on_start=started)
                except Exception:
                    paired = None
                if paired is None:
                    pending_reason = "FROZEN_PAIR_UNAVAILABLE_OR_MODEL_REPLAY_FAILED"
                    continue
                baseline = paired["baseline"]
            learned = paired["learned"] if paired else 0.0
            validation = LearningValidation(portfolio_id=portfolio_id, hypothesis_id=original.id, hypothesis_key=key,
                decision_memory_id=sample["decision_memory_id"], outcome_id=sample["outcome_id"],
                trade_date=_local_day(sample["decision_at"]), target_key=sample["code"],
                baseline_return=baseline, learned_return=learned, difference=learned-baseline, available_at=available,
                source_refs_json={**_sample_ref(sample), "horizon": original.scope_json["horizon"],
                                  "policy": original.scope_json["policy"], "costs_included": False,
                                  "real_account_pnl": False, "paired_replay": paired})
            db.add(validation)
            previous_validations.append(validation)
            known.add((sample["trade_date"], sample["code"]))
        n = len(previous_validations)
        if not n and not source_changed and pending_reason is None:
            continue
        differences = [item.difference for item in previous_validations]
        mean = sum(differences) / n if n else None
        recent = differences[-5:]
        recent_mean = sum(recent) / len(recent) if recent else None
        status, weight, reason = row.status, row.weight, "FORWARD_VALIDATION_PENDING"
        if source_changed:
            status, weight, reason = "disabled", 0.0, "VALIDATION_SOURCE_REVISED"
        elif len(recent) >= 5 and recent_mean <= 0:
            status, weight, reason = "disabled", 0.0, "RECENT_FORWARD_EFFECT_FAILED"
        elif len(recent) >= 3 and recent_mean <= 0:
            status, weight, reason = "reference", 0.1, "FORWARD_EFFECT_DOWNWEIGHTED"
        elif n >= 10 and mean > 0 and sum(value > 0 for value in differences) / n >= 0.6:
            status, weight, reason = "verified", 0.75, "FORWARD_SIMULATION_SUPPORTED"
        elif n >= 3 and mean > 0:
            status, weight, reason = "reference", 0.4, "FORWARD_REFERENCE_SUPPORTED"
        validation_json = {**original.validation_json, "sample_count": n, "mean_difference": mean, "recent_mean_difference": recent_mean,
                           "baseline_mean_return": sum(item.baseline_return for item in previous_validations) / n if n else None,
                           "learned_mean_return": sum(item.learned_return for item in previous_validations) / n if n else None,
                           "basis": "PAIRED_FROZEN_DECISION_REPLAY_GROSS_PORTFOLIO_CONTRIBUTION" if original.scope_json.get("policy") == "ADVISORY_REVIEW_PAIRED_REPLAY" else "SIMULATED_FILTER_VS_UNCHANGED_GROSS_RETURN",
                           "paired_model_replay": original.scope_json.get("policy") == "ADVISORY_REVIEW_PAIRED_REPLAY", "causal_model_ab_test": False,
                           "pending_reason": pending_reason,
                           "independent_sample_ids": [item.decision_memory_id for item in previous_validations]}
        if validation_json == row.validation_json and status == row.status and weight == row.weight:
            continue
        db.add(LearningHypothesis(user_id=user_id, portfolio_id=portfolio_id, hypothesis_key=key, revision=row.revision + 1,
            status=status, statement=row.statement, scope_json=row.scope_json, support_json=row.support_json,
            counterexamples_json=row.counterexamples_json, validation_json=validation_json,
            weight=weight, extraction_cutoff=original.extraction_cutoff, available_at=available, reason=reason, calculation_version=LEARNING_VERSION))
    db.flush()
    return learning_summary(db, user_id=user_id, portfolio_id=portfolio_id, as_of=available)


def for_role(context: dict, role: str) -> dict:
    result = deepcopy(context)
    result["role"] = role
    # Unknown research roles receive no irrelevant experience. 'all' is the frozen master.
    aliases = {"bull_round_1": "bull_researcher", "bull_round_2": "bull_researcher",
               "bear_round_1": "bear_researcher", "bear_round_2": "bear_researcher",
               "aggressive_risk_agent": "risk_manager", "neutral_risk_agent": "risk_manager",
               "conservative_risk_agent": "risk_manager", "risk_synthesis": "risk_manager",
               "trader_revision": "trader", "candidate_llm_review": "candidate_analyst",
               "final_manager": "portfolio_manager", "manager_synthesis": "portfolio_manager",
               "bull": "bull_researcher", "bear": "bear_researcher"}
    effective_role = aliases.get(role, role)
    result["hypotheses"] = [item for item in context.get("hypotheses", [])
                            if role == "all" or effective_role in item.get("scope", {}).get("roles", [])]
    result["ref_ids"] = [item["id"] for item in result["hypotheses"]]
    return result


def build_learning_context(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None,
                           current_features: dict | None = None, role: str = "all",
                           analysis_job_id: int | None = None) -> dict:
    cutoff = to_utc_naive(as_of) or utc_now()
    features = current_features or {}
    eligible = []
    for row in _latest(db, user_id=user_id, portfolio_id=portfolio_id, as_of=cutoff):
        if row.status not in {"reference", "verified"} or row.weight <= 0:
            continue
        scope = row.scope_json or {}
        if any(scope.get(key) not in (None, "UNKNOWN", features.get(key))
               for key in ("market_regime", "security_type") if features.get(key)):
            continue
        if features.get("action_type") and scope.get("action") != features["action_type"]:
            continue
        eligible.append(_serialize(row))
    context = {"version": CONTEXT_VERSION, "as_of": cutoff.isoformat(), "user_id": user_id,
               "portfolio_id": portfolio_id, "hypotheses": sorted(eligible, key=lambda row: row["id"]),
               "ref_ids": [row["id"] for row in eligible],
               "rules": {"advisory_only": True, "must_not_override_current_gates": True,
                         "model_weights_unchanged": True, "strategy_changes_require_governance": True}}
    context["context_id"] = _hash(context)
    context = for_role(context, role)
    if analysis_job_id is not None:
        record_learning_references(db, user_id=user_id, portfolio_id=portfolio_id,
                                   analysis_job_id=analysis_job_id, role=role, context=context)
    return context


def record_learning_references(db: Session, *, user_id: int, portfolio_id: int, analysis_job_id: int,
                               role: str, context: dict) -> None:
    job = db.get(AnalysisJob, analysis_job_id)
    if job is None or job.user_id != user_id or job.portfolio_id != portfolio_id:
        raise ValueError("learning_context_job_ownership_mismatch")
    if context.get("user_id") != user_id or context.get("portfolio_id") != portfolio_id:
        raise ValueError("learning_context_ownership_mismatch")
    subset = for_role(context, role)
    cutoff = to_utc_naive(datetime.fromisoformat(context["as_of"]))
    for item in subset.get("hypotheses", []):
        if to_utc_naive(datetime.fromisoformat(item["available_at"])) > cutoff:
            raise ValueError("learning_context_future_reference")
    exists = db.scalar(select(LearningContextReference.id).where(
        LearningContextReference.analysis_job_id == analysis_job_id, LearningContextReference.role == role,
        LearningContextReference.context_id == context["context_id"],
    ))
    if exists is None:
        db.add(LearningContextReference(user_id=user_id, portfolio_id=portfolio_id, analysis_job_id=analysis_job_id,
            role=role, context_id=context["context_id"], as_of=cutoff, context_json=subset))
        db.flush()


def learning_usage_report(db: Session, *, analysis_job_id: int, context: dict) -> dict:
    """Distinguish supplied experience from public, validated declarations of use."""
    from ..analysis_workflow.models import AnalysisNode, AnalysisArtifact
    from ..v2_models import AnalysisRun
    nodes = db.scalars(select(AnalysisNode).join(AnalysisRun, AnalysisNode.analysis_run_id == AnalysisRun.id)
                       .where(AnalysisRun.job_id == analysis_job_id, AnalysisNode.status.in_(("succeeded", "completed")))).all()
    supplied = {item["id"]: item for item in context.get("hypotheses", [])}
    items = []
    for node in nodes:
        artifact = db.get(AnalysisArtifact, node.output_artifact_id) if node.output_artifact_id else None
        output = artifact.content_json if artifact and isinstance(artifact.content_json, dict) else {}
        for usage in output.get("learning_usage", []):
            hypothesis = supplied.get(usage.get("hypothesis_id"))
            if hypothesis:
                items.append({**usage, "role": node.node_key, "statement": hypothesis["statement"],
                              "revision": hypothesis["revision"]})
    missing = sorted(set(supplied) - {item["hypothesis_id"] for item in items})
    return {"context_id": context.get("context_id"), "supplied_ids": sorted(supplied), "unreported_ids": missing,
            "items": items, "usage_status": "PARTIALLY_REPORTED" if items and missing else "REPORTED" if items else "NO_APPLICABLE_EXPERIENCE" if not supplied else "NOT_REPORTED",
            "advisory_only": True, "declared_effect_is_not_causal_proof": True}
