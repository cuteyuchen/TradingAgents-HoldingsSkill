"""The backend must execute the versioned contract from the repository Skill."""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_shared_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_ARTIFACTS_DIR", os.path.join(BACKEND_DIR, "data", f"test_shared_artifacts_{os.getpid()}"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)


def test_repository_skill_runtime_is_loadable_and_versioned():
    from app.services.skill_runtime import load_skill_runtime, runtime_metadata, runtime_prompt

    runtime = load_skill_runtime()
    metadata = runtime_metadata()
    prompt = runtime_prompt()

    assert runtime["name"] == "tradingagents-holdings-advisor"
    assert runtime["version"] == "2.0.0"
    assert len(runtime["runtime_sha256"]) == 64
    assert metadata["runtime_sha256"] == runtime["runtime_sha256"]
    assert "available_qty" in prompt
    assert "final_quote_refresh" in prompt
    assert "investment_debate_state" in prompt
    assert "risk_debate_state" in prompt
    assert "buy_candidates" in prompt
    assert "today_actions" in metadata["required_structured_outputs"]
    assert "TauricResearch/TradingAgents" in metadata["upstream_references"]
