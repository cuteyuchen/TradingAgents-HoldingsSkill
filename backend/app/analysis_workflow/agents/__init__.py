"""Single-role prompts and validated public conclusions, never private reasoning."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..dag import ANALYST_ROLES, DEBATE_NODES, RISK_ROLES
from ..resume import hash_input

PROMPT_VERSION = "v3-core-3.1"

ROLE_BOUNDARIES = {
    "market_analyst": (
        "Market/technical analyst: indices, trend, price/volume, support/resistance, "
        "volatility, turnover, breadth and VPA only. Do not redo news or fundamentals."
    ),
    "sentiment_analyst": (
        "Sentiment analyst: advancing/declining counts, price-limit breadth, attention, "
        "diffusion, style, risk appetite and market heat. Do not invent social data."
    ),
    "news_analyst": (
        "News analyst: company/industry news, announcements, dated events and source reliability. "
        "Explicitly label fact, inference and rumor in evidence_type; retain publication time and source."
    ),
    "fundamentals_analyst": (
        "Fundamentals analyst: reports, revenue, profit, cash flow, ROE, valuation, "
        "fundamental changes and ETF structure. Missing financials are gaps, never zero."
    ),
    "policy_analyst": (
        "Policy analyst: dated policy/regulation, macro policy, industry support/restrictions, "
        "and direct impact on these holdings/candidates. Distinguish policy facts from interpretation."
    ),
    "capital_flow_analyst": (
        "Hot Money Tracker/capital-flow analyst: main/large-order flows, liquidity, "
        "industry flow and Dragon-Tiger Board when present. All fund-flow estimates are "
        "provider-derived, not observed investor identities or verified institutional trades."
    ),
    "lockup_supply_analyst": (
        "Lockup Watcher/supply-risk analyst: lockups, reductions, shareholder changes, "
        "pledges and future selling pressure. Missing calendars are unknown, not no risk."
    ),
    "bull": "Bull researcher only: strongest evidence-supported bullish claims and rebuttals. Never speak for Bear.",
    "bear": "Bear researcher only: evidence-supported counterexamples, risks and invalidation conditions. Never speak for Bull.",
    "aggressive_risk_agent": (
        "Aggressive risk agent only: opportunity cost, trend persistence, position efficiency "
        "and missed-opportunity risk. Hard caps remain non-negotiable."
    ),
    "neutral_risk_agent": (
        "Neutral risk agent only: risk/reward balance, evidence strength and portfolio stability. "
        "Never relax deterministic constraints."
    ),
    "conservative_risk_agent": (
        "Conservative risk agent only: drawdown, liquidity, data gaps, concentration and extreme "
        "scenarios. No autonomous sell instruction."
    ),
    "claim_resolver": (
        "Claim resolver only: assess each supplied claim as ACCEPTED, PARTIALLY_ACCEPTED, "
        "REJECTED or UNRESOLVED. Do not create claims, change evidence, or generate orders."
    ),
    "risk_synthesis": (
        "Risk synthesis only: summarize the three real risk-agent outputs and their failures, "
        "consensus, disagreements, hard concerns, exposure and unresolved risks. "
        "Missing agents are not consensus. No orders; Portfolio Decision Gate remains authoritative."
    ),
}


class PublicOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Finding(PublicOutput):
    instrument: str | None = None
    statement: str = Field(min_length=1)
    direction: Literal["positive", "negative", "neutral"]
    importance: Literal["high", "medium", "low"]
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str]
    evidence_type: Literal["fact", "inference", "rumor", "provider_derived"]
    published_at: str | None = None
    source: str | None = None
    source_reliability: str | None = None


class AnalystReport(PublicOutput):
    role: str
    scope: Literal["portfolio"]
    summary: str = Field(min_length=1)
    findings: list[Finding]
    portfolio_risks: list[str]
    data_gaps: list[str]
    quality_grade: Literal["A", "B", "C", "D", "F"]
    data_table: list[dict[str, Any]] = Field(default_factory=list)
    missing_checklist_fields: list[str] = Field(default_factory=list)


class Claim(PublicOutput):
    statement: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)
    target_claim_ids: list[str]
    parent_claim_id: str | None = None


class Position(PublicOutput):
    role: str
    summary: str = Field(min_length=1)
    claims: list[Claim]


class Resolution(PublicOutput):
    claim_id: str
    status: Literal["ACCEPTED", "PARTIALLY_ACCEPTED", "REJECTED", "UNRESOLVED"]
    rationale_summary: str


class Resolutions(PublicOutput):
    resolutions: list[Resolution]
    summary: str


class RiskSynthesis(PublicOutput):
    consensus: list[str]
    disagreements: list[str]
    hard_concerns: list[str]
    recommended_exposure: float | None = Field(ge=0, le=1)
    unresolved_risks: list[str]
    summary: str


def role_for_node(key: str) -> str:
    return key.split("_round_")[0]


def output_schema(key: str):
    if key in ANALYST_ROLES:
        return AnalystReport
    if key == "claim_resolver":
        return Resolutions
    if key == "risk_synthesis":
        return RiskSynthesis
    return Position


def prompt_metadata(key: str) -> dict[str, str]:
    return {"role": role_for_node(key), "version": PROMPT_VERSION, "template_id": f"holdings.{key}"}


def prompt_manifest() -> dict[str, Any]:
    return {
        "version": PROMPT_VERSION,
        "templates": {
            key: hash_input(agent_instruction(key))
            for key in (*ANALYST_ROLES, *DEBATE_NODES, *RISK_ROLES, "risk_synthesis")
        },
    }


def agent_instruction(key: str) -> str:
    role = role_for_node(key)
    common = (
        f"Independent Agent Node: {key}\nRole: {role}\n{ROLE_BOUNDARIES[role]}\n"
        "Consume ONLY the supplied frozen evidence snapshot. No fetching, refreshing or invented facts. "
        "evidence_refs must be exact entries from the supplied evidence_refs list. "
        "Report conclusions in Chinese, with public rationale summaries only. "
        "Never output hidden chain-of-thought, scratchpads, orders or other roles' answers. "
        "Respect T+1, available quantity, cash, lot size, hard caps and data quality. "
        "NO_ACTION and zero candidates are valid. Return one structured JSON object.\n"
    )
    if key in ANALYST_ROLES:
        common += (
            "Use the stable analyst envelope. Include a data_table and missing_checklist_fields. "
            "Cover your Skill checklist, with at least 200 characters of substantive public conclusions "
            "when evidence permits; never pad missing evidence to obtain a grade. "
            "For core holdings distinguish short tactical conditions from medium-term thesis. "
        )
    elif "_round_" in key:
        common += (
            "Read opponent_claims: these are the opponent's real preceding response, not a hypothetical one. "
            "Use target_claim_ids/parent_claim_id from prior_claims only. When an opponent has claims, "
            "address at least one. The server assigns stable claim IDs. "
        )
    common += "\nRequired JSON schema:\n" + json.dumps(output_schema(key).model_json_schema(), ensure_ascii=False)
    return common


def validate_output(key: str, raw: Any, payload: dict[str, Any]) -> bool:
    try:
        parsed = output_schema(key).model_validate(raw).model_dump()
    except (ValidationError, TypeError, ValueError):
        return False
    if "role" in parsed and parsed["role"] != role_for_node(key):
        return False
    references = set(payload.get("evidence_refs") or [])
    for item in parsed.get("findings", parsed.get("claims", [])):
        if not set(item["evidence_refs"]) <= references:
            return False
        if key == "capital_flow_analyst" and item.get("evidence_type") != "provider_derived":
            return False
    prior_ids = {claim["claim_id"] for claim in payload.get("prior_claims") or []}
    for claim in parsed.get("claims", []):
        if not set(claim["target_claim_ids"]) <= prior_ids:
            return False
        if claim["parent_claim_id"] is not None and claim["parent_claim_id"] not in prior_ids:
            return False
    opponent_ids = {claim["claim_id"] for claim in payload.get("opponent_claims") or []}
    if "_round_" in key and opponent_ids and not any(opponent_ids.intersection(claim["target_claim_ids"]) for claim in parsed.get("claims", [])):
        return False
    if key == "claim_resolver":
        ids = [item["claim_id"] for item in parsed["resolutions"]]
        if set(ids) != prior_ids or len(ids) != len(set(ids)):
            return False
    return True


def normalise_output(key: str, raw: dict[str, Any], *, attempt_no: int) -> dict[str, Any]:
    value = output_schema(key).model_validate(raw).model_dump()
    if "claims" in value:
        speaker = role_for_node(key).replace("_risk_agent", "")
        stance = {"bull": "bullish", "bear": "bearish", "aggressive": "risk_accept", "neutral": "risk_balance", "conservative": "risk_avoid"}[speaker]
        prefix = "RISK" if key in RISK_ROLES else "INV"
        offset = (int(key.rsplit("_", 1)[-1]) - 1) * 100 if "_round_" in key else 0
        for index, claim in enumerate(value["claims"], 1):
            claim["claim_id"] = f"{prefix}-{speaker.upper()}-{offset + index:03d}" + (f"-A{attempt_no}" if attempt_no > 1 else "")
            claim.update(speaker=speaker, stance=stance, status="open", claim=claim["statement"], evidence=claim["evidence_refs"])
    if key == "capital_flow_analyst":
        value["data_attribute"] = "provider_derived"
    return value
