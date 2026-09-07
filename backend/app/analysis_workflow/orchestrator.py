"""Bounded parallel phases and real claim dependencies on NodeExecutor."""
from __future__ import annotations

import copy
import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event
from typing import Any, Callable

from sqlalchemy.orm import joinedload

from ..config import settings
from ..services import model_client
from ..v2_models import ModelProfile
from .agents import agent_instruction, normalise_output, prompt_metadata, validate_output
from .constants import ArtifactType, DebateType, NodeStatus, StageStatus
from .context import compress_payload
from .dag import ANALYST_ROLES, DEBATE_NODES, RISK_ROLES
from .executor import NodeExecuteResult
from .evidence import model_profile_identity
from .failures import NodeCancelled, ResumeRejected
from .models import AnalysisStage
from .resume import hash_input


def _worst(*grades: str) -> str:
    return max(grades, key=lambda grade: {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}.get(grade, 4))


def analyst_evidence(results: dict[str, NodeExecuteResult]) -> dict[str, Any]:
    reports, failures, gaps, grades = [], {}, [], ["A"]
    for role, result in results.items():
        if role not in ANALYST_ROLES:
            continue
        if result.status not in NodeStatus.SUCCESS:
            failures[role] = {"status": result.status.upper(), "failure_class": result.failure_class, "warning": result.warning}
            gaps.append(f"{role}:{result.status}")
            grades.append("B" if role == "lockup_supply_analyst" else "C")
            continue
        report = copy.deepcopy(result.output)
        grade = report["quality_grade"]
        text = report["summary"] + "".join(item["statement"] for item in report["findings"])
        checks = []
        if len(text) < 200:
            grade = _worst(grade, "D")
            checks.append("report_min_chars")
        fields_missing = report.get("missing_checklist_fields") or []
        if len(fields_missing) >= 3:
            grade = _worst(grade, "B")
            checks.append("mandatory_fields_missing")
        if not report.get("data_table"):
            grade = _worst(grade, "B")
            checks.append("summary_data_table_missing")
        missing_count = len(report["data_gaps"])
        total = missing_count + len(report["findings"])
        missing_ratio = missing_count / total if total else 1.0
        if missing_ratio > 0.7:
            grade = _worst(grade, "D")
            checks.append("data_missing_ratio_critical")
        elif missing_ratio > 0.4:
            grade = _worst(grade, "C")
            checks.append("data_missing_ratio_max")
        report["quality_grade"] = grade
        report["quality_checks"] = checks
        reports.append(report)
        grades.append(grade)
        gaps.extend(f"{role}:{gap}" for gap in report["data_gaps"])
    market_report = next((report for report in reports if report["role"] == "market_analyst"), {})
    return {
        "market_read": market_report.get("summary", ""),
        "analyst_reports": reports,
        "agent_results": {report["role"]: report for report in reports},
        "agent_statuses": {key: result.status.upper() for key, result in results.items() if key in ANALYST_ROLES},
        "agent_failures": failures,
        "holding_evidence": [finding for report in reports for finding in report["findings"]],
        "portfolio_risks": [risk for report in reports for risk in report["portfolio_risks"]],
        "data_gaps": list(dict.fromkeys(gaps)),
        "quality_grade": _worst(*grades),
    }


def _claims(results: dict[str, NodeExecuteResult]) -> list[dict[str, Any]]:
    return [claim for result in results.values() for claim in (result.output or {}).get("claims", [])]


class AgentOrchestrator:
    def __init__(self, audit, frozen, *, stop_event: Event, cancel_check: Callable[[], bool], max_parallel: int | None = None):
        self.audit = audit
        self.frozen = frozen
        self.stop_event = stop_event
        self.cancel_check = cancel_check
        self.max_parallel = max_parallel or settings.ANALYSIS_MAX_PARALLEL_AGENTS
        self.profile_contract = {row["id"]: row for row in json.loads(frozen.content)["contract"]["profiles"]}

    def _execute(self, open_scope, key: str, profile_id: int | None, system: str, payload: dict[str, Any]) -> NodeExecuteResult:
        audit = open_scope()
        try:
            with audit.write_lock:
                profile = audit.db.query(ModelProfile).options(joinedload(ModelProfile.provider)).filter_by(id=profile_id).first() if profile_id else None
                audit.db.commit()

            def call(context_mode="full"):
                if profile is None:
                    raise RuntimeError("default_analysis_model_not_configured")
                if hash_input(model_profile_identity(profile)) != hash_input(self.profile_contract.get(profile.id)):
                    raise ResumeRejected("model_profile_changed_create_new_run")
                body = compress_payload(payload, context_mode)
                instruction = agent_instruction(key)
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": instruction + "\n\n输入数据：\n" + json.dumps(body, ensure_ascii=False, default=str)},
                ]
                audit.record_artifact(ArtifactType.PROMPT_TEMPLATE, {**prompt_metadata(key), "instruction": instruction}, artifact_key=f"{key}.template")
                audit.record_artifact(ArtifactType.RENDERED_PROMPT, {"messages": messages, "context_mode": context_mode}, artifact_key=f"{key}.rendered")
                with model_client.model_cancellation(self.stop_event.is_set):
                    result = model_client.call_model_json(profile, messages, validator=lambda value: validate_output(key, value, payload))
                audit.record_model_result(result, key)
                value = normalise_output(key, result.data, attempt_no=audit._attempt().attempt_no)
                if value.get("claims"):
                    audit.record_claims(value["claims"], debate_type=DebateType.RISK if key in RISK_ROLES else DebateType.INVESTMENT)
                if key == "claim_resolver":
                    audit.resolve_claims(value["resolutions"])
                return value

            return audit.executor.execute(
                key, call, input_payload=payload, profile=profile, metadata=prompt_metadata(key),
                cancelled=self.stop_event.is_set,
            )
        finally:
            audit.close()

    def execute_phase(self, phase_key: str, *, profile_id: int | None, system: str, context: dict[str, Any]) -> dict[str, NodeExecuteResult]:
        phase = self.audit.plan.phase(phase_key)
        stages = {row.phase_key: row.status for row in self.audit.db.query(AnalysisStage).filter_by(analysis_run_id=self.audit.run_id)}
        if any(stages.get(key) != StageStatus.COMPLETED for key in phase.dependencies):
            raise RuntimeError(f"phase_dependencies_not_complete:{phase_key}")
        self.audit.db.commit()
        open_scope = self.audit.node_scope_factory()
        pending = {node.node_key: node for node in phase.nodes if node.llm}
        phase_keys = set(pending)
        results: dict[str, NodeExecuteResult] = {}
        error: Exception | None = None
        running = {}
        # Only the coordinator mutates results/pending/checkpoints. Each worker
        # owns its SQLAlchemy Session and recorder, including retries/artifacts.
        with ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="analysis-agent") as pool:
            while pending or running:
                if self.cancel_check():
                    self.stop_event.set()
                    error = NodeCancelled()
                if error is None:
                    ready = [node for node in pending.values() if set(node.dependencies).intersection(phase_keys) <= results.keys()]
                    for node in ready[:max(0, self.max_parallel - len(running))]:
                        payload = self.frozen.payload(**copy.deepcopy(context))
                        prior = _claims(results)
                        if "_round_" in node.node_key or node.node_key == "claim_resolver":
                            payload["prior_claims"] = prior
                            previous = next((key for key in reversed(DEBATE_NODES) if key in node.dependencies and key in results), None)
                            payload["opponent_claims"] = (results[previous].output or {}).get("claims", []) if previous else []
                        if node.node_key == "risk_synthesis":
                            payload["risk_agent_results"] = {key: result.output for key, result in results.items()}
                            payload["agent_statuses"] = {key: result.status.upper() for key, result in results.items()}
                            payload["agent_failures"] = {key: result.warning for key, result in results.items() if result.status not in NodeStatus.SUCCESS}
                        future = pool.submit(self._execute, open_scope, node.node_key, profile_id, system, payload)
                        running[future] = node.node_key
                        del pending[node.node_key]
                if not running:
                    if error:
                        break
                    if pending:
                        raise RuntimeError(f"workflow_dependency_deadlock:{phase_key}")
                    break
                done, _ = wait(running, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    key = running.pop(future)
                    try:
                        results[key] = future.result()
                    except Exception as exc:
                        error = error or exc
                    self.audit.sync_node_state()
                    self.audit.checkpoint("AGENT_PROGRESS", extra={"phase": phase_key})
            # Do not cancel successful/in-flight siblings on an IMPORTANT
            # failure. MANDATORY/cancel stops admission; in-flight work drains.
        if error:
            raise error
        return {node.node_key: results[node.node_key] for node in phase.nodes if node.node_key in results}

    def analysts(self, *, profile_id, system: str) -> dict[str, Any]:
        return analyst_evidence(self.execute_phase("analysts_running", profile_id=profile_id, system=system, context={}))

    def investment(self, *, profile_id, system: str, evidence: dict, quality_gate: dict) -> dict[str, Any]:
        results = self.execute_phase("investment_debate", profile_id=profile_id, system=system, context={
            "evidence_pack": evidence, "quality_gate": quality_gate,
        })
        claims = _claims(results)
        resolution = results["claim_resolver"].output
        statuses = {item["claim_id"]: item["status"].lower() for item in resolution["resolutions"]}
        claims = [{**claim, "status": statuses[claim["claim_id"]]} for claim in claims]
        unresolved = [claim["claim_id"] for claim in claims if claim["status"] == "unresolved"]
        return {
            "bull_claims": [claim for claim in claims if claim["speaker"] == "bull"],
            "bear_claims": [claim for claim in claims if claim["speaker"] == "bear"],
            "resolved_claims": [claim for claim in claims if claim["status"] != "unresolved"],
            "unresolved_claims": [claim for claim in claims if claim["status"] == "unresolved"],
            "unresolved_claim_ids": unresolved,
            "round_summaries": [{"node": key, "round": int(key[-1]), "summary": result.output["summary"]} for key, result in results.items() if "_round_" in key],
            "judge_decision": resolution["summary"],
            "claim_resolver": resolution,
        }

    def risks(self, *, profile_id, system: str, trader: dict, risk_revision: dict, quality_gate: dict) -> dict[str, Any]:
        results = self.execute_phase("risk_debate", profile_id=profile_id, system=system, context={
            "trader_proposal": trader, "risk_revision": risk_revision, "quality_gate": quality_gate,
        })
        claims = _claims(results)
        synthesis = results["risk_synthesis"].output
        failures = {key: result.warning for key, result in results.items() if result.status not in NodeStatus.SUCCESS}
        return {
            **{f"{role}_claims": [claim for claim in claims if claim["speaker"] == role] for role in ("aggressive", "neutral", "conservative")},
            "unresolved_claim_ids": [claim["claim_id"] for claim in claims],
            "round_summaries": [{"round": 1, "summary": synthesis["summary"]}],
            "judge_decision": synthesis["summary"],
            "agent_statuses": {key: result.status.upper() for key, result in results.items()},
            "agent_failures": failures,
            "risk_synthesis": synthesis,
        }
