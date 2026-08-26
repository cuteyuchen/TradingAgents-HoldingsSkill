"""Pure factor helpers shared by stock and ETF scoring."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from statistics import mean, median, stdev
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).replace("/", "-")[:10])
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_of(value: date | datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    day = _date(value)
    return datetime.combine(day, datetime.max.time(), tzinfo=UTC) if day else None


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def normalize_bars(bars: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in bars or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        row["trade_date"] = _date(row.get("trade_date") or row.get("date"))
        row["close"] = _number(row.get("close") or row.get("price"))
        row["open"] = _number(row.get("open"))
        row["high"] = _number(row.get("high"))
        row["low"] = _number(row.get("low"))
        row["prev_close"] = _number(row.get("prev_close") or row.get("previous_close"))
        row["volume"] = _number(row.get("volume"))
        row["amount"] = _number(row.get("amount") or row.get("turnover"))
        row["turnover_rate"] = _number(row.get("turnover_rate"))
        row["available_at"] = _datetime(row.get("available_at"))
        if row["close"] is not None and row["trade_date"] is not None:
            result.append(row)
    return sorted(result, key=lambda item: item["trade_date"])


def closes_from_bars(bars: Iterable[Any]) -> list[float]:
    return [row["close"] for row in normalize_bars(bars) if row.get("close") is not None and row["close"] > 0]


def returns_from_closes(closes: list[float]) -> list[float]:
    return [current / previous - 1.0 for previous, current in zip(closes, closes[1:]) if previous > 0]


def return_over_window(closes: list[float], window: int) -> float | None:
    if len(closes) <= window or closes[-window - 1] <= 0:
        return None
    return closes[-1] / closes[-window - 1] - 1.0


def moving_average(closes: list[float], window: int) -> float | None:
    return mean(closes[-window:]) if len(closes) >= window else None


def moving_average_slope(closes: list[float], window: int, lookback: int = 5) -> float | None:
    if len(closes) < window + lookback:
        return None
    current = mean(closes[-window:])
    previous = mean(closes[-window - lookback:-lookback])
    return current / previous - 1.0 if previous > 0 else None


def calculate_atr(bars: Iterable[Any], window: int = 14) -> float | None:
    rows = normalize_bars(bars)
    true_ranges: list[float] = []
    previous_close: float | None = None
    for row in rows:
        high, low = row.get("high"), row.get("low")
        close = row.get("close")
        if high is None or low is None or close is None:
            previous_close = close
            continue
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        if true_range >= 0:
            true_ranges.append(true_range)
        previous_close = close
    return mean(true_ranges[-window:]) if len(true_ranges) >= window else None


def max_drawdown(closes: list[float], window: int = 60) -> float | None:
    values = closes[-window:]
    if len(values) < 2:
        return None
    peak = values[0]
    drawdowns: list[float] = []
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdowns.append(value / peak - 1.0)
    return abs(min(drawdowns)) if drawdowns else None


def downside_tail_frequency(closes: list[float], threshold: float = -0.02, window: int = 60) -> float | None:
    values = returns_from_closes(closes[-window - 1:])
    return sum(value <= threshold for value in values) / len(values) if values else None


def median_amount(bars: Iterable[Any], window: int = 20) -> float | None:
    values = [row.get("amount") for row in normalize_bars(bars)[-window:]]
    values = [value for value in values if value is not None and value > 0]
    return median(values) if values else None


def relative_volume(bars: Iterable[Any], window: int = 20) -> float | None:
    rows = normalize_bars(bars)
    if len(rows) < window + 1:
        return None
    latest = rows[-1].get("volume")
    previous = [row.get("volume") for row in rows[-window - 1:-1] if row.get("volume") is not None and row.get("volume") > 0]
    baseline = median(previous) if previous else None
    return latest / baseline if latest is not None and baseline and baseline > 0 else None


def price_volume_confirmation(bars: Iterable[Any]) -> float | None:
    rows = normalize_bars(bars)
    if len(rows) < 6:
        return None
    current, previous = rows[-1], rows[-2]
    current_return = current["close"] / previous["close"] - 1.0 if previous.get("close") else None
    volumes = [row.get("volume") for row in rows[-6:-1] if row.get("volume") is not None]
    baseline = mean(volumes) if volumes else None
    if current_return is None or baseline is None or not baseline or current.get("volume") is None:
        return None
    return 100.0 if current_return > 0 and current["volume"] >= baseline else 35.0 if current_return > 0 else 0.0


def percentile_rank(
    value: float | None,
    values: Iterable[float | None],
    *,
    direction: str = "higher",
    winsorize: tuple[float, float] = (0.01, 0.99),
    min_samples: int = 50,
) -> float | None:
    """Return a 0-100 cross-sectional percentile or ``None`` when unavailable."""

    if value is None:
        return None
    usable = sorted(float(item) for item in values if item is not None and math.isfinite(float(item)))
    if len(usable) < min_samples:
        return None
    low_index = min(len(usable) - 1, max(0, int((len(usable) - 1) * winsorize[0])))
    high_index = min(len(usable) - 1, max(0, int((len(usable) - 1) * winsorize[1])))
    clipped = min(max(float(value), usable[low_index]), usable[high_index])
    below = sum(item < clipped for item in usable)
    equal = sum(item == clipped for item in usable)
    result = 100.0 * (below + max(1, equal) / 2) / len(usable)
    return max(0.0, min(100.0, result if direction == "higher" else 100.0 - result))


class CrossSectionalPercentileService:
    """Small reusable percentile service with explicit sample-size semantics."""

    def __init__(self, *, min_samples: int = 50, winsorize: tuple[float, float] = (0.01, 0.99)):
        self.min_samples = min_samples
        self.winsorize = winsorize

    def score(self, values: Mapping[str, float | None], code: str, *, direction: str = "higher") -> float | None:
        return percentile_rank(
            values.get(code),
            values.values(),
            direction=direction,
            winsorize=self.winsorize,
            min_samples=self.min_samples,
        )

    def rank_map(self, values: Mapping[str, float | None], *, direction: str = "higher") -> dict[str, float | None]:
        return {code: self.score(values, code, direction=direction) for code in values}


def component(
    score: float | None,
    *,
    raw: Any = None,
    available: bool | None = None,
    confidence: float = 100.0,
    source: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    available = score is not None if available is None else available
    return {
        "raw": raw,
        "score": round(max(0.0, min(100.0, float(score))), 4) if score is not None else None,
        "available": bool(available),
        "confidence": round(max(0.0, min(100.0, confidence)), 4),
        "source": source,
        "reason": reason,
    }


def combine_components(components: Mapping[str, Mapping[str, Any]], weights: Mapping[str, float]) -> dict[str, Any]:
    usable = {
        key: item
        for key, item in components.items()
        if item.get("available") and item.get("score") is not None and weights.get(key, 0) > 0
    }
    available_weight = sum(weights[key] for key in usable)
    if not usable or available_weight <= 0:
        return {"score": None, "available_weight": 0.0, "coverage": 0.0, "confidence": 0.0}
    score = sum(float(usable[key]["score"]) * weights[key] for key in usable) / available_weight
    confidence = sum(float(usable[key].get("confidence") or 0.0) * weights[key] for key in usable) / available_weight
    return {
        "score": round(max(0.0, min(100.0, score)), 4),
        "available_weight": round(available_weight, 6),
        "coverage": round(available_weight, 6),
        "confidence": round(max(0.0, min(100.0, confidence * available_weight)), 4),
    }


def metadata_section(metadata: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    value = (metadata or {}).get(name)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def section_available_at(section: Mapping[str, Any], as_of: date | datetime | str | None) -> bool:
    available_at = _datetime(section.get("available_at") or section.get("published_at"))
    cutoff = _as_of(as_of)
    return available_at is None or cutoff is None or available_at <= cutoff


def metric_value(section: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        value = _number(section.get(name))
        if value is not None:
            return value
    return None


def feature_snapshot(bars: Iterable[Any], *, price: float | None = None) -> dict[str, Any]:
    rows = normalize_bars(bars)
    closes = closes_from_bars(rows)
    current = price if price is not None else (closes[-1] if closes else None)
    return {
        "price": current,
        "ma20": moving_average(closes, 20),
        "ma60": moving_average(closes, 60),
        "ma120": moving_average(closes, 120),
        "ma20_slope": moving_average_slope(closes, 20),
        "ma60_slope": moving_average_slope(closes, 60),
        "return20": return_over_window(closes, 20),
        "return60": return_over_window(closes, 60),
        "atr14": calculate_atr(rows, 14),
        "volatility20": stdev(returns_from_closes(closes[-21:])) if len(closes) >= 22 else None,
        "volatility60": stdev(returns_from_closes(closes[-61:])) if len(closes) >= 62 else None,
        "max_drawdown60": max_drawdown(closes, 60),
        "downside_tail_frequency": downside_tail_frequency(closes, window=60),
        "median_amount20": median_amount(rows, 20),
        "relative_volume20": relative_volume(rows, 20),
        "price_volume_confirmation": price_volume_confirmation(rows),
        "history_count": len(closes),
        "latest_bar": rows[-1] if rows else None,
    }


__all__ = [
    "CrossSectionalPercentileService",
    "calculate_atr",
    "closes_from_bars",
    "combine_components",
    "component",
    "downside_tail_frequency",
    "feature_snapshot",
    "max_drawdown",
    "median_amount",
    "metadata_section",
    "metric_value",
    "moving_average",
    "moving_average_slope",
    "normalize_bars",
    "percentile_rank",
    "price_volume_confirmation",
    "relative_volume",
    "return_over_window",
    "returns_from_closes",
    "section_available_at",
]
