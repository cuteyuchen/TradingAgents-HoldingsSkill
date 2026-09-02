"""One safe REST boundary for the Fuyao financial-data API.

The client deliberately owns transport, authentication, response-envelope
validation, retry classification, and request lineage.  Provider adapters only
translate the documented ``data`` payload into canonical application models.
"""
from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import requests


FUYAO_DEFAULT_BASE_URL = "https://fuyao.aicubes.cn"
ERROR_NON_RETRYABLE = "NON_RETRYABLE"
ERROR_PERMISSION = "PERMISSION"
ERROR_DATA_MISSING = "DATA_MISSING"
ERROR_RATE_LIMIT = "RATE_LIMIT"
ERROR_UPSTREAM_FAILURE = "UPSTREAM_FAILURE"
ERROR_RETRYABLE = "RETRYABLE"

_ERROR_CATEGORIES = {
    1001: ERROR_NON_RETRYABLE,
    1002: ERROR_NON_RETRYABLE,
    1003: ERROR_NON_RETRYABLE,
    1004: ERROR_NON_RETRYABLE,
    2001: ERROR_PERMISSION,
    2003: ERROR_PERMISSION,
    3001: ERROR_DATA_MISSING,
    3002: ERROR_DATA_MISSING,
    3004: ERROR_NON_RETRYABLE,
    4001: ERROR_RATE_LIMIT,
    5001: ERROR_UPSTREAM_FAILURE,
    5002: ERROR_RETRYABLE,
    5003: ERROR_UPSTREAM_FAILURE,
}
_RETRYABLE_CODES = frozenset({4001, 5001, 5002, 5003})
_KEY_VALUE_RE = re.compile(r"(?i)(?:x[-_ ]?api[-_ ]?key|api[-_ ]?key|authorization)\s*[:=]\s*[^,;\s]+")
_BEARER_RE = re.compile(r"(?i)bearer\s+[^,;\s]+")


def _safe_text(value: Any, *, secret: str = "", limit: int = 400) -> str:
    """Return a bounded diagnostic string without retaining credentials."""

    text = str(value or "").replace("\x00", " ").strip()
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = _KEY_VALUE_RE.sub(lambda match: match.group(0).split("=", 1)[0].split(":", 1)[0] + "=[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return text[:limit]


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _response_payload(response: Any) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        payload = json_method()
        if isinstance(payload, Mapping):
            return payload
    raw = getattr(response, "content", None)
    if raw is None:
        raw = getattr(response, "text", None)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        payload = json.loads(raw)
        if isinstance(payload, Mapping):
            return payload
    raise ValueError("malformed_json")


@dataclass(frozen=True, slots=True)
class FuyaoResponse:
    """Validated Fuyao response metadata and its unmodified data payload."""

    code: int
    message: str
    request_id: str | None
    data: Any
    endpoint: str
    latency_ms: float
    attempts: int
    capability: str | None = None


class FuyaoAPIError(RuntimeError):
    """Safe structured error raised after a Fuyao request is exhausted."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        category: str = ERROR_UPSTREAM_FAILURE,
        endpoint: str = "",
        request_id: str | None = None,
        status_code: int | None = None,
        attempts: int = 1,
        retryable: bool = False,
        secret: str = "",
    ) -> None:
        self.code = code
        self.category = str(category or ERROR_UPSTREAM_FAILURE)
        self.endpoint = str(endpoint or "")
        self.request_id = str(request_id) if request_id else None
        self.status_code = status_code
        self.attempts = max(1, int(attempts or 1))
        self.retryable = bool(retryable)
        self.safe_message = _safe_text(message, secret=secret)
        super().__init__(self._render())

    def _render(self) -> str:
        parts = ["fuyao_request_failed", f"category={self.category}"]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.status_code is not None:
            parts.append(f"http_status={self.status_code}")
        if self.endpoint:
            parts.append(f"endpoint={self.endpoint}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        if self.safe_message:
            parts.append(f"message={self.safe_message}")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "endpoint": self.endpoint,
            "request_id": self.request_id,
            "status_code": self.status_code,
            "attempts": self.attempts,
            "retryable": self.retryable,
            "message": self.safe_message,
        }


class FuyaoNotConfigured(FuyaoAPIError):
    """Raised when the optional production key is intentionally absent."""

    def __init__(self, *, endpoint: str = "", capability: str | None = None) -> None:
        label = f"{capability}:" if capability else ""
        super().__init__(
            f"{label}provider_not_configured",
            category=ERROR_PERMISSION,
            endpoint=endpoint,
            attempts=1,
            retryable=False,
        )


class FuyaoClient:
    """Authenticated, rate-aware Fuyao REST client.

    ``transport`` and ``sleeper`` are injectable so contract tests never need
    a real API key or a public network request.
    """

    def __init__(
        self,
        *,
        base_url: str = FUYAO_DEFAULT_BASE_URL,
        api_key: str = "",
        connect_timeout: float = 5.0,
        read_timeout: float = 20.0,
        max_retries: int = 2,
        min_interval_seconds: float = 0.2,
        session: requests.Session | None = None,
        transport: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        backoff_seconds: float = 0.25,
    ) -> None:
        self.base_url = str(base_url or FUYAO_DEFAULT_BASE_URL).rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.connect_timeout = max(0.1, float(connect_timeout))
        self.read_timeout = max(0.1, float(read_timeout))
        self.max_retries = max(0, int(max_retries))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.session = session or requests.Session()
        self.transport = transport
        self.sleeper = sleeper or time.sleep
        self.clock = clock or time.monotonic
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self._last_request_at = 0.0
        self.last_response: FuyaoResponse | None = None
        self.last_error: FuyaoAPIError | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _endpoint(self, path: str) -> str:
        normalized = "/" + str(path or "").lstrip("/")
        return normalized

    def _wait_for_rate_limit(self) -> None:
        elapsed = self.clock() - self._last_request_at
        if self.min_interval_seconds and elapsed < self.min_interval_seconds:
            self.sleeper(self.min_interval_seconds - elapsed)

    def _record_request(self) -> None:
        self._last_request_at = self.clock()

    def _retry_delay(self, attempt: int, response: Any = None) -> float:
        retry_after = _as_float(getattr(response, "headers", {}).get("Retry-After") if response is not None else None)
        if retry_after is not None:
            return min(5.0, max(0.0, retry_after))
        return min(5.0, self.backoff_seconds * (2 ** max(0, attempt - 1)))

    def _raise(
        self,
        message: str,
        *,
        endpoint: str,
        attempts: int,
        code: int | None = None,
        category: str = ERROR_UPSTREAM_FAILURE,
        request_id: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> FuyaoAPIError:
        error = FuyaoAPIError(
            message,
            code=code,
            category=category,
            endpoint=endpoint,
            request_id=request_id,
            status_code=status_code,
            attempts=attempts,
            retryable=retryable,
            secret=self.api_key,
        )
        self.last_error = error
        return error

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        capability: str | None = None,
    ) -> FuyaoResponse:
        """GET one documented endpoint and return a validated envelope."""

        endpoint = self._endpoint(path)
        if not self.configured:
            raise FuyaoNotConfigured(endpoint=endpoint, capability=capability)
        url = self.base_url + endpoint
        query = {str(key): value for key, value in (params or {}).items() if value is not None}
        headers = {"Accept": "application/json", "X-api-key": self.api_key}
        last_error: FuyaoAPIError | None = None

        for attempt in range(1, self.max_retries + 2):
            started = self.clock()
            response = None
            try:
                self._wait_for_rate_limit()
                request = self.transport or self.session.get
                response = request(
                    url,
                    params=query,
                    headers=headers,
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                self._record_request()
                latency_ms = max(0.0, (self.clock() - started) * 1000)
                status_code = _as_int(getattr(response, "status_code", None))
                try:
                    payload = _response_payload(response)
                except Exception as exc:  # malformed JSON is an upstream failure
                    last_error = self._raise(
                        "malformed_json",
                        endpoint=endpoint,
                        attempts=attempt,
                        status_code=status_code,
                        category=ERROR_UPSTREAM_FAILURE,
                        retryable=True,
                    )
                    if attempt <= self.max_retries:
                        self.sleeper(self._retry_delay(attempt, response))
                        continue
                    raise last_error from exc

                request_id = _safe_text(payload.get("request_id"), limit=128) or None
                code = _as_int(payload.get("code"))
                message = _safe_text(payload.get("message"), secret=self.api_key, limit=400)
                if code is None or "data" not in payload or "message" not in payload:
                    last_error = self._raise(
                        "malformed_envelope",
                        endpoint=endpoint,
                        attempts=attempt,
                        request_id=request_id,
                        status_code=status_code,
                        category=ERROR_UPSTREAM_FAILURE,
                        retryable=True,
                    )
                    if attempt <= self.max_retries:
                        self.sleeper(self._retry_delay(attempt, response))
                        continue
                    raise last_error

                if status_code is not None and not 200 <= status_code < 300:
                    if status_code == 401 or status_code == 403:
                        category = ERROR_PERMISSION
                        retryable = False
                    elif status_code == 429:
                        category = ERROR_RATE_LIMIT
                        retryable = True
                    elif status_code >= 500 or status_code in {408, 425}:
                        category = ERROR_RETRYABLE
                        retryable = True
                    else:
                        category = ERROR_NON_RETRYABLE
                        retryable = False
                    last_error = self._raise(
                        message or "http_error",
                        endpoint=endpoint,
                        attempts=attempt,
                        code=code,
                        category=category,
                        request_id=request_id,
                        status_code=status_code,
                        retryable=retryable,
                    )
                    if retryable and attempt <= self.max_retries:
                        self.sleeper(self._retry_delay(attempt, response))
                        continue
                    raise last_error

                if code != 0:
                    category = _ERROR_CATEGORIES.get(code, ERROR_UPSTREAM_FAILURE)
                    retryable = code in _RETRYABLE_CODES
                    last_error = self._raise(
                        message or "business_error",
                        endpoint=endpoint,
                        attempts=attempt,
                        code=code,
                        category=category,
                        request_id=request_id,
                        status_code=status_code,
                        retryable=retryable,
                    )
                    if retryable and attempt <= self.max_retries:
                        self.sleeper(self._retry_delay(attempt, response))
                        continue
                    raise last_error

                result = FuyaoResponse(
                    code=code,
                    message=message or "success",
                    request_id=request_id,
                    data=payload.get("data"),
                    endpoint=endpoint,
                    latency_ms=latency_ms,
                    attempts=attempt,
                    capability=capability,
                )
                self.last_response = result
                self.last_error = None
                return result
            except FuyaoAPIError:
                raise
            except Exception as exc:
                # Transport exceptions do not expose URL query values or the
                # credential; only the exception class is retained.
                last_error = self._raise(
                    exc.__class__.__name__.lower(),
                    endpoint=endpoint,
                    attempts=attempt,
                    category=ERROR_RETRYABLE,
                    retryable=True,
                )
                if attempt <= self.max_retries:
                    self.sleeper(self._retry_delay(attempt, response))
                    continue
                raise last_error from exc

        raise last_error or self._raise("request_exhausted", endpoint=endpoint, attempts=self.max_retries + 1)


def client_from_settings(**overrides: Any) -> FuyaoClient:
    """Build the configured client lazily to keep provider primitives importable."""

    from ...config import settings

    values = {
        "base_url": settings.FUYAO_BASE_URL,
        "api_key": settings.FUYAO_API_KEY,
        "connect_timeout": settings.FUYAO_CONNECT_TIMEOUT_SECONDS,
        "read_timeout": settings.FUYAO_READ_TIMEOUT_SECONDS,
        "max_retries": settings.FUYAO_MAX_RETRIES,
        "min_interval_seconds": settings.FUYAO_MIN_INTERVAL_SECONDS,
    }
    values.update(overrides)
    return FuyaoClient(**values)


__all__ = [
    "ERROR_DATA_MISSING",
    "ERROR_NON_RETRYABLE",
    "ERROR_PERMISSION",
    "ERROR_RATE_LIMIT",
    "ERROR_RETRYABLE",
    "ERROR_UPSTREAM_FAILURE",
    "FUYAO_DEFAULT_BASE_URL",
    "FuyaoAPIError",
    "FuyaoClient",
    "FuyaoNotConfigured",
    "FuyaoResponse",
    "client_from_settings",
]
