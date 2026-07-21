"""Load the versioned analysis contract from the repository Skill directory."""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import event

from ..v2_models import AnalysisRun


REQUIRED_STRUCTURED_OUTPUTS = (
    "evidence_pack",
    "quality_gate",
    "investment_debate_state",
    "research_manager_verdict",
    "trader_proposal",
    "risk_revision",
    "risk_debate_state",
    "portfolio_manager_final",
    "today_actions",
    "buy_candidates",
    "rebalance_plan",
    "checkpoint_plan",
)


class SkillRuntimeError(RuntimeError):
    pass


def _candidate_paths() -> list[Path]:
    configured = os.getenv("HOLDINGS_SKILL_DIR")
    current = Path(__file__).resolve()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    # Docker layout: /app/app/services -> /app/skill
    candidates.append(current.parents[2] / "skill" / "tradingagents-holdings-advisor")
    # Source layout: <repo>/backend/app/services -> <repo>/skill
    candidates.append(current.parents[3] / "skill" / "tradingagents-holdings-advisor")
    return candidates


@lru_cache(maxsize=1)
def load_skill_runtime() -> dict[str, Any]:
    for directory in _candidate_paths():
        path = directory / "runtime.json"
        if not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillRuntimeError(f"Invalid holdings skill runtime file: {path}") from exc
        if not isinstance(payload, dict) or not payload.get("version") or not payload.get("rules"):
            raise SkillRuntimeError(f"Incomplete holdings skill runtime file: {path}")
        payload = dict(payload)
        payload["runtime_sha256"] = hashlib.sha256(raw).hexdigest()
        payload["runtime_path"] = str(path)
        return payload
    searched = ", ".join(str(path) for path in _candidate_paths())
    raise SkillRuntimeError(f"Holdings skill runtime.json was not found. Searched: {searched}")


def runtime_prompt() -> str:
    runtime = load_skill_runtime()
    rules = "\n".join(f"- {rule}" for rule in runtime["rules"])
    phases = " → ".join(runtime.get("phases", []))
    checkpoints = "\n".join(
        f"- {name}: {description}" for name, description in runtime.get("checkpoints", {}).items()
    )
    required_outputs = "\n".join(f"- {name}" for name in REQUIRED_STRUCTURED_OUTPUTS)
    return (
        f"Skill: {runtime['name']} v{runtime['version']}\n"
        f"Prompt version: {runtime.get('prompt_version', '-')}\n"
        f"Runtime SHA256: {runtime['runtime_sha256']}\n\n"
        f"Required phases: {phases}\n\n"
        f"Core rules:\n{rules}\n\n"
        f"Required structured outputs:\n{required_outputs}\n\n"
        f"Checkpoint guidance:\n{checkpoints}"
    )


def runtime_metadata() -> dict[str, Any]:
    runtime = load_skill_runtime()
    return {
        "name": runtime["name"],
        "version": runtime["version"],
        "prompt_version": runtime.get("prompt_version"),
        "runtime_sha256": runtime["runtime_sha256"],
        "upstream_references": runtime.get("upstream_references", []),
        "required_structured_outputs": list(REQUIRED_STRUCTURED_OUTPUTS),
    }


@event.listens_for(AnalysisRun, "before_insert")
def _attach_skill_runtime_metadata(_mapper, _connection, target: AnalysisRun) -> None:
    """Persist the exact Skill version and hash used for every report."""
    payload = dict(target.structured_result_json or {})
    payload["skill_runtime"] = runtime_metadata()
    target.structured_result_json = payload
