"""Redact and normalize workflow payloads before they are persisted."""
from __future__ import annotations

from typing import Any

from ..system.logging import SECRET_FIELD_NAMES, redact_object, redact_text
from .hashing import canonical_json, sha256_content

_SECRET_KEYS = {name.lower() for name in SECRET_FIELD_NAMES}


def _key_is_secret(key: str) -> bool:
    lowered = str(key).lower().replace("-", "_")
    if lowered in _SECRET_KEYS:
        return True
    parts = {part for part in lowered.split("_") if part}
    return bool(parts & _SECRET_KEYS)


def _redact_secret_keys(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _key_is_secret(name):
                redacted[name] = "[REDACTED]"
            else:
                redacted[name] = _redact_secret_keys(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_secret_keys(item) for item in value]
    return value


def redact_payload(value: Any) -> Any:
    """Run existing log redaction, then blank secret-named object fields."""

    return _redact_secret_keys(redact_object(value))


def prepare_artifact_content(value: Any) -> tuple[dict | list | None, str | None, str, int, bool]:
    """Return (content_json, content_text, sha256, size, redacted).

    The hash is computed on the redacted stored form so secrets never enter the
    audit digest.
    """

    redacted = redact_payload(value)
    if isinstance(redacted, (dict, list)):
        encoded = canonical_json(redacted)
        digest = sha256_content(encoded)
        return redacted, None, digest, len(encoded.encode("utf-8")), True
    text = redact_text("" if redacted is None else str(redacted))
    digest = sha256_content(text)
    return None, text, digest, len(text.encode("utf-8")), True
