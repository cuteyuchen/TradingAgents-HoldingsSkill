"""Adapters from existing providers to instrument facts, without public raw payloads."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from typing import Any

from ...clock import utc_now
from ...config import settings
from ..instrument_schemas import InstrumentCapabilities, InstrumentIdentity
from ..models import NormalizedQuote, _coerce_date, _coerce_datetime, _coerce_float
from .factory import build_critical_quote_provider, build_kline_provider, create_quote_provider
from .fuyao import FuyaoKLineProvider


@dataclass
class InstrumentProviderResult:
    data: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    trading_date: date | None = None
    status: str = "available"
    fallback: bool = False
    quality_flags: list[str] = field(default_factory=list)
    error_code: str | None = None


def _scaled(value: Any, multiplier: float | None) -> float | None:
    value = _coerce_float(value)
    return _coerce_float(value * multiplier) if value is not None and multiplier is not None else None


def _shares(value: Any, unit: str, instrument: InstrumentIdentity) -> float | None:
    multiplier = 1 if unit == "shares" else instrument.lot_size if unit == "lots" else None
    return _scaled(value, multiplier)


class InstrumentDataAdapters:
    """Reuse the configured quote/history chains; Tencent book and Eastmoney flow."""

    def __init__(
        self, *, quote_provider=None, history_provider=None, order_book_provider=None,
        capital_flow_fetcher: Callable[..., Mapping[str, Any]] | None = None,
        etf_capital_flow: bool = False,
    ) -> None:
        self.quote_provider = quote_provider or build_critical_quote_provider()
        self.history_provider = history_provider or build_kline_provider()
        self.order_book_provider = order_book_provider
        self.capital_flow_fetcher = capital_flow_fetcher
        self.etf_capital_flow = etf_capital_flow

    @property
    def profile(self) -> str:
        # A deployment/profile change must not reuse quotes from a different chain.
        # Only a digest is exposed, never an endpoint, credential or raw config.
        values = {
            "quote": [settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER, *settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS],
            "history": settings.HISTORICAL_KLINE_PROVIDER,
            "endpoint": settings.FUYAO_BASE_URL,
            "acceptance": settings.ACCEPTANCE_MODE,
            "etf_flow": self.etf_capital_flow,
        }
        return "instrument-v1-" + sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]

    def _history_sources(self) -> list:
        return getattr(self.history_provider, "providers", [self.history_provider])

    @staticmethod
    def _supports_adjustment(provider, instrument: InstrumentIdentity, adjustment: str) -> bool:
        if instrument.instrument_type == "INDEX":
            return adjustment == "none"
        if isinstance(provider, FuyaoKLineProvider) and instrument.instrument_type == "ETF":
            return adjustment == "none"
        supported = getattr(provider, "supported_adjustments", ("none", "forward", "backward"))
        return adjustment in supported

    def capabilities(self, instrument: InstrumentIdentity) -> InstrumentCapabilities:
        return InstrumentCapabilities(
            order_book=instrument.instrument_type != "INDEX",
            capital_flow=instrument.instrument_type == "STOCK" or (
                instrument.instrument_type == "ETF" and self.etf_capital_flow
            ),
            fundamentals=instrument.instrument_type == "STOCK",
            etf_profile=instrument.instrument_type == "ETF",
            adjustments=[
                adjustment for adjustment in ("none", "forward", "backward")
                if any(self._supports_adjustment(provider, instrument, adjustment) for provider in self._history_sources())
            ],
        )

    def quotes(self, instruments: Iterable[InstrumentIdentity]) -> dict[str, NormalizedQuote]:
        identities = {item.code: item for item in instruments}
        batch = self.quote_provider.get_instrument_quotes(
            identities, instrument_types={code: item.instrument_type for code, item in identities.items()},
        )
        result = {}
        for code, instrument in identities.items():
            raw = batch.get(code)
            if not isinstance(raw, NormalizedQuote) or raw.symbol != code:
                continue
            quote = deepcopy(raw)
            metadata = quote.metadata
            flags = []
            try:
                if instrument.instrument_type == "INDEX":
                    quote.volume = quote.amount = None
                    flags.append("INDEX_VOLUME_SEMANTICS_UNKNOWN")
                else:
                    quote.volume = _shares(quote.volume, metadata.get("volume_unit", "shares"), instrument)
                    quote.amount = _scaled(
                        quote.amount, {"CNY": 1, "10k_CNY": 10000}.get(metadata.get("turnover_unit", "CNY")),
                    )
                    if metadata.get("volume_unit") == "lots" and not instrument.lot_size:
                        flags.append("VOLUME_UNIT_UNKNOWN")
                if metadata.get("observed_date_inferred"):
                    quote.source_timestamp = None
                    quote.trade_date = None
            except (TypeError, ValueError, AttributeError, OverflowError):
                continue
            quote.metadata = {"facade_flags": flags}
            result[code] = quote
        return result

    def bars(
        self, instrument: InstrumentIdentity, *, start: date, end: date, adjustment: str,
    ) -> InstrumentProviderResult:
        if settings.ACCEPTANCE_MODE:
            return InstrumentProviderResult(
                data={"bars": [
                    {"time": day, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100000, "turnover": 1050000}
                    for offset in range((end - start).days + 1)
                    if (day := start + timedelta(days=offset)).weekday() < 5
                ]},
                provider="acceptance", fetched_at=utc_now(), observed_at=utc_now(),
            )
        attempted = False
        empty = False
        had_failure = False
        for level, provider in enumerate(self._history_sources()):
            if not self._supports_adjustment(provider, instrument, adjustment):
                continue
            attempted = True
            try:
                rows = provider.get_historical(
                    instrument.code, start=start, end=end, adjustment=adjustment,
                    instrument_type=instrument.instrument_type,
                )
                if not rows:
                    if getattr(provider, "last_errors", []):
                        had_failure = True
                    else:
                        empty = True
                    continue
                output = []
                observed = []
                fetched = []
                for row in rows:
                    actual_adjustment = str(row.get("adjustment", adjustment)).lower()
                    actual_adjustment = {"qfq": "forward", "hfq": "backward", "raw": "none"}.get(actual_adjustment, actual_adjustment)
                    if actual_adjustment != adjustment:
                        raise ValueError("bars_adjustment_mismatch")
                    if row.get("provider") not in (None, "", provider.name):
                        raise ValueError("mixed_bar_sources")
                    metadata = row.get("metadata") or {}
                    stamp = _coerce_datetime(metadata.get("source_timestamp"))
                    if stamp is not None:
                        observed.append(stamp)
                    stamp = _coerce_datetime(row.get("fetched_at"))
                    if stamp is not None:
                        fetched.append(stamp)
                    output.append({
                        "time": row.get("trade_date", row.get("date")),
                        **{key: row.get(key) for key in ("open", "high", "low", "close")},
                        "volume": _shares(row.get("volume"), metadata.get("volume_unit", "shares"), instrument)
                        if instrument.instrument_type != "INDEX" else None,
                        "turnover": _scaled(row.get("amount"), 1) if instrument.instrument_type != "INDEX" else None,
                    })
                return InstrumentProviderResult(
                    data={"bars": output}, provider=str(provider.name),
                    observed_at=min(observed) if observed else None,
                    fetched_at=max(fetched) if fetched else utc_now(),
                    fallback=level > 0,
                    quality_flags=["INDEX_VOLUME_SEMANTICS_UNKNOWN"] if instrument.instrument_type == "INDEX" else [],
                )
            except Exception:
                had_failure = True
                continue
        empty = empty and not had_failure
        return InstrumentProviderResult(
            status="empty" if empty else "unavailable" if attempted else "unsupported",
            fetched_at=utc_now() if attempted else None,
            error_code="BARS_EMPTY" if empty else "BARS_PROVIDER_FAILED" if attempted else "ADJUSTMENT_UNSUPPORTED",
        )

    def order_book(self, instrument: InstrumentIdentity) -> InstrumentProviderResult:
        provider = self.order_book_provider or create_quote_provider("tencent")
        batch = provider.get_instrument_quotes([instrument.code])
        quote = batch.get(instrument.code)
        if quote is None or quote.price is None:
            return InstrumentProviderResult(status="unavailable", error_code="ORDER_BOOK_PROVIDER_FAILED", fetched_at=utc_now())
        book = quote.metadata.get("order_book") or {}
        unit = book.get("volume_unit", "shares")
        return InstrumentProviderResult(
            data={
                side: [
                    {"price": level.get("price"), "volume": _shares(level.get("volume"), unit, instrument)}
                    for level in book.get(side, [])
                ]
                for side in ("bids", "asks")
            } | {
                "inner_volume": _shares(book.get("inner_volume"), unit, instrument),
                "outer_volume": _shares(book.get("outer_volume"), unit, instrument),
                "order_ratio": book.get("order_ratio"),
                "order_difference": _shares(book.get("order_difference"), unit, instrument),
            },
            provider=quote.provider, observed_at=quote.source_timestamp,
            fetched_at=quote.fetched_at, trading_date=quote.trade_date,
            fallback=quote.fallback_level > 0,
            quality_flags=["VOLUME_UNIT_UNKNOWN"] if unit == "lots" and not instrument.lot_size else [],
        ) if not quote.metadata.get("observed_date_inferred") else InstrumentProviderResult(
            status="unavailable", provider=quote.provider, fetched_at=quote.fetched_at,
            error_code="ORDER_BOOK_OBSERVED_DATE_MISSING",
        )

    def capital_flow(self, instrument: InstrumentIdentity) -> InstrumentProviderResult:
        if settings.ACCEPTANCE_MODE:
            return InstrumentProviderResult(
                data={"current": {
                    "main_net_inflow": 1200000, "super_large_net_inflow": 500000,
                    "large_net_inflow": 700000, "medium_net_inflow": -200000, "small_net_inflow": -1000000,
                }, "history": []},
                provider="acceptance", fetched_at=utc_now(), observed_at=utc_now(),
                trading_date=_coerce_date(settings.ACCEPTANCE_TRADE_DATE),
            )
        if self.capital_flow_fetcher is None:
            from ...services.market_data import fetch_fund_flow

            fetcher = fetch_fund_flow
        else:
            fetcher = self.capital_flow_fetcher
        payload = fetcher(instrument.code, limit=20, daily=True)
        fetched_at = utc_now()
        if payload.get("error"):
            return InstrumentProviderResult(status="unavailable", fetched_at=fetched_at, error_code="CAPITAL_FLOW_PROVIDER_FAILED")
        fields = {
            "main_net_inflow": "main_net", "super_large_net_inflow": "super_large_net",
            "large_net_inflow": "large_net", "medium_net_inflow": "medium_net",
            "small_net_inflow": "small_net",
        }
        return InstrumentProviderResult(
            data={
                "current": {key: _coerce_float(payload.get(raw)) for key, raw in fields.items()},
                "history": [
                    {"time": row.get("date"), **{key: _coerce_float(row.get(raw)) for key, raw in fields.items()}}
                    for row in payload.get("history", [])
                ],
            },
            provider="eastmoney", fetched_at=fetched_at,
            trading_date=_coerce_date(payload.get("date")),
        )
