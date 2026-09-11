"""Compatibility projections for CORE-3; market facts are owned by the facade."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..clock import utc_now
from ..config import settings
from ..database import SessionLocal
from ..market.codes import normalize_security_code
from ..market.instrument_schemas import InstrumentQuoteResponse
from ..market.instruments import InstrumentMarketService
from ..market.models import _coerce_float
from ..market.quality import _worst_grade


def _legacy_quote(quote: InstrumentQuoteResponse) -> dict[str, Any]:
    quality_status = {
        "available": "VALID", "degraded": "DEGRADED", "stale": "STALE",
        "unavailable": "MISSING", "unsupported": "MISSING", "empty": "MISSING",
    }[quote.status]
    return {
        "code": quote.instrument.symbol, "canonical_code": quote.instrument.code,
        "instrument_id": quote.instrument.instrument_id, "exchange": quote.instrument.exchange,
        "name": quote.instrument.name, "security_type": quote.instrument.instrument_type,
        "price": quote.last, "pct_change": quote.change_pct,
        "prev_close": quote.prev_close, "open": quote.open, "high": quote.high, "low": quote.low,
        "volume": quote.volume, "amount": quote.turnover, "turnover": quote.turnover,
        "turnover_rate": quote.turnover_rate, "source": quote.source, "provider": quote.provider,
        "quality_status": quality_status, "quality_flags": quote.quality_flags,
        "source_timestamp": quote.observed_at.isoformat() if quote.observed_at else None,
        "observed_at": quote.observed_at.isoformat() if quote.observed_at else None,
        "fetched_at": quote.fetched_at.isoformat() if quote.fetched_at else None,
        "trade_date": quote.trading_date.isoformat() if quote.trading_date else None,
        "data_basis": quote.data_basis, "fallback_level": int(quote.fallback),
        "stale": quote.status in {"stale", "unavailable"},
        "error": quote.error_code,
    }


def collect_market_snapshot(codes: list[str], *, service: InstrumentMarketService | None = None) -> dict[str, Any]:
    """Collect once before evidence is frozen. Agent retries never call this."""
    if service is None:
        with SessionLocal() as db:
            return collect_market_snapshot(codes, service=InstrumentMarketService(db))
    from . import market_data

    context_codes = list(codes)
    index_row = service.resolver.resolve_many(["000001.SH"])[0][1]
    if index_row is not None and codes and len(codes) < 100:
        context_codes.append("000001.SH")
    evidence = service.evidence_snapshot(context_codes) if context_codes else None
    quotes = {}
    index_quote = {}
    technicals = {}
    flows = {}
    errors = []
    grades = []
    for item in evidence.items if evidence else []:
        snapshot = item.snapshot
        code = snapshot.instrument.symbol
        if snapshot.instrument.code == "000001.SH":
            index_quote = _legacy_quote(snapshot.quote)
            continue
        quotes[code] = _legacy_quote(snapshot.quote)
        grades.append(snapshot.quote.quality)
        if snapshot.quote.last is None or snapshot.quote.status in {"stale", "unavailable"}:
            errors.append(f"quote:{snapshot.instrument.code}:{snapshot.quote.status}")
        closes = [bar.close for bar in item.bars.bars]
        ma5 = sum(closes[-5:]) / len(closes[-5:]) if closes else None
        ma20 = sum(closes[-20:]) / len(closes[-20:]) if closes else None
        volumes = [bar.volume for bar in item.bars.bars[-6:]]
        volume_ratio = None
        if len(volumes) == 6 and all(volume is not None for volume in volumes):
            average = sum(volumes[:-1]) / 5
            volume_ratio = _coerce_float(volumes[-1] / average) if average > 0 else None
        trend = None
        if closes and ma5 is not None and ma20 is not None:
            trend = "up" if closes[-1] > ma5 > ma20 else "down" if closes[-1] < ma5 < ma20 else "sideways"
        technicals[code] = {
            "code": code, "ma5": ma5, "ma20": ma20, "trend": trend, "volume_ratio": volume_ratio,
            "latest": item.bars.bars[-1].model_dump(mode="json") if closes else None,
            "rows": [bar.model_dump(mode="json") for bar in item.bars.bars],
            "source": item.bars.source, "adjustment": item.bars.adjustment,
        }
        flow = snapshot.capital_flow
        current = flow.current
        flows[code] = {
            "code": code, "main_net": current.main_net_inflow if current else None,
            "small_net": current.small_net_inflow if current else None,
            "medium_net": current.medium_net_inflow if current else None,
            "large_net": current.large_net_inflow if current else None,
            "super_large_net": current.super_large_net_inflow if current else None,
            "source": flow.source, "provider_derived": True, "methodology": flow.methodology,
        }
        if not closes:
            errors.append(f"bars:{snapshot.instrument.code}:{item.bars.status}")
        if snapshot.capabilities.capital_flow and flow.status in {"unavailable", "empty"}:
            errors.append(f"capital_flow:{snapshot.instrument.code}:{flow.status}")

    announcements = {}
    if settings.ACCEPTANCE_MODE:
        news = [{"title": "Acceptance market news", "source": "acceptance"}]
        sectors = [{"rank": 1, "name": "Acceptance sector", "pct_change": 1.2, "source": "acceptance"}]
        etfs = []
    else:
        for code in list(quotes)[:8]:
            try:
                announcements[code] = market_data.fetch_announcements(code)
            except Exception:
                announcements[code] = []
                errors.append(f"announcements:{code}:unavailable")
        extras = {}
        for name, loader in (
            ("news", market_data.fetch_market_news),
            ("sectors", market_data.fetch_sector_heat),
            ("etfs", market_data.fetch_etf_leaders),
        ):
            try:
                extras[name] = loader()
            except Exception:
                extras[name] = []
                errors.append(f"{name}:unavailable")
        news, sectors, etfs = extras["news"], extras["sectors"], extras["etfs"]
    news = news + [
        {**item, "code": code, "kind": "announcement"}
        for code, items in announcements.items() for item in items[:3]
    ]
    # Preserve the legacy optional-data gate, not a new Portfolio/Agent policy.
    # The complete per-module worst grade remains inside instrument_market.
    grade = _worst_grade(*grades) if grades else "F"
    if errors:
        grade = _worst_grade(grade, "B")
    return {
        "captured_at": (evidence.as_of if evidence else utc_now()).isoformat(),
        "quotes": quotes, "technicals": technicals, "fund_flows": flows,
        "instrument_market": evidence.model_dump(mode="json") if evidence else None,
        "announcements": announcements, "news": news,
        "indices": {"sh000001": index_quote}, "sector_heat": sectors, "candidate_pool": {"etf_leaders": etfs},
        "market_mood": market_data._market_mood(index_quote, sectors),
        "quality_grade": grade, "errors": errors,
        "source_chain": sorted({quote["source"] for quote in quotes.values() if quote.get("source")}),
    }


def refresh_snapshot_quotes(
    snapshot: dict[str, Any], codes: list[str], *, service: InstrumentMarketService | None = None,
) -> dict[str, Any]:
    """Only the final quote node bypasses the facade cache; frozen evidence stays intact."""
    if service is None:
        with SessionLocal() as db:
            return refresh_snapshot_quotes(snapshot, codes, service=InstrumentMarketService(db))
    refreshed = deepcopy(snapshot)
    try:
        batch = service.batch_quotes(codes, force_refresh=True)
        refreshed["instrument_quotes"] = batch.model_dump(mode="json")
        refreshed["quotes"] = {
            normalize_security_code(item.code): _legacy_quote(
                InstrumentQuoteResponse.model_validate(item.model_dump(exclude={"code"}))
            ) if item.instrument is not None else {
                "code": normalize_security_code(item.code), "price": None,
                "stale": True, "quality_status": "MISSING", "error": item.error_code,
            }
            for item in batch.items
        }
        usable = all(item.status in {"available", "degraded"} and item.quality in {"A", "B"} and item.last is not None for item in batch.items)
        refreshed["final_quote_refresh_at"] = batch.as_of.isoformat()
        refreshed["final_quote_refresh_status"] = "ok" if usable else "failed"
        if not usable:
            refreshed["final_quote_refresh_error"] = "FINAL_QUOTE_UNAVAILABLE"
    except Exception:
        refreshed["final_quote_refresh_at"] = utc_now().isoformat()
        refreshed["final_quote_refresh_status"] = "failed"
        refreshed["final_quote_refresh_error"] = "FINAL_QUOTE_PROVIDER_FAILED"
    if refreshed["final_quote_refresh_status"] != "ok":
        refreshed.setdefault("errors", []).append("final_quote_refresh:unavailable")
    return refreshed
