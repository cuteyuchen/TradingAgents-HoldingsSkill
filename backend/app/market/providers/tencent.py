"""Tencent ``qt.gtimg.cn`` quote adapter.

The adapter owns the Tencent wire format and exposes only NormalizedQuote to
business code.  It is batch-first and is also the compatibility provider used
for holdings and small all-A fixtures when no specialized bulk provider is
configured.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import requests

from ..codes import normalize_security_code, exchange_for_code, provider_symbol
from ..models import DataQualityStatus, NormalizedQuote
from ..quality import validate_normalized_quote
from .base import QuoteProvider


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q"


def _float(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        parsed = float(str(value).replace(",", ""))
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def decode_tencent(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def tencent_symbol(code: str) -> str:
    return provider_symbol(code, "tencent")


def parse_tencent_line(line: str, *, fetched_at: datetime | None = None) -> NormalizedQuote | None:
    if '="' not in line:
        return None
    raw = line.split('="', 1)[1].rstrip('";\r\n')
    fields = raw.split("~")
    if len(fields) < 38:
        return None
    code = normalize_security_code(fields[2])
    if not code:
        return None
    quote_time = fields[30] or None
    source_timestamp: datetime | str | None = quote_time
    if quote_time and len(quote_time) == 8 and fetched_at is not None:
        try:
            source_timestamp = datetime.combine(
                fetched_at.astimezone(CHINA_TZ).date(),
                datetime.strptime(quote_time, "%H:%M:%S").time(),
                tzinfo=CHINA_TZ,
            )
        except ValueError:
            source_timestamp = quote_time
    quote = NormalizedQuote(
        market="CN",
        exchange=exchange_for_code(code),
        code=code,
        name=fields[1] or None,
        price=_float(fields[3]),
        prev_close=_float(fields[4]),
        open=_float(fields[5]),
        pct_change=_float(fields[32]),
        high=_float(fields[33]),
        low=_float(fields[34]),
        volume=_float(fields[36]),
        amount=_float(fields[37]),
        source_timestamp=source_timestamp,
        provider="tencent",
        fetched_at=fetched_at or datetime.now().astimezone(),
        quality_status=DataQualityStatus.VALID,
        raw_reference=TENCENT_QUOTE_URL,
    )
    validation = validate_normalized_quote(quote, now=quote.fetched_at, max_age_seconds=None)
    quote.quality_status = validation.status
    quote.errors.extend(error for error in validation.errors if error not in quote.errors)
    return quote


class TencentQuoteProvider(QuoteProvider):
    name = "tencent"
    endpoint = TENCENT_QUOTE_URL

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        request: Callable[..., Any] | None = None,
        batch_size: int = 400,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self._request = request
        self.batch_size = max(1, int(batch_size))

    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        normalized = list(dict.fromkeys(normalize_security_code(code) for code in codes if normalize_security_code(code)))
        if not normalized:
            return {}
        request = self._request or self.session.get
        results: dict[str, NormalizedQuote] = {}
        fetched_at = datetime.now().astimezone()
        for offset in range(0, len(normalized), self.batch_size):
            batch = normalized[offset : offset + self.batch_size]
            symbols = ",".join(tencent_symbol(code) for code in batch)
            response = request(
                TENCENT_QUOTE_URL + "=" + symbols,
                headers={"User-Agent": USER_AGENT, "Referer": "https://finance.qq.com/"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            text = decode_tencent(response.content)
            # Tencent may concatenate assignments without newlines.  The
            # terminating quote-semicolon pair is the stable record boundary.
            for record in text.split('";'):
                line = record.strip()
                if not line:
                    continue
                quote = parse_tencent_line(line + '";', fetched_at=fetched_at)
                if quote:
                    results[quote.code] = quote
        for code in set(normalized) - set(results):
            results[code] = NormalizedQuote(
                code=code,
                exchange=exchange_for_code(code),
                provider=self.name,
                fetched_at=fetched_at,
                quality_status=DataQualityStatus.MISSING,
                raw_reference=TENCENT_QUOTE_URL,
                errors=["quote_missing"],
            )
        return results


__all__ = [
    "TENCENT_QUOTE_URL",
    "TencentQuoteProvider",
    "decode_tencent",
    "parse_tencent_line",
    "tencent_symbol",
]
