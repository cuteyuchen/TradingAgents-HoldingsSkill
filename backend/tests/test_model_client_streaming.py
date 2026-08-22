"""Model client streaming, timeout, and retry behaviour.

回归重点：推理型模型思考时间长时不能被误判为超时。
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_model_client_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)


# 模型在返回正文前的思考时长，需要明显大于测试里设置的静默阈值。
THINK_SECONDS = 3


def _make_handler(mode: str):
    """构造一个可控的假模型服务。

    mode="reasoning" 先持续输出思维链再输出正文；
    mode="silent"    非流式且长时间不返回任何字节；
    mode="ignores_stream" 收到 stream=true 却返回普通 JSON。
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _send_json(self, payload: dict):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length) or b"{}")
            completion = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]}

            if mode == "ignores_stream" or not request.get("stream"):
                if mode == "silent":
                    # 完全静默，用于触发非流式读超时。
                    time.sleep(THINK_SECONDS)
                self._send_json(completion)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # 思考阶段只推送 reasoning_content，正文一个字都没有。
            for index in range(THINK_SECONDS):
                chunk = {"choices": [{"delta": {"reasoning_content": f"step{index}"}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                time.sleep(1)
            for piece in ['{"ok"', ": true", "}"]:
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


@pytest.fixture
def fake_model():
    """启动假模型服务，返回一个按参数生成 ModelProfile 的工厂。"""
    servers = []

    def start(mode: str = "reasoning", **params):
        server = HTTPServer(("127.0.0.1", 0), _make_handler(mode))
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        provider = SimpleNamespace(
            provider="openai_compatible",
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            encrypted_api_key=None,
            enabled=True,
        )
        return SimpleNamespace(provider=provider, model_name="fake-reasoner", parameters_json=params)

    yield start
    for server in servers:
        server.shutdown()


def test_streaming_survives_long_thinking(fake_model):
    """思考时间远超静默阈值，但只要持续输出就不应超时。"""
    from app.services import model_client

    profile = fake_model("reasoning", stream=True, stream_idle_timeout=1.5, max_retries=0)
    result = model_client.call_model(profile, [{"role": "user", "content": "hi"}], json_mode=True)

    assert model_client.parse_json_result(result) == {"ok": True}
    assert result.streamed is True
    # 思维链被单独收集，不会污染正文。
    assert "step0" in result.reasoning
    assert "step" not in result.text
    assert result.latency_ms >= THINK_SECONDS * 1000


def test_non_streaming_timeout_explains_how_to_fix(fake_model):
    """非流式静默超时必须抛 ModelTimeoutError，并给出可执行建议。"""
    from app.services import model_client

    profile = fake_model("silent", stream=False, timeout=1, max_retries=0)
    with pytest.raises(model_client.ModelTimeoutError) as excinfo:
        model_client.call_model(profile, [{"role": "user", "content": "hi"}])

    assert "开启流式" in str(excinfo.value)


def test_falls_back_when_gateway_ignores_stream(fake_model):
    """网关无视 stream 返回普通 JSON 时要回退解析，而不是报空响应。"""
    from app.services import model_client

    profile = fake_model("ignores_stream", stream=True, max_retries=0)
    result = model_client.call_model(profile, [{"role": "user", "content": "hi"}], json_mode=True)

    assert model_client.parse_json_result(result) == {"ok": True}
    assert result.streamed is False


def test_timeout_is_retried_and_counted(fake_model):
    """超时属于可重试错误，重试次数要如实记录。"""
    from app.services import model_client

    profile = fake_model("silent", stream=False, timeout=1, max_retries=1)
    started = time.monotonic()
    with pytest.raises(model_client.ModelTimeoutError):
        model_client.call_model(profile, [{"role": "user", "content": "hi"}])

    # 两次超时各 1 秒，加上一次退避，总耗时必然超过单次超时。
    assert time.monotonic() - started >= 2


def test_stream_toggle_and_timeout_semantics():
    """流式开关与两种超时语义的取值规则。"""
    from app.config import settings
    from app.services import model_client

    streaming = SimpleNamespace(parameters_json={"stream": True, "stream_idle_timeout": 42})
    assert model_client._use_stream(streaming) is True
    assert model_client._timeout(streaming, stream=True)[1] == 42

    # 历史配置里的 timeout 仍然作为非流式读超时生效。
    legacy = SimpleNamespace(parameters_json={"stream": False, "timeout": 300})
    assert model_client._use_stream(legacy) is False
    assert model_client._timeout(legacy, stream=False)[1] == 300

    # 未显式配置时跟随全局默认值。
    default = SimpleNamespace(parameters_json={})
    assert model_client._use_stream(default) is settings.MODEL_STREAM_DEFAULT
    # 非法值回退到默认而不是抛错。
    broken = SimpleNamespace(parameters_json={"timeout": "abc"})
    assert model_client._timeout(broken, stream=False)[1] == settings.MODEL_READ_TIMEOUT
