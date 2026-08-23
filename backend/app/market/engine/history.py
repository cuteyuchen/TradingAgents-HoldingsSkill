"""Provider-neutral historical daily bars and history access.

Only this module is allowed to translate legacy K-line payloads into the
``NormalizedDailyBar`` contract.  The scoring functions consume this contract
and never inspect vendor ``fXX`` fields.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from math import isfinite
from typing import Any, Iterable, Mapping, Protocol

from ..codes import exchange_for_code, normalize_security_code


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).replace("/", "-")[:10])
    except ValueError:
        return None


def _datetime(value: Any, *, default: datetime | None = None) -> datetime | None:
    if value is None or value == "":
        return default
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return default
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


@dataclass(slots=True)
class NormalizedDailyBar:
    """One provider-neutral, point-in-time daily OHLCV record.

    ``adjustment`` is ``QFQ`` by default because all MA and new-high/new-low
    calculations in Phase C use one forward-adjusted price series.
    Amount is always expressed in CNY yuan at the adapter boundary.
    """

    code: str
    trade_date: date
    market: str = "CN"
    exchange: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None
    adjustment: str = "QFQ"
    provider: str = ""
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    available_at: datetime | None = None
    quality_status: str = "VALID"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = normalize_security_code(self.code)
        self.exchange = str(self.exchange or exchange_for_code(self.code) or "").upper() or None
        self.market = str(self.market or "CN").upper()
        self.trade_date = _date(self.trade_date) or date.min
        self.adjustment = str(self.adjustment or "QFQ").upper()
        self.provider = str(self.provider or "").lower()
        self.fetched_at = _datetime(self.fetched_at, default=datetime.now(UTC)) or datetime.now(UTC)
        self.available_at = _datetime(self.available_at, default=self.fetched_at)
        self.quality_status = str(getattr(self.quality_status, "value", self.quality_status) or "VALID").upper()
        for name in ("open", "high", "low", "close", "prev_close", "volume", "amount", "turnover_rate"):
            setattr(self, name, _number(getattr(self, name)))
        self.metadata = dict(self.metadata or {})

    @property
    def return_ratio(self) -> float | None:
        if self.prev_close is None or self.prev_close <= 0 or self.close is None:
            return None
        return self.close / self.prev_close - 1.0

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        code: str | None = None,
        provider: str | None = None,
        adjustment: str = "QFQ",
        fetched_at: datetime | None = None,
    ) -> "NormalizedDailyBar":
        data = dict(value)
        resolved_code = normalize_security_code(data.get("code") or data.get("symbol") or code or "")
        trade_date = data.get("trade_date") or data.get("date")
        close = data.get("close", data.get("price"))
        return cls(
            code=resolved_code,
            trade_date=_date(trade_date) or date.min,
            market=data.get("market", "CN"),
            exchange=data.get("exchange"),
            open=data.get("open"),
            high=data.get("high"),
            low=data.get("low"),
            close=close,
            prev_close=data.get("prev_close") or data.get("previous_close"),
            volume=data.get("volume"),
            amount=data.get("amount", data.get("turnover")),
            turnover_rate=data.get("turnover_rate"),
            adjustment=data.get("adjustment", adjustment),
            provider=data.get("provider", provider or ""),
            fetched_at=data.get("fetched_at", fetched_at or datetime.now(UTC)),
            available_at=data.get("available_at"),
            quality_status=data.get("quality_status", data.get("quality", "VALID")),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trade_date"] = self.trade_date.isoformat()
        data["fetched_at"] = self.fetched_at.isoformat()
        data["available_at"] = self.available_at.isoformat() if self.available_at else None
        data["return_ratio"] = self.return_ratio
        return data


class KLineProviderProtocol(Protocol):
    def get_kline(self, code: str, *, limit: int = 30) -> list[dict[str, Any]]: ...


class MarketHistoryAccessLayer:
    """Normalize K-line provider output and enforce as-of/look-ahead bounds."""

    def __init__(self, provider: KLineProviderProtocol | Any, *, adjustment: str = "QFQ") -> None:
        self.provider = provider
        self.adjustment = adjustment.upper()

    def get_bars(
        self,
        codes: Iterable[str],
        *,
        limit: int = 260,
        as_of: date | datetime | str | None = None,
        available_at: datetime | str | None = None,
    ) -> list[NormalizedDailyBar]:
        cutoff_date = _date(as_of)
        cutoff_available = _datetime(available_at)
        output: list[NormalizedDailyBar] = []
        for raw_code in dict.fromkeys(normalize_security_code(code) for code in codes if normalize_security_code(code)):
            rows = self.provider.get_kline(raw_code, limit=limit) or []
            previous: float | None = None
            normalized: list[NormalizedDailyBar] = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                bar = NormalizedDailyBar.from_mapping(
                    row,
                    code=raw_code,
                    provider=getattr(self.provider, "name", None) or row.get("provider", ""),
                    adjustment=self.adjustment,
                )
                if bar.prev_close is None:
                    bar.prev_close = previous
                previous = bar.close
                if cutoff_date is not None and bar.trade_date > cutoff_date:
                    continue
                if cutoff_available is not None and bar.available_at and bar.available_at > cutoff_available:
                    continue
                if bar.quality_status not in {"VALID", "DEGRADED"}:
                    continue
                normalized.append(bar)
            output.extend(normalized)
        output.sort(key=lambda item: (item.trade_date, item.code))
        return output


class LegacyMarketDataHistoryProvider:
    """Adapter around the existing Eastmoney daily K-line helper."""

    name = "eastmoney_daily_qfq"

    def __init__(self, fetcher: Any | None = None) -> None:
        if fetcher is None:
            from ...services.market_data import fetch_kline

            fetcher = fetch_kline
        self.fetcher = fetcher

    def get_kline(self, code: str, *, limit: int = 30) -> list[dict[str, Any]]:
        payload = self.fetcher(code, limit=limit) or {}
        return list(payload.get("rows") or []) if isinstance(payload, Mapping) else []


__all__ = ["NormalizedDailyBar", "MarketHistoryAccessLayer", "LegacyMarketDataHistoryProvider"]
