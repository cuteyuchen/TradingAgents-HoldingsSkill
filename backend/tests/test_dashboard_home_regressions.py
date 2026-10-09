"""Real-data regressions for the dashboard, without provider or model calls."""
from datetime import UTC, date, datetime, timedelta
import json
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.market.foundation import MAJOR_INDEX_DEFINITIONS, MarketFoundationService
from app.market_engine_models import DailyBarCache
from app.market_runtime_models import ProviderHealth
from app.memory.models import DecisionMemory, LearningHypothesis
from app.memory.performance import account_performance, performance_report
from app.operations import workflow
from app.operations.dashboard import _analysis_section, _decision_section, _portfolio_section
from app.operations.models import DailyOperationalCheckpoint, DailyOperationalRun
from app.services import scheduler
from app.v2_models import AnalysisJob, AnalysisRun, HoldingItem, Portfolio
from test_daily_operations import _FakeThread, _db, _local, _portfolio_fixture, _utc_naive


@pytest.fixture
def home_db():
    db = _db()
    fixture = (db, *_portfolio_fixture(db))
    yield fixture
    db.close()


def _fuyao_rows(*, missing_last=False, quality=None):
    rows = [
        {"thscode": definition["code"], "ticker": f"1A{index:04}",
         "last": None if missing_last else 3030.0, "prev_price": 3000.0}
        for index, definition in enumerate(MAJOR_INDEX_DEFINITIONS)
    ]
    if quality is not None:
        for row in rows:
            row["quality_status"] = quality
    return rows


def test_fuyao_indices_have_prices_changes_quality_and_canonical_names(home_db):
    db, *_ = home_db
    indices = MarketFoundationService(db)._major_indices(_fuyao_rows())
    assert [row["name"] for row in indices] == [row["name"] for row in MAJOR_INDEX_DEFINITIONS]
    for row in indices:
        assert row["status"] == "available"
        assert row["quality"] == "VALID"
        assert row["prev_close"] == 3000
        assert row["change"] == 30
        assert row["change_pct"] == pytest.approx(1)


@pytest.mark.parametrize("hour,missing_last,quality,available", [
    (9, True, None, True), (9, True, "INVALID", False),
    (10, True, None, False), (10, False, "STALE", False),
    (10, False, "DEGRADED", True),
])
def test_index_previous_close_fallback_is_session_and_quality_scoped(home_db, hour, missing_last, quality, available):
    db, *_ = home_db
    rows = _fuyao_rows(missing_last=missing_last, quality=quality)
    # A null alias must not hide the provider's actual previous-price field.
    for row in rows:
        row["prev_close"] = None
    service = MarketFoundationService(db, index_snapshot_loader=lambda **_: {"data": {"items": rows}})
    indices = service.major_indices(now=_local(date(2026, 8, 20), hour))
    assert all(row["status"] == ("available" if available else "unavailable") for row in indices)
    if hour == 9 and quality is None:
        assert all(row["last"] == 3000 and row["change"] == 0 and row["change_pct"] == 0 for row in indices)
        assert all(row["quality"] == "DEGRADED" and row["fallback"] for row in indices)


def test_previous_session_basis_can_display_acquired_index_closes(home_db):
    db, *_ = home_db
    from app.market_models import TradingCalendar
    db.add(TradingCalendar(market="CN", trade_date=date(2026, 8, 22), is_open=False, previous_trade_date=date(2026, 8, 20)))
    db.flush()
    service = MarketFoundationService(db, index_snapshot_loader=lambda **_: _fuyao_rows(missing_last=True))
    indices = service.major_indices(now=_local(date(2026, 8, 22), 10))
    assert all(row["last"] == 3000 and row["status"] == "available" for row in indices)


def test_maintenance_serializes_nested_dates_and_checkpoint_transaction_continues(home_db, monkeypatch):
    db, user, portfolio, _ = home_db
    day = date(2026, 8, 20)
    moment = _utc_naive(_local(day, 8, 45))
    db.add(ProviderHealth(provider_name="fuyao", data_type="quote", status="HEALTHY", updated_at=moment))
    db.add(DailyBarCache(code="600519", trade_date=day - timedelta(days=1), available_at=moment))
    db.commit()
    from app.memory import outcomes
    monkeypatch.setattr(outcomes, "refresh_due_decision_outcomes", lambda *_, **__: {"nested": [{"as_of": moment, "trade_date": day}]})
    result = workflow.run_data_maintenance(db, user_id=user.id, portfolio_id=portfolio.id, trade_date=day, as_of=_local(day, 8, 46))
    json.dumps(result, allow_nan=False)
    assert result["quote_provider"]["providers"][0]["updated_at"] == moment.isoformat()
    assert result["daily_bars"]["trade_date"] == (day - timedelta(days=1)).isoformat()
    assert result["daily_bars"]["available_at"] == moment.isoformat()
    assert result["memory_outcomes"]["result"]["nested"][0] == {"as_of": moment.isoformat(), "trade_date": day.isoformat()}
    workflow.run_due_checkpoints(db, portfolio=portfolio, now=_local(day, 8, 46))
    db.commit()
    op = db.scalar(select(DailyOperationalRun))
    assert op.maintenance_result_json == result
    claim = db.scalar(select(DailyOperationalCheckpoint).where(DailyOperationalCheckpoint.checkpoint_name == "maintenance"))
    assert claim.status in {"SUCCESS", "DEGRADED"}
    assert claim.completed_at is not None
    workflow.run_due_checkpoints(db, portfolio=portfolio, now=_local(day, 9, 20))
    db.commit()
    assert db.scalar(select(DailyOperationalCheckpoint).where(DailyOperationalCheckpoint.checkpoint_name == "pre_market")).status == "SUCCESS"


@pytest.mark.parametrize("checkpoint_name,hook,hour,minute,next_checkpoint,next_hour,next_minute", [
    ("maintenance", "run_data_maintenance", 8, 46, "pre_market", 9, 20),
    ("monitor_start", "_run_monitor_lifecycle", 9, 30, "09:35", 9, 35),
    ("morning_snapshot", "_snapshot_hook_result", 11, 30, "13:05", 13, 5),
    ("09:35", "_admit_checkpoint_job", 9, 35, "10:30", 10, 30),
])
def test_checkpoint_exception_finishes_claim_and_later_ticks_continue(
    home_db, monkeypatch, checkpoint_name, hook, hour, minute,
    next_checkpoint, next_hour, next_minute,
):
    db, _, portfolio, _ = home_db
    day = date(2026, 8, 20)
    moment = _local(day, hour, minute)
    db.commit()
    monkeypatch.setattr(workflow, "run_data_maintenance", lambda *_, **__: {"status": "OK"})
    monkeypatch.setattr(workflow, "_run_monitor_lifecycle", lambda *_: {"status": "DISABLED"})
    monkeypatch.setattr(workflow.threading, "Thread", _FakeThread)
    monkeypatch.setattr(_FakeThread, "starts", [])

    def fail_checkpoint(*_, **__):
        if checkpoint_name == "maintenance":
            # Outcome maintenance can commit before a later hook error, so
            # this also covers a claim that was already persisted as CLAIMED.
            db.commit()
        raise RuntimeError("checkpoint execution failed")

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(workflow, hook, fail_checkpoint)
        result = workflow.run_due_checkpoints(db, portfolio=portfolio, now=moment)

    claim_query = select(DailyOperationalCheckpoint).where(
        DailyOperationalCheckpoint.portfolio_id == portfolio.id,
        DailyOperationalCheckpoint.checkpoint_name == checkpoint_name,
    )
    claim = db.scalar(claim_query)
    claim_id = claim.id
    assert claim.status == "DEGRADED"
    assert claim.last_error == "checkpoint execution failed"
    assert claim.completed_at == _utc_naive(moment)
    assert claim.lease_expires_at is None
    assert result["checkpoints"][checkpoint_name]["status"] == "DEGRADED"
    assert result["checkpoints"][checkpoint_name]["error"] == claim.last_error

    # Read the persisted claim again after rollback; terminal errors must not
    # turn into a live claim or block subsequent checkpoints on another tick.
    db.rollback()
    repeated = workflow.run_due_checkpoints(db, portfolio=portfolio, now=moment + timedelta(minutes=1))
    claim = db.scalar(claim_query)
    assert claim.id == claim_id
    assert claim.status == "DEGRADED"
    assert claim.last_error == "checkpoint execution failed"
    assert repeated["checkpoints"][checkpoint_name]["reason"] != "CHECKPOINT_ALREADY_CLAIMED"
    assert repeated["started_jobs"] == []
    later = workflow.run_due_checkpoints(db, portfolio=portfolio, now=_local(day, next_hour, next_minute))
    assert later["checkpoints"][next_checkpoint]["status"] in {"SUCCESS", "RUNNING"}
    assert db.scalar(claim_query).status == "DEGRADED"
    db.commit()


def _fail_database_flush(db):
    # Exercise SQLAlchemy's actual failed-transaction state, not just a
    # RuntimeError that would leave the Session safe to reuse without rollback.
    db.add(Portfolio(user_id=None, name="invalid portfolio"))
    db.flush()


@pytest.mark.parametrize("failure_stage", ["run_due_checkpoints", "_sync_review_checkpoint"])
def test_scheduler_rolls_back_failed_portfolio_and_continues(home_db, monkeypatch, caplog, failure_stage):
    db, user, portfolio, _ = home_db
    other = Portfolio(user_id=user.id, name="next portfolio")
    db.add(other)
    db.commit()
    portfolio_ids = [portfolio.id, other.id]
    visited, synchronized = [], []
    failed = False
    original_sync = scheduler._sync_review_checkpoint
    rollback = Mock(wraps=db.rollback)
    monkeypatch.setattr(db, "rollback", rollback)

    def fail_once(stage, current):
        nonlocal failed
        if stage == failure_stage and current.id == portfolio_ids[0] and not failed:
            failed = True
            _fail_database_flush(db)
        assert db.is_active

    def run_checkpoints(db, *, portfolio, now):
        visited.append(portfolio.id)
        fail_once("run_due_checkpoints", portfolio)

    def sync_review(db, *, portfolio, **kwargs):
        fail_once("_sync_review_checkpoint", portfolio)
        synchronized.append(portfolio.id)
        original_sync(db, portfolio=portfolio, **kwargs)

    monkeypatch.setattr(workflow, "run_due_checkpoints", run_checkpoints)
    monkeypatch.setattr(scheduler, "_sync_review_checkpoint", sync_review)
    moment = _local(date(2026, 8, 20), 8, 46).astimezone(UTC)
    scheduler._run_daily_operations(db, now_utc=moment, portfolios=[portfolio, other])
    db.commit()
    rollback.assert_called_once_with()
    assert visited == portfolio_ids
    assert synchronized == [other.id]
    assert db.scalar(select(DailyOperationalRun).where(DailyOperationalRun.portfolio_id == other.id)) is not None
    assert "IntegrityError" in caplog.text
    assert "PendingRollbackError" not in caplog.text

    scheduler._run_daily_operations(db, now_utc=moment, portfolios=[portfolio, other])
    db.commit()
    assert synchronized == [other.id, *portfolio_ids]
    assert db.scalar(select(DailyOperationalRun).where(DailyOperationalRun.portfolio_id == portfolio.id)) is not None


@pytest.mark.parametrize("failure_stage", ["_run_daily_operations", "_enqueue_memory_reviews"])
def test_scheduler_tick_rolls_back_failed_transaction_and_recovers(home_db, monkeypatch, caplog, failure_stage):
    db, _, portfolio, _ = home_db
    moment = _local(date(2026, 8, 20), 8, 46).astimezone(UTC)
    db.commit()
    portfolio_id = portfolio.id

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(scheduler, "datetime", FrozenDateTime)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    for hook in ("_dispatch_research_backtests", "_sync_monitor_lifecycle", "_run_shadow_maintenance", "_enqueue_memory_reviews"):
        monkeypatch.setattr(scheduler, hook, lambda *_, **__: None)
    monkeypatch.setattr(workflow, "run_data_maintenance", lambda *_, **__: {"status": "OK"})
    rollback = Mock(wraps=db.rollback)
    monkeypatch.setattr(db, "rollback", rollback)

    def fail_tick(db, **_):
        _fail_database_flush(db)

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(scheduler, failure_stage, fail_tick)
        if failure_stage == "_run_daily_operations":
            scheduler.tick_schedules()
        else:
            with pytest.raises(IntegrityError):
                scheduler.tick_schedules()

    rollback.assert_called_once_with()
    assert "PendingRollbackError" not in caplog.text
    scheduler.tick_schedules()
    claim = db.scalar(select(DailyOperationalCheckpoint).where(
        DailyOperationalCheckpoint.portfolio_id == portfolio_id,
        DailyOperationalCheckpoint.checkpoint_name == "maintenance",
    ))
    assert claim.status == "SUCCESS"
    db.commit()


@pytest.mark.parametrize("assets,market_value,cash,exposure,cash_ratio", [
    (180289.5, 180289.5, 0, 1.0, 0.0),
    (100000, 80000, 20000, 0.8, 0.2),
    (0, 0, 0, None, None),
    (-1, 10, 0, None, None),
    (100000, None, None, None, None),
])
def test_portfolio_ratios_without_risk_snapshot_use_known_facts(home_db, assets, market_value, cash, exposure, cash_ratio):
    db, user, portfolio, snapshot = home_db
    snapshot.total_assets, snapshot.total_market_value, snapshot.broker_available_cash = assets, market_value, cash
    db.flush()
    section = _portfolio_section(db, user_id=user.id, portfolio_id=portfolio.id, cutoff=_utc_naive(_local(date(2026, 8, 20), 10)))
    assert section["gross_exposure"] == exposure
    assert section["cash_ratio"] == cash_ratio
    assert section["holdings"] == []
    assert section["day_return"] is None
    assert section["floating_pnl"] is None
    assert section["imported_pnl_amount"] is None
    assert section["imported_cost_basis"] is None
    assert section["pnl_is_realtime"] is False


def test_portfolio_section_with_real_snapshot_cost_and_pnl(home_db):
    db, user, portfolio, snapshot = home_db
    # Snapshot 1, imported at the 2026-10-08 close. PnL is the broker's
    # imported amount; rounded unit costs do not reproduce it exactly.
    rows = [
        ("159325", 14400, 1.170, 0.982, -2703.44),
        ("159915", 20500, 3.686, 3.132, -11360.42),
        ("512400", 14000, 1.990, 1.617, -5216.64),
        ("512570", 5500, 1.266, 1.037, -1258.80),
        ("515880", 50000, 0.822, 0.615, -10333.35),
        ("588080", 15400, 1.969, 1.543, -6563.53),
        ("588170", 21000, 1.102, 0.909, -4057.12),
    ]
    snapshot.snapshot_time = _utc_naive(_local(date(2026, 10, 8), 15))
    snapshot.total_assets = snapshot.total_market_value = 180289.50
    snapshot.broker_available_cash = 0
    for code, qty, cost, price, pnl_amount in rows:
        db.add(HoldingItem(
            snapshot_id=snapshot.id, code=code, qty=qty, available_qty=qty,
            cost=cost, screenshot_price=price, market_value=round(qty * price, 2),
            pnl_amount=pnl_amount,
        ))
    db.flush()

    section = _portfolio_section(
        db, user_id=user.id, portfolio_id=portfolio.id,
        cutoff=_utc_naive(_local(date(2026, 10, 9), 9)),
    )

    assert section["floating_pnl"] == -41493.30
    assert section["imported_pnl_amount"] == -41493.30
    assert section["imported_cost_basis"] == 221798.60
    assert section["pnl_is_realtime"] is False
    assert section["day_return"] is None
    assert section["position_count"] == 7
    holdings = {item["code"]: item for item in section["holdings"]}
    assert set(holdings) == {row[0] for row in rows}
    for code, qty, cost, price, pnl_amount in rows:
        assert holdings[code]["qty"] == qty
        assert holdings[code]["price"] == price
        assert holdings[code]["cost"] == cost
        assert holdings[code]["pnl_amount"] == pnl_amount


@pytest.mark.parametrize("cost,pnl_amount,expected_cost,expected_pnl", [
    (None, None, None, None),
    (0, 0, 0, 0),
    (1.23456, -0.12345, 123.46, -0.12),
])
def test_portfolio_imported_totals_preserve_missing_zero_and_cents(
    home_db, cost, pnl_amount, expected_cost, expected_pnl,
):
    db, user, portfolio, snapshot = home_db
    db.add(HoldingItem(snapshot_id=snapshot.id, code="159915", qty=100,
                       cost=cost, pnl_amount=pnl_amount))
    db.flush()

    section = _portfolio_section(
        db, user_id=user.id, portfolio_id=portfolio.id,
        cutoff=_utc_naive(_local(date(2026, 8, 20), 10)),
    )

    assert section["floating_pnl"] == expected_pnl
    assert section["imported_pnl_amount"] == expected_pnl
    assert section["imported_cost_basis"] == expected_cost
    assert section["pnl_is_realtime"] is False


def _completed_run(home_db, *, finished, status="succeeded", portfolio_id=None):
    db, user, portfolio, snapshot = home_db
    job = AnalysisJob(user_id=user.id, portfolio_id=portfolio_id or portfolio.id, snapshot_id=snapshot.id,
                      status=status, mode="standard", created_at=finished, started_at=finished,
                      finished_at=finished)
    db.add(job)
    db.flush()
    run = AnalysisRun(job_id=job.id, user_id=user.id, portfolio_snapshot_id=snapshot.id, created_at=finished,
                      final_rating="NO_ACTION", data_quality_grade="A", markdown_text="report",
                      structured_result_json={"result": {"portfolio_action": "NO_ACTION", "holdings": [{"code": "600519", "action": "hold"}]},
                                              "decision_validity": {"valid_until": _local(date(2026, 8, 20), 15).isoformat()}})
    db.add(run)
    db.commit()
    return run


def test_latest_historical_report_does_not_become_today_decision(home_db):
    db, user, portfolio, _ = home_db
    previous = _completed_run(home_db, finished=_utc_naive(_local(date(2026, 8, 20), 15, 10)))
    _completed_run(home_db, finished=_utc_naive(_local(date(2026, 8, 21), 8)), status="failed")
    _completed_run(home_db, finished=_utc_naive(_local(date(2026, 8, 21), 10)))
    other = Portfolio(user_id=user.id, name="Other")
    db.add(other)
    db.flush()
    _completed_run(home_db, finished=_utc_naive(_local(date(2026, 8, 21), 8, 30)), portfolio_id=other.id)
    cutoff = _utc_naive(_local(date(2026, 8, 21), 9))
    section = _analysis_section(db, user_id=user.id, portfolio_id=portfolio.id, cutoff=cutoff)
    assert section["status"] == "AVAILABLE"
    assert section["latest"] is None
    assert section["last_analysis"]["analysis_run_id"] == previous.id
    assert section["last_analysis"]["portfolio_action"] == "NO_ACTION"
    assert section["last_analysis"]["validity_status"] == "EXPIRED"
    assert section["last_analysis"]["report_date"] == "2026-08-20"
    assert section["last_analysis"]["is_historical"] is True
    decision = _decision_section(db, user_id=user.id, portfolio_id=portfolio.id, cutoff=cutoff, analysis=section)
    assert decision["latest"] is None and decision["final_action"] == "INCOMPLETE"
    today = _analysis_section(db, user_id=user.id, portfolio_id=portfolio.id, cutoff=_utc_naive(_local(date(2026, 8, 21), 11)))
    assert today["latest"] == today["last_analysis"]
    assert today["latest"]["report_date"] == "2026-08-21"
    assert today["latest"]["is_historical"] is False


@pytest.mark.parametrize("aware", [False, True])
def test_performance_timestamps_are_explicit_utc_including_nested_learning(home_db, aware):
    db, user, portfolio, snapshot = home_db
    day = date(2026, 8, 20)
    local = _local(day, 9)
    cutoff = local if aware else _utc_naive(local)
    snapshot.created_at = snapshot.snapshot_time
    db.add(LearningHypothesis(user_id=user.id, portfolio_id=portfolio.id, hypothesis_key="utc-test",
        revision=1, status="reference", statement="test", scope_json={}, weight=0.4, reason="TEST",
        extraction_cutoff=snapshot.snapshot_time, available_at=snapshot.snapshot_time))
    db.flush()
    report = performance_report(db, user_id=user.id, portfolio_id=portfolio.id, trade_date=day, as_of=cutoff)
    account = account_performance(db, user_id=user.id, portfolio_id=portfolio.id, trade_date=day, as_of=cutoff)
    for stamp in [report["as_of"], account["as_of"], account["cost_basis_start"],
                  report["account_return"]["as_of"], report["learning"]["as_of"],
                  report["learning"]["hypotheses"][0]["extraction_cutoff"],
                  report["learning"]["hypotheses"][0]["available_at"]]:
        parsed = datetime.fromisoformat(stamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)
    assert datetime.fromisoformat(report["as_of"]) == local.astimezone(UTC)
    assert datetime.fromisoformat(account["cost_basis_start"]) == snapshot.snapshot_time.replace(tzinfo=UTC)


def test_performance_without_account_still_marks_cutoff_utc(home_db):
    db, user, portfolio, _ = home_db
    local = _local(date(2026, 8, 20), 8)
    account = account_performance(db, user_id=user.id, portfolio_id=portfolio.id,
                                  trade_date=local.date(), as_of=local)
    assert datetime.fromisoformat(account["as_of"]) == local.astimezone(UTC)
    assert account["cost_basis_start"] is None
    assert "INITIAL_ACCOUNT_MISSING" in account["reason_codes"]


def test_missing_today_memory_is_pending_and_future_decisions_do_not_create_changes(home_db):
    db, user, portfolio, snapshot = home_db
    day = date(2026, 8, 21)
    from app.market_models import TradingCalendar
    db.add(TradingCalendar(market="CN", trade_date=day, is_open=True))

    def add_memory(trade_day, hour, action, quantity):
        stamp = _utc_naive(_local(trade_day, hour))
        run = _completed_run(home_db, finished=stamp)
        db.add(DecisionMemory(user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=run.id,
            analysis_job_id=run.job_id, portfolio_snapshot_id=snapshot.id, trade_date=trade_day,
            decision_at=stamp, available_at=stamp, analysis_mode="standard", decision_type="NO_ACTION",
            quality_status="VALID", holding_decisions_json=[{"target_key": "600519",
                "recommended_action": action, "recommended_qty": quantity}], candidate_decisions_json=[]))
        db.flush()

    add_memory(day - timedelta(days=1), 15, "hold", None)
    pending = performance_report(db, user_id=user.id, portfolio_id=portfolio.id,
                                 trade_date=day, as_of=_local(day, 9))["day_comparison"]
    assert pending["today_status"] == "PENDING_ANALYSIS"
    assert pending["today_status_text"] == "今日待新一轮分析"
    assert pending["today"] == [] and pending["changes"] == []
    assert pending["yesterday"] == [{"code": "600519", "action": "hold", "quantity": None}]

    add_memory(day, 10, "add", 100)
    still_pending = performance_report(db, user_id=user.id, portfolio_id=portfolio.id,
                                       trade_date=day, as_of=_local(day, 9))["day_comparison"]
    assert still_pending == pending
    available = performance_report(db, user_id=user.id, portfolio_id=portfolio.id,
                                   trade_date=day, as_of=_local(day, 11))["day_comparison"]
    assert available["today_status"] == "AVAILABLE"
    assert available["changes"][0]["previous_action"] == "hold"
    assert available["changes"][0]["action"] == "add"
    assert available["changes"][0]["quantity"] == 100
