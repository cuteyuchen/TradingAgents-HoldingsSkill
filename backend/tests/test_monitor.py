from datetime import date, datetime
from types import SimpleNamespace

from app.services.realtime_monitor import RealtimeMonitor


class FakeSession:
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True


def test_monitor_loop_survives_tick_exception(monkeypatch):
    monitor = RealtimeMonitor(session_factory=lambda: FakeSession(), now_provider=lambda: datetime(2026, 8, 24, 10, 0))
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick exploded")
        return {"status": "ok"}

    def wait(_timeout=None):
        if calls["n"] >= 2:
            monitor._stop.set()
            return True
        return False

    monkeypatch.setattr(monitor, "tick", boom)
    monkeypatch.setattr(monitor._stop, "wait", wait)
    monitor._loop()
    assert calls["n"] >= 2
    assert monitor.status()["status"] == "degraded"


def test_monitor_pauses_lunch_without_market_calls(monkeypatch):
    session = FakeSession()
    monitor = RealtimeMonitor(session_factory=lambda: session, now_provider=lambda: datetime(2026, 8, 24, 12, 0))
    class Calendar:
        def current_session(self, _): return "LUNCH"
        def row_for(self, _): return SimpleNamespace(is_open=True)
    monkeypatch.setattr("app.services.realtime_monitor.TradingCalendarService", lambda db: Calendar())
    result = monitor.run_once()
    assert result["status"] == "paused"
    assert result["reason"] == "session_lunch"
    assert session.closed


def test_monitor_busy_single_flight(monkeypatch):
    monitor = RealtimeMonitor()
    monitor._tick_lock.acquire()
    try:
        result = monitor.tick()
    finally:
        monitor._tick_lock.release()
    assert result["skipped"] is True
    assert result["reason"] == "monitor_tick_skipped_busy"
