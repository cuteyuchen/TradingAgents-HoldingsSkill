"""Single-process deterministic realtime monitor for Phase D."""
from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, Callable

from sqlalchemy import select

from ..config import settings
from ..database import SessionLocal
from ..market_engine_models import MarketScoreSnapshot
from ..market.models import DataQualityStatus
from ..market.providers.base import NormalizedQuote
from ..market.providers.factory import create_quote_provider
from ..market.quality import compare_quotes
from ..trigger_models import TriggerPlan
from ..triggers.engine import TriggerDetection, evaluate_holding_plan, evaluate_market_scores
from ..triggers.resolution import create_trigger_analysis_job
from ..triggers.service import apply_detection, detection_would_confirm, expire_unmatched_detections
from ..shadow_models import ShadowAccount, ShadowOrderIntent
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
        summary: dict[str, Any] = {
            "status": "ok",
            "session": None,
            "events": [],
            "confirmed_events": 0,
            "analysis_jobs": 0,
            "reused_analysis_jobs": 0,
            "market_score_calculated": False,
        }
        analysis_job_ids: set[int] = set()
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
            active_plans: list[TriggerPlan] = []
            expired_plans: list[TriggerPlan] = []
            for plan in plans.all():
                if self._plan_expired(plan, now):
                    expired_plans.append(plan)
                elif self._plan_not_yet_valid(plan, now):
                    continue
                else:
                    active_plans.append(plan)
            holding_codes_by_portfolio = {
                key: {str(row.code) for row in snapshot.holdings if row.code}
                for key, snapshot in holdings_by_portfolio.items()
            }
            codes = list(dict.fromkeys(
                [code for values in holding_codes_by_portfolio.values() for code in values]
                + [row.target_key for row in active_plans]
                + [row.code for row in db.query(ShadowOrderIntent.code).join(
                    ShadowAccount,
                    ShadowOrderIntent.shadow_account_id == ShadowAccount.id,
                ).filter(
                    ShadowOrderIntent.status.in_(("PENDING", "PARTIAL")),
                    ShadowAccount.status == "ACTIVE",
                    *( [ShadowAccount.user_id == user_id] if user_id is not None else [] ),
                    *( [ShadowAccount.source_portfolio_id == portfolio_id] if portfolio_id is not None else [] ),
                ).all()]
            ))
            quotes = self._holding_quotes(codes) if codes else {}
            if not dry_run:
                summary["shadow"] = self._persist_shadow_quotes_and_process(
                    now=now,
                    codes=codes,
                    quotes=quotes,
                    user_id=user_id,
                    portfolio_id=portfolio_id,
                )
            matched_keys: set[str] = set()
            evaluated_plans: list[TriggerPlan] = []
            pending_holding: list[tuple[TriggerPlan, TriggerDetection]] = []
            pending_observations: list[tuple[TriggerPlan, float]] = []
            for plan in active_plans:
                snapshot = holdings_by_portfolio.get(plan.portfolio_id)
                if snapshot is None or plan.target_key not in holding_codes_by_portfolio.get(plan.portfolio_id, set()):
                    continue
                evaluated_plans.append(plan)
                quote = quotes.get(plan.target_key)
                if quote is None:
                    quote = NormalizedQuote(
                        code=plan.target_key,
                        provider="critical",
                        quality_status=DataQualityStatus.MISSING,
                        errors=["critical_quote_not_returned"],
                    )
                previous_value = self._last_observed_value(plan)
                detection = evaluate_holding_plan(
                    plan,
                    quote,
                    previous_value=previous_value,
                    portfolio_snapshot_id=snapshot.id,
                )
                observed_value = self._holding_metric_value(plan, quote)
                if observed_value is not None and quote.quality_status in {DataQualityStatus.VALID, DataQualityStatus.DEGRADED}:
                    pending_observations.append((plan, observed_value))
                if detection is None:
                    continue
                if (
                    detection.trigger_type == "HOLDING"
                    and detection.priority in {"P0", "P1"}
                    and detection_would_confirm(db, detection, now=now)
                ):
                    verified_quote, verification = self._verify_holding_quote(plan.target_key)
                    detection = evaluate_holding_plan(
                        plan,
                        verified_quote,
                        previous_value=previous_value,
                        portfolio_snapshot_id=snapshot.id,
                    )
                    if detection is None:
                        continue
                    detection = replace(
                        detection,
                        evidence={**detection.evidence, "dual_source_verification": verification},
                    )
                matched_keys.add(detection.dedupe_key)
                pending_holding.append((plan, detection))

            # No TriggerEvent write has occurred before this point.  The full
            # market calculation therefore owns an isolated DB session instead
            # of keeping a Monitor write transaction open while it fetches data.
            if self._should_calculate_market_score(now):
                market_db = self.session_factory()
                try:
                    MarketEngine(market_db).calculate(captured_at=now, persist=not dry_run)
                finally:
                    market_db.close()
                summary["market_score_calculated"] = True
                self._last_market_score_at = now
                self._set_state(last_market_score_at=now.isoformat())
                db.expire_all()
            current, previous, quality_previous = self._market_score_pair(db, now)
            pending_market: list[TriggerDetection] = []
            if current is not None:
                detections = evaluate_market_scores(current, previous, quality_previous=quality_previous)
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
                        matched_keys.add(scoped_detection.dedupe_key)
                        pending_market.append(scoped_detection)

            if not dry_run:
                for plan in expired_plans:
                    plan.enabled = False
                for plan in evaluated_plans:
                    plan.last_evaluated_at = now
                for plan, value in pending_observations:
                    self._record_observation(plan, value, now)
                for plan, detection in pending_holding:
                    self._persist_detection(
                        db,
                        detection,
                        now=now,
                        summary=summary,
                        analysis_job_ids=analysis_job_ids,
                        plan=plan,
                    )
                for detection in pending_market:
                    self._persist_detection(
                        db,
                        detection,
                        now=now,
                        summary=summary,
                        analysis_job_ids=analysis_job_ids,
                    )
                expire_unmatched_detections(
                    db,
                    matched_keys=matched_keys,
                    now=now,
                    trigger_types=["HOLDING", "MARKET", "DATA_QUALITY"],
                    user_id=user_id,
                    portfolio_id=portfolio_id,
                )
                db.commit()
                try:
                    from ..operations.notifications import dispatch_material_events

                    notification_targets = {
                        (snapshot.user_id, snapshot.portfolio_id)
                        for snapshot in holdings_by_portfolio.values()
                    }
                    for target_user_id, target_portfolio_id in notification_targets:
                        dispatch_material_events(
                            db,
                            user_id=target_user_id,
                            portfolio_id=target_portfolio_id,
                            as_of=now,
                        )
                except Exception:
                    # Notification delivery is advisory; persisted Monitor and
                    # Trigger facts remain authoritative when it fails.
                    logger.exception("Operating notification dispatch failed after monitor tick")
            for job_id in analysis_job_ids:
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

    def _persist_shadow_quotes_and_process(
        self,
        *,
        now: datetime,
        codes: list[str],
        quotes: dict[str, NormalizedQuote],
        user_id: int | None,
        portfolio_id: int | None,
    ) -> dict[str, Any]:
        """Persist the monitor's bounded quote sample and advance paper fills.

        Shadow writes use a separate session so a validation failure cannot
        roll back TriggerEvent or scheduler facts from the monitor tick.
        """

        shadow_db = self.session_factory()
        try:
            from ..shadow.service import persist_live_quote_observation, process_pending_shadow_intents

            persisted = 0
            for code in codes:
                quote = quotes.get(code)
                if quote is None:
                    continue
                _, created = persist_live_quote_observation(
                    shadow_db,
                    quote,
                    captured_at=now,
                    captured_at_precision="EXACT",
                )
                persisted += int(created)
            fills = process_pending_shadow_intents(
                shadow_db,
                now=now,
                user_id=user_id,
                portfolio_id=portfolio_id,
            )
            shadow_db.commit()
            return {"status": "ok", "quotes_persisted": persisted, "fills": fills}
        except Exception as exc:  # noqa: BLE001
            shadow_db.rollback()
            logger.exception("Shadow quote persistence/fill processing failed")
            return {"status": "degraded", "error": str(exc)[:300], "quotes_persisted": 0, "fills": {}}
        finally:
            shadow_db.close()

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _plan_expired(self, plan: TriggerPlan, now: datetime) -> bool:
        expires_at = self._as_utc(plan.expires_at)
        return expires_at is not None and expires_at <= self._as_utc(now)

    def _plan_not_yet_valid(self, plan: TriggerPlan, now: datetime) -> bool:
        valid_from = self._as_utc(plan.valid_from)
        return valid_from is not None and valid_from > self._as_utc(now)

    @staticmethod
    def _holding_metric_value(plan: TriggerPlan, quote: NormalizedQuote) -> float | None:
        metric = str(plan.metric or "").lower()
        if metric == "price":
            value = quote.price
        elif metric in {"pct_change", "change_pct"}:
            value = quote.pct_change
        else:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if isfinite(number) else None

    def _last_observed_value(self, plan: TriggerPlan) -> float | None:
        metadata = dict(plan.metadata_json or {})
        observation = metadata.get("last_observation")
        if not isinstance(observation, dict) or observation.get("metric") != str(plan.metric or "").lower():
            return None
        try:
            value = float(observation.get("value"))
        except (TypeError, ValueError):
            return None
        return value if isfinite(value) else None

    @staticmethod
    def _record_observation(plan: TriggerPlan, value: float, now: datetime) -> None:
        metadata = dict(plan.metadata_json or {})
        metadata["last_observation"] = {
            "metric": str(plan.metric or "").lower(),
            "value": value,
            "observed_at": now.isoformat(),
        }
        plan.metadata_json = metadata

    def _verify_holding_quote(self, code: str) -> tuple[NormalizedQuote, dict[str, Any]]:
        """Fetch two direct critical sources only before P0/P1 confirmation."""

        provider_names = list(dict.fromkeys((
            settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER,
            *settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS,
        )))[:2]
        quotes: list[NormalizedQuote] = []
        evidence: dict[str, Any] = {"providers": [], "comparison": None}
        for provider_name in provider_names:
            try:
                quote = create_quote_provider(provider_name).get_quote(code)
            except Exception as exc:  # noqa: BLE001
                evidence["providers"].append({"provider": provider_name, "error": str(exc)[:300]})
                continue
            if quote is None:
                evidence["providers"].append({"provider": provider_name, "error": "quote_not_returned"})
                continue
            quotes.append(quote)
            evidence["providers"].append({
                "provider": provider_name,
                "price": quote.price,
                "quality_status": quote.quality_status.value,
                "source_timestamp": quote.source_timestamp.isoformat() if quote.source_timestamp else None,
            })
        if len(quotes) < 2:
            return NormalizedQuote(
                code=code,
                provider="dual_source",
                quality_status=DataQualityStatus.MISSING,
                errors=["dual_source_verification_unavailable"],
            ), evidence
        comparison = compare_quotes(
            quotes[0],
            quotes[1],
            price_conflict_threshold_pct=settings.MARKET_QUOTE_CONFLICT_THRESHOLD_PCT,
        )
        evidence["comparison"] = {
            "quality_status": comparison.quality_status.value,
            "price_diff_pct": comparison.price_diff_pct,
            "prev_close_diff_pct": comparison.prev_close_diff_pct,
            "errors": list(comparison.errors),
        }
        verified = replace(quotes[0])
        verified.provider = "dual_source"
        verified.quality_status = comparison.quality_status
        verified.errors = list(dict.fromkeys([*verified.errors, *comparison.errors]))
        return verified, evidence

    def _persist_detection(
        self,
        db: Any,
        detection: TriggerDetection,
        *,
        now: datetime,
        summary: dict[str, Any],
        analysis_job_ids: set[int],
        plan: TriggerPlan | None = None,
    ) -> None:
        event, confirmed = apply_detection(db, detection, now=now)
        if event is None:
            return
        summary["events"].append(event.id)
        if not confirmed:
            return
        summary["confirmed_events"] += 1
        if plan is not None:
            plan.last_triggered_at = now
        if (
            detection.priority not in {"P0", "P1"}
            or detection.trigger_type == "DATA_QUALITY"
            or not settings.TRIGGER_AUTO_FAST_ANALYSIS_ENABLED
        ):
            return
        admission = create_trigger_analysis_job(db, event)
        if admission is None:
            return
        if admission.should_start:
            summary["analysis_jobs"] += 1
            analysis_job_ids.add(admission.job.id)
        else:
            summary["reused_analysis_jobs"] += 1

    @staticmethod
    def _run_analysis_job(job_id: int) -> None:
        from .analysis_engine import run_analysis_job

        run_analysis_job(job_id)

    def _should_calculate_market_score(self, now: datetime) -> bool:
        last = self._last_market_score_at
        if last is None:
            return True
        return (now - last).total_seconds() >= settings.MARKET_SCORE_INTERVAL_MINUTES * 60

    def _market_score_pair(self, db: Any, now: datetime) -> tuple[Any | None, Any | None, Any | None]:
        trade_date = now.astimezone(CHINA_TZ).date()
        current = db.execute(select(MarketScoreSnapshot).where(
            MarketScoreSnapshot.market == "CN",
            MarketScoreSnapshot.trade_date == trade_date,
            MarketScoreSnapshot.captured_at <= now,
        ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
        if current is None:
            return None, None, None
        tolerance = settings.TRIGGER_MARKET_SCORE_BASELINE_TOLERANCE_MINUTES
        window = settings.TRIGGER_MARKET_SCORE_WINDOW_MINUTES
        baseline_min = current.captured_at - timedelta(minutes=window + tolerance)
        baseline_max = current.captured_at - timedelta(minutes=max(1, window - tolerance))
        previous = db.execute(select(MarketScoreSnapshot).where(
            MarketScoreSnapshot.market == "CN",
            MarketScoreSnapshot.trade_date == current.trade_date,
            MarketScoreSnapshot.captured_at >= baseline_min,
            MarketScoreSnapshot.captured_at <= baseline_max,
            MarketScoreSnapshot.is_frozen.is_(False),
            MarketScoreSnapshot.display_score.is_not(None),
        ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
        quality_previous = db.execute(select(MarketScoreSnapshot).where(
            MarketScoreSnapshot.market == "CN",
            MarketScoreSnapshot.trade_date == current.trade_date,
            MarketScoreSnapshot.captured_at < current.captured_at,
        ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(1)).scalar_one_or_none()
        return current, previous, quality_previous


_monitor = RealtimeMonitor()


def get_realtime_monitor() -> RealtimeMonitor:
    return _monitor


def start_realtime_monitor() -> None:
    if settings.REALTIME_MONITOR_ENABLED:
        _monitor.start()


def stop_realtime_monitor() -> None:
    _monitor.stop()


__all__ = ["RealtimeMonitor", "get_realtime_monitor", "start_realtime_monitor", "stop_realtime_monitor"]
