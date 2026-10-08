"""Stable stage-aware Candidate ranking."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


STAGE_ORDER = {"ACTION": 0, "READY": 1, "WATCHLIST": 2, "REJECTED": 3}


def rank_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in candidates]
    rows.sort(
        key=lambda row: (
            STAGE_ORDER.get(str(row.get("stage") or "REJECTED").upper(), 9),
            -float(row.get("decision_edge") if row.get("decision_edge") is not None else -1e9),
            -float(row.get("action_score") if row.get("action_score") is not None else -1e9),
            -float(row.get("confidence") if row.get("confidence") is not None else -1e9),
            -float(row.get("opportunity_score") if row.get("opportunity_score") is not None else -1e9),
            -float(row.get("liquidity_score") if row.get("liquidity_score") is not None else -1e9),
            str(row.get("code") or ""),
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def take_stage_limits(candidates: Iterable[Mapping[str, Any]], *, watchlist_max: int, ready_max: int, action_max: int) -> dict[str, list[dict[str, Any]]]:
    ranked = rank_candidates(candidates)
    return {
        "watchlist": [row for row in ranked if str(row.get("stage")).upper() == "WATCHLIST"][:watchlist_max],
        "ready": [row for row in ranked if str(row.get("stage")).upper() == "READY"][:ready_max],
        "action": [row for row in ranked if str(row.get("stage")).upper() == "ACTION"][:action_max],
    }


__all__ = ["rank_candidates", "take_stage_limits"]
