"""Compatibility facade for the single historical-percentile implementation."""
from .score import historical_percentile, normalize_percentile, percentile_rank, percentile_result

__all__ = ["historical_percentile", "normalize_percentile", "percentile_rank", "percentile_result"]
