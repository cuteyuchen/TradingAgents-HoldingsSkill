from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(TEST_DB_DIR, f"test_shared_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_ARTIFACTS_DIR", os.path.join(TEST_DB_DIR, f"test_shared_artifacts_{os.getpid()}"))
os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app.candidates.models import CandidateRun
from app.database import Base
from app.market_engine_models import AllAMedianIndexDaily, DailyBarCache
from app.market_models import TradingCalendar
from app.memory.decision import capture_decision_memory, determine_decision_type, extract_decision_targets
from app.memory.execution import evaluate_execution_alignment, link_ledger_entry_to_decision, refresh_execution_alignments
from app.memory.models import DecisionMemory, DecisionOutcome
from app.memory.outcomes import calculate_decision_outcome, completed_session_dates, refresh_due_decision_outcomes
from app.memory.retrieval import compute_similarity
from app.memory.review import build_daily_review, memory_stats, run_daily_review
from app.memory.service import current_memory_features
from app.portfolio.ledger import revise_ledger_entry
from app.portfolio_models import (
    PortfolioSnapshotDiff,
    PortfolioRiskSnapshot,
    TradeLedgerEntry,
    TradeLedgerRevision,
)
from app.services.trading_calendar import upsert_calendar
from app.trigger_models import TriggerEvent, TriggerPlan
from app.v2_models import AnalysisJob, AnalysisRun, HoldingItem, Portfolio, PortfolioSnapshot, User


DECISION_AT = datetime(2026, 8, 20, 2, 30)  # 10:30 Asia/Shanghai
AFTER_CLOSE = datetime(2026, 8, 20, 8, 0)  # 16:00 Asia/Shanghai


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    # Importing the model modules above registers every foreign-key target used
    # by the Phase G tables before metadata creation.
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _calendar(db: Session) -> None:
    rows = [
        {"trade_date": "2026-08-20", "is_open": True},
        {"trade_date": "2026-08-21", "is_open": True},
        {"trade_date": "2026-08-22", "is_open": False},
        {"trade_date": "2026-08-23", "is_open": False},
        {"trade_date": "2026-08-24", "is_open": True},
        {"trade_date": "2026-08-25", "is_open": True},
        {"trade_date": "2026-08-26", "is_open": True},
        {"trade_date": "2026-08-27", "is_open": True},
    ]
    upsert_calendar(db, rows)
    db.commit()


def _seed_analysis(
    db: Session,
    *,
    code: str = "600519",
    action: str = "add",
    quantity: float = 100.0,
    reference_price: float = 100.0,
    decision_at: datetime = DECISION_AT,
    model_reference_price: float | None = None,
    quote_price_basis: str | None = "QFQ",
) -> tuple[User, Portfolio, PortfolioSnapshot, AnalysisRun]:
    user = User(email=f"memory-{code}-{action}@example.com", password_hash="test")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name=f"memory-{code}-{action}")
    db.add(portfolio)
    db.flush()
    snapshot = PortfolioSnapshot(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_time=decision_at,
        total_assets=100000.0,
        total_market_value=50000.0,
        broker_available_cash=50000.0,
        status="confirmed",
    )
    db.add(snapshot)
    db.flush()
    db.add(HoldingItem(
        snapshot_id=snapshot.id,
        code=code,
        name="Fixture Asset",
        qty=100.0,
        available_qty=100.0,
        cost=reference_price,
        screenshot_price=reference_price,
        market_value=10000.0,
        weight=0.1,
    ))
    job = AnalysisJob(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        mode="standard",
        status="succeeded",
        created_at=decision_at,
        started_at=decision_at,
        finished_at=decision_at,
    )
    db.add(job)
    db.flush()
    run = AnalysisRun(
        job_id=job.id,
        user_id=user.id,
        portfolio_snapshot_id=snapshot.id,
        data_quality_grade="A",
        final_rating="no_action",
        confidence="high",
        structured_result_json={
            "result": {
                "data_quality_grade": "A",
                "final_rating": "no_action",
                "confidence": "high",
                "holdings": [{
                    "code": code,
                    "name": "Fixture Asset",
                    "action": action,
                    "recommended_qty": quantity,
                    "reference_price": model_reference_price if model_reference_price is not None else reference_price,
                    "security_type": "STOCK",
                }],
                "candidates": [],
            },
            "market_snapshot": {
                "quotes": {code: {"code": code, "price": reference_price, "price_basis": quote_price_basis}},
            },
            "workflow": {
                "analysis_mode": "standard",
                "portfolio_context": {
                    "market_regime": "RISK_ON",
                    "market_score": 70.0,
                    "market_confidence": 0.9,
                    "cash_ratio": 0.5,
                    "gross_exposure": 0.5,
                    "hhi": 0.1,
                    "portfolio_vol_60": 0.2,
                    "portfolio_quality": "VALID",
                },
                "candidate_context": {},
            },
        },
        markdown_text="fixture",
        created_at=decision_at,
    )
    db.add(run)
    db.commit()
    return user, portfolio, snapshot, run


def _same_portfolio_run(
    db: Session,
    *,
    user: User,
    portfolio: Portfolio,
    code: str,
    decision_at: datetime,
    action: str = "add",
    reference_price: float = 100.0,
    quote_price_basis: str | None = "QFQ",
) -> AnalysisRun:
    snapshot = PortfolioSnapshot(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_time=decision_at,
        total_assets=100000.0,
        total_market_value=50000.0,
        broker_available_cash=50000.0,
        status="confirmed",
    )
    db.add(snapshot)
    db.flush()
    db.add(HoldingItem(
        snapshot_id=snapshot.id,
        code=code,
        name="Fixture Asset",
        qty=100.0,
        available_qty=100.0,
        cost=reference_price,
        screenshot_price=reference_price,
        market_value=10000.0,
        weight=0.1,
    ))
    job = AnalysisJob(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        mode="standard",
        status="succeeded",
        created_at=decision_at,
        started_at=decision_at,
        finished_at=decision_at,
    )
    db.add(job)
    db.flush()
    run = AnalysisRun(
        job_id=job.id,
        user_id=user.id,
        portfolio_snapshot_id=snapshot.id,
        data_quality_grade="A",
        final_rating="no_action",
        confidence="high",
        structured_result_json={
            "result": {
                "data_quality_grade": "A",
                "final_rating": "no_action",
                "confidence": "high",
                "holdings": [{
                    "code": code,
                    "name": "Fixture Asset",
                    "action": action,
                    "recommended_qty": 100.0,
                    "reference_price": reference_price,
                    "security_type": "STOCK",
                }],
                "candidates": [],
            },
            "market_snapshot": {
                "quotes": {code: {"code": code, "price": reference_price, "price_basis": quote_price_basis}},
            },
            "workflow": {
                "analysis_mode": "standard",
                "portfolio_context": {"market_regime": "RISK_ON", "portfolio_quality": "VALID"},
                "candidate_context": {},
            },
        },
        markdown_text="fixture",
        created_at=decision_at,
    )
    db.add(run)
    db.commit()
    return run


def _add_bar(
    db: Session,
    code: str,
    trade_date: date,
    *,
    close: float,
    high: float | None = None,
    low: float | None = None,
    available_at: datetime | None = None,
) -> DailyBarCache:
    row = DailyBarCache(
        market="CN",
        code=code,
        trade_date=trade_date,
        open=close,
        high=high or close,
        low=low or close,
        close=close,
        adjustment="QFQ",
        provider="fixture",
        fetched_at=available_at or datetime(2026, 8, 20, 7, 0),
        available_at=available_at or datetime(2026, 8, 20, 7, 0),
        quality_status="VALID",
    )
    db.add(row)
    db.flush()
    return row


def _add_benchmark(db: Session, trade_date: date, value: float, available_at: datetime) -> None:
    db.add(AllAMedianIndexDaily(
        market="CN",
        trade_date=trade_date,
        median_return=0.0,
        index_value=value,
        eligible_count=1,
        quality_status="VALID",
        available_at=available_at,
    ))
    db.flush()


def _outcome(db: Session, memory: DecisionMemory, code: str = "600519", horizon: int = 1) -> DecisionOutcome:
    return db.execute(select(DecisionOutcome).where(
        DecisionOutcome.decision_memory_id == memory.id,
        DecisionOutcome.target_key == code,
        DecisionOutcome.horizon_trading_days == horizon,
    )).scalar_one()


def _extra_outcome(db: Session, memory: DecisionMemory, code: str, action: str = "add") -> DecisionOutcome:
    row = DecisionOutcome(
        decision_memory_id=memory.id,
        target_type="HELD_POSITION",
        target_key=code,
        recommended_action=action,
        recommended_qty=100.0,
        horizon_trading_days=1,
        reference_trade_date=date(2026, 8, 20),
        reference_at=memory.decision_at,
        reference_price=100.0,
        reference_price_basis="QFQ",
        status="PENDING",
        quality_status="PENDING",
        calculation_version="decision-outcome-v1",
    )
    db.add(row)
    db.flush()
    return row


def test_no_action_with_holdings_on_hold_is_not_an_action(db: Session):
    assert determine_decision_type({
        "final_rating": "no_action",
        "holdings": [{"code": "600519", "action": "hold"}],
        "candidates": [],
    }) == "NO_ACTION"


def test_reference_price_uses_server_owned_quote_not_model_entry_price(db: Session):
    _calendar(db)
    _user, _portfolio, _snapshot, run = _seed_analysis(
        db,
        reference_price=100.0,
        model_reference_price=90.0,
        quote_price_basis="QFQ",
    )
    memory = capture_decision_memory(db, run, available_at=DECISION_AT, commit=True)
    assert memory is not None
    outcome = _outcome(db, memory)
    assert outcome.reference_price == pytest.approx(100.0)
    assert outcome.reference_price_basis == "QFQ"

    _holdings, candidates = extract_decision_targets(
        {"candidates": [{"code": "000001", "action": "new_position", "entry_price": 90.0}]},
        {
            "candidate_context": {
                "run": {"quote_snapshot_id": "quote-1"},
                "action": [{
                    "code": "000001",
                    "lineage": {
                        "quote_snapshot_id": "quote-1",
                        "quote_price": 100.0,
                        "quote_quality": "VALID",
                        "quote_price_basis": "QFQ",
                    },
                }],
            },
        },
    )
    assert candidates[0]["reference_price"] == pytest.approx(100.0)


def test_current_memory_features_leave_unknown_action_unset():
    features = current_memory_features(
        portfolio_context={"market_regime": "RISK_ON"},
        candidate_context={"action": []},
        result={"final_rating": "no_action"},
    )
    assert features["action_type"] is None


def _ledger(
    db: Session,
    memory: DecisionMemory,
    *,
    code: str,
    side: str,
    quantity: float,
    price: float,
    executed_at: datetime,
    suffix: str,
) -> TradeLedgerEntry:
    row = TradeLedgerEntry(
        user_id=memory.user_id,
        portfolio_id=memory.portfolio_id,
        analysis_run_id=memory.analysis_run_id,
        entry_type="TRADE",
        security_code=code,
        side=side,
        quantity=quantity,
        price=price,
        gross_amount=quantity * price,
        executed_at=executed_at,
        trade_date=executed_at.date(),
        available_at=executed_at,
        source="TEST",
        idempotency_key=f"{memory.id}-{suffix}",
        status="CONFIRMED",
    )
    db.add(row)
    db.flush()
    return row


def test_capture_is_idempotent_and_immutable(db: Session):
    _calendar(db)
    _user, _portfolio, _snapshot, run = _seed_analysis(db)

    memory = capture_decision_memory(
        db,
        run,
        available_at=datetime(2026, 8, 19, 0, 0),
        commit=True,
    )
    assert memory is not None
    assert memory.available_at == DECISION_AT
    assert memory.analysis_run_id == run.id
    assert memory.decision_type == "PORTFOLIO_ACTION"
    assert db.query(DecisionOutcome).filter_by(decision_memory_id=memory.id).count() == 6

    duplicate = capture_decision_memory(db, run, commit=True)
    assert duplicate is not None
    assert duplicate.id == memory.id
    assert db.query(DecisionMemory).filter_by(analysis_run_id=run.id).count() == 1

    memory.quality_status = "MUTATED"
    with pytest.raises(RuntimeError, match="decision_memory_is_immutable"):
        db.commit()
    db.rollback()
    assert db.get(DecisionMemory, memory.id).quality_status != "MUTATED"


def test_trading_day_horizon_and_no_lookahead(db: Session):
    _calendar(db)
    _user, _portfolio, _snapshot, run = _seed_analysis(db)
    memory = capture_decision_memory(db, run, commit=True)
    assert memory is not None

    assert completed_session_dates(db, DECISION_AT, 1) == [date(2026, 8, 20)]
    assert completed_session_dates(db, AFTER_CLOSE, 1) == [date(2026, 8, 21)]
    holiday_decision = datetime(2026, 8, 22, 2, 0)
    assert completed_session_dates(db, holiday_decision, 1) == [date(2026, 8, 24)]

    _add_bar(
        db,
        "600519",
        date(2026, 8, 20),
        close=105.0,
        high=110.0,
        low=95.0,
        available_at=datetime(2026, 8, 20, 7, 0),
    )
    _add_benchmark(db, date(2026, 8, 20), 100.0, datetime(2026, 8, 20, 7, 0))
    h1 = _outcome(db, memory)
    pending = calculate_decision_outcome(
        db, memory, h1, calculation_as_of=datetime(2026, 8, 20, 6, 0)
    )
    assert pending["status"] == "PENDING"
    assert pending.get("end_price") is None

    mature = calculate_decision_outcome(
        db, memory, h1, calculation_as_of=datetime(2026, 8, 20, 8, 0)
    )
    assert mature["status"] == "DEGRADED"
    assert mature["raw_return"] == pytest.approx(0.05)
    assert mature["benchmark_return"] is None
    assert mature["excess_return"] is None
    assert mature["mfe"] is None
    assert mature["mae"] is None
    assert "PARTIAL_INTRADAY_PATH" in mature["source_refs"]["reason_codes"]
    assert "INTRADAY_BENCHMARK_UNAVAILABLE" in mature["source_refs"]["reason_codes"]

    h5 = _outcome(db, memory, horizon=5)
    _add_bar(
        db,
        "600519",
        date(2026, 8, 26),
        close=120.0,
        available_at=datetime(2026, 8, 26, 7, 0),
    )
    future = calculate_decision_outcome(
        db, memory, h5, calculation_as_of=datetime(2026, 8, 25, 8, 0)
    )
    assert future["status"] == "PENDING"
    assert future["target_trade_date"] == date(2026, 8, 26)
    assert future.get("end_price") is None


def test_incompatible_raw_reference_does_not_mix_with_qfq_bars(db: Session):
    _calendar(db)
    _user, _portfolio, _snapshot, run = _seed_analysis(
        db,
        reference_price=100.0,
        quote_price_basis="RAW_QUOTE",
    )
    memory = capture_decision_memory(db, run, available_at=DECISION_AT, commit=True)
    assert memory is not None
    _add_bar(
        db,
        "600519",
        date(2026, 8, 20),
        close=105.0,
        high=110.0,
        low=95.0,
        available_at=datetime(2026, 8, 20, 7, 0),
    )
    result = calculate_decision_outcome(
        db,
        memory,
        _outcome(db, memory),
        calculation_as_of=AFTER_CLOSE,
    )
    assert result["status"] == "DEGRADED"
    assert result["raw_return"] is None
    assert result["mfe"] is None
    assert result["mae"] is None
    assert "PRICE_BASIS_MISMATCH" in result["source_refs"]["reason_codes"]


def test_intraday_path_and_benchmark_are_explicitly_degraded(db: Session):
    _calendar(db)
    intraday_at = datetime(2026, 8, 20, 6, 30)  # 14:30 Asia/Shanghai
    _user, _portfolio, _snapshot, run = _seed_analysis(
        db,
        decision_at=intraday_at,
        reference_price=100.0,
        quote_price_basis="QFQ",
    )
    memory = capture_decision_memory(db, run, available_at=intraday_at, commit=True)
    assert memory is not None
    _add_bar(
        db,
        "600519",
        date(2026, 8, 20),
        close=102.0,
        high=120.0,
        low=98.0,
        available_at=datetime(2026, 8, 20, 7, 0),
    )
    _add_benchmark(db, date(2026, 8, 20), 100.0, datetime(2026, 8, 20, 7, 0))
    result = calculate_decision_outcome(
        db,
        memory,
        _outcome(db, memory),
        calculation_as_of=AFTER_CLOSE,
    )
    assert result["status"] == "DEGRADED"
    assert result["raw_return"] == pytest.approx(0.02)
    assert result["mfe"] is None
    assert result["mae"] is None
    assert result["benchmark_return"] is None
    assert result["excess_return"] is None
    assert "PARTIAL_INTRADAY_PATH" in result["source_refs"]["reason_codes"]
    assert "INTRADAY_BENCHMARK_UNAVAILABLE" in result["source_refs"]["reason_codes"]


def test_execution_alignment_uses_confirmed_ledger_and_snapshot_evidence(db: Session):
    _calendar(db)
    _user, _portfolio, snapshot, run = _seed_analysis(db)
    memory = capture_decision_memory(db, run, commit=True)
    assert memory is not None
    cutoff = datetime(2026, 8, 21, 8, 0)

    db.add_all([
        HoldingItem(snapshot_id=snapshot.id, code="000001", name="Partial Asset", qty=0.0, available_qty=0.0),
        HoldingItem(snapshot_id=snapshot.id, code="000002", name="Opposite Asset", qty=100.0, available_qty=100.0),
    ])
    db.flush()

    _ledger(db, memory, code="600519", side="BUY", quantity=50, price=100, executed_at=datetime(2026, 8, 20, 3, 0), suffix="a")
    _ledger(db, memory, code="600519", side="BUY", quantity=50, price=102, executed_at=datetime(2026, 8, 21, 1, 0), suffix="b")
    followed = evaluate_execution_alignment(db, memory, _outcome(db, memory), calculation_as_of=cutoff)
    assert followed["alignment"] == "FOLLOWED"
    assert followed["executed_qty"] == pytest.approx(100)
    assert followed["weighted_avg_execution_price"] == pytest.approx(101)
    assert followed["execution_ratio"] == pytest.approx(1)

    partial = _extra_outcome(db, memory, "000001")
    _ledger(db, memory, code="000001", side="BUY", quantity=50, price=101, executed_at=datetime(2026, 8, 20, 4, 0), suffix="partial")
    assert evaluate_execution_alignment(db, memory, partial, calculation_as_of=cutoff)["alignment"] == "PARTIAL"

    opposite = _extra_outcome(db, memory, "000002")
    _ledger(db, memory, code="000002", side="SELL", quantity=100, price=99, executed_at=datetime(2026, 8, 20, 4, 0), suffix="opposite")
    assert evaluate_execution_alignment(db, memory, opposite, calculation_as_of=cutoff)["alignment"] == "OPPOSITE"

    after = PortfolioSnapshot(
        user_id=memory.user_id,
        portfolio_id=memory.portfolio_id,
        snapshot_time=datetime(2026, 8, 21, 7, 0),
        total_assets=100000.0,
        total_market_value=50000.0,
        broker_available_cash=50000.0,
        status="confirmed",
    )
    db.add(after)
    db.flush()
    db.add(HoldingItem(
        snapshot_id=after.id,
        code="600519",
        name="Fixture Asset",
        qty=200.0,
        available_qty=200.0,
        cost=100.0,
        screenshot_price=100.0,
        market_value=10000.0,
        weight=0.1,
    ))
    db.add(HoldingItem(snapshot_id=after.id, code="000001", name="Partial Asset", qty=50.0, available_qty=50.0))
    db.add(HoldingItem(snapshot_id=after.id, code="000002", name="Opposite Asset", qty=0.0, available_qty=0.0))
    ignored = _extra_outcome(db, memory, "000003")
    db.commit()
    ignored_result = evaluate_execution_alignment(db, memory, ignored, calculation_as_of=cutoff)
    assert ignored_result["alignment"] == "IGNORED"
    assert ignored_result["evidence"]["snapshot_diff_ids"]

    refreshed = refresh_execution_alignments(
        db,
        user_id=memory.user_id,
        portfolio_id=memory.portfolio_id,
        calculation_as_of=cutoff,
        persist=True,
    )
    assert refreshed["counts"]["FOLLOWED"] >= 1
    assert isinstance(_outcome(db, memory).source_refs_json["execution"]["execution_window_end"], str)


def test_unlinked_same_code_trade_is_unresolved_for_each_decision(db: Session):
    _calendar(db)
    user, portfolio, _snapshot, first_run = _seed_analysis(
        db,
        decision_at=datetime(2026, 8, 20, 1, 35),
        quote_price_basis="QFQ",
    )
    second_run = _same_portfolio_run(
        db,
        user=user,
        portfolio=portfolio,
        code="600519",
        decision_at=datetime(2026, 8, 20, 2, 30),
    )
    third_run = _same_portfolio_run(
        db,
        user=user,
        portfolio=portfolio,
        code="600519",
        decision_at=datetime(2026, 8, 20, 5, 5),
    )
    memories = [
        capture_decision_memory(db, run, available_at=run.created_at, commit=True)
        for run in (first_run, second_run, third_run)
    ]
    assert all(memory is not None for memory in memories)
    entry = TradeLedgerEntry(
        user_id=user.id,
        portfolio_id=portfolio.id,
        analysis_run_id=None,
        entry_type="TRADE",
        security_code="600519",
        side="BUY",
        quantity=100.0,
        price=101.0,
        gross_amount=10100.0,
        executed_at=datetime(2026, 8, 20, 6, 0),
        trade_date=date(2026, 8, 20),
        available_at=datetime(2026, 8, 20, 6, 0),
        source="MANUAL",
        idempotency_key="unlinked-shared-trade",
        status="CONFIRMED",
    )
    db.add(entry)
    db.commit()
    cutoff = datetime(2026, 8, 22, 8, 0)
    for memory in memories:
        result = evaluate_execution_alignment(db, memory, _outcome(db, memory), calculation_as_of=cutoff)
        assert result["alignment"] == "UNRESOLVED"
        assert result["executed_qty"] is None
        assert result["evidence"]["alignment_reason_codes"] == ["UNLINKED_LEDGER_IN_WINDOW"]


def test_link_and_ledger_revision_refresh_matured_execution_return(db: Session):
    _calendar(db)
    user, portfolio, _snapshot, run = _seed_analysis(db, quote_price_basis="QFQ")
    memory = capture_decision_memory(db, run, available_at=DECISION_AT, commit=True)
    assert memory is not None
    _add_bar(
        db,
        "600519",
        date(2026, 8, 20),
        close=105.0,
        high=105.0,
        low=100.0,
        available_at=datetime(2026, 8, 20, 7, 0),
    )
    _add_benchmark(db, date(2026, 8, 20), 100.0, datetime(2026, 8, 20, 7, 0))
    refresh_due_decision_outcomes(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        calculation_as_of=AFTER_CLOSE,
        persist=True,
    )
    outcome = _outcome(db, memory)
    assert outcome.actual_execution_return is None

    entry = TradeLedgerEntry(
        user_id=user.id,
        portfolio_id=portfolio.id,
        analysis_run_id=None,
        entry_type="TRADE",
        security_code="600519",
        side="BUY",
        quantity=100.0,
        price=100.0,
        gross_amount=10000.0,
        fees=1.0,
        taxes=2.0,
        executed_at=datetime(2026, 8, 20, 3, 0),
        trade_date=date(2026, 8, 20),
        available_at=datetime(2026, 8, 20, 3, 0),
        source="MANUAL",
        idempotency_key="link-refresh-trade",
        status="CONFIRMED",
    )
    db.add(entry)
    db.commit()
    link_ledger_entry_to_decision(
        db,
        memory=memory,
        ledger_entry=entry,
        user_id=user.id,
        reason="link confirmed trade to matured decision",
    )
    db.commit()
    assert outcome.execution_alignment == "FOLLOWED"
    assert outcome.actual_execution_price == pytest.approx(100.0)
    assert outcome.actual_execution_return == pytest.approx(0.05)
    assert outcome.net_execution_return == pytest.approx(105.0 / 100.03 - 1.0)

    revise_ledger_entry(
        db,
        entry=entry,
        user_id=user.id,
        changes={"price": 103.0},
        reason="correct broker fill price",
    )
    db.commit()
    assert outcome.actual_execution_price == pytest.approx(103.0)
    assert outcome.actual_execution_return == pytest.approx(105.0 / 103.0 - 1.0)
    assert outcome.net_execution_return == pytest.approx(105.0 / 103.03 - 1.0)
    assert outcome.execution_fees == pytest.approx(1.0)
    assert outcome.execution_taxes == pytest.approx(2.0)
    assert outcome.recalculation_count >= 2
    assert outcome.last_source_change_at is not None


def test_daily_review_includes_outcome_matured_from_prior_decision(db: Session):
    _calendar(db)
    _user, portfolio, _snapshot, run = _seed_analysis(db, quote_price_basis="QFQ")
    memory = capture_decision_memory(db, run, available_at=DECISION_AT, commit=True)
    assert memory is not None
    outcome = _outcome(db, memory, horizon=20)
    outcome.status = "VALID"
    outcome.quality_status = "VALID"
    outcome.target_trade_date = date(2026, 8, 26)
    outcome.available_at = datetime(2026, 8, 26, 7, 30)
    outcome.raw_return = 0.12
    db.commit()

    payload = build_daily_review(
        db,
        user_id=memory.user_id,
        portfolio_id=portfolio.id,
        trade_date=date(2026, 8, 26),
        as_of=datetime(2026, 8, 26, 8, 0),
    )
    assert payload["decision_count"] == 0
    assert payload["outcomes_matured_count"] == 1
    assert payload["outcome_summary"]["outcomes_matured_today"][0]["id"] == outcome.id
    assert "OUTCOME_MATURED" in payload["reason_codes"]


def test_retrieval_stats_and_daily_review_are_descriptive_and_idempotent(db: Session):
    _calendar(db)
    _user, _portfolio, _snapshot, run = _seed_analysis(db)
    memory = capture_decision_memory(db, run, commit=True)
    assert memory is not None

    features = {
        "market_regime": "RISK_ON",
        "action_type": "add",
        "security_type": "STOCK",
        "market_score": 70,
        "cash_ratio": 0.5,
        "hhi": 0.1,
    }
    score = compute_similarity(features, {**features, "outcome_return": 999})
    assert score["similarity_score"] == pytest.approx(100)
    assert score["similarity_coverage"] >= 0.6

    _add_bar(db, "600519", date(2026, 8, 20), close=105, high=110, low=95, available_at=datetime(2026, 8, 20, 7, 0))
    _add_benchmark(db, date(2026, 8, 20), 100, datetime(2026, 8, 20, 7, 0))
    as_of = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1)
    refresh_due_decision_outcomes(db, user_id=memory.user_id, portfolio_id=memory.portfolio_id, calculation_as_of=as_of, persist=True)
    stats = memory_stats(db, user_id=memory.user_id, portfolio_id=memory.portfolio_id, horizon=1, as_of=as_of)
    assert stats["aggregate_status"] == "INSUFFICIENT_SAMPLE"
    assert stats["statistics"][0]["market_regime"] == "RISK_ON"
    assert "mean_directional_excess" in stats["statistics"][0]

    review = run_daily_review(
        db,
        user_id=memory.user_id,
        portfolio_id=memory.portfolio_id,
        trade_date=date(2026, 8, 20),
        as_of=as_of,
    )
    assert review is not None
    assert review.status == "COMPLETED"
    assert review.quality_status in {"VALID", "DEGRADED"}
    review_id = review.id
    same_review = run_daily_review(
        db,
        user_id=memory.user_id,
        portfolio_id=memory.portfolio_id,
        trade_date=date(2026, 8, 20),
        as_of=as_of,
    )
    assert same_review is not None
    assert same_review.id == review_id
