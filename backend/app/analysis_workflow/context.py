"""Context variants for CONTEXT_OVERFLOW node retries."""
from __future__ import annotations

from typing import Any

from .constants import FailureClass


def next_context_mode(current: str) -> str | None:
    modes = FailureClass.CONTEXT_MODES
    try:
        index = modes.index(current)
    except ValueError:
        return "compressed"
    if index + 1 >= len(modes):
        return None
    return modes[index + 1]


def _trim_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _truncate(value: Any, list_limit: int, text_limit: int) -> Any:
    if isinstance(value, str):
        return _trim_text(value, text_limit)
    if isinstance(value, dict):
        return {str(key): _truncate(item, list_limit, text_limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        clipped = list(value)[:list_limit]
        return [_truncate(item, list_limit, text_limit) for item in clipped]
    return value


def compress_payload(payload: Any, mode: str = "full") -> Any:
    """Return a smaller copy of payload. Never mutates the original."""

    if mode == "full" or payload is None:
        return payload
    if isinstance(payload, dict) and "evidence_hash" in payload:
        # Identity, holdings, hard constraints and the actual opponent's claims
        # survive context reduction. Only ancillary frozen evidence is shortened.
        result = dict(payload)
        source = payload.get("input") or {}
        reduced = _truncate(source, list_limit=8 if mode == "compressed" else 3, text_limit=400 if mode == "compressed" else 120)
        for key in ("snapshot", "portfolio_context"):
            if key in source:
                reduced[key] = source[key]
        result["input"] = reduced
        return result
    if mode == "compressed":
        return _truncate(payload, list_limit=8, text_limit=400)
    if isinstance(payload, dict):
        keep: dict[str, Any] = {}
        for key in ("holdings", "quality_gate", "evidence_pack", "input", "codes", "market"):
            if key in payload:
                keep[key] = _truncate(payload[key], list_limit=3, text_limit=120)
        return keep or _truncate(payload, list_limit=2, text_limit=80)
    return _truncate(payload, list_limit=2, text_limit=80)
