"""PnL normalization helpers.

`pnl` is always a decimal ratio, for example -0.2773 means -27.73%.
Broker/OCR amount-like values are preserved in `pnl_amount`.
"""

from __future__ import annotations

import json

PNL_TOLERANCE = 0.02


def safe_float(value: float | int | str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calc_pnl_from_price(price: float | None, cost: float | None) -> float | None:
    price_value = safe_float(price)
    cost_value = safe_float(cost)
    if price_value is None or cost_value is None or cost_value <= 0:
        return None
    return (price_value - cost_value) / cost_value


def pnl_correction_key(correction: dict) -> str:
    return "|".join(
        str(correction.get(k, ""))
        for k in ("code", "original_pnl", "corrected_pnl", "price", "cost", "reason")
    )


def append_unique_corrections(evidence_pack: dict | None, corrections: list[dict]) -> dict | None:
    if not corrections:
        return evidence_pack

    if isinstance(evidence_pack, str):
        try:
            loaded = json.loads(evidence_pack)
        except json.JSONDecodeError:
            loaded = {}
        pack = loaded if isinstance(loaded, dict) else {}
    else:
        pack = dict(evidence_pack or {})
    existing = pack.get("pnl_corrections")
    if isinstance(existing, list):
        merged = [item for item in existing if isinstance(item, dict)]
    elif isinstance(existing, dict):
        merged = [existing]
    else:
        merged = []

    seen = {pnl_correction_key(item) for item in merged}
    for correction in corrections:
        key = pnl_correction_key(correction)
        if key not in seen:
            merged.append(correction)
            seen.add(key)

    pack["pnl_corrections"] = merged
    return pack


def normalize_pnl(
    code: str,
    name: str | None,
    pnl: float | None,
    price: float | None,
    cost: float | None,
    pnl_amount: float | None = None,
) -> tuple[float | None, float | None, dict | None]:
    """Return (normalized pnl ratio, pnl amount, correction metadata)."""
    computed = calc_pnl_from_price(price, cost)
    uploaded = safe_float(pnl)
    amount = safe_float(pnl_amount)
    if computed is None:
        return uploaded, amount, None

    base = {
        "code": code,
        "name": name,
        "price": price,
        "cost": cost,
    }
    if uploaded is None:
        return computed, amount, {
            **base,
            "original_pnl": None,
            "corrected_pnl": computed,
            "pnl_amount": amount,
            "reason": "missing_pnl_computed_from_price_cost",
        }

    if abs(uploaded - computed) <= PNL_TOLERANCE:
        return uploaded, amount, None

    if abs(uploaded) > 1 and abs((uploaded / 100) - computed) <= PNL_TOLERANCE:
        corrected = uploaded / 100
        return corrected, amount, {
            **base,
            "original_pnl": uploaded,
            "corrected_pnl": corrected,
            "pnl_amount": amount,
            "reason": "uploaded_percent_unit_converted",
        }

    if abs(uploaded) > 1:
        corrected_amount = amount if amount is not None else uploaded
        return computed, corrected_amount, {
            **base,
            "original_pnl": uploaded,
            "corrected_pnl": computed,
            "pnl_amount": corrected_amount,
            "reason": "extreme_pnl_recomputed_from_price_cost",
        }

    return uploaded, amount, None
