"""Moving-average cost shared by account facts and performance replay."""
from __future__ import annotations

import math
from typing import Any


def purchase_amount(entry: Any) -> float | None:
    """A missing cost component must not silently become a zero-cost purchase."""

    values = (entry.fees, entry.taxes)
    if any(value is None for value in values):
        return None
    try:
        amount = (float(entry.net_amount) if entry.net_amount is not None else
                  (float(entry.gross_amount) if entry.gross_amount is not None else
                   float(entry.quantity) * float(entry.price)) + float(entry.fees) + float(entry.taxes))
        return amount if math.isfinite(amount) and amount >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def average_cost_after_buy(
    old_quantity: float | None,
    old_cost: float | None,
    quantity: float,
    amount: float | None,
) -> float | None:
    if old_quantity is None or old_quantity < 0 or amount is None:
        return None
    if old_quantity > 0 and (old_cost is None or old_cost < 0):
        return None
    total_quantity = old_quantity + quantity
    return ((old_quantity * (old_cost or 0.0) + amount) / total_quantity
            if total_quantity > 0 else None)
