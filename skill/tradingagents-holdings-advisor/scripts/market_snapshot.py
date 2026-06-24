#!/usr/bin/env python3
"""Build a fast market snapshot for the holdings-advisor skill.

The script is intentionally read-only for holdings. It accepts holdings from a
JSON file or explicit codes, fetches public quotes in batch, and emits normalized
evidence JSON that the skill can reuse for analysis and final quote refresh.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

CHINA_TZ = timezone(timedelta(hours=8))
MISSING_QUOTE = "[数据缺失: quote]"


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
        if name in {"新标准券", "标准券"} or "国债逆回购" in name:
            continue
        copied = dict(row)
        copied["code"] = normalize_code(copied.get("code"))
        holdings.append(copied)
    return holdings


def holdings_from_codes(codes: str) -> list[dict[str, Any]]:
    return [{"code": normalize_code(code)} for code in codes.split(",") if normalize_code(code)]


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


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


def fetch_tencent_quotes(holdings: list[dict[str, Any]], timeout_sec: float = 6.0) -> dict[str, dict[str, Any]]:
    symbols = build_tencent_symbols(holdings)
    if not symbols:
        return {}
    query = urllib.parse.quote(",".join(symbols), safe=",")
    url = f"https://qt.gtimg.cn/q={query}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        text = decode_tencent_bytes(response.read())
    quotes: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        parsed = _parse_tencent_line(line)
        if parsed and parsed.get("code"):
            quotes[str(parsed["code"])] = parsed
    return quotes


def missing_quote() -> dict[str, Any]:
    return {
        "source": MISSING_QUOTE,
        "quote_time": None,
        "market_session": "unknown",
        "missing_reason": "public quote fetch not run or failed",
    }


def build_empty_snapshot(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "timestamp": now_iso(),
        "data_strategy": "fast_snapshot",
        "holdings": [
            {
                **holding,
                "quote": dict(holding.get("quote") or missing_quote()),
            }
            for holding in holdings
        ],
        "market": {},
        "candidates": [],
        "missing_fields": ["quote"],
    }


def build_snapshot(
    holdings: list[dict[str, Any]],
    quote_fetcher: Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]] = fetch_tencent_quotes,
) -> dict[str, Any]:
    snapshot = build_empty_snapshot(holdings)
    try:
        quotes = quote_fetcher(holdings)
    except Exception as exc:  # noqa: BLE001
        snapshot["errors"] = [f"quote fetch failed: {exc}"]
        return snapshot

    missing: list[str] = []
    for holding in snapshot["holdings"]:
        code = normalize_code(holding.get("code"))
        quote = quotes.get(code)
        if quote:
            holding["quote"] = quote
        else:
            missing.append(code)
    snapshot["missing_fields"] = [f"quote:{code}" for code in missing] if missing else []
    return snapshot


def refresh_final_quotes(
    snapshot: dict[str, Any],
    quote_fetcher: Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]] = fetch_tencent_quotes,
) -> dict[str, Any]:
    holdings = [row for row in snapshot.get("holdings", []) if isinstance(row, dict)]
    quotes = quote_fetcher(holdings)
    for holding in holdings:
        code = normalize_code(holding.get("code"))
        if code in quotes:
            holding["quote"] = quotes[code]
    snapshot["final_quote_refresh_at"] = now_iso()
    return snapshot


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build holdings market snapshot JSON.")
    parser.add_argument("--holdings", type=Path, help="Path to holdings JSON list or object with holdings[].")
    parser.add_argument("--codes", help="Comma-separated 6-digit codes when no holdings JSON is available.")
    parser.add_argument("--output", type=Path, help="Write JSON to this file instead of stdout.")
    parser.add_argument("--timeout", type=float, default=6.0, help="Single quote request timeout in seconds.")
    parser.add_argument("--no-network", action="store_true", help="Emit normalized skeleton without public quote fetch.")
    parser.add_argument("--refresh-final", type=Path, help="Refresh quote fields in an existing snapshot JSON file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.refresh_final:
        snapshot = json.loads(args.refresh_final.read_text(encoding="utf-8"))
        result = refresh_final_quotes(snapshot, lambda rows: fetch_tencent_quotes(rows, args.timeout))
    else:
        if args.holdings:
            holdings = load_holdings(args.holdings)
        elif args.codes:
            holdings = holdings_from_codes(args.codes)
        else:
            raise SystemExit("provide --holdings or --codes")
        result = build_empty_snapshot(holdings) if args.no_network else build_snapshot(
            holdings,
            lambda rows: fetch_tencent_quotes(rows, args.timeout),
        )

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
