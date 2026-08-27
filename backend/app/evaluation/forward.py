"""Phase J forward-observation campaign and evidence-governance services."""
from __future__ import annotations

import os
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any, Iterable

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..decision_contract import CONTRACT_VERSION
from ..market_models import TradingCalendar
from ..operations.config import ANALYSIS_CHECKPOINTS
from ..operations.models import DailyOperationalCheckpoint, DailyOperationalRun
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from ..v2_models import AnalysisRun, Portfolio
from ..trigger_models import TriggerEvent
from .models import (
    DailyEvidenceSeal,
    DailyObservationCoverage,
    DecisionEpisode,
    DecisionEvaluationOutcome,
    EVALUATION_SCHEMA_VERSION,
    EvaluationSnapshot,
    ObservationCampaign,
    PaperObservation,
    TriggerEvaluation,
)
from .service import (
    PAPER_OBSERVATION_MODE,
    _iso,
    _utc_naive,
    content_hash,
    observe_episode_outcomes,
)

CAMPAIGN_STATUSES = frozenset({"PLANNED", "ACTIVE", "PAUSED", "COMPLETED", "BLOCKED"})
FORWARD_OUTCOME_STATUSES = frozenset({
    "PENDING", "MATURED", "COMPUTED", "MISSING_MARKET_DATA", "ADJUSTMENT_UNCERTAIN", "BLOCKED_DATA_QUALITY",
})


class EpisodeIntegrityAuditor:
    """Executable auditor for frozen forward evidence."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def audit(self, episode: DecisionEpisode) -> dict[str, Any]:
        return _episode_audit(self.db, episode)

    def audit_episode(self, *, episode_id: str | int, user_id: int, portfolio_id: int) -> dict[str, Any]:
        return audit_episode_integrity(self.db, episode_id=episode_id, user_id=user_id, portfolio_id=portfolio_id)


class OutcomeMaturityScheduler:
    """Idempotent trading-calendar driven outcome maturity runner."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, *, campaign_id: str | int, user_id: int, portfolio_id: int, as_of: datetime | None = None, limit: int = 1000) -> dict[str, Any]:
        return mature_campaign_outcomes(
            self.db,
            campaign_id=campaign_id,
            user_id=user_id,
            portfolio_id=portfolio_id,
            as_of=as_of,
            limit=limit,
        )

    process = run


def _now(value: datetime | None = None) -> datetime:
    return _utc_naive(value) or datetime.now(UTC).replace(tzinfo=None)


def _campaign_id() -> str:
    return f"camp_{uuid.uuid4().hex[:24]}"


def _seal_id() -> str:
    return f"seal_{uuid.uuid4().hex[:24]}"


def _runtime_code_commit() -> str | None:
    return os.getenv("GIT_COMMIT") or os.getenv("GITHUB_SHA")


def _trading_days(db: Session, start: date | None, end: date | None) -> list[date]:
    if start is not None and end is not None and end < start:
        return []
    filters = [TradingCalendar.market == "CN", TradingCalendar.is_open.is_(True)]
    if start is not None:
        filters.append(TradingCalendar.trade_date >= start)
    if end is not None:
        filters.append(TradingCalendar.trade_date <= end)
    rows = db.execute(select(TradingCalendar.trade_date).where(*filters).order_by(TradingCalendar.trade_date.asc())).scalars().all()
    return list(rows)


def _expected_trading_day(db: Session, *, campaign: ObservationCampaign, trading_date: date) -> bool:
    if campaign.start_date and trading_date < campaign.start_date:
        return False
    if campaign.end_date and trading_date > campaign.end_date:
        return False
    return db.execute(select(TradingCalendar.trade_date).where(
        TradingCalendar.market == "CN",
        TradingCalendar.trade_date == trading_date,
        TradingCalendar.is_open.is_(True),
    )).scalar_one_or_none() is not None


def _owned_campaign(db: Session, *, campaign_id: str | int, user_id: int, portfolio_id: int | None = None) -> ObservationCampaign | None:
    query = select(ObservationCampaign).where(
        ObservationCampaign.campaign_id == str(campaign_id),
        ObservationCampaign.user_id == user_id,
    )
    if str(campaign_id).isdigit():
        query = select(ObservationCampaign).where(
            (ObservationCampaign.campaign_id == str(campaign_id)) | (ObservationCampaign.id == int(campaign_id)),
            ObservationCampaign.user_id == user_id,
        )
    if portfolio_id is not None:
        query = query.where(ObservationCampaign.portfolio_id == portfolio_id)
    return db.execute(query).scalar_one_or_none()


def _serialize_campaign(row: ObservationCampaign) -> dict[str, Any]:
    return {
        "id": row.id,
        "campaign_id": row.campaign_id,
        "user_id": row.user_id,
        "portfolio_id": row.portfolio_id,
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "started_at": _iso(row.started_at),
        "ended_at": _iso(row.ended_at),
        "timezone": row.timezone,
        "decision_contract_version": row.decision_contract_version,
        "evaluation_schema_version": row.evaluation_schema_version,
        "code_commit": row.code_commit,
        "config_hash": row.config_hash,
        "status": row.status,
        "expected_trading_days": row.expected_trading_days,
        "observed_trading_days": row.observed_trading_days,
        "decision_capture_count": row.decision_capture_count,
        "missed_capture_count": row.missed_capture_count,
        "completed_outcome_count": row.completed_outcome_count,
        "pending_outcome_count": row.pending_outcome_count,
        "data_quality_failure_count": row.data_quality_failure_count,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def create_observation_campaign(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    config_hash: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    portfolio = db.execute(select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)).scalar_one_or_none()
    if portfolio is None:
        raise ValueError("portfolio_not_found")
    if start_date and end_date and end_date < start_date:
        raise ValueError("invalid_campaign_date_range")
    overlap_filters = [
        ObservationCampaign.user_id == user_id,
        ObservationCampaign.portfolio_id == portfolio_id,
        ObservationCampaign.status != "BLOCKED",
        or_(ObservationCampaign.end_date.is_(None), start_date is None, ObservationCampaign.end_date >= start_date),
        or_(end_date is None, ObservationCampaign.start_date.is_(None), ObservationCampaign.start_date <= end_date),
    ]
    if db.execute(select(ObservationCampaign.id).where(*overlap_filters).limit(1)).scalar_one_or_none() is not None:
        raise ValueError("campaign_overlap")
    row = ObservationCampaign(
        campaign_id=_campaign_id(),
        user_id=user_id,
        portfolio_id=portfolio_id,
        start_date=start_date,
        end_date=end_date,
        timezone="Asia/Shanghai",
        decision_contract_version=CONTRACT_VERSION,
        evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
        code_commit=_runtime_code_commit(),
        config_hash=config_hash or content_hash({"campaign": "forward-observation", "schema": EVALUATION_SCHEMA_VERSION}),
        status="PLANNED",
        expected_trading_days=len(_trading_days(db, start_date, end_date)),
        created_at=_now(now),
        updated_at=_now(now),
    )
    db.add(row)
    db.commit()
    return _serialize_campaign(row)


def list_observation_campaigns(db: Session, *, user_id: int, portfolio_id: int) -> list[dict[str, Any]]:
    rows = db.execute(select(ObservationCampaign).where(
        ObservationCampaign.user_id == user_id,
        ObservationCampaign.portfolio_id == portfolio_id,
    ).order_by(ObservationCampaign.created_at.desc(), ObservationCampaign.id.desc())).scalars().all()
    return [_serialize_campaign(row) for row in rows]


def get_observation_campaign(db: Session, *, campaign_id: str | int, user_id: int, portfolio_id: int | None = None) -> dict[str, Any] | None:
    row = _owned_campaign(db, campaign_id=campaign_id, user_id=user_id, portfolio_id=portfolio_id)
    return _serialize_campaign(row) if row else None


def transition_campaign(
    db: Session,
    *,
    campaign_id: str | int,
    user_id: int,
    portfolio_id: int | None = None,
    action: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    row = _owned_campaign(db, campaign_id=campaign_id, user_id=user_id, portfolio_id=portfolio_id)
    if row is None:
        raise ValueError("campaign_not_found")
    moment = _now(now)
    action = action.lower()
    allowed = {
        "start": {"PLANNED", "PAUSED"},
        "pause": {"ACTIVE"},
        "resume": {"PAUSED"},
        "complete": {"ACTIVE", "PAUSED"},
    }
    if action not in allowed:
        raise ValueError("unsupported_campaign_action")
    if row.status not in allowed[action]:
        raise ValueError(f"invalid_campaign_transition:{row.status}:{action}")
    if action in {"start", "resume"}:
        row.status = "ACTIVE"
        row.started_at = row.started_at or moment
    elif action == "pause":
        row.status = "PAUSED"
    else:
        row.status = "COMPLETED"
        row.ended_at = moment
    row.updated_at = moment
    db.commit()
    return _serialize_campaign(row)


def _episode_audit(db: Session, episode: DecisionEpisode) -> dict[str, Any]:
    failures: list[str] = []
    portfolio = db.get(Portfolio, episode.portfolio_id)
    if portfolio is None or portfolio.user_id != episode.user_id:
        failures.append("PORTFOLIO_OWNERSHIP_MISMATCH")
    if episode.status != "FROZEN":
        failures.append("DECISION_NOT_FROZEN")
    if episode.decision_run_id is None or db.get(AnalysisRun, episode.decision_run_id) is None:
        failures.append("MISSING_ANALYSIS_REFERENCE")
    if episode.decision_memory_id is None:
        failures.append("MISSING_DECISION_REFERENCE")
    if episode.source_data_cutoff is None or _utc_naive(episode.source_data_cutoff) > _utc_naive(episode.decision_time):
        failures.append("INVALID_SOURCE_CUTOFF")
    if episode.decision_contract_version != CONTRACT_VERSION:
        failures.append("DECISION_CONTRACT_VERSION_MISMATCH")
    if episode.evaluation_schema_version != EVALUATION_SCHEMA_VERSION:
        failures.append("EVALUATION_SCHEMA_VERSION_MISMATCH")
    if not episode.manifest_hash:
        failures.append("MISSING_MANIFEST_HASH")
    if episode.candidate_stage and episode.candidate_stage.upper() not in {"WATCHLIST", "READY", "ACTION"}:
        failures.append("INVALID_CANDIDATE_STAGE")
    if episode.trigger_snapshot_id is not None:
        trigger = db.get(TriggerEvent, episode.trigger_snapshot_id)
        if trigger is None:
            failures.append("MISSING_TRIGGER_REFERENCE")
        elif trigger.user_id not in {None, episode.user_id} or trigger.portfolio_id not in {None, episode.portfolio_id}:
            failures.append("TRIGGER_OWNERSHIP_MISMATCH")
    snapshots = db.execute(select(EvaluationSnapshot).where(EvaluationSnapshot.episode_id == episode.id)).scalars().all()
    if not snapshots:
        failures.append("MISSING_SNAPSHOT_MANIFEST")
    for snapshot in snapshots:
        if content_hash(snapshot.payload_json or {}) != snapshot.content_hash:
            failures.append("HASH_MISMATCH")
        available = _utc_naive(snapshot.available_at)
        decision_time = _utc_naive(episode.decision_time)
        if available is not None and decision_time is not None and available > decision_time:
            failures.append("LOOKAHEAD_DETECTED")
        if str(snapshot.input_type or "").lower() in {"outcome", "evaluation_outcome"}:
            failures.append("OUTCOME_IN_INPUT_MANIFEST")
    return {
        "episode_id": episode.episode_id,
        "status": "PASS" if not failures else "BLOCKED_EVIDENCE",
        "evidence_status": "PASS" if not failures else "BLOCKED",
        "failures": sorted(set(failures)),
        "manifest_hash": episode.manifest_hash,
        "snapshot_count": len(snapshots),
    }


def audit_episode_integrity(db: Session, *, episode_id: str | int, user_id: int, portfolio_id: int) -> dict[str, Any]:
    query = select(DecisionEpisode).where(
        DecisionEpisode.user_id == user_id,
        DecisionEpisode.portfolio_id == portfolio_id,
    )
    if str(episode_id).isdigit():
        query = query.where((DecisionEpisode.episode_id == str(episode_id)) | (DecisionEpisode.id == int(episode_id)))
    else:
        query = query.where(DecisionEpisode.episode_id == str(episode_id))
    episode = db.execute(query).scalar_one_or_none()
    if episode is None:
        raise ValueError("episode_not_found")
    return _episode_audit(db, episode)


def _coverage_status(*, episode_count: int, valid_count: int, expected: bool = True) -> str:
    if not expected:
        return "MISSED"
    if episode_count == 0:
        return "PARTIAL"
    if valid_count < episode_count:
        return "BLOCKED"
    return "COMPLETE"


def build_daily_observation_coverage(
    db: Session,
    *,
    campaign: ObservationCampaign,
    trading_date: date,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    existing = db.execute(select(DailyObservationCoverage).where(
        DailyObservationCoverage.campaign_id == campaign.id,
        DailyObservationCoverage.trading_date == trading_date,
    )).scalar_one_or_none()
    if existing is not None:
        return serialize_coverage(existing)
    checkpoints = db.execute(select(DailyOperationalCheckpoint).where(
        DailyOperationalCheckpoint.user_id == campaign.user_id,
        DailyOperationalCheckpoint.portfolio_id == campaign.portfolio_id,
        DailyOperationalCheckpoint.trade_date == trading_date,
    )).scalars().all()
    operational_run = db.execute(select(DailyOperationalRun).where(
        DailyOperationalRun.user_id == campaign.user_id,
        DailyOperationalRun.portfolio_id == campaign.portfolio_id,
        DailyOperationalRun.trade_date == trading_date,
    )).scalar_one_or_none()
    episodes = db.execute(select(DecisionEpisode).where(
        DecisionEpisode.user_id == campaign.user_id,
        DecisionEpisode.portfolio_id == campaign.portfolio_id,
        DecisionEpisode.trading_date == trading_date,
        DecisionEpisode.source_mode == PAPER_OBSERVATION_MODE,
    )).scalars().all()
    paper = db.execute(select(PaperObservation).where(
        PaperObservation.episode_id.in_([row.id for row in episodes]) if episodes else PaperObservation.id < 0,
    )).scalars().all()
    audits = [_episode_audit(db, row) for row in episodes]
    valid = sum(item["status"] == "PASS" for item in audits)
    expected = _expected_trading_day(db, campaign=campaign, trading_date=trading_date)
    checkpoint_names = {item.checkpoint_name for item in checkpoints}
    expected_analysis = {item.key for item in ANALYSIS_CHECKPOINTS}
    analysis_actual = expected_analysis & checkpoint_names
    missing: list[str] = []
    if expected and not episodes:
        missing.append("MISSED_DECISION_CAPTURE")
    if episodes and valid < len(episodes):
        missing.extend(sorted({failure for audit in audits for failure in audit["failures"]}))
    if expected and not checkpoints:
        missing.append("MISSING_OPERATIONAL_CHECKPOINTS")
    coverage = DailyObservationCoverage(
        campaign_id=campaign.id,
        user_id=campaign.user_id,
        portfolio_id=campaign.portfolio_id,
        trading_date=trading_date,
        market_coverage={"status": "RECORDED" if operational_run else "MISSING"},
        candidate_coverage={"status": "RECORDED" if operational_run else "MISSING"},
        trigger_coverage={"status": "RECORDED" if operational_run else "MISSING"},
        analysis_coverage={"expected": len(expected_analysis), "actual": len(analysis_actual), "status": "COMPLETE" if expected_analysis <= checkpoint_names else "PARTIAL"},
        decision_coverage={"episode_count": len(episodes), "status": "CAPTURED" if episodes else "MISSED"},
        episode_coverage={"count": len(episodes), "valid": valid, "status": _coverage_status(episode_count=len(episodes), valid_count=valid, expected=expected)},
        snapshot_integrity={"audited": len(audits), "valid": valid, "status": "PASS" if valid == len(audits) else "BLOCKED"},
        data_quality={"paper_observations": len(paper), "failures": sorted(set(missing))},
        status=("BLOCKED" if any(item["status"] != "PASS" for item in audits) else "COMPLETE" if episodes and valid == len(episodes) and paper else "MISSED" if expected and not episodes else "PARTIAL"),
        missing_reasons_json=sorted(set(missing)),
        created_at=_now(now),
        updated_at=_now(now),
    )
    db.add(coverage)
    if commit:
        db.commit()
    return serialize_coverage(coverage)


def serialize_coverage(row: DailyObservationCoverage) -> dict[str, Any]:
    return {
        "id": row.id,
        "campaign_id": row.campaign_id,
        "portfolio_id": row.portfolio_id,
        "trading_date": row.trading_date.isoformat(),
        "market_coverage": row.market_coverage,
        "candidate_coverage": row.candidate_coverage,
        "trigger_coverage": row.trigger_coverage,
        "analysis_coverage": row.analysis_coverage,
        "decision_coverage": row.decision_coverage,
        "episode_coverage": row.episode_coverage,
        "snapshot_integrity": row.snapshot_integrity,
        "data_quality": row.data_quality,
        "status": row.status,
        "missing_reasons": row.missing_reasons_json or [],
        "created_at": _iso(row.created_at),
    }


def campaign_coverage(db: Session, *, campaign_id: str | int, user_id: int, portfolio_id: int) -> dict[str, Any]:
    campaign = _owned_campaign(db, campaign_id=campaign_id, user_id=user_id, portfolio_id=portfolio_id)
    if campaign is None:
        raise ValueError("campaign_not_found")
    rows = db.execute(select(DailyObservationCoverage).where(
        DailyObservationCoverage.campaign_id == campaign.id,
    ).order_by(DailyObservationCoverage.trading_date.asc())).scalars().all()
    return {"campaign": _serialize_campaign(campaign), "source": "FORWARD_ONLY", "days": [serialize_coverage(row) for row in rows]}


def create_daily_evidence_seal(
    db: Session,
    *,
    campaign_id: str | int,
    user_id: int,
    portfolio_id: int,
    trading_date: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    campaign = _owned_campaign(db, campaign_id=campaign_id, user_id=user_id, portfolio_id=portfolio_id)
    if campaign is None:
        raise ValueError("campaign_not_found")
    if (campaign.start_date and trading_date < campaign.start_date) or (campaign.end_date and trading_date > campaign.end_date):
        raise ValueError("trading_date_outside_campaign")
    existing = db.execute(select(DailyEvidenceSeal).where(
        DailyEvidenceSeal.portfolio_id == portfolio_id,
        DailyEvidenceSeal.trading_date == trading_date,
    )).scalar_one_or_none()
    if existing is not None:
        return serialize_seal(existing)
    coverage = build_daily_observation_coverage(db, campaign=campaign, trading_date=trading_date, now=now, commit=False)
    # SessionLocal disables autoflush; materialize a new coverage row before
    # the subsequent idempotency queries and seal insert.
    db.flush()
    episodes = db.execute(select(DecisionEpisode).where(
        DecisionEpisode.user_id == user_id,
        DecisionEpisode.portfolio_id == portfolio_id,
        DecisionEpisode.trading_date == trading_date,
        DecisionEpisode.source_mode == PAPER_OBSERVATION_MODE,
    ).order_by(DecisionEpisode.id.asc())).scalars().all()
    episode_ids = [row.episode_id for row in episodes]
    hashes = [row.manifest_hash for row in episodes if row.manifest_hash]
    coverage_hash = content_hash(coverage)
    payload = {
        "trading_date": trading_date.isoformat(),
        "episode_ids": episode_ids,
        "manifest_hashes": hashes,
        "coverage_hash": coverage_hash,
        "code_commit": campaign.code_commit,
        "decision_contract_version": campaign.decision_contract_version,
        "evaluation_schema_version": campaign.evaluation_schema_version,
    }
    seal = DailyEvidenceSeal(
        seal_id=_seal_id(), campaign_id=campaign.id, user_id=user_id, portfolio_id=portfolio_id,
        trading_date=trading_date, episode_ids_json=episode_ids, manifest_hashes_json=hashes,
        episode_count=len(episodes), coverage_hash=coverage_hash, code_commit=campaign.code_commit,
        decision_contract_version=campaign.decision_contract_version,
        evaluation_schema_version=campaign.evaluation_schema_version,
        evidence_hash=content_hash(payload), status="SEALED", created_at=_now(now),
    )
    db.add(seal)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent worker may have sealed the same portfolio/day first.
        db.rollback()
        existing = db.execute(select(DailyEvidenceSeal).where(
            DailyEvidenceSeal.portfolio_id == portfolio_id,
            DailyEvidenceSeal.trading_date == trading_date,
        )).scalar_one_or_none()
        if existing is None:
            raise
        return serialize_seal(existing)
    return serialize_seal(seal)


def serialize_seal(row: DailyEvidenceSeal) -> dict[str, Any]:
    return {
        "seal_id": row.seal_id,
        "campaign_id": row.campaign_id,
        "portfolio_id": row.portfolio_id,
        "trading_date": row.trading_date.isoformat(),
        "episode_ids": row.episode_ids_json or [],
        "manifest_hashes": row.manifest_hashes_json or [],
        "episode_count": row.episode_count,
        "coverage_hash": row.coverage_hash,
        "evidence_hash": row.evidence_hash,
        "status": row.status,
        "created_at": _iso(row.created_at),
    }


def campaign_integrity(db: Session, *, campaign_id: str | int, user_id: int, portfolio_id: int) -> dict[str, Any]:
    campaign = _owned_campaign(db, campaign_id=campaign_id, user_id=user_id, portfolio_id=portfolio_id)
    if campaign is None:
        raise ValueError("campaign_not_found")
    episode_filters = [
        DecisionEpisode.user_id == user_id,
        DecisionEpisode.portfolio_id == portfolio_id,
        DecisionEpisode.source_mode == PAPER_OBSERVATION_MODE,
    ]
    if campaign.start_date:
        episode_filters.append(DecisionEpisode.trading_date >= campaign.start_date)
    if campaign.end_date:
        episode_filters.append(DecisionEpisode.trading_date <= campaign.end_date)
    episodes = db.execute(select(DecisionEpisode).where(*episode_filters)).scalars().all()
    audits = [_episode_audit(db, row) for row in episodes]
    seals = db.execute(select(DailyEvidenceSeal).where(DailyEvidenceSeal.campaign_id == campaign.id)).scalars().all()
    failures = [audit for audit in audits if audit["status"] != "PASS"]
    return {"source": "FORWARD_ONLY", "campaign": _serialize_campaign(campaign), "status": "PASS" if not failures else "BLOCKED_EVIDENCE", "episodes": len(episodes), "audits": audits, "seals": [serialize_seal(row) for row in seals]}


def mature_campaign_outcomes(
    db: Session,
    *,
    campaign_id: str | int,
    user_id: int,
    portfolio_id: int,
    as_of: datetime | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    campaign = _owned_campaign(db, campaign_id=campaign_id, user_id=user_id, portfolio_id=portfolio_id)
    if campaign is None:
        raise ValueError("campaign_not_found")
    cutoff = _now(as_of)
    local_day = cutoff.replace(tzinfo=UTC).astimezone(CHINA_TZ).date()
    episode_filters = [
        DecisionEpisode.user_id == user_id,
        DecisionEpisode.portfolio_id == portfolio_id,
        DecisionEpisode.source_mode == PAPER_OBSERVATION_MODE,
    ]
    if campaign.start_date:
        episode_filters.append(DecisionEpisode.trading_date >= campaign.start_date)
    if campaign.end_date:
        episode_filters.append(DecisionEpisode.trading_date <= campaign.end_date)
    episodes = db.execute(select(DecisionEpisode).where(*episode_filters).order_by(DecisionEpisode.decision_time.asc()).limit(max(1, min(limit, 5000)))).scalars().all()
    computed = 0
    pending = 0
    missing = 0
    for episode in episodes:
        horizons: list[int] = []
        calendar = TradingCalendarService(db)
        for horizon in (1, 3, 5, 10, 20):
            target = episode.trading_date
            for _ in range(horizon):
                target = calendar.next_trading_day(target)
                if target is None:
                    break
            if target is None or target > local_day:
                continue
            existing = db.execute(select(DecisionEvaluationOutcome).where(
                DecisionEvaluationOutcome.episode_id == episode.id,
                DecisionEvaluationOutcome.target_key == episode.symbol,
                DecisionEvaluationOutcome.horizon_trading_days == horizon,
            )).scalar_one_or_none()
            if existing is None or not existing.observation_complete:
                horizons.append(horizon)
        if not horizons:
            continue
        rows = observe_episode_outcomes(db, episode=episode, as_of=cutoff, horizons=horizons, commit=False)
        for row in rows:
            if row.observation_complete:
                row.status = "ADJUSTMENT_UNCERTAIN" if row.quality_status == "ADJUSTMENT_UNCERTAIN" else "COMPUTED"
                computed += 1
            else:
                row.status = "MISSING_MARKET_DATA" if row.quality_status in {"MISSING_PRICE", "MISSING_MARKET_DATA"} else "BLOCKED_DATA_QUALITY"
                missing += 1
    db.commit()
    for episode in episodes:
        outcomes = db.execute(select(DecisionEvaluationOutcome).where(DecisionEvaluationOutcome.episode_id == episode.id)).scalars().all()
        pending += sum(row.status == "PENDING" or not row.observation_complete for row in outcomes)
    return {"campaign_id": campaign.campaign_id, "source": "FORWARD_ONLY", "computed": computed, "missing_market_data": missing, "pending": pending}


def forward_summary(db: Session, *, campaign_id: str | int, user_id: int, portfolio_id: int) -> dict[str, Any]:
    campaign = _owned_campaign(db, campaign_id=campaign_id, user_id=user_id, portfolio_id=portfolio_id)
    if campaign is None:
        raise ValueError("campaign_not_found")
    episode_filters = [
        DecisionEpisode.user_id == user_id,
        DecisionEpisode.portfolio_id == portfolio_id,
        DecisionEpisode.source_mode == PAPER_OBSERVATION_MODE,
    ]
    if campaign.start_date:
        episode_filters.append(DecisionEpisode.trading_date >= campaign.start_date)
    if campaign.end_date:
        episode_filters.append(DecisionEpisode.trading_date <= campaign.end_date)
    episodes = db.execute(select(DecisionEpisode).where(*episode_filters)).scalars().all()
    outcomes = db.execute(select(DecisionEvaluationOutcome).join(DecisionEpisode, DecisionEvaluationOutcome.episode_id == DecisionEpisode.id).where(
        *episode_filters,
    )).scalars().all()
    no_action = sum(row.decision_type in {"NO_ACTION", "HOLD_ONLY"} or bool(row.no_action_reason) for row in episodes)
    def _metric(field: str) -> dict[str, Any]:
        values = sorted(float(getattr(row, field)) for row in outcomes if getattr(row, field) is not None and row.observation_complete and row.quality_status == "OK")
        n = len(values)
        median = values[n // 2] if n and n % 2 else (sum(values[n // 2 - 1:n // 2 + 1]) / 2 if n else None)
        return {"n": n, "median": median, "status": "INSUFFICIENT_SAMPLE" if n < 5 else "EARLY_FORWARD_EVIDENCE" if n < 30 else "ACCUMULATING"}

    episode_ids = [row.id for row in episodes]
    trigger_rows = db.execute(select(TriggerEvaluation).where(
        TriggerEvaluation.user_id == user_id,
        TriggerEvaluation.portfolio_id == portfolio_id,
        TriggerEvaluation.episode_id.in_(episode_ids) if episode_ids else TriggerEvaluation.id < 0,
    )).scalars().all()
    trigger_effectiveness = {
        "trigger_count": len(trigger_rows),
        "confirmed_trigger": sum(str(row.trigger_status).upper() in {"CONFIRMED", "TRIGGERED"} for row in trigger_rows),
        "analysis_refreshed": sum(bool(row.analysis_refreshed) for row in trigger_rows),
        "decision_unchanged": sum(row.decision_changed is False for row in trigger_rows),
        "decision_changed": sum(row.decision_changed is True for row in trigger_rows),
        "no_action_after_trigger": sum(row.resulting_decision_type in {"NO_ACTION", "HOLD_ONLY"} for row in trigger_rows),
    }
    horizons: dict[str, dict[str, Any]] = {}
    for horizon in (1, 3, 5, 10, 20):
        values = [float(row.raw_return) for row in outcomes if row.horizon_trading_days == horizon and row.observation_complete and row.quality_status == "OK"]
        values.sort()
        n = len(values)
        horizons[str(horizon)] = {
            "n": n,
            "median": values[n // 2] if n and n % 2 else (sum(values[n // 2 - 1:n // 2 + 1]) / 2 if n else None),
            "status": "INSUFFICIENT_SAMPLE" if n < 5 else "EARLY_FORWARD_EVIDENCE" if n < 30 else "ACCUMULATING",
        }
    return {
        "source": "FORWARD_ONLY",
        "campaign": _serialize_campaign(campaign),
        "episodes": len(episodes),
        "unique_trading_days": len({row.trading_date for row in episodes}),
        "unique_symbols": len({row.symbol for row in episodes}),
        "decision_distribution": dict(Counter(row.decision_type for row in episodes)),
        "no_action_count": no_action,
        "no_action_rate": no_action / len(episodes) if episodes else None,
        "candidate_stage_distribution": dict(Counter(row.candidate_stage for row in episodes if row.candidate_stage)),
        "candidate_funnel": dict(Counter(row.candidate_stage for row in episodes if row.candidate_stage)),
        "portfolio_decision_days": len({row.trading_date for row in episodes}),
        "outcomes": len(outcomes),
        "completed_t20": sum(row.horizon_trading_days == 20 and row.observation_complete for row in outcomes),
        "horizons": horizons,
        "risk": {"mfe": _metric("mfe"), "mae": _metric("mae"), "drawdown": _metric("max_drawdown")},
        "trigger_effectiveness": trigger_effectiveness,
        "sample_maturity": "INSUFFICIENT_SAMPLE" if not episodes or len({row.trading_date for row in episodes}) < 5 else "EARLY_FORWARD_EVIDENCE" if len({row.trading_date for row in episodes}) < 30 else "ACCUMULATING",
        "data_quality_failures": dict(Counter(row.quality_status for row in outcomes if row.quality_status not in {"OK", "PENDING"})),
    }


def process_campaign_close(db: Session, *, user_id: int, portfolio_id: int, trading_date: date, now: datetime | None = None) -> dict[str, Any]:
    campaign_filters = [
        ObservationCampaign.user_id == user_id,
        ObservationCampaign.portfolio_id == portfolio_id,
        ObservationCampaign.status == "ACTIVE",
        or_(ObservationCampaign.start_date.is_(None), ObservationCampaign.start_date <= trading_date),
        or_(ObservationCampaign.end_date.is_(None), ObservationCampaign.end_date >= trading_date),
    ]
    campaigns = db.execute(select(ObservationCampaign).where(*campaign_filters)).scalars().all()
    result = []
    for campaign in campaigns:
        coverage = build_daily_observation_coverage(db, campaign=campaign, trading_date=trading_date, now=now, commit=False)
        db.flush()
        seal = create_daily_evidence_seal(db, campaign_id=campaign.campaign_id, user_id=user_id, portfolio_id=portfolio_id, trading_date=trading_date, now=now)
        maturity = mature_campaign_outcomes(db, campaign_id=campaign.campaign_id, user_id=user_id, portfolio_id=portfolio_id, as_of=now)
        campaign.observed_trading_days = db.query(DailyObservationCoverage).filter(DailyObservationCoverage.campaign_id == campaign.id, DailyObservationCoverage.status == "COMPLETE").count()
        decision_filters = [
            DecisionEpisode.user_id == campaign.user_id,
            DecisionEpisode.portfolio_id == campaign.portfolio_id,
            DecisionEpisode.source_mode == PAPER_OBSERVATION_MODE,
        ]
        if campaign.start_date:
            decision_filters.append(DecisionEpisode.trading_date >= campaign.start_date)
        if campaign.end_date:
            decision_filters.append(DecisionEpisode.trading_date <= campaign.end_date)
        campaign.decision_capture_count = db.query(DecisionEpisode).filter(*decision_filters).count()
        campaign.missed_capture_count = db.query(DailyObservationCoverage).filter(DailyObservationCoverage.campaign_id == campaign.id, DailyObservationCoverage.status == "MISSED").count()
        campaign.completed_outcome_count = maturity["computed"]
        campaign.pending_outcome_count = maturity["pending"]
        campaign.data_quality_failure_count = sum(
            len(item.get("missing_reasons", []))
            for item in campaign_coverage(db, campaign_id=campaign.campaign_id, user_id=user_id, portfolio_id=portfolio_id)["days"]
        )
        db.commit()
        result.append({"campaign_id": campaign.campaign_id, "coverage": coverage, "seal": seal, "maturity": maturity})
    return {"trading_date": trading_date.isoformat(), "campaigns": result}


__all__ = [
    "CAMPAIGN_STATUSES",
    "FORWARD_OUTCOME_STATUSES",
    "EpisodeIntegrityAuditor",
    "OutcomeMaturityScheduler",
    "audit_episode_integrity",
    "build_daily_observation_coverage",
    "campaign_coverage",
    "campaign_integrity",
    "create_daily_evidence_seal",
    "create_observation_campaign",
    "forward_summary",
    "get_observation_campaign",
    "list_observation_campaigns",
    "mature_campaign_outcomes",
    "process_campaign_close",
    "serialize_campaign",
    "transition_campaign",
]
