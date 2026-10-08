"""Phase F deterministic Candidate Engine contracts."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.candidates.config import CandidateConfig, DEFAULT_CONFIG
from app.candidates.decision_edge import TransactionCostModel, calculate_action_score, calculate_decision_edge
from app.candidates.entry import calculate_entry_score
from app.candidates.etf_score import score_etf_candidate
from app.candidates.factors import combine_components, percentile_rank
from app.candidates.portfolio_fit import calculate_portfolio_fit
from app.candidates.ranking import rank_candidates, take_stage_limits
from app.candidates.risk_reward import calculate_structure_risk_reward
from app.candidates.schemas import CandidateScanRequest
from app.candidates.stock_score import score_stock_candidate
from app.candidates.universe import build_candidate_universe


def _bars(count: int = 130, *, base: float = 100.0, amount: float = 5_000_000.0) -> list[dict]:
    rows: list[dict] = []
    for index in range(count):
        close = base + index * 0.08
        rows.append(
            {
                "trade_date": date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + index),
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "prev_close": close - 0.08,
                "volume": 100_000 + index * 100,
                "amount": amount,
                "turnover_rate": 2.0,
            }
        )
    return rows


def _security(code: str, *, name: str = "普通证券", exchange: str = "SSE", security_type: str = "STOCK", **kwargs):
    return {
        "code": code,
        "name": name,
        "exchange": exchange,
        "security_type": security_type,
        "status": "ACTIVE",
        "listing_date": date(2025, 1, 1),
        "raw_metadata_json": {},
        **kwargs,
    }


def _quote(code: str, *, quality: str = "VALID", price: float = 110.0, amount: float = 5_000_000.0) -> dict:
    return {"code": code, "quality_status": quality, "price": price, "amount": amount}


def test_factor_weights_and_missing_components_are_not_zero_filled():
    assert sum(DEFAULT_CONFIG.stock_factor_weights.values()) == pytest.approx(1.0)
    assert sum(DEFAULT_CONFIG.etf_factor_weights.values()) == pytest.approx(1.0)
    combined = combine_components(
        {
            "present": {"score": 80, "available": True, "confidence": 100},
            "missing": {"score": None, "available": False, "confidence": 0},
        },
        {"present": 0.4, "missing": 0.6},
    )
    assert combined["score"] == 80
    assert combined["coverage"] == pytest.approx(0.4)
    assert combined["confidence"] == pytest.approx(40)
    assert percentile_rank(99, [1, 2, 3], min_samples=50) is None


def test_universe_excludes_exchange_status_listing_and_held_rows():
    securities = [
        _security("600001"),
        _security("000001", exchange="SZSE"),
        _security("830001", exchange="BSE"),
        _security("600002", name="ST风险"),
        _security("600003", is_suspended=True),
        _security("600004", listing_date=date(2026, 7, 1)),
        _security("600005"),
        _security("510001", security_type="ETF", etf_category="BROAD_ETF"),
    ]
    bars = {security["code"]: _bars() for security in securities}
    quotes = {security["code"]: _quote(security["code"]) for security in securities}
    trading_days = {security["code"]: 100 for security in securities}
    trading_days["600004"] = 20
    result = build_candidate_universe(
        securities,
        quotes=quotes,
        bars=bars,
        held_codes=["600005"],
        as_of=datetime(2026, 8, 25),
        trading_days_by_code=trading_days,
        config=CandidateConfig(percentile_min_samples=1),
    )
    eligible_codes = {row["code"] for row in result["eligible"]}
    assert eligible_codes == {"600001", "000001", "510001"}
    assert result["exclusion_counts"]["UNIVERSE_BSE"] == 1
    assert result["exclusion_counts"]["UNIVERSE_ST"] == 1
    assert result["exclusion_counts"]["UNIVERSE_HELD"] == 1
    assert result["exclusion_counts"]["UNIVERSE_NEW_LISTING"] == 1


def test_listing_date_requires_trading_calendar_fact():
    result = build_candidate_universe(
        [_security("600001")],
        quotes={"600001": _quote("600001")},
        bars={"600001": _bars()},
        as_of=date(2026, 8, 25),
        trading_days_by_code={},
        config=CandidateConfig(percentile_min_samples=1),
    )
    assert result["eligible"] == []
    assert "TRADING_CALENDAR_MISSING" in result["exclusions"][0]["reason_codes"]


def test_stock_momentum_missing_middle_subfactor_keeps_original_weights():
    config = CandidateConfig(percentile_min_samples=50)
    score = score_stock_candidate("600001", _bars(25), config=config)
    momentum = score["components"]["momentum"]
    assert momentum["available"] is True
    assert momentum["raw"]["return20"] is not None
    assert momentum["raw"]["return60"] is None
    expected = 100.0 * (score["features"]["return20"] + 0.30) / 0.80
    assert momentum["score"] == pytest.approx(expected)
    assert momentum["confidence"] == 100


def test_future_fundamental_is_unavailable_at_as_of():
    score = score_stock_candidate(
        "600001",
        _bars(),
        as_of=datetime(2026, 8, 25, 10, 30),
        metadata={"fundamental": {"available_at": "2026-08-25T15:00:00", "roe": 0.3}},
        config=CandidateConfig(percentile_min_samples=1),
    )
    assert score["components"]["fundamental"]["available"] is False
    assert score["components"]["fundamental"]["score"] is None


def test_entry_atr_and_structure_risk_reward_are_separate_from_opportunity():
    bars = _bars()
    rr = calculate_structure_risk_reward(bars, price=110.0)
    entry = calculate_entry_score(bars, price=110.0, quote=_quote("600001"), risk_reward=rr)
    assert rr["atr14"] is not None
    assert rr["risk_reward_ratio"] is not None
    assert entry["risk_reward_ratio"] == rr["risk_reward_ratio"]
    assert calculate_action_score(90, 40, 90) == pytest.approx(77.5)


def test_portfolio_fit_exposes_correlation_hhi_and_probe_as_simulation():
    bars = _bars()
    fit = calculate_portfolio_fit(
        {"code": "600001", "security_type": "STOCK"},
        {
            "positions": [{"code": "600002", "weight": 0.50, "industry": "TECH"}],
            "cash_ratio": 0.10,
            "total_assets": 100_000,
            "hhi": 0.25,
            "portfolio_vol_60": 0.20,
        },
        holding_bars={"600002": bars},
        candidate_bars=bars,
    )
    assert fit["weighted_candidate_correlation"] == pytest.approx(1.0)
    assert fit["high_corr_positions"] == ["600002"]
    assert fit["probe_weight_is_simulation"] is True
    assert fit["projected_hhi"] > fit["current_hhi"]


def test_decision_edge_compares_no_action_holdings_and_transaction_cost():
    model = TransactionCostModel(commission_bps=5.0, minimum_commission=5.0, sell_tax_bps=10.0)
    edge = calculate_decision_edge(
        opportunity_score=88,
        entry_score=82,
        portfolio_fit_score=80,
        market_regime="RISK_ON",
        market_quality="VALID",
        held_baseline={"available": True, "median_held_opportunity_score": 70},
        total_assets=100_000,
        probe_weight=0.05,
        transaction_cost_model=model,
    )
    assert edge["action_score"] == pytest.approx(84.1)
    assert edge["edge_vs_no_action"] == pytest.approx(9.1)
    assert edge["edge_vs_current_holdings"] == pytest.approx(18)
    assert edge["probe_notional"] == pytest.approx(5_000)
    assert edge["estimated_cost"] == pytest.approx(5.0)
    assert edge["decision_edge"] < edge["raw_decision_edge"]


def test_frozen_market_forces_no_action_edge_below_gate():
    edge = calculate_decision_edge(
        opportunity_score=100,
        entry_score=100,
        portfolio_fit_score=100,
        market_regime="RISK_ON",
        market_quality="FROZEN",
        market_frozen=True,
    )
    assert edge["no_action_threshold"] == 100
    assert "MARKET_STATE_FROZEN" in edge["reason_codes"]


def test_etf_missing_constituent_breadth_reduces_coverage_without_zero_score():
    result = score_etf_candidate(
        "510001",
        _bars(),
        quote=_quote("510001"),
        metadata={"valuation": {"pe_ttm": 12, "pb": 1.2}},
        benchmark={"return20": 0.02, "return60": 0.05},
        config=CandidateConfig(percentile_min_samples=1),
    )
    breadth = result["components"]["constituent_breadth"]
    assert breadth["available"] is False
    assert breadth["score"] is None
    assert result["coverage"] < 1.0


def test_stable_ranking_and_stage_limits():
    rows = [
        {"code": "000002", "stage": "ACTION", "decision_edge": 5, "action_score": 80, "confidence": 80},
        {"code": "000001", "stage": "ACTION", "decision_edge": 5, "action_score": 80, "confidence": 80},
        *({"code": f"0000{index:02d}", "stage": "WATCHLIST", "action_score": 50} for index in range(3, 35)),
    ]
    ranked = rank_candidates(rows)
    assert [row["code"] for row in ranked[:2]] == ["000001", "000002"]
    pools = take_stage_limits(ranked, watchlist_max=30, ready_max=10, action_max=3)
    assert len(pools["watchlist"]) == 30
    assert len(pools["action"]) == 2


def test_scan_request_is_server_owned_and_forbids_client_scores():
    assert CandidateScanRequest(mode="standard").mode == "standard"
    with pytest.raises(ValidationError):
        CandidateScanRequest.model_validate({"mode": "standard", "scores": [100]})
    with pytest.raises(ValidationError):
        CandidateScanRequest.model_validate({"mode": "standard", "weights": {"trend": 1}})


def test_analysis_cannot_promote_ready_or_invent_candidates():
    from app.services.analysis_engine import _normalize_final

    deterministic = {
        "status": "ready",
        "action": [{
            "code": "600001",
            "stage": "ACTION",
            "score": 8.1,
            "action_score": 81,
            "reason_detail": {"catalyst": "趋势", "capital_flow": "流入", "sector_position": "强势"},
        }],
        "ready": [{"code": "510001", "stage": "READY", "score": 9.9}],
        "watchlist": [],
        "run_id": 7,
    }
    result = _normalize_final(
        {"data_quality_grade": "A", "final_rating": "hold", "holdings": [], "risk_warnings": []},
        [],
        "A",
        {
            "quality_gate": {"status": "pass", "grade": "A"},
            "candidate_context": deterministic,
            "candidates": [
                {"code": "600001", "candidate_type": "new_position", "score": 10, "reason_detail": {"catalyst": "趋势", "capital_flow": "流入", "sector_position": "强势"}},
                {"code": "510001", "candidate_type": "new_position", "score": 10},
                {"code": "000001", "candidate_type": "new_position", "score": 10},
            ],
        },
    )
    assert [row["code"] for row in result["candidates"]] == ["600001"]
    assert result["candidates"][0]["action_score"] == 81


def test_analysis_final_no_action_can_demote_deterministic_action():
    from app.services.analysis_engine import _normalize_final

    result = _normalize_final(
        {
            "data_quality_grade": "A",
            "final_rating": "no_action",
            "holdings": [],
            "risk_warnings": [],
        },
        [],
        "A",
        {
            "quality_gate": {"status": "pass", "grade": "A"},
            "candidate_context": {"status": "ready", "action": [{"code": "600001", "stage": "ACTION", "score": 8}]},
            "candidates": [{"code": "600001", "candidate_type": "new_position", "score": 8}],
        },
    )
    assert result["candidates"] == []
    assert result["final_rating"] == "no_action"


def test_candidate_api_enforces_ownership_and_server_owned_inputs():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.candidates.models import CandidateRun
    from app.database import Base, get_db
    from app.main import app
    from app.v2_dependencies import get_current_user
    from app.v2_models import Portfolio, User

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user_a = User(email="candidate-a@example.com", username="candidate-a", password_hash="hash")
        user_b = User(email="candidate-b@example.com", username="candidate-b", password_hash="hash")
        db.add_all([user_a, user_b])
        db.flush()
        portfolio_a = Portfolio(user_id=user_a.id, name="A")
        portfolio_b = Portfolio(user_id=user_b.id, name="B")
        db.add_all([portfolio_a, portfolio_b])
        db.flush()
        run_b = CandidateRun(
            user_id=user_b.id,
            portfolio_id=portfolio_b.id,
            calculation_key="ownership-test",
            trade_date=date(2026, 8, 25),
            as_of=datetime(2026, 8, 25, 10, 0),
            captured_at=datetime(2026, 8, 25, 10, 0),
            status="COMPLETED",
            mode="standard",
        )
        db.add(run_b)
        db.commit()
        portfolio_a_id = portfolio_a.id
        portfolio_b_id = portfolio_b.id
        run_b_id = run_b.id
        user_a_id = user_a.id

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_a_id, status="active")
    try:
        client = TestClient(app)
        assert client.get(f"/api/v3/portfolios/{portfolio_b_id}/candidates/latest").status_code == 404
        assert client.get(f"/api/v3/portfolios/{portfolio_a_id}/candidates/runs/{run_b_id}").status_code == 404
        response = client.post(
            f"/api/v3/portfolios/{portfolio_a_id}/candidates/scan",
            json={"mode": "standard", "scores": [100]},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
