"""Warm existing evidence caches; confirmed account facts remain authoritative."""
from __future__ import annotations

import logging

from sqlalchemy import select

from ..clock import utc_now
from ..config import settings
from ..database import SessionLocal
from ..market.instruments import InstrumentMarketService
from ..portfolio.account import build_account_state
from ..portfolio.risk import latest_confirmed_snapshot
from ..v2_models import Portfolio
from .instrument_market_evidence import collect_market_snapshot
from .trading_calendar import CHINA_TZ, TradingCalendarService

logger = logging.getLogger(__name__)


def prefetch_holdings_evidence() -> dict:
    if not settings.HOLDINGS_EVIDENCE_PREFETCH_ENABLED or settings.ACCEPTANCE_MODE:
        return {"status": "SKIPPED", "reason": "PREFETCH_DISABLED"}
    now = utc_now()
    local = now.astimezone(CHINA_TZ)
    if not (9 * 60 <= local.hour * 60 + local.minute <= 15 * 60 + 30):
        return {"status": "SKIPPED", "reason": "OUTSIDE_PREFETCH_WINDOW"}
    # Own the session in this scheduler worker, never share it with analysis threads.
    with SessionLocal() as db:
        if not TradingCalendarService(db).is_trading_day(now.astimezone(CHINA_TZ).date()):
            return {"status": "SKIPPED", "reason": "NON_TRADING_DAY"}
        codes = set()
        for portfolio in db.scalars(select(Portfolio)).all():
            snapshot = latest_confirmed_snapshot(db, portfolio_id=portfolio.id, as_of=now)
            if snapshot is None:
                continue
            account = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=now)
            codes.update(row["code"] for row in account["positions"] if row.get("code") and (row.get("qty") or 0) > 0)
        if not codes:
            return {"status": "SKIPPED", "reason": "NO_CONFIRMED_HOLDINGS"}
        snapshot = collect_market_snapshot(sorted(codes), service=InstrumentMarketService(db))
        db.commit()
        gaps = (snapshot.get("unified_evidence") or {}).get("collection_gaps", [])
        result = {"status": "DEGRADED" if snapshot.get("errors") or gaps else "COMPLETED",
                "code_count": len(codes), "evidence_version": snapshot.get("evidence_version"),
                "gaps": gaps,
                "as_of": now.isoformat()}
        logger.info("holdings_evidence_prefetch status=%s code_count=%s gap_count=%s", result["status"], len(codes), len(gaps))
        return result
