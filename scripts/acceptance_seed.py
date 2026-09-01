"""Seed deterministic facts for the isolated Phase O.1 browser acceptance run.

This module is intentionally a direct ORM fixture builder.  It is only enabled
when ``ACCEPTANCE_MODE=true`` and is never imported by the production app.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

if os.getenv("ACCEPTANCE_MODE", "").lower() != "true":
    raise SystemExit("acceptance_seed.py requires ACCEPTANCE_MODE=true")

from sqlalchemy import select

from app.candidates.models import CandidateRun, CandidateScore
from app.clock import utc_now_naive
from app.database import SessionLocal
from app.governance.models import ParameterChangeProposal, ParameterSetVersion
from app.governance.service import (
    approve_proposal,
    bootstrap_parameter_set,
    create_manual_proposal,
    submit_proposal,
)
from app.market_engine_models import DailyBarCache, AllAMedianIndexDaily, MarketMetricSnapshot, MarketScoreSnapshot
from app.market_models import SecurityMaster, TradingCalendar
from app.market_runtime_models import MarketSnapshot, ProviderHealth, SourceLineage
from app.history.models import SecurityClassificationDaily, SecurityLifecycleEvent, SecurityTradingStatusDaily
from app.memory.decision import capture_decision_memory
from app.portfolio_models import PortfolioRiskSnapshot
from app.research.models import BacktestMetricSlice, BacktestRun, CalibrationReport
from app.shadow.service import (
    create_shadow_account,
    ensure_shadow_order_intents,
    evaluate_live_outcomes,
    persist_live_quote_observation,
    process_pending_shadow_intents,
    refresh_shadow_materialized_state,
)
from app.shadow_models import (
    LiveDecisionObservation,
    LiveQuoteObservation,
    ShadowAccount,
    ShadowOrderIntent,
)
from app.v2_models import (
    AnalysisJob,
    AnalysisRun,
    HoldingItem,
    ModelProfile,
    ModelProvider,
    Portfolio,
    PortfolioSnapshot,
    User,
)
from app.security import hash_password
from app.services.analysis_engine import run_analysis_job
from app.services.model_client import ModelResult
from app.market.providers.acceptance import AcceptanceQuoteProvider

CN = ZoneInfo("Asia/Shanghai")
TRADE_DATE = date(2026, 8, 21)
YESTERDAY = date(2026, 8, 20)
PIT_START_DATE = date(2026, 5, 1)
NOW = datetime(2026, 8, 21, 6, 0)
TODAY_START = datetime(2026, 8, 21, 0, 0)
YESTERDAY_MORNING = datetime(2026, 8, 20, 0, 50)
TODAY_MORNING = datetime(2026, 8, 21, 0, 50)
FUTURE_QUOTE_AT = datetime(2026, 8, 21, 6, 30)

PASSWORD = "AcceptancePass123!"


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _ensure_user(db, *, email: str, username: str) -> User:
    row = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if row is not None:
        return row
    row = User(
        email=email,
        username=username,
        password_hash=hash_password(PASSWORD),
        status="active",
        timezone="Asia/Shanghai",
        created_at=YESTERDAY_MORNING,
    )
    db.add(row)
    db.flush()
    return row


def _ensure_models(db, user: User) -> tuple[ModelProvider, dict[str, ModelProfile]]:
    provider = db.execute(select(ModelProvider).where(
        ModelProvider.user_id == user.id,
        ModelProvider.display_name == "Acceptance Deterministic Provider",
    )).scalar_one_or_none()
    if provider is None:
        provider = ModelProvider(
            user_id=user.id,
            provider="acceptance",
            display_name="Acceptance Deterministic Provider",
            base_url="https://acceptance.invalid",
            enabled=True,
            created_at=YESTERDAY_MORNING,
            updated_at=YESTERDAY_MORNING,
        )
        db.add(provider)
        db.flush()
    profiles: dict[str, ModelProfile] = {}
    for purpose in ("vision", "analysis", "deep_analysis"):
        row = db.execute(select(ModelProfile).where(
            ModelProfile.user_id == user.id,
            ModelProfile.purpose == purpose,
            ModelProfile.model_name == "acceptance-fixture-v1",
        )).scalar_one_or_none()
        if row is None:
            row = ModelProfile(
                user_id=user.id,
                provider_id=provider.id,
                purpose=purpose,
                model_name="acceptance-fixture-v1",
                parameters_json={"stream": False, "max_retries": 0},
                is_default=True,
                created_at=YESTERDAY_MORNING,
                updated_at=YESTERDAY_MORNING,
            )
            db.add(row)
            db.flush()
        profiles[purpose] = row
    return provider, profiles


def _ensure_security_master(db) -> None:
    items = {
        "600519": ("SSE", "贵州茅台", "STOCK", None),
        "510300": ("SSE", "沪深300ETF", "ETF", "BROAD_MARKET"),
        "601318": ("SSE", "中国平安", "STOCK", None),
        "000001": ("SZSE", "平安银行", "STOCK", None),
        "159915": ("SZSE", "创业板ETF", "ETF", "BROAD_MARKET"),
    }
    for code, (exchange, name, security_type, category) in items.items():
        row = db.execute(select(SecurityMaster).where(
            SecurityMaster.market == "CN",
            SecurityMaster.exchange == exchange,
            SecurityMaster.code == code,
        )).scalar_one_or_none()
        if row is None:
            db.add(SecurityMaster(
                market="CN",
                exchange=exchange,
                code=code,
                symbol=code,
                name=name,
                security_type=security_type,
                etf_category=category,
                listing_date=date(2010, 1, 1),
                status="ACTIVE",
                lot_size=100,
                source="acceptance-fixture",
                source_updated_at=YESTERDAY_MORNING,
                raw_metadata_json={"deterministic": True},
            ))


def _ensure_calendar(db) -> None:
    days = _pit_trade_days() + [date(2026, 8, 24)]
    for index, day in enumerate(days):
        row = db.execute(select(TradingCalendar).where(
            TradingCalendar.market == "CN", TradingCalendar.trade_date == day,
        )).scalar_one_or_none()
        if row is None:
            db.add(TradingCalendar(
                market="CN",
                trade_date=day,
                is_open=True,
                previous_trade_date=days[index - 1] if index else None,
                next_trade_date=days[index + 1] if index + 1 < len(days) else None,
                source="acceptance-fixture",
            ))


def _pit_trade_days() -> list[date]:
    return [
        PIT_START_DATE + timedelta(days=offset)
        for offset in range((TRADE_DATE - PIT_START_DATE).days + 1)
        if (PIT_START_DATE + timedelta(days=offset)).weekday() < 5
    ]


def _ensure_pit_facts(db) -> None:
    """Seed the smallest fixed PIT state set needed for portfolio replay."""

    securities = {
        "600519": ("SSE", "STOCK", "贵州茅台"),
        "510300": ("SSE", "ETF", "沪深300ETF"),
        "601318": ("SSE", "STOCK", "中国平安"),
        "000001": ("SZSE", "STOCK", "平安银行"),
        "159915": ("SZSE", "ETF", "创业板ETF"),
    }
    lifecycle_available_at = datetime.combine(PIT_START_DATE, datetime.min.time())
    fixture_days = _pit_trade_days()
    for code, (exchange, security_type, name) in securities.items():
        lifecycle_ref = f"acceptance-pit:lifecycle:{code}"
        lifecycle = db.execute(select(SecurityLifecycleEvent).where(
            SecurityLifecycleEvent.market == "CN",
            SecurityLifecycleEvent.code == code,
            SecurityLifecycleEvent.source_ref == lifecycle_ref,
        )).scalar_one_or_none()
        if lifecycle is None:
            db.add(SecurityLifecycleEvent(
                market="CN", exchange=exchange, code=code, security_type=security_type,
                security_name=name, event_type="LISTED", effective_date=PIT_START_DATE,
                effective_at=lifecycle_available_at, source_available_at=lifecycle_available_at,
                captured_at=lifecycle_available_at, ingested_at=lifecycle_available_at, source="acceptance-fixture",
                source_ref=lifecycle_ref, source_lineage_json={"deterministic": True},
                quality_status="VALID", payload_json={"fixture": "phase-o.1"}, created_at=lifecycle_available_at,
            ))
        base_price = {"600519": 1600.0, "510300": 4.2, "601318": 10.5, "000001": 10.5, "159915": 2.5}[code]
        previous_close = base_price
        for index, day in enumerate(fixture_days):
            available_at = datetime.combine(day, datetime.min.time())
            trading_ref = f"acceptance-pit:trading:{code}:{day.isoformat()}"
            trading = db.execute(select(SecurityTradingStatusDaily).where(
                SecurityTradingStatusDaily.market == "CN",
                SecurityTradingStatusDaily.code == code,
                SecurityTradingStatusDaily.trade_date == day,
                SecurityTradingStatusDaily.source_ref == trading_ref,
            )).scalar_one_or_none()
            if trading is None:
                db.add(SecurityTradingStatusDaily(
                    market="CN", exchange=exchange, code=code, trade_date=day,
                    status="TRADING", reason="acceptance fixture", effective_at=available_at,
                    source_available_at=available_at, captured_at=available_at,
                    ingested_at=available_at, source="acceptance-fixture", source_ref=trading_ref,
                    source_lineage_json={"deterministic": True}, quality_status="VALID", created_at=available_at,
                ))
            classification_ref = f"acceptance-pit:classification:{code}:{day.isoformat()}"
            classification = db.execute(select(SecurityClassificationDaily).where(
                SecurityClassificationDaily.market == "CN",
                SecurityClassificationDaily.code == code,
                SecurityClassificationDaily.trade_date == day,
                SecurityClassificationDaily.source_ref == classification_ref,
            )).scalar_one_or_none()
            if classification is None:
                db.add(SecurityClassificationDaily(
                    market="CN", exchange=exchange, code=code, trade_date=day,
                    classification="NORMAL", confidence=1.0, is_name_derived=False,
                    source_available_at=available_at, captured_at=available_at,
                    ingested_at=available_at, source="acceptance-fixture", source_ref=classification_ref,
                    source_lineage_json={"deterministic": True}, quality_status="VALID", created_at=available_at,
                ))
            close = round(base_price * (1 + index * 0.0005), 4)
            bar = db.execute(select(DailyBarCache).where(
                DailyBarCache.market == "CN",
                DailyBarCache.code == code,
                DailyBarCache.trade_date == day,
                DailyBarCache.adjustment == "QFQ",
            )).scalar_one_or_none()
            if bar is None:
                db.add(DailyBarCache(
                    market="CN", exchange=exchange, code=code, trade_date=day,
                    open=round((previous_close + close) / 2, 4), high=close, low=previous_close,
                    close=close, prev_close=previous_close, volume=1_000_000 + index * 1_000,
                    amount=10_000_000 + index * 10_000, turnover_rate=0.02,
                    adjustment="QFQ", provider="acceptance", fetched_at=available_at,
                    available_at=available_at, quality_status="VALID",
                    metadata_json={"fixture": "phase-o.1", "deterministic": True}, created_at=available_at,
                ))
            previous_close = close
    for index, day in enumerate(fixture_days):
        benchmark = db.execute(select(AllAMedianIndexDaily).where(
            AllAMedianIndexDaily.market == "CN",
            AllAMedianIndexDaily.trade_date == day,
        )).scalars().first()
        if benchmark is None:
            db.add(AllAMedianIndexDaily(
                market="CN", trade_date=day, median_return=0.001,
                index_value=100.0 + index * 0.1, eligible_count=5,
                quality_status="VALID", calculation_version="acceptance-market-v1",
                available_at=datetime.combine(day, datetime.min.time()),
            ))
    db.flush()


def _ensure_market_facts(db) -> dict[str, str]:
    metric_key = "acceptance-metric-2026-08-21"
    score_key = "acceptance-score-2026-08-21"
    market_key = "acceptance-market-2026-08-21"
    metric = db.execute(select(MarketMetricSnapshot).where(MarketMetricSnapshot.snapshot_id == metric_key)).scalar_one_or_none()
    if metric is None:
        metric = MarketMetricSnapshot(
            snapshot_id=metric_key,
            market_snapshot_id=market_key,
            market="CN",
            trade_date=TRADE_DATE,
            captured_at=datetime(2026, 8, 21, 0, 45),
            universe_total=5000,
            included_count=5000,
            excluded_count=0,
            coverage=1.0,
            median_return=0.012,
            advance_ratio=0.62,
            top5_concentration=0.18,
            total_amount=1_000_000_000,
            quality_status="VALID",
            confidence=0.92,
            metrics_json={"fixture": "phase-o.1"},
            breadth_metrics_json={"advance_ratio": 0.62},
            trend_metrics_json={"trend": "RANGE"},
            liquidity_metrics_json={"amount": 1_000_000_000},
        )
        db.add(metric)
    score = db.execute(select(MarketScoreSnapshot).where(MarketScoreSnapshot.snapshot_id == score_key)).scalar_one_or_none()
    if score is None:
        score = MarketScoreSnapshot(
            snapshot_id=score_key,
            metric_snapshot_id=metric_key,
            market="CN",
            trade_date=TRADE_DATE,
            captured_at=datetime(2026, 8, 21, 1, 0),
            raw_score=72.0,
            display_score=72.0,
            regime="NEUTRAL",
            confidence=0.9,
            quality_status="VALID",
            is_frozen=False,
            previous_display_score=70.0,
            available_component_weight=1.0,
            breadth_score=68.0,
            trend_score=70.0,
            liquidity_score=75.0,
            profitability_score=73.0,
            diffusion_score=71.0,
            crowding_score=65.0,
            tail_risk_score=62.0,
            positive_drivers_json=["breadth"],
            negative_drivers_json=["sample is deterministic"],
            metadata_json={"source_lineage_status": "AVAILABLE", "fixture": "phase-o.1"},
        )
        db.add(score)
    market = db.execute(select(MarketSnapshot).where(MarketSnapshot.snapshot_id == market_key)).scalar_one_or_none()
    if market is None:
        db.add(MarketSnapshot(
            snapshot_id=market_key,
            snapshot_key=market_key,
            market="CN",
            started_at=datetime(2026, 8, 21, 0, 59),
            completed_at=datetime(2026, 8, 21, 1, 0),
            trade_date=TRADE_DATE,
            provider="acceptance",
            expected_count=5,
            received_count=5,
            coverage_ratio=1.0,
            quality_status="VALID",
            errors_json=[],
            metadata_json={"fixture": "phase-o.1"},
        ))
    provider = db.execute(select(ProviderHealth).where(
        ProviderHealth.provider_name == "acceptance", ProviderHealth.data_type == "quote",
    )).scalar_one_or_none()
    if provider is None:
        db.add(ProviderHealth(
            provider_name="acceptance", data_type="quote", status="HEALTHY",
            success_count=1, last_success_at=datetime(2026, 8, 21, 1, 0),
        ))
    lineage = db.execute(select(SourceLineage).where(
        SourceLineage.entity_type == "market_snapshot", SourceLineage.entity_key == market_key,
    )).scalar_one_or_none()
    if lineage is None:
        db.add(SourceLineage(
            entity_type="market_snapshot", entity_key=market_key, field_name=None,
            provider="acceptance", provider_endpoint="acceptance://quote-fixture",
            operation="acceptance_seed", source_timestamp=datetime(2026, 8, 21, 1, 0),
            fetched_at=datetime(2026, 8, 21, 1, 0), trade_date=TRADE_DATE,
            fallback_level=0, quality_status="VALID", metadata_json={"deterministic": True},
        ))
    median = db.execute(select(AllAMedianIndexDaily).where(
        AllAMedianIndexDaily.market == "CN", AllAMedianIndexDaily.trade_date == TRADE_DATE,
    )).scalar_one_or_none()
    if median is None:
        db.add(AllAMedianIndexDaily(
            market="CN", trade_date=TRADE_DATE, median_return=0.01, index_value=100.0,
            eligible_count=5000, quality_status="VALID", available_at=datetime(2026, 8, 21, 1, 0),
        ))
    db.flush()
    return {"metric_snapshot_id": metric_key, "score_snapshot_id": score_key, "market_snapshot_id": market_key}


def _ensure_snapshot(db, user: User, portfolio: Portfolio, *, when: datetime, kind: str) -> PortfolioSnapshot:
    row = db.execute(select(PortfolioSnapshot).where(
        PortfolioSnapshot.user_id == user.id,
        PortfolioSnapshot.portfolio_id == portfolio.id,
        PortfolioSnapshot.snapshot_time == when,
    )).scalar_one_or_none()
    if row is not None:
        return row
    if kind == "action":
        holdings = [
            ("600519", "贵州茅台", 100.0, 80.0, 1500.0, 1600.0, 160000.0, 0.8),
            ("510300", "沪深300ETF", 10000.0, 10000.0, 4.0, 4.2, 42000.0, 0.21),
        ]
        total_assets, market_value, cash = 250000.0, 202000.0, 48000.0
    elif kind == "veto":
        holdings = [("000001", "平安银行", 1000.0, 1000.0, 10.0, 10.5, 10500.0, 0.2)]
        total_assets, market_value, cash = 50000.0, 10500.0, 39500.0
    else:
        holdings = [("510300", "沪深300ETF", 10000.0, 10000.0, 4.0, 4.2, 42000.0, 0.42)]
        total_assets, market_value, cash = 100000.0, 42000.0, 58000.0
    row = PortfolioSnapshot(
        user_id=user.id,
        portfolio_id=portfolio.id,
        source="acceptance-fixture",
        snapshot_time=when,
        total_assets=total_assets,
        total_market_value=market_value,
        broker_available_cash=cash,
        corrected_unused_funds=cash,
        repo_or_standard_bond_value=0.0,
        status="confirmed",
        raw_json={"fixture": "phase-o.1", "kind": kind},
    )
    db.add(row)
    db.flush()
    for code, name, qty, available, cost, price, value, weight in holdings:
        db.add(HoldingItem(
            snapshot_id=row.id, code=code, name=name, market="A_SHARE", qty=qty,
            available_qty=available, unavailable_qty=max(qty - available, 0), cost=cost,
            screenshot_price=price, market_value=value, pnl_ratio=0.05, pnl_amount=value * 0.05,
            weight=weight, extra_json={"security_type": "ETF" if code == "510300" else "STOCK", "quote_quality": "VALID"},
        ))
    db.flush()
    return row


def _ensure_risk(db, user: User, portfolio: Portfolio, snapshot: PortfolioSnapshot, market_ids: dict[str, str]) -> None:
    exists = db.execute(select(PortfolioRiskSnapshot).where(
        PortfolioRiskSnapshot.portfolio_snapshot_id == snapshot.id,
    )).scalar_one_or_none()
    if exists is not None:
        return
    db.add(PortfolioRiskSnapshot(
        calculation_key=f"acceptance-risk-{snapshot.id}", user_id=user.id,
        portfolio_id=portfolio.id, portfolio_snapshot_id=snapshot.id,
        market_score_snapshot_id=market_ids["score_snapshot_id"], as_of=snapshot.snapshot_time,
        total_assets=snapshot.total_assets, market_value=snapshot.total_market_value,
        cash_ratio=(snapshot.broker_available_cash or 0) / (snapshot.total_assets or 1),
        gross_exposure=(snapshot.total_market_value or 0) / (snapshot.total_assets or 1),
        top1_weight=0.8, top3_weight=1.0, top5_weight=1.0, hhi=0.68,
        portfolio_vol_20=0.12, portfolio_vol_60=0.15,
        weighted_average_correlation=0.35, max_pairwise_correlation=0.5,
        unclassified_weight=0.0, risk_flags_json=[], position_metrics_json=[],
        correlation_summary_json={"status": "AVAILABLE"}, confidence=0.9,
        quality_status="VALID", calculation_version="portfolio-risk-v1",
        created_at=snapshot.snapshot_time,
    ))


def _result(*, action: str, grade: str, holdings: list[dict], candidates: list[dict] | None = None, reason: str | None = None, veto: bool = False) -> dict:
    blocked = grade in {"BLOCKED", "DATA_GAP"}
    action_name = action.upper()
    candidates = candidates or []
    vetoes = [{"code": "601318", "reason": "候选达到 ACTION，但组合层未批准。"}] if veto else []
    gate_status = "BLOCKED" if blocked else "VETO" if veto else "PASS"
    gate_reasons = [reason or ("关键数据缺失，不能形成可执行建议。" if blocked else "组合层未批准候选。")] if blocked or veto else []
    evidence = {
        "market_read": "验收固定行情已采集。",
        "intent": {"goal": "验证 Phase O.1 浏览器业务闭环"},
        "analyst_reports": [{"role": "technical", "summary": "固定行情证据可追溯。", "evidence": ["acceptance://quote-fixture"]}],
        "holding_evidence": [], "data_gaps": gate_reasons if grade == "DATA_GAP" else [],
        "quality_grade": grade, "source_chain": ["acceptance://quote-fixture", "acceptance://confirmed-snapshot"],
    }
    quality = {
        "grade": grade, "status": "BLOCKED" if blocked else "PASS", "blocked": blocked,
        "blockers": gate_reasons, "new_buy_allowed": not blocked and not veto,
        "action_allowed": not blocked,
    }
    return {
        "data_quality_grade": grade,
        "final_rating": action,
        "final_action": action_name,
        "portfolio_action": action,
        "portfolio_conclusion": reason or ("候选达到 ACTION，但组合层未批准。" if veto else "验收固定事实已完成后端决策。"),
        "confidence": "medium",
        "cash_target": "保持现状",
        "holdings": holdings,
        "today_actions": holdings,
        "candidates": candidates,
        "buy_candidates": candidates,
        "candidate_vetoes": vetoes,
        "candidate_blocked_reason": vetoes[0]["reason"] if vetoes else None,
        "evidence_pack": evidence,
        "quality_gate": quality,
        "investment_debate_state": {"claims": [{"claim_id": "INV-1", "speaker": "bull", "claim": "固定行情支持复核。", "status": "addressed"}]},
        "research_manager_verdict": {"rating": "Hold", "winner": "balanced", "reasoning": "确定性 provider 只用于验收。"},
        "trader_proposal": {"orders": holdings, "checkpoint_rule": "固定检查点复核"},
        "risk_revision": {"decision": "reject" if blocked else "pass", "hard_constraints": ["不得绕过 Portfolio Gate"], "de_risk_triggers": ["quote_missing"]},
        "risk_debate_state": {"claims": [{"claim_id": "RISK-1", "speaker": "conservative", "claim": "现实 live evidence 仍需人工验证。", "status": "open"}]},
        "portfolio_manager_final": {"portfolio_rating": action, "cash_target": "保持现状", "risk_decision": "reject" if blocked else "pass"},
        "rebalance_plan": {"status": "PAPER_ONLY"},
        "checkpoint_plan": "固定检查点复核，不发送真实订单。",
        "reason_codes": gate_reasons,
        "risk_warnings": ["不会发送真实订单"],
        "unresolved_claims": ["现实环境仍需人工确认"],
        "decision_gate": {"status": gate_status, "portfolio_action": action, "blocking_reasons": gate_reasons, "new_buy_allowed": not blocked and not veto},
        "portfolio_engine": {"calculation_version": "portfolio-engine-v1", "portfolio_context": {"portfolio_quality": grade}},
        "market_engine": {"calculation_version": "market-engine-v1"},
    }


def _ensure_manual_run(db, user: User, portfolio: Portfolio, snapshot: PortfolioSnapshot, profile: ModelProfile, *, key: str, when: datetime, action: str, grade: str, reason: str, candidates: list[dict] | None = None, veto: bool = False) -> AnalysisRun:
    existing = next(
        (
            run
            for run in db.execute(
                select(AnalysisRun)
                .join(AnalysisJob)
                .where(AnalysisRun.user_id == user.id, AnalysisJob.portfolio_id == portfolio.id)
            ).scalars()
            if isinstance(run.job.context_json, dict) and run.job.context_json.get("acceptance_key") == key
        ),
        None,
    )
    if existing is not None:
        return existing
    job = AnalysisJob(
        user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, trigger_type="manual",
        checkpoint="10:30", mode="deep", status="succeeded", progress_percent=100,
        current_stage="completed", notify=False, started_at=when - timedelta(minutes=5),
        finished_at=when, context_json={"acceptance_key": key}, created_at=when - timedelta(minutes=6),
    )
    db.add(job)
    db.flush()
    holding_action = "reduce" if action.lower() in {"action", "reduce"} else "hold"
    holding_rows = [{
        "code": item.code, "name": item.name, "action": holding_action if action.lower() in {"action", "reduce"} and item.code == "600519" else "hold",
        "reason": reason, "trigger": "固定事实变化后复核", "quantity": "20" if item.code == "600519" and holding_action == "reduce" else None,
        "take_profit": "达到目标后复核", "stop_loss": "质量门控失效", "invalidation": "关键行情缺失",
    } for item in snapshot.holdings]
    result = _result(action=action, grade=grade, holdings=holding_rows, candidates=candidates, reason=reason, veto=veto)
    market = {
        "status": "VALID" if grade not in {"BLOCKED", "DATA_GAP"} else grade,
        "quality_status": "VALID" if grade not in {"BLOCKED", "DATA_GAP"} else grade,
        "freshness": "FRESH", "trade_date": TRADE_DATE.isoformat(), "score": 72.0, "regime": "NEUTRAL",
        "market_snapshot_id": "acceptance-market-2026-08-21", "market_score_snapshot_id": "acceptance-score-2026-08-21",
        "source_chain": ["acceptance://quote-fixture"], "errors": [reason] if grade == "DATA_GAP" else [],
        "quotes": {item.code: {"code": item.code, "name": item.name, "price": item.screenshot_price, "price_basis": "ACCEPTANCE_FIXTURE", "quality_status": "VALID"} for item in snapshot.holdings if item.code},
    }
    structured = {"result": result, "market_snapshot": market, "input_snapshot": {"holdings": holding_rows}, "workflow": result, "skill_execution": {"mode": "deep", "phases_completed": ["acceptance_fixture"]}}
    run = AnalysisRun(
        job_id=job.id, user_id=user.id, portfolio_snapshot_id=snapshot.id, model_profile_id=profile.id,
        data_quality_grade=grade, summary=reason, final_rating=action, cash_target="保持现状",
        confidence="medium", structured_result_json=structured,
        markdown_text=f"# {action}\n\n{reason}\n\n- checkpoint: 10:30\n- mode: deep\n- acceptance fixture: {key}\n",
        parameter_set_version="1", parameter_set_hash="acceptance-parameter-hash", governance_lineage_json={"fixture": "phase-o.1"}, created_at=when,
    )
    db.add(run)
    db.flush()
    return run


def _ensure_real_analysis_run(
    db,
    user: User,
    portfolio: Portfolio,
    snapshot: PortfolioSnapshot,
    *,
    key: str,
    when: datetime,
) -> AnalysisRun:
    """Create the action fixture through the production analysis worker."""

    existing = next(
        (
            run
            for run in db.execute(
                select(AnalysisRun)
                .join(AnalysisJob)
                .where(AnalysisRun.user_id == user.id, AnalysisJob.portfolio_id == portfolio.id)
            ).scalars()
            if isinstance(run.job.context_json, dict) and run.job.context_json.get("acceptance_key") == key
        ),
        None,
    )
    if existing is not None:
        return existing

    job = AnalysisJob(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        trigger_type="manual",
        checkpoint="10:30",
        mode="fast",
        status="queued",
        progress_percent=0,
        current_stage="queued",
        notify=False,
        context_json={"acceptance_key": key},
        created_at=when - timedelta(minutes=1),
    )
    db.add(job)
    db.commit()
    run_analysis_job(job.id)
    db.expire_all()
    job = db.get(AnalysisJob, job.id)
    run = db.execute(select(AnalysisRun).where(AnalysisRun.job_id == job.id)).scalar_one_or_none() if job else None
    if job is None or job.status != "succeeded" or run is None:
        detail = job.error_message if job else "analysis job disappeared"
        raise RuntimeError(f"acceptance real analysis failed: {detail}")
    return run


def _ensure_candidate(db, user: User, portfolio: Portfolio, snapshot: PortfolioSnapshot, *, key: str, code: str, name: str, stage: str = "ACTION", quality: str = "VALID") -> CandidateRun:
    row = db.execute(select(CandidateRun).where(CandidateRun.calculation_key == key)).scalar_one_or_none()
    if row is not None:
        return row
    row = CandidateRun(
        user_id=user.id, portfolio_id=portfolio.id, calculation_key=key, trade_date=TRADE_DATE,
        as_of=NOW - timedelta(minutes=1), captured_at=NOW - timedelta(minutes=1), portfolio_snapshot_id=snapshot.id,
        market_score_snapshot_id="acceptance-score-2026-08-21", market_snapshot_id="acceptance-market-2026-08-21",
        status="COMPLETED", mode="standard", universe_count=1, structural_candidate_count=1,
        quote_ready_count=1, bar_ready_count=1, quote_coverage=1.0, bar_coverage=1.0,
        eligible_count=1, prefilter_count=1, watchlist_count=0, ready_count=1, action_count=1,
        quality_status=quality, confidence=0.9, metadata_json={"fixture": "phase-o.1"},
        parameter_set_version="1", parameter_set_hash="acceptance-parameter-hash",
    )
    db.add(row)
    db.flush()
    db.add(CandidateScore(
        candidate_run_id=row.id, code=code, name=name, security_type="STOCK", stage=stage, rank=1,
        score=88.0, opportunity_score=90.0, entry_score=84.0, portfolio_fit_score=80.0,
        action_score=86.0, edge_vs_no_action=12.0, edge_vs_current_holdings=8.0, decision_edge=10.0,
        risk_reward_ratio=2.2, data_coverage=1.0, confidence=0.85, quality_status=quality,
        components_json={"catalyst": "acceptance fixture"}, portfolio_fit_json={"fit": "available"},
        entry_json={"trigger": "fixed"}, comparison_json={"vs_no_action": 12},
        reason_codes_json=["ACCEPTANCE_FIXTURE"], risk_flags_json=[], positive_drivers_json=["fixed evidence"],
        negative_drivers_json=["not production evidence"], blocking_reasons_json=[], lineage_json={"fixture": "phase-o.1"}, lifecycle="ACTIVE",
    ))
    db.flush()
    return row


def _ensure_research(db, user: User, portfolio: Portfolio) -> BacktestRun:
    key = "acceptance-backtest-partial-v1"
    row = db.execute(select(BacktestRun).where(BacktestRun.calculation_key == key)).scalar_one_or_none()
    if row is not None:
        return row
    row = BacktestRun(
        user_id=user.id, portfolio_id=portfolio.id, scope="PORTFOLIO_DECISION", replay_mode="DETERMINISTIC_RECOMPUTE",
        start_date=date(2026, 8, 1), end_date=TRADE_DATE, status="COMPLETED", progress_percent=100,
        current_stage="COMPLETED", config_version="candidate-engine-v1", engine_version="historical-replay-v1",
        baseline_config_json={"fixture": "phase-o.1"}, experiment_config_json={"horizons": [1, 5, 10]},
        data_manifest_json={"recompute_capability": {"capability": "PARTIAL_PIT_RECOMPUTE", "status": "PARTIAL_PIT_RECOMPUTE", "limitations": ["历史输入缺失，仅供研究"]}},
        data_hash="acceptance-research-data-hash", calculation_key=key, random_seed=7, sample_count=24,
        unique_trade_dates=16, quality_status="PARTIAL", leakage_status="PASS",
        result_summary_json={"recompute": {"capability": "PARTIAL_PIT_RECOMPUTE", "status": "PARTIAL_PIT_RECOMPUTE"}, "fixture": True},
        failure_counts_json={}, horizons_json=[1, 5, 10], known_limitations_json=["PARTIAL_PIT_RECOMPUTE", "Acceptance fixture"],
        started_at=datetime(2026, 8, 21, 1, 30), completed_at=datetime(2026, 8, 21, 1, 35), created_at=datetime(2026, 8, 21, 1, 25),
        attempt_count=1, parameter_set_version="1", parameter_set_hash="acceptance-parameter-hash",
    )
    db.add(row)
    db.flush()
    db.add(BacktestMetricSlice(
        run_id=row.id, slice_key="acceptance-slice", metric_family="RETURN", security_type="PORTFOLIO",
        market_regime="NEUTRAL", stage="ACTION", horizon=5, sample_count=24, trade_date_count=16,
        coverage=0.8, metrics_json={"mean_return": 0.032, "sample_count": 24}, confidence_interval_json={"low": 0.01, "high": 0.05},
        quality_status="PARTIAL", limitations_json=["PARTIAL_PIT_RECOMPUTE"],
    ))
    calibration = CalibrationReport(
        backtest_run_id=row.id, user_id=user.id, portfolio_id=portfolio.id, status="COMPLETED",
        target_parameter="candidate.ready_opportunity_min", current_value_json=60, challenger_value_json=62,
        recommendation="CONSIDER_CHANGE", train_metrics_json={"sample_count": 12}, validation_metrics_json={"sample_count": 6},
        test_metrics_json={"sample_count": 6}, robustness_json={"status": "PARTIAL"}, sample_counts_json={"total": 24},
        risk_notes_json=["仅供人工治理审核"], proposal_json={"requires_manual_approval": True}, report_json={"fixture": True},
        created_at=datetime(2026, 8, 21, 1, 40),
    )
    db.add(calibration)
    db.flush()
    return row


def _ensure_governance(db, user: User) -> dict[str, int | None]:
    active = db.execute(select(ParameterSetVersion).where(ParameterSetVersion.status == "ACTIVE")).scalar_one_or_none()
    if active is None:
        try:
            active = bootstrap_parameter_set(db)
            db.flush()
        except Exception:
            db.rollback()
            active = db.execute(select(ParameterSetVersion).where(ParameterSetVersion.status == "ACTIVE")).scalar_one_or_none()
    proposal = db.execute(select(ParameterChangeProposal).where(
        ParameterChangeProposal.user_id == user.id, ParameterChangeProposal.target_parameter_key == "candidate.ready_opportunity_min",
    ).order_by(ParameterChangeProposal.id.desc())).scalars().first()
    version = None
    if proposal is None and active is not None:
        try:
            proposal = create_manual_proposal(
                db, target_parameter_key="candidate.ready_opportunity_min", proposed_value=62,
                user_id=user.id, reason="Acceptance governance safety fixture", risk_acknowledged=True,
                risk_summary={"evidence": "deterministic acceptance"},
            )
            submit_proposal(db, proposal=proposal, user_id=user.id)
            version = approve_proposal(db, proposal=proposal, reviewer_user_id=user.id, review_comment="Acceptance review only")
        except Exception:
            db.rollback()
    if proposal is None:
        proposal = db.execute(select(ParameterChangeProposal).where(ParameterChangeProposal.user_id == user.id)).scalars().first()
    if version is None and proposal and proposal.approved_version_id:
        version = db.get(ParameterSetVersion, proposal.approved_version_id)
    db.flush()
    return {"active_version_id": active.id if active else None, "proposal_id": proposal.id if proposal else None, "approved_version_id": version.id if version else None}


def seed(output: Path) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        _ensure_security_master(db)
        _ensure_calendar(db)
        _ensure_pit_facts(db)
        market_ids = _ensure_market_facts(db)
        user_a = _ensure_user(db, email="acceptance-user-a@example.com", username="acceptance-user-a")
        user_b = _ensure_user(db, email="acceptance-user-b@example.com", username="acceptance-user-b")
        _, profiles_a = _ensure_models(db, user_a)
        _, profiles_b = _ensure_models(db, user_b)
        action_portfolio = db.execute(select(Portfolio).where(Portfolio.user_id == user_a.id, Portfolio.name == "Acceptance Action")).scalar_one_or_none()
        if action_portfolio is None:
            action_portfolio = Portfolio(user_id=user_a.id, name="Acceptance Action", market="A_SHARE", currency="CNY", is_default=True, created_at=YESTERDAY_MORNING, updated_at=YESTERDAY_MORNING)
            db.add(action_portfolio)
            db.flush()
        state_portfolio = db.execute(select(Portfolio).where(Portfolio.user_id == user_a.id, Portfolio.name == "Acceptance States")).scalar_one_or_none()
        if state_portfolio is None:
            state_portfolio = Portfolio(user_id=user_a.id, name="Acceptance States", market="A_SHARE", currency="CNY", is_default=False, created_at=YESTERDAY_MORNING, updated_at=YESTERDAY_MORNING)
            db.add(state_portfolio)
            db.flush()
        fresh_portfolio = db.execute(select(Portfolio).where(Portfolio.user_id == user_a.id, Portfolio.name == "Acceptance Freshness")).scalar_one_or_none()
        if fresh_portfolio is None:
            fresh_portfolio = Portfolio(user_id=user_a.id, name="Acceptance Freshness", market="A_SHARE", currency="CNY", is_default=False, created_at=YESTERDAY_MORNING, updated_at=YESTERDAY_MORNING)
            db.add(fresh_portfolio)
            db.flush()
        b_portfolio = db.execute(select(Portfolio).where(Portfolio.user_id == user_b.id, Portfolio.name == "User B Private Portfolio")).scalar_one_or_none()
        if b_portfolio is None:
            b_portfolio = Portfolio(user_id=user_b.id, name="User B Private Portfolio", market="A_SHARE", currency="CNY", is_default=True, created_at=YESTERDAY_MORNING, updated_at=YESTERDAY_MORNING)
            db.add(b_portfolio)
            db.flush()

        action_history_snapshot = _ensure_snapshot(db, user_a, action_portfolio, when=YESTERDAY_MORNING, kind="action")
        action_snapshot = _ensure_snapshot(db, user_a, action_portfolio, when=TODAY_MORNING, kind="action")
        state_snapshot = _ensure_snapshot(db, user_a, state_portfolio, when=TODAY_MORNING, kind="veto")
        fresh_snapshot = _ensure_snapshot(db, user_a, fresh_portfolio, when=YESTERDAY_MORNING, kind="fresh")
        b_snapshot = _ensure_snapshot(db, user_b, b_portfolio, when=TODAY_MORNING, kind="fresh")
        for portfolio, snapshot in (
            (action_portfolio, action_history_snapshot),
            (action_portfolio, action_snapshot),
            (state_portfolio, state_snapshot),
            (fresh_portfolio, fresh_snapshot),
            (b_portfolio, b_snapshot),
        ):
            _ensure_risk(db, user_a if portfolio.user_id == user_a.id else user_b, portfolio, snapshot, market_ids)

        _ensure_candidate(db, user_a, action_portfolio, action_snapshot, key="acceptance-candidate-action", code="159915", name="创业板ETF")
        action_run = _ensure_real_analysis_run(
            db,
            user_a,
            action_portfolio,
            action_snapshot,
            key="action-real",
            when=datetime(2026, 8, 21, 1, 5),
        )
        no_action_run = _ensure_manual_run(db, user_a, state_portfolio, state_snapshot, profiles_a["deep_analysis"], key="no-action", when=datetime(2026, 8, 21, 1, 10), action="no_action", grade="A", reason="NO_ACTION：当前没有需要调整的组合动作。")
        blocked_run = _ensure_manual_run(db, user_a, state_portfolio, state_snapshot, profiles_a["deep_analysis"], key="blocked", when=datetime(2026, 8, 21, 1, 20), action="no_action", grade="BLOCKED", reason="BLOCKED：数据质量门控阻断，Shadow 不执行。")
        data_gap_run = _ensure_manual_run(db, user_a, state_portfolio, state_snapshot, profiles_a["deep_analysis"], key="data-gap", when=datetime(2026, 8, 21, 1, 30), action="no_action", grade="DATA_GAP", reason="DATA_GAP：关键行情不可用，显示不可用而非 0。")
        veto_run = _ensure_manual_run(db, user_a, state_portfolio, state_snapshot, profiles_a["deep_analysis"], key="candidate-veto", when=datetime(2026, 8, 21, 1, 40), action="no_action", grade="A", reason="候选达到 ACTION，但组合层未批准。", candidates=[{"code": "601318", "name": "中国平安", "stage": "ACTION", "action": "new_position", "score": 91, "quality_status": "VALID", "reason": "候选达到 ACTION"}], veto=True)
        freshness_run = _ensure_manual_run(db, user_a, fresh_portfolio, fresh_snapshot, profiles_a["deep_analysis"], key="yesterday-action", when=datetime(2026, 8, 20, 1, 0), action="ACTION", grade="A", reason="昨日 ACTION：今天尚未完成分析。")
        _ensure_candidate(db, user_a, state_portfolio, state_snapshot, key="acceptance-candidate-veto", code="601318", name="中国平安")
        _ensure_manual_run(db, user_b, b_portfolio, b_snapshot, profiles_b["deep_analysis"], key="user-b", when=datetime(2026, 8, 21, 1, 0), action="no_action", grade="A", reason="User B private fixture")
        db.commit()

        # Capture the real derived memory path for at least the action run.
        action_memory = db.execute(select(AnalysisRun).where(AnalysisRun.id == action_run.id)).scalar_one()
        capture_decision_memory(db, action_memory, available_at=action_memory.created_at, commit=False)
        capture_decision_memory(db, freshness_run, available_at=freshness_run.created_at, commit=False)
        db.commit()

        account = db.execute(select(ShadowAccount).where(
            ShadowAccount.user_id == user_a.id,
            ShadowAccount.source_portfolio_id == action_portfolio.id,
        )).scalar_one_or_none()
        if account is None:
            account = create_shadow_account(db, user_id=user_a.id, portfolio_id=action_portfolio.id, snapshot_id=action_snapshot.id, name="Acceptance Shadow")
        observation = db.execute(select(LiveDecisionObservation).where(LiveDecisionObservation.source_analysis_run_id == action_run.id)).scalar_one_or_none()
        if observation is None:
            from app.shadow.service import capture_live_decision_observation
            observation = capture_live_decision_observation(db, action_run, create_shadow_intents=False)
        db.flush()
        if observation is not None:
            ensure_shadow_order_intents(db, observation, account=account)
            quote = AcceptanceQuoteProvider().get_quotes(["600519"])["600519"]
            persist_live_quote_observation(db, quote, captured_at=FUTURE_QUOTE_AT)
            process_pending_shadow_intents(db, now=FUTURE_QUOTE_AT + timedelta(minutes=1), account_id=account.id)
            refresh_shadow_materialized_state(db, account, as_of=FUTURE_QUOTE_AT + timedelta(minutes=1))
            evaluate_live_outcomes(db, as_of=FUTURE_QUOTE_AT + timedelta(minutes=1), observation_id=observation.id)
            existing_intents = db.execute(select(ShadowOrderIntent).where(ShadowOrderIntent.shadow_account_id == account.id)).scalars().all()
            if not any(row.status == "BLOCKED" for row in existing_intents):
                db.add(ShadowOrderIntent(
                    shadow_account_id=account.id, shadow_generation=account.shadow_generation, decision_observation_id=observation.id,
                    action_index=98, code="601318", security_type="STOCK", side="BUY", target_qty=100,
                    decision_reference_price=10.0, decision_reference_basis="ACCEPTANCE_FIXTURE", decision_finalized_at=observation.decision_finalized_at,
                    earliest_executable_at=FUTURE_QUOTE_AT, status="BLOCKED", reason_codes_json=["PORTFOLIO_GATE_VETO"],
                    created_at=observation.created_at, expires_at=FUTURE_QUOTE_AT + timedelta(days=1), idempotency_key=f"acceptance-blocked:{account.id}",
                ))
            if not any(row.status == "EXPIRED" for row in existing_intents):
                db.add(ShadowOrderIntent(
                    shadow_account_id=account.id, shadow_generation=account.shadow_generation, decision_observation_id=observation.id,
                    action_index=99, code="000001", security_type="STOCK", side="BUY", target_qty=100,
                    decision_reference_price=10.0, decision_reference_basis="ACCEPTANCE_FIXTURE", decision_finalized_at=observation.decision_finalized_at,
                    earliest_executable_at=FUTURE_QUOTE_AT, status="EXPIRED", reason_codes_json=["EXPIRED_AT_NEXT_CLOSE"],
                    created_at=observation.created_at, expires_at=FUTURE_QUOTE_AT - timedelta(minutes=1), idempotency_key=f"acceptance-expired:{account.id}",
                ))
        governance = _ensure_governance(db, user_a)
        research = _ensure_research(db, user_a, action_portfolio)
        db.commit()
        facts = {
            "users": {"a": {"email": user_a.email, "password": PASSWORD}, "b": {"email": user_b.email, "password": PASSWORD}},
            "portfolios": {"action": action_portfolio.id, "states": state_portfolio.id, "freshness": fresh_portfolio.id, "user_b": b_portfolio.id},
            "runs": {
                "action": action_run.id,
                "no_action": no_action_run.id,
                "blocked": blocked_run.id,
                "data_gap": data_gap_run.id,
                "veto": veto_run.id,
                "freshness": freshness_run.id,
            },
            "shadow": {"account_id": account.id if account else None, "observation_id": observation.id if 'observation' in locals() and observation else None},
            "research": {"backtest_id": research.id}, "governance": governance,
            "trade_date": TRADE_DATE.isoformat(), "now_utc": NOW.isoformat() + "Z",
        }
        output.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
        return facts
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(seed(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
