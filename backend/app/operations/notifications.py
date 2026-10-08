"""Material operating-event notifications over the existing webhook channels."""
from __future__ import annotations

import logging
import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..candidates.models import CandidateRun, CandidateScore
from ..config import settings
from ..market_engine_models import MarketScoreSnapshot
from ..market_runtime_models import ProviderHealth
from ..services.notifications import _post_channel
from ..services.trading_calendar import CHINA_TZ
from ..trigger_models import TriggerEvent
from ..v2_models import AnalysisJob, AnalysisRun, NotificationChannel, PortfolioSnapshot
from ..memory.models import DailyReviewRun
from .config import NOTIFICATION_COOLDOWNS_MINUTES, NOTIFICATION_DISPATCH_LEASE
from .models import DailyOperationalRun, OperatingNotification
from .workflow import ensure_operational_run

logger = logging.getLogger(__name__)

PUSH_SEVERITIES = frozenset({"CRITICAL", "ACTION_REQUIRED", "IMPORTANT"})


@dataclass(frozen=True, slots=True)
class OperatingNotificationEvent:
    title: str
    summary: str
    severity: str
    portfolio_id: int
    user_id: int
    event_type: str
    entity_type: str
    entity_id: str
    occurred_at: datetime
    deep_link: str
    dedupe_key: str


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_utc(value)
    try:
        return _aware_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _notification_id(dedupe_key: str) -> str:
    digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:24]
    return f"opn_{digest}"


def _state_rows(db: Session, *, user_id: int, portfolio_id: int | None = None) -> list[dict[str, Any]]:
    query = select(DailyOperationalRun.notification_state_json).where(
        DailyOperationalRun.user_id == user_id,
    )
    if portfolio_id is not None:
        query = query.where(DailyOperationalRun.portfolio_id == portfolio_id)
    return [state for state in db.execute(query.order_by(DailyOperationalRun.trade_date.desc(), DailyOperationalRun.id.desc())).scalars().all() if isinstance(state, dict)]


def _active_data_health_dedupe_key(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    severity: str,
    cutoff: datetime,
) -> str | None:
    """Reuse the persisted key while one provider-outage episode is active."""

    cutoff_aware = _aware_utc(cutoff)
    outage_events: list[tuple[datetime, dict[str, Any]]] = []
    recovery_times: list[datetime] = []
    for state in _state_rows(db, user_id=user_id, portfolio_id=portfolio_id):
        for payload in state.get("events") or []:
            if not isinstance(payload, dict):
                continue
            occurred_at = _parse_time(payload.get("occurred_at"))
            if occurred_at is None or occurred_at > cutoff_aware:
                continue
            event_type = str(payload.get("event_type") or "").lower()
            if event_type == "provider_recovery":
                recovery_times.append(occurred_at)
            elif (
                event_type == "data_health"
                and str(payload.get("entity_id") or "") == "critical_quote_chain"
                and str(payload.get("severity") or "").upper() == severity
            ):
                outage_events.append((occurred_at, payload))
    if not outage_events:
        return None
    latest_at, latest = max(outage_events, key=lambda item: item[0])
    if recovery_times and max(recovery_times) > latest_at:
        return None
    key = latest.get("dedupe_key")
    return str(key) if key else None


def _existing_dedupe_entry(db: Session, *, user_id: int, portfolio_id: int, dedupe_key: str) -> dict[str, Any] | None:
    for state in _state_rows(db, user_id=user_id, portfolio_id=portfolio_id):
        entry = (state.get("dedupe") or {}).get(dedupe_key)
        if isinstance(entry, dict):
            return entry
        for payload in state.get("events") or []:
            if isinstance(payload, dict) and payload.get("dedupe_key") == dedupe_key:
                return payload
    return None


def _event_payload_matches(payload: dict[str, Any], notification_id: str) -> bool:
    return notification_id in {
        str(payload.get("notification_id") or ""),
        str(payload.get("dedupe_key") or ""),
        str(payload.get("id") or ""),
    }


def _last_delivery_at(db: Session, *, user_id: int, portfolio_id: int, dedupe_key: str) -> datetime | None:
    entry = _existing_dedupe_entry(db, user_id=user_id, portfolio_id=portfolio_id, dedupe_key=dedupe_key)
    if isinstance(entry, dict):
        return _parse_time(entry.get("sent_at"))
    return None


def _event_markdown(event: OperatingNotificationEvent) -> str:
    return "\n\n".join((
        f"### {event.title}",
        f"**级别：** {event.severity}",
        event.summary,
        f"[打开今日操作台]({settings.PUBLIC_APP_URL}{event.deep_link})",
        "仅供研究辅助，不构成交易指令。",
    ))


def _naive_utc(value: datetime | None) -> datetime | None:
    return _aware_utc(value).replace(tzinfo=None) if value is not None else None


def _claim_notification(
    db: Session,
    *,
    event: OperatingNotificationEvent,
    moment: datetime,
    cooldown_minutes: int,
) -> tuple[OperatingNotification, bool, str]:
    """Claim a durable event row before any webhook side effect."""

    query = select(OperatingNotification).where(
        OperatingNotification.user_id == event.user_id,
        OperatingNotification.portfolio_id == event.portfolio_id,
        OperatingNotification.dedupe_key == event.dedupe_key,
    )
    existing = db.execute(query).scalar_one_or_none()
    if existing is None:
        candidate = OperatingNotification(
            notification_id=_notification_id(event.dedupe_key),
            user_id=event.user_id,
            portfolio_id=event.portfolio_id,
            trade_date=_aware_utc(event.occurred_at).astimezone(CHINA_TZ).date(),
            dedupe_key=event.dedupe_key,
            event_type=event.event_type,
            severity=str(event.severity or "INFO").upper(),
            status="DISPATCHING",
            occurred_at=_naive_utc(event.occurred_at),
            last_attempt_at=_naive_utc(moment),
            lease_expires_at=_naive_utc(moment) + NOTIFICATION_DISPATCH_LEASE,
            attempt_count=1,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            db.commit()
            return candidate, True, "CLAIMED"
        except IntegrityError:
            existing = db.execute(query).scalar_one_or_none()
            if existing is None:
                raise
    if existing is None:
        raise RuntimeError("notification_claim_missing")
    status = str(existing.status or "").upper()
    if status in {"SENT", "DASHBOARD_ONLY"}:
        return existing, False, "DEDUPED"
    if status == "DISPATCHING":
        moment_naive = _naive_utc(moment)
        expires_at = _naive_utc(existing.lease_expires_at)
        stale_condition = (
            (OperatingNotification.lease_expires_at.is_not(None) & (OperatingNotification.lease_expires_at <= moment_naive))
            | (
                OperatingNotification.lease_expires_at.is_(None)
                & (
                    OperatingNotification.last_attempt_at.is_(None)
                    | (OperatingNotification.last_attempt_at <= moment_naive - NOTIFICATION_DISPATCH_LEASE)
                )
            )
        )
        last_attempt = _parse_time(existing.last_attempt_at)
        stale = (
            expires_at <= moment_naive
            if expires_at is not None
            else last_attempt is None or moment - last_attempt >= NOTIFICATION_DISPATCH_LEASE
        )
        if stale:
            result = db.execute(
                update(OperatingNotification)
                .where(
                    OperatingNotification.id == existing.id,
                    OperatingNotification.status == "DISPATCHING",
                    stale_condition,
                )
                .values(
                    last_attempt_at=moment_naive,
                    lease_expires_at=moment_naive + NOTIFICATION_DISPATCH_LEASE,
                    attempt_count=OperatingNotification.attempt_count + 1,
                    last_error=None,
                )
            )
            db.commit()
            if result.rowcount:
                refreshed = db.execute(select(OperatingNotification).where(OperatingNotification.id == existing.id)).scalar_one()
                return refreshed, True, "RECLAIMED"
            refreshed = db.execute(select(OperatingNotification).where(OperatingNotification.id == existing.id)).scalar_one()
            return refreshed, False, "ALREADY_CLAIMED"
        return existing, False, "ALREADY_CLAIMED"
    if status == "FAILED":
        last_attempt = _parse_time(existing.last_attempt_at)
        if last_attempt is not None and moment - last_attempt < timedelta(minutes=max(0, cooldown_minutes)):
            return existing, False, "COOLDOWN"
        cutoff = _naive_utc(moment - timedelta(minutes=max(0, cooldown_minutes)))
        result = db.execute(
            update(OperatingNotification)
            .where(
                OperatingNotification.id == existing.id,
                OperatingNotification.status == "FAILED",
                (OperatingNotification.last_attempt_at.is_(None) | (OperatingNotification.last_attempt_at <= cutoff)),
            )
            .values(
                status="DISPATCHING",
                last_attempt_at=_naive_utc(moment),
                lease_expires_at=_naive_utc(moment) + NOTIFICATION_DISPATCH_LEASE,
                attempt_count=OperatingNotification.attempt_count + 1,
                last_error=None,
            )
        )
        db.commit()
        if result.rowcount:
            refreshed = db.execute(select(OperatingNotification).where(OperatingNotification.id == existing.id)).scalar_one()
            return refreshed, True, "RETRY"
        refreshed = db.execute(select(OperatingNotification).where(OperatingNotification.id == existing.id)).scalar_one()
        return refreshed, False, "ALREADY_CLAIMED"
    return existing, False, "ALREADY_CLAIMED"


def dispatch_operating_event(db: Session, event: OperatingNotificationEvent, *, now: datetime | None = None) -> dict[str, Any]:
    moment = _aware_utc(now or datetime.now(UTC))
    cooldown = NOTIFICATION_COOLDOWNS_MINUTES.get(event.event_type, settings.NOTIFICATION_DEFAULT_COOLDOWN_MINUTES)
    local_date = _aware_utc(event.occurred_at).astimezone(CHINA_TZ).date()
    op_run = ensure_operational_run(db, user_id=event.user_id, portfolio_id=event.portfolio_id, trade_date=local_date)
    durable, owns, claim_status = _claim_notification(
        db,
        event=event,
        moment=moment,
        cooldown_minutes=cooldown,
    )
    if not owns:
        return {
            "status": claim_status,
            "notification_id": durable.notification_id,
            "dedupe_key": event.dedupe_key,
            "event_type": event.event_type,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "severity": event.severity,
            "sent_at": durable.sent_at.isoformat() if durable.sent_at else None,
        }
    # Pre-H.1 rows used only JSON state. Preserve their dedupe semantics when
    # the durable table is introduced into an existing installation.
    legacy = _existing_dedupe_entry(
        db,
        user_id=event.user_id,
        portfolio_id=event.portfolio_id,
        dedupe_key=event.dedupe_key,
    )
    if legacy is not None and claim_status == "CLAIMED":
        durable.status = str(legacy.get("status") or "DASHBOARD_ONLY").upper()
        durable.sent_at = _naive_utc(_parse_time(legacy.get("sent_at")))
        durable.payload_json = dict(legacy)
        durable.lease_expires_at = None
        db.commit()
        return {"status": "DEDUPED", "notification_id": durable.notification_id, "dedupe_key": event.dedupe_key, "sent_at": legacy.get("sent_at")}
    state = dict(op_run.notification_state_json or {})
    deliveries: list[dict[str, Any]] = []
    severity = str(event.severity or "INFO").upper()
    channels = []
    if severity in PUSH_SEVERITIES:
        channels = db.execute(select(NotificationChannel).where(
            NotificationChannel.user_id == event.user_id,
            NotificationChannel.enabled.is_(True),
        )).scalars().all()
        for channel in channels:
            try:
                status_code, _excerpt = _post_channel(channel, event.title, _event_markdown(event))
                deliveries.append({"channel_id": channel.id, "status": "SENT", "response_code": status_code})
            except Exception as exc:  # notifications never fail the owning workflow
                logger.exception("Operating notification delivery failed event=%s channel=%s", event.event_type, channel.id)
                deliveries.append({"channel_id": channel.id, "status": "FAILED", "error": str(exc)[:300]})
    delivery_status = "SENT" if any(item["status"] == "SENT" for item in deliveries) else "DASHBOARD_ONLY" if not channels else "FAILED"
    notification_id = durable.notification_id
    payload = {
        **asdict(event),
        "notification_id": notification_id,
        "occurred_at": _aware_utc(event.occurred_at).isoformat(),
        "sent_at": moment.isoformat(),
        "status": delivery_status,
        "deliveries": deliveries,
        "cooldown_minutes": cooldown,
        "read": False,
        "read_at": None,
    }
    dedupe = dict(state.get("dedupe") or {})
    dedupe[event.dedupe_key] = {
        "notification_id": notification_id,
        "sent_at": moment.isoformat(),
        "status": delivery_status,
        "event_type": event.event_type,
    }
    events = [payload, *list(state.get("events") or [])][:50]
    op_run.notification_state_json = {"dedupe": dedupe, "events": events}
    durable.status = delivery_status
    durable.payload_json = payload
    durable.sent_at = _naive_utc(moment)
    durable.last_attempt_at = _naive_utc(moment)
    durable.last_error = next((str(item.get("error")) for item in deliveries if item.get("status") == "FAILED"), None)
    durable.lease_expires_at = None
    db.commit()
    logger.info("notification_event type=%s dedupe=%s severity=%s result=%s", event.event_type, event.dedupe_key, event.severity, delivery_status)
    return payload


def _candidate_events(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> list[OperatingNotificationEvent]:
    runs = db.execute(select(CandidateRun).where(
        CandidateRun.user_id == user_id,
        CandidateRun.portfolio_id == portfolio_id,
        CandidateRun.as_of <= cutoff,
        CandidateRun.status == "COMPLETED",
    ).order_by(CandidateRun.as_of.desc(), CandidateRun.id.desc()).limit(2)).scalars().all()
    if not runs or str(runs[0].quality_status).upper() in {"MISSING", "BLOCKED", "BLOCKED_FOR_ACTION"}:
        return []
    latest_snapshot_id = db.execute(select(PortfolioSnapshot.id).where(
        PortfolioSnapshot.user_id == user_id,
        PortfolioSnapshot.portfolio_id == portfolio_id,
        PortfolioSnapshot.status == "confirmed",
        PortfolioSnapshot.snapshot_time <= cutoff,
    ).order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    current = runs[0]
    if latest_snapshot_id is not None and current.portfolio_snapshot_id != latest_snapshot_id:
        return []
    current_scores = {row.code: row for row in db.execute(select(CandidateScore).where(CandidateScore.candidate_run_id == current.id)).scalars().all()}
    previous_scores: dict[str, CandidateScore] = {}
    if len(runs) > 1:
        previous_scores = {row.code: row for row in db.execute(select(CandidateScore).where(CandidateScore.candidate_run_id == runs[1].id)).scalars().all()}
    events: list[OperatingNotificationEvent] = []
    for code, score in current_scores.items():
        previous_stage = str(previous_scores.get(code).stage or "").upper() if code in previous_scores else None
        current_stage = str(score.stage or "").upper()
        if current_stage == "ACTION" and previous_stage != "ACTION":
            summary = f"{score.name or code}（{code}）进入 ACTION 决策候选；这不是最终买入建议，仍以组合 Gate 和最新 Decision 为准。"
            semantic = "ACTION"
        elif previous_stage == "ACTION" and current_stage != "ACTION":
            summary = f"{score.name or code}（{code}）已退出 ACTION 决策候选，当前阶段为 {current_stage or 'WATCHLIST'}。"
            semantic = current_stage or "WATCHLIST"
        else:
            continue
        events.append(OperatingNotificationEvent(
            title="候选状态发生变化", summary=summary, severity="IMPORTANT", portfolio_id=portfolio_id, user_id=user_id,
            event_type="candidate_stage", entity_type="candidate", entity_id=code, occurred_at=_aware_utc(current.captured_at),
            deep_link=f"/dashboard?portfolio={portfolio_id}#candidates",
            dedupe_key=f"candidate_stage:{portfolio_id}:{current.id}:{code}:{previous_stage or 'NEW'}->{semantic}",
        ))
    return events


def _normalise_final_action(run: AnalysisRun) -> str:
    result = (run.structured_result_json or {}).get("result", {})
    gate = result.get("decision_gate") if isinstance(result.get("decision_gate"), dict) else {}
    raw = result.get("portfolio_action") or gate.get("portfolio_action") or run.final_rating or "NO_ACTION"
    value = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
    if value in {"NO_ACTION", "HOLD", "HOLD_ONLY", "WATCH", "WATCH_ONLY"}:
        return "NO_ACTION"
    if value in {"REDUCE", "SELL", "EXIT", "DE_RISK", "DERISK"}:
        return "REDUCE"
    if value in {"ACTION", "ADD", "BUY", "NEW_POSITION", "ROTATE", "REBALANCE"}:
        return "ACTION"
    if value in {"BLOCKED", "BLOCK"} or str(run.data_quality_grade or "").upper() in {"BLOCKED", "MISSING", "F"}:
        return "BLOCKED"
    return value or "NO_ACTION"


def _analysis_events(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> list[OperatingNotificationEvent]:
    runs = db.execute(select(AnalysisRun).join(
        AnalysisJob, AnalysisRun.job_id == AnalysisJob.id,
    ).where(
        AnalysisRun.user_id == user_id,
        AnalysisJob.portfolio_id == portfolio_id,
        AnalysisJob.status == "succeeded",
        AnalysisRun.created_at <= cutoff,
    ).order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc()).limit(2)).scalars().all()
    if not runs:
        return []
    current = runs[0]
    previous_action = _normalise_final_action(runs[1]) if len(runs) > 1 else None
    current_action = _normalise_final_action(current)
    if previous_action == current_action:
        return []
    if current.job is not None and current.job.trigger_type == "realtime_trigger" and current_action == "NO_ACTION":
        # The trigger-resolution event is the single user-facing closure for
        # this path; avoid a duplicate generic analysis message.
        return []
    severity = "INFO" if current_action == "NO_ACTION" else "ACTION_REQUIRED" if current_action == "REDUCE" else "IMPORTANT"
    transition = f"{previous_action or 'INITIAL'}->{current_action}"
    return [OperatingNotificationEvent(
        title="组合最终决策发生变化",
        summary=f"最新分析已完成，组合最终裁决为 {current_action}。请打开报告查看完整的持仓动作与组合 Gate。",
        severity=severity,
        portfolio_id=portfolio_id,
        user_id=user_id,
        event_type="analysis_action",
        entity_type="analysis_run",
        entity_id=str(current.id),
        occurred_at=_aware_utc(current.created_at),
        deep_link=f"/reports?portfolio={portfolio_id}&run={current.id}",
        dedupe_key=f"analysis_action:{portfolio_id}:{transition}",
    )]


def _review_events(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> list[OperatingNotificationEvent]:
    local_date = cutoff.replace(tzinfo=UTC).astimezone(CHINA_TZ).date()
    review = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == user_id,
        DailyReviewRun.portfolio_id == portfolio_id,
        DailyReviewRun.trade_date == local_date,
        DailyReviewRun.status == "COMPLETED",
        DailyReviewRun.created_at <= cutoff,
    ).order_by(DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
    if review is None:
        return []
    occurred_at = _aware_utc(review.last_refreshed_at or review.completed_at or review.created_at)
    if occurred_at > _aware_utc(cutoff):
        return []
    refreshed = int(review.refresh_count or 0) > 0
    important = int(review.outcomes_matured_count or 0) > 0 or str(review.quality_status or "").upper() != "VALID"
    event_type = "daily_review_refresh" if refreshed else "daily_review_completed"
    severity = "IMPORTANT" if important else "INFO"
    suffix = f"refresh:{int(review.refresh_count or 0)}" if refreshed else "completed"
    summary = (
        f"{('Daily Review 已刷新' if refreshed else 'Daily Review 已完成')}；"
        f"今日成熟 Outcome {int(review.outcomes_matured_count or 0)} 条，质量 {str(review.quality_status or 'UNKNOWN').upper()}。"
    )
    return [OperatingNotificationEvent(
        title="Daily Review 状态更新",
        summary=summary,
        severity=severity,
        portfolio_id=portfolio_id,
        user_id=user_id,
        event_type=event_type,
        entity_type="daily_review",
        entity_id=str(review.id),
        occurred_at=occurred_at,
        deep_link=f"/dashboard?portfolio={portfolio_id}#memory",
        dedupe_key=f"daily_review:{portfolio_id}:{local_date.isoformat()}:{suffix}",
    )]


def _provider_recovery_events(db: Session, *, user_id: int, portfolio_id: int, cutoff: datetime) -> list[OperatingNotificationEvent]:
    provider_names = list(dict.fromkeys((settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER, *settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS)))
    rows = db.execute(select(ProviderHealth).where(
        ProviderHealth.provider_name.in_(provider_names),
        ProviderHealth.data_type == "quote",
    )).scalars().all()
    events: list[OperatingNotificationEvent] = []
    cutoff_aware = _aware_utc(cutoff)
    for row in rows:
        success_at = _parse_time(row.last_success_at)
        failure_at = _parse_time(row.last_failure_at)
        if str(row.status).upper() != "HEALTHY" or success_at is None or failure_at is None or success_at <= failure_at or success_at > cutoff_aware:
            continue
        events.append(OperatingNotificationEvent(
            title="行情数据源已恢复",
            summary=f"行情数据源 {row.provider_name} 已恢复健康，最近成功时间 {success_at.isoformat()}。",
            severity="IMPORTANT",
            portfolio_id=portfolio_id,
            user_id=user_id,
            event_type="provider_recovery",
            entity_type="provider",
            entity_id=row.provider_name,
            occurred_at=success_at,
            deep_link=f"/dashboard?portfolio={portfolio_id}#health",
            dedupe_key=f"provider_recovery:{portfolio_id}:{row.provider_name}:{failure_at.isoformat()}",
        ))
    return events


def collect_material_events(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> list[OperatingNotificationEvent]:
    cutoff = as_of or datetime.now(UTC).replace(tzinfo=None)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.astimezone(UTC).replace(tzinfo=None)
    events: list[OperatingNotificationEvent] = []
    scores = db.execute(select(MarketScoreSnapshot).where(
        MarketScoreSnapshot.market == "CN", MarketScoreSnapshot.captured_at <= cutoff,
    ).order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc()).limit(2)).scalars().all()
    if len(scores) == 2 and scores[0].regime and scores[0].regime != scores[1].regime:
        row = scores[0]
        transition = f"{scores[1].regime}->{row.regime}"
        events.append(OperatingNotificationEvent(
            title="市场状态切换", summary=f"市场 Regime 从 {scores[1].regime} 切换为 {row.regime}。", severity="IMPORTANT",
            portfolio_id=portfolio_id, user_id=user_id, event_type="market_regime", entity_type="market_score", entity_id=row.snapshot_id,
            occurred_at=_aware_utc(row.captured_at), deep_link=f"/dashboard?portfolio={portfolio_id}#market",
            dedupe_key=f"market_regime:{portfolio_id}:{transition}",
        ))
    events.extend(_analysis_events(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    events.extend(_candidate_events(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    local_date = cutoff.replace(tzinfo=UTC).astimezone(CHINA_TZ).date()
    day_start = datetime.combine(local_date, datetime.min.time()).replace(tzinfo=CHINA_TZ).astimezone(UTC).replace(tzinfo=None)
    triggers = db.execute(select(TriggerEvent).where(
        TriggerEvent.portfolio_id == portfolio_id,
        TriggerEvent.user_id == user_id,
        TriggerEvent.priority.in_(("P0", "P1")),
        TriggerEvent.detected_at >= day_start,
        TriggerEvent.detected_at <= cutoff,
        TriggerEvent.status.in_(("CONFIRMED", "ANALYZING", "RESOLVED")),
    )).scalars().all()
    for trigger in triggers:
        if trigger.status == "RESOLVED" and trigger.resolution == "NO_ACTION":
            event_type, severity = "trigger_resolution", "INFO"
            title, summary, semantic = "触发复核完成", "已完成触发复核，最终结论为 NO_ACTION，无需操作。", "NO_ACTION"
        elif trigger.status == "RESOLVED":
            continue
        else:
            event_type, severity = "trigger_confirmed", "CRITICAL" if trigger.priority == "P0" else "IMPORTANT"
            title, summary, semantic = "重要触发已确认", f"{trigger.priority} {trigger.trigger_type} 已确认，需要重新分析；Trigger 本身不是交易信号。", "CONFIRMED"
        events.append(OperatingNotificationEvent(
            title=title, summary=summary, severity=severity, portfolio_id=portfolio_id, user_id=user_id, event_type=event_type,
            entity_type="trigger", entity_id=str(trigger.id), occurred_at=_aware_utc(trigger.confirmed_at or trigger.detected_at),
            deep_link=f"/dashboard?portfolio={portfolio_id}#triggers", dedupe_key=f"{event_type}:{portfolio_id}:{trigger.id}:{semantic}",
        ))
    events.extend(_review_events(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    events.extend(_provider_recovery_events(db, user_id=user_id, portfolio_id=portfolio_id, cutoff=cutoff))
    provider_names = list(dict.fromkeys((settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER, *settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS)))
    health_rows = {row.provider_name: row for row in db.execute(select(ProviderHealth).where(ProviderHealth.provider_name.in_(provider_names), ProviderHealth.data_type == "quote")).scalars().all()}
    if health_rows:
        primary = health_rows.get(settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER)
        fallbacks = [health_rows.get(name) for name in settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS]
        healthy = lambda row: row is not None and str(row.status).upper() == "HEALTHY"
        if not healthy(primary) and any(healthy(row) for row in fallbacks):
            semantic, severity, summary = "DEGRADED", "IMPORTANT", "行情主源不可用，备用源仍健康；系统处于 DEGRADED，提醒已去重。"
        elif not healthy(primary) and not any(healthy(row) for row in fallbacks):
            semantic, severity, summary = "BLOCKED", "CRITICAL", "主、备用行情源均不可可靠使用，暂停增加新风险；不会自动卖出现有持仓。"
        else:
            semantic = "OK"
        if semantic != "OK":
            failure_markers = [
                _parse_time(row.last_failure_at)
                for row in (primary, *fallbacks)
                if row is not None and _parse_time(row.last_failure_at) is not None
            ]
            latest_failure = max(failure_markers) if failure_markers else None
            active_key = _active_data_health_dedupe_key(
                db,
                user_id=user_id,
                portfolio_id=portfolio_id,
                severity=severity,
                cutoff=cutoff,
            )
            dedupe_key = active_key or (
                f"data_health:{portfolio_id}:critical_quote:{semantic}:"
                f"{latest_failure.isoformat() if latest_failure else 'initial'}"
            )
            events.append(OperatingNotificationEvent(
                title="市场数据健康异常", summary=summary, severity=severity, portfolio_id=portfolio_id, user_id=user_id,
                event_type="data_health", entity_type="provider", entity_id="critical_quote_chain",
                occurred_at=latest_failure or _aware_utc(cutoff),
                deep_link=f"/dashboard?portfolio={portfolio_id}#health", dedupe_key=dedupe_key,
            ))
    return events


def dispatch_material_events(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> list[dict[str, Any]]:
    results = []
    for event in collect_material_events(db, user_id=user_id, portfolio_id=portfolio_id, as_of=as_of):
        results.append(dispatch_operating_event(db, event, now=as_of))
    return results


def list_operating_notifications(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int | None = None,
    as_of: datetime | None = None,
    limit: int = 20,
    unread_only: bool = False,
) -> dict[str, Any]:
    """Read persisted operating events without triggering collection or delivery."""

    cutoff = _aware_utc(as_of or datetime.now(UTC))
    events_by_id: dict[str, tuple[datetime, dict[str, Any]]] = {}
    durable_query = select(OperatingNotification).where(
        OperatingNotification.user_id == user_id,
        OperatingNotification.occurred_at <= _naive_utc(cutoff),
    )
    if portfolio_id is not None:
        durable_query = durable_query.where(OperatingNotification.portfolio_id == portfolio_id)
    for row in db.execute(durable_query.order_by(OperatingNotification.occurred_at.desc(), OperatingNotification.id.desc())).scalars().all():
        payload = dict(row.payload_json or {})
        payload.setdefault("notification_id", row.notification_id)
        payload.setdefault("dedupe_key", row.dedupe_key)
        payload.setdefault("event_type", row.event_type)
        payload.setdefault("severity", row.severity)
        payload.setdefault("portfolio_id", row.portfolio_id)
        payload.setdefault("user_id", row.user_id)
        payload.setdefault("occurred_at", row.occurred_at.isoformat())
        payload.setdefault("sent_at", row.sent_at.isoformat() if row.sent_at else None)
        payload["status"] = row.status
        payload["read"] = bool(row.read)
        payload["read_at"] = row.read_at.isoformat() if row.read_at else None
        occurred_at = _parse_time(payload.get("occurred_at"))
        if occurred_at is not None:
            events_by_id[row.notification_id] = (occurred_at, payload)
    for state in _state_rows(db, user_id=user_id, portfolio_id=portfolio_id):
        for payload in (state or {}).get("events", []):
            if not isinstance(payload, dict):
                continue
            if portfolio_id is not None and int(payload.get("portfolio_id") or -1) != portfolio_id:
                continue
            occurred_at = _parse_time(payload.get("occurred_at")) or _parse_time(payload.get("sent_at"))
            if occurred_at is None or occurred_at > cutoff:
                continue
            item = dict(payload)
            dedupe_key = str(item.get("dedupe_key") or item.get("entity_id") or "")
            item.setdefault("notification_id", _notification_id(dedupe_key) if dedupe_key else None)
            item.setdefault("read", False)
            item.setdefault("read_at", None)
            notification_id = str(item.get("notification_id") or dedupe_key)
            previous = events_by_id.get(notification_id)
            if previous is None or occurred_at > previous[0]:
                events_by_id[notification_id] = (occurred_at, item)
    events = sorted(events_by_id.values(), key=lambda item: item[0], reverse=True)
    total_count = len(events)
    unread_count = sum(not bool(payload.get("read")) for _occurred_at, payload in events)
    filtered_events = [item for item in events if not unread_only or not bool(item[1].get("read"))]
    items = [payload for _occurred_at, payload in filtered_events[:max(1, min(limit, 100))]]
    return {
        "items": items,
        "count": len(items),
        "total_count": total_count,
        "unread_count": unread_count,
        "critical_count": sum(str(payload.get("severity", "")).upper() == "CRITICAL" for payload in items),
        "latest_at": events[0][0].isoformat() if events else None,
    }


def unread_operating_notifications(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int | None = None,
    as_of: datetime | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    return list_operating_notifications(
        db,
        user_id=user_id,
        portfolio_id=portfolio_id,
        as_of=as_of,
        limit=limit,
        unread_only=True,
    )


def mark_operating_notification_read(
    db: Session,
    *,
    user_id: int,
    notification_id: str,
    portfolio_id: int | None = None,
    read_at: datetime | None = None,
) -> dict[str, Any]:
    moment = _aware_utc(read_at or datetime.now(UTC)).isoformat()
    query = select(DailyOperationalRun).where(DailyOperationalRun.user_id == user_id)
    if portfolio_id is not None:
        query = query.where(DailyOperationalRun.portfolio_id == portfolio_id)
    rows = db.execute(query.order_by(DailyOperationalRun.trade_date.desc(), DailyOperationalRun.id.desc())).scalars().all()
    durable_query = select(OperatingNotification).where(
        OperatingNotification.user_id == user_id,
        OperatingNotification.notification_id == notification_id,
    )
    if portfolio_id is not None:
        durable_query = durable_query.where(OperatingNotification.portfolio_id == portfolio_id)
    durable = db.execute(durable_query).scalar_one_or_none()
    if durable is not None:
        durable.read = True
        durable.read_at = _naive_utc(read_at or datetime.now(UTC))
        payload = dict(durable.payload_json or {})
        payload["read"] = True
        payload["read_at"] = _aware_utc(durable.read_at).isoformat() if durable.read_at else moment
        durable.payload_json = payload
        db.commit()
        return payload
    for row in rows:
        state = dict(row.notification_state_json or {})
        events = list(state.get("events") or [])
        changed = False
        matched: dict[str, Any] | None = None
        for payload in events:
            if not isinstance(payload, dict) or not _event_payload_matches(payload, notification_id):
                continue
            payload["read"] = True
            payload["read_at"] = moment
            matched = payload
            changed = True
            break
        if changed and matched is not None:
            state["events"] = events
            row.notification_state_json = state
            flag_modified(row, "notification_state_json")
            db.commit()
            return dict(matched)
    raise ValueError("notification_not_found")


list_notifications = list_operating_notifications
mark_notification_read = mark_operating_notification_read


__all__ = [
    "OperatingNotificationEvent",
    "collect_material_events",
    "dispatch_material_events",
    "dispatch_operating_event",
    "list_operating_notifications",
    "list_notifications",
    "mark_operating_notification_read",
    "mark_notification_read",
    "unread_operating_notifications",
]
