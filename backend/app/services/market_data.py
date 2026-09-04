"""Read-only A-share market data collection with explicit source metadata."""
from __future__ import annotations

import math
import random
import threading
import time
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from ..clock import china_now, utc_now
from ..config import settings
from ..market.codes import normalize_security_code
from ..market.providers.factory import create_quote_provider
from ..market.providers.tencent import TencentQuoteProvider, parse_tencent_line as _normalized_parse_tencent_line

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
_EM_LOCK = threading.Lock()
_EM_LAST_CALL = 0.0


def normalize_code(value: str) -> str:
    """Legacy facade for the shared security-code normalizer."""
    return normalize_security_code(value)


def tencent_symbol(code: str) -> str:
    code = normalize_code(code)
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def eastmoney_secid(code: str) -> str:
    code = normalize_code(code)
    return f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"


def _float(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        parsed = float(str(value).replace(",", ""))
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _em_get(
    url: str,
    *,
    params: dict[str, Any],
    timeout: float = 12.0,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """Serialize Eastmoney requests to reduce temporary IP blocking."""
    global _EM_LAST_CALL
    with _EM_LOCK:
        elapsed = time.monotonic() - _EM_LAST_CALL
        if elapsed < 0.8:
            time.sleep(0.8 - elapsed)
        time.sleep(random.uniform(0.05, 0.2))
        response = requests.get(
            url,
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://quote.eastmoney.com/",
                **(headers or {}),
            },
            timeout=timeout,
        )
        _EM_LAST_CALL = time.monotonic()
    response.raise_for_status()
    return response


def _parse_tencent_line(line: str) -> dict[str, Any] | None:
    quote = _normalized_parse_tencent_line(line)
    if quote is None:
        return None
    legacy = quote.to_dict()
    legacy_quote_time = (
        quote.source_timestamp.astimezone(CHINA_TZ).strftime("%H:%M:%S")
        if quote.source_timestamp
        else None
    )
    legacy.update(
        {
            "turnover": legacy.get("amount"),
            "source": "Tencent qt.gtimg.cn",
            "stale": legacy.get("quality_status") in {"STALE", "MISSING"},
            "quote_time": legacy_quote_time,
        }
    )
    return legacy


def fetch_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    normalized = list(dict.fromkeys(normalize_code(code) for code in codes if normalize_code(code)))
    if not normalized:
        return {}
    provider = create_quote_provider("acceptance") if settings.ACCEPTANCE_MODE else TencentQuoteProvider(timeout=10)
    normalized_quotes = provider.get_quotes(normalized)
    results: dict[str, dict[str, Any]] = {}
    for code, quote in normalized_quotes.items():
        parsed = quote.to_dict()
        legacy_quote_time = (
            quote.source_timestamp.astimezone(CHINA_TZ).strftime("%H:%M:%S")
            if quote.source_timestamp
            else None
        )
        if parsed.get("quality_status") in {"MISSING", "INVALID"}:
            errors = parsed.get("errors") or []
            parsed["error"] = str(errors[0]) if errors else (
                "quote_missing" if parsed.get("quality_status") == "MISSING" else "quote_invalid"
            )
        parsed.update(
            {
                "turnover": parsed.get("amount"),
                "source": "Acceptance fixture" if settings.ACCEPTANCE_MODE else "Tencent qt.gtimg.cn",
                "stale": parsed.get("quality_status") in {"STALE", "MISSING"},
                "quote_time": legacy_quote_time,
            }
        )
        results[code] = parsed
    missing = set(normalized) - set(results)
    for code in missing:
        results[code] = {
            "code": code,
            "source": "Acceptance fixture" if settings.ACCEPTANCE_MODE else "Tencent qt.gtimg.cn",
            "error": "quote_missing",
            "stale": True,
        }
    return results


def fetch_kline(code: str, limit: int = 30) -> dict[str, Any]:
    params = {
        "secid": eastmoney_secid(code),
        "klt": "101",
        "fqt": "1",
        "lmt": str(limit),
        "end": "20500101",
        "iscca": "1",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    payload = _em_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params=params).json()
    rows = ((payload.get("data") or {}).get("klines") or [])
    closes: list[float] = []
    volumes: list[float] = []
    parsed_rows: list[dict[str, Any]] = []
    for row in rows:
        fields = str(row).split(",")
        if len(fields) < 6:
            continue
        close = _float(fields[2])
        volume = _float(fields[5])
        if close is None:
            continue
        closes.append(close)
        volumes.append(volume or 0)
        parsed_rows.append(
            {
                "date": fields[0],
                "open": _float(fields[1]),
                "close": close,
                "high": _float(fields[3]),
                "low": _float(fields[4]),
                "volume": volume,
            }
        )
    latest = parsed_rows[-1] if parsed_rows else None
    ma5 = sum(closes[-5:]) / min(len(closes), 5) if closes else None
    ma20 = sum(closes[-20:]) / min(len(closes), 20) if closes else None
    volume_ratio = None
    if len(volumes) >= 6:
        average = sum(volumes[-6:-1]) / 5
        volume_ratio = volumes[-1] / average if average else None
    trend = None
    if latest and ma5 is not None and ma20 is not None:
        if latest["close"] > ma5 > ma20:
            trend = "up"
        elif latest["close"] < ma5 < ma20:
            trend = "down"
        else:
            trend = "sideways"
    return {
        "code": normalize_code(code),
        "latest": latest,
        "ma5": ma5,
        "ma20": ma20,
        "volume_ratio": volume_ratio,
        "trend": trend,
        "rows": parsed_rows,
        "source": "Eastmoney push2his",
    }


def fetch_fund_flow(code: str) -> dict[str, Any]:
    """Fetch the latest main/small/medium/large/super-large net flow row."""
    params = {
        "lmt": "1",
        "klt": "1",
        "secid": eastmoney_secid(code),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    payload = _em_get("https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get", params=params).json()
    rows = ((payload.get("data") or {}).get("klines") or [])
    if not rows:
        return {"code": normalize_code(code), "source": "Eastmoney push2his fund flow", "error": "fund_flow_missing"}
    fields = str(rows[-1]).split(",")
    return {
        "code": normalize_code(code),
        "date": fields[0] if fields else None,
        "main_net": _float(fields[1]) if len(fields) > 1 else None,
        "small_net": _float(fields[2]) if len(fields) > 2 else None,
        "medium_net": _float(fields[3]) if len(fields) > 3 else None,
        "large_net": _float(fields[4]) if len(fields) > 4 else None,
        "super_large_net": _float(fields[5]) if len(fields) > 5 else None,
        "source": "Eastmoney push2his fund flow",
    }


def fetch_announcements(code: str, limit: int = 5) -> list[dict[str, Any]]:
    """Fetch recent company announcements as an event-risk evidence source."""
    params = {
        "sr": "-1",
        "page_size": str(limit),
        "page_index": "1",
        "ann_type": "A",
        "client_source": "web",
        "stock_list": normalize_code(code),
        "f_node": "0",
        "s_node": "0",
    }
    payload = _em_get("https://np-anotice-stock.eastmoney.com/api/security/ann", params=params).json()
    rows = ((payload.get("data") or {}).get("list") or [])
    output: list[dict[str, Any]] = []
    for row in rows:
        title = row.get("title") or row.get("notice_title")
        if not title:
            continue
        output.append(
            {
                "title": title,
                "notice_date": row.get("notice_date") or row.get("display_time"),
                "art_code": row.get("art_code"),
                "source": "Eastmoney announcements",
            }
        )
    return output


def fetch_market_news(limit: int = 8) -> list[dict[str, Any]]:
    """Fetch current market catalysts, with CLS then Eastmoney fallback."""
    try:
        response = requests.get(
            "https://www.cls.cn/nodeapi/telegraphList",
            params={"rn": str(limit), "page": "1"},
            headers={"User-Agent": USER_AGENT, "Referer": "https://www.cls.cn/"},
            timeout=8,
        )
        response.raise_for_status()
        rows = ((response.json().get("data") or {}).get("roll_data") or [])
        items = [
            {
                "title": row.get("title") or row.get("brief"),
                "time": row.get("ctime"),
                "source": "CLS telegraph",
                "kind": "market_news",
            }
            for row in rows[:limit]
            if row.get("title") or row.get("brief")
        ]
        if items:
            return items
    except Exception:
        pass

    payload = _em_get(
        "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
        params={
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "0",
            "pageSize": str(limit),
            "req_trace": str(uuid.uuid4()),
        },
        headers={"Referer": "https://kuaixun.eastmoney.com/"},
        timeout=8,
    ).json()
    rows = ((payload.get("data") or {}).get("fastNewsList") or [])
    if not rows:
        raise ValueError("market_news_missing")
    return [
        {
            "title": row.get("title"),
            "time": row.get("showTime"),
            "source": "Eastmoney 7x24",
            "kind": "market_news",
        }
        for row in rows[:limit]
        if row.get("title")
    ]


def fetch_sector_heat(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch the leading Eastmoney industry/concept boards for candidate context."""
    params = {
        "pn": "1",
        "pz": str(limit),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90+t:2+f:!50",
        "fields": "f12,f14,f3,f62,f104,f105,f106,f184",
    }
    payload = _em_get("https://push2.eastmoney.com/api/qt/clist/get", params=params).json()
    rows = ((payload.get("data") or {}).get("diff") or [])
    output: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        output.append(
            {
                "rank": rank,
                "code": row.get("f12"),
                "name": row.get("f14"),
                "pct_change": _float(row.get("f3")),
                "main_net": _float(row.get("f62")),
                "main_net_ratio": _float(row.get("f184")),
                "advancers": _float(row.get("f104")),
                "decliners": _float(row.get("f105")),
                "unchanged": _float(row.get("f106")),
                "rotation_stage": "intraday_leader" if rank <= 5 else "watch",
                "source": "Eastmoney sector ranking",
            }
        )
    return output


def fetch_etf_leaders(limit: int = 12) -> list[dict[str, Any]]:
    """Fetch liquid ETF leaders as the safer side of the candidate pool."""
    params = {
        "pn": "1",
        "pz": str(limit),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
        "fields": "f12,f14,f2,f3,f5,f6,f8,f10,f62",
    }
    payload = _em_get("https://push2.eastmoney.com/api/qt/clist/get", params=params).json()
    rows = ((payload.get("data") or {}).get("diff") or [])
    output: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        output.append(
            {
                "rank": rank,
                "code": normalize_code(str(row.get("f12") or "")),
                "name": row.get("f14"),
                "price": _float(row.get("f2")),
                "pct_change": _float(row.get("f3")),
                "volume": _float(row.get("f5")),
                "turnover": _float(row.get("f6")),
                "turnover_rate": _float(row.get("f8")),
                "volume_ratio": _float(row.get("f10")),
                "main_net": _float(row.get("f62")),
                "source": "Eastmoney ETF ranking",
            }
        )
    return output


def _market_mood(index_quote: dict[str, Any], sector_heat: list[dict[str, Any]]) -> dict[str, Any]:
    pct_change = _float(index_quote.get("pct_change"))
    positive_sectors = sum(1 for item in sector_heat if (_float(item.get("pct_change")) or 0) > 0)
    if pct_change is None:
        mood = "unknown"
        buy_mode = "watch_only"
    elif pct_change < -1:
        mood = "risk_off"
        buy_mode = "risk_control"
    elif pct_change >= 0 and positive_sectors >= max(1, len(sector_heat) // 2):
        mood = "constructive"
        buy_mode = "rotation_or_conditional_buy"
    else:
        mood = "mixed"
        buy_mode = "rotation_watch"
    return {
        "mood": mood,
        "buy_mode_hint": buy_mode,
        "shanghai_pct_change": pct_change,
        "positive_hot_sectors": positive_sectors,
        "sector_sample_size": len(sector_heat),
        "source": "derived from verified index quote and Eastmoney sector ranking",
    }


def collect_market_snapshot(codes: list[str]) -> dict[str, Any]:
    normalized_codes = list(dict.fromkeys(normalize_code(code) for code in codes if normalize_code(code)))
    if settings.ACCEPTANCE_MODE:
        quotes = fetch_quotes(normalized_codes + ["000001"])
        complete = bool(normalized_codes) and all(code in quotes for code in normalized_codes)
        return {
            "captured_at": china_now().isoformat(timespec="seconds"),
            "quotes": {code: quotes.get(code, {"code": code, "error": "quote_missing", "stale": True}) for code in normalized_codes},
            "technicals": {
                code: {
                    "code": code,
                    "trend": "up",
                    "ma20": quotes.get(code, {}).get("price"),
                    "source": "Acceptance fixture",
                }
                for code in normalized_codes
            },
            "fund_flows": {
                code: {"code": code, "main_net": 1200000.0, "source": "Acceptance fixture"}
                for code in normalized_codes
            },
            "announcements": {code: [] for code in normalized_codes},
            "news": [{"title": "验收固定市场资讯", "source": "Acceptance fixture"}],
            "indices": {"sh000001": quotes.get("000001", {})},
            "sector_heat": [{"rank": 1, "name": "验收板块", "pct_change": 1.2, "source": "Acceptance fixture"}],
            "candidate_pool": {"etf_leaders": []},
            "market_mood": {
                "mood": "constructive" if complete else "unknown",
                "buy_mode_hint": "rotation_or_conditional_buy" if complete else "watch_only",
                "source": "Acceptance fixture",
            },
            "quality_grade": "A" if complete else "F",
            "errors": [] if complete else ["quote: acceptance fixture missing code"],
            "source_chain": ["Acceptance deterministic fixture"],
        }
    quotes: dict[str, Any]
    errors: list[str] = []
    try:
        quotes = fetch_quotes(normalized_codes + ["000001"])
    except Exception as exc:
        quotes = {code: {"code": code, "error": str(exc), "stale": True} for code in normalized_codes}
        errors.append(f"quote: {exc}")

    technicals: dict[str, Any] = {}
    fund_flows: dict[str, Any] = {}
    announcements: dict[str, Any] = {}
    for index, code in enumerate(normalized_codes):
        try:
            technicals[code] = fetch_kline(code)
        except Exception as exc:
            technicals[code] = {"code": code, "error": str(exc), "source": "Eastmoney push2his"}
            errors.append(f"kline {code}: {exc}")
        try:
            fund_flows[code] = fetch_fund_flow(code)
        except Exception as exc:
            fund_flows[code] = {"code": code, "error": str(exc), "source": "Eastmoney push2his fund flow"}
            errors.append(f"fund_flow {code}: {exc}")
        # Limit announcement calls for very large portfolios. The first holdings are
        # normally the largest because the confirmed snapshot preserves screen order.
        if index < 8:
            try:
                announcements[code] = fetch_announcements(code)
            except Exception as exc:
                announcements[code] = []
                errors.append(f"announcements {code}: {exc}")

    try:
        sector_heat = fetch_sector_heat()
    except Exception as exc:
        sector_heat = []
        errors.append(f"sector_heat: {exc}")
    try:
        etf_leaders = fetch_etf_leaders()
    except Exception as exc:
        etf_leaders = []
        errors.append(f"etf_leaders: {exc}")

    try:
        market_news = fetch_market_news()
    except Exception as exc:
        market_news = []
        errors.append(f"market_news: {exc}")

    company_news = [
        {**item, "code": code, "kind": "announcement"}
        for code, items in announcements.items()
        for item in items[:3]
    ]
    news = market_news + company_news

    holding_quotes = {code: quotes.get(code, {}) for code in normalized_codes}
    complete_quotes = sum(1 for item in holding_quotes.values() if item.get("price") is not None)
    ratio = complete_quotes / len(normalized_codes) if normalized_codes else 0
    # Quotes are mandatory. Optional technical/fund/event failures lower A to B but
    # do not block the run unless quote coverage itself becomes insufficient.
    if ratio == 1:
        grade = "A" if not errors else "B"
    elif ratio >= 0.8:
        grade = "B"
    elif ratio >= 0.5:
        grade = "C"
    else:
        grade = "F"
    index_quote = quotes.get("000001", {})
    return {
        "captured_at": china_now().isoformat(timespec="seconds"),
        "quotes": holding_quotes,
        "technicals": technicals,
        "fund_flows": fund_flows,
        "announcements": announcements,
        "news": news,
        "indices": {"sh000001": index_quote},
        "sector_heat": sector_heat,
        "candidate_pool": {"etf_leaders": etf_leaders},
        "market_mood": _market_mood(index_quote, sector_heat),
        "quality_grade": grade,
        "errors": errors,
        "source_chain": [
            "Tencent qt.gtimg.cn",
            "Eastmoney push2his K-line",
            "Eastmoney push2his fund flow",
            "Eastmoney announcements",
            "CLS telegraph / Eastmoney 7x24",
            "Eastmoney sector ranking",
            "Eastmoney ETF ranking",
        ],
    }


def refresh_snapshot_quotes(snapshot: dict[str, Any], codes: list[str]) -> dict[str, Any]:
    """Refresh quote-sensitive fields immediately before the visible decision."""
    refreshed = dict(snapshot)
    refreshed["final_quote_refresh_at"] = china_now().isoformat(timespec="seconds")
    try:
        quotes = fetch_quotes(codes)
        refreshed["quotes"] = {normalize_code(code): quotes.get(normalize_code(code), {}) for code in codes}
        refreshed["final_quote_refresh_status"] = "ok"
    except Exception as exc:
        refreshed["final_quote_refresh_status"] = "failed"
        refreshed["final_quote_refresh_error"] = str(exc)
        refreshed.setdefault("errors", []).append(f"final_quote_refresh: {exc}")
    return refreshed


def is_a_share_trading_day(now: datetime | None = None) -> bool:
    current = now.astimezone(CHINA_TZ) if now else china_now()
    if current.weekday() >= 5:
        return False
    try:
        quote = fetch_quotes(["000001"]).get("000001", {})
        raw = str(quote.get("quote_time") or "")
        digits = "".join(ch for ch in raw if ch.isdigit())
        return len(digits) >= 8 and digits[:8] == current.strftime("%Y%m%d")
    except Exception:
        return False
