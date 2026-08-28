"""Phase M deterministic PIT recompute engine.

The engine is research-only. It consumes persisted PIT facts, reuses the
production deterministic scoring cores, performs no network or LLM calls, and
never writes production decision state.
"""

from __future__ import annotations

from .capability import build_recompute_capability_manifest
from .context import HistoricalRecomputeContext
from .engine import DeterministicRecomputeResult, recompute_deterministic_scope

__all__ = [
    "HistoricalRecomputeContext",
    "DeterministicRecomputeResult",
    "build_recompute_capability_manifest",
    "recompute_deterministic_scope",
]
