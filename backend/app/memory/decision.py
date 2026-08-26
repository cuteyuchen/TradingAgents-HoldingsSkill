"""Decision normalization and idempotent immutable-memory capture."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..candidates.models import CandidateRun
from ..decision_contract import ACTIONABLE_HOLDING_ACTIONS, ACTIONABLE_PORTFOLIO_RATINGS
from ..market_engine_models import MarketMetricSnapshot, MarketScoreSnapshot
from ..portfolio_models import PortfolioRiskSnapshot
from ..trigger_models import TriggerEvent
from ..v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot
from .config import DECISION_MEMORY_VERSION
from .models import DecisionMemory

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DECISION_TYPES = frozenset({
    "NO_ACTION",
    "HOLD_ONLY",
    "PORTFOLIO_ACTION",
    "NEW_POSITION_ACTION",
    "MIXED_ACTION",
    "WATCH_ONLY",
})


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _quantity(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def canonical_action(value: Any, *, default: str = "watch") -> str:
    """Reuse the existing action vocabulary instead of creating a parser copy."""

    action = str(value or default).strip().lower()
    return {
        "buy": "add",
        "conditional_buy": "conditional_add",
        "new": "new_position",
        "trim": "reduce",
        "exit_all": "exit",
        "wait": "hold",
        "observe": "watch_only",
        "watch": "watch_only",
        "hold_only": "hold",
        "加仓": "add",
        "条件加仓": "conditional_add",
        "减仓": "reduce",
        "卖出": "sell",
        "清仓": "exit",
        "持有": "hold",
        "观察": "watch_only",
    }.get(action, action)


def _result_payload(run: AnalysisRun) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = run.structured_result_json if isinstance(run.structured_result_json, dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
    return result if isinstance(result, dict) else {}, workflow


def _market_quotes(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    market = payload.get("market_snapshot")
    if not isinstance(market, dict):
        return {}
    quotes = market.get("quotes")
    return quotes if isinstance(quotes, dict) else {}


def _normalise_price_basis(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return text or "RAW_QUOTE"


def _quote_price_basis(quote: dict[str, Any]) -> str:
    metadata = quote.get("metadata") if isinstance(quote.get("metadata"), dict) else {}
    return _normalise_price_basis(
        quote.get("price_basis")
        or quote.get("adjustment")
        or metadata.get("price_basis")
        or metadata.get("adjustment")
    )


def _trusted_quote_price(quote: Any) -> float | None:
    if not isinstance(quote, dict):
        return None
    quality = str(quote.get("quality_status") or "").upper()
    if quality in {"CONFLICT", "INVALID", "MISSING", "STALE"} or quote.get("stale") is True:
        return None
    for field in ("price", "close", "last"):
        value = _number(quote.get(field))
        if value is not None and value > 0:
            return value
    return None


def _candidate_context_row(candidate_context: Any, code: str) -> dict[str, Any] | None:
    if not isinstance(candidate_context, dict):
        return None
    rows = candidate_context.get("action") or candidate_context.get("candidates") or []
    for item in rows:
        if isinstance(item, dict) and str(item.get("code") or "").strip() == code:
            return item
    return None


def _reference_price(
    row: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
    code: str,
    *,
    target_type: str,
    candidate_context: dict[str, Any] | None = None,
) -> tuple[float | None, str, str]:
    """Read only server-owned quote facts, never model-proposed prices."""

    if target_type == "HELD_POSITION":
        quote = quotes.get(code) or {}
        price = _trusted_quote_price(quote)
        if price is not None:
            return price, _quote_price_basis(quote), "analysis_market_quote"
        return None, "missing", "analysis_market_quote"

    if target_type == "NEW_POSITION":
        candidate = _candidate_context_row(candidate_context, code)
        lineage = candidate.get("lineage") if isinstance(candidate, dict) else None
        lineage = lineage if isinstance(lineage, dict) else {}
        run = candidate_context.get("run") if isinstance(candidate_context, dict) else None
        run = run if isinstance(run, dict) else {}
        quote_snapshot_id = lineage.get("quote_snapshot_id") or run.get("quote_snapshot_id")
        price = _number(lineage.get("quote_price"))
        quality = str(lineage.get("quote_quality") or "").upper()
        if (
            quote_snapshot_id
            and price is not None
            and price > 0
            and quality not in {"CONFLICT", "INVALID", "MISSING", "STALE"}
            and not lineage.get("quote_is_proxy")
        ):
            return price, _normalise_price_basis(lineage.get("quote_price_basis")), "candidate_quote_snapshot"
        return None, "missing", "candidate_quote_snapshot"

    return None, "missing", "missing"


def _target_row(
    raw: Any,
    *,
    target_type: str,
    quotes: dict[str, dict[str, Any]],
    default_action: str,
    candidate_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or raw.get("security_code") or "").strip()
    if target_type != "PORTFOLIO" and not code:
        return None
    action = canonical_action(
        raw.get("action") or raw.get("recommended_action") or raw.get("candidate_type"),
        default=default_action,
    )
    if target_type == "NEW_POSITION" and action in {"watch_only", "rotation_watch"}:
        action = "new_position"
    reference_price, reference_basis, reference_source = _reference_price(
        raw,
        quotes,
        code,
        target_type=target_type,
        candidate_context=candidate_context,
    )
    recommended_qty = _quantity(raw.get("recommended_qty") or raw.get("quantity") or raw.get("qty") or raw.get("initial_size"))
    recommended_weight = _number(raw.get("recommended_weight") or raw.get("weight"))
    target_weight = _number(raw.get("target_weight"))
    return {
        "target_type": target_type,
        "target_key": code or "PORTFOLIO",
        "code": code or None,
        "name": raw.get("name") or raw.get("security_name"),
        "security_type": raw.get("security_type") or raw.get("type"),
        "etf_category": raw.get("etf_category"),
        "recommended_action": action,
        "recommended_qty": recommended_qty,
        "recommended_weight": recommended_weight,
        "target_weight": target_weight,
        "reference_price": reference_price,
        "reference_price_basis": reference_basis,
        "reference_price_source": reference_source,
        "reference_at": None,
        "source": dict(raw),
        "opportunity_score": _number(raw.get("opportunity_score")),
        "entry_score": _number(raw.get("entry_score")),
        "portfolio_fit": _number(raw.get("portfolio_fit_score") or raw.get("portfolio_fit")),
        "decision_edge": _number(raw.get("decision_edge")),
    }


def extract_decision_targets(
    result: dict[str, Any],
    workflow: dict[str, Any] | None = None,
    *,
    decision_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract only the final normalized holding/candidate rows."""

    workflow = workflow or {}
    payload = {"market_snapshot": workflow.get("market_snapshot")}
    quotes = _market_quotes(payload)
    candidate_context = workflow.get("candidate_context") if isinstance(workflow.get("candidate_context"), dict) else None
    targets: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    for raw in result.get("holdings") or result.get("today_actions") or []:
        target = _target_row(
            raw,
            target_type="HELD_POSITION",
            quotes=quotes,
            default_action="watch_only",
            candidate_context=candidate_context,
        )
        if target is not None:
            holdings.append(target)
            targets.append(target)
    candidates = result.get("candidates") or result.get("buy_candidates")
    for raw in candidates or []:
        target = _target_row(
            raw,
            target_type="NEW_POSITION",
            quotes=quotes,
            default_action="new_position",
            candidate_context=candidate_context,
        )
        if target is not None:
            targets.append(target)
    if not targets:
        portfolio_action = canonical_action(
            result.get("final_rating")
            or (result.get("portfolio_manager_final") or {}).get("portfolio_rating"),
            default="no_action",
        )
        targets.append({
            "target_type": "PORTFOLIO",
            "target_key": "PORTFOLIO",
            "code": None,
            "name": None,
            "security_type": None,
            "etf_category": None,
            "recommended_action": portfolio_action,
            "recommended_qty": None,
            "recommended_weight": None,
            "target_weight": None,
            "reference_price": None,
            "reference_price_basis": "not_applicable",
            "reference_at": None,
            "source": {},
            "opportunity_score": None,
            "entry_score": None,
            "portfolio_fit": None,
            "decision_edge": None,
        })
    if decision_at is not None:
        for target in targets:
            target["reference_at"] = decision_at.isoformat()
    return holdings, [target for target in targets if target not in holdings]


def determine_decision_type(result: dict[str, Any], quality_status: str | None = None) -> str:
    holdings = result.get("holdings") or result.get("today_actions") or []
    candidates = result.get("candidates") or result.get("buy_candidates") or []
    holding_actions = {
        canonical_action(row.get("action") or row.get("recommended_action"))
        for row in holdings
        if isinstance(row, dict)
    }
    has_holding_action = bool(holding_actions & ACTIONABLE_HOLDING_ACTIONS)
    has_candidate_action = any(
        isinstance(row, dict)
        and canonical_action(
            row.get("action") or row.get("recommended_action") or row.get("candidate_type"),
            default="new_position",
        )
        in {"new_position", "add", "conditional_add"}
        for row in candidates
    )
    final_rating = str(
        result.get("final_rating")
        or (result.get("portfolio_manager_final") or {}).get("portfolio_rating")
        or ""
    ).lower()
    if str(quality_status or "").upper() in {"BLOCKED", "MISSING", "BLOCKED_FOR_ACTION"} or final_rating == "watch_only":
        return "WATCH_ONLY"
    if final_rating == "no_action" and not has_holding_action and not has_candidate_action:
        return "NO_ACTION"
    if has_holding_action and has_candidate_action:
        return "MIXED_ACTION"
    if has_candidate_action:
        return "NEW_POSITION_ACTION"
    if has_holding_action or final_rating in ACTIONABLE_PORTFOLIO_RATINGS:
        return "PORTFOLIO_ACTION"
    if holdings and all(
        canonical_action(row.get("action") or row.get("recommended_action")) in {"hold", "watch_only"}
        for row in holdings
        if isinstance(row, dict)
    ):
        return "HOLD_ONLY"
    return "NO_ACTION"


def _quality_status(result: dict[str, Any]) -> str:
    gate = result.get("quality_gate") if isinstance(result.get("quality_gate"), dict) else {}
    grade = str(result.get("data_quality_grade") or gate.get("grade") or "F").upper()
    if str(gate.get("status") or "").lower() == "blocked" or grade in {"D", "F"}:
        return "BLOCKED"
    if grade == "C":
        return "DEGRADED"
    return "VALID"


def _confidence(value: Any) -> float:
    numeric = _number(value)
    if numeric is not None:
        return max(0.0, min(1.0, numeric / 100.0 if numeric > 1 else numeric))
    return {"high": 0.9, "medium": 0.7, "low": 0.4}.get(str(value or "").lower(), 0.0)


def _candidate_context(result: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
    candidate = workflow.get("candidate_context")
    if isinstance(candidate, dict):
        return candidate
    return result.get("candidate_engine") if isinstance(result.get("candidate_engine"), dict) else {}


def _portfolio_context(result: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
    context = workflow.get("portfolio_context")
    if isinstance(context, dict):
        return context
    engine = result.get("portfolio_engine") if isinstance(result.get("portfolio_engine"), dict) else {}
    context = engine.get("portfolio_context")
    return context if isinstance(context, dict) else {}


def build_decision_features(
    result: dict[str, Any],
    workflow: dict[str, Any],
    *,
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    portfolio = _portfolio_context(result, workflow)
    candidate_context = _candidate_context(result, workflow)
    candidate_run = candidate_context.get("run") if isinstance(candidate_context.get("run"), dict) else {}
    candidate_market = candidate_context.get("market") if isinstance(candidate_context.get("market"), dict) else {}
    if not candidate_market:
        metadata = candidate_run.get("metadata") if isinstance(candidate_run.get("metadata"), dict) else {}
        candidate_market = metadata.get("market") if isinstance(metadata.get("market"), dict) else {}
    market = {
        **candidate_market,
        **(workflow.get("market_state") if isinstance(workflow.get("market_state"), dict) else {}),
    }
    candidates = [row for row in targets if row.get("target_type") == "NEW_POSITION"]
    best = max(candidates, key=lambda row: float(row.get("decision_edge") or -1e9), default={})
    features: dict[str, Any] = {
        "market_regime": portfolio.get("market_regime") or candidate_context.get("market_regime") or market.get("regime"),
        "market_score": _number(market.get("market_score") or market.get("display_score")),
        "market_confidence": _number(market.get("market_confidence") or market.get("confidence")),
        "cash_ratio": _number(portfolio.get("cash_ratio")),
        "gross_exposure": _number(portfolio.get("gross_exposure")),
        "hhi": _number(portfolio.get("hhi")),
        "portfolio_volatility": _number(portfolio.get("portfolio_vol_60") or portfolio.get("portfolio_volatility")),
        "portfolio_vol_60": _number(portfolio.get("portfolio_vol_60")),
        "holding_count": len(result.get("holdings") or result.get("today_actions") or []),
        "candidate_action_count": len(candidates),
        "candidate_best_opportunity": best.get("opportunity_score"),
        "candidate_best_entry": best.get("entry_score"),
        "candidate_best_fit": best.get("portfolio_fit"),
        "candidate_best_edge": best.get("decision_edge"),
        "portfolio_quality": portfolio.get("portfolio_quality"),
        "candidate_quality": candidate_context.get("quality_status") or result.get("candidate_engine", {}).get("quality_status"),
        "analysis_mode": workflow.get("analysis_mode"),
        "target_security_type": best.get("security_type") if best else None,
        "action_type": best.get("recommended_action") if best else canonical_action(result.get("final_rating"), default="no_action"),
    }
    return {key: value for key, value in features.items() if value is not None}


def _lineage_ids(
    db: Session,
    result: dict[str, Any],
    workflow: dict[str, Any],
    *,
    as_of: datetime,
    user_id: int,
    portfolio_id: int,
) -> dict[str, Any]:
    candidate = _candidate_context(result, workflow)
    run_payload = candidate.get("run") if isinstance(candidate.get("run"), dict) else {}
    candidate_run_id = candidate.get("run_id") or run_payload.get("id")
    try:
        candidate_run_id = int(candidate_run_id) if candidate_run_id is not None else None
    except (TypeError, ValueError):
        candidate_run_id = None
    candidate_run = db.execute(select(CandidateRun).where(
        CandidateRun.id == candidate_run_id,
        CandidateRun.user_id == user_id,
        CandidateRun.portfolio_id == portfolio_id,
    )).scalar_one_or_none() if candidate_run_id is not None else None
    candidate_run_id = candidate_run.id if candidate_run is not None else None
    market_score_id = candidate.get("market_score_snapshot_id") or run_payload.get("market_score_snapshot_id")
    market_snapshot_id = candidate.get("market_snapshot_id") or run_payload.get("market_snapshot_id")
    metric_snapshot_id = None
    if market_score_id:
        score = db.execute(select(MarketScoreSnapshot).where(MarketScoreSnapshot.snapshot_id == str(market_score_id))).scalar_one_or_none()
        if score is not None:
            market_score_id = score.snapshot_id
            metric_snapshot_id = score.metric_snapshot_id
            market_snapshot_id = market_snapshot_id or (
                db.execute(select(MarketMetricSnapshot.market_snapshot_id).where(MarketMetricSnapshot.snapshot_id == score.metric_snapshot_id)).scalar_one_or_none()
                if score.metric_snapshot_id else None
            )
        else:
            market_score_id = None
    portfolio_context = _portfolio_context(result, workflow)
    risk_snapshot_id = portfolio_context.get("risk_snapshot_id")
    try:
        risk_snapshot_id = int(risk_snapshot_id) if risk_snapshot_id is not None else None
    except (TypeError, ValueError):
        risk_snapshot_id = None
    snapshot_id = result.get("portfolio_snapshot_id")
    risk_snapshot = db.execute(select(PortfolioRiskSnapshot).where(
        PortfolioRiskSnapshot.id == risk_snapshot_id,
        PortfolioRiskSnapshot.user_id == user_id,
        PortfolioRiskSnapshot.portfolio_id == portfolio_id,
        PortfolioRiskSnapshot.portfolio_snapshot_id == snapshot_id,
    )).scalar_one_or_none() if risk_snapshot_id is not None else None
    risk_snapshot_id = risk_snapshot.id if risk_snapshot is not None else None
    if risk_snapshot_id is None:
        risk_snapshot_id = db.execute(select(PortfolioRiskSnapshot.id).where(
            PortfolioRiskSnapshot.user_id == user_id,
            PortfolioRiskSnapshot.portfolio_id == portfolio_id,
            PortfolioRiskSnapshot.portfolio_snapshot_id == result.get("portfolio_snapshot_id"),
            PortfolioRiskSnapshot.as_of <= as_of,
        ).order_by(PortfolioRiskSnapshot.as_of.desc(), PortfolioRiskSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    trigger = workflow.get("trigger_context") if isinstance(workflow.get("trigger_context"), dict) else {}
    event_ids = trigger.get("trigger_event_ids") or []
    trigger_event_id = event_ids[-1] if event_ids else trigger.get("trigger_event_id")
    try:
        trigger_event_id = int(trigger_event_id) if trigger_event_id is not None else None
    except (TypeError, ValueError):
        trigger_event_id = None
    trigger_event = db.execute(select(TriggerEvent).where(
        TriggerEvent.id == trigger_event_id,
        TriggerEvent.user_id == user_id,
        TriggerEvent.portfolio_id == portfolio_id,
    )).scalar_one_or_none() if trigger_event_id is not None else None
    trigger_event_id = trigger_event.id if trigger_event is not None else None
    context = workflow.get("source_refs") if isinstance(workflow.get("source_refs"), dict) else {}
    refs = {
        **context,
        "analysis_run_id": result.get("analysis_run_id"),
        "analysis_job_id": result.get("analysis_job_id"),
        "portfolio_snapshot_id": result.get("portfolio_snapshot_id"),
        "portfolio_risk_snapshot_id": risk_snapshot_id,
        "candidate_run_id": candidate_run_id,
        "quote_snapshot_id": run_payload.get("quote_snapshot_id") or candidate.get("quote_snapshot_id"),
        "market_score_snapshot_id": market_score_id,
        "market_metric_snapshot_id": metric_snapshot_id,
        "market_snapshot_id": market_snapshot_id,
        "trigger_event_id": trigger_event_id,
    }
    return {key: value for key, value in refs.items() if value is not None}


def capture_decision_memory(
    db: Session,
    analysis_run: AnalysisRun | int,
    *,
    available_at: datetime | None = None,
    commit: bool = False,
) -> DecisionMemory | None:
    """Capture one successful run; failed/cancelled runs are never memories."""

    run = analysis_run if isinstance(analysis_run, AnalysisRun) else db.get(AnalysisRun, analysis_run)
    if run is None:
        return None
    job = run.job
    if job is None and run.job_id:
        job = db.get(AnalysisJob, run.job_id)
    if job is None or str(job.status).lower() != "succeeded":
        return None
    if job.user_id != run.user_id or job.snapshot_id != run.portfolio_snapshot_id:
        return None
    portfolio = db.execute(select(Portfolio).where(
        Portfolio.id == job.portfolio_id,
        Portfolio.user_id == run.user_id,
    )).scalar_one_or_none()
    if portfolio is None:
        return None
    snapshot = db.get(PortfolioSnapshot, run.portfolio_snapshot_id)
    if snapshot is None or snapshot.user_id != run.user_id or snapshot.portfolio_id != job.portfolio_id:
        return None
    existing = db.execute(select(DecisionMemory).where(DecisionMemory.analysis_run_id == run.id)).scalar_one_or_none()
    if existing is not None:
        return existing
    result, workflow = _result_payload(run)
    if not result:
        return None
    decision_at = _utc_naive(run.created_at) or _utc_naive(job.started_at) or _now_utc_naive()
    known_at = _utc_naive(available_at) or _now_utc_naive()
    completion_at = max(
        (
            value
            for value in (
                _utc_naive(getattr(run, "finished_at", None)),
                _utc_naive(job.finished_at),
                decision_at,
            )
            if value is not None
        ),
        default=decision_at,
    )
    if known_at < completion_at:
        known_at = completion_at
    holdings, candidates = extract_decision_targets(result, {**workflow, "market_snapshot": (run.structured_result_json or {}).get("market_snapshot")}, decision_at=decision_at)
    targets = [*holdings, *candidates]
    quality_status = _quality_status(result)
    decision_type = determine_decision_type(result, quality_status)
    portfolio_action = canonical_action(
        result.get("final_rating")
        or (result.get("portfolio_manager_final") or {}).get("portfolio_rating"),
        default="no_action",
    )
    features = build_decision_features(result, workflow, targets=targets)
    market_context = (run.structured_result_json or {}).get("market_snapshot")
    if not isinstance(market_context, dict):
        market_context = {}
    portfolio_context = _portfolio_context(result, workflow)
    candidate_context = _candidate_context(result, workflow)
    refs = _lineage_ids(
        db,
        {
            **result,
            "analysis_run_id": run.id,
            "analysis_job_id": job.id,
            "portfolio_snapshot_id": run.portfolio_snapshot_id,
        },
        workflow,
        as_of=known_at,
        user_id=run.user_id,
        portfolio_id=job.portfolio_id,
    )
    memory = DecisionMemory(
        user_id=run.user_id,
        portfolio_id=job.portfolio_id,
        analysis_run_id=run.id,
        analysis_job_id=job.id,
        portfolio_snapshot_id=run.portfolio_snapshot_id,
        portfolio_risk_snapshot_id=refs.get("portfolio_risk_snapshot_id"),
        candidate_run_id=refs.get("candidate_run_id"),
        trigger_event_id=refs.get("trigger_event_id"),
        market_score_snapshot_id=refs.get("market_score_snapshot_id"),
        market_metric_snapshot_id=refs.get("market_metric_snapshot_id"),
        market_snapshot_id=refs.get("market_snapshot_id"),
        trade_date=decision_at.replace(tzinfo=UTC).astimezone(CHINA_TZ).date(),
        decision_at=decision_at,
        available_at=known_at,
        analysis_mode=str(job.mode or workflow.get("analysis_mode") or "deep"),
        decision_type=decision_type,
        final_rating=result.get("final_rating"),
        portfolio_action=portfolio_action,
        quality_status=quality_status,
        confidence=_confidence(result.get("confidence")),
        market_context_json=market_context,
        portfolio_context_json=portfolio_context,
        candidate_context_json=candidate_context,
        holding_decisions_json=holdings,
        candidate_decisions_json=candidates,
        no_action_context_json={
            "is_no_action": decision_type in {"NO_ACTION", "HOLD_ONLY"},
            "reason": result.get("portfolio_conclusion"),
        },
        decision_features_json=features,
        source_refs_json=refs,
        calculation_version=DECISION_MEMORY_VERSION,
    )
    db.add(memory)
    db.flush()
    try:
        from .outcomes import ensure_outcome_rows

        ensure_outcome_rows(db, memory)
    except Exception:
        if commit:
            db.rollback()
        raise
    if commit:
        db.commit()
        db.refresh(memory)
    return memory


def backfill_decision_memories(
    db: Session,
    *,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    available_at: datetime | None = None,
    limit: int | None = None,
) -> list[DecisionMemory]:
    """Idempotently capture legacy successful runs without rewriting them."""

    from ..v2_models import AnalysisJob

    query = select(AnalysisRun).join(AnalysisJob, AnalysisRun.job_id == AnalysisJob.id).where(
        AnalysisJob.status == "succeeded",
        AnalysisRun.structured_result_json.is_not(None),
    )
    if user_id is not None:
        query = query.where(AnalysisRun.user_id == user_id)
    if portfolio_id is not None:
        query = query.where(AnalysisJob.portfolio_id == portfolio_id)
    query = query.order_by(AnalysisRun.created_at.asc(), AnalysisRun.id.asc())
    if limit is not None:
        query = query.limit(max(1, min(int(limit), 5000)))
    captured: list[DecisionMemory] = []
    for run in db.execute(query).scalars().all():
        memory = capture_decision_memory(db, run, available_at=available_at, commit=False)
        if memory is not None:
            captured.append(memory)
    db.commit()
    return captured


__all__ = [
    "DECISION_TYPES",
    "backfill_decision_memories",
    "build_decision_features",
    "canonical_action",
    "capture_decision_memory",
    "determine_decision_type",
    "extract_decision_targets",
]
