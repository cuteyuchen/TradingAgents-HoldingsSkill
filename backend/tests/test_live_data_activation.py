"""Phase O.2 live data activation：环境透传与 UAT 脚本契约。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_forwards_monitor_vars():
    for name in ("docker-compose.yml", "docker-compose.deploy.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "REALTIME_MONITOR_ENABLED=${REALTIME_MONITOR_ENABLED:-false}" in text
        assert "MONITOR_INTERVAL_SECONDS=${MONITOR_INTERVAL_SECONDS:-60}" in text
        assert "MARKET_SCORE_INTERVAL_MINUTES=${MARKET_SCORE_INTERVAL_MINUTES:-5}" in text
        assert "FUYAO_API_KEY=${FUYAO_API_KEY:-}" in text


def test_uat_script_enables_monitor_without_exposing_secret():
    text = (ROOT / "scripts" / "start_uat.ps1").read_text(encoding="utf-8")
    assert "IsNullOrWhiteSpace($env:REALTIME_MONITOR_ENABLED)" in text
    assert '$env:REALTIME_MONITOR_ENABLED = "true"' in text
    assert "IsNullOrWhiteSpace($env:MONITOR_INTERVAL_SECONDS)" in text
    assert "IsNullOrWhiteSpace($env:MARKET_SCORE_INTERVAL_MINUTES)" in text
    assert "Write-Host $env:FUYAO_API_KEY" not in text
    assert "Write-Host $env:FUYAO" not in text
    assert "echo $env:FUYAO_API_KEY" not in text.lower()
    # 用户显式值优先：只有空值才写入默认，不得无条件覆盖。
    enabled_block = text.split("REALTIME_MONITOR_ENABLED")[1]
    assert "IsNullOrWhiteSpace" in text
    assert enabled_block


def test_uat_script_preserves_user_explicit_env_overrides():
    text = (ROOT / "scripts" / "start_uat.ps1").read_text(encoding="utf-8")
    for name in (
        "REALTIME_MONITOR_ENABLED",
        "MONITOR_INTERVAL_SECONDS",
        "MARKET_SCORE_INTERVAL_MINUTES",
        "SECURITY_MASTER_SYNC_ENABLED",
    ):
        assert f"IsNullOrWhiteSpace($env:{name})" in text
