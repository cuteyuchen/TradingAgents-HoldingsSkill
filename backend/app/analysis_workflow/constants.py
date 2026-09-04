"""Central workflow/audit constants for V3-CORE-1.

String values stay lowercase to match existing AnalysisJob status conventions,
except artifact types and checkpoint names which are stable uppercase codes.
"""
from __future__ import annotations

from dataclasses import dataclass


WORKFLOW_VERSION = "v3-core-1"


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    TERMINAL = (COMPLETED, BLOCKED, FAILED, CANCELLED, INTERRUPTED)
    REPORTABLE = (COMPLETED, BLOCKED)


class StageStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class NodeStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class AttemptStatus:
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Criticality:
    MANDATORY = "mandatory"
    IMPORTANT = "important"
    OPTIONAL = "optional"


class ArtifactType:
    INPUT = "INPUT"
    EVIDENCE = "EVIDENCE"
    PROMPT_TEMPLATE = "PROMPT_TEMPLATE"
    RENDERED_PROMPT = "RENDERED_PROMPT"
    MODEL_RAW_OUTPUT = "MODEL_RAW_OUTPUT"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    QUALITY_GATE = "QUALITY_GATE"
    CLAIMS = "CLAIMS"
    CHECKPOINT = "CHECKPOINT"
    ERROR = "ERROR"
    FINAL_DECISION = "FINAL_DECISION"
    MARKET_SNAPSHOT = "MARKET_SNAPSHOT"
    PORTFOLIO_SNAPSHOT = "PORTFOLIO_SNAPSHOT"


class ClaimStatus:
    OPEN = "open"
    ADDRESSED = "addressed"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIALLY_ACCEPTED = "partially_accepted"


class CheckpointName:
    CONTEXT_READY = "CONTEXT_READY"
    MARKET_READY = "MARKET_READY"
    ANALYSTS_DONE = "ANALYSTS_DONE"
    QUALITY_GATE_DONE = "QUALITY_GATE_DONE"
    DEBATE_DONE = "DEBATE_DONE"
    RESEARCH_DONE = "RESEARCH_DONE"
    TRADER_DONE = "TRADER_DONE"
    RISK_DONE = "RISK_DONE"
    CANDIDATES_DONE = "CANDIDATES_DONE"
    PORTFOLIO_DONE = "PORTFOLIO_DONE"
    FINALIZED = "FINALIZED"


class DebateType:
    INVESTMENT = "investment"
    RISK = "risk"


@dataclass(frozen=True)
class NodeSpec:
    node_key: str
    node_type: str
    agent_role: str
    criticality: str
    retryable: bool = False
    resumable: bool = True
    llm: bool = False
    max_attempts: int = 1


@dataclass(frozen=True)
class PhaseSpec:
    phase_key: str
    phase_order: int
    display_name: str
    criticality: str
    nodes: tuple[NodeSpec, ...]
    checkpoint: str | None = None
    updates_job_stage: bool = True


def _node(
    node_key: str,
    *,
    node_type: str,
    agent_role: str,
    criticality: str,
    llm: bool = False,
    retryable: bool = False,
    resumable: bool = True,
) -> NodeSpec:
    return NodeSpec(
        node_key=node_key,
        node_type=node_type,
        agent_role=agent_role,
        criticality=criticality,
        retryable=retryable,
        resumable=resumable,
        llm=llm,
    )


LEGACY_PHASES: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        phase_key="context_loading",
        phase_order=10,
        display_name="Context Loading",
        criticality=Criticality.MANDATORY,
        checkpoint=CheckpointName.CONTEXT_READY,
        nodes=(_node("context_loader", node_type="context", agent_role="system", criticality=Criticality.MANDATORY),),
    ),
    PhaseSpec(
        phase_key="market_collecting",
        phase_order=20,
        display_name="Market Collecting",
        criticality=Criticality.MANDATORY,
        checkpoint=CheckpointName.MARKET_READY,
        nodes=(_node("market_snapshot_collector", node_type="market", agent_role="system", criticality=Criticality.MANDATORY),),
    ),
    PhaseSpec(
        phase_key="analysts_running",
        phase_order=30,
        display_name="Analyst Team",
        criticality=Criticality.IMPORTANT,
        checkpoint=CheckpointName.ANALYSTS_DONE,
        nodes=(_node("analyst_team_legacy", node_type="analyst", agent_role="analyst_team", criticality=Criticality.IMPORTANT, llm=True, retryable=True),),
    ),
    PhaseSpec(
        phase_key="quality_gate",
        phase_order=40,
        display_name="Quality Gate",
        criticality=Criticality.MANDATORY,
        checkpoint=CheckpointName.QUALITY_GATE_DONE,
        nodes=(_node("quality_gate", node_type="gate", agent_role="system", criticality=Criticality.MANDATORY, resumable=False),),
    ),
    PhaseSpec(
        phase_key="investment_debate",
        phase_order=50,
        display_name="Investment Debate",
        criticality=Criticality.IMPORTANT,
        checkpoint=CheckpointName.DEBATE_DONE,
        nodes=(_node("investment_debate_legacy", node_type="debate", agent_role="bull_bear", criticality=Criticality.IMPORTANT, llm=True, retryable=True),),
    ),
    PhaseSpec(
        phase_key="research_verdict",
        phase_order=60,
        display_name="Research Manager",
        criticality=Criticality.MANDATORY,
        checkpoint=CheckpointName.RESEARCH_DONE,
        nodes=(_node("research_manager", node_type="manager", agent_role="research_manager", criticality=Criticality.MANDATORY, llm=True, retryable=True),),
    ),
    PhaseSpec(
        phase_key="trader_proposal",
        phase_order=70,
        display_name="Trader Proposal",
        criticality=Criticality.MANDATORY,
        checkpoint=CheckpointName.TRADER_DONE,
        nodes=(_node("trader", node_type="trader", agent_role="trader", criticality=Criticality.MANDATORY, llm=True, retryable=True),),
    ),
    PhaseSpec(
        phase_key="risk_revision",
        phase_order=80,
        display_name="Risk Revision",
        criticality=Criticality.MANDATORY,
        nodes=(
            _node("risk_manager", node_type="risk", agent_role="risk_manager", criticality=Criticality.MANDATORY, llm=True, retryable=True),
            _node("trader_revision", node_type="trader", agent_role="trader", criticality=Criticality.IMPORTANT, llm=True, retryable=True),
        ),
    ),
    PhaseSpec(
        phase_key="risk_debate",
        phase_order=90,
        display_name="Risk Debate",
        criticality=Criticality.IMPORTANT,
        checkpoint=CheckpointName.RISK_DONE,
        nodes=(_node("risk_debate_legacy", node_type="debate", agent_role="risk_committee", criticality=Criticality.IMPORTANT, llm=True, retryable=True),),
    ),
    PhaseSpec(
        phase_key="final_quote_refresh",
        phase_order=100,
        display_name="Final Quote Refresh",
        criticality=Criticality.MANDATORY,
        nodes=(_node("final_quote_refresh", node_type="market", agent_role="system", criticality=Criticality.MANDATORY),),
    ),
    PhaseSpec(
        phase_key="candidate_screening",
        phase_order=110,
        display_name="Candidate Screening",
        criticality=Criticality.MANDATORY,
        checkpoint=CheckpointName.CANDIDATES_DONE,
        nodes=(
            _node("deterministic_candidate_gate", node_type="gate", agent_role="candidate_engine", criticality=Criticality.MANDATORY, resumable=False),
            _node("candidate_llm_review", node_type="review", agent_role="analyst_team", criticality=Criticality.OPTIONAL, llm=True, retryable=True),
        ),
    ),
    PhaseSpec(
        phase_key="portfolio_synthesis",
        phase_order=120,
        display_name="Portfolio Manager",
        criticality=Criticality.MANDATORY,
        nodes=(_node("portfolio_manager", node_type="manager", agent_role="portfolio_manager", criticality=Criticality.MANDATORY, llm=True, retryable=True),),
    ),
    PhaseSpec(
        phase_key="portfolio_decision_gate",
        phase_order=130,
        display_name="Portfolio Decision Gate",
        criticality=Criticality.MANDATORY,
        checkpoint=CheckpointName.PORTFOLIO_DONE,
        updates_job_stage=False,
        nodes=(_node("portfolio_decision_gate", node_type="gate", agent_role="system", criticality=Criticality.MANDATORY, resumable=False),),
    ),
    PhaseSpec(
        phase_key="report_rendering",
        phase_order=140,
        display_name="Report Rendering",
        criticality=Criticality.MANDATORY,
        checkpoint=CheckpointName.FINALIZED,
        nodes=(_node("report_renderer", node_type="render", agent_role="system", criticality=Criticality.MANDATORY),),
    ),
)

PHASE_BY_KEY = {item.phase_key: item for item in LEGACY_PHASES}
NODE_BY_KEY = {node.node_key: node for phase in LEGACY_PHASES for node in phase.nodes}
NODE_PHASE_BY_KEY = {node.node_key: phase.phase_key for phase in LEGACY_PHASES for node in phase.nodes}


def phase_spec(phase_key: str) -> PhaseSpec:
    spec = PHASE_BY_KEY.get(phase_key)
    if spec is not None:
        return spec
    return PhaseSpec(
        phase_key=phase_key,
        phase_order=900,
        display_name=phase_key.replace("_", " ").title(),
        criticality=Criticality.OPTIONAL,
        nodes=(),
    )


def node_spec(node_key: str) -> NodeSpec:
    spec = NODE_BY_KEY.get(node_key)
    if spec is not None:
        return spec
    return NodeSpec(
        node_key=node_key,
        node_type="legacy",
        agent_role="unknown",
        criticality=Criticality.OPTIONAL,
    )
