from datetime import date, datetime
from types import SimpleNamespace

from app.services.realtime_monitor import RealtimeMonitor


class FakeSession:
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True


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
