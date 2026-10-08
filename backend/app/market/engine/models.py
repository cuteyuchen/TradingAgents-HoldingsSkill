"""Serializable value objects for deterministic Market Engine calculations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from .config import MARKET_ENGINE_VERSION, SCORE_CONFIG_VERSION, UNIVERSE_RULE_VERSION


class MarketQualityStatus(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    FROZEN = "FROZEN"


class MarketRegime(str, Enum):
    STRONG_RISK_ON = "STRONG_RISK_ON"
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"
    STRONG_RISK_OFF = "STRONG_RISK_OFF"


@dataclass(slots=True)
class SecurityIdentity:
    code: str
    exchange: str | None = None
    security_type: str = "STOCK"
    name: str | None = None
    listing_date: date | None = None
    delisting_date: date | None = None
    status: str = "ACTIVE"
    is_st: bool = False
    is_suspended: bool = False
    board: str | None = None
    industry_code: str | None = None
    industry_name: str | None = None


@dataclass(slots=True)
class UniverseSnapshot:
    trade_date: date
    universe_total: int
    included_count: int
    excluded_count: int
    included_codes: list[str] = field(default_factory=list)
    exclusion_counts: dict[str, int] = field(default_factory=dict)
    exclusion_reasons: dict[str, list[str]] = field(default_factory=dict)
    universe_rule_version: str = UNIVERSE_RULE_VERSION

    @property
    def inclusion_ratio(self) -> float:
        return self.included_count / self.universe_total if self.universe_total else 0.0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["trade_date"] = self.trade_date.isoformat()
        result["inclusion_ratio"] = round(self.inclusion_ratio, 6)
        return result


@dataclass(frozen=True, slots=True)
class PercentileResult:
    metric_name: str
    percentile: float | None
    normalized_score: float | None
    sample_count: int
    confidence: float
    direction: str
    reason: str | None = None


@dataclass(slots=True)
class ComponentScore:
    name: str
    score: float | None
    raw_metrics: dict[str, Any] = field(default_factory=dict)
    normalized_metrics: dict[str, float | None] = field(default_factory=dict)
    eligible_count: int = 0
    denominator: int = 0
    quality_status: str = "VALID"
    unavailable_reason: str | None = None
    historical_sample_count: int = 0
    subcomponent_available_weight: float = 0.0
    confidence: float = 100.0

    @property
    def available(self) -> bool:
        return self.score is not None and self.quality_status.upper() not in {
            "MISSING",
            "INVALID",
            "UNAVAILABLE",
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"available": self.available}


@dataclass(frozen=True, slots=True)
class ScoreAggregation:
    score: float | None
    available_weight: float
    missing_components: tuple[str, ...]
    contributions: dict[str, float]

    def __float__(self) -> float:
        return float(self.score or 0.0)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (int, float)):
            return self.score == float(other)
        if isinstance(other, ScoreAggregation):
            return (
                self.score,
                self.available_weight,
                self.missing_components,
                self.contributions,
            ) == (
                other.score,
                other.available_weight,
                other.missing_components,
                other.contributions,
            )
        return NotImplemented


@dataclass(slots=True)
class MarketMetricsSnapshot:
    trade_date: date
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    universe: UniverseSnapshot | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    components: dict[str, ComponentScore] = field(default_factory=dict)
    quality_status: str = MarketQualityStatus.VALID.value
    confidence: float = 0.0
    calculation_version: str = MARKET_ENGINE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "captured_at": self.captured_at.isoformat(),
            "universe": self.universe.to_dict() if self.universe else None,
            "metrics": dict(self.metrics),
            "components": {name: value.to_dict() for name, value in self.components.items()},
            "quality_status": self.quality_status,
            "confidence": self.confidence,
            "calculation_version": self.calculation_version,
        }


@dataclass(slots=True)
class MarketScoreSnapshot:
    trade_date: date | None
    raw_score: float | None
    display_score: float | None
    regime: str | None
    confidence: float
    quality_status: str = MarketQualityStatus.VALID.value
    is_frozen: bool = False
    freeze_reason: str | None = None
    components: dict[str, ComponentScore] = field(default_factory=dict)
    previous_display_score: float | None = None
    available_component_weight: float = 0.0
    calculation_version: str = MARKET_ENGINE_VERSION
    score_config_version: str = SCORE_CONFIG_VERSION
    universe_rule_version: str = UNIVERSE_RULE_VERSION

    @property
    def status(self) -> str:
        return MarketQualityStatus.FROZEN.value if self.is_frozen else self.quality_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "raw_score": self.raw_score,
            "display_score": self.display_score,
            "regime": self.regime,
            "confidence": self.confidence,
            "quality_status": self.quality_status,
            "status": self.status,
            "is_frozen": self.is_frozen,
            "freeze_reason": self.freeze_reason,
            "components": {name: value.to_dict() for name, value in self.components.items()},
            "previous_display_score": self.previous_display_score,
            "available_component_weight": self.available_component_weight,
            "calculation_version": self.calculation_version,
            "score_config_version": self.score_config_version,
            "universe_rule_version": self.universe_rule_version,
        }


# Historical bars live in the dedicated access-layer module; re-exporting the
# canonical class here keeps the value-object import path convenient without
# creating a second daily-bar model.
from .history import NormalizedDailyBar  # noqa: E402  (intentional late import)
