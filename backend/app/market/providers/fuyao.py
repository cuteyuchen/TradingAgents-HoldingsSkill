"""Fuyao adapters for canonical market, identity, and enrichment data."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
import math
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..codes import exchange_for_code, normalize_security_code
from ..models import DataQualityStatus, NormalizedQuote
from .base import CalendarProvider, KLineProvider, QuoteProvider, SecurityProvider
from .fuyao_client import FuyaoAPIError, FuyaoClient, FuyaoResponse, client_from_settings


FUYAO_QUOTE_ENDPOINT = "/api/a-share/prices/snapshot"
FUYAO_HISTORICAL_ENDPOINT = "/api/a-share/prices/historical"
FUYAO_TICKER_SEARCH_ENDPOINT = "/api/meta/tickers/search"
FUYAO_TICKER_LIST_ENDPOINT = "/api/meta/tickers/list"
FUYAO_CALENDAR_ENDPOINT = "/api/a-share/calendar/trading-days"
FUYAO_CORPORATE_ACTIONS_ENDPOINT = "/api/a-share/corporate-actions/adjustment-factors"
FUYAO_VALUATION_ENDPOINT = "/api/a-share/valuations/snapshot"
FUYAO_INCOME_ENDPOINT = "/api/a-share/financials/income-statements"
FUYAO_BALANCE_ENDPOINT = "/api/a-share/financials/balance-sheets"
FUYAO_CASH_FLOW_ENDPOINT = "/api/a-share/financials/cash-flow-statements"
FUYAO_INDICATORS_ENDPOINT = "/api/a-share/financials/indicators"
FUYAO_INDEX_CATALOG_ENDPOINT = "/api/a-share-index/catalog/ths-index-list"
FUYAO_INDEX_CONSTITUENTS_ENDPOINT = "/api/a-share-index/constituents/ths-stock-list"
FUYAO_INDEX_SNAPSHOT_ENDPOINT = "/api/a-share-index/prices/snapshot"
FUYAO_INDEX_HISTORICAL_ENDPOINT = "/api/a-share-index/prices/historical"
FUYAO_FUND_SNAPSHOT_ENDPOINT = "/api/fund/market/snapshot"
FUYAO_FUND_HISTORICAL_ENDPOINT = "/api/fund/market/historical"
FUYAO_SPECIAL_DATA_PREFIX = "/api/a-share/special-data/"

CHINA_TZ = ZoneInfo("Asia/Shanghai")
_EXCHANGE_SUFFIX = {"SSE": "SH", "SH": "SH", "SZSE": "SZ", "SZ": "SZ", "BSE": "BJ", "BJ": "BJ"}
_ASSET_TYPES = {"a-share": "STOCK", "fund-etf": "ETF"}


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "null"):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp(value: Any) -> datetime | None:
    if value in (None, "", "-"):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=CHINA_TZ)
    try:
        number = int(str(value))
        # Fuyao's documented timestamps are Unix milliseconds.
        return datetime.fromtimestamp(number / 1000, tz=CHINA_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _full_thscode(code: str, exchange: str | None = None) -> str:
    normalized = normalize_security_code(code)
    if not normalized:
        return ""
    resolved = str(exchange or exchange_for_code(normalized) or "").upper()
    suffix = _EXCHANGE_SUFFIX.get(resolved)
    return f"{normalized}.{suffix}" if suffix else normalized


def _rows(data: Any) -> list[Mapping[str, Any]]:
    if not isinstance(data, Mapping):
        return []
    raw = data.get("item")
    if raw is None:
        raw = data.get("items") or data.get("rows") or data.get("list")
    if isinstance(raw, Mapping):
        raw = list(raw.values())
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _safe_error(error: Exception, *, provider: str, endpoint: str) -> dict[str, Any]:
    if isinstance(error, FuyaoAPIError):
        payload = error.to_dict()
        payload["provider"] = provider
        return payload
    return {
        "provider": provider,
        "endpoint": endpoint,
        "category": "UPSTREAM_FAILURE",
        "message": error.__class__.__name__.lower(),
    }


def _quality_for_quote(price: float | None) -> tuple[DataQualityStatus, list[str]]:
    return (
        (DataQualityStatus.VALID, [])
        if price is not None
        else (DataQualityStatus.MISSING, ["quote_missing"])
    )


def parse_fuyao_quote(
    row: Mapping[str, Any],
    *,
    response_timestamp: Any = None,
    fetched_at: datetime | None = None,
    request_id: str | None = None,
    endpoint: str = FUYAO_QUOTE_ENDPOINT,
    provider: str = "fuyao",
) -> NormalizedQuote | None:
    """Translate one documented ``PriceSnapshotItem``."""

    raw_code = row.get("thscode") or row.get("ticker") or row.get("code")
    code = normalize_security_code(raw_code)
    if not code:
        return None
    source_timestamp = _timestamp(response_timestamp or row.get("timestamp"))
    fetched = fetched_at or datetime.now(UTC)
    price = _number(row.get("last_price"))
    quality, errors = _quality_for_quote(price)
    asset_type = str(row.get("asset_type") or "").strip().lower()
    metadata: dict[str, Any] = {
        "endpoint": endpoint,
        "request_id": request_id,
        "thscode": str(raw_code) if raw_code else _full_thscode(code),
    }
    if response_timestamp is not None:
        metadata["source_timestamp_ms"] = response_timestamp
    return NormalizedQuote(
        code=code,
        market="CN",
        exchange=exchange_for_code(code),
        name=str(row.get("name") or "").strip() or None,
        security_type=_ASSET_TYPES.get(asset_type),
        price=price,
        prev_close=_number(row.get("prev_price")),
        open=_number(row.get("open_price")),
        high=_number(row.get("high_price")),
        low=_number(row.get("low_price")),
        pct_change=_number(row.get("price_change_ratio_pct")),
        volume=_number(row.get("volume")),
        amount=_number(row.get("turnover")),
        trade_date=source_timestamp.astimezone(CHINA_TZ).date() if source_timestamp else None,
        source_timestamp=source_timestamp,
        provider=provider,
        fetched_at=fetched,
        quality_status=quality,
        raw_reference=endpoint,
        errors=errors,
        metadata=metadata,
    )


class FuyaoQuoteProvider(QuoteProvider):
    """Fuyao A-share snapshot adapter with explicit batch and full paging paths."""

    name = "fuyao"
    endpoint = FUYAO_QUOTE_ENDPOINT

    def __init__(
        self,
        *,
        client: FuyaoClient | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        max_retries: int | None = None,
        min_interval_seconds: float | None = None,
        session: Any | None = None,
        transport: Any | None = None,
        request: Any | None = None,
        timeout: float | None = None,
        batch_size: int = 100,
        page_size: int = 100,
        max_pages: int = 200,
        full_market_threshold: int = 200,
    ) -> None:
        client_options: dict[str, Any] = {}
        if base_url is not None:
            client_options["base_url"] = base_url
        if api_key is not None:
            client_options["api_key"] = api_key
        if connect_timeout is not None:
            client_options["connect_timeout"] = connect_timeout
        if read_timeout is not None:
            client_options["read_timeout"] = read_timeout
        if timeout is not None:
            client_options.setdefault("connect_timeout", timeout)
            client_options.setdefault("read_timeout", timeout)
        if max_retries is not None:
            client_options["max_retries"] = max_retries
        if min_interval_seconds is not None:
            client_options["min_interval_seconds"] = min_interval_seconds
        if session is not None:
            client_options["session"] = session
        if transport is not None:
            client_options["transport"] = transport
        elif request is not None:
            client_options["transport"] = request
        self.client = client or client_from_settings(**client_options)
        self.batch_size = max(1, int(batch_size))
        self.page_size = max(1, int(page_size))
        self.max_pages = max(1, int(max_pages))
        self.full_market_threshold = max(1, int(full_market_threshold))
        self.last_errors: list[dict[str, Any]] = []
        self.last_responses: list[dict[str, Any]] = []

    def _missing(self, code: str, *, fetched_at: datetime) -> NormalizedQuote:
        return NormalizedQuote(
            code=code,
            exchange=exchange_for_code(code),
            provider=self.name,
            fetched_at=fetched_at,
            quality_status=DataQualityStatus.MISSING,
            raw_reference=self.endpoint,
            errors=["quote_missing"],
        )

    def _parse_response(
        self,
        response: FuyaoResponse,
        *,
        fetched_at: datetime,
        wanted: set[str] | None = None,
    ) -> dict[str, NormalizedQuote]:
        data = response.data if isinstance(response.data, Mapping) else {}
        result: dict[str, NormalizedQuote] = {}
        for row in _rows(data):
            quote = parse_fuyao_quote(
                row,
                response_timestamp=data.get("timestamp"),
                fetched_at=fetched_at,
                request_id=response.request_id,
                endpoint=response.endpoint,
                provider=self.name,
            )
            if quote is None or (wanted is not None and quote.code not in wanted):
                continue
            result.setdefault(quote.code, quote)
        self.last_responses.append(
            {
                "endpoint": response.endpoint,
                "request_id": response.request_id,
                "latency_ms": response.latency_ms,
                "attempts": response.attempts,
                "received_count": len(result),
            }
        )
        return result

    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        normalized = list(
            dict.fromkeys(
                code for value in codes if (code := normalize_security_code(value))
            )
        )
        if not normalized:
            return {}
        fetched_at = datetime.now(UTC)
        self.last_errors = []
        self.last_responses = []
        result: dict[str, NormalizedQuote] = {}
        for offset in range(0, len(normalized), self.batch_size):
            batch = normalized[offset : offset + self.batch_size]
            params = {"thscodes": ",".join(_full_thscode(code) for code in batch)}
            try:
                response = self.client.get(self.endpoint, params=params, capability="quotes")
                result.update(self._parse_response(response, fetched_at=fetched_at, wanted=set(batch)))
            except Exception as exc:  # fallback layer decides whether to continue
                self.last_errors.append(_safe_error(exc, provider=self.name, endpoint=self.endpoint))
        for code in normalized:
            result.setdefault(code, self._missing(code, fetched_at=fetched_at))
        if not result:
            raise RuntimeError("fuyao_quote_batch_empty")
        return result

    def get_all_a_share_quotes(self, universe: Iterable[str]) -> dict[str, NormalizedQuote]:
        normalized = list(
            dict.fromkeys(
                code for value in universe if (code := normalize_security_code(value))
            )
        )
        if len(normalized) <= self.full_market_threshold:
            return self.get_quotes(normalized)
        if not normalized:
            return {}
        fetched_at = datetime.now(UTC)
        self.last_errors = []
        self.last_responses = []
        wanted = set(normalized)
        result: dict[str, NormalizedQuote] = {}
        total: int | None = None
        for page in range(self.max_pages):
            offset = page * self.page_size
            try:
                response = self.client.get(
                    self.endpoint,
                    params={"limit": self.page_size, "offset": offset},
                    capability="quotes",
                )
            except Exception as exc:
                self.last_errors.append(_safe_error(exc, provider=self.name, endpoint=self.endpoint))
                break
            data = response.data if isinstance(response.data, Mapping) else {}
            try:
                total = int(data.get("total")) if data.get("total") is not None else total
            except (TypeError, ValueError):
                pass
            page_result = self._parse_response(response, fetched_at=fetched_at, wanted=wanted)
            result.update(page_result)
            page_rows = _rows(data)
            if wanted.issubset(result):
                break
            if not page_rows:
                break
            if total is not None and offset + self.page_size >= total:
                break
            if len(page_rows) < self.page_size and total is None:
                break
        for code in normalized:
            result.setdefault(code, self._missing(code, fetched_at=fetched_at))
        return result

    def get_run_metadata(self) -> dict[str, Any]:
        request_ids = [
            str(item.get("request_id"))
            for item in self.last_responses
            if item.get("request_id")
        ]
        received_count = sum(int(item.get("received_count") or 0) for item in self.last_responses)
        return {
            "provider": self.name,
            "provider_counts": {self.name: received_count} if received_count else {},
            "provider_endpoints": {self.name: self.endpoint} if self.last_responses else {},
            "provider_request_ids": {self.name: list(dict.fromkeys(request_ids))} if request_ids else {},
            "provider_attempts": [
                {
                    "provider": self.name,
                    "fallback_level": 0,
                    "endpoint": item.get("endpoint"),
                    "request_id": item.get("request_id"),
                    "status": "success" if item.get("received_count") else "unusable",
                    "latency_ms": item.get("latency_ms"),
                    "contribution_count": item.get("received_count", 0),
                }
                for item in self.last_responses
            ],
            "fallback_level": 0,
            "fallback_errors": list(self.last_errors),
        }


class FuyaoKLineProvider(KLineProvider):
    """Historical daily K-line adapter with local availability lineage."""

    name = "fuyao_historical"
    endpoint = FUYAO_HISTORICAL_ENDPOINT

    def __init__(self, *, client: FuyaoClient | None = None, now: Callable[[], datetime] | None = None) -> None:
        self.client = client or client_from_settings()
        self.now = now or (lambda: datetime.now(CHINA_TZ))
        self.last_errors: list[dict[str, Any]] = []

    @staticmethod
    def _adjustment(value: str) -> str:
        normalized = str(value or "QFQ").upper()
        return {"QFQ": "forward", "HFQ": "backward", "NONE": "none", "RAW": "none"}.get(normalized, "forward")

    def get_historical(
        self,
        code: str,
        *,
        start: date,
        end: date,
        adjustment: str = "QFQ",
    ) -> list[dict[str, Any]]:
        normalized = normalize_security_code(code)
        if not normalized:
            return []
        fetched_at = self.now()
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=CHINA_TZ)
        self.last_errors = []
        try:
            response = self.client.get(
                self.endpoint,
                params={
                    "thscode": _full_thscode(normalized),
                    "interval": "1d",
                    "start": int(datetime.combine(start, datetime.min.time(), tzinfo=CHINA_TZ).timestamp() * 1000),
                    "end": int(datetime.combine(end, datetime.max.time(), tzinfo=CHINA_TZ).timestamp() * 1000),
                    "adjust": self._adjustment(adjustment),
                },
                capability="historical",
            )
        except Exception as exc:
            self.last_errors.append(_safe_error(exc, provider=self.name, endpoint=self.endpoint))
            return []
        data = response.data if isinstance(response.data, Mapping) else {}
        source_timestamp = _timestamp(data.get("timestamp"))
        rows: list[dict[str, Any]] = []
        previous_close: float | None = None
        for raw in sorted(_rows(data), key=lambda item: int(item.get("date_ms") or 0)):
            day_timestamp = _timestamp(raw.get("date_ms"))
            if day_timestamp is None:
                continue
            trade_day = day_timestamp.astimezone(CHINA_TZ).date()
            if trade_day < start or trade_day > end:
                continue
            close = _number(raw.get("close_price"))
            row = {
                "code": normalized,
                "market": "CN",
                "exchange": exchange_for_code(normalized),
                "trade_date": trade_day,
                "open": _number(raw.get("open_price")),
                "high": _number(raw.get("high_price")),
                "low": _number(raw.get("low_price")),
                "close": close,
                "prev_close": previous_close,
                "volume": _number(raw.get("volume")),
                "amount": _number(raw.get("turnover")),
                "adjustment": str(adjustment or "QFQ").upper(),
                "provider": self.name,
                "fetched_at": fetched_at,
                # Historical daily prices become usable at the session close;
                # the API does not provide a separate publication timestamp.
                "available_at": datetime(
                    trade_day.year,
                    trade_day.month,
                    trade_day.day,
                    15,
                    tzinfo=CHINA_TZ,
                ),
                "quality_status": "VALID" if close is not None else "MISSING",
                "metadata": {
                    "endpoint": response.endpoint,
                    "request_id": response.request_id,
                    "source_timestamp": source_timestamp.isoformat() if source_timestamp else None,
                    "source_timestamp_ms": data.get("timestamp"),
                    "bar_timestamp_ms": raw.get("date_ms"),
                },
            }
            rows.append(row)
            previous_close = close
        return rows

    def get_kline(self, code: str, *, limit: int = 30) -> list[dict[str, Any]]:
        end = self.now().astimezone(CHINA_TZ).date()
        start = end - timedelta(days=max(30, int(limit) * 4))
        rows = self.get_historical(code, start=start, end=end, adjustment="QFQ")
        return rows[-max(1, int(limit)) :]


class FallbackKLineProvider(KLineProvider):
    """Small K-line fallback chain used by explicit sync jobs."""

    name = "fallback_kline"

    def __init__(self, providers: Iterable[KLineProvider]) -> None:
        self.providers = list(providers)
        self.last_errors: list[dict[str, Any]] = []

    def get_kline(self, code: str, *, limit: int = 30) -> list[dict[str, Any]]:
        self.last_errors = []
        for provider in self.providers:
            try:
                rows = provider.get_kline(code, limit=limit) or []
            except Exception as exc:
                self.last_errors.append(
                    _safe_error(
                        exc,
                        provider=str(getattr(provider, "name", provider.__class__.__name__)),
                        endpoint=str(getattr(provider, "endpoint", "")),
                    )
                )
                continue
            if rows:
                return rows
            for error in getattr(provider, "last_errors", []) or []:
                if isinstance(error, Mapping):
                    self.last_errors.append(dict(error))
        return []


def _linked_calendar_rows(start: date, end: date, open_days: list[date], *, source: str, market: str) -> list[dict[str, Any]]:
    from bisect import bisect_left

    rows: list[dict[str, Any]] = []
    current = start
    while current <= end:
        index = bisect_left(open_days, current)
        is_open = index < len(open_days) and open_days[index] == current
        rows.append(
            {
                "trade_date": current,
                "market": market,
                "is_open": is_open,
                "previous_trade_date": open_days[index - 1] if index > 0 else None,
                "next_trade_date": open_days[index + 1] if is_open and index + 1 < len(open_days) else open_days[index] if index < len(open_days) and not is_open else None,
                "source": source,
            }
        )
        current += timedelta(days=1)
    return rows


class FuyaoCalendarProvider(CalendarProvider):
    name = "fuyao_calendar"
    endpoint = FUYAO_CALENDAR_ENDPOINT

    def __init__(self, *, client: FuyaoClient | None = None) -> None:
        self.client = client or client_from_settings()
        self.last_errors: list[dict[str, Any]] = []

    def get_calendar(self, start: date, end: date, *, market: str = "CN") -> list[dict[str, Any]]:
        if start > end:
            raise ValueError("calendar start must not be after end")
        normalized_market = str(market or "CN").upper()
        if normalized_market != "CN":
            raise ValueError(f"unsupported calendar market: {normalized_market}")
        try:
            response = self.client.get(self.endpoint, capability="calendar")
        except Exception as exc:
            self.last_errors = [_safe_error(exc, provider=self.name, endpoint=self.endpoint)]
            raise
        data = response.data if isinstance(response.data, Mapping) else {}
        open_days: list[date] = []
        for row in _rows(data):
            raw_date = row.get("date")
            parsed: date | None = None
            if raw_date:
                try:
                    parsed = datetime.strptime(str(raw_date), "%Y%m%d").date()
                except ValueError:
                    parsed = None
            if parsed is None:
                stamp = _timestamp(row.get("date_ms"))
                parsed = stamp.astimezone(CHINA_TZ).date() if stamp else None
            if parsed is not None:
                open_days.append(parsed)
        open_days = sorted(set(open_days))
        return _linked_calendar_rows(start, end, open_days, source=self.name, market=normalized_market)


class FuyaoSecurityProvider(SecurityProvider):
    """Official ticker list/search adapter for stocks and exchange ETFs."""

    name = "fuyao_security"
    endpoint = FUYAO_TICKER_LIST_ENDPOINT

    def __init__(
        self,
        *,
        client: FuyaoClient | None = None,
        page_size: int = 10000,
        max_pages: int = 20,
        min_interval_seconds: float | None = None,
    ) -> None:
        self.client = client or client_from_settings()
        self.page_size = max(1, min(int(page_size), 10000))
        self.max_pages = max(1, int(max_pages))
        self.last_errors: list[dict[str, Any]] = []

    @staticmethod
    def _identity_row(raw: Mapping[str, Any], *, fetched_at: datetime) -> dict[str, Any] | None:
        thscode = str(raw.get("thscode") or "").strip().upper()
        code = normalize_security_code(thscode or raw.get("ticker"))
        asset_type = str(raw.get("asset_type") or "").strip().lower()
        security_type = _ASSET_TYPES.get(asset_type)
        if not code or security_type is None:
            return None
        exchange = str(raw.get("exchange") or "").strip().upper()
        exchange = {"SH": "SSE", "SSE": "SSE", "SZ": "SZSE", "SZSE": "SZSE", "BJ": "BSE", "BSE": "BSE"}.get(exchange, exchange)
        if exchange not in {"SSE", "SZSE", "BSE"}:
            exchange = exchange_for_code(code)
        if exchange not in {"SSE", "SZSE", "BSE"}:
            return None
        return {
            "market": "CN",
            "exchange": exchange,
            "code": code,
            "symbol": thscode or _full_thscode(code, exchange),
            "name": str(raw.get("name") or "").strip() or None,
            "security_type": security_type,
            "currency": str(raw.get("currency") or "CNY").upper(),
            "source": "fuyao_security",
            "source_updated_at": fetched_at,
            "raw_metadata_json": {
                "thscode": thscode or None,
                "asset_type": asset_type,
            },
        }

    def search(self, query: str, *, exchange: str | None = None, asset_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": str(query), "limit": max(1, min(int(limit), 50))}
        if exchange:
            params["exchange"] = str(exchange).upper()
        if asset_type:
            params["asset_type"] = {"STOCK": "a-share", "ETF": "fund-etf"}.get(
                str(asset_type).strip().upper(), asset_type
            )
        response = self.client.get(FUYAO_TICKER_SEARCH_ENDPOINT, params=params, capability="security_master")
        fetched_at = datetime.now(UTC)
        data = response.data if isinstance(response.data, Mapping) else {}
        return [row for raw in _rows(data) if (row := self._identity_row(raw, fetched_at=fetched_at)) is not None]

    def list_securities(self, *, market: str = "CN") -> list[dict[str, Any]]:
        if str(market or "CN").upper() != "CN":
            raise ValueError(f"unsupported security market: {market}")
        result: dict[tuple[str, str], dict[str, Any]] = {}
        self.last_errors = []
        fetched_at = datetime.now(UTC)
        for page in range(self.max_pages):
            offset = page * self.page_size
            try:
                response = self.client.get(
                    FUYAO_TICKER_LIST_ENDPOINT,
                    params={"asset_type": "a-share,fund-etf", "limit": self.page_size, "offset": offset},
                    capability="security_master",
                )
            except Exception as exc:
                self.last_errors.append(_safe_error(exc, provider=self.name, endpoint=self.endpoint))
                break
            data = response.data if isinstance(response.data, Mapping) else {}
            page_rows = _rows(data)
            for raw in page_rows:
                row = self._identity_row(raw, fetched_at=fetched_at)
                if row is not None:
                    result[(row["exchange"], row["code"])] = row
            if len(page_rows) < self.page_size:
                break
        return list(result.values())


class FuyaoDataProvider:
    """Thin typed facade for enrichment endpoints used by deterministic analytics."""

    name = "fuyao_data"

    def __init__(self, *, client: FuyaoClient | None = None) -> None:
        self.client = client or client_from_settings()

    def get(self, endpoint: str, *, params: Mapping[str, Any] | None = None, capability: str | None = None) -> FuyaoResponse:
        return self.client.get(endpoint, params=params, capability=capability)

    def get_corporate_actions(self, thscode: str, *, start: date | None = None, end: date | None = None) -> FuyaoResponse:
        params: dict[str, Any] = {"thscode": _full_thscode(thscode)}
        if start is not None:
            params["from"] = start.isoformat()
        if end is not None:
            params["to"] = end.isoformat()
        return self.get(FUYAO_CORPORATE_ACTIONS_ENDPOINT, params=params, capability="corporate_actions")

    def get_valuation(self, codes: Iterable[str]) -> FuyaoResponse:
        values = list(dict.fromkeys(_full_thscode(code) for code in codes if normalize_security_code(code)))
        return self.get(FUYAO_VALUATION_ENDPOINT, params={"thscodes": ",".join(values)}, capability="valuation")

    def get_financials(self, thscode: str, *, period: str = "annual", limit: int = 4) -> dict[str, FuyaoResponse]:
        common = {"thscode": _full_thscode(thscode), "period": period, "limit": max(1, min(int(limit), 20))}
        return {
            "income": self.get(FUYAO_INCOME_ENDPOINT, params=common, capability="financials"),
            "balance": self.get(FUYAO_BALANCE_ENDPOINT, params=common, capability="financials"),
            "cash_flow": self.get(FUYAO_CASH_FLOW_ENDPOINT, params=common, capability="financials"),
        }

    def get_indicators(self, thscode: str, report: str) -> FuyaoResponse:
        return self.get(FUYAO_INDICATORS_ENDPOINT, params={"thscode": _full_thscode(thscode), "report": report}, capability="financial_indicators")

    def get_index_catalog(self, *, tag: str | None = None) -> FuyaoResponse:
        return self.get(FUYAO_INDEX_CATALOG_ENDPOINT, params={"tag": tag} if tag else None, capability="index")

    def get_index_constituents(self, thscode: str) -> FuyaoResponse:
        return self.get(FUYAO_INDEX_CONSTITUENTS_ENDPOINT, params={"thscode": str(thscode).upper()}, capability="index")

    def get_index_snapshot(self, codes: Iterable[str]) -> FuyaoResponse:
        values = [str(code).strip().upper() for code in codes if str(code).strip()]
        return self.get(FUYAO_INDEX_SNAPSHOT_ENDPOINT, params={"thscodes": ",".join(values)}, capability="index")

    def get_index_historical(self, code: str, *, start: int, end: int) -> FuyaoResponse:
        return self.get(FUYAO_INDEX_HISTORICAL_ENDPOINT, params={"thscode": str(code).upper(), "interval": "1d", "start": start, "end": end}, capability="index")

    def get_fund_snapshot(self, thscode: str) -> FuyaoResponse:
        return self.get(FUYAO_FUND_SNAPSHOT_ENDPOINT, params={"thscode": _full_thscode(thscode)}, capability="fund")

    def get_fund_historical(self, thscode: str, *, start: int, end: int) -> FuyaoResponse:
        return self.get(FUYAO_FUND_HISTORICAL_ENDPOINT, params={"thscode": _full_thscode(thscode), "interval": "1d", "start": start, "end": end}, capability="fund")

    def get_special(self, endpoint: str, *, params: Mapping[str, Any] | None = None) -> FuyaoResponse:
        normalized = str(endpoint or "").strip()
        if not normalized.startswith(FUYAO_SPECIAL_DATA_PREFIX):
            normalized = FUYAO_SPECIAL_DATA_PREFIX + normalized.lstrip("/")
        return self.get(normalized, params=params, capability="special_data")


__all__ = [
    "FUYAO_BALANCE_ENDPOINT",
    "FUYAO_CALENDAR_ENDPOINT",
    "FUYAO_CASH_FLOW_ENDPOINT",
    "FUYAO_CORPORATE_ACTIONS_ENDPOINT",
    "FUYAO_FUND_HISTORICAL_ENDPOINT",
    "FUYAO_FUND_SNAPSHOT_ENDPOINT",
    "FUYAO_HISTORICAL_ENDPOINT",
    "FUYAO_INCOME_ENDPOINT",
    "FUYAO_INDICATORS_ENDPOINT",
    "FUYAO_INDEX_CATALOG_ENDPOINT",
    "FUYAO_INDEX_CONSTITUENTS_ENDPOINT",
    "FUYAO_INDEX_HISTORICAL_ENDPOINT",
    "FUYAO_INDEX_SNAPSHOT_ENDPOINT",
    "FUYAO_QUOTE_ENDPOINT",
    "FUYAO_SPECIAL_DATA_PREFIX",
    "FUYAO_TICKER_LIST_ENDPOINT",
    "FUYAO_TICKER_SEARCH_ENDPOINT",
    "FUYAO_VALUATION_ENDPOINT",
    "FuyaoCalendarProvider",
    "FuyaoDataProvider",
    "FuyaoKLineProvider",
    "FuyaoQuoteProvider",
    "FuyaoSecurityProvider",
    "FallbackKLineProvider",
    "parse_fuyao_quote",
]
