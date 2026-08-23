"""Pure cross-sectional and historical Market Engine metrics."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
from math import ceil, isfinite, sqrt
from statistics import fmean, median
from typing import Any

from ..codes import exchange_for_code, normalize_security_code
from .config import MA_WINDOWS, NEW_HIGH_LOW_WINDOWS


ALLOWED_QUALITY = {"VALID", "DEGRADED"}
CONCENTRATION_LEVELS = (0.01, 0.03, 0.05, 0.10, 0.20)


def _value(row: object, key: str, default: Any = None) -> Any:
    return row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)


def _float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


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


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _quality(row: object) -> str:
    status = _value(row, "quality_status", "VALID")
    return str(getattr(status, "value", status) or "VALID").upper()


def _code(row: object) -> str:
    return normalize_security_code(_value(row, "code", _value(row, "symbol")))


def _capture_time(row: object) -> datetime | None:
    metadata = _value(row, "metadata", {}) or {}
    metadata_capture = metadata.get("captured_at") if isinstance(metadata, Mapping) else None
    return _as_datetime(
        _value(row, "captured_at", metadata_capture)
        or _value(row, "snapshot_captured_at")
        or _value(row, "fetched_at")
    )


def _has_explicit_capture(row: object) -> bool:
    """Whether a row carries a deliberate snapshot capture marker.

    Provider ``fetched_at`` values often differ by a few milliseconds inside
    one batch.  They still belong to one coherent run; explicit ``captured_at``
    or ``snapshot_id`` markers, on the other hand, must match exactly.
    """

    if _value(row, "captured_at") is not None or _value(row, "snapshot_captured_at") is not None:
        return True
    metadata = _value(row, "metadata", {}) or {}
    return isinstance(metadata, Mapping) and (
        metadata.get("captured_at") is not None or metadata.get("snapshot_id") is not None
    )


def _return_ratio(row: object) -> float | None:
    explicit = _float(_value(row, "return_ratio"))
    if explicit is not None:
        return explicit
    pct_change = _float(_value(row, "pct_change"))
    if pct_change is not None:
        return pct_change / 100.0
    current = _float(_value(row, "price", _value(row, "close")))
    previous = _float(_value(row, "prev_close"))
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - 1.0


def _coherent_rows(
    rows: Iterable[object],
    *,
    universe_codes: Iterable[str] | None = None,
    captured_at: datetime | str | None = None,
    snapshot_id: str | None = None,
) -> list[object]:
    requested = {
        code
        for raw in (universe_codes or [])
        if (code := normalize_security_code(raw))
    }
    target_time = _as_datetime(captured_at)
    output: list[object] = []
    seen: set[str] = set()
    for row in rows:
        code = _code(row)
        if not code or code in seen or (requested and code not in requested):
            continue
        if _quality(row) not in ALLOWED_QUALITY:
            continue
        if target_time is not None:
            row_time = _capture_time(row)
            if row_time is None:
                continue
            if _has_explicit_capture(row):
                if row_time != target_time:
                    continue
            elif abs((row_time - target_time).total_seconds()) > 120:
                # A batch's per-row fetched_at is allowed to drift slightly;
                # a genuinely different run should still be excluded.
                continue
        if snapshot_id is not None:
            metadata = _value(row, "metadata", {}) or {}
            row_snapshot = _value(row, "snapshot_id") or (
                metadata.get("snapshot_id") if isinstance(metadata, Mapping) else None
            )
            if str(row_snapshot or "") != str(snapshot_id):
                continue
        seen.add(code)
        output.append(row)
    return output


def median_return(values: Iterable[float | int | None]) -> float | None:
    """Return the statistical median in the same units as the input values."""

    clean = [value for raw in values if (value := _float(raw)) is not None]
    return float(median(clean)) if clean else None


def calculate_median_index(
    median_returns: Iterable[float | int | None],
    *,
    base_value: float = 1000.0,
    returns_are_percent: bool = False,
) -> list[float]:
    """Compound daily median returns from a stable base value."""

    current = float(base_value)
    values: list[float] = []
    for raw_return in median_returns:
        daily_return = _float(raw_return)
        if daily_return is None:
            values.append(round(current, 10))
            continue
        ratio = daily_return / 100.0 if returns_are_percent else daily_return
        current *= 1.0 + ratio
        values.append(round(current, 10))
    return values


def next_median_index(
    previous_value: float | None,
    current_median_return: float,
    *,
    base_value: float = 1000.0,
) -> float:
    previous = base_value if previous_value is None else float(previous_value)
    return round(previous * (1.0 + float(current_median_return)), 10)


def top_concentration(
    amounts: Iterable[float | int | None],
    *,
    fraction: float = 0.05,
    universe_size: int | None = None,
) -> dict[str, float | int | None]:
    """Calculate one Top-N amount share with ``ceil(universe * fraction)``."""

    clean = sorted(
        (value for raw in amounts if (value := _float(raw)) is not None and value >= 0),
        reverse=True,
    )
    denominator = sum(clean)
    population = max(0, int(universe_size)) if universe_size is not None else len(clean)
    top_count = ceil(population * fraction) if population else 0
    numerator = sum(clean[:top_count])
    return {
        "fraction": fraction,
        "top_count": top_count,
        "amount_eligible_count": len(clean),
        "numerator": numerator,
        "denominator": denominator,
        "ratio": numerator / denominator if denominator > 0 else None,
    }


def top_concentrations(
    rows: Iterable[object],
    *,
    universe_codes: Iterable[str] | None = None,
    captured_at: datetime | str | None = None,
    snapshot_id: str | None = None,
    universe_size: int | None = None,
    levels: Sequence[float] = CONCENTRATION_LEVELS,
) -> dict[str, Any]:
    """Calculate coherent Top1/3/5/10/20 amount concentrations."""

    coherent = _coherent_rows(
        rows,
        universe_codes=universe_codes,
        captured_at=captured_at,
        snapshot_id=snapshot_id,
    )
    amounts = [_float(_value(row, "amount", _value(row, "turnover"))) for row in coherent]
    requested_count = len(
        {
            code
            for raw in (universe_codes or [])
            if (code := normalize_security_code(raw))
        }
    )
    population = universe_size if universe_size is not None else (requested_count or len(coherent))
    result: dict[str, Any] = {
        "universe_size": population,
        "coherent_count": len(coherent),
        "amount_eligible_count": sum(value is not None and value >= 0 for value in amounts),
    }
    for level in levels:
        label = f"top{int(round(level * 100))}"
        detail = top_concentration(amounts, fraction=level, universe_size=population)
        result[f"{label}_concentration"] = detail["ratio"]
        result[f"{label}_amount"] = detail["numerator"]
        result[f"{label}_count"] = detail["top_count"]
    result["total_amount"] = top_concentration(amounts, universe_size=population)["denominator"]
    return result


def _quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _dispersion(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    average = fmean(values)
    std = sqrt(sum((value - average) ** 2 for value in values) / len(values))
    q1 = _quantile(values, 0.25)
    q3 = _quantile(values, 0.75)
    return std, (q3 - q1) if q1 is not None and q3 is not None else None


def price_limit_percentage(code: str, *, board: str | None = None, is_st: bool = False) -> float:
    """Return the deterministic daily limit for common current A-share boards."""

    if is_st:
        return 5.0
    normalized_board = str(board or "").strip().upper().replace(" ", "_")
    normalized_code = normalize_security_code(code)
    if normalized_board in {"BSE", "BEIJING", "北交所"} or exchange_for_code(normalized_code) == "BSE":
        return 30.0
    if normalized_board in {"CHINEXT", "GEM", "创业板", "STAR", "STAR_MARKET", "科创板"}:
        return 20.0
    if normalized_code.startswith(("300", "301", "688")):
        return 20.0
    return 10.0


def theoretical_limit_price(
    prev_close: float,
    code: str,
    *,
    board: str | None = None,
    is_st: bool = False,
    direction: str = "up",
) -> float:
    percentage = Decimal(str(price_limit_percentage(code, board=board, is_st=is_st))) / Decimal("100")
    multiplier = Decimal("1") + percentage if direction.lower() == "up" else Decimal("1") - percentage
    return float((Decimal(str(prev_close)) * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_price_limit(
    pct_change: float,
    code: str,
    *,
    board: str | None = None,
    is_st: bool = False,
    direction: str = "up",
    tolerance_percentage_points: float = 0.15,
) -> bool:
    limit = price_limit_percentage(code, board=board, is_st=is_st)
    change = float(pct_change)
    if direction.lower() == "down":
        return change <= -(limit - tolerance_percentage_points)
    return change >= limit - tolerance_percentage_points


def calculate_cross_section_metrics(
    rows: Iterable[object],
    *,
    universe_codes: Iterable[str] | None = None,
    captured_at: datetime | str | None = None,
    snapshot_id: str | None = None,
    identity_by_code: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Calculate core breadth/profitability/crowding facts in one O(N log N) pass."""

    requested = {
        code
        for raw in (universe_codes or [])
        if (code := normalize_security_code(raw))
    }
    coherent = _coherent_rows(
        rows,
        universe_codes=requested,
        captured_at=captured_at,
        snapshot_id=snapshot_id,
    )
    returns: list[float] = []
    amounts: list[float] = []
    turnovers: list[float] = []
    advance_count = decline_count = flat_count = 0
    limit_up_count = limit_down_count = 0

    for row in coherent:
        value = _return_ratio(row)
        if value is not None:
            returns.append(value)
            if value > 0:
                advance_count += 1
            elif value < 0:
                decline_count += 1
            else:
                flat_count += 1
            code = _code(row)
            identity = (identity_by_code or {}).get(code)
            board = _value(identity, "board") if identity is not None else _value(row, "board")
            is_st = bool(_value(identity, "is_st", False)) if identity is not None else bool(_value(row, "is_st", False))
            if is_price_limit(value * 100.0, code, board=board, is_st=is_st):
                limit_up_count += 1
            if is_price_limit(value * 100.0, code, board=board, is_st=is_st, direction="down"):
                limit_down_count += 1
        amount = _float(_value(row, "amount", _value(row, "turnover")))
        if amount is not None and amount >= 0:
            amounts.append(amount)
        turnover_rate = _float(_value(row, "turnover_rate"))
        if turnover_rate is not None and turnover_rate >= 0:
            turnovers.append(turnover_rate)

    return_count = len(returns)
    average_return = fmean(returns) if returns else None
    median_value = float(median(returns)) if returns else None
    std, iqr = _dispersion(returns)
    result: dict[str, Any] = {
        "coherent_count": len(coherent),
        "requested_count": len(requested) or len(coherent),
        "return_eligible_count": return_count,
        "advance_count": advance_count,
        "decline_count": decline_count,
        "flat_count": flat_count,
        "advance_ratio": advance_count / return_count if return_count else None,
        "decline_ratio": decline_count / return_count if return_count else None,
        "positive_ratio": advance_count / return_count if return_count else None,
        "all_a_average_return": average_return,
        "all_a_median_return": median_value,
        "cross_section_return_std": std,
        "cross_section_return_iqr": iqr,
        "total_amount": sum(amounts),
        "median_amount": float(median(amounts)) if amounts else None,
        "median_turnover_rate": float(median(turnovers)) if turnovers else None,
        "amount_eligible_count": len(amounts),
        "turnover_eligible_count": len(turnovers),
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "limit_up_ratio": limit_up_count / return_count if return_count else None,
        "limit_down_ratio": limit_down_count / return_count if return_count else None,
    }
    for threshold in (1, 3, 5, 7):
        ratio = threshold / 100.0
        result[f"return_gt_{threshold}_ratio"] = (
            sum(value > ratio for value in returns) / return_count if return_count else None
        )
        result[f"return_lt_minus{threshold}_ratio"] = (
            sum(value < -ratio for value in returns) / return_count if return_count else None
        )
    result.update(
        top_concentrations(
            coherent,
            universe_codes=requested or None,
            captured_at=captured_at,
            snapshot_id=snapshot_id,
            universe_size=len(requested) or len(coherent),
        )
    )
    return result


def _history_by_code(
    history: Mapping[str, Iterable[object]] | Iterable[object],
    *,
    as_of: date | datetime | str | None = None,
    available_at: datetime | str | None = None,
    adjustment: str | None = "QFQ",
) -> dict[str, list[object]]:
    cutoff_date = _as_date(as_of)
    cutoff_available = _as_datetime(available_at)
    grouped: dict[str, list[object]] = {}
    iterable: Iterable[object]
    if isinstance(history, Mapping):
        flattened: list[object] = []
        for raw_code, bars in history.items():
            for bar in bars:
                if isinstance(bar, Mapping) and not _code(bar):
                    bar = dict(bar) | {"code": raw_code}
                flattened.append(bar)
        iterable = flattened
    else:
        iterable = history

    for bar in iterable:
        code = _code(bar)
        trade_day = _as_date(_value(bar, "trade_date", _value(bar, "date")))
        if not code or trade_day is None or _quality(bar) not in ALLOWED_QUALITY:
            continue
        if cutoff_date is not None and trade_day > cutoff_date:
            continue
        bar_available = _as_datetime(_value(bar, "available_at", _value(bar, "fetched_at")))
        if cutoff_available is not None and bar_available is not None and bar_available > cutoff_available:
            continue
        bar_adjustment = str(_value(bar, "adjustment", "QFQ") or "QFQ").upper()
        if adjustment is not None and bar_adjustment != adjustment.upper():
            continue
        if _float(_value(bar, "close", _value(bar, "price"))) is None:
            continue
        grouped.setdefault(code, []).append(bar)
    for bars in grouped.values():
        bars.sort(key=lambda item: _as_date(_value(item, "trade_date", _value(item, "date"))) or date.min)
    return grouped


def calculate_ma_breadth(
    history: Mapping[str, Iterable[object]] | Iterable[object],
    *,
    as_of: date | datetime | str | None = None,
    available_at: datetime | str | None = None,
    universe_codes: Iterable[str] | None = None,
    adjustment: str = "QFQ",
    windows: Sequence[int] = MA_WINDOWS,
) -> dict[str, Any]:
    """Calculate close-above-MA ratios with a separate denominator per window."""

    grouped = _history_by_code(history, as_of=as_of, available_at=available_at, adjustment=adjustment)
    requested = {
        code
        for raw in (universe_codes or grouped.keys())
        if (code := normalize_security_code(raw))
    }
    result: dict[str, Any] = {}
    for window in windows:
        above_count = eligible_count = 0
        for code in requested:
            bars = grouped.get(code, [])
            if len(bars) < window:
                continue
            closes = [_float(_value(bar, "close", _value(bar, "price"))) for bar in bars[-window:]]
            if any(value is None for value in closes):
                continue
            eligible_count += 1
            current = closes[-1]
            average = fmean(value for value in closes if value is not None)
            above_count += bool(current is not None and current > average)
        result[f"above_ma{window}_count"] = above_count
        result[f"ma{window}_eligible_count"] = eligible_count
        result[f"above_ma{window}_ratio"] = above_count / eligible_count if eligible_count else None
    return result


def calculate_ma_trend_metrics(
    history: Mapping[str, Iterable[object]] | Iterable[object],
    *,
    as_of: date | datetime | str | None = None,
    available_at: datetime | str | None = None,
    universe_codes: Iterable[str] | None = None,
    adjustment: str = "QFQ",
) -> dict[str, Any]:
    """Calculate MA ordering and the five-session MA60 endpoint slope."""

    grouped = _history_by_code(history, as_of=as_of, available_at=available_at, adjustment=adjustment)
    requested = {
        code
        for raw in (universe_codes or grouped.keys())
        if (code := normalize_security_code(raw))
    }
    ma5_gt_ma20 = ma5_ma20_eligible = 0
    ma20_gt_ma60 = ma20_ma60_eligible = 0
    ma60_rising = ma60_rising_eligible = 0
    for code in requested:
        closes = [
            value
            for bar in grouped.get(code, [])
            if (value := _float(_value(bar, "close", _value(bar, "price")))) is not None
        ]
        if len(closes) >= 20:
            ma5_ma20_eligible += 1
            ma5_gt_ma20 += fmean(closes[-5:]) > fmean(closes[-20:])
        if len(closes) >= 60:
            ma20_ma60_eligible += 1
            ma20_gt_ma60 += fmean(closes[-20:]) > fmean(closes[-60:])
        # Need 65 closes to compare current MA60 against the endpoint five sessions ago.
        if len(closes) >= 65:
            ma60_rising_eligible += 1
            ma60_rising += fmean(closes[-60:]) > fmean(closes[-65:-5])
    return {
        "ma5_gt_ma20_count": ma5_gt_ma20,
        "ma5_ma20_eligible_count": ma5_ma20_eligible,
        "ma5_gt_ma20_ratio": ma5_gt_ma20 / ma5_ma20_eligible if ma5_ma20_eligible else None,
        "ma20_gt_ma60_count": ma20_gt_ma60,
        "ma20_ma60_eligible_count": ma20_ma60_eligible,
        "ma20_gt_ma60_ratio": ma20_gt_ma60 / ma20_ma60_eligible if ma20_ma60_eligible else None,
        "ma60_rising_count": ma60_rising,
        "ma60_rising_eligible_count": ma60_rising_eligible,
        "ma60_rising_ratio": ma60_rising / ma60_rising_eligible if ma60_rising_eligible else None,
    }


def calculate_new_high_low(
    history: Mapping[str, Iterable[object]] | Iterable[object],
    *,
    as_of: date | datetime | str | None = None,
    available_at: datetime | str | None = None,
    universe_codes: Iterable[str] | None = None,
    adjustment: str = "QFQ",
    windows: Sequence[int] = NEW_HIGH_LOW_WINDOWS,
) -> dict[str, Any]:
    """Use today's close versus the prior N valid closes; no current-day look-ahead."""

    grouped = _history_by_code(history, as_of=as_of, available_at=available_at, adjustment=adjustment)
    requested = {
        code
        for raw in (universe_codes or grouped.keys())
        if (code := normalize_security_code(raw))
    }
    result: dict[str, Any] = {}
    for window in windows:
        high_count = low_count = eligible_count = 0
        for code in requested:
            closes = [
                value
                for bar in grouped.get(code, [])
                if (value := _float(_value(bar, "close", _value(bar, "price")))) is not None
            ]
            if len(closes) < window + 1:
                continue
            eligible_count += 1
            current = closes[-1]
            prior = closes[-(window + 1):-1]
            high_count += current >= max(prior)
            low_count += current <= min(prior)
        result[f"new_high_{window}_count"] = high_count
        result[f"new_low_{window}_count"] = low_count
        result[f"new_high_low_{window}_eligible_count"] = eligible_count
        result[f"new_high_{window}_ratio"] = high_count / eligible_count if eligible_count else None
        result[f"new_low_{window}_ratio"] = low_count / eligible_count if eligible_count else None
        result[f"new_high_low_{window}_net_ratio"] = (
            (high_count - low_count) / eligible_count if eligible_count else None
        )
    return result
