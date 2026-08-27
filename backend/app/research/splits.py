"""Chronological train/validation/test and walk-forward splits."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ResearchSplit:
    fold: int
    train_dates: tuple[date, ...]
    validation_dates: tuple[date, ...]
    test_dates: tuple[date, ...]
    status: str = "COMPLETED"

    @property
    def train_range(self) -> dict[str, str | None]:
        return _range(self.train_dates)

    @property
    def validation_range(self) -> dict[str, str | None]:
        return _range(self.validation_dates)

    @property
    def test_range(self) -> dict[str, str | None]:
        return _range(self.test_dates)

    def as_dict(self) -> dict:
        return {
            "fold": self.fold,
            "status": self.status,
            "train_dates": [item.isoformat() for item in self.train_dates],
            "validation_dates": [item.isoformat() for item in self.validation_dates],
            "test_dates": [item.isoformat() for item in self.test_dates],
            "train_range": self.train_range,
            "validation_range": self.validation_range,
            "test_range": self.test_range,
            "sample_counts": {
                "train_trade_dates": len(self.train_dates),
                "validation_trade_dates": len(self.validation_dates),
                "test_trade_dates": len(self.test_dates),
            },
        }


def _range(values: tuple[date, ...]) -> dict[str, str | None]:
    return {
        "start": values[0].isoformat() if values else None,
        "end": values[-1].isoformat() if values else None,
    }


def _normalise_dates(values: Iterable[date]) -> list[date]:
    return sorted(set(values))


def chronological_splits(dates: Iterable[date], *, min_train_days: int = 252) -> list[ResearchSplit]:
    """Build only chronological splits; this function never shuffles input."""

    ordered = _normalise_dates(dates)
    if len(ordered) < min_train_days:
        return [ResearchSplit(0, tuple(ordered), (), (), status="DIAGNOSTIC_ONLY")]
    if len(ordered) >= 504:
        result: list[ResearchSplit] = []
        fold = 0
        train_end = 252
        while train_end + 63 + 63 <= len(ordered):
            result.append(ResearchSplit(
                fold=fold,
                train_dates=tuple(ordered[:train_end]),
                validation_dates=tuple(ordered[train_end:train_end + 63]),
                test_dates=tuple(ordered[train_end + 63:train_end + 126]),
            ))
            fold += 1
            train_end += 63
        return result
    train_end = max(1, int(len(ordered) * 0.60))
    validation_end = min(len(ordered), train_end + max(1, int(len(ordered) * 0.20)))
    return [ResearchSplit(
        fold=0,
        train_dates=tuple(ordered[:train_end]),
        validation_dates=tuple(ordered[train_end:validation_end]),
        test_dates=tuple(ordered[validation_end:]),
    )]


def build_walk_forward_splits(dates: Iterable[date], **kwargs) -> list[ResearchSplit]:
    return chronological_splits(dates, **kwargs)


def split_cases_by_dates(cases: Iterable[dict], split: ResearchSplit, *, date_key: str = "trade_date") -> dict[str, list[dict]]:
    train = set(split.train_dates)
    validation = set(split.validation_dates)
    test = set(split.test_dates)
    output = {"train": [], "validation": [], "test": []}
    for case in cases:
        value = case.get(date_key)
        if isinstance(value, str):
            value = date.fromisoformat(value[:10])
        if value in train:
            output["train"].append(case)
        elif value in validation:
            output["validation"].append(case)
        elif value in test:
            output["test"].append(case)
    return output


__all__ = ["ResearchSplit", "chronological_splits", "build_walk_forward_splits", "split_cases_by_dates"]
