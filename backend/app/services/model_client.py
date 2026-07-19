"""Unified LLM/VLM client used by vision parsing, analysis, and health checks."""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests

from ..security import decrypt_secret
from ..v2_models import ModelProfile, ModelProvider


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


@dataclass
class ModelResult:
    text: str
    latency_ms: int
    raw: dict[str, Any]


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


def _timeout(profile: ModelProfile) -> float:
    params = profile.parameters_json or {}
    return float(params.get("timeout", 90))


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
        raise ModelCallError(f"模型没有返回有效 JSON：{text[:240]}") from exc
    if not isinstance(value, dict):
        raise ModelCallError("模型 JSON 顶层必须是对象")
    return value


def parse_json_result(result: ModelResult) -> dict[str, Any]:
    return _json_from_text(result.text)


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

    started = time.monotonic()
    response = requests.post(
        f"{_base_url(provider)}/chat/completions",
        headers=headers,
        json=payload,
        timeout=_timeout(profile),
    )
    latency = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        raise ModelCallError(f"模型接口 {response.status_code}: {response.text[:500]}")
    raw = response.json()
    try:
        text = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelCallError(f"无法解析模型返回：{str(raw)[:500]}") from exc
    return ModelResult(text=text or "", latency_ms=latency, raw=raw)


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
    payload = {
        "model": profile.model_name,
        "system": "\n\n".join(system_parts),
        "messages": converted,
        "max_tokens": params.get("max_tokens", 4096),
        "temperature": params.get("temperature", 0.2),
    }
    started = time.monotonic()
    response = requests.post(
        f"{_base_url(provider)}/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_timeout(profile),
    )
    latency = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        raise ModelCallError(f"Anthropic {response.status_code}: {response.text[:500]}")
    raw = response.json()
    text = "".join(item.get("text", "") for item in raw.get("content", []) if item.get("type") == "text")
    return ModelResult(text=text, latency_ms=latency, raw=raw)


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
    generation: dict[str, Any] = {
        "temperature": params.get("temperature", 0.2),
        "maxOutputTokens": params.get("max_tokens", 4096),
    }
    if json_mode:
        generation["responseMimeType"] = "application/json"
    started = time.monotonic()
    response = requests.post(
        f"{_base_url(provider)}/v1beta/models/{profile.model_name}:generateContent",
        params={"key": api_key},
        json={"contents": [{"role": "user", "parts": parts}], "generationConfig": generation},
        timeout=_timeout(profile),
    )
    latency = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        raise ModelCallError(f"Gemini {response.status_code}: {response.text[:500]}")
    raw = response.json()
    try:
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelCallError(f"无法解析 Gemini 返回：{str(raw)[:500]}") from exc
    return ModelResult(text=text, latency_ms=latency, raw=raw)


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
    try:
        if provider_name == "anthropic":
            return _anthropic(profile, provider, messages, image_bytes, image_mime)
        if provider_name == "gemini":
            return _gemini(profile, provider, messages, image_bytes, image_mime, json_mode)
        return _openai_compatible(profile, provider, messages, image_bytes, image_mime, json_mode)
    except requests.RequestException as exc:
        raise ModelCallError(f"模型接口请求失败：{exc}") from exc


def health_check(profile: ModelProfile) -> ModelResult:
    return call_model(
        profile,
        [
            {"role": "system", "content": "You are a connection test. Reply with exactly OK."},
            {"role": "user", "content": "OK"},
        ],
        json_mode=False,
    )
