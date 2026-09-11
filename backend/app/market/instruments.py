"""Deterministic single/multi-instrument market facade, independent of analysis."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import utc_now
from ..market_models import SecurityMaster
from ..services.market_snapshot_service import get_market_data_cache, put_market_data_cache
from .codes import canonical_security_code, exchange_hint, normalize_security_code
from .instrument_schemas import (
    MAX_BAR_LIMIT, MAX_BATCH_QUOTES, BatchQuoteItem, BatchQuoteResponse,
    CapitalFlowHistoryPoint, CapitalFlowSnapshot, InstrumentBar, InstrumentBarsResponse,
    InstrumentCapabilities, InstrumentCapitalFlowResponse, InstrumentEvidenceItem,
    InstrumentIdentity, InstrumentMarketEvidence, InstrumentMarketSnapshotResponse,
    InstrumentMetadata, InstrumentMetadataResponse, InstrumentOrderBookResponse,
    InstrumentQuoteResponse, MarketDataProvenance, MarketDataQuality, OrderBookLevel,
)
from .models import CHINA_TZ, DataQualityStatus, NormalizedQuote, _coerce_date, _coerce_datetime, _coerce_float
from .providers.instruments import InstrumentDataAdapters, InstrumentProviderResult
from .quality import DEFAULT_QUOTE_FRESHNESS_SECONDS, _worst_grade, validate_quote
from .session import MarketSessionResolution, MarketSessionService


_SOURCES = {
    "fuyao", "fuyao_historical", "tencent", "eastmoney", "eastmoney_batch",
    "eastmoney_daily_qfq", "acceptance", "inmemory", "fixture", "security_master",
}
_ADAPTER_FLAGS = {"INDEX_VOLUME_SEMANTICS_UNKNOWN", "VOLUME_UNIT_UNKNOWN"}
_PRIVATE_TEXT = re.compile(r"(api[_-]?key|authorization|cookie|secret|credential|://[^/\s]*@)", re.I)
_FAILURE_TTL = 1.0


def _text(value: Any) -> str | None:
    if not isinstance(value, str) or _PRIVATE_TEXT.search(value):
        return None
    return value[:256] or None


def _source(value: Any) -> str | None:
    return value if value in _SOURCES else "unknown" if value else None


def _mark(result: MarketDataQuality, flag: str, grade: str = "B", *, status: str | None = None) -> None:
    result.quality = _worst_grade(result.quality, grade)
    result.quality_flags = sorted(set([*result.quality_flags, flag]))
    if status is not None:
        result.status = status
    elif result.status == "available":
        result.status = "degraded"


class InstrumentLookupError(ValueError):
    def __init__(self, code: str, http_status: int = 404) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class InstrumentIdentityResolver:
    """One batched SecurityMaster lookup; no provider-created identities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve_many(self, codes: Iterable[str]) -> list[tuple[str, SecurityMaster | None]]:
        inputs = list(codes)
        if any(not normalize_security_code(code) for code in inputs):
            raise InstrumentLookupError("INVALID_INSTRUMENT_CODE", 422)
        rows = self.db.execute(select(SecurityMaster).where(
            SecurityMaster.market == "CN",
            SecurityMaster.code.in_({normalize_security_code(code) for code in inputs}),
        )).scalars().all()
        by_symbol: dict[str, list[SecurityMaster]] = {}
        for row in rows:
            by_symbol.setdefault(row.code, []).append(row)
        output = {}
        for raw in inputs:
            candidates = by_symbol.get(normalize_security_code(raw), [])
            qualified = canonical_security_code(raw)
            matching = [
                row for row in candidates
                if canonical_security_code(row.code, row.exchange) == qualified
            ]
            row = matching[0] if len(matching) == 1 else (
                candidates[0] if len(candidates) == 1 and exchange_hint(raw) is None else None
            )
            if row is not None and row.security_type not in {"STOCK", "ETF", "INDEX"}:
                row = None
            key = canonical_security_code(row.code, row.exchange) if row is not None else qualified
            output.setdefault(key, row)
        return list(output.items())

    def resolve(self, code: str) -> SecurityMaster:
        _, row = self.resolve_many([code])[0]
        if row is None:
            raise InstrumentLookupError("INSTRUMENT_NOT_FOUND")
        return row

    @staticmethod
    def identity(row: SecurityMaster) -> InstrumentIdentity:
        return InstrumentIdentity(
            instrument_id=str(row.id), code=canonical_security_code(row.code, row.exchange),
            symbol=row.code, exchange=row.exchange, name=_text(row.name),
            instrument_type=row.security_type, board=_text(row.board), currency=row.currency,
            lot_size=row.lot_size if row.lot_size and row.lot_size > 0 else None,
            is_st=row.is_st, is_suspended=row.is_suspended, status=row.status,
        )


class InstrumentMarketService:
    def __init__(
        self, db: Session, *, adapters: InstrumentDataAdapters | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.resolver = InstrumentIdentityResolver(db)
        self.session_service = MarketSessionService(db)
        self.adapters = adapters or InstrumentDataAdapters()
        self.now = now or utc_now
        self.cache_scope = f"{id(db.get_bind())}:{id(adapters) if adapters is not None else 'runtime'}"

    def _now(self) -> datetime:
        value = self.now()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    def _session(self) -> MarketSessionResolution:
        return self.session_service.resolve_session(self._now())

    def _key(self, instrument: InstrumentIdentity, kind: str, session: MarketSessionResolution, *params) -> str:
        identity_hash = sha256(instrument.model_dump_json().encode()).hexdigest()[:16]
        return ":".join(map(str, (
            "instrument", self.cache_scope, instrument.instrument_id, identity_hash,
            kind, session.trading_date, session.data_basis, self.adapters.profile, *params,
        )))

    @staticmethod
    def _store(key: str, result: MarketDataProvenance, ttl: float) -> None:
        put_market_data_cache(
            key, result.model_dump(mode="json"),
            ttl=_FAILURE_TTL if result.status in {"unavailable", "empty"} else ttl,
        )

    def _temporal(
        self, result: MarketDataProvenance, session: MarketSessionResolution, *,
        historical: bool = False, daily: bool = False,
    ) -> None:
        result.data_basis = session.data_basis
        if result.status in {"unavailable", "unsupported", "empty"}:
            return
        now = session.resolved_at
        observed = result.observed_at.astimezone(CHINA_TZ) if result.observed_at else None
        day = result.trading_date or (observed.date() if observed else None)
        result.trading_date = day
        if historical:
            result.data_basis = (
                "live" if day == now.date() and now.time() < time(15)
                else "session_close" if day == now.date() else "previous_session_close"
            )
            return
        if session.is_trading_day is None:
            _mark(result, "TRADING_CALENDAR_UNAVAILABLE", "C")
            return
        expected = (
            session.previous_trading_date if session.is_trading_day else session.latest_valid_trading_date
        ) if session.data_basis == "previous_session_close" else session.trading_date
        if observed is None:
            _mark(result, "OBSERVED_TIME_MISSING")
            if day is None:
                _mark(result, "TRADING_DATE_UNVERIFIED", "C")
            elif expected is not None and day != expected:
                _mark(result, "STALE_TRADING_DATE", "C", status="stale")
            return
        if observed > now + timedelta(seconds=5):
            _mark(result, "FUTURE_OBSERVED_TIME", "F", status="unavailable")
            result.error_code = "INVALID_OBSERVED_TIME"
            return
        if day != observed.date():
            _mark(result, "OBSERVED_DATE_MISMATCH", "F", status="unavailable")
            return
        closed = observed.time() >= time(15)
        if closed:
            result.data_basis = "session_close" if observed.date() == now.date() else "previous_session_close"
        else:
            result.data_basis = "live"
        if expected is None or day != expected:
            _mark(result, "STALE_TRADING_DATE", "C", status="stale")
        elif session.data_basis != "live":
            if not closed and not daily:
                _mark(result, "CLOSE_NOT_CONFIRMED", "C", status="stale")
        elif not daily:
            reference = now
            if session.session == "LUNCH_BREAK":
                reference = datetime.combine(now.date(), time(11, 30), tzinfo=CHINA_TZ)
            if (reference - observed).total_seconds() > DEFAULT_QUOTE_FRESHNESS_SECONDS:
                _mark(result, "QUOTE_STALE", "C", status="stale")

    def _provenance(self, value: InstrumentProviderResult, session: MarketSessionResolution) -> dict[str, Any]:
        status = value.status if value.status in {"available", "unavailable", "unsupported", "empty"} else "unavailable"
        result = MarketDataProvenance(
            status=status, quality="A" if status == "available" else "F",
            source=_source(value.provider), provider=_source(value.provider),
            provider_profile=self.adapters.profile, fallback=value.fallback,
            observed_at=_coerce_datetime(value.observed_at), fetched_at=_coerce_datetime(value.fetched_at),
            trading_date=_coerce_date(value.trading_date), data_basis=session.data_basis,
            error_code=value.error_code,
        )
        for flag in value.quality_flags:
            if flag in _ADAPTER_FLAGS:
                _mark(result, flag)
        if value.fallback:
            _mark(result, "PROVIDER_FALLBACK")
        return result.model_dump()

    def _quote(
        self, instrument: InstrumentIdentity, quote: NormalizedQuote | None, session: MarketSessionResolution,
    ) -> InstrumentQuoteResponse:
        if instrument.is_suspended or quote is None:
            return InstrumentQuoteResponse(
                instrument=instrument, status="unavailable", quality="F", data_basis=session.data_basis,
                provider_profile=self.adapters.profile,
                fetched_at=None if instrument.is_suspended else self._now() if quote is None else quote.fetched_at,
                error_code="INSTRUMENT_SUSPENDED" if instrument.is_suspended else "QUOTE_PROVIDER_FAILED",
                quality_flags=["INSTRUMENT_SUSPENDED"] if instrument.is_suspended else ["QUOTE_UNAVAILABLE"],
            )
        result = InstrumentQuoteResponse(
            instrument=instrument, **self._provenance(InstrumentProviderResult(
                provider=quote.provider, observed_at=quote.source_timestamp, fetched_at=quote.fetched_at,
                trading_date=quote.trade_date, fallback=quote.fallback_level > 0,
                quality_flags=quote.metadata.get("facade_flags", []),
            ), session),
            last=_coerce_float(quote.price), prev_close=_coerce_float(quote.prev_close),
            open=_coerce_float(quote.open), high=_coerce_float(quote.high), low=_coerce_float(quote.low),
            volume=_coerce_float(quote.volume), turnover=_coerce_float(quote.amount),
            change_pct=_coerce_float(quote.pct_change), turnover_rate=_coerce_float(quote.turnover_rate),
        )
        validation = validate_quote(quote, now=self._now(), max_age_seconds=None)
        if result.last is None or validation.status in {DataQualityStatus.INVALID, DataQualityStatus.MISSING, DataQualityStatus.CONFLICT}:
            result.last = None
            _mark(result, "QUOTE_INVALID", "F", status="unavailable")
            result.error_code = "QUOTE_INVALID"
        else:
            if result.last is not None and result.prev_close is not None:
                result.change = _coerce_float(result.last - result.prev_close)
                if result.prev_close > 0:
                    if result.change_pct is None:
                        result.change_pct = _coerce_float((result.last / result.prev_close - 1) * 100)
                    if result.high is not None and result.low is not None:
                        result.amplitude_pct = _coerce_float((result.high - result.low) / result.prev_close * 100)
            if any(getattr(result, key) is None for key in ("prev_close", "open", "high", "low", "volume", "turnover")):
                _mark(result, "QUOTE_FIELDS_MISSING")
            if quote.quality_status == DataQualityStatus.DEGRADED:
                _mark(result, "PROVIDER_DATA_DEGRADED")
        self._temporal(result, session)
        return result

    def batch_quotes(self, codes: Iterable[str], *, force_refresh: bool = False) -> BatchQuoteResponse:
        codes = list(codes)
        if not 1 <= len(codes) <= MAX_BATCH_QUOTES:
            raise InstrumentLookupError("BATCH_SIZE_INVALID", 422)
        resolved = self.resolver.resolve_many(codes)
        session = self._session()
        items = {}
        pending = []
        for code, row in resolved:
            if row is None:
                items[code] = BatchQuoteItem(
                    code=code, status="unavailable", quality="F", error_code="INSTRUMENT_NOT_FOUND",
                    data_basis=session.data_basis, quality_flags=["INSTRUMENT_NOT_FOUND"],
                )
                continue
            instrument = self.resolver.identity(row)
            key = self._key(instrument, "quote", session)
            cached = None if force_refresh else get_market_data_cache(key)
            if cached is not None:
                result = InstrumentQuoteResponse.model_validate(cached)
                self._temporal(result, session)
                items[code] = BatchQuoteItem(code=code, **result.model_dump())
            elif instrument.is_suspended:
                result = self._quote(instrument, None, session)
                items[code] = BatchQuoteItem(code=code, **result.model_dump())
            else:
                pending.append(instrument)
        if pending:
            try:
                quotes = self.adapters.quotes(pending)
            except Exception:
                quotes = {}
            for instrument in pending:
                result = self._quote(instrument, quotes.get(instrument.code), session)
                self._store(self._key(instrument, "quote", session), result, 4.0)
                items[instrument.code] = BatchQuoteItem(code=instrument.code, **result.model_dump())
        return BatchQuoteResponse(items=[items[code] for code, _ in resolved], as_of=self._now())

    def quote(self, code: str, *, force_refresh: bool = False) -> InstrumentQuoteResponse:
        row = self.resolver.resolve(code)
        item = self.batch_quotes([canonical_security_code(row.code, row.exchange)], force_refresh=force_refresh).items[0]
        return InstrumentQuoteResponse.model_validate(item.model_dump(exclude={"code"}))

    def metadata(self, code: str) -> InstrumentMetadataResponse:
        row = self.resolver.resolve(code)
        identity = self.resolver.identity(row)
        raw = row.raw_metadata_json if isinstance(row.raw_metadata_json, Mapping) else {}
        values: dict[str, Any] = {}
        keys = {
            "STOCK": ("industry", "price_limit_rule"),
            "ETF": ("fund_type", "underlying_index", "management_company", "tracking_target", "price_limit_rule"),
            "INDEX": ("publisher",),
        }[identity.instrument_type]
        values.update({key: _text(raw.get(key)) for key in keys})
        if identity.instrument_type == "STOCK" and isinstance(raw.get("concepts"), list):
            values["concepts"] = [value for item in raw["concepts"] if (value := _text(item)) is not None][:100]
        if identity.instrument_type == "ETF":
            values["fund_type"] = values.get("fund_type") or _text(row.etf_category)
            values["expense_ratio"] = _coerce_float(raw.get("expense_ratio"))
        if identity.instrument_type == "INDEX":
            values["base_date"] = _coerce_date(raw.get("base_date"))
            values["base_value"] = _coerce_float(raw.get("base_value"))
            count = _coerce_float(raw.get("constituent_count"))
            values["constituent_count"] = int(count) if count is not None and count >= 0 else None
        result = InstrumentMetadataResponse(
            identity=identity, capabilities=self.adapters.capabilities(identity),
            metadata=InstrumentMetadata(
                board=identity.board, is_st=identity.is_st, list_date=row.listing_date,
                lot_size=identity.lot_size,
                available_for_trading=identity.instrument_type != "INDEX" and identity.status == "ACTIVE" and not identity.is_suspended,
                **values,
            ),
            source="security_master", provider="security_master", provider_profile=self.adapters.profile,
            observed_at=_coerce_datetime(row.source_updated_at), fetched_at=_coerce_datetime(row.updated_at),
            data_basis=self._session().data_basis,
        )
        if result.observed_at is None:
            _mark(result, "OBSERVED_TIME_MISSING")
        return result

    def bars(
        self, code: str, *, interval: str = "1d", start: date | None = None,
        end: date | None = None, limit: int = 250, adjustment: str = "none",
    ) -> InstrumentBarsResponse:
        if interval not in {"1d", "1w", "1M"} or adjustment not in {"none", "forward", "backward"}:
            raise InstrumentLookupError("INVALID_BARS_PARAMETERS", 422)
        if not 1 <= limit <= MAX_BAR_LIMIT or (start and end and start > end):
            raise InstrumentLookupError("INVALID_BARS_RANGE", 422)
        instrument = self.resolver.identity(self.resolver.resolve(code))
        session = self._session()
        through = min(end or session.trading_date, session.trading_date)
        since = start or through - timedelta(days=limit * {"1d": 4, "1w": 10, "1M": 40}[interval])
        if through < since:
            raise InstrumentLookupError("INVALID_BARS_RANGE", 422)
        key = self._key(instrument, "bars", session, interval, adjustment, since, through, limit)
        cached = get_market_data_cache(key)
        if cached is not None:
            return InstrumentBarsResponse.model_validate(cached)
        if adjustment not in self.adapters.capabilities(instrument).adjustments:
            value = InstrumentProviderResult(status="unsupported", error_code="ADJUSTMENT_UNSUPPORTED")
        else:
            fetch_start = since - timedelta(days=since.weekday()) if interval == "1w" else since.replace(day=1) if interval == "1M" else since
            try:
                value = self.adapters.bars(instrument, start=fetch_start, end=through, adjustment=adjustment)
            except Exception:
                value = InstrumentProviderResult(status="unavailable", error_code="BARS_PROVIDER_FAILED", fetched_at=self._now())
        result = InstrumentBarsResponse(instrument=instrument, interval=interval, adjustment=adjustment, **self._provenance(value, session))
        rows = value.data.get("bars", [])
        if not isinstance(rows, (list, tuple)):
            _mark(result, "INVALID_BARS_PAYLOAD", "F", status="unavailable")
            result.error_code = "INVALID_BARS_PAYLOAD"
            rows = []
        times = Counter(_coerce_date(row.get("time")) for row in rows if isinstance(row, Mapping))
        accepted = []
        for row in rows:
            if not isinstance(row, Mapping):
                _mark(result, "INVALID_BAR", "C")
                continue
            day = _coerce_date(row.get("time"))
            if day is None:
                _mark(result, "INVALID_BAR_TIME", "C")
                continue
            if times[day] > 1:
                _mark(result, "DUPLICATE_BAR_TIME", "C")
                continue
            if day > through:
                _mark(result, "BAR_OUTSIDE_RANGE", "C")
                continue
            ohlc = {key: _coerce_float(row.get(key)) for key in ("open", "high", "low", "close")}
            if any(number is None or number <= 0 for number in ohlc.values()) or (
                ohlc["high"] < max(ohlc["open"], ohlc["close"], ohlc["low"])
                or ohlc["low"] > min(ohlc["open"], ohlc["close"], ohlc["high"])
            ):
                _mark(result, "INVALID_BAR_OHLC", "C")
                continue
            volume, turnover = _coerce_float(row.get("volume")), _coerce_float(row.get("turnover"))
            if (volume is not None and volume < 0) or (turnover is not None and turnover < 0):
                _mark(result, "NEGATIVE_BAR_VOLUME_OR_TURNOVER", "C")
                continue
            if volume is None or turnover is None:
                _mark(result, "BAR_VOLUME_OR_TURNOVER_MISSING")
            accepted.append(InstrumentBar(time=day, **ohlc, volume=volume, turnover=turnover))
        accepted.sort(key=lambda bar: bar.time)
        if interval != "1d":
            groups: dict[tuple, list[InstrumentBar]] = {}
            for bar in accepted:
                bucket = bar.time.isocalendar()[:2] if interval == "1w" else (bar.time.year, bar.time.month)
                groups.setdefault(bucket, []).append(bar)
            accepted = [
                InstrumentBar(
                    time=group[-1].time, open=group[0].open, close=group[-1].close,
                    high=max(bar.high for bar in group), low=min(bar.low for bar in group),
                    volume=_coerce_float(sum(bar.volume for bar in group)) if all(bar.volume is not None for bar in group) else None,
                    turnover=_coerce_float(sum(bar.turnover for bar in group)) if all(bar.turnover is not None for bar in group) else None,
                )
                for group in groups.values()
            ]
        result.bars = [bar for bar in accepted if since <= bar.time <= through][-limit:]
        if not result.bars and result.status not in {"unsupported", "unavailable"}:
            _mark(result, "BARS_EMPTY", "F", status="empty")
            result.error_code = "BARS_EMPTY"
        if result.bars:
            result.trading_date = result.bars[-1].time
            if result.observed_at is None:
                _mark(result, "OBSERVED_TIME_MISSING")
            self._temporal(result, session, historical=True)
        self._store(key, result, 30 if through == session.trading_date and session.data_basis == "live" else 21600)
        return result

    def order_book(self, code: str) -> InstrumentOrderBookResponse:
        instrument = self.resolver.identity(self.resolver.resolve(code))
        session = self._session()
        key = self._key(instrument, "order_book", session)
        cached = get_market_data_cache(key)
        if cached is not None:
            result = InstrumentOrderBookResponse.model_validate(cached)
            self._temporal(result, session)
            return result
        if not self.adapters.capabilities(instrument).order_book:
            value = InstrumentProviderResult(status="unsupported", error_code="ORDER_BOOK_UNSUPPORTED")
        elif instrument.is_suspended:
            value = InstrumentProviderResult(status="unavailable", error_code="INSTRUMENT_SUSPENDED")
        else:
            try:
                value = self.adapters.order_book(instrument)
            except Exception:
                value = InstrumentProviderResult(status="unavailable", error_code="ORDER_BOOK_PROVIDER_FAILED", fetched_at=self._now())
        result = InstrumentOrderBookResponse(instrument=instrument, **self._provenance(value, session))
        for side in ("bids", "asks"):
            accepted = {}
            rows = value.data.get(side, [])
            if not isinstance(rows, (list, tuple)):
                _mark(result, "INVALID_ORDER_BOOK_PAYLOAD", "F", status="unavailable")
                result.error_code = "INVALID_ORDER_BOOK_PAYLOAD"
                rows = []
            for row in rows:
                if not isinstance(row, Mapping):
                    _mark(result, "INVALID_ORDER_BOOK_LEVEL", "C")
                    continue
                price, volume = _coerce_float(row.get("price")), _coerce_float(row.get("volume"))
                if price in (None, 0) and volume in (None, 0):
                    continue
                if price is None or price <= 0 or (volume is not None and volume < 0):
                    _mark(result, "INVALID_ORDER_BOOK_LEVEL", "C")
                    continue
                if price in accepted:
                    _mark(result, "DUPLICATE_ORDER_BOOK_PRICE", "C")
                    continue
                if volume is None:
                    _mark(result, "ORDER_BOOK_VOLUME_MISSING")
                accepted[price] = volume
            levels = [
                OrderBookLevel(level=index, price=price, volume=accepted[price])
                for index, price in enumerate(sorted(accepted, reverse=side == "bids")[:5], 1)
            ]
            setattr(result, side, levels)
        if result.status not in {"unsupported", "unavailable"}:
            if not result.bids and not result.asks:
                _mark(result, "ORDER_BOOK_EMPTY", "C", status="empty")
            elif len(result.bids) < 5 or len(result.asks) < 5:
                _mark(result, "PARTIAL_ORDER_BOOK")
        for side, name in (("bids", "bid_volume_total"), ("asks", "ask_volume_total")):
            levels = getattr(result, side)
            if levels and all(level.volume is not None for level in levels):
                setattr(result, name, _coerce_float(sum(level.volume for level in levels)))
        for name in ("order_ratio", "order_difference", "inner_volume", "outer_volume"):
            number = _coerce_float(value.data.get(name))
            if name in {"inner_volume", "outer_volume"} and number is not None and number < 0:
                _mark(result, "INVALID_ORDER_BOOK_VOLUME", "C")
                number = None
            setattr(result, name, number)
        if result.bid_volume_total is not None and result.ask_volume_total is not None:
            difference = result.bid_volume_total - result.ask_volume_total
            total = result.bid_volume_total + result.ask_volume_total
            for name, number in (
                ("order_difference", difference),
                ("order_ratio", difference / total * 100 if total > 0 else None),
            ):
                if getattr(result, name) is None and number is not None:
                    setattr(result, name, _coerce_float(number))
                    result.derived_fields.append(name)
            result.derived = bool(result.derived_fields)
        if (
            session.session in {"MORNING", "AFTERNOON"} and result.bids and result.asks
            and result.bids[0].price > result.asks[0].price
        ):
            _mark(result, "CROSSED_ORDER_BOOK", "C")
        self._temporal(result, session)
        self._store(key, result, 2.0)
        return result

    def capital_flow(self, code: str) -> InstrumentCapitalFlowResponse:
        instrument = self.resolver.identity(self.resolver.resolve(code))
        session = self._session()
        key = self._key(instrument, "capital_flow", session)
        cached = get_market_data_cache(key)
        if cached is not None:
            result = InstrumentCapitalFlowResponse.model_validate(cached)
            self._temporal(result, session, daily=True)
            return result
        if not self.adapters.capabilities(instrument).capital_flow:
            value = InstrumentProviderResult(status="unsupported", error_code="CAPITAL_FLOW_UNSUPPORTED")
        elif instrument.is_suspended:
            value = InstrumentProviderResult(status="unavailable", error_code="INSTRUMENT_SUSPENDED")
        else:
            try:
                value = self.adapters.capital_flow(instrument)
            except Exception:
                value = InstrumentProviderResult(status="unavailable", error_code="CAPITAL_FLOW_PROVIDER_FAILED", fetched_at=self._now())
        result = InstrumentCapitalFlowResponse(instrument=instrument, **self._provenance(value, session))
        fields = CapitalFlowSnapshot.model_fields
        current = value.data.get("current") or {}
        if not isinstance(current, Mapping):
            _mark(result, "INVALID_CAPITAL_FLOW_PAYLOAD", "F", status="unavailable")
            result.error_code = "INVALID_CAPITAL_FLOW_PAYLOAD"
            current = {}
        if any(_coerce_float(current.get(name)) is not None for name in fields):
            result.current = CapitalFlowSnapshot(**{name: _coerce_float(current.get(name)) for name in fields})
        history = {}
        rows = value.data.get("history", [])
        if not isinstance(rows, (list, tuple)):
            _mark(result, "INVALID_CAPITAL_FLOW_PAYLOAD", "F", status="unavailable")
            result.error_code = "INVALID_CAPITAL_FLOW_PAYLOAD"
            rows = []
        for row in rows:
            if not isinstance(row, Mapping):
                _mark(result, "INVALID_CAPITAL_FLOW_ROW", "C")
                continue
            day = _coerce_date(row.get("time"))
            if day is None or day > session.trading_date:
                _mark(result, "INVALID_CAPITAL_FLOW_DATE", "C")
                continue
            if day in history:
                _mark(result, "DUPLICATE_CAPITAL_FLOW_DATE", "C")
                continue
            history[day] = CapitalFlowHistoryPoint(time=day, **{name: _coerce_float(row.get(name)) for name in fields})
        result.history = [history[day] for day in sorted(history)]
        if result.status not in {"unsupported", "unavailable"}:
            if result.current is None and not result.history:
                _mark(result, "CAPITAL_FLOW_EMPTY", "F", status="empty")
            else:
                _mark(result, "PROVIDER_DERIVED_CLASSIFICATION")
                self._temporal(result, session, daily=True)
        self._store(key, result, 45.0)
        return result

    def _snapshot(self, instrument: InstrumentIdentity, quote: InstrumentQuoteResponse) -> InstrumentMarketSnapshotResponse:
        capabilities = self.adapters.capabilities(instrument)
        book = self.order_book(instrument.code)
        flow = self.capital_flow(instrument.code)
        critical = [quote]
        if capabilities.order_book:
            critical.append(book)
        if capabilities.capital_flow:
            critical.append(flow)
        grade = _worst_grade(*(module.quality for module in critical))
        quality = MarketDataQuality(
            quality=grade, status=(
                "unavailable" if quote.status == "unavailable" else "stale" if quote.status == "stale"
                else "degraded" if grade != "A" else "available"
            ),
            quality_flags=sorted({flag for module in critical for flag in module.quality_flags}),
        )
        return InstrumentMarketSnapshotResponse(
            instrument=instrument, capabilities=capabilities, quote=quote, order_book=book, capital_flow=flow,
            data_quality=quality, as_of=self._now(), **quality.model_dump(),
        )

    def snapshot(self, code: str) -> InstrumentMarketSnapshotResponse:
        quote = self.quote(code)
        return self._snapshot(quote.instrument, quote)

    def evidence_snapshot(self, codes: Iterable[str], *, bars_limit: int = 30) -> InstrumentMarketEvidence:
        batch = self.batch_quotes(codes)
        items = []
        for item in batch.items:
            if item.instrument is None:
                raise InstrumentLookupError("INSTRUMENT_NOT_FOUND")
            quote = InstrumentQuoteResponse.model_validate(item.model_dump(exclude={"code"}))
            items.append(InstrumentEvidenceItem(
                snapshot=self._snapshot(quote.instrument, quote),
                bars=self.bars(
                    item.code, limit=bars_limit,
                    adjustment="none" if quote.instrument.instrument_type == "INDEX" else "forward",
                ),
            ))
        return InstrumentMarketEvidence(items=items, as_of=self._now())
