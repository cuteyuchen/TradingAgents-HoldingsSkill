"""Unified LLM/VLM client used by vision parsing, analysis, and health checks."""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests

from ..config import settings
from ..security import decrypt_secret
from ..v2_models import ModelProfile, ModelProvider

logger = logging.getLogger(__name__)


DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "minimax": "https://api.minimax.io/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://host.docker.internal:11434/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
}


class ModelCallError(RuntimeError):
    pass


class ModelTimeoutError(ModelCallError):
    """模型在允许的静默时间内没有返回任何数据。

    单独成类是为了让上层能区分"网络/超时"和"模型返回了无效内容"，
    前者可以安全重试，后者重试没有意义。
    """


class StructuredOutputError(ModelCallError):
    """Model returned content, but it was not a usable structured object.

    ``category`` keeps the safe diagnostic machine-readable without exposing
    raw model text to the UI.  This is a structured-output failure and is
    retried separately from transport-level failures.
    """

    code = "structured_output_error"

    def __init__(
        self,
        category: str,
        message: str,
        *,
        retry_count: int = 0,
    ) -> None:
        self.category = category
        self.retry_count = retry_count
        super().__init__(message)


@dataclass
class ModelResult:
    text: str
    latency_ms: int
    raw: dict[str, Any]
    # 推理型模型的思维链（reasoning_content / thinking），仅用于排查，不参与解析。
    reasoning: str = ""
    # 本次调用是否走流式；便于在日志和健康检查里回看实际链路。
    streamed: bool = False
    # 因超时或连接中断而自动重试的次数。
    retries: int = field(default=0)
    # 上游明确给出的 finish_reason / stop_reason；用于识别输出截断。
    finish_reason: str | None = None


@dataclass
class StructuredModelResult:
    data: dict[str, Any]
    retry_count: int = 0


def _api_key(provider: ModelProvider) -> str | None:
    return decrypt_secret(provider.encrypted_api_key) if provider.encrypted_api_key else None


def _base_url(provider: ModelProvider) -> str:
    value = (provider.base_url or DEFAULT_BASE_URLS.get(provider.provider.lower()) or "").rstrip("/")
    if not value:
        raise ModelCallError("该模型供应商必须配置 Base URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelCallError("Base URL 必须是完整的 http:// 或 https:// 地址")
    return value


def _params(profile: ModelProfile) -> dict[str, Any]:
    return profile.parameters_json or {}


def _float_param(params: dict[str, Any], key: str, default: float) -> float:
    """读取数值参数，非法值回退到默认值而不是直接抛错。"""
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _use_stream(profile: ModelProfile) -> bool:
    """是否使用流式请求。模型档案里的 stream 参数优先于全局默认值。"""
    params = _params(profile)
    if "stream" in params:
        return bool(params.get("stream"))
    return settings.MODEL_STREAM_DEFAULT


def _timeout(profile: ModelProfile, *, stream: bool) -> tuple[float, float]:
    """返回 (连接超时, 读超时)。

    requests 的读超时含义是"两次收到数据之间的最大间隔"，不是整个请求的总时长。
    非流式请求在模型思考期间完全没有数据返回，所以读超时必须覆盖最长思考时间；
    流式请求只要模型持续输出（包括思维链）就会不断刷新这个计时器，因此可以用
    一个小得多的静默阈值，既能容忍长思考，也能及时发现真正的连接卡死。
    """
    params = _params(profile)
    connect = _float_param(params, "connect_timeout", settings.MODEL_CONNECT_TIMEOUT)
    if stream:
        read = _float_param(
            params,
            "stream_idle_timeout",
            settings.MODEL_STREAM_IDLE_TIMEOUT,
        )
    else:
        # 兼容历史配置：老的模型档案里 timeout 就是非流式的读超时。
        read = _float_param(params, "timeout", settings.MODEL_READ_TIMEOUT)
    return connect, read


def _max_retries(profile: ModelProfile) -> int:
    params = _params(profile)
    try:
        value = int(params.get("max_retries", settings.MODEL_MAX_RETRIES))
    except (TypeError, ValueError):
        return settings.MODEL_MAX_RETRIES
    return max(0, min(value, 5))


def _timeout_hint(profile: ModelProfile, *, stream: bool, elapsed_ms: int) -> str:
    """把 requests 的超时异常翻译成能直接照着改的提示。"""
    _, read = _timeout(profile, stream=stream)
    seconds = round(elapsed_ms / 1000)
    if stream:
        return (
            f"模型在 {int(read)} 秒内没有返回任何新内容（已等待 {seconds} 秒）。"
            "这通常是上游网关断流或模型排队。可在设置中调大该模型的超时，"
            "或检查 Base URL 对应的中转服务是否稳定。"
        )
    return (
        f"模型在 {int(read)} 秒内没有返回结果（已等待 {seconds} 秒）。"
        "推理型模型思考时间较长，非流式请求必须把整段思考时间算进超时。"
        "建议在设置中为该模型开启流式，或把超时调大。"
    )


def _json_from_text(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError("INVALID_JSON", "模型没有返回有效 JSON") from exc
    if not isinstance(value, dict):
        raise StructuredOutputError("WRONG_TOP_LEVEL_TYPE", "模型 JSON 顶层必须是对象")
    return value


def parse_json_result(result: ModelResult) -> dict[str, Any]:
    return _json_from_text(result.text)


def _structured_retry_budget(profile: ModelProfile, retries: int | None) -> int:
    if retries is not None:
        try:
            value = int(retries)
        except (TypeError, ValueError):
            value = settings.MODEL_JSON_MAX_RETRIES
    else:
        params = _params(profile)
        try:
            value = int(params.get("json_max_retries", settings.MODEL_JSON_MAX_RETRIES))
        except (TypeError, ValueError):
            value = settings.MODEL_JSON_MAX_RETRIES
    return max(0, min(value, 3))


def _truncated_finish(result: ModelResult) -> bool:
    value = str(result.finish_reason or "").strip().upper()
    if value in {"LENGTH", "MAX_TOKENS"}:
        return True
    raw = result.raw
    if not isinstance(raw, dict):
        return False
    candidates = [raw.get("stop_reason")]
    choices = raw.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        candidates.append(choices[0].get("finish_reason"))
    gemini = raw.get("candidates")
    if isinstance(gemini, list) and gemini and isinstance(gemini[0], dict):
        candidates.append(gemini[0].get("finishReason"))
    return any(str(item or "").strip().upper() in {"LENGTH", "MAX_TOKENS"} for item in candidates)


def _structured_repair_instruction(category: str) -> str:
    text = (
        "上一响应未通过结构化 JSON 校验。请重新完成同一分析，仅返回一个完整、合法、"
        "闭合的 JSON 对象。不要 Markdown，不要代码围栏，不要 JSON 之外的解释。"
        "保持原始输入事实、数据质量和风险门控不变，不得为通过校验而编造数据。"
    )
    if category == "TRUNCATED_OUTPUT":
        text += " 请减少冗长解释，优先保证所有必填 JSON 字段和对象完整闭合。"
    return text


def _validator_failure(validator: Any, value: dict[str, Any]) -> str | None:
    if validator is None:
        return None
    outcome = validator(value)
    if outcome in (None, True):
        return None
    if outcome is False:
        return "MISSING_REQUIRED_FIELDS"
    text = str(outcome or "").strip().upper()
    return text if text else "MISSING_REQUIRED_FIELDS"


def call_model_json(
    profile: ModelProfile,
    messages: list[dict[str, Any]],
    *,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    retries: int | None = None,
    validator: Any = None,
    transport: Any = None,
) -> StructuredModelResult:
    """Call a model and retry only structured-output failures.

    Transport failures (timeout/connection/upstream) remain inside
    ``call_model`` and are not counted against the structured retry budget.
    Invalid/truncated model content is retried up to ``retries`` times with a
    repair instruction; raw malformed output is never fed back to the model.
    """

    budget = _structured_retry_budget(profile, retries)
    attempts = budget + 1
    transport_call = transport or (
        lambda attempt_messages: call_model(
            profile,
            attempt_messages,
            image_bytes=image_bytes,
            image_mime=image_mime,
            json_mode=True,
        )
    )
    current_messages = list(messages)
    last_error: StructuredOutputError | None = None
    for attempt in range(attempts):
        result = transport_call(current_messages)
        if _truncated_finish(result):
            last_error = StructuredOutputError(
                "TRUNCATED_OUTPUT",
                "模型输出达到长度上限，正文可能不完整。",
                retry_count=attempt,
            )
        else:
            try:
                parsed = parse_json_result(result)
            except StructuredOutputError as exc:
                last_error = exc
                last_error.retry_count = attempt
            else:
                if not parsed:
                    last_error = StructuredOutputError(
                        "EMPTY_OBJECT",
                        "模型返回了空对象。",
                        retry_count=attempt,
                    )
                else:
                    missing = _validator_failure(validator, parsed)
                    if missing:
                        last_error = StructuredOutputError(
                            missing,
                            "模型返回对象缺少本阶段要求的字段。",
                            retry_count=attempt,
                        )
                    else:
                        return StructuredModelResult(data=parsed, retry_count=attempt)
        if attempt >= attempts - 1:
            break
        current_messages = list(messages) + [
            {"role": "user", "content": _structured_repair_instruction(last_error.category)}
        ]
    raise StructuredOutputError(
        last_error.category if last_error is not None else "INVALID_JSON",
        f"分析暂时失败。模型连续返回无法解析的结构化结果，系统已自动重试 {budget} 次。",
        retry_count=budget,
    )


def _response_text_utf8(response: requests.Response) -> str:
    """Decode upstream response bytes explicitly as UTF-8."""

    raw = getattr(response, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        try:
            return bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ModelCallError("模型接口返回内容不是有效 UTF-8") from exc
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    return ""


def _response_json_utf8(response: requests.Response) -> dict[str, Any]:
    """Parse JSON from UTF-8 bytes before falling back to test doubles."""

    raw = getattr(response, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        try:
            payload = json.loads(bytes(raw).decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ModelCallError("模型接口返回内容不是有效 UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise ModelCallError("模型接口返回了无效 JSON") from exc
    else:
        json_method = getattr(response, "json", None)
        if not callable(json_method):
            raise ModelCallError("模型接口没有返回 JSON")
        payload = json_method()
    if not isinstance(payload, dict):
        raise ModelCallError("模型接口 JSON 顶层必须是对象")
    return payload


def _looks_like_sse(response: requests.Response) -> bool:
    """判断响应是否真的是 SSE。

    部分中转网关会忽略 stream 参数直接返回完整 JSON。这种情况下按 SSE 解析
    会得到空正文，必须回退到普通 JSON 解析，否则流式默认开启就会误伤这些网关。
    """
    content_type = (response.headers.get("Content-Type") or "").lower()
    return "text/event-stream" in content_type


def _sse_payloads(response: requests.Response) -> Any:
    """逐行解析 SSE 响应，产出每个 data: 后面的 JSON 对象。

    只要还在产生数据块，requests 的读超时计时器就会被刷新，
    因此模型思考再久也不会被误判为超时。
    """
    for raw_line in response.iter_lines(decode_unicode=False):
        if not raw_line:
            # SSE 的心跳空行同样能刷新读超时，直接跳过。
            continue
        if isinstance(raw_line, bytes):
            try:
                raw_line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ModelCallError("模型 SSE 内容不是有效 UTF-8") from exc
        elif not isinstance(raw_line, str):
            raw_line = str(raw_line)
        line = raw_line.strip()
        if line.startswith(":"):
            # 部分网关用注释行做 keep-alive。
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            if data == "[DONE]":
                return
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            # 单个坏块不应该让整次调用失败。
            logger.debug("Skipped malformed SSE chunk: %s", data[:200])
            continue


def _openai_compatible(
    profile: ModelProfile,
    provider: ModelProvider,
    messages: list[dict[str, Any]],
    image_bytes: bytes | None,
    image_mime: str | None,
    json_mode: bool,
) -> ModelResult:
    api_key = _api_key(provider)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    converted: list[dict[str, Any]] = []
    for message in messages:
        converted.append({"role": message["role"], "content": message["content"]})
    if image_bytes:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        text = str(converted[-1]["content"])
        converted[-1]["content"] = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{image_mime or 'image/png'};base64,{encoded}"}},
        ]

    parameters = dict(profile.parameters_json or {})
    stream = _use_stream(profile)
    payload: dict[str, Any] = {
        "model": profile.model_name,
        "messages": converted,
        "temperature": parameters.get("temperature", 0.2),
        "max_tokens": parameters.get("max_tokens", 4096),
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    for key in ("top_p", "frequency_penalty", "presence_penalty", "reasoning_effort"):
        if key in parameters:
            payload[key] = parameters[key]
    if stream:
        payload["stream"] = True

    connect_timeout, read_timeout = _timeout(profile, stream=stream)
    started = time.monotonic()
    response = requests.post(
        f"{_base_url(provider)}/chat/completions",
        headers={**headers, **({"Accept": "text/event-stream"} if stream else {})},
        json=payload,
        timeout=(connect_timeout, read_timeout),
        stream=stream,
    )
    if response.status_code >= 400:
        # 流式下错误体也要先读出来再关闭连接。
        detail = _response_text_utf8(response)[:500]
        response.close()
        raise ModelCallError(f"模型接口 {response.status_code}: {detail}")

    if not stream or not _looks_like_sse(response):
        # 网关忽略了 stream 参数时也走这里，按普通 JSON 解析。
        latency = int((time.monotonic() - started) * 1000)
        raw = _response_json_utf8(response)
        try:
            message = raw["choices"][0]["message"]
            text = message.get("content")
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelCallError(f"无法解析模型返回：{str(raw)[:500]}") from exc
        finish_reason = raw.get("choices", [{}])[0].get("finish_reason")
        return ModelResult(
            text=text or "",
            latency_ms=latency,
            raw=raw,
            reasoning=str(reasoning or ""),
            streamed=False,
            finish_reason=finish_reason,
        )

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    model_name = profile.model_name
    try:
        for chunk in _sse_payloads(response):
            if not isinstance(chunk, dict):
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            if chunk.get("model"):
                model_name = chunk["model"]
            # 有些网关把错误塞进流里而不是用 HTTP 状态码返回。
            if chunk.get("error"):
                raise ModelCallError(f"模型接口返回错误：{str(chunk['error'])[:500]}")
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or choice.get("message") or {}
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                # 少数实现把 delta.content 组织成分段数组。
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str):
                reasoning_parts.append(reasoning)
    finally:
        response.close()

    latency = int((time.monotonic() - started) * 1000)
    text = "".join(text_parts)
    reasoning = "".join(reasoning_parts)
    if not text:
        if reasoning:
            raise ModelCallError(
                "模型只返回了思考过程没有返回正文，通常是 max_tokens 太小被思维链占满。"
                f"请调大该模型的 Max Tokens（当前 {payload['max_tokens']}）。"
            )
        raise ModelCallError("模型返回了空响应，请检查模型名称与上游服务状态。")
    if finish_reason == "length":
        logger.warning(
            "Model %s output truncated by max_tokens=%s", profile.model_name, payload["max_tokens"]
        )
    raw = {
        "model": model_name,
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}],
        "usage": usage or {},
        "streamed": True,
    }
    return ModelResult(
        text=text,
        latency_ms=latency,
        raw=raw,
        reasoning=reasoning,
        streamed=True,
        finish_reason=finish_reason,
    )


def _anthropic(
    profile: ModelProfile,
    provider: ModelProvider,
    messages: list[dict[str, Any]],
    image_bytes: bytes | None,
    image_mime: str | None,
) -> ModelResult:
    api_key = _api_key(provider)
    if not api_key:
        raise ModelCallError("Anthropic 需要 API Key")
    system_parts = [str(m["content"]) for m in messages if m["role"] == "system"]
    user_messages = [m for m in messages if m["role"] != "system"]
    converted: list[dict[str, Any]] = []
    for message in user_messages:
        converted.append({"role": message["role"], "content": str(message["content"])})
    if image_bytes:
        converted[-1]["content"] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_mime or "image/png",
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                },
            },
            {"type": "text", "text": str(user_messages[-1]["content"])},
        ]
    params = profile.parameters_json or {}
    stream = _use_stream(profile)
    payload: dict[str, Any] = {
        "model": profile.model_name,
        "system": "\n\n".join(system_parts),
        "messages": converted,
        "max_tokens": params.get("max_tokens", 4096),
        "temperature": params.get("temperature", 0.2),
    }
    if stream:
        payload["stream"] = True

    connect_timeout, read_timeout = _timeout(profile, stream=stream)
    started = time.monotonic()
    response = requests.post(
        f"{_base_url(provider)}/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            **({"Accept": "text/event-stream"} if stream else {}),
        },
        json=payload,
        timeout=(connect_timeout, read_timeout),
        stream=stream,
    )
    if response.status_code >= 400:
        detail = _response_text_utf8(response)[:500]
        response.close()
        raise ModelCallError(f"Anthropic {response.status_code}: {detail}")

    if not stream or not _looks_like_sse(response):
        # 网关忽略了 stream 参数时也走这里，按普通 JSON 解析。
        latency = int((time.monotonic() - started) * 1000)
        raw = _response_json_utf8(response)
        text = "".join(item.get("text", "") for item in raw.get("content", []) if item.get("type") == "text")
        thinking = "".join(
            item.get("thinking", "") for item in raw.get("content", []) if item.get("type") == "thinking"
        )
        return ModelResult(
            text=text,
            latency_ms=latency,
            raw=raw,
            reasoning=thinking,
            streamed=False,
            finish_reason=raw.get("stop_reason"),
        )

    # Anthropic 的 SSE 用 content_block_delta 增量推送，thinking 块与正文分开。
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    stop_reason: str | None = None
    usage: dict[str, Any] = {}
    try:
        for event in _sse_payloads(response):
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "error":
                raise ModelCallError(f"Anthropic 流式返回错误：{str(event.get('error'))[:500]}")
            if event_type == "content_block_delta":
                delta = event.get("delta") or {}
                if isinstance(delta.get("text"), str):
                    text_parts.append(delta["text"])
                elif isinstance(delta.get("thinking"), str):
                    thinking_parts.append(delta["thinking"])
            elif event_type == "message_delta":
                delta = event.get("delta") or {}
                if delta.get("stop_reason"):
                    stop_reason = delta["stop_reason"]
                if event.get("usage"):
                    usage = event["usage"]
            elif event_type == "message_start":
                usage = (event.get("message") or {}).get("usage") or usage
    finally:
        response.close()

    latency = int((time.monotonic() - started) * 1000)
    text = "".join(text_parts)
    thinking = "".join(thinking_parts)
    if not text:
        if thinking:
            raise ModelCallError(
                "模型只返回了思考过程没有返回正文，通常是 max_tokens 太小被思维链占满。"
                f"请调大该模型的 Max Tokens（当前 {payload['max_tokens']}）。"
            )
        raise ModelCallError("Anthropic 返回了空响应，请检查模型名称与上游服务状态。")
    if stop_reason == "max_tokens":
        logger.warning(
            "Model %s output truncated by max_tokens=%s", profile.model_name, payload["max_tokens"]
        )
    raw = {
        "model": profile.model_name,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": usage,
        "streamed": True,
    }
    return ModelResult(
        text=text,
        latency_ms=latency,
        raw=raw,
        reasoning=thinking,
        streamed=True,
        finish_reason=stop_reason,
    )


def _gemini(
    profile: ModelProfile,
    provider: ModelProvider,
    messages: list[dict[str, Any]],
    image_bytes: bytes | None,
    image_mime: str | None,
    json_mode: bool,
) -> ModelResult:
    api_key = _api_key(provider)
    if not api_key:
        raise ModelCallError("Gemini 需要 API Key")
    parts: list[dict[str, Any]] = [{"text": "\n\n".join(str(m["content"]) for m in messages)}]
    if image_bytes:
        parts.append(
            {
                "inlineData": {
                    "mimeType": image_mime or "image/png",
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                }
            }
        )
    params = profile.parameters_json or {}
    stream = _use_stream(profile)
    generation: dict[str, Any] = {
        "temperature": params.get("temperature", 0.2),
        "maxOutputTokens": params.get("max_tokens", 4096),
    }
    if json_mode:
        generation["responseMimeType"] = "application/json"

    connect_timeout, read_timeout = _timeout(profile, stream=stream)
    # 流式走 streamGenerateContent + alt=sse，返回标准 SSE。
    method = "streamGenerateContent" if stream else "generateContent"
    query = {"key": api_key}
    if stream:
        query["alt"] = "sse"
    started = time.monotonic()
    response = requests.post(
        f"{_base_url(provider)}/v1beta/models/{profile.model_name}:{method}",
        params=query,
        json={"contents": [{"role": "user", "parts": parts}], "generationConfig": generation},
        timeout=(connect_timeout, read_timeout),
        stream=stream,
    )
    if response.status_code >= 400:
        detail = _response_text_utf8(response)[:500]
        response.close()
        raise ModelCallError(f"Gemini {response.status_code}: {detail}")

    if not stream or not _looks_like_sse(response):
        # 网关忽略了 stream 参数时也走这里，按普通 JSON 解析。
        latency = int((time.monotonic() - started) * 1000)
        raw = _response_json_utf8(response)
        try:
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelCallError(f"无法解析 Gemini 返回：{str(raw)[:500]}") from exc
        finish_reason = raw.get("candidates", [{}])[0].get("finishReason")
        return ModelResult(
            text=text,
            latency_ms=latency,
            raw=raw,
            streamed=False,
            finish_reason=finish_reason,
        )

    text_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    try:
        for chunk in _sse_payloads(response):
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                raise ModelCallError(f"Gemini 流式返回错误：{str(chunk['error'])[:500]}")
            if chunk.get("usageMetadata"):
                usage = chunk["usageMetadata"]
            for candidate in chunk.get("candidates") or []:
                if not isinstance(candidate, dict):
                    continue
                if candidate.get("finishReason"):
                    finish_reason = candidate["finishReason"]
                for part in (candidate.get("content") or {}).get("parts") or []:
                    # thought 为 true 的分片是思维链，不计入正文。
                    if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought"):
                        text_parts.append(part["text"])
    finally:
        response.close()

    latency = int((time.monotonic() - started) * 1000)
    text = "".join(text_parts)
    if not text:
        raise ModelCallError("Gemini 返回了空响应，请检查模型名称与上游服务状态。")
    if finish_reason == "MAX_TOKENS":
        logger.warning(
            "Model %s output truncated by maxOutputTokens=%s",
            profile.model_name,
            generation["maxOutputTokens"],
        )
    raw = {
        "candidates": [
            {"content": {"parts": [{"text": text}]}, "finishReason": finish_reason}
        ],
        "usageMetadata": usage,
        "streamed": True,
    }
    return ModelResult(
        text=text,
        latency_ms=latency,
        raw=raw,
        streamed=True,
        finish_reason=finish_reason,
    )


def _acceptance_input(messages: list[dict[str, Any]]) -> dict[str, Any]:
    marker = "输入数据："
    for message in reversed(messages):
        content = str(message.get("content") or "")
        if marker not in content:
            continue
        try:
            value = json.loads(content.split(marker, 1)[1].strip())
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _acceptance_phase_content(messages: list[dict[str, Any]]) -> str:
    """Return the model-facing phase instruction, skipping repair prompts."""

    repair = (
        "上一响应未通过结构化 JSON 校验",
        "请重新完成同一分析",
    )
    for message in reversed(messages):
        content = str(message.get("content") or "")
        if content.startswith(repair):
            continue
        return content
    return str(messages[-1].get("content") or "") if messages else ""


def _identity_fixture_holdings(
    image_bytes: bytes,
) -> dict[str, Any] | None:
    """Return fictional O.2 holding fixtures used by the browser acceptance."""

    def holding(code: str | None, name: str, *, ocr_name: str | None = None) -> dict[str, Any]:
        return {
            "code": code,
            "name": name,
            "market": "A_SHARE",
            "qty": 10000,
            "available_qty": 10000,
            "cost": 1.0,
            "price": 1.1,
            "market_value": 11000,
            "pnl": 0.1,
            "pnl_amount": 1000,
            "weight": 0.1,
            "extra": {"ocr_name": ocr_name} if ocr_name else {},
        }

    if b"identity-7cn" in image_bytes:
        holdings = [
            holding(None, "创业板ETF"),
            holding(None, "通信ETF"),
            holding(None, "有色ETF"),
            holding(None, "半导体ETF"),
            holding(None, "科创50ETF"),
            holding(None, "中证1000ETF"),
            holding(None, "沪深300ETF"),
        ]
        return {
            "holdings": holdings,
            "total_assets": 1000000,
            "total_market_value": 77000,
            "broker_available_cash": 923000,
            "corrected_unused_funds": 923000,
            "repo_or_standard_bond_value": 0,
            "excluded_items": [],
            "notes": ["phase-o.2 identity fixture"],
        }
    if b"identity-ambiguous" in image_bytes:
        return {
            "holdings": [holding(None, "同名验收ETF")],
            "total_assets": 100000,
            "total_market_value": 11000,
            "broker_available_cash": 89000,
            "corrected_unused_funds": 89000,
            "repo_or_standard_bond_value": 0,
            "excluded_items": [],
            "notes": ["phase-o.2 ambiguous fixture"],
        }
    if b"identity-unresolved" in image_bytes:
        return {
            "holdings": [holding(None, "不存在的验收标的")],
            "total_assets": 100000,
            "total_market_value": 11000,
            "broker_available_cash": 89000,
            "corrected_unused_funds": 89000,
            "repo_or_standard_bond_value": 0,
            "excluded_items": [],
            "notes": ["phase-o.2 unresolved fixture"],
        }
    if b"identity-history-source" in image_bytes:
        return {
            "holdings": [holding("159915", "创业板", ocr_name="创业板")],
            "total_assets": 200000,
            "total_market_value": 22000,
            "broker_available_cash": 178000,
            "corrected_unused_funds": 178000,
            "repo_or_standard_bond_value": 0,
            "excluded_items": [],
            "notes": ["phase-o.2 history source fixture"],
        }
    if b"identity-history-reuse" in image_bytes:
        return {
            "holdings": [holding(None, "创业板")],
            "total_assets": 200000,
            "total_market_value": 22000,
            "broker_available_cash": 178000,
            "corrected_unused_funds": 178000,
            "repo_or_standard_bond_value": 0,
            "excluded_items": [],
            "notes": ["phase-o.2 history reuse fixture"],
        }
    if b"identity-mostly" in image_bytes:
        holdings = [
            holding(None, "创业板ETF"),
            holding(None, "通信ETF"),
            holding(None, "有色ETF"),
            holding(None, "半导体ETF"),
            holding(None, "科创50ETF"),
            holding(None, "中证1000ETF"),
            holding(None, "同名验收ETF"),
        ]
        return {
            "holdings": holdings,
            "total_assets": 1000000,
            "total_market_value": 77000,
            "broker_available_cash": 923000,
            "corrected_unused_funds": 923000,
            "repo_or_standard_bond_value": 0,
            "excluded_items": [],
            "notes": ["phase-o.2 mostly automatic fixture"],
        }
    return None


def _acceptance_result(
    messages: list[dict[str, Any]],
    *,
    image_bytes: bytes | None,
    json_mode: bool,
) -> ModelResult:
    """Return deterministic provider facts for the isolated acceptance runner."""

    content = _acceptance_phase_content(messages)
    last_content = str(messages[-1].get("content") or "") if messages else ""
    input_value = _acceptance_input(messages)
    payload_input = input_value.get("input") if isinstance(input_value.get("input"), dict) else input_value
    checkpoint = str((payload_input or {}).get("checkpoint") or "").strip()
    if "多空辩论" in content and checkpoint in {"retry-success", "retry-exhausted"}:
        repair_present = "上一响应未通过结构化 JSON 校验" in last_content or "请重新完成同一分析" in last_content
        if checkpoint == "retry-exhausted" or not repair_present:
            return ModelResult(
                text='{"bull_claims": [{"claim": "acceptance truncation fixture',
                latency_ms=0,
                raw={"provider": "acceptance", "deterministic": True},
                streamed=False,
                finish_reason="length",
            )
    if image_bytes is not None:
        identity_value = _identity_fixture_holdings(image_bytes)
        if identity_value is not None:
            value = identity_value
        elif b"acceptance-invalid" in image_bytes:
            value: dict[str, Any] = {
                "holdings": [],
                "excluded_items": [],
                "notes": ["acceptance_invalid_fixture"],
            }
        else:
            value = {
                "holdings": [
                    {
                        "code": "600519",
                        "name": "贵州茅台",
                        "market": "A_SHARE",
                        "qty": 100,
                        "available_qty": 80,
                        "cost": 1500,
                        "price": 1600,
                        "market_value": 160000,
                        "pnl": 0.0667,
                        "pnl_amount": 10000,
                        "weight": 0.8,
                    },
                    {
                        "code": "510300",
                        "name": "沪深300ETF",
                        "market": "A_SHARE",
                        "qty": 10000,
                        "available_qty": 10000,
                        "cost": 4.0,
                        "price": 4.2,
                        "market_value": 42000,
                        "pnl": 0.05,
                        "pnl_amount": 2000,
                        "weight": 0.21,
                    },
                ],
                "total_assets": 250000,
                "total_market_value": 202000,
                "broker_available_cash": 48000,
                "corrected_unused_funds": 48000,
                "repo_or_standard_bond_value": 0,
                "excluded_items": [],
                "notes": ["phase-o.1 deterministic vision fixture"],
            }
        raw = value
    elif "匹配六位证券代码" in content:
        input_value = _acceptance_input(messages)
        matches = []
        for item in input_value.get("holdings") or []:
            name = str(item.get("name") or "")
            code = "600519" if "茅台" in name else "510300" if "沪深" in name or "ETF" in name else None
            matches.append({"index": item.get("index"), "code": code, "confidence": "high", "reason": "acceptance fixture"})
        raw = {"matches": matches}
    elif "证据包" in content:
        raw = {
            "market_read": "验收行情稳定，组合数据可用。",
            "intent": {"goal": "验证真实分析链路"},
            "analyst_reports": [
                {"role": "technical", "summary": "固定行情与持仓快照一致。", "evidence": ["Acceptance fixture quote"]},
                {"role": "risk", "summary": "可用数量与组合现金事实已进入门控。", "evidence": ["Confirmed snapshot"]},
            ],
            "holding_evidence": [],
            "portfolio_risks": ["验收环境不代表真实市场证据"],
            "data_gaps": [],
            "quality_grade": "A",
        }
    elif "多空辩论" in content:
        raw = {
            "bull_claims": [{"claim_id": "INV-1", "speaker": "bull", "claim": "固定行情支持继续观察组合。", "evidence": ["quote"], "confidence": 0.8, "status": "addressed"}],
            "bear_claims": [{"claim_id": "INV-2", "speaker": "bear", "claim": "验收事实不等同于实时生产证据。", "evidence": ["fixture"], "confidence": 0.7, "status": "open"}],
            "unresolved_claim_ids": ["INV-2"],
            "round_summaries": [{"round": 1, "goal": "建立核心论点", "summary": "验收固定事实完成攻防。"}],
            "judge_decision": "组合动作由后端 Gate 最终决定。",
        }
    elif "研究总监裁决" in content:
        raw = {
            "rating": "Hold",
            "winner": "balanced",
            "unresolved_claim_treatment": ["INV-2"],
            "strategic_action": "保持组合与候选事实分离。",
            "confidence": "medium",
            "reasoning": "确定性 provider 只用于浏览器验收。",
        }
    elif "风控经理审查" in content:
        raw = {
            "decision": "pass",
            "reason": "持仓动作均为 hold，候选仍由后端组合 Gate 约束。",
            "hard_constraints": ["不得绕过 Portfolio Gate"],
            "soft_constraints": ["验收环境不发送真实订单"],
            "de_risk_triggers": ["quote_missing"],
            "execution_prerequisites": ["paper-only shadow"],
        }
    elif "交易员方案" in content:
        input_value = _acceptance_input(messages)
        snapshot = input_value.get("input", {}).get("snapshot") or input_value.get("snapshot") or {}
        orders = [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "action": "conditional_add" if item.get("code") == "510300" else ("reduce" if item.get("code") == "600519" else "hold"),
                "trigger": "固定事实变化后重新复核",
                "quantity": "20" if item.get("code") == "600519" else None,
                "take_profit": "达到预设目标后复核",
                "stop_loss": "质量门控失效",
                "invalidating_condition": "关键行情缺失",
                "checkpoint_rule": "验收固定检查点",
            }
            for item in snapshot.get("holdings") or []
            if item.get("code")
        ]
        raw = {"orders": orders, "checkpoint_rule": "固定检查点复核", "cancel_all_buys_when": "质量门控阻断"}
    elif "三方风控辩论" in content:
        raw = {
            "claims": [
                {"claim_id": "RISK-1", "speaker": "aggressive", "claim": "只允许验证性纸面动作。", "evidence": ["shadow"], "confidence": 0.7, "status": "open"},
                {"claim_id": "RISK-2", "speaker": "neutral", "claim": "后端 Gate 优先。", "evidence": ["portfolio context"], "confidence": 0.8, "status": "addressed"},
                {"claim_id": "RISK-3", "speaker": "conservative", "claim": "现实行情仍需人工验证。", "evidence": ["NOT_READY"], "confidence": 0.9, "status": "open"},
            ],
            "unresolved_claim_ids": ["RISK-3"],
            "round_summaries": [{"round": 1, "goal": "风险取舍", "summary": "三方确认只验证纸面链路。"}],
            "judge_decision": "以保守约束为准。",
        }
    elif "只审查后端 deterministic_action_candidates" in content:
        input_value = _acceptance_input(messages)
        candidates = input_value.get("deterministic_action_candidates") or []
        codes = [item.get("code") for item in candidates if item.get("code")]
        veto_codes = [code for code in codes if code == "601318"]
        raw = {
            "accepted_codes": [code for code in codes if code not in veto_codes],
            "veto_codes": veto_codes,
            "explanations": {
                code: {
                    "reason_detail": {
                        "catalyst": "验收固定催化",
                        "capital_flow": "验收固定资金面证据",
                        "sector_position": "验收固定板块位置证据",
                    },
                    "risk": ["组合层需最终批准"],
                }
                for code in codes
            },
            "candidate_blocked_reason": "候选达到 ACTION，但组合层未批准。" if veto_codes else "",
            "hot_sectors": [{"name": "验收板块", "pct_change": 1.2}],
        }
    elif "组合经理最终决策" in content:
        input_value = _acceptance_input(messages)
        payload_input = input_value.get("input") or {}
        snapshot = payload_input.get("snapshot") or {}
        candidate_context = payload_input.get("candidate_context") or {}
        candidate_rows = candidate_context.get("action") or []
        vetoed_candidate = any(item.get("code") == "601318" for item in candidate_rows if isinstance(item, dict))
        action_holding = any(item.get("code") == "600519" for item in snapshot.get("holdings") or [])
        holdings = [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "action": "conditional_add" if not vetoed_candidate and item.get("code") == "510300" else ("hold" if vetoed_candidate or item.get("code") != "600519" else "reduce"),
                "reason": "验收固定证据支持保持当前持仓。",
                "trigger": "关键事实变化后复核",
                "quantity": None if vetoed_candidate or item.get("code") != "600519" else "20",
                "target_weight": "0.22" if not vetoed_candidate and item.get("code") == "510300" else None,
                "stop_loss": "质量门控失效",
                "take_profit": "达到目标后复核",
                "risk": "验收环境不替代真实市场风险",
            }
            for item in snapshot.get("holdings") or []
            if item.get("code")
        ]
        raw = {
            "data_quality_grade": "A",
            "market_read": "验收固定行情已通过质量门控。",
            "portfolio_conclusion": "候选达到 ACTION，但组合层未批准，保持 NO_ACTION。" if vetoed_candidate else ("ACTION：按固定验收事实执行可审计的减仓建议。" if action_holding else "当前组合保持不变。"),
            "final_rating": "no_action" if vetoed_candidate or not action_holding else "reduce",
            "cash_target": "保持现状",
            "confidence": "medium",
            "holdings": holdings,
            "candidates": [],
            "history_consistency": "固定交易日与 Asia/Shanghai 显示用于可重复验收。",
            "bull_case": ["行情事实完整"],
            "bear_case": ["现实 live evidence 尚未满足"],
            "unresolved_claims": ["现实环境仍需人工确认"],
            "risk_warnings": ["不会发送真实订单"],
            "evidence": ["Acceptance deterministic fixture"],
        }
    else:
        raw = {"status": "ok", "message": "OK"} if json_mode else "OK"

    text = json.dumps(raw, ensure_ascii=False, separators=(",", ":")) if isinstance(raw, dict) else str(raw)
    return ModelResult(
        text=text,
        latency_ms=0,
        raw={"provider": "acceptance", "deterministic": True},
        streamed=False,
        finish_reason="stop",
    )


def call_model(
    profile: ModelProfile,
    messages: list[dict[str, Any]],
    *,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    json_mode: bool = False,
) -> ModelResult:
    provider = profile.provider
    if not provider.enabled:
        raise ModelCallError("模型供应商已停用")
    provider_name = provider.provider.lower()
    if provider_name == "acceptance":
        if not settings.ACCEPTANCE_MODE:
            raise ModelCallError("Acceptance provider 仅允许在 ACCEPTANCE_MODE 下使用")
        return _acceptance_result(messages, image_bytes=image_bytes, json_mode=json_mode)
    stream = _use_stream(profile)
    attempts = _max_retries(profile) + 1
    last_error: ModelCallError | None = None

    for attempt in range(attempts):
        started = time.monotonic()
        try:
            if provider_name == "anthropic":
                result = _anthropic(profile, provider, messages, image_bytes, image_mime)
            elif provider_name == "gemini":
                result = _gemini(profile, provider, messages, image_bytes, image_mime, json_mode)
            else:
                result = _openai_compatible(profile, provider, messages, image_bytes, image_mime, json_mode)
            result.retries = attempt
            return result
        except requests.Timeout as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            last_error = ModelTimeoutError(_timeout_hint(profile, stream=stream, elapsed_ms=elapsed))
            last_error.__cause__ = exc
        except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
            # 长连接被中间层掐断时 requests 抛的是连接类错误，与超时同样可以重试。
            last_error = ModelTimeoutError(f"与模型服务的连接中断：{exc}")
            last_error.__cause__ = exc
        except requests.RequestException as exc:
            # 其余请求异常（如非法 URL、SSL 错误）重试没有意义。
            raise ModelCallError(f"模型接口请求失败：{exc}") from exc

        if attempt < attempts - 1:
            logger.warning(
                "Model %s call failed (%s), retrying %s/%s",
                profile.model_name,
                last_error,
                attempt + 1,
                attempts - 1,
            )
            # 退避一小段时间，避免上游正在限流时立刻打第二次。
            time.sleep(min(2 ** attempt, 5))

    raise last_error if last_error else ModelCallError("模型调用失败")


def health_check(profile: ModelProfile) -> ModelResult:
    return call_model(
        profile,
        [
            {"role": "system", "content": "You are a connection test. Reply with exactly OK."},
            {"role": "user", "content": "OK"},
        ],
        json_mode=False,
    )
