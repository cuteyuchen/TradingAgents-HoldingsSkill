"""Run upload + list + detail routes."""
from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_token
from ..database import get_db
from ..schemas import RunCreated, RunSummary, RunUpload
from ..services.alpha import compute_alpha_for_holding
from ..services.pnl import append_unique_corrections, normalize_pnl

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _norm_code(code: str | None) -> str:
    return (code or "").strip().upper()


def _store_run(db: Session, payload: RunUpload) -> tuple[models.Run, dict]:
    """Persist a full run and compute alpha per holding. Returns (run, alphas)."""
    holding_codes = {_norm_code(h.code) for h in payload.holdings}
    candidate_conflicts = [
        {"code": c.code, "name": c.name}
        for c in payload.candidates
        if _norm_code(c.code) in holding_codes
    ]
    evidence_pack = dict(payload.evidence_pack or {})
    if candidate_conflicts:
        evidence_pack["candidate_conflicts_removed"] = candidate_conflicts
        evidence_pack["candidate_policy"] = (
            "今日买入/轮动候选必须是非当前持仓；当前持仓的持有/加仓/减仓只写入交易员方案。"
        )
    normalized_holdings: list[tuple[float | None, float | None]] = []
    pnl_corrections: list[dict] = []
    for h in payload.holdings:
        normalized_pnl, pnl_amount, correction = normalize_pnl(
            h.code, h.name, h.pnl, h.price, h.cost, h.pnl_amount
        )
        normalized_holdings.append((normalized_pnl, pnl_amount))
        if correction:
            pnl_corrections.append(correction)
    if pnl_corrections:
        evidence_pack = append_unique_corrections(evidence_pack, pnl_corrections) or {}

    run = models.Run(
        timestamp=payload.timestamp,
        checkpoint=payload.checkpoint,
        holdings_source=payload.holdings_source,
        data_quality_grade=payload.data_quality_grade,
        intent=payload.intent.model_dump() if payload.intent else None,
        evidence_pack=evidence_pack or None,
        transcript=payload.transcript,
        sections=payload.sections,
        screenshot=payload.screenshot,
    )
    db.add(run)
    db.flush()  # get run.id

    alphas: dict[str, dict] = {}

    # Quality gates.
    for q in payload.quality_gates:
        db.add(models.QualityGate(
            run_id=run.id, analyst=q.analyst, hard_check=q.hard_check,
            llm_review=q.llm_review, grade=q.grade, gaps=q.gaps,
        ))

    # Holdings + indicators + alpha.
    for idx, h in enumerate(payload.holdings):
        normalized_pnl, pnl_amount = normalized_holdings[idx]
        # Compute alpha BEFORE inserting this snapshot (prev = previous same-code run).
        alpha_info = compute_alpha_for_holding(db, h.code, h.price, payload.timestamp)
        alphas[h.code] = alpha_info

        snap = models.HoldingSnapshot(
            run_id=run.id, code=h.code, name=h.name, qty=h.qty,
            available_qty=h.available_qty, cost=h.cost, price=h.price,
            market_value=h.market_value, pnl=normalized_pnl, pnl_amount=pnl_amount,
            data_quality=h.data_quality,
            raw_return=alpha_info["raw_return"],
            benchmark_return=alpha_info["benchmark_return"],
            alpha=alpha_info["alpha"],
        )
        db.add(snap)
        db.flush()
        if h.indicators:
            ind = h.indicators
            db.add(models.HoldingIndicator(
                snapshot_id=snap.id,
                quote=ind.quote.model_dump() if ind.quote else None,
                technicals=ind.technicals.model_dump() if ind.technicals else None,
                vpa=ind.vpa.model_dump() if ind.vpa else None,
                fund_flow=ind.fund_flow.model_dump() if ind.fund_flow else None,
                red_flags=ind.red_flags,
            ))

    # Claims.
    for c in payload.claims:
        db.add(models.Claim(
            run_id=run.id, claim_id=c.claim_id, speaker=c.speaker, stance=c.stance,
            claim=c.claim, evidence=c.evidence, confidence=c.confidence,
            status=c.status, target_claim_ids=c.target_claim_ids, round=c.round,
        ))

    # Research verdict.
    if payload.research_verdict:
        rv = payload.research_verdict
        db.add(models.ResearchVerdict(
            run_id=run.id, rating=rv.rating, winner=rv.winner, rationale=rv.rationale,
            unresolved_handling=rv.unresolved_handling, strategy=rv.strategy, confidence=rv.confidence,
        ))

    # Trader proposals + revisions.
    for tp in payload.trader_proposals:
        prop = models.TraderProposal(
            run_id=run.id, code=tp.code, action=tp.action, trigger_price=tp.trigger_price,
            qty=tp.qty, take_profit=tp.take_profit, stop_loss=tp.stop_loss,
            invalidation=tp.invalidation, checkpoint_rule=tp.checkpoint_rule,
        )
        db.add(prop)
        db.flush()
        if tp.revision:
            r = tp.revision
            db.add(models.RiskRevision(
                proposal_id=prop.id, verdict=r.verdict, hard_constraints=r.hard_constraints,
                soft_constraints=r.soft_constraints, execution_preconditions=r.execution_preconditions,
                de_risk_triggers=r.de_risk_triggers, revision_reason=r.revision_reason,
                revised_proposal=r.revised_proposal,
            ))

    # PM final.
    if payload.pm_final:
        pm = payload.pm_final
        db.add(models.PortfolioManagerFinal(
            run_id=run.id, rating=pm.rating, cash_target=pm.cash_target,
            actions=pm.actions, priority_notes=pm.priority_notes,
        ))

    # Candidates.
    for c in payload.candidates:
        if _norm_code(c.code) in holding_codes:
            continue
        db.add(models.Candidate(
            run_id=run.id, code=c.code, name=c.name, type=c.type, score=c.score,
            score_breakdown=c.score_breakdown, entry_trigger=c.entry_trigger,
            initial_size=c.initial_size, take_profit_1=c.take_profit_1,
            take_profit_2=c.take_profit_2, stop_loss=c.stop_loss,
            invalidation=c.invalidation, status=c.status,
        ))

    db.commit()
    db.refresh(run)
    return run, alphas


@router.post("", response_model=RunCreated, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: RunUpload,
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
) -> RunCreated:
    run, alphas = _store_run(db, payload)
    return RunCreated(run_id=run.id, alphas=alphas)


@router.get("", response_model=list[RunSummary])
def list_runs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    code: str | None = Query(None),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None),
    checkpoint: str | None = Query(None),
    grade: str | None = Query(None),
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
) -> list[RunSummary]:
    q = db.query(models.Run)
    if code:
        q = q.join(models.HoldingSnapshot).filter(models.HoldingSnapshot.code == code)
    if from_date:
        q = q.filter(models.Run.timestamp >= _parse_date_bound(from_date, end=False))
    if to_date:
        q = q.filter(models.Run.timestamp <= _parse_date_bound(to_date, end=True))
    if checkpoint:
        q = q.filter(models.Run.checkpoint == checkpoint)
    if grade:
        q = q.filter(models.Run.data_quality_grade == grade.upper())
    runs = q.order_by(models.Run.timestamp.desc()).offset(offset).limit(limit).all()
    out = []
    for r in runs:
        pm_rating = r.pm_final.rating if r.pm_final else None
        out.append(RunSummary(
            id=r.id, timestamp=r.timestamp, checkpoint=r.checkpoint,
            data_quality_grade=r.data_quality_grade, pm_rating=pm_rating,
            holdings_count=len(r.holdings), candidates_count=len(_non_conflicting_candidates(r)),
        ))
    return out


@router.get("/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    run = db.get(models.Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return _serialize_run(run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    run_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
) -> None:
    run = db.get(models.Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    db.delete(run)
    db.commit()


def _serialize_run(run: models.Run) -> dict:
    """Full nested serialization for the detail view."""
    return {
        "id": run.id,
        "timestamp": run.timestamp.isoformat(),
        "checkpoint": run.checkpoint,
        "holdings_source": run.holdings_source,
        "data_quality_grade": run.data_quality_grade,
        "intent": run.intent,
        "evidence_pack": run.evidence_pack,
        "transcript": run.transcript,
        "sections": run.sections,
        "screenshot": run.screenshot,
        "quality_gates": [
            {"analyst": q.analyst, "hard_check": q.hard_check, "llm_review": q.llm_review,
             "grade": q.grade, "gaps": q.gaps}
            for q in run.quality_gates
        ],
        "holdings": [_serialize_holding(h) for h in run.holdings],
        "claims": [
            {"claim_id": c.claim_id, "speaker": c.speaker, "stance": c.stance, "claim": c.claim,
             "evidence": c.evidence, "confidence": c.confidence, "status": c.status,
             "target_claim_ids": c.target_claim_ids, "round": c.round}
            for c in run.claims
        ],
        "research_verdict": _one(run.research_verdict),
        "trader_proposals": [_serialize_proposal(p) for p in run.trader_proposals],
        "pm_final": _one(run.pm_final),
        "candidates": [
            {"code": c.code, "name": c.name, "type": c.type, "score": c.score,
             "score_breakdown": c.score_breakdown, "entry_trigger": c.entry_trigger,
             "initial_size": c.initial_size, "take_profit_1": c.take_profit_1,
             "take_profit_2": c.take_profit_2, "stop_loss": c.stop_loss,
             "invalidation": c.invalidation, "status": c.status}
            for c in _non_conflicting_candidates(run)
        ],
    }


def _serialize_holding(h: models.HoldingSnapshot) -> dict:
    ind = h.indicators
    pnl, pnl_amount, _correction = normalize_pnl(h.code, h.name, h.pnl, h.price, h.cost, h.pnl_amount)
    return {
        "code": h.code, "name": h.name, "qty": h.qty, "available_qty": h.available_qty,
        "cost": h.cost, "price": h.price, "market_value": h.market_value, "pnl": pnl,
        "pnl_amount": pnl_amount,
        "data_quality": h.data_quality, "raw_return": h.raw_return,
        "benchmark_return": h.benchmark_return, "alpha": h.alpha,
        "indicators": {
            "quote": ind.quote if ind else None,
            "technicals": ind.technicals if ind else None,
            "vpa": ind.vpa if ind else None,
            "fund_flow": ind.fund_flow if ind else None,
            "red_flags": ind.red_flags if ind else None,
        } if ind else None,
    }


def _serialize_proposal(p: models.TraderProposal) -> dict:
    r = p.revision
    return {
        "code": p.code, "action": p.action, "trigger_price": p.trigger_price,
        "qty": p.qty, "take_profit": p.take_profit, "stop_loss": p.stop_loss,
        "invalidation": p.invalidation, "checkpoint_rule": p.checkpoint_rule,
        "revision": {
            "verdict": r.verdict, "hard_constraints": r.hard_constraints,
            "soft_constraints": r.soft_constraints,
            "execution_preconditions": r.execution_preconditions,
            "de_risk_triggers": r.de_risk_triggers, "revision_reason": r.revision_reason,
            "revised_proposal": r.revised_proposal,
        } if r else None,
    }


def _one(obj) -> dict | None:
    if obj is None:
        return None
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != "id" and c.name != "run_id"}


def _non_conflicting_candidates(run: models.Run) -> list[models.Candidate]:
    holding_codes = {_norm_code(h.code) for h in run.holdings}
    return [c for c in run.candidates if _norm_code(c.code) not in holding_codes]


def _parse_date_bound(value: str, end: bool) -> datetime:
    try:
        if len(value) == 10:
            parsed_date = datetime.fromisoformat(value).date()
            return datetime.combine(parsed_date, time.max if end else time.min)
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date filter: {value}") from exc
