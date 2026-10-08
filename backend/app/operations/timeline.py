"""Pure Asia/Shanghai workflow-state and timeline derivation."""
from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum
from typing import Any

from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from .config import CHECKPOINTS


class WorkflowState(StrEnum):
    PRE_MARKET_MAINTENANCE = "PRE_MARKET_MAINTENANCE"
    PRE_MARKET_READY = "PRE_MARKET_READY"
    AUCTION = "AUCTION"
    MORNING_SESSION = "MORNING_SESSION"
    LUNCH_BREAK = "LUNCH_BREAK"
    AFTERNOON_SESSION = "AFTERNOON_SESSION"
    LATE_SESSION = "LATE_SESSION"
    MARKET_CLOSED = "MARKET_CLOSED"
    POST_CLOSE_ANALYSIS = "POST_CLOSE_ANALYSIS"
    DAILY_REVIEW = "DAILY_REVIEW"
    DAY_COMPLETE = "DAY_COMPLETE"
    NON_TRADING_DAY = "NON_TRADING_DAY"


def china_time(value: datetime | None = None) -> datetime:
    moment = value or datetime.now(CHINA_TZ)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=CHINA_TZ)
    return moment.astimezone(CHINA_TZ)


def derive_workflow_state(
    db: Any,
    *,
    as_of: datetime | None = None,
    review_complete: bool = False,
    review_stale: bool = False,
) -> WorkflowState:
    local = china_time(as_of)
    if not TradingCalendarService(db).is_trading_day(local.date()):
        return WorkflowState.NON_TRADING_DAY
    current = local.time()
    if current < time(9, 20):
        return WorkflowState.PRE_MARKET_MAINTENANCE
    if current < time(9, 25):
        return WorkflowState.PRE_MARKET_READY
    if current < time(9, 30):
        return WorkflowState.AUCTION
    if current < time(11, 30):
        return WorkflowState.MORNING_SESSION
    if current < time(13, 0):
        return WorkflowState.LUNCH_BREAK
    if current < time(14, 55):
        return WorkflowState.AFTERNOON_SESSION
    if current < time(15, 0):
        return WorkflowState.LATE_SESSION
    if current < time(15, 10):
        return WorkflowState.MARKET_CLOSED
    if current < time(15, 30):
        return WorkflowState.POST_CLOSE_ANALYSIS
    if current < time(20, 30):
        return WorkflowState.DAY_COMPLETE if review_complete and not review_stale else WorkflowState.DAILY_REVIEW
    return WorkflowState.DAY_COMPLETE if review_complete and not review_stale else WorkflowState.DAILY_REVIEW


def checkpoint_moment(local: datetime, checkpoint_time: time) -> datetime:
    return local.replace(
        hour=checkpoint_time.hour,
        minute=checkpoint_time.minute,
        second=0,
        microsecond=0,
    )


def base_timeline(as_of: datetime | None = None) -> list[dict[str, Any]]:
    local = china_time(as_of)
    return [
        {
            "key": item.key,
            "time": item.at.strftime("%H:%M"),
            "label": item.label,
            "kind": item.kind,
            "mode": item.mode,
            "scheduled_at": checkpoint_moment(local, item.at).isoformat(),
            "is_current": (
                checkpoint_moment(local, item.at) <= local
                and (
                    item is CHECKPOINTS[-1]
                    or local < checkpoint_moment(local, CHECKPOINTS[CHECKPOINTS.index(item) + 1].at)
                )
            ),
        }
        for item in CHECKPOINTS
    ]


__all__ = ["WorkflowState", "base_timeline", "checkpoint_moment", "china_time", "derive_workflow_state"]
