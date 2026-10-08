"""Conservative, structure-based risk/reward calculations."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .factors import calculate_atr, feature_snapshot, normalize_bars


def calculate_structure_risk_reward(
    bars: Iterable[Any],
    *,
    price: float | None = None,
    lookback: int = 60,
) -> dict[str, Any]:
    rows = normalize_bars(bars)
    features = feature_snapshot(rows, price=price)
    current = features.get("price")
    if current is None or current <= 0:
        return {
            "risk_reward_ratio": None,
            "support": None,
            "resistance": None,
            "risk": None,
            "reward": None,
            "atr14": features.get("atr14"),
            "available": False,
            "reason_codes": ["PRICE_ANOMALY"],
        }

    recent = rows[-lookback:]
    support_candidates: list[dict[str, Any]] = []
    for value in (features.get("ma20"),):
        if value is not None and 0 < value < current:
            support_candidates.append({"value": float(value), "source": "MA20"})

    # A support level needs confirmation on both sides.  Using the nearest raw
    # low makes an ordinary intraday wick look like a structural floor and can
    # materially overstate the calculated R/R.
    swing_window = 2
    for index in range(swing_window, len(recent) - swing_window):
        low = recent[index].get("low")
        if low is None or not 0 < float(low) < current:
            continue
        neighbours = [
            float(recent[offset]["low"])
            for offset in range(index - swing_window, index + swing_window + 1)
            if offset != index and recent[offset].get("low") is not None
        ]
        if len(neighbours) == swing_window * 2 and float(low) <= min(neighbours):
            support_candidates.append({"value": float(low), "source": "CONFIRMED_SWING_LOW"})
    supports = max((item["value"] for item in support_candidates), default=None)

    resistance_candidates: list[float] = []
    highs = [float(row["high"]) for row in rows[-120:] if row.get("high") is not None and row["high"] > current]
    if highs:
        resistance_candidates.append(min(highs))
    resistance = min(resistance_candidates) if resistance_candidates else None
    risk = current - supports if supports is not None else None
    reward = resistance - current if resistance is not None else None
    ratio = reward / risk if risk is not None and risk > 0 and reward is not None and reward > 0 else None
    reason_codes: list[str] = []
    if supports is None:
        reason_codes.append("SUPPORT_UNAVAILABLE")
    if resistance is None:
        reason_codes.append("RESISTANCE_UNAVAILABLE")
    if ratio is not None and ratio < 1.3:
        reason_codes.append("RISK_REWARD_LOW")
    return {
        "risk_reward_ratio": ratio,
        "support": supports,
        "resistance": resistance,
        "risk": risk,
        "reward": reward,
        "atr14": calculate_atr(rows, 14),
        "available": ratio is not None,
        "breakout_without_resistance": resistance is None,
        "reason_codes": reason_codes,
        "structure": {
            "support_candidates": support_candidates,
            "resistance_candidates": resistance_candidates,
            "lookback": lookback,
            "support_selection": (
                "CONFIRMED_SWING_LOW_OR_MA20" if support_candidates else "UNAVAILABLE"
            ),
        },
    }


def risk_reward_ratio(bars: Iterable[Any], *, price: float | None = None, lookback: int = 60) -> float | None:
    return calculate_structure_risk_reward(bars, price=price, lookback=lookback).get("risk_reward_ratio")


__all__ = ["calculate_structure_risk_reward", "risk_reward_ratio"]
