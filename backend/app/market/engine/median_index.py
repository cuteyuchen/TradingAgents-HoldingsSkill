"""Compatibility facade for the deterministic All-A Median Index functions."""
from .metrics import calculate_median_index, median_return, next_median_index

__all__ = ["median_return", "calculate_median_index", "next_median_index"]
