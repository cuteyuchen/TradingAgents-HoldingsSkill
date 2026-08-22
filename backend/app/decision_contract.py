"""Small, dependency-free V3 Phase A decision contract.

This module is intentionally pure: it does not touch the database, network, or
model providers.  Runtime loading and deterministic result normalisation import
these values so the machine-readable Skill and backend cannot silently drift.
"""
from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "2.1.0"
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
            "deterministic_enforcement": False,
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
    if candidates:
        return False
    return all(str(row.get("action") or "watch").lower() in {"hold", "watch"} for row in holdings)
