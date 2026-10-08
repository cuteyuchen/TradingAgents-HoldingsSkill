"""Small, dependency-free V3 Phase A decision contract.

This module is intentionally pure: it does not touch the database, network, or
model providers.  Runtime loading and deterministic result normalisation import
these values so the machine-readable Skill and backend cannot silently drift.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

CONTRACT_VERSION = "2.4.0"
DEFAULT_PORTFOLIO_ACTION = "no_action"
CANDIDATE_MIN_COUNT = 0
CANDIDATE_MAX_COUNT = 3
CANDIDATE_FORCE_OUTPUT = False
NEW_CANDIDATE_EXCLUDE_CURRENT_HOLDINGS = True
STOCK_HARD_CAP_RATIO = 0.20
SECTOR_THEME_ETF_HARD_CAP_RATIO = 0.30

HORIZONS = {
    "short": {"min_trading_days": 1, "max_trading_days": 5},
    "swing": {"min_trading_days": 6, "max_trading_days": 20},
    "medium": {"min_trading_days": 21, "max_trading_days": 120},
}

CANONICAL_ANALYSIS_MODES = ("fast", "standard", "deep")
ANALYSIS_MODE_ALIASES = {"quick": "fast"}
SUPPORTED_ANALYSIS_MODES = ("quick",) + CANONICAL_ANALYSIS_MODES
ACTIONABLE_HOLDING_ACTIONS = frozenset({"add", "conditional_add", "reduce", "sell"})
ACTIONABLE_PORTFOLIO_RATINGS = frozenset({"add", "reduce", "sell", "rotate"})
ACTIONABLE_CANDIDATE_TYPES = frozenset({"new_position", "add", "buy"})


@dataclass(frozen=True)
class ActionSizing:
    quantity: float | None
    target_weight: float | None
    errors: tuple[str, ...] = ()


def parse_share_quantity(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str):
        match = re.fullmatch(r"\s*((?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?)\s*(?:股|份)?\s*", value)
        if match is None:
            return None
        value = match.group(1).replace(",", "")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 and number.is_integer() else None


def parse_target_weight(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    is_percent = False
    if isinstance(value, str):
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?|\.[0-9]+)\s*([%％])?\s*", value)
        if match is None:
            return None
        value = match.group(1)
        is_percent = match.group(2) is not None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if is_percent:
        number /= 100.0
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def normalize_action_sizing(row: dict[str, Any]) -> ActionSizing:
    errors: list[str] = []
    quantities: list[float] = []
    for field in ("quantity", "proposed_qty"):
        raw = row.get(field)
        if raw is None or isinstance(raw, str) and not raw.strip():
            continue
        quantity = parse_share_quantity(raw)
        if quantity is None or quantity <= 0:
            if "QUANTITY_INVALID" not in errors:
                errors.append("QUANTITY_INVALID")
        else:
            quantities.append(quantity)
    if len(quantities) == 2 and quantities[0] != quantities[1]:
        errors.append("QUANTITY_CONFLICT")
    raw_target = row.get("target_weight")
    target = parse_target_weight(raw_target)
    if raw_target is not None and not (isinstance(raw_target, str) and not raw_target.strip()) and target is None:
        errors.append("TARGET_WEIGHT_INVALID")
    return ActionSizing(
        quantity=quantities[0] if quantities and not any(reason.startswith("QUANTITY_") for reason in errors) else None,
        target_weight=target,
        errors=tuple(errors),
    )


def holding_decision_status(row: Any) -> str:
    if not isinstance(row, dict):
        return "INCOMPLETE"
    if str(row.get("portfolio_gate") or "").upper() == "BLOCKED":
        return "DATA_GAP"
    persisted_status = str(row.get("decision_status") or "").upper()
    if persisted_status in {"INCOMPLETE", "DATA_GAP"}:
        return persisted_status
    action = str(row.get("action") or row.get("recommended_action") or "").strip().lower()
    if action in {"add", "buy", "reduce", "sell"}:
        return "ACTION"
    if action in {"watch", "watch_only", "wait", "conditional_add", "conditional_buy"}:
        return "WAITING"
    if action in {"hold", "hold_only", "no_action"}:
        return "NO_ACTION"
    return "INCOMPLETE"


def resolve_decision_status(result: Any) -> str:
    if not isinstance(result, dict) or not result:
        return "INCOMPLETE"
    gate = result.get("decision_gate") if isinstance(result.get("decision_gate"), dict) else {}
    quality = result.get("quality_gate") if isinstance(result.get("quality_gate"), dict) else {}
    risk = result.get("risk_revision") if isinstance(result.get("risk_revision"), dict) else {}
    if (
        str(gate.get("status") or "").upper() == "BLOCKED"
        or str(quality.get("status") or "").lower() == "blocked"
        or str(quality.get("grade") or result.get("data_quality_grade") or "").upper() in {"D", "F"}
        or str(risk.get("decision") or "").lower() == "reject"
    ):
        return "DATA_GAP"
    holdings = result.get("holdings") if "holdings" in result else result.get("today_actions")
    states = [holding_decision_status(row) for row in holdings] if isinstance(holdings, list) else []
    candidates = result.get("candidates") if "candidates" in result else result.get("buy_candidates")
    for row in candidates if isinstance(candidates, list) else []:
        if not isinstance(row, dict):
            states.append("INCOMPLETE")
            continue
        if row.get("buyable") is False or row.get("actionable") is False or str(row.get("portfolio_gate") or "").upper() == "BLOCKED":
            continue
        candidate_type = str(row.get("candidate_type") or row.get("action") or row.get("recommended_action") or "").lower()
        if candidate_type in ACTIONABLE_CANDIDATE_TYPES:
            states.append("ACTION")
    for state in ("DATA_GAP", "INCOMPLETE", "ACTION", "WAITING"):
        if state in states:
            return state
    return "NO_ACTION" if states and all(state == "NO_ACTION" for state in states) else "INCOMPLETE"


def apply_decision_status(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("holdings", "today_actions"):
        for row in result.get(key) or []:
            if isinstance(row, dict):
                row["decision_status"] = holding_decision_status(row)
    result["decision_status"] = resolve_decision_status(result)
    return result


def canonicalize_analysis_mode(value: Any, *, default: str = "deep") -> str:
    """Return a canonical analysis mode while keeping legacy ``quick`` readable."""
    mode = str(value or default).strip().lower()
    mode = ANALYSIS_MODE_ALIASES.get(mode, mode)
    if mode not in CANONICAL_ANALYSIS_MODES:
        raise ValueError(f"Unsupported analysis mode: {value}")
    return mode


def decision_contract_payload() -> dict[str, Any]:
    """Return the JSON-shaped contract used by ``runtime.json``."""
    return {
        "version": CONTRACT_VERSION,
        "default_portfolio_action": DEFAULT_PORTFOLIO_ACTION,
        "candidates": {
            "min": CANDIDATE_MIN_COUNT,
            "max": CANDIDATE_MAX_COUNT,
            "force_output": CANDIDATE_FORCE_OUTPUT,
            "exclude_current_holdings": NEW_CANDIDATE_EXCLUDE_CURRENT_HOLDINGS,
        },
        "hard_caps": {
            "stock": STOCK_HARD_CAP_RATIO,
            "sector_theme_etf": SECTOR_THEME_ETF_HARD_CAP_RATIO,
            "deterministic_enforcement": True,
        },
        "horizons": HORIZONS,
        "analysis_modes": {
            "canonical": list(CANONICAL_ANALYSIS_MODES),
            "aliases": dict(ANALYSIS_MODE_ALIASES),
        },
    }


def validate_decision_contract(payload: Any) -> dict[str, Any]:
    """Validate and return a runtime decision contract.

    The checks are deliberately structural and narrow.  They protect the
    contract boundary without pretending that instrument classification or hard
    cap enforcement exists in Phase A.
    """
    if not isinstance(payload, dict):
        raise ValueError("decision_contract must be an object")
    expected = decision_contract_payload()
    if payload.get("version") != expected["version"]:
        raise ValueError("decision_contract version mismatch")
    if payload.get("default_portfolio_action") != expected["default_portfolio_action"]:
        raise ValueError("decision_contract default action mismatch")

    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        raise ValueError("decision_contract candidates must be an object")
    for key in ("min", "max", "force_output", "exclude_current_holdings"):
        if candidates.get(key) != expected["candidates"][key]:
            raise ValueError(f"decision_contract candidates.{key} mismatch")

    caps = payload.get("hard_caps")
    if not isinstance(caps, dict):
        raise ValueError("decision_contract hard_caps must be an object")
    for key in ("stock", "sector_theme_etf", "deterministic_enforcement"):
        if caps.get(key) != expected["hard_caps"][key]:
            raise ValueError(f"decision_contract hard_caps.{key} mismatch")

    if payload.get("horizons") != expected["horizons"]:
        raise ValueError("decision_contract horizons mismatch")
    modes = payload.get("analysis_modes")
    if not isinstance(modes, dict):
        raise ValueError("decision_contract analysis_modes must be an object")
    if modes.get("canonical") != expected["analysis_modes"]["canonical"]:
        raise ValueError("decision_contract canonical modes mismatch")
    if modes.get("aliases") != expected["analysis_modes"]["aliases"]:
        raise ValueError("decision_contract mode aliases mismatch")
    return payload


def should_normalize_no_action(
    *,
    quality_gate_status: Any,
    holdings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> bool:
    """Whether a successful analysis deterministically means no portfolio change."""
    if str(quality_gate_status or "").lower() == "blocked":
        return False
    return not has_actionable_portfolio_change(
        {"holdings": holdings, "candidates": candidates},
        include_final_rating=False,
    )


def has_actionable_portfolio_change(
    result: Any,
    *,
    include_final_rating: bool = True,
) -> bool:
    """Return whether a normalized result contains a real portfolio change.

    This intentionally treats a current Action Candidate as a change while
    excluding watch-only rows.  It is shared by result normalization and
    TriggerEvent resolution so their ACTION semantics cannot drift.
    """

    if not isinstance(result, dict):
        return False
    if include_final_rating and str(result.get("final_rating") or "").lower() in ACTIONABLE_PORTFOLIO_RATINGS:
        return True
    for row in result.get("holdings") or []:
        if isinstance(row, dict) and str(row.get("action") or "").lower() in ACTIONABLE_HOLDING_ACTIONS:
            return True
    for row in result.get("today_actions") or []:
        if isinstance(row, dict) and str(row.get("action") or row.get("type") or "").lower() in ACTIONABLE_HOLDING_ACTIONS:
            return True
    candidate_rows = result.get("candidates") if "candidates" in result else result.get("buy_candidates")
    for row in candidate_rows or []:
        if not isinstance(row, dict) or row.get("buyable") is False:
            continue
        candidate_type = str(row.get("candidate_type") or row.get("action") or row.get("type") or "").lower()
        if candidate_type in ACTIONABLE_CANDIDATE_TYPES:
            return True
    return False
