"""Affordable whole-lot buys using the existing configured transaction-cost model."""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_FLOOR

from ..config import settings
from .ledger import transaction_cost_estimate


def affordable_buy_quantity(*, price: float, lot: int, gross_limit: float, cash: float) -> tuple[int, float | None]:
    config = (settings.PORTFOLIO_BROKER_COMMISSION_BPS, settings.PORTFOLIO_MINIMUM_COMMISSION)
    if any(value is None or not math.isfinite(value) or value < 0 for value in config):
        return 0, None
    if price <= 0 or lot <= 0 or gross_limit <= 0 or cash <= 0:
        return 0, 0.0
    price_decimal = Decimal(str(price))
    cash_decimal = Decimal(str(cash))
    upper = int((Decimal(str(min(gross_limit, cash))) / (price_decimal * lot)).to_integral_value(rounding=ROUND_FLOOR))

    def fee_for(quantity: int) -> float:
        return transaction_cost_estimate(
            side="BUY", gross_amount=float(price_decimal * quantity),
            commission_bps=config[0], minimum_commission=config[1], sell_tax_bps=None,
        )

    # Binary search avoids one iteration per lot when the minimum fee is large.
    lower = 0
    while lower < upper:
        middle = (lower + upper + 1) // 2
        quantity = middle * lot
        fee = fee_for(quantity)
        if price_decimal * quantity + Decimal(str(fee)) <= cash_decimal:
            lower = middle
        else:
            upper = middle - 1
    quantity = lower * lot
    return quantity, fee_for(quantity) if quantity else 0.0
