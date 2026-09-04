"""Historical EOD Candidate deterministic recompute.

Every factor is rebuilt from the frozen PIT dataset.  Persisted
``CandidateRun`` / ``CandidateScore`` rows are never read as inputs.  Flow and
Industry history are absent in Phase M, so a missing factor is reported as
unavailable instead of zero and stock capability stays PARTIAL.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping

from ..candidates import service as candidate_service
from ..candidates.config import CandidateConfig, DEFAULT_CONFIG
from ..candidates.etf_score import score_etf_candidate
from ..candidates.ranking import rank_candidates, take_stage_limits
from ..candidates.stock_score import score_stock_candidate
from ..governance.registry import candidate_config_from_snapshot, normalize_snapshot
from ..history.universe import resolve_equity_universe_from_facts
from ..market.codes import normalize_security_code
from .config import KNOWN_MISSING_FACTORS, RecomputeCapability
from .dataset import RecomputePitDataset, eod_cutoff
from .portfolio import build_historical_portfolio_state


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _quote_map(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        code = normalize_security_code(str(raw.get("code") or ""))
        if not code:
            continue
        quote = dict(raw)
        quote["code"] = code
        quote["source"] = "daily_bar_cache_close_proxy"
        quote["quote_is_proxy"] = True
        quote["provenance"] = "EOD_CLOSE_PROXY"
        quote["price_basis"] = "QFQ"
        result[code] = quote
    return result


def _metadata_for(
    code: str,
    *,
    day: date,
    cutoff: datetime,
    dataset: RecomputePitDataset,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    fundamental = dataset.fundamental_by_code(day, cutoff).get(code)
    if fundamental and fundamental.get("available"):
        metadata["fundamental"] = {
            "roe": fundamental.get("roe"),
            "revenue_yoy": fundamental.get("revenue_yoy"),
            "profit_yoy": fundamental.get("net_profit_yoy"),
            "operating_cash_flow": fundamental.get("operating_cash_flow"),
            "gross_margin": fundamental.get("gross_margin"),
            "available_at": fundamental.get("visible_at"),
        }
    valuation = dataset.valuation_by_code(day, cutoff).get(code)
    if valuation:
        metadata["valuation"] = {
            "pe_ttm": valuation.get("pe_ttm"),
            "pb": valuation.get("pb"),
            "dividend_yield": valuation.get("dividend_yield"),
            "available_at": valuation.get("source_available_at"),
        }
    etf = dataset.etf_metadata_by_code(day, cutoff).get(code)
    if etf:
        metadata["etf_category"] = etf.get("category")
        metadata["underlying_index_code"] = etf.get("index_code") or etf.get("benchmark_code")
    return metadata


def _benchmark_from_rows(rows: Iterable[Any], *, day: date, cutoff: datetime) -> dict[str, Any]:
    values: list[float] = []
    for row in rows:
        if row.trade_date > day:
            continue
        available_at = _naive_utc(row.available_at)
        if available_at is not None and available_at > cutoff:
            continue
        value = _number(row.index_value)
        if value is not None and value > 0:
            values.append(value)
    if not values:
        return {"return20": None, "return60": None, "available": False, "source": "all_a_median_index"}
    return {
        "return20": values[-1] / values[-21] - 1.0 if len(values) >= 21 else None,
        "return60": values[-1] / values[-61] - 1.0 if len(values) >= 61 else None,
        "available": True,
        "source": "all_a_median_index",
    }


def _market_state(result: Mapping[str, Any] | None) -> dict[str, Any]:
    if result is None:
        return {
            "available": False,
            "is_frozen": False,
            "quality_status": "MISSING",
            "regime": None,
            "confidence": 0.0,
            "snapshot_id": None,
        }
    quality = str(result.get("quality_status") or "MISSING").upper()
    return {
        "available": quality in {"VALID", "DEGRADED"},
        "is_frozen": bool(result.get("is_frozen")),
        "quality_status": quality,
        "regime": result.get("regime"),
        "confidence": result.get("confidence"),
        "snapshot_id": None,
        "freeze_reason": result.get("freeze_reason"),
    }


def _held_score_rows(
    codes: Iterable[str],
    *,
    states: Mapping[str, Mapping[str, Any]],
    etf_metadata: Mapping[str, Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    bars_by_code: Mapping[str, list[dict[str, Any]]],
    cross_sectional: Mapping[str, Mapping[str, float | None]],
    benchmark: Mapping[str, Any],
    as_of: datetime,
    config: CandidateConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code in sorted(set(normalize_security_code(value) for value in codes if normalize_security_code(value))):
        state = states.get(code) or {}
        security_type = str(state.get("security_type") or "STOCK").upper()
        metadata = {}
        etf = etf_metadata.get(code) or {}
        if etf:
            metadata["etf_category"] = etf.get("category")
            metadata["underlying_index_code"] = etf.get("index_code") or etf.get("benchmark_code")
        quote = dict(quotes.get(code) or {})
        price = _number(quote.get("price"))
        if security_type == "ETF":
            score = score_etf_candidate(
                code,
                bars_by_code.get(code, []),
                price=price,
                quote=quote,
                metadata=metadata,
                cross_sectional=cross_sectional,
                benchmark=benchmark,
                as_of=as_of,
                live=False,
                config=config,
            )
        else:
            score = score_stock_candidate(
                code,
                bars_by_code.get(code, []),
                price=price,
                quote=quote,
                metadata=metadata,
                cross_sectional=cross_sectional,
                as_of=as_of,
                live=False,
                config=config,
            )
        rows.append({
            "code": code,
            "name": state.get("name"),
            "security_type": security_type,
            "opportunity_score": score.get("score"),
            "coverage": score.get("coverage"),
            "data_coverage": score.get("coverage"),
            "confidence": score.get("confidence"),
            "keep_score": None,
            "keep_score_confidence": None,
            "components": score.get("components") or {},
        })
    return rows


def _factor_audit(
    row: Mapping[str, Any],
    *,
    config: CandidateConfig,
    missing_factors: tuple[str, ...],
) -> list[dict[str, Any]]:
    security_type = str(row.get("security_type") or "STOCK").upper()
    weights = config.etf_factor_weights if security_type == "ETF" else config.stock_factor_weights
    components = row.get("components") or {}
    audit: list[dict[str, Any]] = []
    for factor_name in sorted(weights):
        component = components.get(factor_name) or {}
        structurally_missing = factor_name in missing_factors
        available = bool(component.get("available")) and not structurally_missing
        weight = float(weights.get(factor_name) or 0.0)
        missing_reason = None
        if structurally_missing:
            missing_reason = f"historical {factor_name} input is not persisted"
        elif not available:
            missing_reason = component.get("reason") or "FACTOR_UNAVAILABLE"
        audit.append({
            "factor_name": factor_name,
            "value": component.get("score"),
            "available": available,
            "source": component.get("source"),
            "coverage": 0.0 if structurally_missing else weight if available else 0.0,
            "quality": component.get("confidence"),
            "effective_weight": 0.0 if structurally_missing else weight if available else 0.0,
            "missing_reason": missing_reason,
        })
    return audit


@dataclass
class HistoricalCandidateRecomputeResult:
    trade_date: date
    as_of: datetime
    capability: str
    universe: dict[str, Any]
    coverage: float
    candidate_count: int
    action_count: int
    candidates: list[dict[str, Any]]
    held_scores: list[dict[str, Any]]
    missing_factors: list[str]
    limitations: list[str]
    source_ids: tuple[str, ...] = field(default_factory=tuple)
    query_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trade_date"] = self.trade_date.isoformat()
        payload["as_of"] = self.as_of.isoformat()
        return payload


def recompute_candidate_dates(
    dataset: RecomputePitDataset,
    *,
    dates: Iterable[date],
    market_results: Mapping[date, Any] | Iterable[Any],
    parameter_snapshot: Mapping[str, Any] | None,
) -> list[HistoricalCandidateRecomputeResult]:
    """Recompute the full candidate universe with production scoring cores."""

    normalized_snapshot = normalize_snapshot(parameter_snapshot)
    config = candidate_config_from_snapshot(normalized_snapshot)
    market_by_date = {
        result.trade_date: result
        for result in (market_results.values() if isinstance(market_results, Mapping) else market_results)
    }
    results: list[HistoricalCandidateRecomputeResult] = []
    for day in dates:
        cutoff = eod_cutoff(day)
        states = dataset.lifecycle_states(day, cutoff)
        classification = dataset.classification_by_code(day, cutoff)
        trading = dataset.trading_status_by_code(day, cutoff)
        calendar = {value for value in dataset.calendar_dates if value <= day}
        held = dataset.held_codes(day, cutoff)
        stock_universe = resolve_equity_universe_from_facts(
            day,
            purpose="CANDIDATE_STOCK",
            states=states,
            classification=classification,
            trading=trading,
            calendar_dates=calendar,
            held=held,
            minimum_trading_days=config.min_listing_trading_days,
        )
        etf_universe = resolve_equity_universe_from_facts(
            day,
            purpose="CANDIDATE_ETF",
            states=states,
            classification=classification,
            trading=trading,
            calendar_dates=calendar,
            held=held,
            minimum_trading_days=config.min_listing_trading_days,
        )
        stock_codes = set(stock_universe.eligible_codes)
        etf_codes = set(etf_universe.eligible_codes)
        eligible_codes = sorted(stock_codes | etf_codes)
        universe_total = max(1, stock_universe.total_count + etf_universe.total_count)
        universe_unknown = stock_universe.unknown_count + etf_universe.unknown_count
        universe = {
            "as_of_date": day.isoformat(),
            "universe_version": stock_universe.universe_version,
            "eligible_codes": eligible_codes,
            "stock_count": len(stock_codes),
            "etf_count": len(etf_codes),
            "total_count": universe_total,
            "unknown_count": universe_unknown,
            "coverage": round(max(0.0, 1.0 - universe_unknown / universe_total), 6),
            "status": "FULL" if universe_unknown == 0 else "PARTIAL",
            "exclusions": {
                "stock": stock_universe.excluded_counts,
                "etf": etf_universe.excluded_counts,
            },
        }
        quotes = _quote_map(dataset.quote_rows(day, cutoff))
        bars_by_code = dataset.bars_by_code(day, cutoff)
        etf_metadata = dataset.etf_metadata_by_code(day, cutoff)
        benchmark = _benchmark_from_rows(dataset.benchmark_rows, day=day, cutoff=cutoff)
        market_result = market_by_date.get(day)
        market = _market_state(market_result.as_dict() if market_result is not None else None)
        snapshot = dataset.latest_snapshot(day, cutoff)
        if snapshot is not None:
            portfolio_context = build_historical_portfolio_state(
                dataset,
                day=day,
                cutoff=cutoff,
                snapshot=snapshot,
                market_result=market_result.as_dict() if market_result is not None else None,
            )
        else:
            portfolio_context = {
                "total_assets": None,
                "cash_ratio": None,
                "spendable_cash": None,
                "positions": [],
                "position_constraints": [],
                "portfolio_quality": "BLOCKED",
                "portfolio_confidence": 0.0,
            }

        eligible_rows: list[dict[str, Any]] = []
        for code in eligible_codes:
            state = states.get(code) or {}
            security_type = "ETF" if code in etf_codes else "STOCK"
            eligible_rows.append({
                "code": code,
                "name": state.get("name"),
                "security_type": security_type,
                "etf_category": (etf_metadata.get(code) or {}).get("category"),
                "quote": dict(quotes.get(code) or {}),
                "bars": bars_by_code.get(code, []),
                "metadata": _metadata_for(code, day=day, cutoff=cutoff, dataset=dataset),
                "quote_is_proxy": True,
                "limit_up": False,
                "limit_down": False,
                "quote_provenance": "EOD_CLOSE_PROXY",
            })
        held_codes = sorted(code for code in held if code in states)
        held_rows: list[dict[str, Any]] = []
        for code in held_codes:
            state = states.get(code) or {}
            security_type = str(state.get("security_type") or "STOCK").upper()
            held_rows.append({
                "code": code,
                "name": state.get("name"),
                "security_type": security_type,
                "etf_category": (etf_metadata.get(code) or {}).get("category"),
                "quote": dict(quotes.get(code) or {}),
                "bars": bars_by_code.get(code, []),
                "metadata": _metadata_for(code, day=day, cutoff=cutoff, dataset=dataset),
                "quote_is_proxy": True,
                "limit_up": False,
                "limit_down": False,
                "quote_provenance": "EOD_CLOSE_PROXY",
            })
        # Held rows feed the cross-sectional percentile and held-opportunity
        # baseline only. Like production, the new-position prefilter reads the
        # eligible universe exclusively so a current holding can never surface
        # as a WATCHLIST / READY / ACTION candidate.
        scoring_rows = [*eligible_rows, *held_rows]
        cross_sectional = candidate_service._cross_sectional(scoring_rows, as_of=cutoff, live=False)
        held_scores = _held_score_rows(
            held_codes,
            states=states,
            etf_metadata=etf_metadata,
            quotes=quotes,
            bars_by_code=bars_by_code,
            cross_sectional=cross_sectional,
            benchmark=benchmark,
            as_of=cutoff,
            config=config,
        )
        for row in eligible_rows:
            row["cheap_score"] = candidate_service._cheap_score(row)
        stocks = sorted(
            (row for row in eligible_rows if str(row.get("security_type") or "").upper() == "STOCK"),
            key=lambda row: (-float(row.get("cheap_score") or 0.0), str(row.get("code") or "")),
        )[: config.stock_prefilter_limit]
        etfs = sorted(
            (row for row in eligible_rows if str(row.get("security_type") or "").upper() == "ETF"),
            key=lambda row: (-float(row.get("cheap_score") or 0.0), str(row.get("code") or "")),
        )[: config.etf_prefilter_limit]
        prefiltered = [*stocks, *etfs]
        scored = [
            candidate_service._score_row(
                row,
                cross_sectional=cross_sectional,
                benchmark=benchmark,
                as_of=cutoff,
                portfolio_context=portfolio_context,
                holding_bars=bars_by_code,
                held_scores=held_scores,
                market=market,
                live=False,
                config=config,
            )
            for row in prefiltered
        ]
        quote_coverage = (
            sum(1 for code in eligible_codes if code in quotes) / len(eligible_codes)
            if eligible_codes
            else 0.0
        )
        bar_coverage = (
            sum(1 for code in eligible_codes if bars_by_code.get(code)) / len(eligible_codes)
            if eligible_codes
            else 0.0
        )
        global_coverage = min(quote_coverage, bar_coverage)
        market_quality = str(market.get("quality_status") or "MISSING").upper()
        if not eligible_codes:
            run_quality = "MISSING"
        elif not market.get("available") or market_quality in {"MISSING", "INVALID", "STALE", "FROZEN"}:
            run_quality = "BLOCKED_FOR_ACTION"
        elif global_coverage >= 0.98 and market_quality == "VALID":
            run_quality = "NORMAL"
        elif global_coverage >= 0.95:
            run_quality = "DEGRADED"
        else:
            run_quality = "BLOCKED_FOR_ACTION"
        pools = take_stage_limits(
            scored,
            watchlist_max=config.watchlist_max,
            ready_max=config.ready_max,
            action_max=config.action_max,
        )
        if run_quality in {"BLOCKED_FOR_ACTION", "MISSING"}:
            pools["action"] = []
        if run_quality == "MISSING":
            pools["ready"] = []
        selected = rank_candidates([*pools["watchlist"], *pools["ready"], *pools["action"]])
        selected_by_code = {row["code"]: row for row in selected}
        selected_rows = list(selected_by_code.values())
        selected_rows.sort(key=lambda row: (
            0 if str(row.get("stage") or "").upper() == "ACTION"
            else 1 if str(row.get("stage") or "").upper() == "READY"
            else 2,
            -float(row.get("decision_edge") if row.get("decision_edge") is not None else -1e9),
            -float(row.get("action_score") if row.get("action_score") is not None else -1e9),
            str(row.get("code") or ""),
        ))
        for rank, row in enumerate(selected_rows, start=1):
            row["rank"] = rank
            row["reference_price"] = _number((row.get("quote") or {}).get("price"))
            row["reference_price_basis"] = "QFQ_EOD_CLOSE_PROXY"
            row["market_regime"] = market.get("regime")
            row["market_quality"] = market.get("quality_status")
            row["market_frozen"] = market.get("is_frozen")
            row["quote_quality"] = str((row.get("quote") or {}).get("quality_status") or "MISSING").upper()
            row["quote_is_proxy"] = True
            row["quality_status"] = row.get("quality_status") or "MISSING"
            row["factor_audit"] = _factor_audit(
                row,
                config=config,
                missing_factors=tuple(
                    factor for factor in KNOWN_MISSING_FACTORS.get("CANDIDATE_STOCK", ())
                    if factor != "etf_constituent_breadth"
                ),
            )
        missing_factors = list(KNOWN_MISSING_FACTORS.get("CANDIDATE_STOCK", ()))
        limitations = [
            "historical flow/industry factors are unavailable and are never filled with zero or current facts",
            "quotes are EOD close proxies and are not actionable at production-equivalent equivalence",
        ]
        if universe_unknown:
            limitations.append("PIT universe coverage is incomplete; capability is PARTIAL")
        if not eligible_codes:
            capability = RecomputeCapability.DATA_GAP
        else:
            capability = RecomputeCapability.PARTIAL_PIT_RECOMPUTE
        results.append(HistoricalCandidateRecomputeResult(
            trade_date=day,
            as_of=cutoff,
            capability=str(capability),
            universe=universe,
            coverage=global_coverage,
            candidate_count=len(selected_rows),
            action_count=len([row for row in selected_rows if str(row.get("stage") or "").upper() == "ACTION"]),
            candidates=selected_rows,
            held_scores=held_scores,
            missing_factors=missing_factors,
            limitations=limitations,
            source_ids=tuple(dataset.source_ids()),
            query_count=dataset.query_count,
        ))
    return results


__all__ = [
    "HistoricalCandidateRecomputeResult",
    "recompute_candidate_dates",
]
