"""Auditable Market Score universe selection."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from ..codes import exchange_for_code, normalize_security_code
from .config import UNIVERSE_RULE_VERSION
from .models import UniverseSnapshot


_DELISTING_STATUSES = {
    "DELISTING",
    "DELISTED",
    "TERMINATED",
    "RETIRED",
    "INACTIVE",
}
_SUSPENDED_STATUSES = {"SUSPENDED", "PAUSED", "HALTED"}


def _value(row: object, key: str, default: Any = None) -> Any:
    return row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value).replace("/", "-")[:10])
        except ValueError:
            return None
    return None


def _open_dates(calendar_rows: Iterable[object], *, through: date) -> list[date]:
    result: set[date] = set()
    for row in calendar_rows:
        if isinstance(row, (date, datetime)):
            day = _as_date(row)
            is_open = True
        else:
            day = _as_date(_value(row, "trade_date", _value(row, "date")))
            is_open = bool(_value(row, "is_open", _value(row, "open", False)))
        if day is not None and day <= through and is_open:
            result.add(day)
    return sorted(result)


def trading_age(
    listing_date: date | datetime | str,
    as_of: date | datetime | str,
    calendar_rows: Iterable[object],
) -> int:
    """Count exchange-open days since listing, inclusive of listing day."""

    listed = _as_date(listing_date)
    current = _as_date(as_of)
    if listed is None or current is None:
        return 0
    return sum(listed <= day <= current for day in _open_dates(calendar_rows, through=current))


def _st_name(name: Any) -> bool:
    normalized = str(name or "").strip().upper().replace(" ", "")
    return normalized.startswith(("ST", "*ST"))


def build_market_score_universe(
    securities: Iterable[object],
    *,
    trade_date: date | datetime | str,
    trading_calendar: Iterable[object],
    requested_codes: Iterable[str] | None = None,
    minimum_trading_days: int = 20,
) -> UniverseSnapshot:
    """Build the SSE/SZSE ordinary-stock universe without calendar heuristics.

    Unknown identities and an unavailable listing/calendar fact fail closed and
    are preserved in the audit counters rather than silently entering Score.
    """

    current = _as_date(trade_date)
    if current is None:
        raise ValueError("trade_date is required")
    calendar_rows = list(trading_calendar)
    open_dates = _open_dates(calendar_rows, through=current)

    identities: dict[str, object] = {}
    invalid_identity_keys: list[str] = []
    for index, row in enumerate(securities):
        code = normalize_security_code(_value(row, "code", _value(row, "symbol")))
        if code:
            identities.setdefault(code, row)
        else:
            invalid_identity_keys.append(f"invalid-{index + 1}")

    if requested_codes is None:
        candidates = [*identities, *invalid_identity_keys]
    else:
        candidates = list(
            dict.fromkeys(
                code if code else f"invalid-{index + 1}"
                for index, raw in enumerate(requested_codes)
                if (code := normalize_security_code(raw)) or str(raw or "").strip()
            )
        )

    included: list[str] = []
    exclusion_reasons: dict[str, list[str]] = {}
    exclusion_counts: dict[str, int] = {}

    for candidate in candidates:
        row = identities.get(candidate)
        reasons: list[str] = []
        if row is None:
            reasons.append("excluded_missing_identity")
        else:
            exchange = str(_value(row, "exchange") or exchange_for_code(candidate) or "").upper()
            # SecurityMaster defaults to STOCK; tolerate minimal fixtures that
            # omit the field while still rejecting explicit non-stock types.
            security_type = str(_value(row, "security_type", "STOCK") or "STOCK").upper()
            status = str(_value(row, "status", "ACTIVE") or "ACTIVE").upper()
            name = _value(row, "name")
            listed = _as_date(_value(row, "listing_date"))
            delisted = _as_date(_value(row, "delisting_date"))

            if security_type != "STOCK":
                reasons.append("excluded_etf" if security_type == "ETF" else "excluded_non_stock")
            if exchange == "BSE":
                reasons.append("excluded_bse")
            elif exchange not in {"SSE", "SZSE"}:
                reasons.append("excluded_missing_identity")
            if bool(_value(row, "is_st", False)) or _st_name(name):
                reasons.append("excluded_st")
            if bool(_value(row, "is_suspended", False)) or status in _SUSPENDED_STATUSES:
                reasons.append("excluded_suspended")
            if status in _DELISTING_STATUSES or (delisted is not None and delisted <= current):
                reasons.append("excluded_delisting")
            if listed is None:
                reasons.append("excluded_missing_listing_date")
            elif not open_dates:
                reasons.append("excluded_calendar_unavailable")
            else:
                age = sum(listed <= day <= current for day in open_dates)
                if age < minimum_trading_days:
                    reasons.append("excluded_new_listing")

        if reasons:
            unique_reasons = list(dict.fromkeys(reasons))
            exclusion_reasons[candidate] = unique_reasons
            for reason in unique_reasons:
                exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
        else:
            included.append(candidate)

    included.sort()
    return UniverseSnapshot(
        trade_date=current,
        universe_total=len(candidates),
        included_count=len(included),
        excluded_count=len(candidates) - len(included),
        included_codes=included,
        exclusion_counts=exclusion_counts,
        exclusion_reasons=exclusion_reasons,
        universe_rule_version=UNIVERSE_RULE_VERSION,
    )


MarketScoreUniverse = UniverseSnapshot
