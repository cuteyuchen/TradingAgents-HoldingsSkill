"""Security identity and universe helpers for the market-data foundation.

The service intentionally contains no provider or network code.  Providers may
feed dictionaries into the upsert helpers, while business logic reads the
canonical :class:`SecurityMaster` rows instead of guessing from names.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ..market.codes import exchange_for_code, normalize_security_code as _normalize_security_code
from ..market_models import SecurityMaster

CN_MARKET = "CN"
SSE = "SSE"
SZSE = "SZSE"
BSE = "BSE"
STOCK = "STOCK"
ETF = "ETF"

_EXCHANGE_ALIASES = {
    "SH": SSE,
    "SSE": SSE,
    "上交所": SSE,
    "上海": SSE,
    "SZ": SZSE,
    "SZSE": SZSE,
    "深交所": SZSE,
    "深圳": SZSE,
    "BJ": BSE,
    "BSE": BSE,
    "北交所": BSE,
    "北京": BSE,
}


def normalize_security_code(value: Any) -> str:
    """Return the canonical six-digit code embedded in a common symbol form.

    Accepted examples include ``600519``, ``sh600519``, ``600519.SH`` and
    ``SH600519``.  Empty or non-code values return ``""`` so callers can keep
    the existing optional-code workflow for OCR/screenshot uploads.
    """

    return _normalize_security_code(value)


def normalize_exchange(value: Any) -> str | None:
    """Normalize common exchange aliases to ``SSE``, ``SZSE`` or ``BSE``."""

    if value is None:
        return None
    text = str(value).strip().upper()
    return _EXCHANGE_ALIASES.get(text, text or None)


def infer_exchange(code: Any, exchange: Any = None) -> str:
    """Resolve an exchange from explicit metadata, then conservative code rules.

    The code-prefix rule is only a fallback for identity resolution.  A
    SecurityMaster row remains the authoritative source once persisted.
    """

    explicit = normalize_exchange(exchange)
    if explicit:
        if explicit not in {SSE, SZSE, BSE}:
            raise ValueError(f"unsupported exchange: {explicit}")
        return explicit
    normalized = normalize_security_code(code)
    inferred = exchange_for_code(normalized)
    if inferred is None:
        raise ValueError(f"cannot infer exchange for security code: {normalized or code!r}")
    return inferred


def _coerce_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"invalid security date: {value!r}") from exc


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid security datetime: {value!r}") from exc


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是"}


def _payload(row: SecurityMaster | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(row, SecurityMaster):
        return {
            column.name: getattr(row, column.name)
            for column in SecurityMaster.__table__.columns
            if column.name != "id"
        }
    return dict(row)


def _normalized_payload(row: SecurityMaster | Mapping[str, Any]) -> dict[str, Any]:
    raw = _payload(row)
    code = normalize_security_code(raw.get("code") or raw.get("symbol"))
    if not code:
        raise ValueError("security code must contain six digits")
    security_type = str(raw.get("security_type") or STOCK).strip().upper()
    if security_type in {"SHARE", "EQUITY"}:
        security_type = STOCK
    if security_type not in {STOCK, ETF}:
        raise ValueError(f"unsupported security_type: {security_type}")
    exchange = infer_exchange(code, raw.get("exchange"),)
    symbol = raw.get("symbol")
    if not symbol:
        suffix = {SSE: "SH", SZSE: "SZ", BSE: "BJ"}.get(exchange, exchange)
        symbol = f"{code}.{suffix}" if suffix else code
    return {
        "market": str(raw.get("market") or CN_MARKET).upper(),
        "exchange": exchange,
        "code": code,
        "symbol": str(symbol).upper() if symbol else None,
        "name": raw.get("name"),
        "security_type": security_type,
        "etf_category": raw.get("etf_category"),
        "listing_date": _coerce_date(raw.get("listing_date")),
        "delisting_date": _coerce_date(raw.get("delisting_date")),
        "status": str(raw.get("status") or "ACTIVE").upper(),
        "is_st": _coerce_bool(raw.get("is_st")),
        "is_suspended": _coerce_bool(raw.get("is_suspended")),
        "board": raw.get("board"),
        "lot_size": raw.get("lot_size", 100),
        "currency": str(raw.get("currency") or "CNY").upper(),
        "source": raw.get("source"),
        "source_updated_at": _coerce_datetime(raw.get("source_updated_at")),
        "raw_metadata_json": raw.get("raw_metadata_json", raw.get("metadata")),
    }


def upsert_security(db: Session, row: SecurityMaster | Mapping[str, Any]) -> SecurityMaster:
    """Insert or update one canonical security without deleting old identities."""

    values = _normalized_payload(row)
    existing = db.execute(
        select(SecurityMaster).where(
            SecurityMaster.market == values["market"],
            SecurityMaster.exchange == values["exchange"],
            SecurityMaster.code == values["code"],
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = SecurityMaster(**values)
        db.add(existing)
    else:
        for key, value in values.items():
            if value is not None or key in {"is_st", "is_suspended", "status"}:
                setattr(existing, key, value)
    db.flush()
    return existing


def upsert_securities(db: Session, rows: Iterable[SecurityMaster | Mapping[str, Any]]) -> list[SecurityMaster]:
    """Upsert a batch in one transaction; caller controls commit/rollback."""

    return [upsert_security(db, row) for row in rows]


def get_security(
    db: Session,
    code: Any,
    *,
    exchange: Any = None,
    market: str = CN_MARKET,
) -> SecurityMaster | None:
    normalized = normalize_security_code(code)
    if not normalized:
        return None
    statement: Select[tuple[SecurityMaster]] = select(SecurityMaster).where(
        SecurityMaster.market == market.upper(), SecurityMaster.code == normalized
    )
    resolved_exchange = normalize_exchange(exchange)
    if resolved_exchange:
        statement = statement.where(SecurityMaster.exchange == resolved_exchange)
    return db.execute(statement.order_by(SecurityMaster.id.asc())).scalars().first()


def get_market_universe(
    db: Session,
    *,
    market: str = CN_MARKET,
    security_type: str | None = None,
    exchange: str | None = None,
    board: str | None = None,
    status: str | None = None,
    include_inactive: bool = False,
    include_suspended: bool = True,
    include_st: bool = True,
) -> list[SecurityMaster]:
    """Return canonical instruments matching explicit universe filters."""

    statement: Select[tuple[SecurityMaster]] = select(SecurityMaster).where(
        SecurityMaster.market == market.upper()
    )
    if security_type:
        statement = statement.where(SecurityMaster.security_type == security_type.upper())
    if exchange:
        statement = statement.where(SecurityMaster.exchange == normalize_exchange(exchange))
    if board:
        statement = statement.where(SecurityMaster.board == board)
    if status and status.upper() not in {"ALL", "*"}:
        statement = statement.where(SecurityMaster.status == status.upper())
    elif not include_inactive:
        statement = statement.where(SecurityMaster.status == "ACTIVE")
    if not include_suspended:
        statement = statement.where(SecurityMaster.is_suspended.is_(False))
    if not include_st:
        statement = statement.where(SecurityMaster.is_st.is_(False))
    return list(db.execute(statement.order_by(SecurityMaster.code.asc())).scalars())


# A concise alias for callers that use "sync" terminology.
sync_securities = upsert_securities
