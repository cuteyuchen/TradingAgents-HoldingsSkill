"""Immutable historical recompute context."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Mapping


LEGACY_PRE_GOVERNANCE = "LEGACY_PRE_GOVERNANCE"


@dataclass(frozen=True)
class HistoricalRecomputeContext:
    scope: str
    trade_date: date
    start_date: date
    end_date: date
    replay_mode: str
    user_id: int | None = None
    portfolio_id: int | None = None
    parameter_set_version_id: int | None = None
    parameter_set_version: str = LEGACY_PRE_GOVERNANCE
    config_hash: str | None = None
    parameter_snapshot: dict[str, Any] = field(default_factory=dict)
    universe_version: str = "pit-universe-v1"
    source_manifest_hash: str | None = None
    input_quality: str = "PARTIAL"
    capability: str = "PARTIAL_PIT_RECOMPUTE"
    as_of: datetime | None = None
    decision_feature_cutoff: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("trade_date", "start_date", "end_date"):
            value = payload[key]
            payload[key] = value.isoformat() if hasattr(value, "isoformat") else value
        for key in ("as_of", "decision_feature_cutoff"):
            value = payload[key]
            payload[key] = value.isoformat() if hasattr(value, "isoformat") else value
        return payload


def build_context(
    *,
    scope: str,
    trade_date: date,
    start_date: date,
    end_date: date,
    replay_mode: str = "DETERMINISTIC_RECOMPUTE",
    user_id: int | None = None,
    portfolio_id: int | None = None,
    parameter_set_version_id: int | None = None,
    parameter_set_version: str | None = None,
    config_hash: str | None = None,
    parameter_snapshot: Mapping[str, Any] | None = None,
    universe_version: str = "pit-universe-v1",
    as_of: datetime | None = None,
    decision_feature_cutoff: datetime | None = None,
) -> HistoricalRecomputeContext:
    return HistoricalRecomputeContext(
        scope=str(scope).upper(),
        trade_date=trade_date,
        start_date=start_date,
        end_date=end_date,
        replay_mode=str(replay_mode).upper(),
        user_id=user_id,
        portfolio_id=portfolio_id,
        parameter_set_version_id=parameter_set_version_id,
        parameter_set_version=parameter_set_version or LEGACY_PRE_GOVERNANCE,
        config_hash=config_hash,
        parameter_snapshot=dict(parameter_snapshot or {}),
        universe_version=universe_version,
        as_of=as_of,
        decision_feature_cutoff=decision_feature_cutoff,
    )


__all__ = [
    "HistoricalRecomputeContext",
    "LEGACY_PRE_GOVERNANCE",
    "build_context",
]
