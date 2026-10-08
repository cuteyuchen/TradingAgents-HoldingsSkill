"""V3 Phase A decision-contract and deterministic normalisation tests."""
import json
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)


def _candidate(code: str, score: int = 8, *, held_reason: bool = False) -> dict:
    return {
        "code": code,
        "name": code,
        "candidate_type": "new_position",
        "reason_detail": {
            "catalyst": "政策催化" if not held_reason else "持仓加仓",
            "capital_flow": "资金净流入",
            "sector_position": "板块强势",
        },
        "score": score,
    }


def test_no_action_when_no_change():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {
            "data_quality_grade": "A",
            "final_rating": "hold",
            "holdings": [
                {"code": "600519", "action": "hold"},
                {"code": "510300", "action": "watch"},
            ],
            "candidates": [],
            "risk_warnings": [],
            "quality_gate": {"status": "pass", "grade": "A"},
        },
        [
            {"code": "600519", "name": "贵州茅台", "available_qty": 80},
            {"code": "510300", "name": "沪深300ETF", "available_qty": 100},
        ],
        "A",
    )

    assert result["final_rating"] == "no_action"
    assert result["portfolio_manager_final"]["portfolio_rating"] == "no_action"


def test_quality_block_preserves_watch_only():
    from app.services.analysis_engine import _blocked_result

    result = _blocked_result(
        {"holdings": [{"code": "600519", "name": "贵州茅台", "available_qty": 0}]},
        {"quality_grade": "F", "quotes": {}, "errors": ["quote_coverage"], "source_chain": []},
    )

    assert result["final_rating"] == "watch_only"
    assert result["portfolio_manager_final"]["portfolio_rating"] == "watch_only"


def test_portfolio_gate_failure_fail_closes_new_position_candidates():
    from app.services.analysis_engine import _fail_closed_portfolio_gate_result

    result = _fail_closed_portfolio_gate_result(
        {
            "final_rating": "add",
            "holdings": [{"code": "600519", "action": "add", "target_weight": 0.2}],
            "candidates": [{"code": "510300", "candidate_type": "new_position", "action": "new_position", "buyable": True}],
        },
        RuntimeError("portfolio gate unavailable"),
    )

    assert result["final_rating"] == "watch_only"
    assert result["holdings"][0]["action"] == "watch"
    assert result["candidates"][0]["buyable"] is False
    assert result["candidates"][0]["actionable"] is False
    assert result["decision_gate"]["portfolio_action"] == "WATCH_ONLY"


def test_candidate_zero_valid():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {"data_quality_grade": "A", "holdings": [{"code": "600519", "action": "hold"}], "risk_warnings": []},
        [{"code": "600519", "name": "贵州茅台", "available_qty": 80}],
        "A",
        {"quality_gate": {"status": "pass", "grade": "A"}, "candidates": []},
    )

    assert result["candidates"] == []
    assert result["buy_candidates"] == []
    assert result["final_rating"] == "no_action"


def test_candidate_max_three_filters_before_truncation():
    from app.services.analysis_engine import _normalize_final

    raw = [
        {"code": "", "score": 10},
        _candidate("600519", 10),  # held: remove before count cap
        _candidate("000001", 6),
        _candidate("000002", 8),
        _candidate("000003", 7),
        _candidate("000004", 9),
        {"code": "000005", "score": 10, "reason_detail": {}},  # missing core evidence
    ]
    result = _normalize_final(
        {"data_quality_grade": "A", "holdings": [{"code": "600519", "action": "hold"}], "risk_warnings": []},
        [{"code": "600519", "name": "贵州茅台", "available_qty": 80}],
        "A",
        {"quality_gate": {"status": "pass", "grade": "A"}, "candidates": raw},
    )

    assert len(result["candidates"]) <= 3
    assert [row["code"] for row in result["candidates"]] == ["000004", "000002", "000003"]


def test_invalid_candidate_code_is_removed():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {"data_quality_grade": "A", "holdings": [], "risk_warnings": []},
        [],
        "A",
        {
            "quality_gate": {"status": "pass", "grade": "A"},
            "candidates": [_candidate("not-a-code", 10)],
        },
    )

    assert result["candidates"] == []


def test_current_holding_removed_from_candidate_but_holding_add_remains():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {
            "data_quality_grade": "A",
            "holdings": [{"code": "600519", "action": "add", "reason": "趋势确认"}],
            "risk_warnings": [],
        },
        [{"code": "600519", "name": "贵州茅台", "available_qty": 80}],
        "A",
        {"quality_gate": {"status": "pass", "grade": "A"}, "candidates": [_candidate("600519")]},
    )

    assert result["holdings"][0]["action"] == "add"
    assert result["candidates"] == []


def test_blocked_candidate_scan_overrides_stale_final_candidates():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {
            "data_quality_grade": "A",
            "holdings": [{"code": "600519", "action": "hold"}],
            "candidates": [_candidate("000001")],
            "risk_warnings": [],
        },
        [{"code": "600519", "name": "贵州茅台", "available_qty": 80}],
        "A",
        {
            "quality_gate": {"status": "pass", "grade": "A"},
            "candidates": [],
            "candidate_status": "blocked_missing_evidence",
        },
    )

    assert result["candidates"] == []
    assert result["buy_candidates"] == []
    assert result["final_rating"] == "no_action"


def test_subthreshold_candidate_does_not_block_no_action():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {
            "data_quality_grade": "A",
            "final_rating": "hold",
            "holdings": [{"code": "600519", "action": "hold"}],
            "risk_warnings": [],
        },
        [{"code": "600519", "name": "贵州茅台", "available_qty": 80}],
        "A",
        {
            "quality_gate": {"status": "pass", "grade": "A"},
            "candidates": [_candidate("000001", 6)],
        },
    )

    assert result["candidates"] == []
    assert result["buy_candidates"] == []
    assert result["final_rating"] == "no_action"


def test_rotation_watch_does_not_enter_action_candidates_or_block_no_action():
    from app.services.analysis_engine import _normalize_final

    candidate = _candidate("000001", 9)
    candidate["candidate_type"] = "rotation_watch"
    result = _normalize_final(
        {
            "data_quality_grade": "A",
            "final_rating": "hold",
            "holdings": [{"code": "600519", "action": "hold"}],
            "risk_warnings": [],
        },
        [{"code": "600519", "name": "贵州茅台", "available_qty": 80}],
        "A",
        {
            "quality_gate": {"status": "pass", "grade": "A"},
            "candidates": [candidate],
        },
    )

    assert result["candidates"] == []
    assert result["buy_candidates"] == []
    assert result["final_rating"] == "no_action"


def test_no_action_overwrites_stale_conclusion_and_final_actions():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {
            "data_quality_grade": "A",
            "final_rating": "hold",
            "portfolio_conclusion": "建议减仓通信ETF并换入半导体。",
            "holdings": [{"code": "600519", "action": "hold", "reason": "趋势与风险平衡"}],
            "today_actions": [{"code": "600519", "action": "sell", "quantity": "80"}],
            "portfolio_manager_final": {
                "portfolio_rating": "hold",
                "final_actions": [{"code": "600519", "action": "sell", "quantity": "80"}],
            },
            "trader_proposal": {
                "orders": [{"code": "600519", "action": "sell", "quantity": "80"}],
            },
            "rebalance_plan": {"action": "换仓"},
            "risk_warnings": [],
        },
        [{"code": "600519", "name": "贵州茅台", "available_qty": 80}],
        "A",
        {"quality_gate": {"status": "pass", "grade": "A"}, "candidates": []},
    )

    assert result["final_rating"] == "no_action"
    assert result["portfolio_conclusion"] == "当前没有足够证据证明调整组合优于保持现状，维持当前组合。"
    assert result["portfolio_manager_final"]["portfolio_rating"] == "no_action"
    assert result["today_actions"] == result["holdings"]
    assert result["portfolio_manager_final"]["final_actions"] == result["holdings"]
    assert result["trader_proposal"]["orders"] == result["holdings"]
    assert result["trader_proposal"]["decision"] == "hold"
    assert result["rebalance_plan"] == {}
    assert all(row["action"] in {"hold", "watch"} for row in result["today_actions"])


def test_blocked_quality_gate_cannot_become_no_action_during_normalization():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {
            "data_quality_grade": "F",
            "final_rating": "no_action",
            "portfolio_conclusion": "保持现状",
            "holdings": [{"code": "600519", "action": "hold"}],
            "risk_warnings": [],
        },
        [{"code": "600519", "name": "贵州茅台", "available_qty": 80}],
        "F",
        {"quality_gate": {"status": "blocked", "grade": "F"}, "candidates": []},
    )

    assert result["final_rating"] == "watch_only"
    assert result["portfolio_manager_final"]["portfolio_rating"] == "watch_only"


def test_analysis_mode_alias_and_standard_mode():
    from app.decision_contract import canonicalize_analysis_mode
    from app.v2_schemas import AnalysisJobCreate

    assert canonicalize_analysis_mode("quick") == "fast"
    assert canonicalize_analysis_mode("standard") == "standard"
    assert AnalysisJobCreate(snapshot_id=1, mode="quick").mode == "fast"
    assert AnalysisJobCreate(snapshot_id=1, mode="standard").mode == "standard"


def test_contract_runtime_sync():
    from app.decision_contract import decision_contract_payload

    runtime_path = Path(BACKEND_DIR).parent / "skill" / "tradingagents-holdings-advisor" / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert runtime["decision_contract"] == decision_contract_payload()


@pytest.mark.parametrize("raw,expected", [
    (100, 100.0), (100.0, 100.0), ("100股", 100.0), (" 100 份 ", 100.0),
    ("1,000股", 1000.0), ("100.0", 100.0), (0, 0.0),
    ("20%", None), ("20％", None), ("100股或20%", None),
    ("1,00", None), (0.2, None), (-100, None), (True, None),
    (float("nan"), None), (float("inf"), None), ("NaN", None),
    (10 ** 400, None),
])
def test_share_quantity_requires_finite_nonnegative_whole_shares(raw, expected):
    from app.decision_contract import parse_share_quantity

    assert parse_share_quantity(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (0.2, 0.2), ("0.2", 0.2), ("20%", 0.2), ("20％", 0.2),
    (0, 0.0), (1, 1.0), ("100%", 1.0),
    ("100股", None), (20, None), ("20", None), ("20%%", None),
    (-0.2, None), (True, None), (float("nan"), None), (float("inf"), None),
    ("120%", None), (10 ** 400, None),
])
def test_target_weight_requires_ratio_or_explicit_percent(raw, expected):
    from app.decision_contract import parse_target_weight

    assert parse_target_weight(raw) == expected


@pytest.mark.parametrize("row,errors", [
    ({"quantity": "100股", "proposed_qty": 100, "target_weight": "20%"}, ()),
    ({"quantity": "20%", "proposed_qty": 100}, ("QUANTITY_INVALID",)),
    ({"quantity": "100股", "proposed_qty": "20%"}, ("QUANTITY_INVALID",)),
    ({"quantity": 100, "proposed_qty": 200}, ("QUANTITY_CONFLICT",)),
    ({"quantity": 0}, ("QUANTITY_INVALID",)),
    ({"quantity": 100, "target_weight": "100股"}, ("TARGET_WEIGHT_INVALID",)),
])
def test_action_sizing_never_hides_invalid_or_conflicting_fields(row, errors):
    from app.decision_contract import normalize_action_sizing

    sizing = normalize_action_sizing(row)
    assert sizing.errors == errors
    if not errors:
        assert sizing.quantity == 100
        assert sizing.target_weight == 0.2
    elif any(reason.startswith("QUANTITY_") for reason in errors):
        assert sizing.quantity is None


@pytest.mark.parametrize("result,expected", [
    ({}, "INCOMPLETE"),
    ({"final_rating": "no_action", "holdings": []}, "INCOMPLETE"),
    ({"holdings": [{"code": "600519"}]}, "INCOMPLETE"),
    ({"holdings": [{"action": "hold"}]}, "NO_ACTION"),
    ({"holdings": [{"action": "watch"}]}, "WAITING"),
    ({"holdings": [{"action": "conditional_add"}]}, "WAITING"),
    ({"holdings": [{"action": "sell"}]}, "ACTION"),
    ({"holdings": [{"action": "watch", "portfolio_gate": "BLOCKED"}]}, "DATA_GAP"),
    ({"holdings": [{"action": "watch", "decision_status": "INCOMPLETE"}]}, "INCOMPLETE"),
    ({"holdings": [{"action": "hold"}], "quality_gate": {"grade": "F"}}, "DATA_GAP"),
    ({"holdings": [{"action": "hold"}], "candidates": [{"candidate_type": "new_position", "buyable": True}]}, "ACTION"),
])
def test_explicit_decision_states_do_not_conflate_missing_waiting_and_holding(result, expected):
    from app.decision_contract import apply_decision_status

    assert apply_decision_status(result)["decision_status"] == expected
    assert all("decision_status" in row for row in result.get("holdings", []))
