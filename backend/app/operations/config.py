"""Frozen Phase H operating timeline and runtime settings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, time

from ..config import settings


@dataclass(frozen=True, slots=True)
class OperatingCheckpoint:
    key: str
    at: time
    label: str
    kind: str
    mode: str | None = None
    catch_up_minutes: int | None = None


WORKFLOW_VERSION = "daily-operations-v1"
ANALYSIS_CLAIM_LEASE = timedelta(minutes=settings.ANALYSIS_CLAIM_LEASE_MINUTES)
REVIEW_CLAIM_LEASE = timedelta(minutes=settings.REVIEW_CLAIM_LEASE_MINUTES)
NOTIFICATION_DISPATCH_LEASE = timedelta(minutes=settings.NOTIFICATION_DISPATCH_LEASE_MINUTES)
SNAPSHOT_HOOK_LOOKBACK = timedelta(minutes=15)

CHECKPOINTS: tuple[OperatingCheckpoint, ...] = (
    OperatingCheckpoint("maintenance", time(8, 45), "Data Maintenance", "maintenance", catch_up_minutes=30),
    OperatingCheckpoint("pre_market", time(9, 20), "Pre-market Snapshot", "snapshot", catch_up_minutes=10),
    OperatingCheckpoint("auction", time(9, 25), "Auction Observation", "observation", catch_up_minutes=5),
    OperatingCheckpoint("monitor_start", time(9, 30), "Realtime Monitor Start", "monitor", catch_up_minutes=210),
    OperatingCheckpoint("09:35", time(9, 35), "Standard Analysis", "analysis", "standard", settings.CHECKPOINT_CATCHUP_MINUTES),
    OperatingCheckpoint("10:30", time(10, 30), "Fast Analysis", "analysis", "fast", settings.CHECKPOINT_CATCHUP_MINUTES),
    OperatingCheckpoint("morning_snapshot", time(11, 30), "Morning Snapshot", "snapshot", catch_up_minutes=15),
    OperatingCheckpoint("13:05", time(13, 5), "Fast Analysis", "analysis", "fast", settings.CHECKPOINT_CATCHUP_MINUTES),
    OperatingCheckpoint("14:30", time(14, 30), "Standard Analysis", "analysis", "standard", settings.CHECKPOINT_CATCHUP_MINUTES),
    OperatingCheckpoint("late_caution", time(14, 55), "Late-session Candidate Caution", "rule", catch_up_minutes=5),
    OperatingCheckpoint("market_close", time(15, 0), "Monitor Stop / Close Snapshot", "monitor", catch_up_minutes=10),
    OperatingCheckpoint("15:10", time(15, 10), "Deep Analysis", "analysis", "deep", settings.CHECKPOINT_CATCHUP_MINUTES),
    OperatingCheckpoint("daily_review", time(15, 30), "Memory Maintenance + Daily Review", "review", catch_up_minutes=None),
    OperatingCheckpoint("critical_event_hook", time(20, 30), "Critical Event Hook", "event_hook", catch_up_minutes=60),
)

ANALYSIS_CHECKPOINTS = tuple(item for item in CHECKPOINTS if item.kind == "analysis")
CHECKPOINT_BY_KEY = {item.key: item for item in CHECKPOINTS}

FRESHNESS_LIMITS_SECONDS = {
    "market": max(180, settings.MARKET_SCORE_INTERVAL_MINUTES * 3 * 60),
    "portfolio": 36 * 60 * 60,
    "candidate": 30 * 60,
    "scheduler": max(settings.SCHEDULER_INTERVAL_SECONDS * 3, 180),
}

NOTIFICATION_COOLDOWNS_MINUTES = {
    "market_regime": settings.NOTIFICATION_DEFAULT_COOLDOWN_MINUTES,
    "candidate_stage": settings.NOTIFICATION_DEFAULT_COOLDOWN_MINUTES,
    "trigger_confirmed": settings.NOTIFICATION_DEFAULT_COOLDOWN_MINUTES,
    "trigger_resolution": settings.NOTIFICATION_DEFAULT_COOLDOWN_MINUTES,
    "data_health": max(settings.NOTIFICATION_DEFAULT_COOLDOWN_MINUTES, 60),
}


__all__ = [
    "ANALYSIS_CHECKPOINTS",
    "ANALYSIS_CLAIM_LEASE",
    "CHECKPOINTS",
    "CHECKPOINT_BY_KEY",
    "FRESHNESS_LIMITS_SECONDS",
    "NOTIFICATION_DISPATCH_LEASE",
    "NOTIFICATION_COOLDOWNS_MINUTES",
    "OperatingCheckpoint",
    "REVIEW_CLAIM_LEASE",
    "SNAPSHOT_HOOK_LOOKBACK",
    "WORKFLOW_VERSION",
]
