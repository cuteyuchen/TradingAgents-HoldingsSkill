"""Read-only A-share market data collection with explicit source metadata."""
from __future__ import annotations

import math
import random
import re
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
_EM_LOCK = threading.Lock()
_EM_LAST_CALL = 0.0


def normalize_code(value: str) -> str:
    match = re.search(r"(\d{6})", value or "")
    return match.group(1) if match else (value or "").strip().upper()


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


def _em_get(url: str, *, params: dict[str, Any], timeout: float = 12.0) -> requests.Response:
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
            headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
            timeout=timeout,
        )
        _EM_LAST_CALL = time.monotonic()
    response.raise_for_status()
    return response


def _parse_tencent_line(line: str) -> dict[str, Any] | None:
    if '="' not in line:
        return None
    raw = line.split('="', 1)[1].rstrip('";\r\n')
    fields = raw.split("~")
    if len(fields) < 38:
        return None
    code = normalize_code(fields[2])
    quote_time = fields[30] or None
    return {
        "code": code,
        "name": fields[1] or None,
        "price": _float(fields[3]),
        "prev_close": _float(fields[4]),
        "open": _float(fields[5]),
        "pct_change": _float(fields[32]),
        "high": _float(fields[33]),
        "low": _float(fields[34]),
        "volume": _float(fields[36]),
        "turnover": _float(fields[37]),
        "quote_time": quote_time,
        "source": "Tencent qt.gtimg.cn",
        "stale": False,
    }


def fetch_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    normalized = list(dict.fromkeys(normalize_code(code) for code in codes if normalize_code(code)))
    if not normalized:
        return {}
    symbols = [tencent_symbol(code) for code in normalized]
    response = requests.get(
        "https://qt.gtimg.cn/q=" + ",".join(symbols),
        headers={"User-Agent": USER_AGENT, "Referer": "https://finance.qq.com/"},
        timeout=10,
    )
    response.raise_for_status()
    results: dict[str, dict[str, Any]] = {}
    for line in _decode(response.content).splitlines():
        parsed = _parse_tencent_line(line)
        if parsed and parsed["code"]:
            results[parsed["code"]] = parsed
    missing = set(normalized) - set(results)
    for code in missing:
        results[code] = {
            "code": code,
            "source": "Tencent qt.gtimg.cn",
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


def collect_market_snapshot(codes: list[str]) -> dict[str, Any]:
    normalized_codes = list(dict.fromkeys(normalize_code(code) for code in codes if normalize_code(code)))
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
    return {
        "captured_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        "quotes": holding_quotes,
        "technicals": technicals,
        "fund_flows": fund_flows,
        "announcements": announcements,
        "indices": {"sh000001": quotes.get("000001", {})},
        "quality_grade": grade,
        "errors": errors,
        "source_chain": [
            "Tencent qt.gtimg.cn",
            "Eastmoney push2his K-line",
            "Eastmoney push2his fund flow",
            "Eastmoney announcements",
        ],
    }


def refresh_snapshot_quotes(snapshot: dict[str, Any], codes: list[str]) -> dict[str, Any]:
    """Refresh quote-sensitive fields immediately before the visible decision."""
    refreshed = dict(snapshot)
    refreshed["final_quote_refresh_at"] = datetime.now(CHINA_TZ).isoformat(timespec="seconds")
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
    current = now.astimezone(CHINA_TZ) if now else datetime.now(CHINA_TZ)
    if current.weekday() >= 5:
        return False
    try:
        quote = fetch_quotes(["000001"]).get("000001", {})
        raw = str(quote.get("quote_time") or "")
        digits = "".join(ch for ch in raw if ch.isdigit())
        return len(digits) >= 8 and digits[:8] == current.strftime("%Y%m%d")
    except Exception:
        return False
