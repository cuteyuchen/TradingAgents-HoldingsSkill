"""Phase F.1 production hardening contracts."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from math import sqrt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.candidates.config import CandidateConfig
from app.candidates.factors import section_available_at
from app.candidates.models import CandidateRun, CandidateScore
from app.candidates.portfolio_fit import calculate_portfolio_fit
from app.candidates.risk_reward import calculate_structure_risk_reward
from app.candidates.service import _load_market_state, _run_payload, scan_candidates
from app.candidates.stock_score import score_stock_candidate
from app.candidates.universe import build_candidate_universe
from app.database import Base
from app.market_engine_models import DailyBarCache, MarketMetricSnapshot, MarketScoreSnapshot
from app.market_models import SecurityMaster
from app.market_runtime_models import MarketSnapshot
from app.trigger_models import TriggerEvent  # noqa: F401 - register FK target metadata
from app.v2_models import Portfolio, PortfolioSnapshot, User


def _bars(count: int = 130, *, base: float = 100.0) -> list[dict]:
    rows: list[dict] = []
    first = date(2026, 1, 1)
    for index in range(count):
        close = base + index * 0.08
        rows.append(
            {
                "trade_date": first + timedelta(days=index),
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "prev_close": close - 0.08,
                "volume": 100_000 + index * 100,
                "amount": 5_000_000.0,
                "turnover_rate": 2.0,
            }
        )
    return rows


def _security(code: str) -> dict:
    return {
        "code": code,
        "name": "普通证券",
        "exchange": "SSE",
        "security_type": "STOCK",
        "status": "ACTIVE",
        "listing_date": date(2025, 1, 1),
        "raw_metadata_json": {},
    }


def test_universe_coverage_uses_structural_denominator_before_data_exclusion():
    result = build_candidate_universe(
        [_security("600001"), _security("600002")],
        quotes={"600001": {"code": "600001", "price": 110, "quality_status": "VALID"}},
        bars={"600001": _bars(), "600002": []},
        as_of=datetime(2026, 8, 26, 10, 0),
        trading_days_by_code={"600001": 100, "600002": 100},
        config=CandidateConfig(percentile_min_samples=1),
    )

    assert result["structural_candidate_count"] == 2
    assert result["quote_ready_count"] == 1
    assert result["bar_ready_count"] == 1
    assert result["quote_coverage"] == pytest.approx(0.5)
    assert result["bar_coverage"] == pytest.approx(0.5)
    assert {row["code"] for row in result["eligible"]} == {"600001"}


def test_undated_factor_is_live_only_and_historical_replay_fails_closed():
    section = {"roe": 0.25}
    cutoff = datetime(2026, 8, 26, 10, 0)
    assert section_available_at(section, cutoff, live=False) is False
    assert section_available_at(section, cutoff, live=True) is True

    historical = score_stock_candidate(
        "600001",
        _bars(),
        as_of=cutoff,
        metadata={"fundamental": section},
        config=CandidateConfig(percentile_min_samples=1),
    )
    live = score_stock_candidate(
        "600001",
        _bars(),
        metadata={"fundamental": section},
        config=CandidateConfig(percentile_min_samples=1),
    )
    assert historical["components"]["fundamental"]["available"] is False
    assert live["components"]["fundamental"]["available"] is True


def test_run_payload_fail_closes_action_and_missing_ready():
    run = CandidateRun(
        user_id=1,
        portfolio_id=1,
        calculation_key="quality-gate-test",
        trade_date=date(2026, 8, 26),
        as_of=datetime(2026, 8, 26, 10, 0),
        captured_at=datetime(2026, 8, 26, 10, 0),
        quality_status="BLOCKED_FOR_ACTION",
    )
    run.scores = [
        CandidateScore(code="600001", security_type="STOCK", stage="ACTION", rank=1),
        CandidateScore(code="600002", security_type="STOCK", stage="READY", rank=2),
    ]
    blocked = _run_payload(run)
    assert blocked["action"] == []
    assert [row["code"] for row in blocked["ready"]] == ["600002"]

    run.quality_status = "MISSING"
    missing = _run_payload(run)
    assert missing["action"] == []
    assert missing["ready"] == []


def test_persisted_action_score_exposes_new_position_contract():
    run = CandidateRun(
        user_id=1,
        portfolio_id=1,
        calculation_key="persisted-action-contract-test",
        trade_date=date(2026, 8, 26),
        as_of=datetime(2026, 8, 26, 10, 0),
        captured_at=datetime(2026, 8, 26, 10, 0),
        quality_status="VALID",
    )
    run.scores = [CandidateScore(code="600001", security_type="STOCK", stage="ACTION", rank=1)]

    payload = _run_payload(run)

    assert payload["action"][0]["candidate_type"] == "new_position"
    assert payload["action"][0]["action"] == "new_position"


def test_analysis_normalization_rechecks_blocked_candidate_run():
    from app.services.analysis_engine import _normalize_final

    candidate = {
        "code": "600001",
        "candidate_type": "new_position",
        "score": 8.0,
        "reason_detail": {
            "catalyst": "趋势",
            "capital_flow": "流入",
            "sector_position": "强势",
        },
    }
    result = _normalize_final(
        {"data_quality_grade": "A", "holdings": [], "candidates": [candidate], "risk_warnings": []},
        [],
        "A",
        {
            "quality_gate": {"status": "pass", "grade": "A"},
            "candidate_context": {
                "status": "ready",
                "quality_status": "BLOCKED_FOR_ACTION",
                "action": [candidate],
            },
            "candidates": [candidate],
        },
    )
    assert result["candidates"] == []
    assert result["candidate_status"] == "none"


def test_portfolio_fit_uses_60_days_and_replacement_really_moves_weight():
    bars = _bars()
    fit = calculate_portfolio_fit(
        {"code": "600001", "security_type": "STOCK"},
        {
            "positions": [{"code": "600002", "weight": 0.20, "industry": "TECH"}],
            "cash_ratio": 0.0,
            "total_assets": 100_000,
            "hhi": 0.04,
            "portfolio_vol_60": 0.20,
        },
        holding_bars={"600002": bars},
        candidate_bars=bars,
        held_opportunity_scores=[
            {
                "code": "600002",
                "opportunity_score": 40,
                "keep_score": 45,
                "confidence": 80,
                "coverage": 0.8,
            }
        ],
    )

    assert fit["funding_mode"] == "REPLACEMENT_REVIEW"
    assert fit["probe_source_code"] == "600002"
    assert fit["correlation_samples"][0]["samples"] == 60
    assert fit["correlation_samples"][0]["samples"] >= 40
    assert fit["projected_weights"]["600002"] == pytest.approx(0.15)
    assert fit["projected_weights"]["600001"] == pytest.approx(0.05)


def test_cash_funded_risk_keeps_existing_risk_assets_at_original_weight():
    bars = _bars()
    fit = calculate_portfolio_fit(
        {"code": "600001", "security_type": "STOCK"},
        {
            "positions": [{"code": "600002", "weight": 0.20}],
            "cash_ratio": 0.10,
            "total_assets": 100_000,
            "portfolio_vol_60": 0.20,
        },
        holding_bars={"600002": bars},
        candidate_bars=bars,
    )
    assert fit["funding_mode"] == "CASH_FUNDED"
    probe = fit["probe_weight"]
    candidate_vol = fit["candidate_volatility"]
    corr = fit["correlation_for_risk"]
    expected = sqrt(0.20**2 + probe**2 * candidate_vol**2 + 2 * probe * corr * 0.20 * candidate_vol)
    assert fit["projected_portfolio_volatility"] == pytest.approx(expected)


def test_risk_reward_support_is_ma20_or_confirmed_swing_low():
    result = calculate_structure_risk_reward(_bars(), price=110.0)
    assert result["structure"]["support_selection"] == "CONFIRMED_SWING_LOW_OR_MA20"
    assert all(item["source"] in {"MA20", "CONFIRMED_SWING_LOW"} for item in result["structure"]["support_candidates"])


def test_market_score_freshness_and_lineage_are_checked_from_metric_source():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 26, 10, 0)
    with Session(engine) as db:
        db.add_all(
            [
                MarketSnapshot(
                    snapshot_id="quote-source",
                    snapshot_key="quote-source",
                    market="CN",
                    started_at=now - timedelta(minutes=2),
                    completed_at=now - timedelta(minutes=1),
                    provider="fixture",
                    quality_status="VALID",
                ),
                MarketMetricSnapshot(
                    snapshot_id="metric-1",
                    market_snapshot_id="quote-source",
                    market="CN",
                    trade_date=date(2026, 8, 26),
                    captured_at=now - timedelta(minutes=1),
                    coverage=1.0,
                    quality_status="VALID",
                ),
                MarketScoreSnapshot(
                    snapshot_id="score-1",
                    metric_snapshot_id="metric-1",
                    market="CN",
                    trade_date=date(2026, 8, 26),
                    captured_at=now - timedelta(minutes=1),
                    confidence=100,
                    quality_status="VALID",
                    is_frozen=False,
                ),
            ]
        )
        db.commit()

        fresh = _load_market_state(db, as_of=now, live=True)
        assert fresh["available"] is True
        assert fresh["market_snapshot_id"] == "quote-source"
        assert fresh["market_snapshot_id"] != fresh["metric_snapshot_id"]
        assert fresh["lineage_status"] == "VALID"

        score = db.query(MarketScoreSnapshot).filter_by(snapshot_id="score-1").one()
        score.captured_at = now - timedelta(hours=1)
        db.commit()
        stale = _load_market_state(db, as_of=now, live=True)
        assert stale["available"] is False
        assert stale["quality_status"] == "STALE"


def test_live_standard_scan_uses_one_bulk_quote_snapshot(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(engine) as db:
        user = User(email="bulk@example.com", username="bulk", password_hash="hash")
        db.add(user)
        db.flush()
        portfolio = Portfolio(user_id=user.id, name="Bulk")
        db.add(portfolio)
        db.flush()
        snapshot = PortfolioSnapshot(
            user_id=user.id,
            portfolio_id=portfolio.id,
            snapshot_time=now,
            status="confirmed",
            total_assets=100_000,
            total_market_value=100_000,
            broker_available_cash=100_000,
        )
        db.add_all(
            [
                snapshot,
                SecurityMaster(market="CN", exchange="SSE", code="600001", name="Candidate", status="ACTIVE"),
                MarketSnapshot(
                    snapshot_id="market-source",
                    snapshot_key="market-source",
                    market="CN",
                    started_at=now - timedelta(minutes=2),
                    completed_at=now - timedelta(minutes=1),
                    provider="fixture",
                    quality_status="VALID",
                ),
                MarketMetricSnapshot(
                    snapshot_id="metric-bulk",
                    market_snapshot_id="market-source",
                    market="CN",
                    trade_date=now.date(),
                    captured_at=now - timedelta(minutes=1),
                    coverage=1.0,
                    quality_status="VALID",
                ),
                MarketScoreSnapshot(
                    snapshot_id="score-bulk",
                    metric_snapshot_id="metric-bulk",
                    market="CN",
                    trade_date=now.date(),
                    captured_at=now - timedelta(minutes=1),
                    confidence=100,
                    quality_status="VALID",
                    is_frozen=False,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                DailyBarCache(
                    market="CN",
                    exchange="SSE",
                    code="600001",
                    trade_date=now.date() - timedelta(days=index),
                    close=100 + index * 0.08,
                    prev_close=100 + max(0, index - 1) * 0.08,
                    amount=5_000_000,
                    adjustment="QFQ",
                    provider="fixture",
                    available_at=now - timedelta(minutes=1),
                    quality_status="VALID",
                )
                for index in range(130)
            ]
        )
        db.commit()

        calls: list[dict] = []

        def bulk_quote(db_arg, **kwargs):
            calls.append(kwargs)
            return {
                "snapshot_id": "bulk-quote-1",
                "quality_status": "VALID",
                "quotes": [
                    {
                        "code": "600001",
                        "price": 110,
                        "prev_close": 109.9,
                        "quality_status": "VALID",
                        "provider": "fixture-bulk",
                        "source_timestamp": (now - timedelta(seconds=5)).isoformat(),
                    }
                ],
            }

        monkeypatch.setattr("app.candidates.service.get_all_a_share_quote_snapshot", bulk_quote)
        result = scan_candidates(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            mode="standard",
            persist=False,
        )

        assert len(calls) == 1
        assert calls[0]["include_etf"] is True
        assert result["run"]["quote_snapshot_id"] == "bulk-quote-1"
        assert result["run"]["metadata"]["scan_contract"]["network_fetch"] is True
        assert result["run"]["structural_candidate_count"] == 1
        assert result["run"]["quote_coverage"] == pytest.approx(1.0)
        assert result["run"]["bar_coverage"] == pytest.approx(1.0)
