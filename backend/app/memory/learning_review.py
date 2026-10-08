"""Public review hypotheses and paired replays using only saved decision-time facts."""
from __future__ import annotations

from copy import deepcopy
import math

from pydantic import BaseModel, ConfigDict, Field

from ..v2_models import AnalysisRun
from ..config import settings
from ..portfolio.decision_gate import apply_portfolio_decision_gate
from ..services.model_client import call_model_json


class ReviewHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=12, max_length=1200)
    dimension: str
    support_ids: list[int] = Field(min_length=1)
    counterexample_ids: list[int]
    roles: list[str] = Field(min_length=1)
    limitations: str = Field(min_length=5)


class ReplayDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    action: str
    quantity: int = Field(ge=0)
    reason: str = Field(min_length=1)
    evidence_refs: list[str]


def _profile(db, user_id):
    from ..services.analysis_engine import _profile as select_profile
    return select_profile(db, user_id, "analysis")


def propose_hypothesis(db, *, user_id, samples, roles):
    profile = _profile(db, user_id)
    if profile is None:
        return None
    ids = {sample["outcome_id"] for sample in samples}
    body = [{key: sample.get(key) for key in (
        "outcome_id", "code", "trade_date", "return", "scope", "public_reason", "review_dimensions"
    )} for sample in samples[-24:]]
    instruction = (
        "从已核算建议复盘提出一条待验证经验，以中文公开结论解释事实、逻辑、执行或市场结果问题。"
        "必须区分结果不利和当时建议错误，不能用事后事件指责此前分析。只引用输入 outcome_id；"
        "保留反例和不确定性，不改变仓位规则、策略参数或模型权重。返回 JSON: "
        "statement, dimension(fact/logic/execution/market_result), support_ids, counterexample_ids, roles, limitations。"
    )
    import json
    result = call_model_json(profile, [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(body, ensure_ascii=False, default=str)},
    ], validator=lambda raw: _valid_hypothesis(raw, ids, roles))
    return ReviewHypothesis.model_validate(result.data).model_dump()


def _valid_hypothesis(raw, ids, roles):
    try:
        value = ReviewHypothesis.model_validate(raw)
        return (value.dimension in {"fact", "logic", "execution", "market_result"}
                and set(value.support_ids) <= ids and set(value.counterexample_ids) <= ids
                and not set(value.support_ids).intersection(value.counterexample_ids)
                and set(value.roles) <= set(roles))
    except (TypeError, ValueError):
        return False


def paired_replay(db, *, user_id, sample, hypothesis, on_start=None):
    """Two public decision calls; no future outcomes in either model input."""
    from .models import DecisionMemory
    from ..analysis_workflow.evidence import model_profile_identity
    from ..analysis_workflow.resume import hash_input
    from ..services.unified_evidence import project_unified_evidence

    memory = db.get(DecisionMemory, sample["decision_memory_id"])
    run = db.get(AnalysisRun, memory.analysis_run_id) if memory else None
    saved = run.structured_result_json if run else None
    profile = _profile(db, user_id)
    if not saved or profile is None:
        return None
    snapshot = saved.get("input_snapshot")
    market = deepcopy(saved.get("market_snapshot") or {})
    context = deepcopy(memory.portfolio_context_json or {})
    if not snapshot or "position_constraints" not in context:
        return None
    if market.get("unified_evidence"):
        market["unified_evidence"] = project_unified_evidence(market["unified_evidence"], sample["decision_at"])
    # Final refreshed quotes can postdate the research snapshot, but never the published decision.
    from ..services.unified_evidence import evidence_time
    cutoff = evidence_time(sample["decision_at"])
    if cutoff is None or evidence_time(hypothesis.available_at) >= cutoff:
        return None
    for quote in [*market.get("quotes", {}).values(), *(context.get("execution_quotes") or {}).values()]:
        stamp = evidence_time(quote.get("fetched_at") or quote.get("observed_at") or quote.get("source_timestamp"))
        if stamp is None or stamp > cutoff or quote.get("stale") or str(quote.get("quality_status", "MISSING")).upper() not in {"VALID", "DEGRADED"}:
            return None
        if (cutoff - stamp).total_seconds() > settings.QUOTE_FRESHNESS_SECONDS:
            return None
        for clock in ("observed_at", "source_timestamp"):
            observed = evidence_time(quote.get(clock))
            if observed is not None and observed > cutoff:
                return None
    refs = list((market.get("unified_evidence") or {}).get("records", {}))
    refs = [key for key in refs if market["unified_evidence"]["records"][key].get("status") in {"available", "degraded"}]
    price = (market.get("quotes", {}).get(sample["code"]) or {}).get("price")
    assets = context.get("current_estimated_total_assets")
    position = next((item for item in snapshot["holdings"] if item.get("code") == sample["code"]), {})
    if not refs or not price or not assets or not math.isfinite(float(assets)) or float(assets) <= 0 or not math.isfinite(float(price)) or float(price) <= 0:
        return None
    payload = {"snapshot": snapshot, "market": market, "portfolio_context": context,
               "code": sample["code"], "as_of": sample["decision_at"], "evidence_refs": refs}
    import json
    outputs, scores, diagnostics = {}, {}, {}
    if on_start is not None:
        on_start()
    # Keep the model/settings, facts, instruction and evaluation fixed across the pair.
    for name, learning in (("without_learning", []), ("with_learning", [
        {"id": hypothesis.id, "statement": hypothesis.statement, "scope": hypothesis.scope_json,
         "support": hypothesis.support_json, "counterexamples": hypothesis.counterexamples_json}
    ])):
        body = {**payload, "learning_context": learning}
        instruction = ("根据给定时点冻结事实，为指定 code 提出公开建议。经验仅供参考，不得覆盖账户、行情和风控。"
                       "不得取数或使用未来结果。返回 JSON: code, action(add/reduce/sell/hold/no_action), quantity(整数股数), reason, evidence_refs。"
                       "条件不能证明已触发时保留 hold；禁止猜测交易条件。")
        result = call_model_json(profile, [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(body, ensure_ascii=False, default=str)},
        ], validator=lambda raw: _valid_decision(raw, sample["code"], refs))
        value = ReplayDecision.model_validate(result.data).model_dump()
        requested = dict(value)
        if value["action"] == "add":
            requested["target_weight"] = ((float(position.get("qty") or 0) + value["quantity"]) * float(price) / float(assets))
        pair_context = deepcopy(context)
        candidate = value["action"] == "add" and not position
        if candidate:
            permitted = next((row for row in pair_context.get("execution_candidates", []) if row.get("code") == sample["code"]), None)
            if permitted is None or not pair_context.get("execution_quotes_required"):
                return None
            permitted["probe_weight"] = min(float(permitted.get("probe_weight") or 0), requested["target_weight"])
            requested.update(candidate_type="new_position", action="new_position")
        gated = apply_portfolio_decision_gate({"holdings": [] if candidate else [requested], "candidates": [requested] if candidate else [],
            "data_quality_grade": "B", "quality_gate": {"grade": "B", "status": "passed"}}, portfolio_context=pair_context, as_of=cutoff)
        row = gated["candidates" if candidate else "holdings"][0]
        diagnostics[name] = {"portfolio_gate": row.get("portfolio_gate"), "reasons": row.get("portfolio_gate_reasons", [])}
        if row.get("portfolio_gate") == "BLOCKED":
            return None
        quantity = float(row.get("proposed_qty") or row.get("quantity") or 0)
        delta = quantity * (1 if row["action"] in {"add", "new_position"} else -1 if row["action"] in {"reduce", "sell"} else 0)
        qty = float(position.get("qty") or 0) + delta
        if qty < 0:
            return None
        scores[name] = qty * float(price) / float(assets) * float(sample["market_return"])
        outputs[name] = {"model_decision": value, "gated_decision": row}
    return {"baseline": scores["without_learning"], "learned": scores["with_learning"],
            "basis": "PAIRED_FROZEN_DECISION_REPLAY_GROSS_PORTFOLIO_CONTRIBUTION",
            "outputs": outputs, "diagnostics": diagnostics, "model": model_profile_identity(profile),
            "input_hash": hash_input(payload), "costs_included": False, "real_account_pnl": False,
            "independent_forward_sample": True, "causal_proof": False}


def _valid_decision(raw, code, refs):
    try:
        value = ReplayDecision.model_validate(raw)
        return (value.code == code and value.action in {"add", "reduce", "sell", "hold", "no_action"}
                and bool(value.evidence_refs) and set(value.evidence_refs) <= set(refs)
                and (value.quantity > 0 if value.action in {"add", "reduce", "sell"} else value.quantity == 0))
    except (TypeError, ValueError):
        return False
