"""Chronological train/validation/test and walk-forward splits."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

GLOBAL_FINAL_TEST_DAYS = 63
WALK_FORWARD_VALIDATION_DAYS = 63


@dataclass(frozen=True)
class ResearchSplit:
    fold: int
    train_dates: tuple[date, ...]
    validation_dates: tuple[date, ...]
    test_dates: tuple[date, ...]
    status: str = "COMPLETED"
    global_final_test_dates: tuple[date, ...] = ()

    @property
    def train_range(self) -> dict[str, str | None]:
        return _range(self.train_dates)

    @property
    def validation_range(self) -> dict[str, str | None]:
        return _range(self.validation_dates)

    @property
    def test_range(self) -> dict[str, str | None]:
        return _range(self.test_dates)

    @property
    def global_final_test_range(self) -> dict[str, str | None]:
        return _range(self.global_final_test_dates)

    def as_dict(self) -> dict:
        return {
            "fold": self.fold,
            "status": self.status,
            "train_dates": [item.isoformat() for item in self.train_dates],
            "validation_dates": [item.isoformat() for item in self.validation_dates],
            "test_dates": [item.isoformat() for item in self.test_dates],
            "global_final_test_dates": [item.isoformat() for item in self.global_final_test_dates],
            "train_range": self.train_range,
            "validation_range": self.validation_range,
            "test_range": self.test_range,
            "global_final_test_range": self.global_final_test_range,
            "sample_counts": {
                "train_trade_dates": len(self.train_dates),
                "validation_trade_dates": len(self.validation_dates),
                "test_trade_dates": len(self.test_dates),
                "global_final_test_trade_dates": len(self.global_final_test_dates),
            },
        }


def _range(values: tuple[date, ...]) -> dict[str, str | None]:
    return {
        "start": values[0].isoformat() if values else None,
        "end": values[-1].isoformat() if values else None,
    }


def _normalise_dates(values: Iterable[date]) -> list[date]:
    return sorted(set(values))


def chronological_splits(
    dates: Iterable[date],
    *,
    min_train_days: int = 252,
    final_test_days: int = GLOBAL_FINAL_TEST_DAYS,
    validation_days: int = WALK_FORWARD_VALIDATION_DAYS,
) -> list[ResearchSplit]:
    """Build chronological folds plus one global final holdout.

    The last ``final_test_days`` trading dates form a single global test set
    that is never reused as train or validation.  Every fold carries the same
    ``global_final_test_dates`` tuple and keeps ``test_dates`` empty so a
    fixed challenger can be evaluated on the holdout exactly once.
    """

    ordered = _normalise_dates(dates)
    if len(ordered) < min_train_days:
        return [ResearchSplit(0, tuple(ordered), (), (), status="DIAGNOSTIC_ONLY")]
    if final_test_days <= 0 or validation_days <= 0:
        raise ValueError("split_window_sizes_must_be_positive")
    final_test_dates = tuple(ordered[-final_test_days:])
    trainable = ordered[:-final_test_days]
    result: list[ResearchSplit] = []
    if len(trainable) >= validation_days * 2:
        train_end = min(min_train_days, max(1, len(trainable) - validation_days))
        while train_end + validation_days <= len(trainable):
            result.append(ResearchSplit(
                fold=len(result),
                train_dates=tuple(ordered[:train_end]),
                validation_dates=tuple(ordered[train_end:train_end + validation_days]),
                test_dates=(),
                global_final_test_dates=final_test_dates,
            ))
            train_end += validation_days
        if result and len(result[-1].validation_dates) == validation_days:
            validation_start = len(trainable) - validation_days
            last_train_end = len(result[-1].train_dates)
            if last_train_end < validation_start:
                result.append(ResearchSplit(
                    fold=len(result),
                    train_dates=tuple(ordered[:validation_start]),
                    validation_dates=tuple(ordered[validation_start:len(trainable)]),
                    test_dates=(),
                    global_final_test_dates=final_test_dates,
                ))
    if not result:
        result.append(ResearchSplit(
            fold=0,
            train_dates=tuple(trainable[:-validation_days]) if len(trainable) > validation_days else (),
            validation_dates=tuple(trainable[-validation_days:]) if len(trainable) > validation_days else tuple(trainable),
            test_dates=(),
            global_final_test_dates=final_test_dates,
        ))
    return result


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


__all__ = [
    "ResearchSplit",
    "chronological_splits",
    "build_walk_forward_splits",
    "split_cases_by_dates",
    "GLOBAL_FINAL_TEST_DAYS",
    "WALK_FORWARD_VALIDATION_DAYS",
]
