"""Offline historical research namespace for Phase I.

The research package is deliberately separate from live decision, candidate,
portfolio, and memory services.  It only reads production facts and writes
research-owned records.
"""

from .config import (
    BACKTEST_ENGINE_VERSION,
    CALIBRATION_ENGINE_VERSION,
    METRICS_ENGINE_VERSION,
    REPLAY_MODES,
    RESEARCH_SCOPES,
)
from .models import BacktestMetricSlice, BacktestRun, CalibrationReport

__all__ = [
    "BACKTEST_ENGINE_VERSION",
    "CALIBRATION_ENGINE_VERSION",
    "METRICS_ENGINE_VERSION",
    "REPLAY_MODES",
    "RESEARCH_SCOPES",
    "BacktestRun",
    "BacktestMetricSlice",
    "CalibrationReport",
]
