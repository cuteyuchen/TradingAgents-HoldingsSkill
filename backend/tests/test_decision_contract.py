"""V3 Phase A decision-contract and deterministic normalisation tests."""
import json
import os
import sys
from pathlib import Path

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
