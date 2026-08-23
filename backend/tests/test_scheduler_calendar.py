"""Scheduler integration tests for the persisted CN trading calendar."""
from datetime import UTC, date, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.market_models import TradingCalendar
from app.services import scheduler
from app.services.market_identity_sync import initialize_local_market_identity
from app.services.trading_calendar import upsert_calendar
from app.v2_models import Schedule


def _calendar_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    TradingCalendar.__table__.create(engine)
    return Session(engine)


def _schedule(**overrides):
    values = {
        "id": 1,
        "hour": 9,
        "minute": 35,
        "timezone": "America/New_York",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_next_run_uses_shanghai_time_and_persisted_open_days():
    db = _calendar_session()
    try:
        upsert_calendar(
            db,
            [
                {"trade_date": "2026-08-20", "is_open": True},
                {"trade_date": "2026-08-21", "is_open": False},
                {"trade_date": "2026-08-24", "is_open": True},
            ],
        )
        db.commit()

        before_checkpoint = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
        assert scheduler.next_run(_schedule(), before_checkpoint, db=db) == datetime(
            2026, 8, 20, 1, 35, tzinfo=UTC
        )

        after_checkpoint = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)
        assert scheduler.next_run(_schedule(), after_checkpoint, db=db) == datetime(
            2026, 8, 24, 1, 35, tzinfo=UTC
        )
    finally:
        db.close()


def test_next_run_interprets_naive_now_as_shanghai_time():
    db = _calendar_session()
    try:
        upsert_calendar(db, [{"trade_date": "2026-08-20", "is_open": True}])
        db.commit()

        # 09:00 in the A-share business timezone, independent of host TZ.
        assert scheduler.next_run(_schedule(), datetime(2026, 8, 20, 9, 0), db=db) == datetime(
            2026, 8, 20, 1, 35, tzinfo=UTC
        )
    finally:
        db.close()


def test_next_run_fails_closed_when_calendar_range_is_missing(caplog):
    db = _calendar_session()
    try:
        result = scheduler.next_run(
            _schedule(),
            datetime(2026, 8, 20, 1, 0, tzinfo=UTC),
            db=db,
        )
        assert result is None
        assert "fail-closed" in caplog.text
    finally:
        db.close()


def _scheduler_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Schedule.__table__.create(engine)
    TradingCalendar.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(
            Schedule(
                id=1,
                user_id=1,
                portfolio_id=1,
                name="fixture",
                timezone="America/New_York",
                hour=9,
                minute=35,
                checkpoint="09:35",
                mode="standard",
                enabled=True,
                stale_snapshot_days=3,
                notify=False,
                max_consecutive_failures=3,
                consecutive_failures=0,
            )
        )
        db.commit()
    return factory


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 8, 20, 1, 35, tzinfo=UTC)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


class _LateFrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 8, 20, 2, 5, tzinfo=UTC)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


def test_tick_schedules_missing_calendar_is_fail_closed(monkeypatch, caplog):
    factory = _scheduler_db()
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    monkeypatch.setattr(scheduler, "datetime", _FrozenDateTime)
    calls = []
    monkeypatch.setattr(scheduler, "create_scheduled_job", lambda *_args, **_kwargs: calls.append(True))

    scheduler.tick_schedules()

    with factory() as db:
        row = db.get(Schedule, 1)
        assert row.consecutive_failures == 0
        assert row.enabled is True
        assert row.next_run_at is None
    assert calls == []
    assert "scheduled analysis is fail-closed" in caplog.text


def test_upgrade_bootstrap_restores_existing_schedule(monkeypatch):
    """An upgraded database must recover after the offline startup bootstrap."""

    factory = _scheduler_db()
    with factory() as db:
        assert db.query(TradingCalendar).count() == 0

    status = initialize_local_market_identity(
        session_factory=factory,
        as_of=date(2026, 8, 20),
    )
    assert status["status"] == "ready"

    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    monkeypatch.setattr(scheduler, "datetime", _FrozenDateTime)
    created = []
    started = []

    def fake_create(_db, schedule):
        created.append(schedule.id)
        return SimpleNamespace(id=101)

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.args = args
            assert target is scheduler.run_scheduled_job
            assert daemon is True

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(scheduler, "create_scheduled_job", fake_create)
    monkeypatch.setattr(scheduler.threading, "Thread", FakeThread)

    scheduler.tick_schedules()

    assert created == [1]
    assert started == [(101, 1)]
    with factory() as db:
        row = db.get(Schedule, 1)
        assert row.enabled is True
        assert row.last_run_at == datetime(2026, 8, 20, 1, 35)
        assert row.next_run_at == datetime(2026, 8, 21, 1, 35)


def test_tick_schedules_enqueues_only_on_persisted_open_day(monkeypatch):
    factory = _scheduler_db()
    with factory() as db:
        upsert_calendar(
            db,
            [
                {"trade_date": "2026-08-20", "is_open": True},
                {"trade_date": "2026-08-21", "is_open": True},
            ],
        )
        db.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    monkeypatch.setattr(scheduler, "datetime", _FrozenDateTime)
    created = []
    started = []

    def fake_create(_db, schedule):
        created.append(schedule.id)
        return SimpleNamespace(id=99)

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.args = args
            assert target is scheduler.run_scheduled_job
            assert daemon is True

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(scheduler, "create_scheduled_job", fake_create)
    monkeypatch.setattr(scheduler.threading, "Thread", FakeThread)

    scheduler.tick_schedules()

    assert created == [1]
    assert started == [(99, 1)]
    with factory() as db:
        row = db.get(Schedule, 1)
        assert row.last_run_at == datetime(2026, 8, 20, 1, 35)
        assert row.next_run_at == datetime(2026, 8, 21, 1, 35)


def test_tick_schedules_catches_up_when_tick_arrives_after_checkpoint(monkeypatch):
    factory = _scheduler_db()
    with factory() as db:
        upsert_calendar(
            db,
            [
                {"trade_date": "2026-08-20", "is_open": True},
                {"trade_date": "2026-08-21", "is_open": True},
            ],
        )
        db.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    monkeypatch.setattr(scheduler, "datetime", _LateFrozenDateTime)
    created = []
    started = []

    def fake_create(_db, schedule):
        created.append(schedule.id)
        return SimpleNamespace(id=100)

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.args = args
            assert target is scheduler.run_scheduled_job
            assert daemon is True

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(scheduler, "create_scheduled_job", fake_create)
    monkeypatch.setattr(scheduler.threading, "Thread", FakeThread)

    scheduler.tick_schedules()

    assert created == [1]
    assert started == [(100, 1)]
