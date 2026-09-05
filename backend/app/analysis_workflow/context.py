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
    if mode == "compressed":
        return _truncate(payload, list_limit=8, text_limit=400)
    if isinstance(payload, dict):
        keep: dict[str, Any] = {}
        for key in ("holdings", "quality_gate", "evidence_pack", "input", "codes", "market"):
            if key in payload:
                keep[key] = _truncate(payload[key], list_limit=3, text_limit=120)
        return keep or _truncate(payload, list_limit=2, text_limit=80)
    return _truncate(payload, list_limit=2, text_limit=80)
