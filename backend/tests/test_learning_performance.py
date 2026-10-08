from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.memory.facts import ledger_facts_at
from app.memory.learning import (build_learning_context, for_role, learning_summary,
                                 record_learning_references, refresh_learning)
from app.memory.models import LearningContextReference, LearningHypothesis, LearningValidation
from app.memory.performance import account_performance, performance_report
from app.portfolio_models import TradeLedgerEntry, TradeLedgerRevision
from app.v2_models import HoldingItem, PortfolioSnapshot

from test_alpha_memory import (db, _add_bar, _calendar, _outcome, _seed_analysis,
                               capture_decision_memory, DECISION_AT)


def _ledger(db, user, portfolio, **values):
    defaults = dict(user_id=user.id, portfolio_id=portfolio.id, entry_type="TRADE",
                    security_code="600519", side="BUY", quantity=100, price=110,
                    fees=2.0, taxes=0.0, currency="CNY", executed_at=datetime(2026, 8, 21, 2),
                    trade_date=date(2026, 8, 21), available_at=datetime(2026, 8, 21, 2),
                    created_at=datetime(2026, 8, 21, 2), updated_at=datetime(2026, 8, 21, 2), status="CONFIRMED")
    defaults.update(values)
    entry = TradeLedgerEntry(**defaults)
    db.add(entry)
    db.flush()
    return entry


def _account_seed(db):
    _calendar(db)
    user, portfolio, anchor, run = _seed_analysis(db)
    anchor.snapshot_time = anchor.created_at = datetime(2026, 8, 20, 7)
    anchor.total_assets = 60000
    close = PortfolioSnapshot(user_id=user.id, portfolio_id=portfolio.id,
        snapshot_time=datetime(2026, 8, 21, 7), created_at=datetime(2026, 8, 21, 7),
        status="confirmed", total_assets=66000, broker_available_cash=48000)
    db.add(close)
    db.flush()
    db.add(HoldingItem(snapshot_id=close.id, code="600519", qty=150, cost=105.01,
                       screenshot_price=120, market_value=18000))
    _ledger(db, user, portfolio)
    _ledger(db, user, portfolio, side="SELL", quantity=50, price=120, fees=1, taxes=1,
            executed_at=datetime(2026, 8, 21, 3), available_at=datetime(2026, 8, 21, 3))
    _ledger(db, user, portfolio, entry_type="CASH_IN", security_code=None, quantity=None,
            side=None, price=None, net_amount=4000, executed_at=datetime(2026, 8, 21, 4))
    _ledger(db, user, portfolio, entry_type="DIVIDEND", security_code=None, quantity=None,
            side=None, price=None, net_amount=100, executed_at=datetime(2026, 8, 21, 4))
    db.flush()
    return user, portfolio, anchor, close, run


def test_account_equity_flow_and_weighted_cost_are_separate(db):
    user, portfolio, _, _, _ = _account_seed(db)
    result = account_performance(db, user_id=user.id, portfolio_id=portfolio.id,
                                 trade_date=date(2026, 8, 21), as_of=datetime(2026, 8, 21, 8))
    assert result["status"] == "VALID"
    assert result["net_pnl"] == 2000  # 66000 - 60000 - external 4000
    assert result["external_flow"] == 4000
    assert result["positions"][0]["average_cost"] == pytest.approx(105.01)
    assert result["realized_pnl"] == pytest.approx(747.5)
    assert result["unrealized_pnl"] == pytest.approx(2248.5)
    assert result["total_pnl_since_anchor"] == pytest.approx(3096)
    assert result["fees"] == 3 and result["taxes"] == 1 and result["dividends"] == 100
    assert result["return_rate"] == pytest.approx(2000 / 60500)  # 3 of 24 hours remaining


def test_missing_cost_and_quantity_reconciliation_do_not_invent_profit(db):
    user, portfolio, anchor, close, _ = _account_seed(db)
    anchor.holdings[0].cost = None
    close.holdings[0].qty = 160
    db.flush()
    result = account_performance(db, user_id=user.id, portfolio_id=portfolio.id,
                                 trade_date=date(2026, 8, 21), as_of=datetime(2026, 8, 21, 8))
    assert result["status"] == "INCOMPLETE"
    assert result["realized_pnl"] is None
    assert result["unrealized_pnl"] is None
    assert any("INITIAL_COST" in code for code in result["reason_codes"])


def test_real_cost_valuation_rejects_adjusted_price_only(db):
    user, portfolio, _, close, _ = _account_seed(db)
    close.holdings[0].screenshot_price = None
    _add_bar(db, "600519", date(2026, 8, 21), close=130, available_at=datetime(2026, 8, 21, 7))
    db.flush()
    result = account_performance(db, user_id=user.id, portfolio_id=portfolio.id,
                                 trade_date=date(2026, 8, 21), as_of=datetime(2026, 8, 21, 8))
    assert result["unrealized_pnl"] is None
    assert result["positions"][0]["mark_price"] is None


def test_history_replays_future_revision_and_void_without_mutation(db):
    user, portfolio, _, _, _ = _account_seed(db)
    entry = _ledger(db, user, portfolio, quantity=200)
    db.add(TradeLedgerRevision(ledger_entry_id=entry.id, revision_no=1,
        changes_json={"before": {"quantity": 100}}, reason="correct", created_by_user_id=user.id,
        created_at=datetime(2026, 8, 22)))
    db.add(TradeLedgerRevision(ledger_entry_id=entry.id, revision_no=2,
        changes_json={"before_status": "CONFIRMED", "after_status": "VOIDED"}, reason="void",
        created_by_user_id=user.id, created_at=datetime(2026, 8, 23)))
    entry.status = "VOIDED"
    db.flush()
    old = ledger_facts_at(db, user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 21, 8))
    assert next(row for row in old if row.id == entry.id).quantity == 100
    assert entry.quantity == 200 and entry.status == "VOIDED"
    assert not any(row.id == entry.id for row in ledger_facts_at(
        db, user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 24)))


def test_unexecuted_advice_not_real_profit_and_missing_data_pending(db):
    _calendar(db)
    user, portfolio, _, run = _seed_analysis(db)
    memory = capture_decision_memory(db, run, available_at=DECISION_AT)
    report = performance_report(db, user_id=user.id, portfolio_id=portfolio.id,
                               trade_date=date(2026, 8, 21), as_of=datetime(2026, 8, 21, 8))
    assert report["account_return"]["realized_pnl"] is None
    assert report["recommendation_effect"]["items"]
    assert all(row["market_return"] is None for row in report["recommendation_effect"]["items"])
    assert all(row["prediction_failed"] is None for row in report["recommendation_effect"]["items"])
    assert all(not row["is_actual_account_pnl"] for row in report["recommendation_effect"]["items"])


def test_unobserved_condition_is_not_prediction_failure_or_training_sample(db):
    _calendar(db)
    user, portfolio, _, run = _seed_analysis(db, action="conditional_add")
    capture_decision_memory(db, run, available_at=DECISION_AT)
    _add_bar(db, "600519", date(2026, 8, 20), close=90)
    report = performance_report(db, user_id=user.id, portfolio_id=portfolio.id,
                               trade_date=date(2026, 8, 20), as_of=datetime(2026, 8, 21, 8))
    assert all(row["condition_status"] == "CONDITION_UNOBSERVED" for row in report["recommendation_effect"]["items"])
    assert all(row["prediction_failed"] is None for row in report["recommendation_effect"]["items"])
    assert report["simulation_comparison"]["items"] == []


def _hypothesis(db, user, portfolio, *, revision=1, status="reference", at=datetime(2026, 8, 21), key="test"):
    row = LearningHypothesis(user_id=user.id, portfolio_id=portfolio.id, hypothesis_key=key, revision=revision,
        status=status, statement="test", scope_json={"market_regime": "RISK_ON", "roles": ["risk_manager"]},
        support_json=[{"decision_memory_id": 1}], counterexamples_json=[], validation_json={}, weight=0.4,
        extraction_cutoff=at, available_at=at, reason="TEST")
    db.add(row)
    db.flush()
    return row


def test_context_is_versioned_role_scoped_and_future_disable_cannot_rewrite_past(db):
    user, portfolio, _, run = _seed_analysis(db)
    original = _hypothesis(db, user, portfolio)
    _hypothesis(db, user, portfolio, revision=2, status="disabled", at=datetime(2026, 8, 23))
    context = build_learning_context(db, user_id=user.id, portfolio_id=portfolio.id,
                                     as_of=datetime(2026, 8, 22), current_features={"market_regime": "RISK_ON"})
    assert context["ref_ids"] == [original.id]
    assert for_role(context, "news_analyst")["hypotheses"] == []
    assert len(for_role(context, "risk_manager")["hypotheses"]) == 1
    record_learning_references(db, user_id=user.id, portfolio_id=portfolio.id, analysis_job_id=run.job_id,
                               role="risk_manager", context=context)
    record_learning_references(db, user_id=user.id, portfolio_id=portfolio.id, analysis_job_id=run.job_id,
                               role="risk_manager", context=context)
    assert len(db.scalars(select(LearningContextReference)).all()) == 1
    later = build_learning_context(db, user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 24))
    assert later["hypotheses"] == []
    assert context["hypotheses"][0]["status"] == "reference"


def test_context_reference_rejects_cross_portfolio(db):
    user, portfolio, _, run = _seed_analysis(db)
    context = build_learning_context(db, user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 22))
    with pytest.raises(ValueError, match="ownership"):
        record_learning_references(db, user_id=user.id, portfolio_id=portfolio.id + 1,
                                   analysis_job_id=run.job_id, role="risk_manager", context=context)


def _sample(memory_id, outcome_id, day, result):
    return {"decision_memory_id": memory_id, "outcome_id": outcome_id, "trade_date": day.isoformat(),
            "code": "600519", "decision_at": datetime.combine(day, datetime.min.time()),
            "available_at": datetime.combine(day, datetime.min.time()) + timedelta(days=7), "return": result,
            "scope": {"action": "add", "market_regime": "RISK_ON", "security_type": "STOCK", "horizon": 5,
                      "roles": ["risk_manager"], "policy": "AVOID_NEW_RISK_VS_UNCHANGED"}, "source_hash": str(result)}


def test_forward_validation_excludes_training_and_versions_failure(db, monkeypatch):
    user, portfolio, _, run = _seed_analysis(db)
    memory = capture_decision_memory(db, run, available_at=DECISION_AT)
    outcome = _outcome(db, memory)
    samples = [_sample(memory.id, outcome.id, date(2026, 8, 1), -0.1),
               _sample(memory.id, outcome.id, date(2026, 8, 2), -0.05),
               _sample(memory.id, outcome.id, date(2026, 8, 3), -0.02)]
    monkeypatch.setattr("app.memory.learning._samples", lambda *args, **kwargs: samples)
    monkeypatch.setattr("app.memory.learning.utc_now", lambda: datetime(2026, 8, 10))
    first = refresh_learning(db, user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 10))
    assert first["hypotheses"][0]["status"] == "reference"
    assert not db.scalars(select(LearningValidation)).all()
    # Positive later baseline returns disprove the hypothesis that avoiding them helps.
    samples.extend(_sample(memory.id, outcome.id, date(2026, 8, 11) + timedelta(days=i), 0.02) for i in range(3))
    monkeypatch.setattr("app.memory.learning.utc_now", lambda: datetime(2026, 8, 25))
    second = refresh_learning(db, user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 25))
    assert second["hypotheses"][0]["weight"] == 0.1
    assert second["hypotheses"][0]["validation"]["sample_count"] == 3
    samples.extend(_sample(memory.id, outcome.id, date(2026, 8, 14) + timedelta(days=i), 0.02) for i in range(2))
    monkeypatch.setattr("app.memory.learning.utc_now", lambda: datetime(2026, 8, 27))
    third = refresh_learning(db, user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 27))
    assert third["hypotheses"][0]["status"] == "disabled"
    assert len(db.scalars(select(LearningValidation)).all()) == 5
    # Frozen earlier context still sees the original version.
    past = learning_summary(db, user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 11))
    assert past["hypotheses"][0]["revision"] == 1
