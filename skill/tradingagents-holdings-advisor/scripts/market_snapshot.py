#!/usr/bin/env python3
"""Build a verified market snapshot for the holdings-advisor skill.

The script is intentionally read-only for holdings. It accepts holdings from a
JSON file or explicit codes, fetches public market evidence once, and emits
normalized JSON that the skill can reuse across analysts, debate, risk review,
candidate scoring, and final quote refresh.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import requests
except ImportError:  # pragma: no cover - exercised only in minimal runtimes
    requests = None  # type: ignore[assignment]

CHINA_TZ = timezone(timedelta(hours=8))
MISSING_QUOTE = "[数据缺失: quote]"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
EM_MIN_INTERVAL_SEC = float(os.getenv("EM_MIN_INTERVAL", "1.0"))
EM_JITTER_RANGE = (0.1, 0.5)
NORTHBOUND_CACHE = Path(os.getenv("TRADINGAGENTS_NORTHBOUND_CACHE", Path.home() / ".tradingagents" / "cache" / "northbound_daily.csv"))

_SESSION = None
_EM_LOCK = threading.Lock()
_LAST_EM_CALL = 0.0


def now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else text


def tencent_symbol(code: str) -> str:
    normalized = normalize_code(code)
    if not re.fullmatch(r"\d{6}", normalized):
        return normalized.lower()
    if normalized.startswith(("5", "6", "9")):
        return f"sh{normalized}"
    return f"sz{normalized}"


def sina_symbol(code: str) -> str:
    normalized = normalize_code(code)
    if normalized.startswith(("5", "6", "9")):
        return f"sh{normalized}"
    if normalized.startswith("8"):
        return f"bj{normalized}"
    return f"sz{normalized}"


def eastmoney_secid(code: str) -> str:
    normalized = normalize_code(code)
    market = "1" if normalized.startswith(("5", "6", "9")) else "0"
    return f"{market}.{normalized}"


def build_tencent_symbols(holdings: list[dict[str, Any]]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for holding in holdings:
        code = normalize_code(holding.get("code"))
        if not code or code in seen:
            continue
        seen.add(code)
        symbols.append(tencent_symbol(code))
    return symbols


class TTLCache:
    def __init__(self, ttl_sec: int = 15):
        self.ttl_sec = ttl_sec
        self._values: dict[str, tuple[float, Any]] = {}

    def get_or_fetch(self, key: str, fetcher: Callable[[], Any]) -> Any:
        now = time.time()
        cached = self._values.get(key)
        if cached and now - cached[0] < self.ttl_sec:
            return cached[1]
        value = fetcher()
        self._values[key] = (now, value)
        return value


def _session():
    global _SESSION
    if requests is None:
        raise RuntimeError("requests is required for Eastmoney/CLS/Baidu sources")
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    return _SESSION


def _em_get(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 8.0):
    """Eastmoney GET with process-wide serial throttling.

    This mirrors the upstream astock lesson: all eastmoney.com requests must go
    through one throttle to avoid temporary IP bans during multi-agent runs.
    """
    global _LAST_EM_CALL
    with _EM_LOCK:
        elapsed = time.time() - _LAST_EM_CALL
        delay = EM_MIN_INTERVAL_SEC - elapsed
        if delay > 0:
            time.sleep(delay)
        time.sleep(random.uniform(*EM_JITTER_RANGE))
        response = _session().get(url, params=params, headers=headers, timeout=timeout)
        _LAST_EM_CALL = time.time()
    response.raise_for_status()
    return response


def load_holdings(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("holdings") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("holdings JSON must be a list or an object with holdings[]")
    holdings: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if name in {"新标准券", "标准券"} or "国债逆回购" in name or "逆回购" in name:
            continue
        copied = dict(row)
        copied["code"] = normalize_code(copied.get("code"))
        holdings.append(copied)
    return holdings


def holdings_from_codes(codes: str) -> list[dict[str, Any]]:
    return [{"code": normalize_code(code)} for code in codes.split(",") if normalize_code(code)]


def _to_float(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _scaled(value: Any, scale: float = 100.0) -> float | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return parsed / scale


def _parse_tencent_line(line: str) -> dict[str, Any] | None:
    if not line.strip() or '="' not in line:
        return None
    _, raw = line.split('="', 1)
    fields = raw.rstrip('";\n\r').split("~")
    if len(fields) < 6:
        return None
    code = normalize_code(fields[2] if len(fields) > 2 else "")
    price = _to_float(fields[3] if len(fields) > 3 else None)
    prev_close = _to_float(fields[4] if len(fields) > 4 else None)
    open_price = _to_float(fields[5] if len(fields) > 5 else None)
    pct_change = _to_float(fields[32] if len(fields) > 32 else None)
    high = _to_float(fields[33] if len(fields) > 33 else None)
    low = _to_float(fields[34] if len(fields) > 34 else None)
    volume = _to_float(fields[36] if len(fields) > 36 else None)
    turnover = _to_float(fields[37] if len(fields) > 37 else None)
    quote_time = fields[30] if len(fields) > 30 and fields[30] else now_iso()
    return {
        "code": code,
        "name": fields[1] if len(fields) > 1 else None,
        "price": price,
        "prev_close": prev_close,
        "open": open_price,
        "pct_change": pct_change,
        "high": high,
        "low": low,
        "volume": volume,
        "turnover": turnover,
        "source": "Tencent qt.gtimg.cn",
        "quote_time": quote_time,
        "market_session": "trading_or_latest",
    }


def decode_tencent_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _fetch_tencent_symbol_list(symbols: list[str], timeout_sec: float = 6.0) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    query = urllib.parse.quote(",".join(symbols), safe=",")
    url = f"https://qt.gtimg.cn/q={query}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        text = decode_tencent_bytes(response.read())
    quotes: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        parsed = _parse_tencent_line(line)
        if parsed and parsed.get("code"):
            quotes[str(parsed["code"])] = parsed
    return quotes


def fetch_tencent_quotes(holdings: list[dict[str, Any]], timeout_sec: float = 6.0) -> dict[str, dict[str, Any]]:
    return _fetch_tencent_symbol_list(build_tencent_symbols(holdings), timeout_sec)


def fetch_sina_quotes(holdings: list[dict[str, Any]], timeout_sec: float = 6.0) -> dict[str, dict[str, Any]]:
    symbols = [sina_symbol(row.get("code")) for row in holdings if normalize_code(row.get("code"))]
    if not symbols:
        return {}
    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        text = response.read().decode("gb18030", errors="replace")
    quotes: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if '="' not in line:
            continue
        prefix, raw = line.split('="', 1)
        fields = raw.rstrip('";\n\r').split(",")
        if len(fields) < 32 or not fields[0]:
            continue
        code = normalize_code(prefix)
        quotes[code] = {
            "code": code,
            "name": fields[0],
            "open": _to_float(fields[1]),
            "prev_close": _to_float(fields[2]),
            "price": _to_float(fields[3]),
            "high": _to_float(fields[4]),
            "low": _to_float(fields[5]),
            "volume": _to_float(fields[8]),
            "turnover": _to_float(fields[9]),
            "pct_change": _calc_pct(_to_float(fields[3]), _to_float(fields[2])),
            "source": "Sina hq.sinajs.cn",
            "quote_time": f"{fields[30]} {fields[31]}",
            "market_session": "trading_or_latest",
        }
    return quotes


def fetch_eastmoney_quotes(holdings: list[dict[str, Any]], timeout_sec: float = 8.0) -> dict[str, dict[str, Any]]:
    secids = [eastmoney_secid(row.get("code")) for row in holdings if normalize_code(row.get("code"))]
    if not secids:
        return {}
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "secids": ",".join(secids),
        "fields": "f12,f14,f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f170",
    }
    data = _em_get(url, params=params, timeout=timeout_sec).json().get("data", {})
    rows = data.get("diff") or []
    quotes: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = normalize_code(row.get("f12") or row.get("f57"))
        if not code:
            continue
        price = _to_float(row.get("f43"))
        prev_close = _to_float(row.get("f60"))
        quotes[code] = {
            "code": code,
            "name": row.get("f14") or row.get("f58"),
            "price": price,
            "prev_close": prev_close,
            "open": _to_float(row.get("f46")),
            "high": _to_float(row.get("f44")),
            "low": _to_float(row.get("f45")),
            "volume": _to_float(row.get("f47")),
            "turnover": _to_float(row.get("f48")),
            "pct_change": _to_float(row.get("f170")) or _calc_pct(price, prev_close),
            "source": "Eastmoney push2",
            "quote_time": str(row.get("f86") or now_iso()),
            "market_session": "trading_or_latest",
        }
    return quotes


def _calc_pct(price: float | None, prev_close: float | None) -> float | None:
    if price is None or prev_close in (None, 0):
        return None
    return round((price - prev_close) / prev_close * 100, 4)


def fetch_quote_chain(holdings: list[dict[str, Any]], timeout_sec: float = 6.0) -> dict[str, Any]:
    quotes: dict[str, dict[str, Any]] = {}
    source_chains: dict[str, list[str]] = {normalize_code(row.get("code")): [] for row in holdings}
    errors: list[str] = []
    routes: list[tuple[str, Callable[[list[dict[str, Any]], float], dict[str, dict[str, Any]]]]] = [
        ("Tencent qt.gtimg.cn", fetch_tencent_quotes),
        ("Sina hq.sinajs.cn", fetch_sina_quotes),
        ("Eastmoney push2", fetch_eastmoney_quotes),
    ]

    remaining = list(holdings)
    for source, fetcher in routes:
        if not remaining:
            break
        for row in remaining:
            source_chains.setdefault(normalize_code(row.get("code")), []).append(source)
        try:
            fetched = fetcher(remaining, timeout_sec)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source} quote failed: {type(exc).__name__}: {exc}")
            continue
        quotes.update(fetched)
        remaining = [row for row in remaining if normalize_code(row.get("code")) not in quotes]

    return {"quotes": quotes, "source_chains": source_chains, "errors": errors}


def missing_quote(source_chain: list[str] | None = None) -> dict[str, Any]:
    return {
        "source": MISSING_QUOTE,
        "quote_time": None,
        "market_session": "unknown",
        "source_chain": source_chain or [],
        "missing_reason": "all configured public quote routes failed or were not run",
    }


def build_empty_snapshot(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = {
        "schema_version": "2026-06-25.verified-snapshot.v1",
        "timestamp": now_iso(),
        "data_strategy": "verified_fast_snapshot",
        "source_chain": {
            "quote": ["Tencent qt.gtimg.cn", "Sina hq.sinajs.cn", "Eastmoney push2"],
            "fund_flow": ["Eastmoney push2"],
            "northbound": ["THS hsgtApi realtime", "local CSV cache"],
            "concept_blocks": ["Baidu finance.pae.baidu.com", "Eastmoney sector fallback"],
            "news": ["CLS telegraph", "Eastmoney 7x24 with req_trace"],
        },
        "holdings": [
            {
                **holding,
                "quote": dict(holding.get("quote") or missing_quote()),
                "fund_flow": {"source": "[数据缺失: fund_flow]", "missing_reason": "public fund flow fetch not run"},
                "concept_blocks": {"source": "[数据缺失: concept_blocks]", "items": []},
                "news": {"source": "[数据缺失: news]", "items": []},
                "vpa": {"source": "[数据缺失: vpa]", "missing_reason": "quote/OHLCV unavailable"},
                "data_quality": "F",
            }
            for holding in holdings
        ],
        "market": {
            "major_indices": [],
            "hot_sectors": [],
            "northbound": {"source": "[数据缺失: northbound]"},
            "news": [],
        },
        "candidates": [],
        "missing_fields": ["quote"],
        "errors": [],
        "quality_gate": {
            "grade": "F",
            "action_allowed": False,
            "new_buy_allowed": False,
            "blockers": ["quote"],
            "notes": ["No public data fetched"],
        },
    }
    return snapshot


def _coerce_quote_result(result: Any) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[str]]:
    if isinstance(result, dict) and "quotes" in result:
        return (
            result.get("quotes") or {},
            result.get("source_chains") or {},
            result.get("errors") or [],
        )
    if isinstance(result, dict):
        return result, {}, []
    return {}, {}, [f"unexpected quote result: {type(result).__name__}"]


def fetch_market_context(timeout_sec: float = 8.0) -> tuple[dict[str, Any], list[str], list[str]]:
    market = {"major_indices": [], "hot_sectors": [], "northbound": {}, "news": []}
    missing: list[str] = []
    errors: list[str] = []

    try:
        market["major_indices"] = list(_fetch_tencent_symbol_list(["sh000001", "sz399001", "sz399006", "sh000688"], timeout_sec).values())
        if not market["major_indices"]:
            missing.append("market.major_indices")
    except Exception as exc:  # noqa: BLE001
        missing.append("market.major_indices")
        errors.append(f"major indices failed: {type(exc).__name__}: {exc}")

    try:
        market["hot_sectors"] = fetch_hot_sectors(timeout_sec)
        if not market["hot_sectors"]:
            missing.append("market.hot_sectors")
    except Exception as exc:  # noqa: BLE001
        missing.append("market.hot_sectors")
        errors.append(f"hot sectors failed: {type(exc).__name__}: {exc}")

    try:
        market["northbound"] = fetch_northbound_flow(timeout_sec)
        if not market["northbound"].get("history"):
            missing.append("market.northbound.history")
    except Exception as exc:  # noqa: BLE001
        missing.append("market.northbound")
        errors.append(f"northbound failed: {type(exc).__name__}: {exc}")

    try:
        market["news"] = fetch_market_news(timeout_sec)
        if not market["news"]:
            missing.append("market.news")
    except Exception as exc:  # noqa: BLE001
        missing.append("market.news")
        errors.append(f"market news failed: {type(exc).__name__}: {exc}")

    return market, missing, errors


def fetch_hot_sectors(timeout_sec: float = 8.0) -> list[dict[str, Any]]:
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "10",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90+t:2",
        "fields": "f12,f14,f3,f62,f128",
    }
    rows = (_em_get(url, params=params, timeout=timeout_sec).json().get("data", {}) or {}).get("diff") or []
    return [
        {
            "code": row.get("f12"),
            "name": row.get("f14"),
            "pct_change": _to_float(row.get("f3")),
            "main_net_flow": _to_float(row.get("f62")),
            "leader": row.get("f128"),
            "source": "Eastmoney push2 sector",
        }
        for row in rows[:10]
    ]


def fetch_northbound_flow(timeout_sec: float = 8.0) -> dict[str, Any]:
    url = "https://push2.eastmoney.com/api/qt/kamt/get"
    params = {"fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60"}
    payload = _em_get(url, params=params, timeout=timeout_sec).json().get("data") or {}
    value = payload.get("hk2sh") or payload.get("hk2sz") or payload
    result = {
        "source": "Eastmoney/THS-compatible realtime snapshot",
        "snapshot_time": now_iso(),
        "raw": value,
        "history_source": "local_csv_cache_only",
        "history": load_northbound_history(limit=20),
    }
    save_northbound_snapshot(result)
    return result


def save_northbound_snapshot(snapshot: dict[str, Any]) -> None:
    NORTHBOUND_CACHE.parent.mkdir(parents=True, exist_ok=True)
    exists = NORTHBOUND_CACHE.exists()
    with NORTHBOUND_CACHE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["date", "snapshot_time", "source"])
        if not exists:
            writer.writeheader()
        writer.writerow({
            "date": datetime.now(CHINA_TZ).strftime("%Y-%m-%d"),
            "snapshot_time": snapshot.get("snapshot_time"),
            "source": snapshot.get("source"),
        })


def load_northbound_history(limit: int = 20) -> list[dict[str, str]]:
    if not NORTHBOUND_CACHE.exists():
        return []
    with NORTHBOUND_CACHE.open("r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return rows[-limit:]


def fetch_market_news(timeout_sec: float = 8.0) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if requests is not None:
        try:
            cls_url = "https://www.cls.cn/nodeapi/telegraphList"
            data = _session().get(cls_url, params={"rn": "5", "page": "1"}, headers={"Referer": "https://www.cls.cn/"}, timeout=timeout_sec).json()
            for row in data.get("data", {}).get("roll_data", [])[:5]:
                items.append({"title": row.get("title") or row.get("brief"), "source": "CLS telegraph", "time": row.get("ctime")})
        except Exception:
            pass
    if items:
        return items

    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {"client": "web", "biz": "web_724", "fastColumn": "102", "pageSize": "5", "req_trace": str(uuid.uuid4())}
    rows = (_em_get(url, params=params, headers={"Referer": "https://kuaixun.eastmoney.com/"}, timeout=timeout_sec).json().get("data", {}) or {}).get("fastNewsList") or []
    return [{"title": row.get("title"), "source": "Eastmoney 7x24", "time": row.get("showTime")} for row in rows[:5]]


def fetch_fund_flow(code: str, timeout_sec: float = 8.0) -> dict[str, Any]:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "secid": eastmoney_secid(code),
        "fields": "f57,f58,f62,f66,f69,f72,f75,f78,f81,f84,f87,f164,f184",
    }
    data = _em_get(url, params=params, timeout=timeout_sec).json().get("data") or {}
    if not data:
        raise ValueError("empty Eastmoney fund-flow payload")
    return {
        "source": "Eastmoney push2",
        "main_net": _to_float(data.get("f62")),
        "main_net_pct": _to_float(data.get("f184")),
        "super_large_net": _to_float(data.get("f66")),
        "large_net": _to_float(data.get("f72")),
        "medium_net": _to_float(data.get("f78")),
        "small_net": _to_float(data.get("f84")),
    }


def fetch_concept_blocks(code: str, timeout_sec: float = 8.0) -> dict[str, Any]:
    """Fetch concept/sector tags.

    Baidu PAE is used only for classification. Fund-flow data must not be read
    from Baidu PAE because the upstream astock project confirmed that endpoint
    is unavailable.
    """
    try:
        url = "https://finance.pae.baidu.com/vapi/v1/stock"
        data = _session().get(url, params={"code": code, "finClientType": "pc"}, headers={"Referer": "https://gushitong.baidu.com/"}, timeout=timeout_sec).json()
        result = data.get("Result") or data.get("result") or {}
        tags = result.get("concepts") or result.get("plate") or result.get("industry") or []
        if isinstance(tags, str):
            tags = [tags]
        if tags:
            return {"source": "Baidu finance.pae.baidu.com classification", "items": tags[:12]}
    except Exception:
        pass
    return {"source": "[数据缺失: concept_blocks]", "items": [], "missing_reason": "Baidu classification route unavailable"}


def compute_basic_vpa(quote: dict[str, Any]) -> dict[str, Any]:
    price = quote.get("price")
    high = quote.get("high")
    low = quote.get("low")
    open_price = quote.get("open")
    if price is None or high is None or low is None or open_price is None or high == low:
        return {"source": "[数据缺失: vpa]", "missing_reason": "quote OHLC fields incomplete"}
    close_position = (price - low) / (high - low)
    bar_type = "阳线" if price > open_price else "阴线" if price < open_price else "十字星"
    return {
        "source": "quote_derived_intraday_vpa",
        "bar_type": bar_type,
        "close_position": round(close_position, 4),
        "intraday_range_pct": _calc_pct(high, low),
        "bearish_intraday": close_position < 0.3,
        "bullish_intraday": close_position > 0.7,
    }


def enrich_holding(holding: dict[str, Any], timeout_sec: float = 8.0) -> tuple[dict[str, Any], list[str], list[str]]:
    code = normalize_code(holding.get("code"))
    missing: list[str] = []
    errors: list[str] = []

    try:
        holding["fund_flow"] = fetch_fund_flow(code, timeout_sec)
    except Exception as exc:  # noqa: BLE001
        holding["fund_flow"] = {"source": "[数据缺失: fund_flow]", "missing_reason": str(exc)}
        missing.append(f"fund_flow:{code}")
        errors.append(f"{code} fund flow failed: {type(exc).__name__}: {exc}")

    try:
        holding["concept_blocks"] = fetch_concept_blocks(code, timeout_sec)
        if not holding["concept_blocks"].get("items"):
            missing.append(f"concept_blocks:{code}")
    except Exception as exc:  # noqa: BLE001
        holding["concept_blocks"] = {"source": "[数据缺失: concept_blocks]", "items": [], "missing_reason": str(exc)}
        missing.append(f"concept_blocks:{code}")
        errors.append(f"{code} concept blocks failed: {type(exc).__name__}: {exc}")

    holding["vpa"] = compute_basic_vpa(holding.get("quote") or {})
    if str(holding["vpa"].get("source", "")).startswith("[数据缺失"):
        missing.append(f"vpa:{code}")

    holding["data_quality"] = grade_holding_quality(holding)
    return holding, missing, errors


def grade_holding_quality(holding: dict[str, Any]) -> str:
    quote = holding.get("quote") or {}
    if quote.get("source") == MISSING_QUOTE or quote.get("price") is None:
        return "F"
    missing = 0
    for key in ("fund_flow", "concept_blocks", "vpa"):
        value = holding.get(key) or {}
        if str(value.get("source", "")).startswith("[数据缺失"):
            missing += 1
    if missing >= 3:
        return "D"
    if missing == 2:
        return "C"
    if missing == 1:
        return "B"
    return "A"


def assess_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    missing = set(snapshot.get("missing_fields") or [])
    blockers: list[str] = []
    notes: list[str] = []
    if any(str(item).startswith("quote") for item in missing):
        blockers.append("quote")
        notes.append("缺少持仓行情，阻断对应标的交易动作")
    if "market.major_indices" in missing:
        notes.append("缺少主要指数，市场方向可信度下降")
    if "market.hot_sectors" in missing or any(str(item).startswith("concept_blocks") for item in missing):
        notes.append("缺少板块/概念位置，阻断新买入候选")
    if any(str(item).startswith("fund_flow") for item in missing):
        notes.append("缺少个股资金流，降低持仓动作置信度")
    if "market.news" in missing:
        notes.append("缺少新闻/基本面快照，需人工核对催化或风险")
    if "market.northbound.history" in missing:
        notes.append("北向仅有实时快照，本地历史不足，不能推断趋势")

    grades = [row.get("data_quality", "F") for row in snapshot.get("holdings", []) if isinstance(row, dict)]
    if blockers:
        grade = "F"
    elif any(g in ("D", "F") for g in grades):
        grade = "D"
    elif len(missing) >= 4:
        grade = "C"
    elif missing:
        grade = "B"
    else:
        grade = "A"

    new_buy_allowed = grade in ("A", "B") and "market.hot_sectors" not in missing and not any(str(item).startswith("concept_blocks") for item in missing)
    return {
        "grade": grade,
        "action_allowed": not blockers and grade in ("A", "B", "C"),
        "new_buy_allowed": new_buy_allowed,
        "blockers": blockers,
        "notes": notes,
        "missing_count": len(missing),
    }


def build_snapshot(
    holdings: list[dict[str, Any]],
    quote_fetcher: Callable[[list[dict[str, Any]]], Any] = fetch_quote_chain,
    context_fetcher: Callable[[], tuple[dict[str, Any], list[str], list[str]]] | None = None,
    holding_enricher: Callable[[dict[str, Any]], tuple[dict[str, Any], list[str], list[str]]] | None = None,
) -> dict[str, Any]:
    snapshot = build_empty_snapshot(holdings)
    try:
        quotes, source_chains, quote_errors = _coerce_quote_result(quote_fetcher(holdings))
        snapshot["errors"].extend(quote_errors)
    except Exception as exc:  # noqa: BLE001
        quotes, source_chains = {}, {}
        snapshot["errors"].append(f"quote fetch failed: {type(exc).__name__}: {exc}")

    missing: list[str] = []
    for holding in snapshot["holdings"]:
        code = normalize_code(holding.get("code"))
        quote = quotes.get(code)
        if quote:
            quote["source_chain"] = source_chains.get(code, [quote.get("source")])
            holding["quote"] = quote
        else:
            holding["quote"] = missing_quote(source_chains.get(code, []))
            missing.append(f"quote:{code}")

    if context_fetcher is None:
        context_fetcher = fetch_market_context
    try:
        market, market_missing, market_errors = context_fetcher()
        snapshot["market"] = market
        missing.extend(market_missing)
        snapshot["errors"].extend(market_errors)
    except Exception as exc:  # noqa: BLE001
        missing.extend(["market.major_indices", "market.hot_sectors", "market.northbound", "market.news"])
        snapshot["errors"].append(f"market context failed: {type(exc).__name__}: {exc}")

    if holding_enricher is None:
        holding_enricher = enrich_holding
    enriched: list[dict[str, Any]] = []
    for holding in snapshot["holdings"]:
        try:
            item, item_missing, item_errors = holding_enricher(holding)
            enriched.append(item)
            missing.extend(item_missing)
            snapshot["errors"].extend(item_errors)
        except Exception as exc:  # noqa: BLE001
            holding["data_quality"] = grade_holding_quality(holding)
            enriched.append(holding)
            missing.append(f"holding_enrichment:{normalize_code(holding.get('code'))}")
            snapshot["errors"].append(f"holding enrichment failed: {type(exc).__name__}: {exc}")
    snapshot["holdings"] = enriched

    snapshot["missing_fields"] = sorted(set(missing))
    snapshot["quality_gate"] = assess_quality(snapshot)
    return snapshot


def refresh_final_quotes(
    snapshot: dict[str, Any],
    quote_fetcher: Callable[[list[dict[str, Any]]], Any] = fetch_quote_chain,
) -> dict[str, Any]:
    holdings = [row for row in snapshot.get("holdings", []) if isinstance(row, dict)]
    quotes, source_chains, errors = _coerce_quote_result(quote_fetcher(holdings))
    if errors:
        snapshot.setdefault("errors", []).extend(errors)
    missing = [item for item in snapshot.get("missing_fields", []) if not str(item).startswith("quote:")]
    for holding in holdings:
        code = normalize_code(holding.get("code"))
        if code in quotes:
            quote = quotes[code]
            quote["source_chain"] = source_chains.get(code, [quote.get("source")])
            holding["quote"] = quote
            holding["vpa"] = compute_basic_vpa(quote)
            holding["data_quality"] = grade_holding_quality(holding)
        else:
            missing.append(f"quote:{code}")
    snapshot["missing_fields"] = sorted(set(missing))
    snapshot["final_quote_refresh_at"] = now_iso()
    snapshot["quality_gate"] = assess_quality(snapshot)
    return snapshot


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build holdings market snapshot JSON.")
    parser.add_argument("--holdings", type=Path, help="Path to holdings JSON list or object with holdings[].")
    parser.add_argument("--codes", help="Comma-separated 6-digit codes when no holdings JSON is available.")
    parser.add_argument("--output", type=Path, help="Write JSON to this file instead of stdout.")
    parser.add_argument("--timeout", type=float, default=6.0, help="Single request timeout in seconds.")
    parser.add_argument("--no-network", action="store_true", help="Emit normalized skeleton without public data fetch.")
    parser.add_argument("--refresh-final", type=Path, help="Refresh quote fields in an existing snapshot JSON file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.refresh_final:
        snapshot = json.loads(args.refresh_final.read_text(encoding="utf-8"))
        result = refresh_final_quotes(snapshot, lambda rows: fetch_quote_chain(rows, args.timeout))
    else:
        if args.holdings:
            holdings = load_holdings(args.holdings)
        elif args.codes:
            holdings = holdings_from_codes(args.codes)
        else:
            raise SystemExit("provide --holdings or --codes")
        result = build_empty_snapshot(holdings) if args.no_network else build_snapshot(
            holdings,
            lambda rows: fetch_quote_chain(rows, args.timeout),
            lambda: fetch_market_context(args.timeout),
            lambda row: enrich_holding(row, args.timeout),
        )

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
