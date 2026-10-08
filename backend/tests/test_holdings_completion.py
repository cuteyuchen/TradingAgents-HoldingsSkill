"""Safety checks for background evidence and frozen advisory replays."""
from copy import deepcopy
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from app.memory import learning_review
from app.services import holdings_evidence_prefetch as prefetch, unified_evidence as unified
from app.analysis_workflow.agents import validate_output
from test_alpha_memory import db, _seed_analysis, capture_decision_memory


def test_replay_is_paired_frozen_and_scores_only_gated_quantity(db, monkeypatch):
    user, portfolio, _, run = _seed_analysis(db, action="reduce", quantity=150)
    stamp = run.created_at
    quote = {"code": "600519", "price": 100, "quality_status": "VALID", "fetched_at": stamp.isoformat(), "source": "fixture"}
    record = unified.make_evidence_record("quote", quote, source="fixture", fetched_at=stamp, observed_at=stamp)
    context = {"current_estimated_total_assets": 100000, "portfolio_quality": "VALID", "spendable_cash": 50000,
               "position_constraints": [{"code": "600519", "max_sellable_qty": 100, "current_price": 100,
                                          "quote_quality": "VALID", "lot_size": 100}]}
    run.structured_result_json = {**run.structured_result_json,
        "input_snapshot": {"holdings": [{"code": "600519", "qty": 100}]},
        "market_snapshot": {"quotes": {"600519": quote}, "unified_evidence": {"records": {record["id"]: record}}},
        "workflow": {"portfolio_context": context}}
    db.flush()
    memory = capture_decision_memory(db, run)
    sample = {"decision_memory_id": memory.id, "decision_at": stamp, "code": "600519", "market_return": -.1}
    hypothesis = SimpleNamespace(id=9, statement="检查旧建议", scope_json={}, support_json=[], counterexamples_json=[], available_at=stamp-timedelta(days=1))
    profile = object()
    monkeypatch.setattr(learning_review, "_profile", lambda *_: profile)
    monkeypatch.setattr("app.analysis_workflow.evidence.model_profile_identity", lambda value: {"same_profile": value is profile})
    calls = []
    def model(value, messages, validator):
        import json
        payload = json.loads(messages[1]["content"])
        calls.append((value, payload))
        output = {"code": "600519", "action": "sell" if not payload["learning_context"] else "hold",
                  "quantity": 150 if not payload["learning_context"] else 0, "reason": "公开依据", "evidence_refs": [record["id"]]}
        assert validator(output)
        return SimpleNamespace(data=output)
    monkeypatch.setattr(learning_review, "call_model_json", model)
    result = learning_review.paired_replay(db, user_id=user.id, sample=sample, hypothesis=hypothesis)
    assert len(calls) == 2 and all(item[0] is profile for item in calls)
    first, second = deepcopy(calls[0][1]), deepcopy(calls[1][1])
    assert first.pop("learning_context") == [] and second.pop("learning_context")
    assert first == second and "market_return" not in first and "return" not in first
    assert result["baseline"] == 0
    assert result["learned"] == pytest.approx(-.01)
    assert result["outputs"]["without_learning"]["gated_decision"]["proposed_qty"] == 100
    assert result["causal_proof"] is False and result["real_account_pnl"] is False
    run.structured_result_json = {**run.structured_result_json, "market_snapshot": {
        **run.structured_result_json["market_snapshot"], "quotes": {"600519": {**quote, "fetched_at": (stamp+timedelta(seconds=1)).isoformat()}}}}
    assert learning_review.paired_replay(db, user_id=user.id, sample=sample, hypothesis=hypothesis) is None
    assert len(calls) == 2


def test_supplied_learning_requires_an_explicit_public_usage_declaration():
    payload = {"input": {"learning_context": {"hypotheses": [{"id": 9}]}}, "evidence_refs": []}
    report = {"role": "news_analyst", "scope": "portfolio", "summary": "检查新闻", "findings": [],
              "portfolio_risks": [], "data_gaps": [], "quality_grade": "B"}
    assert not validate_output("news_analyst", report, payload)
    report["learning_usage"] = [{"hypothesis_id": 9, "applied": False, "effect": "当前没有适用行业事件"}]
    assert validate_output("news_analyst", report, payload)
    report["learning_usage"].append(report["learning_usage"][0])
    assert not validate_output("news_analyst", report, payload)


def test_unheld_candidate_replay_uses_archived_tradeability_and_historical_quote_clock(db, monkeypatch):
    user, _, _, run = _seed_analysis(db)
    stamp = run.created_at
    quote = {"code": "159915", "price": 10, "quality_status": "VALID", "fetched_at": stamp.isoformat(),
             "observed_at": stamp.isoformat(), "source": "fixture", "security_type": "ETF", "instrument_status": "ACTIVE",
             "is_suspended": False, "is_st": False, "lot_size": 100, "pct_change": 0, "data_basis": "live"}
    record = unified.make_evidence_record("quote", quote, source="fixture", fetched_at=stamp, observed_at=stamp)
    run.structured_result_json = {**run.structured_result_json,
        "input_snapshot": {"holdings": []},
        "market_snapshot": {"quotes": {"159915": quote}, "unified_evidence": {"records": {record["id"]: record}}},
        "workflow": {"portfolio_context": {"current_estimated_total_assets": 100000, "portfolio_quality": "VALID",
            "spendable_cash": 10000, "cash_ratio": .1, "position_constraints": [], "execution_quotes_required": True,
            "execution_quotes": {"159915": quote}, "execution_candidates": [{"code": "159915", "probe_weight": .05,
                "funding_mode": "CASH_FUNDED", "candidate_engine_stage": "ACTION", "portfolio_fit": {}}]}}}
    db.flush()
    memory = capture_decision_memory(db, run)
    sample = {"decision_memory_id": memory.id, "decision_at": stamp, "code": "159915", "market_return": .1}
    hypothesis = SimpleNamespace(id=9, statement="检查新机会", scope_json={}, support_json=[], counterexamples_json=[], available_at=stamp-timedelta(days=1))
    monkeypatch.setattr(learning_review, "_profile", lambda *_: object())
    monkeypatch.setattr("app.analysis_workflow.evidence.model_profile_identity", lambda _: {})
    monkeypatch.setattr(learning_review, "call_model_json", lambda *args, **kwargs: SimpleNamespace(data={
        "code": "159915", "action": "add", "quantity": 100, "reason": "冻结候选已可交易", "evidence_refs": [record["id"]]}))
    result = learning_review.paired_replay(db, user_id=user.id, sample=sample, hypothesis=hypothesis)
    assert result is not None
    assert result["outputs"]["without_learning"]["gated_decision"]["proposed_qty"] == 100
    assert result["baseline"] == pytest.approx(.001)


def test_prefetch_skips_outside_window_without_opening_database(monkeypatch):
    monkeypatch.setattr(prefetch.settings, "HOLDINGS_EVIDENCE_PREFETCH_ENABLED", True)
    monkeypatch.setattr(prefetch.settings, "ACCEPTANCE_MODE", False)
    monkeypatch.setattr(prefetch, "utc_now", lambda: datetime(2026, 10, 8, 12, tzinfo=UTC))
    monkeypatch.setattr(prefetch, "SessionLocal", lambda: pytest.fail("closed window must not access database/providers"))
    assert prefetch.prefetch_holdings_evidence()["reason"] == "OUTSIDE_PREFETCH_WINDOW"


def test_prefetch_uses_confirmed_snapshot_and_applied_ledger_only(db, monkeypatch):
    from contextlib import contextmanager
    from app.v2_models import PortfolioSnapshot, HoldingItem
    from app.portfolio_models import TradeLedgerEntry
    user, portfolio, baseline, _ = _seed_analysis(db)
    now = datetime(2026, 10, 8, 2, tzinfo=UTC)
    pending = PortfolioSnapshot(user_id=user.id, portfolio_id=portfolio.id, status="pending",
                               snapshot_time=now.replace(tzinfo=None)-timedelta(hours=1))
    db.add(pending)
    db.flush()
    db.add(HoldingItem(snapshot_id=pending.id, code="600999", qty=100))
    stamp = now.replace(tzinfo=None)-timedelta(minutes=10)
    db.add(TradeLedgerEntry(user_id=user.id, portfolio_id=portfolio.id, entry_type="TRADE", status="CONFIRMED",
        security_code="159915", side="BUY", quantity=100, price=10, fees=0, taxes=0, currency="CNY",
        executed_at=stamp, trade_date=stamp.date(), available_at=stamp, source="TEST"))
    db.flush()
    @contextmanager
    def session():
        yield db
    monkeypatch.setattr(prefetch, "SessionLocal", session)
    monkeypatch.setattr(prefetch, "utc_now", lambda: now)
    monkeypatch.setattr(prefetch.settings, "HOLDINGS_EVIDENCE_PREFETCH_ENABLED", True)
    monkeypatch.setattr(prefetch.settings, "ACCEPTANCE_MODE", False)
    monkeypatch.setattr(prefetch.TradingCalendarService, "is_trading_day", lambda *_: True)
    monkeypatch.setattr(prefetch, "InstrumentMarketService", lambda *_: object())
    fetched = []
    monkeypatch.setattr(prefetch, "collect_market_snapshot", lambda codes, **kwargs: fetched.append(codes) or {})
    assert prefetch.prefetch_holdings_evidence()["status"] == "COMPLETED"
    assert fetched == [["159915", "600519"]]


def test_event_channel_is_an_explicit_hypothesis_bound_to_derived_exposure():
    stamp = datetime(2026, 10, 8, 2, tzinfo=UTC)
    pack = unified.build_unified_evidence({"captured_at": stamp.isoformat(), "news": [
        {"title": "美联储利率调整", "source": "fixture", "published_at": stamp.isoformat(), "fetched_at": stamp.isoformat()}]},
        requested_codes=["600000"], supplemental={"metadata": {"600000": {"identity": {"name": "银行甲"}, "metadata": {"industry": "银行"}}}})
    snapshot = unified.attach_portfolio_exposures({"captured_at": stamp.isoformat(), "unified_evidence": pack}, [{"code": "600000", "qty": 700, "weight": .1}])
    link = next(link for link in snapshot["unified_evidence"]["event_links"] if link["method"] == "declared_transmission_channel")
    assert link["causal_effect"] == "unverified" and link["required_checks"]
    assert link["portfolio_exposure"]["quantity"] == 700
    assert unified.project_unified_evidence(snapshot["unified_evidence"], stamp+timedelta(days=40))["event_links"] == []
