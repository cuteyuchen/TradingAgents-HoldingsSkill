"""Structured-output retry budget and failure classification tests."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_structured_retry_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)

from app.services.model_client import (  # noqa: E402
    ModelCallError,
    ModelResult,
    ModelTimeoutError,
    StructuredOutputError,
    call_model_json,
)


def _profile(**parameters) -> SimpleNamespace:
    return SimpleNamespace(parameters_json=parameters)


def _result(text: str, *, finish_reason: str = "stop") -> ModelResult:
    return ModelResult(text=text, latency_ms=0, raw={}, finish_reason=finish_reason)


def test_first_invalid_second_valid_succeeds_with_retry_count_one():
    calls: list[list[dict[str, object]]] = []
    payloads = [
        '{"market_read": "未完',
        '{"market_read": "完整", "quality_grade": "A"}',
    ]

    def transport(messages):
        calls.append(list(messages))
        return _result(payloads.pop(0))

    result = call_model_json(_profile(), [{"role": "user", "content": "输入"}], transport=transport)

    assert result.retry_count == 1
    assert result.data["market_read"] == "完整"
    assert len(calls) == 2
    assert "上一响应未通过结构化 JSON 校验" in calls[1][-1]["content"]
    assert "未完" not in calls[1][-1]["content"]


def test_two_invalid_attempts_then_valid_succeeds_with_retry_count_two():
    calls: list[list[dict[str, object]]] = []
    payloads = ["not-json", '{"market_read": "仍不完整"', '{"market_read": "完整", "quality_grade": "B"}']

    def transport(messages):
        calls.append(list(messages))
        return _result(payloads.pop(0))

    result = call_model_json(_profile(), [{"role": "user", "content": "输入"}], transport=transport)

    assert result.retry_count == 2
    assert result.data["quality_grade"] == "B"
    assert len(calls) == 3
    assert len(calls[0]) == 1
    assert len(calls[1]) == 2
    assert len(calls[-1]) == 2


def test_all_invalid_fails_closed_with_safe_message():
    def transport(_messages):
        return _result('{"truncated": "raw-model-secret-should-not-leak"')

    with pytest.raises(StructuredOutputError) as excinfo:
        call_model_json(_profile(), [{"role": "user", "content": "输入"}], transport=transport)

    assert excinfo.value.category == "INVALID_JSON"
    assert excinfo.value.retry_count == 2
    assert "已自动重试 2 次" in str(excinfo.value)
    assert "raw-model-secret-should-not-leak" not in str(excinfo.value)


def test_wrong_top_level_list_retries():
    calls = []

    def transport(messages):
        calls.append(messages)
        return _result("[]" if len(calls) == 1 else '{"market_read": "ok"}')

    result = call_model_json(_profile(), [], transport=transport)

    assert result.retry_count == 1
    assert result.data == {"market_read": "ok"}


def test_empty_object_and_missing_required_fields_retry():
    calls = []

    def transport(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _result("{}")
        if len(calls) == 2:
            return _result('{"market_read": "缺少决策字段"}')
        return _result('{"market_read": "完整", "decision": "pass"}')

    result = call_model_json(
        _profile(),
        [],
        transport=transport,
        validator=lambda value: "decision" in value,
    )

    assert result.retry_count == 2
    assert result.data["decision"] == "pass"


def test_finish_reason_length_is_truncated_output_and_retries():
    calls = []

    def transport(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _result('{"market_read": "complete", "extra": "more"}', finish_reason="length")
        return _result('{"market_read": "complete"}')

    result = call_model_json(_profile(), [], transport=transport)

    assert result.retry_count == 1
    assert "减少冗长解释" in calls[1][-1]["content"]


def test_auth_and_config_errors_do_not_consume_structured_budget():
    calls = []

    def transport(_messages):
        calls.append("call")
        raise ModelCallError("API key invalid")

    with pytest.raises(ModelCallError, match="API key invalid"):
        call_model_json(_profile(), [], transport=transport)

    assert len(calls) == 1


def test_timeout_is_not_structured_retried():
    calls = []

    def transport(_messages):
        calls.append("call")
        raise ModelTimeoutError("timeout")

    with pytest.raises(ModelTimeoutError):
        call_model_json(_profile(), [], transport=transport)

    assert len(calls) == 1
