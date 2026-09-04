"""Deterministic Phase D trigger engine."""

from .engine import TriggerDetection, evaluate_holding_plan, evaluate_market_scores
from .service import apply_detection, expire_unmatched_detections

__all__ = [
    "TriggerDetection",
    "apply_detection",
    "evaluate_holding_plan",
    "evaluate_market_scores",
    "expire_unmatched_detections",
]
