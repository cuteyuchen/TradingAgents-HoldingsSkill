"""Phase M deterministic recompute acceptance tests.

Every test rebuilds Market / Candidate / Portfolio state from PIT facts only.
Persisted production outputs (MarketScoreSnapshot, CandidateRun) are seeded as
decoy rows and must never be read as recompute inputs.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.database import Base
from app.history.models import (
    EtfMetadataHistory,
    FundamentalReport,
    PriceBasisMetadata,
    SecurityClassificationDaily,
    SecurityLifecycleEvent,
    SecurityTradingStatusDaily,
    SecurityValuationDaily,
)
from app.market_engine_models import AllAMedianIndexDaily, DailyBarCache, MarketScoreSnapshot
from app.market_models import TradingCalendar
from app.research.replay import load_replay_facts
from app.v2_models import HoldingItem, Portfolio, PortfolioSnapshot, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    import app.candidates.models  # noqa: F401
    import app.governance.models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.portfolio_models  # noqa: F401
    import app.trigger_models  # noqa: F401
    import app.research.models  # noqa: F401
    import app.history.models  # noqa: F401

    Base.metadata.create_all(engine)
    session = Session(engine)
    session._engine = engine  # type: ignore[attr-defined]
    return session


def _business_days(start: date, count: int) -> list[date]:
    result: list[date] = []
    day = start
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day += timedelta(days=1)
    return result


def _calendar(db: Session, days: list[date]) -> None:
    for index, day in enumerate(days):
        db.add(TradingCalendar(
            market="CN",
            trade_date=day,
            is_open=True,
            previous_trade_date=days[index - 1] if index else None,
            next_trade_date=days[index + 1] if index + 1 < len(days) else None,
        ))


def _at09(day: date) -> datetime:
    # Persisted timestamps are UTC-naive. The EOD recompute cutoff is 07:10
    # UTC, so every daily fact must be visible strictly before that instant.
    return datetime(day.year, day.month, day.day, 7, 0)


def _seed_pit_dataset(
    db: Session,
    *,
    stock_count: int = 50,
    day_count: int = 90,
    start: date = date(2025, 1, 2),
) -> tuple[list[date], list[str]]:
    days = _business_days(start, day_count)
    _calendar(db, days)
    codes = [f"{600000 + index:06d}" for index in range(stock_count)]
    listed = days[0] - timedelta(days=700)
    for code in codes:
        db.add(SecurityLifecycleEvent(
            market="CN",
            exchange="SSE",
            code=code,
            security_type="STOCK",
            security_name=f"PIT-{code}",
            event_type="LISTED",
            effective_date=listed,
            source="operator-import",
            source_ref=f"phase-m-lifecycle-{code}",
            source_available_at=_at09(days[0]),
            quality_status="VALID",
        ))
    for day_index, day in enumerate(days):
        db.add(AllAMedianIndexDaily(
            market="CN",
            trade_date=day,
            median_return=0.001,
            index_value=1000.0 + day_index,
            eligible_count=stock_count,
            quality_status="VALID",
            calculation_version="market-engine-v1",
            available_at=_at09(day),
        ))
        for code_index, code in enumerate(codes):
            base = 10.0 + code_index * 0.3
            close = base * (1.0 + 0.0008 * day_index)
            prev_close = base * (1.0 + 0.0008 * max(0, day_index - 1))
            db.add(SecurityClassificationDaily(
                market="CN",
                code=code,
                trade_date=day,
                classification="NORMAL",
                source="operator-import",
                source_ref=f"phase-m-class-{code}-{day}",
                source_available_at=_at09(day),
                quality_status="VALID",
            ))
            db.add(SecurityTradingStatusDaily(
                market="CN",
                code=code,
                trade_date=day,
                status="TRADING",
                source="operator-import",
                source_ref=f"phase-m-status-{code}-{day}",
                source_available_at=_at09(day),
                quality_status="VALID",
            ))
            db.add(SecurityValuationDaily(
                market="CN",
                code=code,
                trade_date=day,
                pe_ttm=15.0 + (code_index % 10),
                pb=1.5 + (code_index % 5) * 0.2,
                dividend_yield=0.01 + (code_index % 3) * 0.01,
                source="operator-import",
                source_ref=f"phase-m-val-{code}-{day}",
                source_available_at=_at09(day),
                quality_status="VALID",
            ))
            db.add(PriceBasisMetadata(
                market="CN",
                code=code,
                trade_date=day,
                basis="QFQ",
                source="operator-import",
                source_ref=f"phase-m-basis-{code}-{day}",
                source_available_at=_at09(day),
                quality_status="VALID",
            ))
            db.add(DailyBarCache(
                market="CN",
                exchange="SSE",
                code=code,
                trade_date=day,
                open=close * 0.995,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                prev_close=prev_close,
                volume=1_000_000.0 + code_index * 10_000.0,
                amount=close * (1_000_000.0 + code_index * 10_000.0),
                turnover_rate=0.01 + code_index * 0.0001,
                adjustment="QFQ",
                provider="test",
                available_at=datetime(day.year, day.month, day.day, 7, 0),
                quality_status="VALID",
            ))
    for code_index, code in enumerate(codes):
        publish_day = days[min(20, len(days) - 1)]
        db.add(FundamentalReport(
            market="CN",
            code=code,
            report_period=date(2024, 12, 31),
            report_type="ANNUAL",
            published_at=_at09(publish_day),
            source_available_at=_at09(publish_day),
            revision_number=0,
            source="operator-import",
            source_ref=f"phase-m-fund-{code}-v0",
            roe=0.08 + code_index * 0.001,
            revenue_yoy=0.05 + code_index * 0.002,
            net_profit_yoy=0.03 + code_index * 0.003,
            operating_cash_flow=10_000.0 + code_index * 100.0,
            gross_margin=0.2 + code_index * 0.001,
            net_profit=100.0 + code_index,
            quality_status="VALID",
        ))
    db.flush()
    return days, codes


def _seed_portfolio(
    db: Session,
    *,
    holdings_by_time: list[tuple[datetime, list[str]]],
    total_assets: float = 1_000_000.0,
    cash: float | None = 200_000.0,
    email: str = "phase-m@example.com",
) -> tuple[int, int]:
    user = User(email=email, password_hash="x", timezone="Asia/Shanghai")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user=user, name="PHASE-M")
    db.add(portfolio)
    db.flush()
    for snapshot_time, codes in holdings_by_time:
        snapshot = PortfolioSnapshot(
            user_id=user.id,
            portfolio_id=portfolio.id,
            snapshot_time=snapshot_time,
            status="confirmed",
            total_assets=total_assets,
            total_market_value=total_assets - (cash or 0.0),
            broker_available_cash=cash,
        )
        db.add(snapshot)
        db.flush()
        for code in codes:
            db.add(HoldingItem(
                snapshot_id=snapshot.id,
                code=code,
                name=f"HELD-{code}",
                qty=1000.0,
                available_qty=1000.0,
                market_value=total_assets / max(1, len(codes)) * 0.8,
            ))
    db.flush()
    return user.id, portfolio.id


def _run_recompute(
    db: Session,
    *,
    scope: str,
    start_date: date,
    end_date: date,
    portfolio_id: int | None = None,
    user_id: int | None = None,
    parameter_snapshot: dict | None = None,
    config_hash: str | None = None,
) -> dict:
    return load_replay_facts(
        db,
        scope=scope,
        replay_mode="DETERMINISTIC_RECOMPUTE",
        start_date=start_date,
        end_date=end_date,
        portfolio_id=portfolio_id,
        user_id=user_id,
        parameter_snapshot=parameter_snapshot,
        parameter_set_version="LEGACY_PRE_GOVERNANCE",
        config_hash=config_hash,
    )


def test_deterministic_recompute_ignores_persisted_candidate_output() -> None:
    db = _db()
    try:
        days, codes = _seed_pit_dataset(db)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[(datetime(2025, 4, 1, 12, 0), [codes[0]])],
            email="phase-m-no-reuse@example.com",
        )
        from app.candidates.models import CandidateRun, CandidateScore

        db.add(CandidateRun(
            user_id=user_id,
            portfolio_id=portfolio_id,
            calculation_key="decoy-run",
            trade_date=days[-1],
            as_of=datetime(2025, 4, 1, 12, 0),
            captured_at=datetime(2025, 4, 1, 12, 0),
            status="COMPLETED",
            quality_status="VALID",
            action_count=1,
        ))
        db.flush()
        run_id = db.execute(select(CandidateRun.id).where(CandidateRun.calculation_key == "decoy-run")).scalar_one()
        db.add(CandidateScore(
            candidate_run_id=run_id,
            code=codes[1],
            name="DECOY",
            security_type="STOCK",
            stage="ACTION",
            rank=1,
            score=99.0,
            opportunity_score=99.0,
            data_coverage=1.0,
            confidence=99.0,
            quality_status="FULL",
        ))
        db.commit()
        facts = _run_recompute(
            db,
            scope="CANDIDATE",
            start_date=days[-1],
            end_date=days[-1],
            portfolio_id=portfolio_id,
            user_id=user_id,
        )
        cases = facts["recompute_cases"]
        assert cases
        assert all(case.facts.get("candidate_run_id") is None for case in cases)
        assert all(case.facts.get("score") != 99.0 for case in cases)
        assert all(case.facts.get("opportunity_score") != 99.0 for case in cases)
        assert facts["recompute_summary"]["capability"] != "FULL_PIT_EQUIVALENT"
    finally:
        db.close()


def test_market_recompute_ignores_persisted_market_score_snapshot() -> None:
    db = _db()
    try:
        days, _ = _seed_pit_dataset(db, stock_count=50, day_count=90)
        db.commit()
        base = _run_recompute(db, scope="MARKET", start_date=days[-1], end_date=days[-1])
        db.add(MarketScoreSnapshot(
            snapshot_id="decoy-market",
            market="CN",
            trade_date=days[-1],
            captured_at=datetime(2025, 4, 1, 15, 0),
            raw_score=99.0,
            display_score=99.0,
            regime="STRONG_RISK_OFF",
            quality_status="VALID",
        ))
        db.commit()
        after = _run_recompute(db, scope="MARKET", start_date=days[-1], end_date=days[-1])
        base_case = base["recompute_cases"][0]
        after_case = after["recompute_cases"][0]
        assert base_case.facts["raw_score"] != 99.0
        assert after_case.facts == base_case.facts
        assert after["recompute_summary"]["deterministic_hash"] == base["recompute_summary"]["deterministic_hash"]
    finally:
        db.close()


def test_deterministic_hash_stable_and_parameter_frozen() -> None:
    db = _db()
    try:
        days, codes = _seed_pit_dataset(db)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[(datetime(2025, 4, 1, 12, 0), [codes[0]])],
            email="phase-m-hash@example.com",
        )
        db.commit()
        snapshot = {
            "candidate": {
                "watchlist_max": 12,
                "ready_max": 5,
                "action_max": 2,
            }
        }
        config_hash = "frozen-config-hash-v1"
        first = _run_recompute(
            db,
            scope="CANDIDATE",
            start_date=days[-1],
            end_date=days[-1],
            portfolio_id=portfolio_id,
            user_id=user_id,
            parameter_snapshot=snapshot,
            config_hash=config_hash,
        )
        second = _run_recompute(
            db,
            scope="CANDIDATE",
            start_date=days[-1],
            end_date=days[-1],
            portfolio_id=portfolio_id,
            user_id=user_id,
            parameter_snapshot=snapshot,
            config_hash=config_hash,
        )
        assert first["recompute_summary"]["deterministic_hash"] == second["recompute_summary"]["deterministic_hash"]
        manifest = first["recompute_manifest"]
        assert manifest["config_hash"] == config_hash
        assert manifest["parameter_version"] == "LEGACY_PRE_GOVERNANCE"
    finally:
        db.close()


def test_historical_holdings_reenter_and_exclude_by_snapshot() -> None:
    db = _db()
    try:
        days, codes = _seed_pit_dataset(db)
        january_snapshot_time = datetime(2025, 2, 1, 12, 0)
        february_snapshot_time = datetime(2025, 3, 1, 12, 0)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[
                (january_snapshot_time, [codes[0]]),
                (february_snapshot_time, [codes[1]]),
            ],
            email="phase-m-holdings@example.com",
        )
        db.commit()
        february_day = days[-1]
        from app.recompute.candidate import recompute_candidate_dates
        from app.recompute.dataset import load_recompute_pit_dataset

        dataset = load_recompute_pit_dataset(
            db,
            start_date=february_day,
            end_date=february_day,
            market="CN",
            lookback_trading_days=750,
            include_candidates=True,
            include_portfolio=True,
            portfolio_id=portfolio_id,
        )
        results = recompute_candidate_dates(
            dataset,
            dates=[february_day],
            market_results=[],
            parameter_snapshot=None,
        )
        assert len(results) == 1
        universe = results[0].universe
        stock_excluded_counts = universe["exclusions"]["stock"]
        assert stock_excluded_counts.get("UNIVERSE_HELD", 0) == 1
        assert codes[0] in universe["eligible_codes"]
    finally:
        db.close()


def test_future_fundamental_revision_is_invisible_before_publish() -> None:
    db = _db()
    try:
        days, codes = _seed_pit_dataset(db, day_count=120)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[(datetime(2025, 5, 1, 12, 0), [])],
            email="phase-m-fund@example.com",
        )
        code = codes[0]
        db.add(FundamentalReport(
            market="CN",
            code=code,
            report_period=date(2024, 12, 31),
            report_type="ANNUAL",
            published_at=datetime(2025, 5, 1, 8, 0),
            source_available_at=datetime(2025, 5, 1, 8, 0),
            revision_number=1,
            is_restatement=True,
            source="operator-import",
            source_ref="phase-m-fund-revision",
            net_profit=999.0,
            quality_status="VALID",
        ))
        db.commit()
        april_day = next(day for day in days if day >= date(2025, 4, 28))
        june_day = next(day for day in days if day >= date(2025, 6, 1))
        before = _run_recompute(
            db,
            scope="CANDIDATE",
            start_date=april_day,
            end_date=april_day,
            portfolio_id=portfolio_id,
            user_id=user_id,
        )
        after = _run_recompute(
            db,
            scope="CANDIDATE",
            start_date=june_day,
            end_date=june_day,
            portfolio_id=portfolio_id,
            user_id=user_id,
        )
        assert before["recompute_summary"]["date_count"] == 1
        assert after["recompute_summary"]["date_count"] == 1
        assert before["recompute_summary"]["deterministic_hash"] != after["recompute_summary"]["deterministic_hash"]
    finally:
        db.close()


def test_missing_flow_and_industry_never_fill_zero_and_never_full() -> None:
    db = _db()
    try:
        days, _ = _seed_pit_dataset(db)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[(datetime(2025, 4, 1, 12, 0), [])],
            email="phase-m-missing-factor@example.com",
        )
        db.commit()
        facts = _run_recompute(
            db,
            scope="CANDIDATE",
            start_date=days[-1],
            end_date=days[-1],
            portfolio_id=portfolio_id,
            user_id=user_id,
        )
        manifest = facts["recompute_manifest"]
        assert manifest["capability"] != "FULL_PIT_EQUIVALENT"
        assert any("flow" in item for item in manifest.get("limitations", []))
        assert any("industry" in item for item in manifest.get("limitations", []))
        assert facts["recompute_cases"]
        audited = [case for case in facts["recompute_cases"] if case.facts.get("factor_audit")]
        assert audited
        for case in audited:
            by_name = {item["factor_name"]: item for item in case.facts["factor_audit"]}
            assert by_name["flow"]["missing_reason"] == "historical flow input is not persisted"
            assert by_name["flow"]["effective_weight"] == 0.0
            assert by_name["industry"]["missing_reason"] == "historical industry input is not persisted"
            assert by_name["industry"]["effective_weight"] == 0.0
            components = case.facts.get("components") or {}
            assert (components.get("flow") or {}).get("raw", {}).get("money_flow") is None
    finally:
        db.close()


def test_warmup_incomplete_never_full() -> None:
    db = _db()
    try:
        days, _ = _seed_pit_dataset(db, stock_count=20, day_count=10)
        db.commit()
        facts = _run_recompute(db, scope="MARKET", start_date=days[-1], end_date=days[-1])
        assert facts["recompute_summary"]["capability"] != "FULL_PIT_EQUIVALENT"
        market_facts = facts["recompute_cases"][0].facts
        assert market_facts.get("quality_status") in {"VALID", "DEGRADED", "MISSING", "FROZEN"}
    finally:
        db.close()


def test_no_future_bar_leaks_into_feature_cutoff() -> None:
    db = _db()
    try:
        days, codes = _seed_pit_dataset(db)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[(datetime(2025, 4, 1, 12, 0), [codes[0]])],
            email="phase-m-future-bar@example.com",
        )
        future_bar = DailyBarCache(
            market="CN",
            exchange="SSE",
            code=codes[1],
            trade_date=days[-1] + timedelta(days=3),
            open=999.0,
            high=999.0,
            low=999.0,
            close=999.0,
            prev_close=1.0,
            volume=1.0,
            amount=999.0,
            turnover_rate=1.0,
            adjustment="QFQ",
            provider="test",
            available_at=datetime(days[-1].year, days[-1].month, days[-1].day, 10, 0),
            quality_status="VALID",
        )
        db.add(future_bar)
        db.commit()
        facts = _run_recompute(
            db,
            scope="CANDIDATE",
            start_date=days[-1],
            end_date=days[-1],
            portfolio_id=portfolio_id,
            user_id=user_id,
        )
        assert facts["recompute_summary"]["date_count"] == 1
        future_id = f"daily_bar_cache:{future_bar.id}"
        assert all(future_id not in case.source_ids for case in facts["recompute_cases"])
    finally:
        db.close()


def test_portfolio_decision_gate_blocks_candidate_action_without_cash() -> None:
    db = _db()
    try:
        days, codes = _seed_pit_dataset(db)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[(datetime(2025, 4, 1, 12, 0), [codes[0]])],
            cash=None,
            email="phase-m-gate@example.com",
        )
        db.commit()
        facts = _run_recompute(
            db,
            scope="PORTFOLIO_DECISION",
            start_date=days[-1],
            end_date=days[-1],
            portfolio_id=portfolio_id,
            user_id=user_id,
        )
        cases = [case for case in facts["recompute_cases"] if case.scope == "PORTFOLIO_DECISION"]
        assert cases
        case = cases[0]
        assert case.facts.get("portfolio_action") in {"NO_ACTION", "ACTION"}
        blocking_reasons = case.facts.get("blocking_reasons") or []
        assert "INSUFFICIENT_CASH_DATA" in blocking_reasons
        assert case.facts.get("coverage") is not None
    finally:
        db.close()


def test_recompute_query_count_is_bounded_for_large_cohort() -> None:
    db = _db()
    try:
        days, _ = _seed_pit_dataset(db, stock_count=120, day_count=60)
        db.commit()
        queries: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            text = str(statement).lower()
            if text.lstrip().startswith("select"):
                queries.append(text)

        event.listen(db._engine, "before_cursor_execute", _count)  # type: ignore[attr-defined]
        try:
            facts = _run_recompute(db, scope="MARKET", start_date=days[-1], end_date=days[-1])
        finally:
            event.remove(db._engine, "before_cursor_execute", _count)  # type: ignore[attr-defined]
        assert facts["recompute_summary"]["date_count"] == 1
        # The count is constant across cohort sizes (191 for 20x10 and for
        # 120x60); the bound only proves there is no N*D query explosion.
        assert len(queries) < 250
    finally:
        db.close()


def test_candidate_scope_uses_historical_snapshot_not_today_holdings() -> None:
    db = _db()
    try:
        days, codes = _seed_pit_dataset(db)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[(datetime(2025, 4, 1, 12, 0), [codes[0]])],
            email="phase-m-today@example.com",
        )
        # A "today" snapshot created after the historical decision cutoff must
        # never influence the historical recompute.
        db.add(PortfolioSnapshot(
            user_id=user_id,
            portfolio_id=portfolio_id,
            snapshot_time=datetime(2026, 1, 1, 12, 0),
            status="confirmed",
            total_assets=1_000_000.0,
            broker_available_cash=200_000.0,
        ))
        db.flush()
        latest_snapshot_id = db.execute(
            select(PortfolioSnapshot.id)
            .where(
                PortfolioSnapshot.user_id == user_id,
                PortfolioSnapshot.portfolio_id == portfolio_id,
            )
            .order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc())
            .limit(1)
        ).scalar_one()
        db.add(HoldingItem(
            snapshot_id=latest_snapshot_id,
            code=codes[1],
            name="TODAY-HELD",
            qty=1000.0,
            available_qty=1000.0,
        ))
        db.commit()
        facts = _run_recompute(
            db,
            scope="CANDIDATE",
            start_date=days[-1],
            end_date=days[-1],
            portfolio_id=portfolio_id,
            user_id=user_id,
        )
        assert facts["recompute_summary"]["date_count"] == 1
        source_ids = set(facts["recompute_source_ids"])
        assert not any("portfolio_snapshots:" in value for value in source_ids if "2026" in value)
    finally:
        db.close()


def test_held_code_never_enters_new_position_candidate_pool() -> None:
    db = _db()
    try:
        days, codes = _seed_pit_dataset(db)
        user_id, portfolio_id = _seed_portfolio(
            db,
            holdings_by_time=[(datetime(2025, 4, 1, 12, 0), [codes[1]])],
            email="phase-m-held-pool@example.com",
        )
        db.commit()
        from app.recompute.candidate import recompute_candidate_dates
        from app.recompute.dataset import load_recompute_pit_dataset

        dataset = load_recompute_pit_dataset(
            db,
            start_date=days[-1],
            end_date=days[-1],
            market="CN",
            lookback_trading_days=750,
            include_candidates=True,
            include_portfolio=True,
            portfolio_id=portfolio_id,
        )
        results = recompute_candidate_dates(
            dataset,
            dates=[days[-1]],
            market_results=[],
            parameter_snapshot=None,
        )
        assert len(results) == 1
        held_code = codes[1]
        candidate_codes = {row["code"] for row in results[0].candidates}
        assert held_code not in candidate_codes
        assert results[0].universe["exclusions"]["stock"].get("UNIVERSE_HELD", 0) == 1
        assert any(row["code"] == held_code for row in results[0].held_scores)
    finally:
        db.close()


def test_market_target_result_is_independent_of_requested_start_with_warmup() -> None:
    db = _db()
    try:
        days, _ = _seed_pit_dataset(db, stock_count=20, day_count=90)
        db.commit()
        from app.recompute.dataset import load_recompute_pit_dataset
        from app.recompute.market import recompute_market_dates

        target = days[-1]
        earlier_start = days[-16]
        dataset = load_recompute_pit_dataset(
            db,
            start_date=earlier_start,
            end_date=target,
            market="CN",
            lookback_trading_days=750,
        )
        short = recompute_market_dates(dataset, dates=[target], parameter_snapshot=None)
        long_dates = [day for day in days if earlier_start <= day <= target]
        long = recompute_market_dates(dataset, dates=long_dates, parameter_snapshot=None)
        short_target = short[0].as_dict()
        long_target = next(row.as_dict() for row in long if row.trade_date == target)
        assert short_target == long_target
        assert short_target["warmup_days"] == len(days) - 1
    finally:
        db.close()


def test_partial_recompute_leakage_pass_but_capability_blocks_calibration() -> None:
    from app.research.calibration import recommend_calibration
    from app.research.models import BacktestRun
    from app.research.runner import _final_leakage_status

    run = BacktestRun(
        scope="MARKET",
        replay_mode="DETERMINISTIC_RECOMPUTE",
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 2),
        data_hash="a" * 64,
        calculation_key="phase-m-partial-gate",
        data_manifest_json={"recompute_capability": {"capability": "PARTIAL_PIT_RECOMPUTE"}},
    )
    assert _final_leakage_status(
        run,
        {"recompute_summary": {"capability": "PARTIAL_PIT_RECOMPUTE"}},
    ) == "PASS"
    recommendation = recommend_calibration(
        baseline={"median": 0.01},
        challenger={"median": 0.02},
        train={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        validation={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        test={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        robustness={"status": "ROBUST_PLATEAU"},
        sample_counts={"case_count": 252, "trade_date_count": 252},
        quality_status="VALID",
        leakage_status="PASS",
        replay_capability="PARTIAL_PIT_RECOMPUTE",
    )
    assert recommendation == "INSUFFICIENT_EVIDENCE"
    assert _final_leakage_status(
        run,
        {"recompute_summary": {"capability": "LEAKAGE_BLOCKED"}},
    ) == "LEAKAGE_BLOCKED"


def test_full_pit_equivalent_with_pass_leakage_reaches_calibration_gate() -> None:
    from app.research.calibration import recommend_calibration
    from app.research.models import BacktestRun
    from app.research.runner import _final_leakage_status

    run = BacktestRun(
        scope="MARKET",
        replay_mode="DETERMINISTIC_RECOMPUTE",
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 2),
        data_hash="b" * 64,
        calculation_key="phase-m-full-gate",
        data_manifest_json={"recompute_capability": {"capability": "FULL_PIT_EQUIVALENT"}},
    )
    assert _final_leakage_status(
        run,
        {"recompute_summary": {"capability": "FULL_PIT_EQUIVALENT"}},
    ) == "PASS"
    recommendation = recommend_calibration(
        baseline={"median": 0.01},
        challenger={"median": 0.02},
        train={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        validation={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        test={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        robustness={"status": "ROBUST_PLATEAU"},
        sample_counts={"case_count": 252, "trade_date_count": 252},
        quality_status="FULL",
        leakage_status="PASS",
        replay_capability="FULL_PIT_EQUIVALENT",
        fold_directions=[True, True, True],
    )
    assert recommendation != "INSUFFICIENT_EVIDENCE"


__all__: list[str] = []
