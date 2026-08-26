"""Batch, deterministic Candidate Engine orchestration."""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market_engine_models import AllAMedianIndexDaily, MarketScoreSnapshot
from ..market_models import SecurityMaster, TradingCalendar
from ..market_runtime_models import MarketSnapshot
from ..portfolio.risk import latest_confirmed_snapshot
from ..portfolio.service import calculate_portfolio_risk
from ..v2_models import HoldingItem, PortfolioSnapshot
from .config import CandidateConfig, DEFAULT_CONFIG
from .decision_edge import calculate_decision_edge, held_opportunity_baseline
from .entry import calculate_entry_score
from .etf_score import score_etf_candidate
from .factors import feature_snapshot, metadata_section, percentile_rank, section_available_at
from .models import CandidateRun, CandidateScore
from .portfolio_fit import calculate_portfolio_fit
from .ranking import rank_candidates, take_stage_limits
from .risk_reward import calculate_structure_risk_reward
from .stock_score import score_stock_candidate
from .universe import build_candidate_universe
from ..services.daily_bar_cache import load_daily_bars

logger = logging.getLogger(__name__)


def _utc_naive(value: datetime | None = None) -> datetime:
    moment = value or datetime.now(UTC)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _as_of(value: date | datetime | str | None) -> datetime:
    if value is None:
        return _utc_naive()
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.max.time())
    if isinstance(value, datetime):
        moment = _utc_naive(value)
    else:
        try:
            moment = _utc_naive(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except ValueError as exc:
            raise ValueError("invalid_as_of") from exc
    if moment > _utc_naive() + timedelta(minutes=1):
        raise ValueError("as_of_cannot_be_in_the_future")
    return moment


def _code(value: Any) -> str:
    from ..market.codes import normalize_security_code

    return normalize_security_code(value)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).replace("/", "-")[:10])
    except (TypeError, ValueError):
        return None


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _quote_map(raw: Any, *, as_of: datetime) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping) and "quotes" in raw:
        raw = raw.get("quotes") or []
    if isinstance(raw, Mapping):
        values = []
        for key, value in raw.items():
            if isinstance(value, Mapping):
                item = dict(value)
                item.setdefault("code", key)
                values.append(item)
    elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
        values = list(raw)
    else:
        values = []
    output: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        code = _code(item.get("code") or item.get("symbol"))
        if not code:
            continue
        available_at = item.get("available_at") or item.get("source_timestamp")
        if available_at:
            try:
                parsed = datetime.fromisoformat(str(available_at).replace("Z", "+00:00"))
                parsed = _utc_naive(parsed)
                if parsed > as_of:
                    item["quality_status"] = "MISSING"
            except ValueError:
                pass
        item["code"] = code
        output[code] = item
    return output


def _bars_map(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        code = _code(raw.get("code") or raw.get("symbol"))
        if code:
            output[code].append(dict(raw))
    for values in output.values():
        values.sort(key=lambda row: str(row.get("trade_date") or row.get("date") or ""))
    return dict(output)


def _metadata(security: SecurityMaster) -> dict[str, Any]:
    raw = security.raw_metadata_json if isinstance(security.raw_metadata_json, dict) else {}
    return dict(raw)


def _load_market_state(db: Session, *, as_of: datetime) -> dict[str, Any]:
    row = db.execute(
        select(MarketScoreSnapshot)
        .where(MarketScoreSnapshot.market == "CN", MarketScoreSnapshot.captured_at <= as_of)
        .order_by(MarketScoreSnapshot.captured_at.desc(), MarketScoreSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    snapshot = db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.market == "CN", MarketSnapshot.completed_at <= as_of)
        .order_by(MarketSnapshot.completed_at.desc(), MarketSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {
            "available": False,
            "is_frozen": False,
            "quality_status": "MISSING",
            "regime": None,
            "confidence": 0.0,
            "snapshot_id": None,
            "market_snapshot_id": snapshot.snapshot_id if snapshot else None,
        }
    return {
        "available": True,
        "snapshot_id": row.snapshot_id,
        "market_snapshot_id": row.metric_snapshot_id,
        "captured_at": row.captured_at.isoformat() if row.captured_at else None,
        "trade_date": row.trade_date.isoformat() if row.trade_date else None,
        "display_score": row.display_score,
        "regime": row.regime,
        "confidence": row.confidence,
        "quality_status": row.quality_status,
        "is_frozen": row.is_frozen,
        "freeze_reason": row.freeze_reason,
    }


def _calendar_days_by_code(db: Session, securities: Iterable[SecurityMaster], *, as_of: datetime) -> dict[str, int]:
    listings = {
        security.code: security.listing_date
        for security in securities
        if security.code and security.listing_date is not None
    }
    if not listings:
        return {}
    rows = db.execute(
        select(TradingCalendar.trade_date)
        .where(
            TradingCalendar.market == "CN",
            TradingCalendar.is_open.is_(True),
            TradingCalendar.trade_date <= as_of.date(),
        )
        .order_by(TradingCalendar.trade_date.asc())
    ).scalars().all()
    return {
        code: sum(1 for trade_date in rows if listing_date <= trade_date <= as_of.date())
        for code, listing_date in listings.items()
    }


def _benchmark(db: Session, *, as_of: datetime) -> dict[str, Any]:
    rows = db.execute(
        select(AllAMedianIndexDaily)
        .where(
            AllAMedianIndexDaily.market == "CN",
            AllAMedianIndexDaily.trade_date <= as_of.date(),
            (AllAMedianIndexDaily.available_at.is_(None)) | (AllAMedianIndexDaily.available_at <= as_of),
            AllAMedianIndexDaily.quality_status.in_(("VALID", "DEGRADED")),
        )
        .order_by(AllAMedianIndexDaily.trade_date.asc())
    ).scalars().all()
    values = [float(row.index_value) for row in rows if row.index_value and row.index_value > 0]
    return {
        "return20": values[-1] / values[-21] - 1.0 if len(values) >= 21 else None,
        "return60": values[-1] / values[-61] - 1.0 if len(values) >= 61 else None,
        "available": bool(values),
        "source": "all_a_median_index",
    }


def _cross_sectional(rows: Iterable[Mapping[str, Any]], *, as_of: datetime) -> dict[str, dict[str, float | None]]:
    maps: dict[str, dict[str, float | None]] = defaultdict(dict)
    for row in rows:
        code = str(row.get("code") or "")
        if not code:
            continue
        features = feature_snapshot(row.get("bars") or [], price=_number((row.get("quote") or {}).get("price")))
        for key in ("return20", "return60", "amount20", "relative_volume20"):
            source_key = "median_amount20" if key == "amount20" else key
            maps[key][code] = _number(features.get(source_key))
        if features.get("return20") is not None and features.get("return60") is not None:
            maps["acceleration"][code] = features["return20"] - features["return60"] / 3.0
        metadata = row.get("metadata") or {}
        for section_name, metric_names in {
            "fundamental": {
                "roe": ("roe", "roe_ttm"),
                "revenue_growth": ("revenue_yoy", "revenue_growth"),
                "profit_growth": ("profit_yoy", "profit_growth"),
                "cash_quality": ("operating_cash_quality", "ocf_quality", "operating_cash_flow"),
                "margin_quality": ("margin_quality", "gross_margin", "net_margin"),
            },
            "valuation": {"pe": ("pe_ttm", "pe"), "pb": ("pb",), "dividend": ("dividend_yield", "dividend_yield_ratio")},
            "industry": {"industry_rs": ("relative_strength", "rs20", "industry_rs"), "industry_breadth": ("breadth", "industry_breadth"), "industry_trend": ("trend", "industry_trend")},
        }.items():
            section = metadata_section(metadata, section_name)
            if not section_available_at(section, as_of):
                section = {}
            for map_key, names in metric_names.items():
                value = next((_number(section.get(name)) for name in names if _number(section.get(name)) is not None), None)
                if section_name == "valuation" and map_key in {"pe", "pb"} and value is not None and value <= 0:
                    value = None
                maps[map_key][code] = value
        flow = metadata_section(metadata, "flow")
        if not section_available_at(flow, as_of):
            flow = {}
        maps["money_flow"][code] = _number(flow.get("main_net") or flow.get("net_money_flow"))
        etf_breadth = metadata_section(metadata, "constituent_breadth") or metadata_section(metadata, "breadth")
        if not section_available_at(etf_breadth, as_of):
            etf_breadth = {}
        breadth = _number(etf_breadth.get("score") or etf_breadth.get("breadth") or etf_breadth.get("advance_ratio"))
        if breadth is not None and breadth <= 1:
            breadth *= 100.0
        maps["constituent_breadth"][code] = breadth
        maps["relative20"][code] = maps["return20"].get(code)
        maps["relative60"][code] = maps["return60"].get(code)
    return dict(maps)


def _cheap_score(row: Mapping[str, Any]) -> float:
    features = feature_snapshot(row.get("bars") or [], price=_number((row.get("quote") or {}).get("price")))
    trend_checks = []
    if features.get("price") is not None and features.get("ma20") is not None:
        trend_checks.append(features["price"] > features["ma20"])
    if features.get("ma20") is not None and features.get("ma60") is not None:
        trend_checks.append(features["ma20"] > features["ma60"])
    trend = 100.0 * sum(trend_checks) / len(trend_checks) if trend_checks else 0.0
    momentum = max(0.0, min(100.0, 50.0 + float(features.get("return20") or 0.0) * 200.0))
    liquidity = max(0.0, min(100.0, float(features.get("relative_volume20") or 1.0) * 50.0))
    return trend * 0.5 + momentum * 0.3 + liquidity * 0.2


def _score_row(
    row: Mapping[str, Any],
    *,
    cross_sectional: Mapping[str, Mapping[str, float | None]],
    benchmark: Mapping[str, Any],
    as_of: datetime,
    portfolio_context: Mapping[str, Any],
    holding_bars: Mapping[str, Iterable[Any]],
    held_scores: list[dict[str, Any]],
    market: Mapping[str, Any],
    config: CandidateConfig,
) -> dict[str, Any]:
    code = str(row["code"])
    quote = dict(row.get("quote") or {})
    price = _number(quote.get("price"))
    metadata = dict(row.get("metadata") or {})
    security_type = str(row.get("security_type") or "STOCK").upper()
    if security_type == "ETF":
        underlying_code = metadata.get("underlying_index_code") or metadata.get("underlying_code")
        underlying_bars = holding_bars.get(_code(underlying_code)) if underlying_code else None
        opportunity = score_etf_candidate(
            code,
            row.get("bars") or [],
            price=price,
            quote=quote,
            metadata=metadata,
            cross_sectional=cross_sectional,
            benchmark=benchmark,
            underlying_bars=underlying_bars,
            as_of=as_of,
            config=config,
        )
    else:
        opportunity = score_stock_candidate(
            code,
            row.get("bars") or [],
            price=price,
            quote=quote,
            metadata=metadata,
            cross_sectional=cross_sectional,
            as_of=as_of,
            config=config,
        )
    rr = calculate_structure_risk_reward(row.get("bars") or [], price=price)
    entry = calculate_entry_score(row.get("bars") or [], price=price, quote=quote, risk_reward=rr, config=config)
    candidate_input = {
        "code": code,
        "security_type": security_type,
        "etf_category": row.get("etf_category"),
        "metadata": metadata,
        "industry": metadata_section(metadata, "industry").get("name") or metadata.get("industry"),
    }
    fit = calculate_portfolio_fit(
        candidate_input,
        portfolio_context,
        holding_bars=holding_bars,
        candidate_bars=row.get("bars") or [],
        held_opportunity_scores=held_scores,
        config=config,
    )
    baseline = held_opportunity_baseline(held_scores, config=config)
    edge = calculate_decision_edge(
        opportunity_score=opportunity.get("score"),
        entry_score=entry.get("score"),
        portfolio_fit_score=fit.get("score"),
        market_regime=market.get("regime"),
        market_quality=market.get("quality_status"),
        market_frozen=bool(market.get("is_frozen")),
        held_baseline=baseline,
        total_assets=_number(portfolio_context.get("total_assets") or portfolio_context.get("current_estimated_total_assets")),
        probe_weight=fit.get("probe_weight"),
        config=config,
    )
    market_confidence = _number(market.get("confidence")) or 0.0
    opportunity_confidence = _number(opportunity.get("confidence")) or 0.0
    entry_confidence = _number(entry.get("confidence")) or 0.0
    fit_confidence = _number(fit.get("confidence")) or 0.0
    candidate_confidence = round(0.50 * opportunity_confidence + 0.15 * entry_confidence + 0.20 * fit_confidence + 0.15 * market_confidence, 4)
    coverage = float(opportunity.get("coverage") or 0.0)
    quality = str(opportunity.get("quality_status") or "INSUFFICIENT")
    quote_quality = str(quote.get("quality_status") or "MISSING").upper()
    reasons: list[str] = []
    risk_flags: list[str] = []
    if row.get("limit_up"):
        risk_flags.append("PRICE_LIMIT_UP")
    if row.get("limit_down"):
        risk_flags.append("PRICE_LIMIT_DOWN")
    if fit.get("hard_cap_violation"):
        reasons.append("HARD_CAP_CONSTRAINT")
        risk_flags.append("HARD_CAP_CONSTRAINT")
    if fit.get("high_corr_positions"):
        reasons.append("HIGH_PORTFOLIO_CORRELATION")
        risk_flags.append("HIGH_PORTFOLIO_CORRELATION")
    if coverage < 0.50:
        reasons.append("FACTOR_COVERAGE_LOW")
    if opportunity.get("score") is None or opportunity.get("score", 0) < config.watchlist_opportunity_min:
        reasons.append("OPPORTUNITY_SCORE_LOW")
    if entry.get("score") is None or entry.get("score", 0) < config.ready_entry_min:
        reasons.append("ENTRY_SCORE_LOW")
    rr_ratio = rr.get("risk_reward_ratio")
    if rr_ratio is None or rr_ratio < config.rr_ready_min:
        reasons.append("RISK_REWARD_LOW")
    if fit.get("score") is None or fit.get("score", 0) < config.ready_fit_min:
        reasons.append("PORTFOLIO_FIT_LOW")
    if held_scores and not baseline.get("available"):
        reasons.append("HELD_BASELINE_UNAVAILABLE")
    if edge.get("edge_vs_no_action") is not None and edge["edge_vs_no_action"] < config.min_decision_edge:
        reasons.append("EDGE_VS_NO_ACTION_LOW")
    if edge.get("edge_vs_current_holdings") is not None and edge["edge_vs_current_holdings"] < config.min_holding_edge:
        reasons.append("EDGE_VS_HOLDINGS_LOW")
    if quote_quality not in {"VALID", "DEGRADED"}:
        reasons.append("QUOTE_INVALID")

    stage = "REJECTED"
    watch_ok = (
        opportunity.get("score") is not None
        and opportunity["score"] >= config.watchlist_opportunity_min
        and coverage >= 0.50
        and quote_quality in {"VALID", "DEGRADED"}
    )
    if watch_ok:
        stage = "WATCHLIST"
    market_available = bool(market.get("available")) and str(market.get("quality_status") or "MISSING").upper() not in {"MISSING", "INVALID", "FROZEN"}
    ready_ok = (
        market_available
        and not bool(market.get("is_frozen"))
        and opportunity.get("score") is not None
        and opportunity["score"] >= config.ready_opportunity_min
        and entry.get("score") is not None
        and entry["score"] >= config.ready_entry_min
        and fit.get("score") is not None
        and fit["score"] >= config.ready_fit_min
        and rr_ratio is not None
        and rr_ratio >= config.rr_ready_min
        and coverage >= config.ready_coverage_min
        and candidate_confidence >= config.ready_confidence_min
        and not fit.get("hard_cap_violation")
    )
    if ready_ok:
        stage = "READY"
    action_ok = (
        ready_ok
        and opportunity["score"] >= config.action_opportunity_min
        and entry["score"] >= config.action_entry_min
        and fit["score"] >= config.action_fit_min
        and rr_ratio >= config.rr_action_min
        and coverage >= config.action_coverage_min
        and candidate_confidence >= config.action_confidence_min
        and edge.get("decision_edge") is not None
        and edge["decision_edge"] >= config.min_decision_edge
        and (
            not held_scores
            or (
                baseline.get("available")
                and edge.get("edge_vs_current_holdings") is not None
                and edge["edge_vs_current_holdings"] >= config.min_holding_edge
            )
        )
        and fit.get("funding_mode") == "CASH_FUNDED"
        and not row.get("limit_up")
    )
    if action_ok:
        stage = "ACTION"
    if stage == "ACTION" and fit.get("funding_mode") == "REPLACEMENT_REVIEW":
        stage = "READY"
        reasons.append("REPLACEMENT_REVIEW_REQUIRED")
    if market.get("is_frozen") or not market.get("available") or str(market.get("quality_status") or "").upper() in {"MISSING", "INVALID"}:
        if stage in {"READY", "ACTION"}:
            stage = "WATCHLIST" if watch_ok else "REJECTED"
        if market.get("is_frozen"):
            reasons.append("MARKET_STATE_FROZEN")
        else:
            reasons.append("MARKET_STATE_UNAVAILABLE")
    if stage == "ACTION" and fit.get("funding_mode") != "CASH_FUNDED":
        stage = "READY" if ready_ok else "WATCHLIST"
        reasons.append("REPLACEMENT_REVIEW_REQUIRED" if fit.get("funding_mode") == "REPLACEMENT_REVIEW" else "UNFUNDED")
    if stage != "ACTION" and not reasons:
        reasons.append("ACTION_GATE_NOT_PASSED")

    positive = []
    negative = []
    for name, item in (opportunity.get("components") or {}).items():
        score = item.get("score")
        if score is not None and score >= 70:
            positive.append(f"{name}:{round(float(score), 1)}")
        elif score is not None and score <= 40:
            negative.append(f"{name}:{round(float(score), 1)}")
        elif not item.get("available"):
            negative.append(f"{name}:unavailable")
    if fit.get("weighted_candidate_correlation") is not None and fit["weighted_candidate_correlation"] < 0.5:
        positive.append("portfolio:diversification")
    if fit.get("high_corr_positions"):
        negative.append("portfolio:high_correlation")
    reason_detail = {
        "catalyst": "确定性趋势/动量与风险因子通过" if positive else "机会因子证据不足",
        "capital_flow": "价量流动性因子可用" if (opportunity.get("components") or {}).get("flow", {}).get("available") else "资金流因子 unavailable",
        "sector_position": "行业映射因子可用" if (opportunity.get("components") or {}).get("industry", {}).get("available") else "行业映射 unavailable",
    }
    return {
        "code": code,
        "name": row.get("name"),
        "security_type": security_type,
        "etf_category": row.get("etf_category"),
        "candidate_type": "new_position",
        "action": "new_position" if stage == "ACTION" else "rotation_watch",
        "stage": stage,
        "candidate_engine_stage": stage,
        "score": round(float(edge["action_score"]) / 10.0, 2) if edge.get("action_score") is not None else None,
        "opportunity_score": opportunity.get("score"),
        "entry_score": entry.get("score"),
        "portfolio_fit_score": fit.get("score"),
        "action_score": edge.get("action_score"),
        "decision_edge": edge.get("decision_edge"),
        "edge_vs_no_action": edge.get("edge_vs_no_action"),
        "edge_vs_current_holdings": edge.get("edge_vs_current_holdings"),
        "no_action_threshold": edge.get("no_action_threshold"),
        "risk_reward_ratio": rr_ratio,
        "risk_reward": rr,
        "data_coverage": coverage,
        "coverage": coverage,
        "confidence": candidate_confidence,
        "quality_status": quality,
        "funding_mode": fit.get("funding_mode"),
        "probe_weight": fit.get("probe_weight"),
        "positive_drivers": positive,
        "negative_drivers": negative,
        "blocking_reasons": list(dict.fromkeys(reasons)),
        "reason_codes": list(dict.fromkeys(reasons)),
        "risk_flags": list(dict.fromkeys(risk_flags)),
        "reason_detail": reason_detail,
        "components": opportunity.get("components") or {},
        "entry": entry,
        "portfolio_fit": fit,
        "comparison": {"held_baseline": baseline},
        "decision_edge_detail": edge,
        "lineage": {
            "as_of": as_of.isoformat(),
            "quote_provider": quote.get("provider"),
            "quote_quality": quote_quality,
            "daily_bar_provider": (row.get("bars") or [{}])[-1].get("provider") if row.get("bars") else None,
            "factor_version": config.engine_version,
        },
        "buyable": stage == "ACTION",
        "actionable": stage == "ACTION",
    }


def _held_score_rows(
    snapshot: PortfolioSnapshot,
    positions: Iterable[Mapping[str, Any]],
    security_by_code: Mapping[str, SecurityMaster],
    bars_by_code: Mapping[str, list[dict[str, Any]]],
    quotes: Mapping[str, Mapping[str, Any]],
    cross_sectional: Mapping[str, Mapping[str, float | None]],
    *,
    as_of: datetime,
    benchmark: Mapping[str, Any],
    config: CandidateConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    position_map = {str(row.get("code")): dict(row) for row in positions if row.get("code")}
    for code, position in position_map.items():
        security = security_by_code.get(code)
        security_type = str((security.security_type if security else position.get("security_type")) or "STOCK").upper()
        metadata = _metadata(security) if security else {}
        quote = dict(quotes.get(code) or {})
        if security_type == "ETF":
            score = score_etf_candidate(code, bars_by_code.get(code, []), price=_number(quote.get("price")), quote=quote, metadata=metadata, cross_sectional=cross_sectional, benchmark=benchmark, as_of=as_of, config=config)
        else:
            score = score_stock_candidate(code, bars_by_code.get(code, []), price=_number(quote.get("price")), quote=quote, metadata=metadata, cross_sectional=cross_sectional, as_of=as_of, config=config)
        rows.append({
            "code": code,
            "name": position.get("name") or (security.name if security else None),
            "security_type": security_type,
            "opportunity_score": score.get("score"),
            "coverage": score.get("coverage"),
            "data_coverage": score.get("coverage"),
            "confidence": score.get("confidence"),
            "keep_score": position.get("keep_score"),
            "keep_score_confidence": position.get("keep_score_confidence"),
            "components": score.get("components"),
        })
    return rows


def _calculation_key(
    *,
    portfolio_id: int,
    snapshot_id: int | None,
    as_of: datetime,
    market_id: str | None,
    mode: str,
    config: CandidateConfig,
) -> str:
    bucket = as_of.replace(second=0, microsecond=0).isoformat()
    return f"{portfolio_id}:{snapshot_id or 'none'}:{bucket}:{market_id or 'none'}:{mode}:{config.engine_version}"


def _serialize_score(row: CandidateScore | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(row, CandidateScore):
        return {
            "id": row.id,
            "candidate_run_id": row.candidate_run_id,
            "code": row.code,
            "name": row.name,
            "security_type": row.security_type,
            "etf_category": row.etf_category,
            "stage": row.stage,
            "rank": row.rank,
            "score": row.score,
            "opportunity_score": row.opportunity_score,
            "entry_score": row.entry_score,
            "portfolio_fit_score": row.portfolio_fit_score,
            "action_score": row.action_score,
            "edge_vs_no_action": row.edge_vs_no_action,
            "edge_vs_current_holdings": row.edge_vs_current_holdings,
            "decision_edge": row.decision_edge,
            "risk_reward_ratio": row.risk_reward_ratio,
            "data_coverage": row.data_coverage,
            "confidence": row.confidence,
            "quality_status": row.quality_status,
            "probe_weight": row.probe_weight,
            "funding_mode": row.funding_mode,
            "components": row.components_json or {},
            "portfolio_fit": row.portfolio_fit_json or {},
            "entry": row.entry_json or {},
            "comparison": row.comparison_json or {},
            "reason_codes": row.reason_codes_json or [],
            "risk_flags": row.risk_flags_json or [],
            "positive_drivers": row.positive_drivers_json or [],
            "negative_drivers": row.negative_drivers_json or [],
            "blocking_reasons": row.blocking_reasons_json or [],
            "lineage": row.lineage_json or {},
            "lifecycle": row.lifecycle,
        }
    return dict(row)


def _run_payload(row: CandidateRun, scores: Iterable[CandidateScore] | None = None) -> dict[str, Any]:
    score_rows = [_serialize_score(item) for item in (list(scores) if scores is not None else row.scores)]
    return {
        "status": "ready" if row.status == "COMPLETED" else "unavailable",
        "run": {
            "id": row.id,
            "run_id": row.id,
            "user_id": row.user_id,
            "portfolio_id": row.portfolio_id,
            "trade_date": row.trade_date.isoformat() if row.trade_date else None,
            "as_of": row.as_of.isoformat() if row.as_of else None,
            "captured_at": row.captured_at.isoformat() if row.captured_at else None,
            "portfolio_snapshot_id": row.portfolio_snapshot_id,
            "market_score_snapshot_id": row.market_score_snapshot_id,
            "market_snapshot_id": row.market_snapshot_id,
            "status": row.status,
            "mode": row.mode,
            "universe_count": row.universe_count,
            "eligible_count": row.eligible_count,
            "prefilter_count": row.prefilter_count,
            "watchlist_count": row.watchlist_count,
            "ready_count": row.ready_count,
            "action_count": row.action_count,
            "quality_status": row.quality_status,
            "confidence": row.confidence,
            "calculation_version": row.calculation_version,
            "stock_score_version": row.stock_score_version,
            "etf_score_version": row.etf_score_version,
            "entry_score_version": row.entry_score_version,
            "portfolio_fit_version": row.portfolio_fit_version,
            "decision_edge_version": row.decision_edge_version,
            "exclusion_counts": row.exclusion_counts_json or {},
            "metadata": row.metadata_json or {},
        },
        "candidate_engine": {
            "run_id": row.id,
            "quality_status": row.quality_status,
            "confidence": row.confidence,
            "market_regime": (row.metadata_json or {}).get("market", {}).get("regime"),
            "watchlist_count": row.watchlist_count,
            "ready_count": row.ready_count,
            "action_count": row.action_count,
            "calculation_version": row.calculation_version,
        },
        "scores": score_rows,
        "watchlist": [item for item in score_rows if item.get("stage") == "WATCHLIST"],
        "ready": [item for item in score_rows if item.get("stage") == "READY"],
        "action": [item for item in score_rows if item.get("stage") == "ACTION"],
        "candidates": [item for item in score_rows if item.get("stage") == "ACTION"],
        "held_opportunity_scores": (row.metadata_json or {}).get("held_opportunity_scores", []),
        "diagnostics": (row.metadata_json or {}).get("diagnostics", {}),
    }


def scan_candidates(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    as_of: date | datetime | str | None = None,
    mode: str = "standard",
    persist: bool = True,
    quote_rows: Any = None,
    snapshot_id: int | None = None,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Run one local-cache Candidate scan without provider/network fan-out."""

    started = time.perf_counter()
    moment = _as_of(as_of)
    mode = str(mode or "standard").lower()
    if mode not in {"fast", "standard", "deep"}:
        raise ValueError("unsupported_candidate_mode")
    if mode == "fast":
        return latest_candidate_context(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            snapshot_id=snapshot_id,
            as_of=moment,
            max_age_seconds=30 * 60,
            require_reliable=True,
        )
    snapshot = (
        db.get(PortfolioSnapshot, snapshot_id)
        if snapshot_id is not None
        else latest_confirmed_snapshot(db, portfolio_id=portfolio_id, as_of=moment)
    )
    if snapshot is None or snapshot.user_id != user_id:
        raise ValueError("confirmed_snapshot_not_found")
    if snapshot.portfolio_id != portfolio_id or snapshot.status != "confirmed":
        raise ValueError("confirmed_snapshot_not_found")
    market = _load_market_state(db, as_of=moment)
    calculation_key = _calculation_key(
        portfolio_id=portfolio_id,
        snapshot_id=snapshot.id,
        as_of=moment,
        market_id=market.get("snapshot_id"),
        mode=mode,
        config=config,
    )
    if persist:
        existing = db.execute(select(CandidateRun).where(CandidateRun.calculation_key == calculation_key)).scalar_one_or_none()
        if existing is not None:
            return _run_payload(existing)

    holdings = list(snapshot.holdings)
    held_codes = [_code(row.code) for row in holdings if _code(row.code)]
    all_securities = list(
        db.execute(
            select(SecurityMaster)
            .where(
                SecurityMaster.market == "CN",
                SecurityMaster.status.in_(("ACTIVE", "LISTED")),
                SecurityMaster.security_type.in_(("STOCK", "ETF")),
            )
            .order_by(SecurityMaster.code.asc())
        ).scalars().all()
    )
    held_security_rows = db.execute(select(SecurityMaster).where(SecurityMaster.market == "CN", SecurityMaster.code.in_(held_codes))).scalars().all() if held_codes else []
    security_by_code = {row.code: row for row in [*all_securities, *held_security_rows]}
    all_codes = list(dict.fromkeys([row.code for row in all_securities] + held_codes))
    bars_by_code = _bars_map(load_daily_bars(db, all_codes, trade_date=moment.date(), available_at=moment, limit=max(130, config.min_history_bars)))
    quotes = _quote_map(quote_rows, as_of=moment)
    for code, rows in bars_by_code.items():
        if code in quotes or not rows:
            continue
        latest = rows[-1]
        quotes[code] = {
            "code": code,
            "price": latest.get("close"),
            "prev_close": latest.get("prev_close"),
            "amount": latest.get("amount"),
            "volume": latest.get("volume"),
            "turnover_rate": latest.get("turnover_rate"),
            "quality_status": "DEGRADED",
            "provider": latest.get("provider") or "daily_bar_cache",
            "available_at": latest.get("available_at"),
            "source": "daily_bar_cache_close_proxy",
        }
    calendar_days = _calendar_days_by_code(db, all_securities, as_of=moment)
    universe = build_candidate_universe(
        all_securities,
        quotes=quotes,
        bars=bars_by_code,
        held_codes=held_codes,
        as_of=moment,
        trading_days_by_code=calendar_days,
        config=config,
    )
    eligible = list(universe["eligible"])
    held_rows = []
    for code in held_codes:
        security = security_by_code.get(code)
        if security is None:
            continue
        held_rows.append({
            "code": code,
            "name": security.name,
            "security_type": security.security_type,
            "etf_category": security.etf_category,
            "quote": quotes.get(code) or {},
            "bars": bars_by_code.get(code, []),
            "metadata": _metadata(security),
        })
    scoring_rows = [*eligible, *held_rows]
    cross_sectional = _cross_sectional(scoring_rows, as_of=moment)
    benchmark = _benchmark(db, as_of=moment)

    try:
        calculated = calculate_portfolio_risk(
            db,
            portfolio_id=portfolio_id,
            user_id=user_id,
            as_of=moment,
            persist=False,
            snapshot=snapshot,
            quote_rows={"quotes": list(quotes.values())},
        )
        state, risk, constraints = calculated["state"], calculated["risk"], calculated["constraints"]
        portfolio_context = {
            **state,
            **risk,
            "position_constraints": constraints.get("positions") or [],
            "portfolio_quality": risk.get("quality_status"),
            "portfolio_confidence": risk.get("confidence"),
            "market_state": market,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Candidate portfolio context unavailable for portfolio %s", portfolio_id)
        portfolio_context = {
            "total_assets": snapshot.total_assets,
            "cash_ratio": None,
            "positions": [],
            "position_constraints": [],
            "portfolio_quality": "BLOCKED",
            "portfolio_confidence": 0.0,
            "portfolio_engine_error": str(exc)[:300],
        }
    positions = portfolio_context.get("positions") or []
    held_scores = _held_score_rows(
        snapshot,
        positions,
        security_by_code,
        bars_by_code,
        quotes,
        cross_sectional,
        as_of=moment,
        benchmark=benchmark,
        config=config,
    )
    baseline = held_opportunity_baseline(held_scores, config=config)

    # Cheap prefilter is computed from already-loaded local facts and is split
    # by security type so ETFs cannot disappear behind a much larger stock pool.
    for row in eligible:
        row["cheap_score"] = _cheap_score(row)
    stocks = sorted((row for row in eligible if row["security_type"] == "STOCK"), key=lambda row: (-row["cheap_score"], row["code"]))[: config.stock_prefilter_limit]
    etfs = sorted((row for row in eligible if row["security_type"] == "ETF"), key=lambda row: (-row["cheap_score"], row["code"]))[: config.etf_prefilter_limit]
    prefiltered = [*stocks, *etfs]
    scored = [
        _score_row(
            row,
            cross_sectional=cross_sectional,
            benchmark=benchmark,
            as_of=moment,
            portfolio_context=portfolio_context,
            holding_bars=bars_by_code,
            held_scores=held_scores,
            market=market,
            config=config,
        )
        for row in prefiltered
    ]
    pools = take_stage_limits(scored, watchlist_max=config.watchlist_max, ready_max=config.ready_max, action_max=config.action_max)
    selected = rank_candidates([*pools["watchlist"], *pools["ready"], *pools["action"]])
    selected_by_code = {row["code"]: row for row in selected}
    selected = list(selected_by_code.values())
    selected.sort(key=lambda row: (0 if row["stage"] == "ACTION" else 1 if row["stage"] == "READY" else 2, -float(row.get("decision_edge") if row.get("decision_edge") is not None else -1e9), -float(row.get("action_score") if row.get("action_score") is not None else -1e9), row["code"]))
    for rank, row in enumerate(selected, start=1):
        row["rank"] = rank

    quality_inputs = [
        float(universe.get("quote_coverage") or 0.0),
        float(universe.get("bar_coverage") or 0.0),
        float(market.get("confidence") or 0.0) / 100.0,
        float(portfolio_context.get("portfolio_confidence") or 0.0) / 100.0,
    ]
    run_confidence = round(sum(quality_inputs) / len(quality_inputs) * 100.0, 4)
    if not eligible:
        run_quality = "MISSING"
    elif run_confidence >= 80.0 and str(market.get("quality_status") or "").upper() == "VALID":
        run_quality = "VALID"
    elif run_confidence >= 65.0:
        run_quality = "DEGRADED"
    else:
        run_quality = "BLOCKED_FOR_ACTION"
    exclusion_counts = Counter(universe.get("exclusion_counts") or {})
    diagnostics = {
        "universe_total": universe.get("universe_count", 0),
        "eligible": len(eligible),
        "prefilter": len(prefiltered),
        "stock_prefilter_count": len(stocks),
        "etf_prefilter_count": len(etfs),
        "full_scored_count": len(scored),
        "quote_coverage": universe.get("quote_coverage", 0.0),
        "bar_coverage": universe.get("bar_coverage", 0.0),
        "factor_coverage": round(sum(float(row.get("data_coverage") or 0.0) for row in scored) / len(scored), 4) if scored else 0.0,
        "action_zero_reasons": dict(Counter(reason for row in scored if row.get("stage") != "ACTION" for reason in row.get("blocking_reasons") or [])),
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "baseline": baseline,
    }
    metadata = {
        "market": market,
        "benchmark": benchmark,
        "held_opportunity_scores": held_scores,
        "baseline": baseline,
        "diagnostics": diagnostics,
        "scan_contract": {
            "network_fetch": False,
            "server_owned_inputs": True,
            "no_lookahead": True,
            "probe_weight_is_simulation": True,
        },
    }
    if not persist:
        fake = {
            "status": "ready",
            "run": {
                "id": None,
                "run_id": None,
                "user_id": user_id,
                "portfolio_id": portfolio_id,
                "trade_date": moment.date().isoformat(),
                "as_of": moment.isoformat(),
                "captured_at": _utc_naive().isoformat(),
                "portfolio_snapshot_id": snapshot.id,
                "market_score_snapshot_id": market.get("snapshot_id"),
                "market_snapshot_id": market.get("market_snapshot_id"),
                "status": "COMPLETED",
                "mode": mode,
                "universe_count": universe.get("universe_count", 0),
                "eligible_count": len(eligible),
                "prefilter_count": len(prefiltered),
                "watchlist_count": len(pools["watchlist"]),
                "ready_count": len(pools["ready"]),
                "action_count": len(pools["action"]),
                "quality_status": run_quality,
                "confidence": run_confidence,
                "calculation_version": config.engine_version,
                "stock_score_version": config.stock_score_version,
                "etf_score_version": config.etf_score_version,
                "entry_score_version": config.entry_score_version,
                "portfolio_fit_version": config.portfolio_fit_version,
                "decision_edge_version": config.decision_edge_version,
                "exclusion_counts": dict(exclusion_counts),
                "metadata": metadata,
            },
            "candidate_engine": {
                "run_id": None,
                "quality_status": run_quality,
                "confidence": run_confidence,
                "market_regime": market.get("regime"),
                "watchlist_count": len(pools["watchlist"]),
                "ready_count": len(pools["ready"]),
                "action_count": len(pools["action"]),
                "calculation_version": config.engine_version,
            },
            "scores": selected,
            "watchlist": pools["watchlist"],
            "ready": pools["ready"],
            "action": pools["action"],
            "candidates": pools["action"],
            "held_opportunity_scores": held_scores,
            "diagnostics": diagnostics,
        }
        return fake

    run = CandidateRun(
        user_id=user_id,
        portfolio_id=portfolio_id,
        calculation_key=calculation_key,
        trade_date=moment.date(),
        as_of=moment,
        captured_at=_utc_naive(),
        portfolio_snapshot_id=snapshot.id,
        market_score_snapshot_id=market.get("snapshot_id"),
        market_snapshot_id=market.get("market_snapshot_id"),
        status="COMPLETED",
        mode=mode,
        universe_count=universe.get("universe_count", 0),
        eligible_count=len(eligible),
        prefilter_count=len(prefiltered),
        watchlist_count=len(pools["watchlist"]),
        ready_count=len(pools["ready"]),
        action_count=len(pools["action"]),
        quality_status=run_quality,
        confidence=run_confidence,
        calculation_version=config.engine_version,
        stock_score_version=config.stock_score_version,
        etf_score_version=config.etf_score_version,
        entry_score_version=config.entry_score_version,
        portfolio_fit_version=config.portfolio_fit_version,
        decision_edge_version=config.decision_edge_version,
        exclusion_counts_json=dict(exclusion_counts),
        metadata_json=metadata,
    )
    db.add(run)
    db.flush()
    for row in selected:
        db.add(
            CandidateScore(
                candidate_run_id=run.id,
                code=row["code"],
                name=row.get("name"),
                security_type=row["security_type"],
                etf_category=row.get("etf_category"),
                stage=row["stage"],
                rank=row.get("rank", 0),
                score=row.get("score"),
                opportunity_score=row.get("opportunity_score"),
                entry_score=row.get("entry_score"),
                portfolio_fit_score=row.get("portfolio_fit_score"),
                action_score=row.get("action_score"),
                edge_vs_no_action=row.get("edge_vs_no_action"),
                edge_vs_current_holdings=row.get("edge_vs_current_holdings"),
                decision_edge=row.get("decision_edge"),
                risk_reward_ratio=row.get("risk_reward_ratio"),
                data_coverage=row.get("data_coverage") or 0.0,
                confidence=row.get("confidence") or 0.0,
                quality_status=row.get("quality_status") or "INSUFFICIENT",
                probe_weight=row.get("probe_weight"),
                funding_mode=row.get("funding_mode"),
                components_json=row.get("components"),
                portfolio_fit_json=row.get("portfolio_fit"),
                entry_json=row.get("entry"),
                comparison_json=row.get("comparison"),
                reason_codes_json=row.get("reason_codes"),
                risk_flags_json=row.get("risk_flags"),
                positive_drivers_json=row.get("positive_drivers"),
                negative_drivers_json=row.get("negative_drivers"),
                blocking_reasons_json=row.get("blocking_reasons"),
                lineage_json={
                    **(row.get("lineage") or {}),
                    "candidate_run_id": run.id,
                    "portfolio_snapshot_id": snapshot.id,
                    "market_score_snapshot_id": market.get("snapshot_id"),
                    "market_snapshot_id": market.get("market_snapshot_id"),
                },
                lifecycle="NEW",
            )
        )
    db.commit()
    db.refresh(run)
    logger.info(
        "candidate_scan portfolio=%s universe=%s eligible=%s prefilter=%s watchlist=%s ready=%s action=%s quality=%s duration_ms=%s",
        portfolio_id,
        universe.get("universe_count", 0),
        len(eligible),
        len(prefiltered),
        len(pools["watchlist"]),
        len(pools["ready"]),
        len(pools["action"]),
        run_quality,
        diagnostics["duration_ms"],
    )
    return _run_payload(run)


def latest_candidate_context(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    snapshot_id: int | None = None,
    as_of: date | datetime | str | None = None,
    max_age_seconds: float | None = None,
    require_reliable: bool = False,
) -> dict[str, Any]:
    moment = _as_of(as_of)
    filters = [CandidateRun.user_id == user_id, CandidateRun.portfolio_id == portfolio_id, CandidateRun.as_of <= moment]
    if snapshot_id is not None:
        filters.append(CandidateRun.portfolio_snapshot_id == snapshot_id)
    row = db.execute(
        select(CandidateRun)
        .where(*filters)
        .order_by(CandidateRun.as_of.desc(), CandidateRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {
            "status": "unavailable",
            "quality_status": "MISSING",
            "confidence": 0.0,
            "run_id": None,
            "watchlist": [],
            "ready": [],
            "action": [],
            "candidates": [],
            "reason": "CANDIDATE_RUN_MISSING",
        }
    payload = _run_payload(row)
    age = max(0.0, (moment - row.captured_at).total_seconds()) if row.captured_at else 0.0
    if require_reliable and (
        row.status != "COMPLETED"
        or str(row.quality_status or "MISSING").upper() in {"MISSING", "BLOCKED", "BLOCKED_FOR_ACTION"}
        or (max_age_seconds is not None and age > max_age_seconds)
    ):
        return {
            "status": "unavailable",
            "quality_status": row.quality_status,
            "confidence": row.confidence,
            "run_id": row.id,
            "portfolio_snapshot_id": row.portfolio_snapshot_id,
            "freshness_seconds": age,
            "watchlist": [],
            "ready": [],
            "action": [],
            "candidates": [],
            "reason": "CANDIDATE_RUN_STALE" if max_age_seconds is not None and age > max_age_seconds else "CANDIDATE_RUN_UNRELIABLE",
        }
    scores = payload["scores"]
    if age > 30 * 60:
        for score in scores:
            if score.get("stage") == "ACTION":
                score["stage"] = "READY"
                score["candidate_engine_stage"] = "READY"
                score["buyable"] = False
                score["actionable"] = False
                score.setdefault("blocking_reasons", []).append("CANDIDATE_ACTION_STALE")
    if age > 60 * 60:
        for score in scores:
            if score.get("stage") == "READY":
                score["stage"] = "WATCHLIST"
                score["candidate_engine_stage"] = "WATCHLIST"
                score["buyable"] = False
                score["actionable"] = False
                score.setdefault("blocking_reasons", []).append("CANDIDATE_READY_STALE")
    payload["scores"] = scores
    payload["watchlist"] = [item for item in scores if item.get("stage") == "WATCHLIST"]
    payload["ready"] = [item for item in scores if item.get("stage") == "READY"]
    payload["action"] = [item for item in scores if item.get("stage") == "ACTION"]
    payload["candidates"] = payload["action"]
    payload["candidate_engine"].update(
        {
            "watchlist_count": len(payload["watchlist"]),
            "ready_count": len(payload["ready"]),
            "action_count": len(payload["action"]),
        }
    )
    payload["status"] = "ready"
    payload["freshness_seconds"] = age
    return payload


def list_candidate_runs(db: Session, *, user_id: int, portfolio_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute(
        select(CandidateRun)
        .where(CandidateRun.user_id == user_id, CandidateRun.portfolio_id == portfolio_id)
        .order_by(CandidateRun.as_of.desc(), CandidateRun.id.desc())
        .limit(max(1, min(int(limit), 200)))
    ).scalars().all()
    return [_run_payload(row)["run"] for row in rows]


def get_candidate_run(db: Session, *, user_id: int, portfolio_id: int, run_id: int) -> dict[str, Any] | None:
    row = db.execute(
        select(CandidateRun).where(
            CandidateRun.id == run_id,
            CandidateRun.user_id == user_id,
            CandidateRun.portfolio_id == portfolio_id,
        )
    ).scalar_one_or_none()
    return _run_payload(row) if row is not None else None


__all__ = [
    "get_candidate_run",
    "latest_candidate_context",
    "list_candidate_runs",
    "scan_candidates",
]
