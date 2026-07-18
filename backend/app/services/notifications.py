"""DingTalk and WeCom robot notification delivery."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from ..config import settings
from ..security import decrypt_secret
from ..v2_models import AnalysisRun, NotificationChannel, NotificationDelivery

ALLOWED_WEBHOOK_HOSTS = {
    "dingtalk": {"oapi.dingtalk.com", "api.dingtalk.com"},
    "wecom": {"qyapi.weixin.qq.com"},
}


def validate_webhook(channel_type: str, webhook: str) -> str:
    parsed = urlparse(webhook.strip())
    allowed = ALLOWED_WEBHOOK_HOSTS.get(channel_type)
    if parsed.scheme != "https" or not parsed.hostname or not allowed or parsed.hostname.lower() not in allowed:
        raise ValueError(f"{channel_type} webhook host is not allowed")
    return webhook.strip()


def _dingtalk_url(webhook: str, secret: str | None) -> str:
    if not secret:
        return webhook
    timestamp = str(int(time.time() * 1000))
    digest = hmac.new(secret.encode("utf-8"), f"{timestamp}\n{secret}".encode("utf-8"), hashlib.sha256).digest()
    sign = base64.b64encode(digest).decode("ascii")
    parsed = urlparse(webhook)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"timestamp": timestamp, "sign": sign})
    return urlunparse(parsed._replace(query=urlencode(query)))


def _post_channel(channel: NotificationChannel, title: str, markdown: str) -> tuple[int, str]:
    webhook = validate_webhook(channel.type, decrypt_secret(channel.encrypted_webhook))
    secret = decrypt_secret(channel.encrypted_secret) if channel.encrypted_secret else None
    if channel.type == "dingtalk":
        url = _dingtalk_url(webhook, secret)
        payload = {"msgtype": "markdown", "markdown": {"title": title, "text": markdown}}
    elif channel.type == "wecom":
        url = webhook
        payload = {"msgtype": "markdown", "markdown": {"content": markdown}}
    else:
        raise ValueError("Unsupported notification type")
    response = requests.post(url, json=payload, timeout=12)
    if response.status_code >= 400:
        raise RuntimeError(f"Webhook HTTP {response.status_code}: {response.text[:500]}")
    body = response.text[:1000]
    try:
        data = response.json()
        error_code = data.get("errcode")
        if error_code not in (None, 0):
            raise RuntimeError(f"Webhook error: {body}")
    except ValueError:
        pass
    return response.status_code, body


def test_channel(channel: NotificationChannel) -> tuple[int, str]:
    return _post_channel(
        channel,
        "持仓投研系统连接测试",
        "### 持仓投研系统\n\n通知渠道连接成功。",
    )


def _run_message(run: AnalysisRun) -> tuple[str, str]:
    result = (run.structured_result_json or {}).get("result", {})
    title = f"持仓分析 #{run.id}"
    rows = result.get("holdings") or []
    action_lines = []
    for row in rows[:8]:
        action_lines.append(
            f"> **{row.get('name') or row.get('code')}（{row.get('code')}）**：{row.get('action') or '观察'}；"
            f"{row.get('reason') or ''}"
        )
    warnings = result.get("risk_warnings") or []
    url = f"{settings.PUBLIC_APP_URL}/?run={run.id}"
    markdown = "\n\n".join(
        [
            f"### {title}",
            f"**组合结论：** {result.get('portfolio_conclusion') or run.summary or '-'}",
            f"**方向：** {run.final_rating or '-'}　**数据质量：** {run.data_quality_grade or '-'}　**现金目标：** {run.cash_target or '-'}",
            *action_lines,
            ("**主要风险：** " + "；".join(str(item) for item in warnings[:4])) if warnings else "",
            f"[查看完整报告]({url})",
            "仅供研究辅助，不构成投资建议。",
        ]
    ).strip()
    return title, markdown


def send_run_notifications(db, run: AnalysisRun) -> None:
    channels = (
        db.query(NotificationChannel)
        .filter(NotificationChannel.user_id == run.user_id, NotificationChannel.enabled.is_(True))
        .all()
    )
    title, markdown = _run_message(run)
    for channel in channels:
        delivery = NotificationDelivery(channel_id=channel.id, analysis_run_id=run.id, status="sending")
        db.add(delivery)
        db.flush()
        try:
            status_code, excerpt = _post_channel(channel, title, markdown)
            delivery.status = "sent"
            delivery.response_code = status_code
            delivery.response_excerpt = excerpt
            delivery.sent_at = datetime.now(UTC)
        except Exception as exc:
            delivery.status = "failed"
            delivery.error_message = str(exc)[:2000]
