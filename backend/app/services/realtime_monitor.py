"""Single-process deterministic realtime monitor for Phase D."""
from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select

from ..config import settings
from ..database import SessionLocal
from ..market_engine_models import MarketScoreSnapshot
from ..market.providers.base import NormalizedQuote
from ..trigger_models import TriggerPlan
from ..triggers.engine import evaluate_holding_plan, evaluate_market_scores
from ..triggers.resolution import create_trigger_analysis_job
from ..triggers.service import apply_detection, expire_unmatched_detections
from ..v2_models import PortfolioSnapshot
from .market_engine import MarketEngine
from .market_snapshot_service import collect_snapshot_quotes
from .trading_calendar import AUCTION, CLOSED, LUNCH, MORNING, AFTERNOON, PRE_MARKET, CHINA_TZ, TradingCalendarService

logger = logging.getLogger(__name__)


class RealtimeMonitor:
    """Deterministic monitor; it never calls an LLM and owns one DB session per tick."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = SessionLocal,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.now_provider = now_provider or (lambda: datetime.now(CHINA_TZ))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_market_score_at: datetime | None = None
        self._state: dict[str, Any] = {
            "last_tick_at": None, "last_success_at": None, "last_error": None,
            "consecutive_errors": 0, "last_market_score_at": None,
            "tick_count": 0, "error_count": 0, "recent_confirmed_events": 0,
            "status": "stopped", "last_session": None,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="realtime-monitor", daemon=True)
        self._thread.start()
        self._set_state(status="running")
        logger.info("Realtime monitor started interval=%ss", settings.MONITOR_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(2.0, settings.MONITOR_INTERVAL_SECONDS + 1))
        self._thread = None
        self._set_state(status="stopped")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._state)

    def _set_state(self, **values: Any) -> None:
        with self._state_lock:
            self._state.update(values)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(settings.MONITOR_INTERVAL_SECONDS)

    def tick(self, *, portfolio_id: int | None = None, user_id: int | None = None, dry_run: bool = False) -> dict[str, Any]:
        if not self._tick_lock.acquire(blocking=False):
            self._set_state(last_error="monitor_tick_skipped_busy")
            logger.info("monitor_tick_skipped_busy")
            return {"status": "busy", "skipped": True, "reason": "monitor_tick_skipped_busy"}
        try:
            return self.run_once(portfolio_id=portfolio_id, user_id=user_id, dry_run=dry_run)
        finally:
            self._tick_lock.release()

    def run_once(self, *, portfolio_id: int | None = None, user_id: int | None = None, dry_run: bool = False) -> dict[str, Any]:
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=CHINA_TZ)
        now = now.astimezone(CHINA_TZ)
        self._set_state(last_tick_at=now.isoformat(), tick_count=self.status()["tick_count"] + 1)
        db = self.session_factory()
        summary: dict[str, Any] = {"status": "ok", "session": None, "events": [], "confirmed_events": 0, "analysis_jobs": 0, "market_score_calculated": False, "_analysis_job_ids": []}
        try:
            calendar = TradingCalendarService(db)
            session = calendar.current_session(now)
            summary["session"] = session
            self._set_state(last_session=session)
            if calendar.row_for(now.date()) is None:
                summary.update(status="not_ready", reason="calendar_not_initialized")
                self._set_state(status="degraded", last_success_at=now.isoformat(), consecutive_errors=0)
                return summary
            if session in {CLOSED, PRE_MARKET, AUCTION, LUNCH}:
                summary.update(status="paused", reason=f"session_{session.lower()}")
                self._set_state(status="paused", last_success_at=now.isoformat(), consecutive_errors=0)
                return summary

            snapshots = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.status == "confirmed")
            if portfolio_id is not None:
                snapshots = snapshots.filter(PortfolioSnapshot.portfolio_id == portfolio_id)
            if user_id is not None:
                snapshots = snapshots.filter(PortfolioSnapshot.user_id == user_id)
            snapshots = snapshots.order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).all()
            holdings_by_portfolio: dict[int, PortfolioSnapshot] = {}
            for snapshot in snapshots:
                holdings_by_portfolio.setdefault(snapshot.portfolio_id, snapshot)
            plans = db.query(TriggerPlan).filter(TriggerPlan.enabled.is_(True), TriggerPlan.target_type == "HOLDING")
            if portfolio_id is not None:
                plans = plans.filter(TriggerPlan.portfolio_id == portfolio_id)
            if user_id is not None:
                plans = plans.filter(TriggerPlan.user_id == user_id)
            plans = plans.all()
            codes = list(dict.fromkeys(row.target_key for row in plans))
            quotes = self._holding_quotes(codes) if codes else {}
            matched_keys: set[str] = set()
            for plan in plans:
                snapshot = holdings_by_portfolio.get(plan.portfolio_id)
                if snapshot is None or plan.target_key not in {row.code for row in snapshot.holdings}:
                    continue
                quote = quotes.get(plan.target_key)
                if quote is None:
                    continue
                plan.last_evaluated_at = now
                detection = evaluate_holding_plan(plan, quote, portfolio_snapshot_id=snapshot.id)
                if detection is None:
                    continue
                matched_keys.add(detection.dedupe_key)
                summary_event, confirmed = (None, False) if dry_run else apply_detection(db, detection, now=now)
                if summary_event is not None:
                    if confirmed:
                        plan.last_triggered_at = now
                    summary["events"].append(summary_event.id)
                    if confirmed:
                        summary["confirmed_events"] += 1
                        if detection.priority in {"P0", "P1"} and detection.trigger_type != "DATA_QUALITY" and settings.TRIGGER_AUTO_FAST_ANALYSIS_ENABLED:
                            job = create_trigger_analysis_job(db, summary_event)
                            if job is not None:
                                summary["analysis_jobs"] += 1
                                if job.id not in summary["_analysis_job_ids"]:
                                    summary["_analysis_job_ids"].append(job.id)

            if not dry_run:
                expire_unmatched_detections(db, matched_keys=matched_keys, now=now, trigger_types=["HOLDING"])
            if self._should_calculate_market_score(now):
                result = MarketEngine(db).calculate(captured_at=now, persist=not dry_run)
                summary["market_score_calculated"] = True
                self._last_market_score_at = now
                self._set_state(last_market_score_at=now.isoformat())
            current, previous = self._market_score_pair(db, now)
            if current is not None:
                detections = evaluate_market_scores(current, previous)
                market_targets = list(holdings_by_portfolio.values()) or [None]
                for detection in detections:
                    for target_snapshot in market_targets:
                        scoped_detection = replace(
                            detection,
                            user_id=getattr(target_snapshot, "user_id", None),
                            portfolio_id=getattr(target_snapshot, "portfolio_id", None),
                            portfolio_snapshot_id=getattr(target_snapshot, "id", None),
                            dedupe_key=(
                                f"{detection.dedupe_key}:portfolio:{target_snapshot.portfolio_id}"
                                if target_snapshot is not None else detection.dedupe_key
                            ),
                        )
                        summary_event, confirmed = (None, False) if dry_run else apply_detection(db, scoped_detection, now=now)
                        if summary_event is not None:
                            summary["events"].append(summary_event.id)
                            if confirmed:
                                summary["confirmed_events"] += 1
                                if scoped_detection.priority in {"P0", "P1"} and scoped_detection.trigger_type != "DATA_QUALITY" and settings.TRIGGER_AUTO_FAST_ANALYSIS_ENABLED:
                                    job = create_trigger_analysis_job(db, summary_event)
                                    if job is not None:
                                        summary["analysis_jobs"] += 1
                                        if job.id not in summary["_analysis_job_ids"]:
                                            summary["_analysis_job_ids"].append(job.id)
            db.commit()
            for job_id in summary.pop("_analysis_job_ids", []):
                threading.Thread(target=self._run_analysis_job, args=(job_id,), name=f"trigger-analysis-{job_id}", daemon=True).start()
            self._set_state(status="running", last_success_at=now.isoformat(), last_error=None, consecutive_errors=0, recent_confirmed_events=summary["confirmed_events"])
            return summary
        except Exception as exc:
            db.rollback()
            logger.exception("Realtime monitor tick failed")
            self._set_state(status="degraded", last_error=str(exc)[:500], error_count=self.status()["error_count"] + 1, consecutive_errors=self.status()["consecutive_errors"] + 1)
            return {**summary, "status": "error", "error": str(exc)}
        finally:
            db.close()

    def _holding_quotes(self, codes: list[str]) -> dict[str, NormalizedQuote]:
        try:
            raw = collect_snapshot_quotes({"codes": codes, "route": "critical"})
            values = raw.get("quotes", []) if isinstance(raw, dict) else []
            if isinstance(values, dict):
                values = list(values.values())
            return {
                getattr(quote, "code", None): quote
                for quote in values
                if isinstance(quote, NormalizedQuote) and getattr(quote, "code", None)
            }
        except Exception:
            return {}

    @staticmethod
    def _run_analysis_job(job_id: int) -> None:
        from .analysis_engine import run_analysis_job

        run_analysis_job(job_id)

    def _should_calculate_market_score(self, now: datetime) -> bool:
        last = self._last_market_score_at
        if last is None:
            return True
        return (now - last).total_seconds() >= settings.MARKET_SCORE_INTERVAL_MINUTES * 60

    def _market_score_pair(self, db: Any, now: datetime) -> tuple[Any | None, Any | None]:
        current = db.execute(select(MarketScoreSnapshot).where(
            MarketScoreSnapshot.market == "CN", MarketScoreSnapshot.captured_at <= now
        ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
        if current is None:
            return None, None
        cutoff = current.captured_at - timedelta(minutes=settings.TRIGGER_MARKET_SCORE_WINDOW_MINUTES)
        previous = db.execute(select(MarketScoreSnapshot).where(MarketScoreSnapshot.market == "CN", MarketScoreSnapshot.captured_at <= cutoff).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
        return current, previous


_monitor = RealtimeMonitor()


def get_realtime_monitor() -> RealtimeMonitor:
    return _monitor


def start_realtime_monitor() -> None:
    if settings.REALTIME_MONITOR_ENABLED:
        _monitor.start()


def stop_realtime_monitor() -> None:
    _monitor.stop()


__all__ = ["RealtimeMonitor", "get_realtime_monitor", "start_realtime_monitor", "stop_realtime_monitor"]
