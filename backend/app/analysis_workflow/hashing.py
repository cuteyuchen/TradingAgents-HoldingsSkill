"""Stable JSON canonicalization and SHA-256 helpers for audit artifacts."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return _json_safe(value.value)
    return value


def canonical_json(value: Any) -> str:
    """Return a stable JSON document for hashing and artifact storage."""

    return json.dumps(_json_safe(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def sha256_content(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
