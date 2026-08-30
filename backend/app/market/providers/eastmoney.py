"""Eastmoney ``push2`` batch quote adapter.

Only this module knows Eastmoney's ``fXX`` field names.  Callers receive the
same provider-neutral :class:`~app.market.models.NormalizedQuote` objects as
the Tencent adapter, so a provider swap cannot leak wire-format details into
the analysis layer.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

import requests

from ..codes import exchange_for_code, normalize_security_code
from ..models import DataQualityStatus, NormalizedQuote
from .base import QuoteProvider


EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "null"):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _scaled_price(value: Any) -> float | None:
    """Decode a price from a response requested with ``fltt=2``."""

    return _number(value)


def _scaled_percent(value: Any) -> float | None:
    return _number(value)


def _source_timestamp(value: Any) -> datetime | str | None:
    """Decode common Eastmoney timestamp spellings."""

    if value in (None, "", "-", "null"):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            if len(text) == 14:
                return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ)
            if len(text) >= 13:
                return datetime.fromtimestamp(int(text[:13]) / 1000, tz=UTC)
            if len(text) == 10:
                return datetime.fromtimestamp(int(text), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    # ``HH:MM:SS`` is intentionally left as a string.  The canonical model
    # resolves it against the current China trading date.
    return text


def _infer_security_type(code: str) -> str:
    # A-share ETFs are commonly in the 15/16/18/50/51/56/58 ranges.  This is
    # only a provider-neutral hint; SecurityMaster remains the source of truth.
    return "ETF" if code.startswith(("15", "16", "18", "50", "51", "56", "58")) else "STOCK"


def _payload(response: Any) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        value = json_method()
        if isinstance(value, Mapping):
            return value
    raw = getattr(response, "content", None)
    if raw is None:
        raw = getattr(response, "text", None)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        import json

        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, Mapping) else {}
    return {}


def _rows(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], int | None]:
    data = payload.get("data") or {}
    if not isinstance(data, Mapping):
        return [], None
    raw_rows = data.get("diff") or data.get("rows") or data.get("list") or []
    if isinstance(raw_rows, Mapping):
        raw_rows = list(raw_rows.values())
    if not isinstance(raw_rows, Iterable) or isinstance(raw_rows, (str, bytes)):
        return [], _int_or_none(data.get("total"))
    result = [row for row in raw_rows if isinstance(row, Mapping)]
    return result, _int_or_none(data.get("total"))


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def parse_eastmoney_row(
    row: Mapping[str, Any],
    *,
    fetched_at: datetime | None = None,
    provider: str = "eastmoney_batch",
) -> NormalizedQuote | None:
    """Translate one Eastmoney row without exposing ``fXX`` names."""

    code = normalize_security_code(row.get("f12") or row.get("code") or row.get("symbol"))
    if not code:
        return None
    source_time = _source_timestamp(row.get("f86") or row.get("source_timestamp") or row.get("quote_time"))
    price = _scaled_price(row.get("f43", row.get("price")))
    quality = DataQualityStatus.VALID if price is not None else DataQualityStatus.MISSING
    errors = [] if price is not None else ["quote_missing"]
    return NormalizedQuote(
        market="CN",
        exchange=exchange_for_code(code),
        code=code,
        name=str(row.get("f14") or row.get("name") or "").strip() or None,
        security_type=str(row.get("security_type") or _infer_security_type(code)).upper(),
        price=price,
        prev_close=_scaled_price(row.get("f60", row.get("prev_close"))),
        open=_scaled_price(row.get("f46", row.get("open"))),
        high=_scaled_price(row.get("f44", row.get("high"))),
        low=_scaled_price(row.get("f45", row.get("low"))),
        pct_change=_scaled_percent(row.get("f170", row.get("pct_change"))),
        volume=_number(row.get("f47", row.get("volume"))),
        amount=_number(row.get("f48", row.get("amount"))),
        source_timestamp=source_time,
        provider=provider,
        fetched_at=fetched_at or datetime.now(UTC),
        quality_status=quality,
        raw_reference=EASTMONEY_QUOTE_URL,
        is_suspended=bool(row.get("is_suspended") or row.get("suspended")),
        errors=errors,
    )


class EastmoneyBatchQuoteProvider(QuoteProvider):
    """Batch-first Eastmoney provider for all-A quote snapshots.

    ``transport`` is injectable and receives the URL plus normal ``requests``
    keyword arguments.  Tests can consequently use a deterministic fixture and
    never touch the public endpoint.
    """

    name = "eastmoney_batch"
    endpoint = EASTMONEY_QUOTE_URL

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        transport: Callable[..., Any] | None = None,
        request: Callable[..., Any] | None = None,
        timeout: float = 10.0,
        page_size: int = 5000,
        max_pages: int = 20,
        market_filter: str = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        min_interval_seconds: float = 0.0,
    ) -> None:
        self.session = session or requests.Session()
        self.transport = transport or request
        self.timeout = float(timeout)
        self.page_size = max(1, min(int(page_size), 5000))
        self.max_pages = max(1, int(max_pages))
        self.market_filter = market_filter
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._last_request = 0.0
        self._lock = Lock()

    def _get(self, params: dict[str, Any]) -> Mapping[str, Any]:
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if self.min_interval_seconds and elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            request = self.transport or self.session.get
            response = request(
                EASTMONEY_QUOTE_URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
                timeout=self.timeout,
            )
            self._last_request = time.monotonic()
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        return _payload(response)

    @staticmethod
    def _base_params(page: int, page_size: int, market_filter: str) -> dict[str, Any]:
        return {
            "pn": page,
            "pz": page_size,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": market_filter,
            "fields": "f12,f14,f43,f60,f46,f44,f45,f47,f48,f170,f86",
        }

    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        normalized = list(dict.fromkeys(normalize_security_code(code) for code in codes if normalize_security_code(code)))
        if not normalized:
            return {}
        wanted = set(normalized)
        results: dict[str, NormalizedQuote] = {}
        fetched_at = datetime.now(UTC)
        total: int | None = None
        for page in range(1, self.max_pages + 1):
            payload = self._get(self._base_params(page, self.page_size, self.market_filter))
            rows, page_total = _rows(payload)
            if page_total is not None:
                total = page_total
            if not rows:
                break
            for row in rows:
                quote = parse_eastmoney_row(row, fetched_at=fetched_at, provider=self.name)
                if quote is None or (wanted and quote.code not in wanted):
                    continue
                results.setdefault(quote.code, quote)
            if wanted and wanted.issubset(results):
                break
            if total is not None and page * self.page_size >= total:
                break
            if len(rows) < self.page_size and total is None:
                break

        # Returning explicit missing records keeps all-fail diagnostics visible
        # to the snapshot builder and lets the fallback provider fill gaps.
        for code in normalized:
            if code not in results:
                results[code] = NormalizedQuote(
                    code=code,
                    exchange=exchange_for_code(code),
                    provider=self.name,
                    fetched_at=fetched_at,
                    quality_status=DataQualityStatus.MISSING,
                    raw_reference=EASTMONEY_QUOTE_URL,
                    errors=["quote_missing"],
                )
        return results

    def get_all_a_share_quotes(self, universe: Iterable[str]) -> dict[str, NormalizedQuote]:
        return self.get_quotes(universe)


# A concise compatibility spelling for callers that do not need the full name.
EastmoneyQuoteProvider = EastmoneyBatchQuoteProvider


__all__ = [
    "EASTMONEY_QUOTE_URL",
    "EastmoneyBatchQuoteProvider",
    "EastmoneyQuoteProvider",
    "parse_eastmoney_row",
]
