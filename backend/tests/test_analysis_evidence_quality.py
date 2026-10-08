"""Evidence completeness and presentation quality have separate consequences."""
from __future__ import annotations

import copy
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_evidence_quality_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)


def _report(role="market_analyst", **updates):
    from app.analysis_workflow.constants import NodeStatus
    from app.analysis_workflow.executor import NodeExecuteResult

    report = {
        "role": role,
        "scope": "portfolio",
        "summary": "报价与已确认持仓匹配，集中度超过风险上限。",
        "findings": [{
            "instrument": "600519",
            "statement": "可卖数量为 100 股，报价来源已验证。",
            "direction": "negative",
            "importance": "high",
            "confidence": 0.9,
            "evidence_refs": ["market.quotes.600519", "input.snapshot.holdings"],
            "evidence_type": "fact",
        }],
        "portfolio_risks": ["持仓集中度过高"],
        "data_gaps": [],
        "quality_grade": "A",
        "data_table": [{"field": "quote", "evidence_ref": "market.quotes.600519"}],
        "missing_checklist_fields": [],
    }
    report.update(updates)
    return NodeExecuteResult(node_key=role, status=NodeStatus.SUCCEEDED, output=report)


def test_concise_cited_report_preserves_evidence_quality_and_source():
    from app.analysis_workflow.orchestrator import analyst_evidence

    result = _report()
    original = copy.deepcopy(result.output)
    evidence = analyst_evidence({"market_analyst": result})

    assert evidence["quality_grade"] == "A"
    assert evidence["action_quality_grade"] == "A"
    assert evidence["risk_increase_allowed"] is True
    assert evidence["content_quality_grade"] == "B"
    assert evidence["agent_results"]["market_analyst"]["content_quality_checks"] == ["report_min_chars"]
    assert evidence["holding_evidence"] == original["findings"]
    assert result.output == original


def test_report_length_alone_does_not_change_evidence_grade():
    from app.analysis_workflow.orchestrator import analyst_evidence

    concise = analyst_evidence({"market_analyst": _report()})
    verbose = analyst_evidence({"market_analyst": _report(summary="完整且有来源的公开分析结论。" * 30)})

    assert concise["quality_grade"] == verbose["quality_grade"] == "A"
    assert verbose["content_quality_grade"] == "A"


def test_presentation_gaps_do_not_become_action_data_failures():
    from app.analysis_workflow.orchestrator import analyst_evidence

    evidence = analyst_evidence({"market_analyst": _report(
        data_table=[], missing_checklist_fields=["section_1", "section_2", "section_3"],
    )})

    assert evidence["quality_grade"] == "A"
    report = evidence["agent_results"]["market_analyst"]
    assert set(report["content_quality_checks"]) == {"report_min_chars", "mandatory_fields_missing", "summary_data_table_missing"}
    assert evidence["content_quality_grade"] == "B"


@pytest.mark.parametrize("findings", [[], [{
    "instrument": "600519", "statement": "没有来源支撑的长结论。" * 30,
    "direction": "negative", "importance": "high", "confidence": 0.9,
    "evidence_refs": [], "evidence_type": "inference",
}]])
def test_length_cannot_mask_missing_evidence(findings):
    from app.analysis_workflow.orchestrator import analyst_evidence

    evidence = analyst_evidence({"market_analyst": _report(summary="公开结论。" * 50, findings=findings)})

    assert evidence["quality_grade"] == "D"
    assert evidence["content_quality_grade"] == "D"
    assert evidence["action_quality_grade"] == "D"
    assert evidence["risk_increase_allowed"] is False


def test_optional_failure_and_missing_domain_data_remain_visible():
    from app.analysis_workflow.constants import NodeStatus
    from app.analysis_workflow.executor import NodeExecuteResult
    from app.analysis_workflow.orchestrator import analyst_evidence

    evidence = analyst_evidence({
        "market_analyst": _report(),
        "fundamentals_analyst": _report("fundamentals_analyst", data_gaps=["revenue", "profit", "cash_flow", "valuation"]),
        "lockup_supply_analyst": NodeExecuteResult(
            node_key="lockup_supply_analyst", status=NodeStatus.SKIPPED,
            warning="source unavailable", failure_class="permanent",
        ),
    })

    assert evidence["quality_grade"] == "D"
    assert evidence["action_quality_grade"] == "A"
    assert evidence["risk_increase_allowed"] is False
    assert evidence["agent_results"]["market_analyst"]["quality_grade"] == "A"
    assert evidence["agent_failures"]["lockup_supply_analyst"]["status"] == "SKIPPED"
    assert "fundamentals_analyst:cash_flow" in evidence["data_gaps"]
    assert "data_missing_ratio_critical" in evidence["agent_results"]["fundamentals_analyst"]["quality_checks"]


@pytest.mark.parametrize("include_failure", [False, True])
def test_market_evidence_failure_cannot_be_replaced_by_other_domains(include_failure):
    from app.analysis_workflow.constants import NodeStatus
    from app.analysis_workflow.executor import NodeExecuteResult
    from app.analysis_workflow.orchestrator import analyst_evidence

    results = {"news_analyst": _report("news_analyst")}
    if include_failure:
        results["market_analyst"] = NodeExecuteResult(node_key="market_analyst", status=NodeStatus.FAILED)
    evidence = analyst_evidence(results)

    assert evidence["action_quality_grade"] == "D"
    assert evidence["risk_increase_allowed"] is False


def test_partial_data_gap_preserves_reduction_evidence_without_new_risk():
    from app.analysis_workflow.orchestrator import analyst_evidence

    result = _report(data_gaps=["turnover_history"])
    result.output["findings"] *= 3
    evidence = analyst_evidence({"market_analyst": result})

    assert evidence["quality_grade"] == "A"
    assert evidence["action_quality_grade"] == "A"
    assert evidence["risk_increase_allowed"] is False


def test_prompt_requests_evidence_without_a_word_count_target():
    from app.analysis_workflow.agents import agent_instruction

    prompt = agent_instruction("market_analyst")

    assert "at least 200 characters" not in prompt
    assert "evidence_refs" in prompt
    assert "T+1, available quantity, cash, lot size, hard caps" in prompt
