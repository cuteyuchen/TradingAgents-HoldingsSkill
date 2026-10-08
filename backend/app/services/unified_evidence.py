"""Research evidence with explicit provenance and point-in-time availability.

The persisted analysis artifact owns these records. This module does not create
another database or a second market-data authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
import json
import math
from typing import Any
from urllib.parse import urlsplit

from ..clock import utc_now
from ..config import settings
from ..market.codes import normalize_security_code
from ..market.models import CHINA_TZ
from ..market.providers.fuyao import (
    FUYAO_FUND_HISTORICAL_ENDPOINT, FUYAO_FUND_SNAPSHOT_ENDPOINT,
    FUYAO_HISTORICAL_ENDPOINT, FUYAO_INDEX_HISTORICAL_ENDPOINT, FUYAO_INDEX_SNAPSHOT_ENDPOINT,
    FUYAO_QUOTE_ENDPOINT, FuyaoDataProvider, _rows,
)
from .market_snapshot_service import get_market_data_cache, put_market_data_cache


SCHEMA_VERSION = "holdings-evidence-v1"
_INTERNATIONAL = ("美国", "美联储", "美股", "欧盟", "欧洲", "欧央行", "日本", "日央行", "英国", "俄罗斯", "乌克兰", "伊朗", "以色列", "中东", "OPEC", "美债", "美元")
# A transparent relevance shortlist, never a verified causal effect or trading rule.
_EVENT_CHANNELS = (
    (("原油", "OPEC", "中东", "能源"), ("石油", "能源", "航空", "航运", "化工"), "能源价格与供给", "需核对能源报价、成本占比及供给变化"),
    (("美联储", "利率", "美债", "美元", "汇率"), ("银行", "金融", "出口", "科技", "有色"), "利率、汇率与估值", "需核对利率汇率、融资成本及收入币种"),
    (("关税", "贸易", "制裁", "出口", "禁令"), ("半导体", "汽车", "机械", "电子", "出口"), "贸易成本与订单", "需核对政策原文、适用产品及出口收入"),
)
_TTL = {"quote": 90, "bars": 7 * 86400, "capital_flow": 86400, "factors": 86400,
        "valuation": 86400, "financials": 180 * 86400, "metadata": 30 * 86400,
        "announcement": 7 * 86400, "news": 3 * 86400, "international_event": 3 * 86400,
        "market_breadth": 86400, "market_turnover": 86400, "market_industry": 86400,
        "market_indices": 86400}


def evidence_time(value: Any) -> datetime | None:
    """Naive provider times are Shanghai times; date-only releases use day end."""
    if value in (None, "", "-"):
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)) or str(value).isdigit():
            numeric = float(value)
            parsed = datetime.fromtimestamp(numeric / 1000 if abs(numeric) > 1e11 else numeric, UTC)
        else:
            raw = str(value).strip()
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if len(raw) == 10:
                parsed = datetime.combine(parsed.date(), time.max)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=CHINA_TZ)).astimezone(UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _link(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"https", "http"} and parsed.netloc and not parsed.username and not parsed.password:
            # Query credentials must never be copied into research artifacts.
            if not any(word in parsed.query.lower() for word in ("token", "key", "secret", "auth")):
                return value
    except ValueError:
        pass
    return None


def _first_time(row: dict[str, Any], fields: tuple[str, ...]) -> datetime | None:
    return next((parsed for field in fields if (parsed := evidence_time(row.get(field))) is not None), None)


def _has_metrics(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_metrics(item) for key, item in value.items()
                   if key not in {"thscode", "ticker", "code", "report", "period", "statement"}
                   and not any(word in key.lower() for word in ("date", "time", "period", "published", "available")))
    if isinstance(value, list):
        return any(_has_metrics(item) for item in value)
    try:
        return value is not None and not isinstance(value, bool) and math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        return False


def _market_source_url(provider: str | None, identity: dict[str, Any], kind: str) -> str | None:
    prefix = {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}.get(identity.get("exchange"))
    if prefix and provider == "tencent" and kind == "quote":
        return f"https://qt.gtimg.cn/q={prefix}{identity['symbol']}"
    if prefix and provider and provider.startswith("eastmoney"):
        return f"https://quote.eastmoney.com/{prefix}{identity['symbol']}.html"
    if provider in {"fuyao", "fuyao_historical"} and kind in {"quote", "bars"}:
        endpoints = {
            "STOCK": (FUYAO_QUOTE_ENDPOINT, FUYAO_HISTORICAL_ENDPOINT),
            "ETF": (FUYAO_FUND_SNAPSHOT_ENDPOINT, FUYAO_FUND_HISTORICAL_ENDPOINT),
            "INDEX": (FUYAO_INDEX_SNAPSHOT_ENDPOINT, FUYAO_INDEX_HISTORICAL_ENDPOINT),
        }
        endpoint = endpoints.get(identity.get("instrument_type"))
        if endpoint:
            return _link(str(settings.FUYAO_BASE_URL).rstrip("/") + endpoint[0 if kind == "quote" else 1])
    return None


def make_evidence_record(
    kind: str, data: Any, *, source: str | None, fetched_at: Any, code: str | None = None,
    observed_at: Any = None, published_at: Any = None, available_at: Any = None,
    source_url: str | None = None, evidence_type: str = "fact", status: str = "available",
    sectors: list[str] | None = None, gaps: list[str] | None = None, ttl_seconds: int | None = None,
    instrument_type: str | None = None,
) -> dict[str, Any]:
    fetched, observed, published = map(evidence_time, (fetched_at, observed_at, published_at))
    known = [moment for moment in (fetched, published, evidence_time(available_at)) if moment]
    available = max(known) if fetched and known else None
    missing = list(gaps or [])
    link = _link(source_url)
    if not source:
        missing.append("SOURCE_MISSING")
    if not link:
        missing.append("SOURCE_URL_MISSING")
    if fetched is None:
        missing.append("FETCHED_TIME_MISSING")
    if published is None and kind in {"financials", "announcement", "news", "international_event"}:
        missing.append("PUBLICATION_TIME_MISSING")
    if observed is None and kind in {"quote", "bars", "capital_flow", "valuation", "market_industry"}:
        missing.append("OBSERVED_TIME_MISSING")
    anchor = published or observed or fetched
    deadline = anchor + timedelta(seconds=ttl_seconds if ttl_seconds is not None else _TTL.get(kind, 86400)) if anchor else None
    payload = deepcopy(data)
    if isinstance(payload, dict):
        for field in ("source_url", "url"):
            if field in payload:
                payload[field] = _link(payload[field])
    item = {
        "kind": kind, "code": normalize_security_code(code) if code else None,
        "canonical_code": code if code and "." in code else None,
        "instrument_type": instrument_type,
        "source": source, "source_url": link, "evidence_type": evidence_type,
        "published_at": _iso(published), "observed_at": _iso(observed), "fetched_at": _iso(fetched),
        "available_at": _iso(available), "valid_until": _iso(deadline), "status": status,
        "sectors": sorted(set(sectors or [])), "gaps": sorted(set(missing)), "data": payload,
    }
    item["id"] = sha256(json.dumps(item, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]
    return item


def project_unified_evidence(pack: dict[str, Any], as_of: Any) -> dict[str, Any]:
    """Project only persisted records; never refetch today's facts for a replay."""
    cutoff = evidence_time(as_of)
    if cutoff is None:
        raise ValueError("EVIDENCE_AS_OF_REQUIRED")
    result = deepcopy(pack)
    retained = {}
    rejected = list(result.get("excluded") or [])
    for key, record in (result.get("records") or {}).items():
        available = evidence_time(record.get("available_at"))
        clocks = [evidence_time(record.get(field)) for field in ("published_at", "observed_at", "fetched_at", "available_at")]
        reason = "AVAILABILITY_UNPROVEN" if available is None else "NOT_YET_AVAILABLE" if any(moment and moment > cutoff for moment in clocks) else None
        if reason:
            rejected.append({"record_id": key, "kind": record.get("kind"), "code": record.get("code"), "reason": reason})
            continue
        deadline = evidence_time(record.get("valid_until"))
        if deadline is not None and deadline < cutoff and record.get("status") in {"available", "degraded"}:
            record["status"] = "stale"
            record["gaps"] = sorted(set([*record.get("gaps", []), "EVIDENCE_EXPIRED"]))
        retained[key] = record
    result["records"] = retained
    result["excluded"] = rejected
    result["as_of"] = cutoff.isoformat()
    result["event_links"] = [link for link in result.get("event_links", [])
                             if retained.get(link.get("event_id"), {}).get("status") in {"available", "degraded"}
                             and retained.get(link.get("metadata_id"), {}).get("status") in {"available", "degraded"}]
    result["gaps"] = [
        {"record_id": key, "code": row.get("code"), "kind": row["kind"], "reason": reason}
        for key, row in retained.items() for reason in row.get("gaps", [])
    ] + rejected + list(result.get("collection_gaps") or [])
    requested = result.get("requested_codes", [])
    result["coverage"] = {
        code: {kind: sum(row.get("code") == code and row.get("instrument_type") != "INDEX" and row["kind"] == kind and row["status"] in {"available", "degraded"} for row in retained.values())
               for kind in ("quote", "bars", "financials", "valuation", "capital_flow", "factors", "announcement")}
        for code in requested
    }
    result.pop("evidence_version", None)
    result["evidence_version"] = SCHEMA_VERSION + ":" + sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return result


def collect_supplemental_evidence(service, codes: list[str], *, provider=None, include_market: bool = True) -> dict[str, Any]:
    """Reuse the market foundation and Fuyao transport/cache; no score changes."""
    result: dict[str, Any] = {"metadata": {}, "financials": {}, "valuation": {}, "overview": {}, "gaps": []}
    stocks = []
    for code in codes:
        try:
            metadata = service.metadata(code).model_dump(mode="json")
            result["metadata"][normalize_security_code(code)] = metadata
            if metadata["identity"]["instrument_type"] == "STOCK":
                stocks.append(metadata["identity"]["code"])
        except Exception:
            result["gaps"].append({"code": normalize_security_code(code), "kind": "metadata", "reason": "METADATA_UNAVAILABLE"})
    if settings.ACCEPTANCE_MODE:
        result["gaps"].append({"kind": "supplemental", "reason": "ACCEPTANCE_SUPPLEMENTAL_NOT_PROVIDED"})
        return result
    if include_market:
        try:
            from ..market.foundation import MarketFoundationService
            result["overview"] = MarketFoundationService(service.db, now=service.now).overview()
            result["overview_fetched_at"] = service._now().isoformat()
        except Exception:
            result["gaps"].append({"kind": "market_overview", "reason": "MARKET_OVERVIEW_UNAVAILABLE"})
    provider = provider or FuyaoDataProvider()
    if not provider.client.configured:
        for code in stocks:
            for kind in ("financials", "valuation"):
                result["gaps"].append({"code": normalize_security_code(code), "kind": kind, "reason": "PROVIDER_NOT_CONFIGURED"})
        return result

    def load(key, loader, ttl):
        cache_key = f"holdings-evidence:{service.cache_scope}:{key}"
        cached = get_market_data_cache(cache_key)
        if cached is not None:
            return cached
        response = loader()
        data = response.data
        output = {"rows": [data] if isinstance(data, dict) and data.get("abilities") else _rows(data), "fetched_at": utc_now().isoformat(),
                  "observed_at": (data.get("timestamp") or data.get("snapshot_time_ms")) if isinstance(data, dict) else None,
                  "source": "fuyao", "endpoint": response.endpoint,
                  "source_url": _link(str(provider.client.base_url).rstrip("/") + response.endpoint)}
        put_market_data_cache(cache_key, output, ttl=ttl)
        return output

    for offset in range(0, len(stocks), 100):
        batch = stocks[offset:offset + 100]
        try:
            value = load("valuation:" + ",".join(batch), lambda: provider.get_valuation(batch), 60)
            for code in batch:
                symbol = normalize_security_code(code)
                result["valuation"][symbol] = {**value, "rows": [row for row in value["rows"] if normalize_security_code(row.get("thscode") or row.get("ticker")) == symbol]}
        except Exception:
            for code in batch:
                result["gaps"].append({"code": normalize_security_code(code), "kind": "valuation", "reason": "VALUATION_UNAVAILABLE"})
    for code in stocks:
        symbol = normalize_security_code(code)
        result["financials"][symbol] = {}
        for statement, endpoint in (("income", "income-statements"), ("balance", "balance-sheets"), ("cash_flow", "cash-flow-statements")):
            try:
                result["financials"][symbol][statement] = load(
                    f"financials:{code}:{statement}",
                    lambda: provider.get(f"/api/a-share/financials/{endpoint}", params={"thscode": code, "period": "annual", "limit": 4}, capability="financials"),
                    3600,
                )
            except Exception:
                result["gaps"].append({"code": symbol, "kind": "financials", "reason": f"{statement.upper()}_UNAVAILABLE"})
        report = f"{service._now().astimezone(CHINA_TZ).year - 1}-4"
        try:
            result["financials"][symbol]["indicators"] = load(
                f"indicators:{code}:{report}", lambda: provider.get_indicators(code, report), 3600,
            )
        except Exception:
            result["gaps"].append({"code": symbol, "kind": "financials", "reason": "INDICATORS_UNAVAILABLE"})
    return result


def build_unified_evidence(
    snapshot: dict[str, Any], *, requested_codes: list[str], supplemental: dict[str, Any] | None = None,
    include_market: bool = True,
) -> dict[str, Any]:
    supplemental = supplemental or {}
    captured = snapshot["captured_at"]
    records: dict[str, dict[str, Any]] = {}
    collection_gaps = list(supplemental.get("gaps") or [])

    def add(kind, data, **kwargs):
        row = make_evidence_record(kind, data, **kwargs)
        records[row["id"]] = row
        return row

    metadata_records = {}
    for code, item in supplemental.get("metadata", {}).items():
        sectors = [item["metadata"]["industry"]] if item.get("metadata", {}).get("industry") else []
        if item.get("identity", {}).get("instrument_type") == "INDEX":
            continue
        metadata_records[code] = add("metadata", item, code=code, sectors=sectors, source="security_master", fetched_at=captured, observed_at=item.get("observed_at"))
    for item in (snapshot.get("instrument_market") or {}).get("items", []):
        current, bars = item["snapshot"], item["bars"]
        identity = current["instrument"]
        code = identity["symbol"]
        sectors = metadata_records.get(code, {}).get("sectors", [])
        for kind, raw in (("quote", current["quote"]), ("capital_flow", current["capital_flow"]), ("bars", bars)):
            add(kind, raw, code=identity["code"], instrument_type=identity["instrument_type"], sectors=sectors, source=raw.get("source"), fetched_at=raw.get("fetched_at") or captured,
                observed_at=raw.get("observed_at"), source_url=raw.get("source_url") or _market_source_url(raw.get("source"), identity, kind),
                status=raw.get("status", "unavailable"), evidence_type="provider_derived" if kind == "capital_flow" else "fact",
                gaps=raw.get("quality_flags", []), ttl_seconds=86400 if kind == "quote" and raw.get("data_basis") != "live" else None)
        technical = snapshot.get("technicals", {}).get(code)
        if technical:
            add("factors", {key: value for key, value in technical.items() if key not in {"rows", "latest"}}, code=identity["code"], instrument_type=identity["instrument_type"], sectors=sectors,
                source=bars.get("source"), fetched_at=bars.get("fetched_at") or captured, observed_at=bars.get("observed_at"),
                evidence_type="inference", status="available" if technical.get("ma20") is not None else "unavailable",
                gaps=[] if len(bars.get("bars", [])) >= 20 else ["INSUFFICIENT_FACTOR_HISTORY"])
    for kind in ("valuation", "financials"):
        for code, value in supplemental.get(kind, {}).items():
            tables = {"valuation": value} if kind == "valuation" else value
            for table, envelope in tables.items():
                if not envelope.get("rows"):
                    collection_gaps.append({"code": code, "kind": kind, "reason": f"{table.upper()}_EMPTY"})
                for row in envelope.get("rows", []):
                    published = _first_time(row, ("published_at", "announcement_date", "announcement_date_ms", "announce_date", "ann_date", "publish_time", "publish_time_ms", "disclosure_date"))
                    observed = _first_time(row, ("observed_at", "timestamp", "snapshot_time_ms")) or evidence_time(envelope.get("observed_at"))
                    add(kind, {"statement": table, **row}, code=code, source=envelope.get("source"), source_url=envelope.get("source_url"),
                        fetched_at=envelope.get("fetched_at"), observed_at=observed, published_at=published, available_at=row.get("available_at"),
                        sectors=metadata_records.get(code, {}).get("sectors", []),
                        status="available" if _has_metrics(row) else "unavailable", gaps=[] if _has_metrics(row) else ["FINANCIAL_METRICS_MISSING"])
    overview = supplemental.get("overview") or {}
    for kind, keys in (("market_breadth", ("breadth", "all_a_median")), ("market_turnover", ("total_turnover", "turnover_concentration")), ("market_indices", ("major_indices",))):
        payload = {key: overview[key] for key in keys if key in overview}
        if payload:
            components = [component for value in payload.values() for component in (value if isinstance(value, list) else [value]) if isinstance(component, dict)]
            statuses = [component.get("status", "unavailable") for component in components]
            status = "available" if statuses and all(value == "available" for value in statuses) else "degraded" if "available" in statuses else "unavailable"
            gaps = [flag for component in components for flag in [*component.get("quality_flags", []), *component.get("missing_fields", [])]]
            if status == "unavailable":
                gaps.append("MARKET_CONTEXT_UNAVAILABLE")
            add(kind, payload, source="market_foundation", fetched_at=supplemental.get("overview_fetched_at") or captured,
                observed_at=overview.get("quote_as_of"), evidence_type="inference",
                status=status, gaps=gaps)
        elif include_market:
            collection_gaps.append({"kind": kind, "reason": "MARKET_CONTEXT_UNAVAILABLE"})
    for sector in snapshot.get("sector_heat", []):
        add("market_industry", sector, source=sector.get("source"), source_url=sector.get("source_url"),
            fetched_at=sector.get("fetched_at") or captured, observed_at=sector.get("observed_at"),
            sectors=[sector["name"]] if sector.get("name") else [], evidence_type="provider_derived",
            gaps=["INDUSTRY_COVERAGE_PARTIAL"] if sector.get("coverage_complete") is False else [])
    if include_market and not snapshot.get("sector_heat"):
        collection_gaps.append({"kind": "market_industry", "reason": "INDUSTRY_DATA_UNAVAILABLE"})
    for code, announcements in snapshot.get("announcements", {}).items():
        if not announcements:
            collection_gaps.append({"kind": "announcement", "code": code, "reason": "NO_ANNOUNCEMENTS_IN_SOURCE_WINDOW"})
    for raw in snapshot.get("news", []):
        title = str(raw.get("title") or "")
        kind = "announcement" if raw.get("kind") == "announcement" else "international_event" if any(token.casefold() in title.casefold() for token in _INTERNATIONAL) else "news"
        add(kind, raw, code=raw.get("code"), source=raw.get("source"), source_url=raw.get("source_url") or raw.get("url"),
            fetched_at=raw.get("fetched_at") or captured, published_at=raw.get("published_at") or raw.get("time") or raw.get("notice_date"),
            available_at=raw.get("available_at"))
    if include_market and not any(row["kind"] == "international_event" for row in records.values()):
        collection_gaps.append({"kind": "international_event", "reason": "NO_INTERNATIONAL_EVENT_IN_SOURCE_WINDOW"})
    for error in snapshot.get("errors", []):
        collection_gaps.append({"kind": "collection", "reason": str(error)})
    pack = {"schema_version": SCHEMA_VERSION, "requested_codes": list(dict.fromkeys(normalize_security_code(code) for code in requested_codes)),
            "collection_policy": {"news_window_items": 80, "announcement_window_items_per_instrument": 20, "industry_limit": 500,
                                  "historical_replay": "persisted_records_only", "event_matching": "explicit_mentions_are_not_causal_proof"},
            "records": records, "event_links": _event_links(records), "collection_gaps": collection_gaps}
    return project_unified_evidence(pack, captured)


def _event_links(records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    metadata_records = {row["code"]: row for row in records.values() if row["kind"] == "metadata"}
    links = []
    for row in records.values():
        if row["kind"] not in {"announcement", "news", "international_event"}:
            continue
        text = str(row["data"].get("title") or "")
        for code, metadata in metadata_records.items():
            name = metadata["data"].get("identity", {}).get("name")
            sectors = [sector for sector in metadata["sectors"] if sector in text]
            direct = row.get("code") == code or code in text or bool(name and len(name) >= 2 and name in text)
            if direct or sectors:
                links.append({"event_id": row["id"], "metadata_id": metadata["id"], "code": code,
                              "sectors": (sectors or metadata["sectors"]) if direct else sectors,
                              "evidence_type": "inference", "method": "explicit_security_or_sector_mention", "causal_effect": "unverified",
                              "impact_channel": "直接公司或行业事件", "required_checks": ["核对原始公告、事件时点及业务敞口"],
                              "source_url": row.get("source_url"), "published_at": row.get("published_at")})
            exposure_text = " ".join([str(name or ""), *metadata["sectors"]])
            for triggers, affected, channel, checks in _EVENT_CHANNELS:
                matched = [sector for sector in affected if sector in exposure_text]
                if matched and any(trigger.casefold() in text.casefold() for trigger in triggers):
                    links.append({"event_id": row["id"], "metadata_id": metadata["id"], "code": code,
                                  "sectors": matched, "evidence_type": "inference", "method": "declared_transmission_channel",
                                  "impact_channel": channel, "required_checks": [checks], "causal_effect": "unverified",
                                  "direction": "requires_corroboration", "source_url": row.get("source_url"),
                                  "published_at": row.get("published_at")})
    return links


def attach_portfolio_exposures(snapshot: dict, holdings: list[dict], *, portfolio_context: dict | None = None) -> dict:
    """Bind event hypotheses to this analysis's confirmed derived positions."""
    pack = snapshot.get("unified_evidence")
    if not isinstance(pack, dict):
        return snapshot
    positions = {normalize_security_code(row.get("code")): row for row in holdings}
    weights = {normalize_security_code(row.get("code")): row.get("weight")
               for row in (portfolio_context or {}).get("position_constraints", [])}
    for link in pack.get("event_links", []):
        position = positions.get(normalize_security_code(link.get("code")))
        link["portfolio_exposure"] = {
            "held": bool(position and (position.get("qty") or 0) > 0), "quantity": position.get("qty") if position else None,
            "weight": weights.get(normalize_security_code(link.get("code"))),
            "basis": "CONFIRMED_DERIVED_QUANTITY; FROZEN_CONSTRAINT_WEIGHT",
            "weight_status": "available" if weights.get(normalize_security_code(link.get("code"))) is not None else "unknown",
        }
    pack = project_unified_evidence(pack, snapshot["captured_at"])
    snapshot["unified_evidence"] = pack
    snapshot["evidence_version"] = pack["evidence_version"]
    return snapshot


def merge_unified_evidence(first: dict[str, Any], addition: dict[str, Any], *, as_of: Any) -> dict[str, Any]:
    records = {**first.get("records", {}), **addition.get("records", {})}
    return project_unified_evidence({
        "schema_version": SCHEMA_VERSION,
        "collection_policy": first.get("collection_policy") or addition.get("collection_policy"),
        "requested_codes": list(dict.fromkeys([*first.get("requested_codes", []), *addition.get("requested_codes", [])])),
        "records": records, "event_links": _event_links(records),
        "excluded": [*first.get("excluded", []), *addition.get("excluded", [])],
        "collection_gaps": [*first.get("collection_gaps", []), *addition.get("collection_gaps", [])],
    }, as_of)
