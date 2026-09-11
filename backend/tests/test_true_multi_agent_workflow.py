"""CORE-3: real independent calls, parallel audit ownership and frozen resume."""
from __future__ import annotations

import copy
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from test_analysis_workflow_audit_integration import _empty_candidates, _market_ok, _register_and_seed
from app.analysis_workflow.agents import validate_output
from app.analysis_workflow.constants import NodeStatus
from app.analysis_workflow.dag import ANALYST_ROLES, DEBATE_NODES, RISK_ROLES, build_workflow_plan
from app.analysis_workflow.models import AnalysisArtifact, AnalysisClaim, AnalysisNode, AnalysisNodeAttempt
from app.analysis_workflow.resume import resume_from_checkpoint
from app.config import settings
from app.clock import utc_now
from app.database import SessionLocal
from app.main import app
from app.services import analysis_engine, model_client
from app.v2_models import AnalysisJob, AnalysisRun, PortfolioSnapshot


class ModelHarness:
    def __init__(self):
        self.calls = []
        self.failures = {}
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.block_keys = set()
        self.barriers = {}
        self.revise = False
        self.unsafe = False

    def __call__(self, profile, messages, **kwargs):
        payload = model_client._acceptance_input(messages)
        instruction = model_client._acceptance_phase_content(messages).split("输入数据：")[0]
        if instruction.startswith("Independent Agent Node: "):
            key = instruction.splitlines()[0].split(": ", 1)[1]
        else:
            key = next(key for phrase, key in (
                ("研究总监裁决", "research_manager"), ("唯一一次修正", "trader_revision"),
                ("风控经理审查", "risk_manager"), ("交易员方案", "trader"),
                ("组合经理最终决策", "portfolio_manager"), ("deterministic_action_candidates", "candidate_llm_review"),
            ) if phrase in instruction)
        with self.lock:
            self.calls.append({"key": key, "payload": copy.deepcopy(payload), "started": time.monotonic(), "thread": threading.get_ident()})
            number = sum(item["key"] == key for item in self.calls)
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 3:
                self.waiting.set()
        try:
            if (self.block and key in ANALYST_ROLES) or key in self.block_keys:
                assert self.release.wait(10)
            if key in self.barriers:
                self.barriers[key].wait(timeout=15)
            time.sleep(0.02)
            failure = self.failures.get((key, number))
            if failure:
                raise failure
            data = self.output(key, payload)
            return model_client.ModelResult(
                text=json.dumps(data), latency_ms=20,
                raw={"id": f"request-{key}-{number}", "usage": {"prompt_tokens": 13, "completion_tokens": 7}},
            )
        finally:
            with self.lock:
                self.active -= 1

    def output(self, key, payload):
        ref = next((ref for ref in payload.get("evidence_refs", []) if ref.startswith("market.quotes.")), "market")
        if key in ANALYST_ROLES:
            return {
                "role": key, "scope": "portfolio",
                "summary": (
                    "The deterministic test evidence contains a confirmed holding and a quote at the frozen timestamp. "
                    "This role reports only its assigned domain, distinguishes missing inputs from zero values, "
                    "and leaves all position sizing and execution authority with the deterministic portfolio gate."
                ),
                "findings": [{
                    "instrument": "600519", "statement": "The fixture quote matches the frozen snapshot.",
                    "direction": "neutral", "importance": "medium", "confidence": 0.8,
                    "evidence_refs": [ref],
                    "evidence_type": "provider_derived" if key == "capital_flow_analyst" else "fact",
                }],
                "portfolio_risks": [], "data_gaps": [], "quality_grade": "A",
                "data_table": [{"field": "quote", "evidence_ref": ref}],
            }
        if key in RISK_ROLES or "_round_" in key:
            opponents = payload.get("opponent_claims") or []
            return {
                "role": key.split("_round_")[0], "summary": f"Public position from {key}",
                "claims": [{
                    "statement": f"{key} responds to the actual evidence and prior claim.",
                    "evidence_refs": [ref], "confidence": 0.8,
                    "target_claim_ids": [opponents[0]["claim_id"]] if opponents else [],
                    "parent_claim_id": opponents[0]["claim_id"] if opponents else None,
                }],
            }
        if key == "claim_resolver":
            return {
                "resolutions": [{"claim_id": claim["claim_id"], "status": "PARTIALLY_ACCEPTED", "rationale_summary": "Evidence supports observation only."} for claim in payload["prior_claims"]],
                "summary": "Resolve existing claims without changing evidence.",
            }
        if key == "risk_synthesis":
            return {
                "consensus": ["Respect hard caps"], "disagreements": [], "hard_concerns": [],
                "recommended_exposure": 0.2, "unresolved_risks": [],
                "summary": "The three actual role outputs remain advisory.",
            }
        if key == "research_manager":
            return {"rating": "Hold", "strategic_action": "Keep a healthy portfolio unchanged.", "confidence": "medium", "key_risks": [], "conditions": []}
        if key in {"trader", "trader_revision"}:
            return {"orders": [{"code": "600519", "action": "hold", "quantity": None}]}
        if key == "risk_manager":
            return {"decision": "revise" if self.revise else "pass", "reason": "Keep verified limits.", "hard_constraints": []}
        if key == "candidate_llm_review":
            return {"accepted_codes": [], "veto_codes": []}
        return {
            "data_quality_grade": "A", "market_read": "Frozen test facts.",
            "portfolio_conclusion": "A healthy holding with no stronger candidates.",
            "final_rating": "add" if self.unsafe else "hold", "cash_target": "unchanged", "confidence": "medium",
            "holdings": [{"code": "600519", "action": "add" if self.unsafe else "hold", "target_weight": 0.9 if self.unsafe else None, "quantity": "999999" if self.unsafe else None}],
            "candidates": [],
        }

    def counts(self):
        return Counter(call["key"] for call in self.calls)


@pytest.fixture
def environment(monkeypatch):
    from app.market.providers.health import reset_runtime_provider_health_registry

    reset_runtime_provider_health_registry()
    monkeypatch.setattr(settings, "TRUE_MULTI_AGENT_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(settings, "ANALYSIS_MAX_PARALLEL_AGENTS", 3)
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: {
        **market, "final_quote_refresh_status": "ok", "final_quote_refresh_at": utc_now().isoformat(),
    })
    monkeypatch.setattr(analysis_engine, "_candidate_context_for_analysis", _empty_candidates)
    harness = ModelHarness()
    monkeypatch.setattr(model_client, "call_model", harness)
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)

    def make_job(mode="deep"):
        with SessionLocal() as db:
            snapshot = db.get(PortfolioSnapshot, snapshot_id)
            job = AnalysisJob(user_id=snapshot.user_id, portfolio_id=snapshot.portfolio_id, snapshot_id=snapshot.id, mode=mode, status="queued", notify=False)
            db.add(job)
            db.commit()
            return job.id

    yield harness, make_job, client, headers
    reset_runtime_provider_health_registry()


def _run(job_id):
    analysis_engine.run_analysis_job(job_id)
    with SessionLocal() as db:
        run = db.query(AnalysisRun).filter_by(job_id=job_id).one()
        db.expunge(run)
        return run


def test_deep_has_parallel_independent_nodes_and_artifact_ownership(environment):
    harness, make_job, client, headers = environment
    analyst_barrier = threading.Barrier(3)
    risk_barrier = threading.Barrier(3)
    harness.barriers = {
        **dict.fromkeys(ANALYST_ROLES[:3], analyst_barrier),
        **dict.fromkeys(RISK_ROLES, risk_barrier),
    }
    run = _run(make_job())
    assert run.status == "completed", run.error_message
    assert run.workflow_version == "v3-core-3"
    assert run.structured_result_json["workflow_execution"]["legacy_fallback_used"] is False
    assert 2 <= harness.peak <= 3
    expected = {*ANALYST_ROLES, *DEBATE_NODES, *RISK_ROLES, "risk_synthesis", "research_manager", "trader", "risk_manager", "portfolio_manager"}
    assert set(harness.counts()) == expected
    assert set(harness.counts().values()) == {1}
    assert not any("legacy" in key for key in harness.counts())
    binding = run.structured_result_json["workflow"]["evidence_snapshot"]
    assert all(all(call["payload"][key] == value for key, value in binding.items()) for call in harness.calls)
    with SessionLocal() as db:
        nodes = {node.node_key: node for node in db.query(AnalysisNode).filter_by(analysis_run_id=run.id)}
        attempts = list(db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run.id))
        ids = {attempt.id for attempt in attempts}
        assert len(ids) == len(attempts)
        for key in expected:
            rows = [attempt for attempt in attempts if attempt.node_id == nodes[key].id]
            assert len(rows) == 1
            assert rows[0].input_tokens == 13
            assert rows[0].output_tokens == 7
            assert rows[0].provider == "openai_compatible"
            assert rows[0].model == "test-model"
        by_id = {attempt.id: attempt for attempt in attempts}
        for artifact in db.query(AnalysisArtifact).filter_by(analysis_run_id=run.id):
            if artifact.attempt_id:
                assert artifact.node_id == by_id[artifact.attempt_id].node_id
                assert artifact.stage_id == by_id[artifact.attempt_id].stage_id
        risk_nodes = [nodes[key] for key in RISK_ROLES]
        assert max(node.started_at for node in risk_nodes) < min(node.completed_at for node in risk_nodes)
        assert nodes["portfolio_manager"].started_at >= nodes["risk_synthesis"].completed_at
        assert client.get(f"/api/v2/analysis/runs/{run.id}/workflow", headers=headers).status_code == 200


def test_only_news_retries_with_same_evidence(environment):
    harness, make_job, *_ = environment
    harness.failures[("news_analyst", 1)] = TimeoutError("news timeout")
    run = _run(make_job())
    assert run.status == "completed", run.error_message
    assert harness.counts()["news_analyst"] == 2
    assert all(harness.counts()[role] == 1 for role in ANALYST_ROLES if role != "news_analyst")
    calls = [call["payload"] for call in harness.calls if call["key"] == "news_analyst"]
    assert calls[0] == calls[1]
    with SessionLocal() as db:
        node = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="news_analyst").one()
        attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).order_by(AnalysisNodeAttempt.attempt_no).all()
        assert [attempt.status for attempt in attempts] == ["failed", "completed"]
        assert attempts[0].failure_class == "transient"
        assert attempts[0].input_hash == attempts[1].input_hash


@pytest.mark.parametrize("role,grade,status", [
    ("fundamentals_analyst", "C", "FAILED"), ("market_analyst", "D", "FAILED"), ("lockup_supply_analyst", "B", "SKIPPED"),
])
def test_criticality_is_visible_to_quality_gate(environment, role, grade, status):
    harness, make_job, *_ = environment
    harness.failures[(role, 1)] = RuntimeError("401 provider denied")
    harness.unsafe = True
    run = _run(make_job())
    assert run.status in {"completed", "blocked"}, run.error_message
    result = run.structured_result_json["result"]
    gate = result["quality_gate"]
    assert gate["agent_statuses"][role] == status
    assert gate["grade"] == grade
    assert role in gate["agent_failures"]
    assert any(role in error for error in result["phase_errors"])
    if role != "lockup_supply_analyst":
        assert all(row["action"] not in {"add", "conditional_add"} for row in result["holdings"])
        assert result["candidate_actions"] == []
    if role == "market_analyst":
        assert run.status == "blocked"
        assert "bull_round_1" not in harness.counts()
    assert all(harness.counts()[other] == 1 for other in ANALYST_ROLES if other != role)


def test_bull_bear_uses_real_preceding_claims_and_resolver_keeps_authorship(environment):
    harness, make_job, *_ = environment
    run = _run(make_job())
    assert run.status == "completed", run.error_message
    calls = {call["key"]: call["payload"] for call in harness.calls}
    keys = [call["key"] for call in harness.calls if call["key"] in DEBATE_NODES]
    assert keys == list(DEBATE_NODES)
    for before, after in zip(DEBATE_NODES[:3], DEBATE_NODES[1:4]):
        opponent = calls[after]["opponent_claims"]
        assert len(opponent) == 1
        assert before in opponent[0]["statement"]
        assert opponent[0]["claim_id"] in {claim["claim_id"] for claim in calls[after]["prior_claims"]}
    with SessionLocal() as db:
        claims = db.query(AnalysisClaim).filter_by(analysis_run_id=run.id, debate_type="investment").all()
        assert len(claims) == 4
        nodes = {node.id: node.node_key for node in db.query(AnalysisNode).filter_by(analysis_run_id=run.id)}
        assert {claim.status for claim in claims} == {"partially_accepted"}
        assert {claim.claim_id for claim in claims} == {"INV-BULL-001", "INV-BEAR-001", "INV-BULL-101", "INV-BEAR-101"}
        assert all(nodes[claim.node_id] in DEBATE_NODES[:4] for claim in claims)
        originals = db.query(AnalysisArtifact).filter_by(analysis_run_id=run.id, artifact_key="claims.investment").all()
        assert len(originals) == 4
        assert all(artifact.content_json[0]["status"] == "open" for artifact in originals)


def test_resume_parallel_phase_runs_only_failed_sibling(environment, monkeypatch):
    harness, make_job, client, headers = environment
    harness.failures[("fundamentals_analyst", 1)] = RuntimeError("401 temporarily misconfigured")
    stage = analysis_engine._job_stage
    paused = [False]

    def pause(db, job, name, progress):
        if name == "quality_gate" and not paused[0]:
            paused[0] = True
            raise RuntimeError("simulated_process_stop_after_agents")
        return stage(db, job, name, progress)

    monkeypatch.setattr(analysis_engine, "_job_stage", pause)
    job_id = make_job()
    first = _run(job_id)
    assert first.status == "failed"
    with SessionLocal() as db:
        contract = resume_from_checkpoint(db.get(AnalysisRun, first.id), db)
        assert set(ANALYST_ROLES) - {"fundamentals_analyst"} <= set(contract["completed_nodes"])
    response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        run = db.get(AnalysisRun, first.id)
        assert run.status == "completed", run.error_message
        assert run.structured_result_json["workflow"]["evidence_snapshot"] == first.structured_result_json.get("workflow", {}).get("evidence_snapshot", run.structured_result_json["workflow"]["evidence_snapshot"])
    assert harness.counts()["fundamentals_analyst"] == 2
    assert all(harness.counts()[role] == 1 for role in ANALYST_ROLES if role != "fundamentals_analyst")


@pytest.mark.parametrize("mode,analysts,maximum", [("fast", 2, 5), ("standard", 5, 9), ("deep", 7, 20)])
def test_explicit_mode_plans_and_no_action(environment, mode, analysts, maximum):
    harness, make_job, *_ = environment
    run = _run(make_job(mode))
    assert run.status == "completed", run.error_message
    assert sum(key in ANALYST_ROLES for key in harness.counts()) == analysts
    assert len(harness.calls) == maximum
    if mode != "deep":
        assert not set(harness.counts()).intersection({*DEBATE_NODES, *RISK_ROLES})
    final = run.structured_result_json["result"]
    assert final["outcome"] == "NO_ACTION"
    assert final["candidate_actions"] == []
    assert final["candidates"] == []
    build_workflow_plan(mode).validate()


def test_trader_revision_is_at_most_once(environment):
    harness, make_job, *_ = environment
    harness.revise = True
    run = _run(make_job())
    assert run.status == "completed", run.error_message
    assert harness.counts()["trader_revision"] == 1
    assert run.structured_result_json["result"]["risk_revision"]["revision_count"] == 1


def test_structured_validator_rejects_fabricated_claim_targets():
    payload = {"evidence_refs": ["market"], "prior_claims": [], "opponent_claims": []}
    raw = {"role": "bear", "summary": "public", "claims": [{"statement": "counter", "evidence_refs": ["market"], "confidence": 0.5, "target_claim_ids": ["imagined"], "parent_claim_id": None}]}
    assert validate_output("bear_round_1", raw, payload) is False
