"""Structured correlation and secret redaction for self-hosted operations."""

from __future__ import annotations

import contextvars
import logging
import re
import secrets
import threading
from collections import deque
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..config import settings

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("advisor_request_id", default="")
_correlation: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "advisor_correlation", default={}
)
_MEMORY: deque[str] = deque(maxlen=5000)
_MEMORY_LOCK = threading.Lock()

SECRET_FIELD_NAMES = (
    "authorization",
    "access_token",
    "refresh_token",
    "api_key",
    "encrypted_api_key",
    "password",
    "cookie",
    "secret",
    "token",
    "webhook",
    "encrypted_webhook",
)
_QUOTED_KEY_PATTERN = re.compile(
    r"(?i)([\"']?(?:"
    + "|".join(re.escape(item) for item in SECRET_FIELD_NAMES)
    + r")[\"']?\s*[:=]\s*[\"'])([^\"']{4,})([\"'])"
)
_BARE_KEY_PATTERN = re.compile(
    r"(?i)([\"']?(?:"
    + "|".join(re.escape(item) for item in SECRET_FIELD_NAMES)
    + r")[\"']?\s*[:=]\s*)([A-Za-z0-9._~+/=-]{4,})(?=[,\s}\]]|$)"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]+=*")


def _known_secret_values() -> list[str]:
    values = [
        settings.ADVISOR_TOKEN,
        settings.APP_SECRET_KEY,
        settings.MARKET_IDENTITY_SYNC_TOKEN,
    ]
    return [value for value in values if value and len(value) >= 4]


def redact_text(value: str) -> str:
    redacted = _BEARER_PATTERN.sub(r"\1[REDACTED]", value)
    redacted = _QUOTED_KEY_PATTERN.sub(r"\1[REDACTED]\3", redacted)
    redacted = _BARE_KEY_PATTERN.sub(r"\1[REDACTED]", redacted)
    for secret in _known_secret_values():
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def redact_object(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): redact_object(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_object(item) for item in value]
    return value


class RedactingFormatter(logging.Formatter):
    """Format records with correlation fields and redact every rendered line."""

    _DEFAULT_FORMAT = (
        "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s "
        "analysis_job_id=%(analysis_job_id)s backtest_run_id=%(backtest_run_id)s "
        "parameter_set_version=%(parameter_set_version)s %(message)s"
    )

    def __init__(self, fmt: str | None = None) -> None:
        super().__init__(fmt or self._DEFAULT_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        correlation = correlation_fields()
        record.request_id = str(
            record.__dict__.get("request_id")
            or correlation.get("request_id")
            or get_request_id()
            or "-"
        )
        record.analysis_job_id = str(
            record.__dict__.get("analysis_job_id")
            or correlation.get("analysis_job_id")
            or "-"
        )
        record.backtest_run_id = str(
            record.__dict__.get("backtest_run_id")
            or correlation.get("backtest_run_id")
            or "-"
        )
        record.parameter_set_version = str(
            record.__dict__.get("parameter_set_version")
            or correlation.get("parameter_set_version")
            or "-"
        )
        rendered = super().format(record)
        return redact_text(rendered)


class MemoryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        with _MEMORY_LOCK:
            _MEMORY.append(message)


_HANDLER_INSTALLED = False


def configure_logging() -> None:
    global _HANDLER_INSTALLED
    root = logging.getLogger()
    if not _HANDLER_INSTALLED:
        handler = MemoryLogHandler()
        handler.setFormatter(RedactingFormatter())
        handler.setLevel(logging.INFO)
        root.addHandler(handler)
    _HANDLER_INSTALLED = True
    # basicConfig may install a plain console handler before this module is
    # configured. Replace every non-redacting formatter so stdout/stderr logs
    # carry the same redaction and correlation fields as the memory handler.
    for handler in list(root.handlers):
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter())


def tail_logs(limit: int = 2000) -> list[str]:
    with _MEMORY_LOCK:
        return [redact_text(item) for item in list(_MEMORY)[-max(10, int(limit)):]]


def get_request_id() -> str:
    return _request_id.get() or ""


def correlation_fields() -> dict[str, str]:
    return dict(_correlation.get() or {})


def bind_worker_context(**fields: str | int | None) -> None:
    token = _correlation.set(
        {
            **(_correlation.get() or {}),
            **{key: str(value) for key, value in fields.items() if value is not None},
        }
    )
    return token  # type: ignore[return-value]


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach one server-generated or reused request id to every response."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        supplied = request.headers.get("X-Request-ID", "").strip()
        if supplied and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", supplied):
            request_id = supplied
        else:
            request_id = "req_" + secrets.token_hex(10)
        _request_id.set(request_id)
        _correlation.set({"request_id": request_id})
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


__all__ = [
    "MemoryLogHandler",
    "RedactingFormatter",
    "RequestIDMiddleware",
    "bind_worker_context",
    "configure_logging",
    "correlation_fields",
    "get_request_id",
    "redact_object",
    "redact_text",
    "tail_logs",
]
