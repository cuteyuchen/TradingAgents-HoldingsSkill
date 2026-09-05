"""Classify analysis node failures for retry vs fail-closed."""
from __future__ import annotations

import re
from typing import Any

from .constants import FailureClass

try:
    from ..services.model_client import ModelTimeoutError, StructuredOutputError
except Exception:  # pragma: no cover - import cycle guard
    ModelTimeoutError = tuple()  # type: ignore[misc, assignment]
    StructuredOutputError = tuple()  # type: ignore[misc, assignment]

_TRANSIENT_STATUS = re.compile(r"\b(502|503|504)\b")
_AUTH_STATUS = re.compile(r"\b(401|403)\b")
_CONTEXT = re.compile(
    r"context[_ ]?(?:length|window|overflow)|maximum context|too many tokens|prompt is too long",
    re.I,
)
_MODEL_MISSING = re.compile(r"model not found|invalid model|does not exist", re.I)
_CONFIG = re.compile(
    r"not_configured|invalid config|base url|api key|missing required|confirmed_snapshot_not_found",
    re.I,
)


class ResumeRejected(RuntimeError):
    code = "resume_input_hash_mismatch"

    def __init__(self, message: str, *, mismatches: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.mismatches = mismatches or {}


def _message(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def classify_failure(exc: BaseException) -> str:
    """Return a FailureClass for a node/model exception.

    Unknown errors default to non-retryable so bugs are not retried as traffic.
    """

    if isinstance(exc, ResumeRejected):
        return FailureClass.NON_RETRYABLE
    if ModelTimeoutError and isinstance(exc, ModelTimeoutError):
        return FailureClass.TRANSIENT
    if StructuredOutputError and isinstance(exc, StructuredOutputError):
        text = _message(exc)
        if _CONTEXT.search(text):
            return FailureClass.CONTEXT_OVERFLOW
        return FailureClass.STRUCTURED_OUTPUT

    name = type(exc).__name__.lower()
    text = _message(exc)
    lowered = text.lower()

    if "job_cancelled" in lowered:
        return FailureClass.NON_RETRYABLE
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)) or "timeout" in name or "connection" in name:
        return FailureClass.TRANSIENT
    if _CONTEXT.search(text):
        return FailureClass.CONTEXT_OVERFLOW
    if _AUTH_STATUS.search(text) or _MODEL_MISSING.search(text) or _CONFIG.search(text):
        return FailureClass.NON_RETRYABLE
    if _TRANSIENT_STATUS.search(text) or "timeout" in lowered or "connection" in lowered:
        return FailureClass.TRANSIENT
    if "structured" in lowered or "invalid json" in lowered or "truncated" in lowered:
        return FailureClass.STRUCTURED_OUTPUT
    return FailureClass.NON_RETRYABLE
