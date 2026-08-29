"""Provider-independent market-data models."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from zoneinfo import ZoneInfo

from .codes import exchange_for_code, normalize_security_code


CHINA_TZ = ZoneInfo("Asia/Shanghai")
_EXCHANGE_ALIASES = {
    "SH": "SSE",
    "SSE": "SSE",
    "SZ": "SZSE",
    "SZSE": "SZSE",
    "BJ": "BSE",
    "BSE": "BSE",
}


class DataQualityStatus(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    MISSING = "MISSING"
    INVALID = "INVALID"


# Short alias used by some integrations.
QualityStatus = DataQualityStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _coerce_datetime(value: datetime | date | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ).astimezone(UTC)
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=CHINA_TZ).astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("/", "-")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d%H%M%S", "%H:%M:%S"):
            try:
                parsed = datetime.strptime(normalized, fmt)
                if fmt == "%H:%M:%S":
                    today = datetime.now(CHINA_TZ).date()
                    parsed = datetime.combine(today, parsed.time())
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(UTC)


def _coerce_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("/", "-")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _coerce_float(value: Any) -> float | None:
    """Coerce vendor/fixture numeric values without allowing NaN/inf through."""

    if value in (None, "", "-"):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


@dataclass(slots=True)
class NormalizedQuote:
    """A provider-neutral quote record.

    Numeric fields intentionally remain nullable.  A provider that does not
    expose turnover rate, for example, must use ``None`` rather than inventing
    a zero value.
    """

    code: str
    market: str = "CN"
    exchange: str | None = None
    name: str | None = None
    security_type: str | None = None
    price: float | None = None
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    pct_change: float | None = None
    volume: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None
    bid: float | None = None
    ask: float | None = None
    trade_date: date | None = None
    source_timestamp: datetime | None = None
    provider: str = ""
    fetched_at: datetime = field(default_factory=_utcnow)
    quality_status: DataQualityStatus = DataQualityStatus.VALID
    fallback_level: int = 0
    raw_reference: str | None = None
    is_suspended: bool = False
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = normalize_security_code(self.code)
        if self.exchange is None:
            self.exchange = exchange_for_code(self.code)
        else:
            self.exchange = _EXCHANGE_ALIASES.get(str(self.exchange).strip().upper(), str(self.exchange).strip().upper())
        self.market = str(self.market or "CN").upper()
        self.provider = str(self.provider or "").lower()
        if self.security_type is not None:
            self.security_type = str(self.security_type).strip().upper() or None
        if not isinstance(self.quality_status, DataQualityStatus):
            try:
                self.quality_status = DataQualityStatus(str(self.quality_status).upper())
            except ValueError:
                self.quality_status = DataQualityStatus.INVALID
        self.trade_date = _coerce_date(self.trade_date)
        self.source_timestamp = _coerce_datetime(self.source_timestamp)
        if self.trade_date is None and self.source_timestamp is not None:
            self.trade_date = self.source_timestamp.astimezone(CHINA_TZ).date()
        fetched = _coerce_datetime(self.fetched_at)
        self.fetched_at = fetched or _utcnow()
        self.fallback_level = max(0, int(self.fallback_level or 0))
        for field_name in (
            "price",
            "prev_close",
            "open",
            "high",
            "low",
            "pct_change",
            "volume",
            "amount",
            "turnover_rate",
            "bid",
            "ask",
        ):
            setattr(self, field_name, _coerce_float(getattr(self, field_name)))
        self.is_suspended = bool(self.is_suspended)
        self.errors = [str(item) for item in (self.errors or []) if str(item).strip()]
        self.metadata = dict(self.metadata or {})

    @property
    def symbol(self) -> str:
        suffix = {"SSE": ".SH", "SZSE": ".SZ", "BSE": ".BJ"}.get(self.exchange or "")
        return f"{self.code}{suffix}" if self.code and suffix else self.code

    @property
    def freshness_seconds(self) -> float | None:
        if self.source_timestamp is None or self.fetched_at is None:
            return None
        return max(0.0, (self.fetched_at - self.source_timestamp).total_seconds())

    @property
    def turnover(self) -> float | None:
        """Legacy-friendly alias for normalized transaction amount."""

        return self.amount

    @classmethod
    def from_mapping(cls, value: dict[str, Any], *, provider: str | None = None) -> "NormalizedQuote":
        """Create a quote from common vendor/fixture field spellings."""

        data = dict(value)
        if provider is not None:
            data["provider"] = provider
        if "amount" not in data:
            data["amount"] = data.get("turnover")
        if "source_timestamp" not in data:
            data["source_timestamp"] = data.get("quote_time") or data.get("timestamp")
        if "quality_status" not in data and data.get("quality") is not None:
            data["quality_status"] = data.get("quality")
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: item for key, item in data.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["quality_status"] = self.quality_status.value
        data["trade_date"] = self.trade_date.isoformat() if self.trade_date else None
        data["source_timestamp"] = self.source_timestamp.isoformat() if self.source_timestamp else None
        data["fetched_at"] = self.fetched_at.isoformat()
        return data


@dataclass(frozen=True, slots=True)
class QuoteValidation:
    status: DataQualityStatus
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status in {DataQualityStatus.VALID, DataQualityStatus.DEGRADED, DataQualityStatus.STALE}

    def __bool__(self) -> bool:
        return self.valid


@dataclass(frozen=True, slots=True)
class QuoteComparison:
    price_diff_pct: float | None
    prev_close_diff_pct: float | None
    prev_close_conflict: bool
    trade_status_conflict: bool
    quality_status: DataQualityStatus
    errors: tuple[str, ...] = ()

    @property
    def status(self) -> DataQualityStatus:
        return self.quality_status


@dataclass(slots=True)
class QuoteSnapshot:
    """In-memory snapshot metadata; persistence is intentionally out of scope."""

    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    snapshot_key: str | None = None
    market: str = "CN"
    started_at: datetime = field(default_factory=_utcnow)
    completed_at: datetime | None = None
    trade_date: date | None = None
    provider: str | None = None
    fallback_level: int = 0
    expected_count: int = 0
    received_count: int = 0
    coverage_ratio: float = 0.0
    quotes: list[NormalizedQuote] = field(default_factory=list)
    errors: list[Any] = field(default_factory=list)
    quality_status: DataQualityStatus = DataQualityStatus.MISSING
    missing_codes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.started_at = _coerce_datetime(self.started_at) or _utcnow()
        self.completed_at = _coerce_datetime(self.completed_at)
        self.trade_date = _coerce_date(self.trade_date)
        if not isinstance(self.quality_status, DataQualityStatus):
            try:
                self.quality_status = DataQualityStatus(str(self.quality_status).upper())
            except ValueError:
                self.quality_status = DataQualityStatus.INVALID
        self.quotes = [quote if isinstance(quote, NormalizedQuote) else NormalizedQuote.from_mapping(quote) for quote in self.quotes]
        self.missing_codes = list(
            dict.fromkeys(normalize_security_code(code) for code in self.missing_codes if normalize_security_code(code))
        )
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_key": self.snapshot_key,
            "market": self.market,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "provider": self.provider,
            "fallback_level": self.fallback_level,
            "expected_count": self.expected_count,
            "received_count": self.received_count,
            "coverage_ratio": self.coverage_ratio,
            "quotes": [quote.to_dict() for quote in self.quotes],
            "errors": list(self.errors),
            "quality_status": self.quality_status.value,
            "missing_codes": list(self.missing_codes),
            "metadata": dict(self.metadata),
        }
