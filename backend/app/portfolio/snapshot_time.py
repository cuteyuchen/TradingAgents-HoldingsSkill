"""Do not reinterpret legacy screenshot confirmation times as observation facts."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def observation_time_status(snapshot: Any) -> str:
    raw = snapshot.raw_json if isinstance(snapshot.raw_json, dict) else {}
    if raw.get("observed_at") and raw.get("observation_time_status") == "USER_CONFIRMED":
        try:
            observed = datetime.fromisoformat(str(raw["observed_at"]))
            snapshot_time = snapshot.snapshot_time
            if snapshot_time.tzinfo is not None:
                snapshot_time = snapshot_time.astimezone(UTC).replace(tzinfo=None)
            if observed.utcoffset() is not None and observed.astimezone(UTC).replace(tzinfo=None) == snapshot_time:
                return "USER_CONFIRMED"
        except (TypeError, ValueError):
            pass
    # Deleting an upload sets its FK to NULL; it cannot establish a fact time.
    if str(snapshot.source).lower() == "screenshot":
        return "UNVERIFIED"
    # Non-screenshot anchors explicitly supply their event time.
    return "EXPLICIT"
