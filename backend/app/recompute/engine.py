"""Deterministic recompute orchestration for Phase M.

The engine is the single entry point that turns frozen PIT facts into a
canonical, hashable result per requested trading date.  It never reads
persisted Market/Candidate outputs as inputs and never writes production state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from ..research.replay import ReplayCase, content_hash
from .candidate import HistoricalCandidateRecomputeResult, recompute_candidate_dates
from .capability import build_recompute_capability_manifest
from .config import (
    RECOMPUTE_ENGINE_VERSION,
    RECOMPUTE_SCHEMA_VERSION,
    UNIVERSE_VERSION,
    RecomputeCapability,
    RecomputeScope,
)
from .context import HistoricalRecomputeContext
from .dataset import RecomputePitDataset, expected_calendar_dates, load_recompute_pit_dataset
from .market import HistoricalMarketRecomputeResult, recompute_market_dates
from .portfolio import HistoricalPortfolioRecomputeResult, recompute_portfolio_dates

_STATUS_BY_CAPABILITY = {
    RecomputeCapability.FULL_PIT_EQUIVALENT: "COMPLETED",
    RecomputeCapability.PARTIAL_PIT_RECOMPUTE: "PARTIAL_COMPLETED",
    RecomputeCapability.DIAGNOSTIC_ONLY: "DIAGNOSTIC_ONLY",
    RecomputeCapability.DATA_GAP: "DATA_GAP",
    RecomputeCapability.LEAKAGE_BLOCKED: "LEAKAGE_BLOCKED",
    RecomputeCapability.UNSUPPORTED: "UNSUPPORTED",
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


@dataclass
class DeterministicRecomputeResult:
    scope: str
    trade_date: date
    capability: str
    status: str
    parameter_version: str | None
    config_hash: str | None
    universe_version: str
    source_manifest_hash: str
    input_coverage: dict[str, Any]
    market_result: dict[str, Any] | None = None
    candidate_results: list[dict[str, Any]] = field(default_factory=list)
    portfolio_result: dict[str, Any] | None = None
    missing_inputs: list[str] = field(default_factory=list)
    partial_inputs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    deterministic_hash: str = ""
    source_ids: tuple[str, ...] = field(default_factory=tuple)
    query_count: int = 0
    engine_version: str = RECOMPUTE_ENGINE_VERSION
    schema_version: str = RECOMPUTE_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trade_date"] = self.trade_date.isoformat()
        payload["source_ids"] = sorted(self.source_ids)
        return payload

    def to_replay_cases(self) -> list[ReplayCase]:
        if self.scope == RecomputeScope.MARKET and self.market_result is not None:
            return [_market_case(self.market_result, self.source_ids)]
        if self.scope == RecomputeScope.CANDIDATE:
            return [_candidate_case(self, row) for row in self.candidate_results]
        if self.scope == RecomputeScope.PORTFOLIO_DECISION and self.portfolio_result is not None:
            return [_portfolio_case(self.portfolio_result, self.source_ids)]
        return []


def _market_case(result: Mapping[str, Any], source_ids: Iterable[str]) -> ReplayCase:
    trade_date = result["trade_date"]
    return ReplayCase(
        trade_date=trade_date,
        as_of=result.get("as_of"),
        scope="MARKET",
        replay_mode="DETERMINISTIC_RECOMPUTE",
        entity_id=f"recompute-market:{trade_date}",
        facts={
            "market_score": result.get("display_score") if result.get("display_score") is not None else result.get("raw_score"),
            "raw_score": result.get("raw_score"),
            "display_score": result.get("display_score"),
            "market_regime": result.get("regime"),
            "confidence": result.get("confidence"),
            "quality_status": result.get("quality_status"),
            "benchmark_index": result.get("median_index"),
            "benchmark_id": None,
            "score_config_version": result.get("score_config_version"),
            "calculation_version": result.get("calculation_version"),
            "canonical_observation": "EOD_15_10",
            "capability": result.get("capability"),
        },
        quality_status=str(result.get("quality_status") or "MISSING"),
        reason_codes=(),
        source_ids=tuple(sorted(source_ids)),
        coverage=result.get("coverage"),
    )


def _candidate_case(result: DeterministicRecomputeResult, row: Mapping[str, Any]) -> ReplayCase:
    trade_date = result.trade_date
    return ReplayCase(
        trade_date=trade_date,
        as_of=result.market_result.get("as_of") if result.market_result else None,
        scope="CANDIDATE",
        replay_mode="DETERMINISTIC_RECOMPUTE",
        entity_id=f"recompute-candidate:{trade_date}:{row.get('code')}",
        facts={
            "code": row.get("code"),
            "name": row.get("name"),
            "security_type": row.get("security_type"),
            "etf_category": row.get("etf_category"),
            "stage": row.get("stage"),
            "score": row.get("score"),
            "opportunity_score": row.get("opportunity_score"),
            "entry_score": row.get("entry_score"),
            "portfolio_fit_score": row.get("portfolio_fit_score"),
            "action_score": row.get("action_score"),
            "decision_edge": row.get("decision_edge"),
            "risk_reward_ratio": row.get("risk_reward_ratio"),
            "coverage": row.get("data_coverage"),
            "confidence": row.get("confidence"),
            "reference_price": row.get("reference_price"),
            "reference_price_basis": row.get("reference_price_basis"),
            "market_regime": row.get("market_regime"),
            "candidate_run_id": None,
            "candidate_score_id": None,
            "censored_sample": False,
            "market_available": bool(row.get("market_available", True)),
            "market_quality": row.get("market_quality") or "MISSING",
            "market_frozen": bool(row.get("market_frozen")),
            "funding_mode": row.get("funding_mode"),
            "quote_quality": row.get("quote_quality") or "MISSING",
            "quote_is_proxy": bool(row.get("quote_is_proxy")),
            "limit_up": bool(row.get("limit_up")),
            "limit_down": bool(row.get("limit_down")),
            "edge_vs_no_action": row.get("edge_vs_no_action"),
            "edge_vs_current_holdings": row.get("edge_vs_current_holdings"),
            "components": row.get("components") or {},
            "factor_audit": row.get("factor_audit") or [],
            "portfolio_fit_components": row.get("portfolio_fit") or {},
            "hard_cap_violation": bool((row.get("portfolio_fit") or {}).get("hard_cap_violation")),
            "held_baseline": (row.get("comparison") or {}).get("held_baseline"),
            "intraday": False,
        },
        quality_status=str(row.get("quality_status") or "MISSING"),
        reason_codes=tuple(row.get("blocking_reasons") or []),
        source_ids=tuple(sorted(result.source_ids)),
        coverage=row.get("data_coverage"),
    )


def _portfolio_case(result: Mapping[str, Any], source_ids: Iterable[str]) -> ReplayCase:
    trade_date = result["trade_date"]
    return ReplayCase(
        trade_date=trade_date,
        as_of=result.get("as_of"),
        scope="PORTFOLIO_DECISION",
        replay_mode="DETERMINISTIC_RECOMPUTE",
        entity_id=f"recompute-portfolio:{trade_date}",
        facts={
            "portfolio_snapshot_id": result.get("portfolio_snapshot_id"),
            "portfolio_action": result.get("final_action"),
            "market_regime": (result.get("market") or {}).get("regime"),
            "market_quality": (result.get("market") or {}).get("quality_status"),
            "cash_ratio": (result.get("portfolio_context") or {}).get("cash_ratio"),
            "candidate_action_count": len(result.get("candidate_actions") or []),
            "blocking_reasons": result.get("blocking_reasons") or [],
            "coverage": result.get("coverage"),
            "excess_return": None,
            "status": result.get("status"),
            "quality_status": result.get("quality_status"),
        },
        quality_status=str(result.get("quality_status") or "MISSING"),
        reason_codes=tuple(result.get("blocking_reasons") or []),
        source_ids=tuple(sorted(source_ids)),
        coverage=result.get("coverage"),
    )


def _cohort_capability(
    results: Iterable[DeterministicRecomputeResult],
) -> str:
    values = {str(result.capability) for result in results}
    if not values:
        return str(RecomputeCapability.DATA_GAP)
    if values == {str(RecomputeCapability.FULL_PIT_EQUIVALENT)}:
        return str(RecomputeCapability.FULL_PIT_EQUIVALENT)
    for blocked in (
        RecomputeCapability.DATA_GAP,
        RecomputeCapability.LEAKAGE_BLOCKED,
        RecomputeCapability.UNSUPPORTED,
    ):
        if str(blocked) in values:
            return str(blocked)
    return str(RecomputeCapability.PARTIAL_PIT_RECOMPUTE)


def _result_hash(payload: dict[str, Any]) -> str:
    stable = {key: value for key, value in payload.items() if key not in {"deterministic_hash", "query_count"}}
    return content_hash(stable)


def recompute_deterministic_scope(
    db,
    *,
    context: HistoricalRecomputeContext,
    parameter_snapshot: Mapping[str, Any] | None = None,
    config_hash: str | None = None,
    dates: Iterable[date] | None = None,
) -> list[DeterministicRecomputeResult]:
    """Recompute one research scope for the context's requested date cohort."""

    scope = str(context.scope).upper()
    manifest = build_recompute_capability_manifest(
        db,
        scope=scope,
        start_date=context.start_date,
        end_date=context.end_date,
        market="CN",
        checkpoint="EOD",
        parameter_version=context.parameter_set_version,
        config_hash=config_hash,
        universe_version=context.universe_version,
    )
    include_candidates = scope in {
        RecomputeScope.CANDIDATE,
        RecomputeScope.CANDIDATE_STOCK,
        RecomputeScope.CANDIDATE_ETF,
        RecomputeScope.PORTFOLIO_DECISION,
    }
    # CANDIDATE recompute needs the PIT portfolio state when a portfolio is
    # known: held securities are excluded from the universe and Portfolio Fit
    # compares against the historical snapshot. Without a portfolio_id the
    # candidate dataset stays empty-held and is labelled PARTIAL/DIAGNOSTIC.
    include_portfolio = (
        scope == RecomputeScope.PORTFOLIO_DECISION
        or (
            scope in {
                RecomputeScope.CANDIDATE,
                RecomputeScope.CANDIDATE_STOCK,
                RecomputeScope.CANDIDATE_ETF,
            }
            and context.portfolio_id is not None
        )
    )
    dataset = load_recompute_pit_dataset(
        db,
        start_date=context.start_date,
        end_date=context.end_date,
        market="CN",
        lookback_trading_days=750,
        include_candidates=include_candidates,
        include_portfolio=include_portfolio,
        portfolio_id=context.portfolio_id,
    )
    requested = list(dates) if dates is not None else expected_calendar_dates(dataset)
    if not requested:
        return []
    capability = str(manifest.get("capability") or RecomputeCapability.PARTIAL_PIT_RECOMPUTE)
    blocked = capability in {
        str(RecomputeCapability.DATA_GAP),
        str(RecomputeCapability.LEAKAGE_BLOCKED),
        str(RecomputeCapability.UNSUPPORTED),
    }
    if blocked:
        return [
            _build_result(
                dataset=dataset,
                manifest=manifest,
                context=context,
                config_hash=config_hash,
                trade_date=day,
                capability=capability,
                parameter_snapshot=parameter_snapshot,
            )
            for day in requested
        ]

    market_results: list[HistoricalMarketRecomputeResult] = []
    candidate_results: list[HistoricalCandidateRecomputeResult] = []
    portfolio_results: list[HistoricalPortfolioRecomputeResult] = []
    if scope in {
        RecomputeScope.MARKET,
        RecomputeScope.CANDIDATE,
        RecomputeScope.CANDIDATE_STOCK,
        RecomputeScope.CANDIDATE_ETF,
        RecomputeScope.PORTFOLIO_DECISION,
    }:
        market_results = recompute_market_dates(
            dataset,
            dates=requested,
            parameter_snapshot=parameter_snapshot,
        )
    if include_candidates:
        candidate_results = recompute_candidate_dates(
            dataset,
            dates=requested,
            market_results=market_results,
            parameter_snapshot=parameter_snapshot,
        )
    if include_portfolio:
        portfolio_results = recompute_portfolio_dates(
            dataset,
            dates=requested,
            market_results=market_results,
            candidate_results=candidate_results,
            parameter_snapshot=parameter_snapshot,
        )

    market_by_date = {result.trade_date: result for result in market_results}
    candidate_by_date = {result.trade_date: result for result in candidate_results}
    portfolio_by_date = {result.trade_date: result for result in portfolio_results}
    results: list[DeterministicRecomputeResult] = []
    for day in requested:
        market = market_by_date.get(day)
        candidates = candidate_by_date.get(day)
        portfolio = portfolio_by_date.get(day)
        date_capability = capability
        if market is not None:
            date_capability = market.capability
        if candidates is not None and candidates.capability != RecomputeCapability.FULL_PIT_EQUIVALENT:
            date_capability = candidates.capability
        if portfolio is not None and portfolio.capability != RecomputeCapability.FULL_PIT_EQUIVALENT:
            date_capability = portfolio.capability
        results.append(_build_result(
            dataset=dataset,
            manifest=manifest,
            context=context,
            config_hash=config_hash,
            trade_date=day,
            capability=date_capability,
            parameter_snapshot=parameter_snapshot,
            market_result=market.as_dict() if market else None,
            candidate_result=candidates.as_dict() if candidates else None,
            portfolio_result=portfolio.as_dict() if portfolio else None,
        ))
    return results


def _build_result(
    *,
    dataset: RecomputePitDataset,
    manifest: Mapping[str, Any],
    context: HistoricalRecomputeContext,
    config_hash: str | None,
    trade_date: date,
    capability: str,
    parameter_snapshot: Mapping[str, Any] | None,
    market_result: dict[str, Any] | None = None,
    candidate_result: dict[str, Any] | None = None,
    portfolio_result: dict[str, Any] | None = None,
) -> DeterministicRecomputeResult:
    source_ids = tuple(dataset.source_ids())
    result = DeterministicRecomputeResult(
        scope=str(context.scope).upper(),
        trade_date=trade_date,
        capability=capability,
        status=_STATUS_BY_CAPABILITY.get(capability, "PARTIAL_COMPLETED"),
        parameter_version=context.parameter_set_version,
        config_hash=config_hash,
        universe_version=context.universe_version,
        source_manifest_hash=content_hash(source_ids),
        input_coverage={
            "market": (market_result or {}).get("coverage"),
            "universe": (market_result or {}).get("universe", {}).get("coverage"),
            "candidate": (candidate_result or {}).get("coverage"),
        },
        market_result=market_result,
        candidate_results=list((candidate_result or {}).get("candidates") or []),
        portfolio_result=portfolio_result,
        missing_inputs=list(manifest.get("missing_inputs") or []),
        partial_inputs=list(manifest.get("partial_inputs") or []),
        limitations=list(manifest.get("limitations") or []),
        source_ids=source_ids,
        query_count=dataset.query_count,
    )
    result.deterministic_hash = _result_hash(result.as_dict())
    return result


def cohort_recompute_summary(results: Iterable[DeterministicRecomputeResult]) -> dict[str, Any]:
    rows = list(results)
    return {
        "capability": _cohort_capability(rows),
        "date_count": len(rows),
        "query_count": sum(int(row.query_count or 0) for row in rows),
        "deterministic_hash": content_hash(
            [(row.trade_date.isoformat(), row.deterministic_hash) for row in rows]
        ),
        "universe_version": rows[0].universe_version if rows else UNIVERSE_VERSION,
    }


__all__ = [
    "DeterministicRecomputeResult",
    "cohort_recompute_summary",
    "recompute_deterministic_scope",
]
