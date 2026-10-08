"""Mode-specific topology over the existing NodeSpec/PhaseSpec contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from ..decision_contract import canonicalize_analysis_mode
from .constants import (
    DEFAULT_NODE_MAX_ATTEMPTS,
    LEGACY_PHASES,
    Criticality,
    NodeSpec,
    PhaseSpec,
)

ANALYST_ROLES = (
    "market_analyst",
    "sentiment_analyst",
    "news_analyst",
    "fundamentals_analyst",
    "policy_analyst",
    "capital_flow_analyst",
    "lockup_supply_analyst",
)
RISK_ROLES = ("aggressive_risk_agent", "neutral_risk_agent", "conservative_risk_agent")
DEBATE_NODES = ("bull_round_1", "bear_round_1", "bull_round_2", "bear_round_2", "claim_resolver")


def _agent(key: str, kind: str, *, dependencies=(), group=None, optional=False) -> NodeSpec:
    return NodeSpec(
        node_key=key,
        node_type=kind,
        agent_role=key.split("_round_")[0],
        criticality=Criticality.OPTIONAL if optional else Criticality.IMPORTANT,
        llm=True,
        retryable=True,
        max_attempts=DEFAULT_NODE_MAX_ATTEMPTS,
        dependencies=tuple(dependencies),
        parallel_group=group,
    )


@dataclass(frozen=True)
class WorkflowPlan:
    analysis_mode: str
    phases: tuple[PhaseSpec, ...]

    def phase(self, key: str) -> PhaseSpec:
        return next(phase for phase in self.phases if phase.phase_key == key)

    def node(self, key: str) -> NodeSpec:
        return next(node for phase in self.phases for node in phase.nodes if node.node_key == key)

    def has_phase(self, key: str) -> bool:
        return any(phase.phase_key == key for phase in self.phases)

    def payload(self) -> dict:
        return asdict(self)

    def validate(self) -> None:
        phases_seen: set[str] = set()
        nodes_seen: set[str] = set()
        for phase in self.phases:
            if phase.phase_key in phases_seen or not set(phase.dependencies) <= phases_seen:
                raise ValueError(f"invalid_phase_dependencies:{phase.phase_key}")
            phases_seen.add(phase.phase_key)
            for node in phase.nodes:
                if node.node_key in nodes_seen or not set(node.dependencies) <= nodes_seen:
                    raise ValueError(f"invalid_node_dependencies:{node.node_key}")
                nodes_seen.add(node.node_key)


def build_workflow_plan(mode: str) -> WorkflowPlan:
    mode = canonicalize_analysis_mode(mode)
    roles = ANALYST_ROLES
    if mode == "fast":
        roles = ("market_analyst", "capital_flow_analyst")
    elif mode == "standard":
        roles = ("market_analyst", "news_analyst", "fundamentals_analyst", "policy_analyst", "capital_flow_analyst")

    phases: list[PhaseSpec] = []
    previous_nodes: tuple[str, ...] = ()
    for source in LEGACY_PHASES:
        if mode != "deep" and source.phase_key in {"investment_debate", "risk_debate"}:
            continue
        nodes = source.nodes
        if source.phase_key == "analysts_running":
            nodes = tuple(
                _agent(key, "analyst", dependencies=previous_nodes, group="analysts", optional=key == "lockup_supply_analyst")
                for key in roles
            )
            if mode == "fast":
                nodes += (NodeSpec(
                    "trigger_recheck", "context", "system", Criticality.MANDATORY,
                    dependencies=tuple(roles),
                ),)
        elif source.phase_key == "investment_debate":
            result = []
            dependencies = previous_nodes
            for key in DEBATE_NODES:
                node = _agent(key, "resolver" if key == "claim_resolver" else "debate", dependencies=dependencies)
                # A real exchange cannot continue by inventing a missing opponent.
                result.append(replace(node, criticality=Criticality.MANDATORY))
                dependencies = (key,)
            nodes = tuple(result)
        elif source.phase_key == "risk_debate":
            nodes = tuple(_agent(key, "risk", dependencies=previous_nodes, group="risk_agents") for key in RISK_ROLES)
            nodes += (replace(
                _agent("risk_synthesis", "manager", dependencies=RISK_ROLES),
                criticality=Criticality.MANDATORY,
            ),)
        else:
            nodes = tuple(
                replace(
                    node,
                    dependencies=previous_nodes if index == 0 else (nodes[index - 1].node_key,),
                    conditional=node.node_key in {"trader_revision", "candidate_llm_review"},
                )
                for index, node in enumerate(nodes)
            )
        if mode == "fast" and source.phase_key == "research_verdict":
            nodes = (replace(nodes[0], node_type="context", llm=False, retryable=False, max_attempts=1),)
        nodes = tuple(replace(node, analysis_modes=(mode,)) for node in nodes)
        phases.append(replace(
            source,
            nodes=nodes,
            dependencies=(phases[-1].phase_key,) if phases else (),
        ))
        previous_nodes = tuple(node.node_key for node in nodes)
    plan = WorkflowPlan(mode, tuple(phases))
    plan.validate()
    return plan
