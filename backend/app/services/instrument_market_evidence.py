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
from .unified_evidence import build_unified_evidence, collect_supplemental_evidence, merge_unified_evidence
from .market_snapshot_service import get_market_data_cache, put_market_data_cache


def _cached_context(service, name, loader, ttl=300):
    key = f"holdings-context:{service.cache_scope}:{name}"
    cached = get_market_data_cache(key)
    if cached is not None:
        return cached["items"]
    items = loader()
    put_market_data_cache(key, {"items": items}, ttl=ttl)
    return items


def _legacy_quote(quote: InstrumentQuoteResponse) -> dict[str, Any]:
    quality_status = {
        "available": "VALID", "degraded": "DEGRADED", "stale": "STALE",
        "unavailable": "MISSING", "unsupported": "MISSING", "empty": "MISSING",
    }[quote.status]
    return {
        "code": quote.instrument.symbol, "canonical_code": quote.instrument.code,
        "instrument_id": quote.instrument.instrument_id, "exchange": quote.instrument.exchange,
        "name": quote.instrument.name, "security_type": quote.instrument.instrument_type,
        "lot_size": quote.instrument.lot_size, "board": quote.instrument.board,
        "is_st": quote.instrument.is_st, "is_suspended": quote.instrument.is_suspended,
        "instrument_status": quote.instrument.status,
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


def collect_market_snapshot(
    codes: list[str], *, candidate_codes: list[str] | None = None,
    service: InstrumentMarketService | None = None, _include_context: bool = True,
) -> dict[str, Any]:
    """Collect once before evidence is frozen. Agent retries never call this."""
    if service is None:
        with SessionLocal() as db:
            return collect_market_snapshot(codes, candidate_codes=candidate_codes, service=InstrumentMarketService(db), _include_context=_include_context)
    from . import market_data

    codes = list(dict.fromkeys([*codes, *(candidate_codes or [])]))
    context_codes = list(codes)
    index_row = service.resolver.resolve_many(["000001.SH"])[0][1] if _include_context else None
    if index_row is not None and codes:
        context_codes.append("000001.SH")
    resolved = service.resolver.resolve_many(context_codes) if context_codes else []
    known_codes = [code for code, row in resolved if row is not None]
    evidence = None
    for offset in range(0, len(known_codes), 100):
        batch = service.evidence_snapshot(known_codes[offset:offset + 100])
        if evidence is None:
            evidence = batch
        else:
            evidence.items.extend(batch.items)
            evidence.as_of = batch.as_of
    quotes = {}
    index_quote = {}
    technicals = {}
    flows = {}
    errors = [f"identity:{code}:unavailable" for code, row in resolved if row is None]
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
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
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
            "return_5d_pct": (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] > 0 else None,
            "return_20d_pct": (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 and closes[-21] > 0 else None,
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
        for code in quotes:
            try:
                announcements[code] = _cached_context(service, f"announcements:{code}", lambda: market_data.fetch_announcements(code, limit=20), 900)
            except Exception:
                announcements[code] = []
                errors.append(f"announcements:{code}:unavailable")
        extras = {}
        for name, loader in (
            ("news", lambda: market_data.fetch_market_news(limit=80)),
            ("sectors", lambda: market_data.fetch_sector_heat(limit=500)),
            ("etfs", market_data.fetch_etf_leaders),
        ):
            if not _include_context:
                extras[name] = []
                continue
            try:
                extras[name] = _cached_context(service, name, loader)
            except Exception:
                extras[name] = []
                errors.append(f"{name}:unavailable")
        news, sectors, etfs = extras["news"], extras["sectors"], extras["etfs"]
    news = news + [
        {**item, "code": code, "kind": "announcement"}
        for code, items in announcements.items() for item in items
    ]
    # Preserve the legacy optional-data gate, not a new Portfolio/Agent policy.
    # The complete per-module worst grade remains inside instrument_market.
    grade = _worst_grade(*grades) if grades else "F"
    if errors:
        grade = _worst_grade(grade, "B")
    supplemental = collect_supplemental_evidence(service, known_codes, include_market=_include_context)
    result = {
        "captured_at": service._now().isoformat(),
        "quotes": quotes, "technicals": technicals, "fund_flows": flows,
        "instrument_market": evidence.model_dump(mode="json") if evidence else None,
        "announcements": announcements, "news": news,
        "indices": {"sh000001": index_quote}, "sector_heat": sectors, "candidate_pool": {"etf_leaders": etfs},
        "market_mood": market_data._market_mood(index_quote, sectors),
        "quality_grade": grade, "errors": errors,
        "source_chain": sorted({quote["source"] for quote in quotes.values() if quote.get("source")}),
    }
    result["unified_evidence"] = build_unified_evidence(result, requested_codes=codes, supplemental=supplemental, include_market=_include_context)
    result["evidence_version"] = result["unified_evidence"]["evidence_version"]
    # Only time-admissible events enter the legacy agent context, too.
    result["news"] = [row["data"] for row in result["unified_evidence"]["records"].values() if row["kind"] in {"news", "international_event", "announcement"}]
    result["announcements"] = {code: [row for row in result["news"] if row.get("kind") == "announcement" and row.get("code") == code] for code in announcements}
    return result


def enrich_candidate_evidence(
    snapshot: dict[str, Any], candidate_codes: list[str], *, service: InstrumentMarketService | None = None,
) -> dict[str, Any]:
    """Enrich the scan's new candidates before freezing, without recollecting the market."""
    existing = {normalize_security_code(code) for code in snapshot.get("quotes", {})}
    missing = list(dict.fromkeys(code for code in candidate_codes if normalize_security_code(code) not in existing))
    if not missing:
        return snapshot
    if service is None:
        with SessionLocal() as db:
            return enrich_candidate_evidence(snapshot, missing, service=InstrumentMarketService(db))
    addition = collect_market_snapshot(missing, service=service, _include_context=False)
    merged = deepcopy(snapshot)
    for key in ("quotes", "technicals", "fund_flows", "announcements"):
        merged[key] = {**merged.get(key, {}), **addition.get(key, {})}
    merged["news"] = [*merged.get("news", []), *addition.get("news", [])]
    if addition.get("instrument_market"):
        if merged.get("instrument_market"):
            merged["instrument_market"]["items"].extend(addition["instrument_market"]["items"])
            merged["instrument_market"]["as_of"] = addition["instrument_market"]["as_of"]
        else:
            merged["instrument_market"] = addition["instrument_market"]
    merged["captured_at"] = addition["captured_at"]
    merged["source_chain"] = sorted(set([*merged.get("source_chain", []), *addition.get("source_chain", [])]))
    merged["candidate_evidence_errors"] = addition.get("errors", [])
    merged["unified_evidence"] = merge_unified_evidence(merged.get("unified_evidence") or {}, addition["unified_evidence"], as_of=merged["captured_at"])
    merged["evidence_version"] = merged["unified_evidence"]["evidence_version"]
    return merged


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
        requested_codes = {normalize_security_code(code) for code in codes}
        required_codes = {
            normalize_security_code(code)
            for code in snapshot.get("final_quote_required_codes", codes)
        }
        usable_codes = {
            normalize_security_code(item.code) for item in batch.items
            if item.instrument is not None
            and normalize_security_code(item.instrument.code) == normalize_security_code(item.code)
            and item.status in {"available", "degraded"}
            and item.quality in {"A", "B"}
            and item.last is not None and item.last > 0
        }
        usable = bool(required_codes) and required_codes <= requested_codes and required_codes <= usable_codes
        refreshed["final_quote_unavailable_codes"] = sorted(requested_codes - usable_codes)
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
