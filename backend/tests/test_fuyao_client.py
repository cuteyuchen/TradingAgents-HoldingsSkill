"""Offline contract tests for the Fuyao REST boundary."""
from __future__ import annotations

from requests import Timeout

import pytest

from app.market.providers.fuyao_client import (
    ERROR_DATA_MISSING,
    ERROR_NON_RETRYABLE,
    ERROR_PERMISSION,
    ERROR_RATE_LIMIT,
    ERROR_RETRYABLE,
    ERROR_UPSTREAM_FAILURE,
    FuyaoAPIError,
    FuyaoClient,
    FuyaoNotConfigured,
)


class Response:
    def __init__(self, payload, *, status_code: int = 200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload


def _client(transport, *, key: str = "unit-test-secret", retries: int = 0, sleeps=None):
    return FuyaoClient(
        base_url="https://fuyao.test",
        api_key=key,
        max_retries=retries,
        min_interval_seconds=0,
        transport=transport,
        sleeper=(sleeps.append if sleeps is not None else lambda _value: None),
    )


def test_client_sends_key_header_and_validates_success_envelope():
    captured = {}

    def transport(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return Response({"code": 0, "message": "success", "request_id": "req-1", "data": {"item": []}})

    result = _client(transport).get("/api/a-share/prices/snapshot", params={"thscodes": "600519.SH"}, capability="quotes")

    assert result.code == 0
    assert result.request_id == "req-1"
    assert result.endpoint == "/api/a-share/prices/snapshot"
    assert captured["kwargs"]["headers"]["X-api-key"] == "unit-test-secret"
    assert captured["kwargs"]["timeout"] == (5.0, 20.0)
    assert "unit-test-secret" not in str(result)


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (1001, ERROR_NON_RETRYABLE),
        (1002, ERROR_NON_RETRYABLE),
        (1003, ERROR_NON_RETRYABLE),
        (1004, ERROR_NON_RETRYABLE),
        (2001, ERROR_PERMISSION),
        (2003, ERROR_PERMISSION),
        (3001, ERROR_DATA_MISSING),
        (3002, ERROR_DATA_MISSING),
        (3004, ERROR_NON_RETRYABLE),
        (4001, ERROR_RATE_LIMIT),
        (5001, ERROR_UPSTREAM_FAILURE),
        (5002, ERROR_RETRYABLE),
        (5003, ERROR_UPSTREAM_FAILURE),
    ],
)
def test_business_error_codes_map_to_safe_categories(code, category):
    client = _client(lambda *_args, **_kwargs: Response({"code": code, "message": "safe", "request_id": "req-error", "data": {}}))

    with pytest.raises(FuyaoAPIError) as caught:
        client.get("/api/test")

    assert caught.value.code == code
    assert caught.value.category == category
    assert caught.value.request_id == "req-error"
    assert "unit-test-secret" not in str(caught.value)


def test_rate_limit_and_upstream_errors_retry_bounded():
    calls = []
    sleeps = []

    def transport(_url, **_kwargs):
        calls.append(1)
        return Response({"code": 4001, "message": "limited", "request_id": f"r-{len(calls)}", "data": {}})

    client = _client(transport, retries=2, sleeps=sleeps)
    with pytest.raises(FuyaoAPIError) as caught:
        client.get("/api/test")
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert caught.value.category == ERROR_RATE_LIMIT
    assert caught.value.attempts == 3


def test_non_retryable_permission_does_not_retry():
    calls = []

    def transport(_url, **_kwargs):
        calls.append(1)
        return Response({"code": 2003, "message": "permission denied", "request_id": "r", "data": {}})

    with pytest.raises(FuyaoAPIError):
        _client(transport, retries=3).get("/api/test")
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [ValueError("invalid json"), {"code": 0, "message": "missing data"}])
def test_malformed_json_or_envelope_is_safe_and_bounded(payload):
    calls = []

    class BrokenResponse(Response):
        def json(self):
            if isinstance(payload, Exception):
                raise payload
            return payload

    def transport(_url, **_kwargs):
        calls.append(1)
        return BrokenResponse(payload)

    with pytest.raises(FuyaoAPIError) as caught:
        _client(transport, retries=1).get("/api/test")
    assert len(calls) == 2
    assert caught.value.category == ERROR_UPSTREAM_FAILURE
    assert "unit-test-secret" not in str(caught.value)


def test_timeout_is_retryable_but_secret_is_redacted_from_error():
    calls = []

    def transport(_url, **_kwargs):
        calls.append(1)
        raise Timeout("x-api-key=unit-test-secret")

    with pytest.raises(FuyaoAPIError) as caught:
        _client(transport, retries=1).get("/api/test")
    assert len(calls) == 2
    assert caught.value.category == ERROR_RETRYABLE
    assert "unit-test-secret" not in str(caught.value)


def test_missing_key_is_explicitly_unconfigured_without_request():
    calls = []
    client = _client(lambda *_args, **_kwargs: calls.append(1), key="")

    with pytest.raises(FuyaoNotConfigured) as caught:
        client.get("/api/test", capability="quotes")
    assert calls == []
    assert caught.value.category == ERROR_PERMISSION
    assert "api_key" not in str(caught.value).lower()
