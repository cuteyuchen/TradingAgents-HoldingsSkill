"""Deterministic Entry Score, separate from long-horizon opportunity quality."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .config import CandidateConfig, DEFAULT_CONFIG
from .factors import combine_components, component, feature_snapshot, normalize_bars
from .risk_reward import calculate_structure_risk_reward


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def calculate_entry_score(
    bars: Iterable[Any],
    *,
    price: float | None = None,
    quote: Mapping[str, Any] | None = None,
    risk_reward: Mapping[str, Any] | None = None,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    rows = normalize_bars(bars)
    quote = dict(quote or {})
    features = feature_snapshot(rows, price=price or _number(quote.get("price")))
    current = features.get("price")
    rr = dict(risk_reward or calculate_structure_risk_reward(rows, price=current))
    ma20, ma60 = features.get("ma20"), features.get("ma60")
    atr = features.get("atr14")

    trend_score = None
    if current is not None and ma20 is not None:
        checks = [current >= ma20]
        if ma60 is not None:
            checks.append(ma20 >= ma60)
        trend_score = 100.0 * sum(checks) / len(checks)

    extension_score = None
    extension_atr = None
    if current is not None and ma20 is not None and atr is not None and atr > 0:
        extension_atr = (current - ma20) / atr
        extension_score = max(0.0, min(100.0, 100.0 - max(0.0, extension_atr - 0.5) * 35.0))

    volume_score = None
    relative_volume = features.get("relative_volume20")
    confirmation = features.get("price_volume_confirmation")
    if relative_volume is not None or confirmation is not None:
        parts = []
        if relative_volume is not None:
            parts.append(max(0.0, min(100.0, 50.0 + (relative_volume - 1.0) * 40.0)))
        if confirmation is not None:
            parts.append(float(confirmation))
        volume_score = sum(parts) / len(parts)

    rr_score = None
    if rr.get("risk_reward_ratio") is not None:
        rr_score = max(0.0, min(100.0, float(rr["risk_reward_ratio"]) / 2.0 * 100.0))

    quote_score = None
    quality = str(quote.get("quality_status") or "").upper()
    if quality in {"VALID", "DEGRADED"}:
        quote_score = 100.0 if quality == "VALID" else 70.0
        if features.get("median_amount20") is not None:
            quote_score = min(100.0, quote_score + 0.0)

    components = {
        "trend_alignment": component(trend_score, raw={"price": current, "ma20": ma20, "ma60": ma60}, source="daily_bar_cache"),
        "extension": component(extension_score, raw={"extension_atr": extension_atr, "atr14": atr}, source="daily_bar_cache"),
        "volume_confirmation": component(volume_score, raw={"relative_volume20": relative_volume, "confirmation": confirmation}, source="daily_bar_cache"),
        "risk_reward_structure": component(rr_score, raw=rr, source="daily_bar_cache", reason="R/R unavailable when support or resistance is missing"),
        "quote_liquidity": component(quote_score, raw={"quality_status": quality, "amount": features.get("median_amount20")}, source=quote.get("provider") or "daily_bar_cache"),
    }
    combined = combine_components(components, config.entry_factor_weights)
    return {
        **combined,
        "components": components,
        "risk_reward_ratio": rr.get("risk_reward_ratio"),
        "extension_atr": extension_atr,
        "quality_status": quality or "MISSING",
    }


entry_score = calculate_entry_score


__all__ = ["calculate_entry_score", "entry_score"]
