"""Bulk PIT fact loading for deterministic recompute.

The dataset materializes every Phase L fact used by a recompute in a handful of
SQL statements, then serves date-indexed views from memory. This is the
performance boundary that prevents N+1 queries in batch research.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..history.models import (
    EtfMetadataHistory,
    FundamentalReport,
    PriceBasisMetadata,
    SecurityClassificationDaily,
    SecurityLifecycleEvent,
    SecurityTradingStatusDaily,
    SecurityValuationDaily,
)
from ..history.time import fundamental_visible_at, visible
from ..market.codes import normalize_security_code
from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache
from ..market_models import TradingCalendar
from ..v2_models import HoldingItem, PortfolioSnapshot

CHINA_TZ = ZoneInfo("Asia/Shanghai")
EOD_DECISION_TIME = time(15, 10)
_ACTIVE_EVENTS = {"LISTED", "RELISTED"}
_INACTIVE_EVENTS = {"DELIST_PENDING", "DELISTED"}
_INVALID_STATUSES = {"SUSPENDED", "HALTED", "DELISTED", "PAUSED"}
_ST_CLASSIFICATIONS = {"ST", "STAR_ST", "DELIST_RISK"}


def eod_cutoff(day: date) -> datetime:
    """Convert the 15:10 Shanghai EOD decision instant to UTC-naive."""

    return datetime.combine(day, EOD_DECISION_TIME, tzinfo=CHINA_TZ).astimezone(UTC).replace(tzinfo=None)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _code(value: str | None) -> str:
    return normalize_security_code(value or "")


def _serialize_bar(row: DailyBarCache) -> dict[str, Any]:
    return {
        "code": row.code,
        "trade_date": row.trade_date,
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "prev_close": row.prev_close,
        "volume": row.volume,
        "amount": row.amount,
        "turnover_rate": row.turnover_rate,
        "adjustment": row.adjustment,
        "provider": row.provider,
        "fetched_at": row.fetched_at,
        "available_at": row.available_at,
        "quality_status": row.quality_status,
        "metadata": row.metadata_json or {},
    }


@dataclass
class RecomputePitDataset:
    market: str
    start_date: date
    end_date: date
    warmup_start_date: date
    calendar_dates: list[date] = field(default_factory=list)
    lifecycle_rows: list[SecurityLifecycleEvent] = field(default_factory=list)
    classification_rows: list[SecurityClassificationDaily] = field(default_factory=list)
    trading_rows: list[SecurityTradingStatusDaily] = field(default_factory=list)
    valuation_rows: list[SecurityValuationDaily] = field(default_factory=list)
    fundamental_rows: list[FundamentalReport] = field(default_factory=list)
    etf_rows: list[EtfMetadataHistory] = field(default_factory=list)
    basis_rows: list[PriceBasisMetadata] = field(default_factory=list)
    bars: list[DailyBarCache] = field(default_factory=list)
    benchmark_rows: list[AllAMedianIndexDaily] = field(default_factory=list)
    portfolio_snapshots: list[PortfolioSnapshot] = field(default_factory=list)
    holding_rows: list[HoldingItem] = field(default_factory=list)
    query_count: int = 0

    def source_ids(self) -> list[str]:
        """Stable typed source references for one frozen source set."""

        ids: list[str] = []
        for row in self.calendar_dates:
            ids.append(f"trading_calendar:{row.isoformat()}")
        for rows, label in (
            (self.lifecycle_rows, "security_lifecycle"),
            (self.classification_rows, "security_classification"),
            (self.trading_rows, "security_trading_status"),
            (self.valuation_rows, "security_valuation"),
            (self.fundamental_rows, "fundamental_reports"),
            (self.etf_rows, "etf_metadata"),
            (self.basis_rows, "price_basis"),
            (self.bars, "daily_bar_cache"),
            (self.benchmark_rows, "all_a_median_index_daily"),
            (self.portfolio_snapshots, "portfolio_snapshots"),
            (self.holding_rows, "holding_items"),
        ):
            for row in rows:
                ids.append(f"{label}:{row.id}")
        return sorted(set(ids))

    def lifecycle_states(self, day: date, cutoff: datetime) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for row in self.lifecycle_rows:
            if row.effective_date > day or not visible(row.source_available_at, cutoff):
                continue
            code = _code(row.code)
            if not code:
                continue
            state = states.setdefault(code, {
                "code": code,
                "market": row.market,
                "exchange": row.exchange,
                "security_type": None,
                "name": None,
                "listed_date": None,
                "delisted_date": None,
                "active": False,
                "events": [],
            })
            event_type = str(row.event_type or "").upper()
            if event_type == "LISTED":
                state["listed_date"] = row.effective_date
                state["active"] = True
                state["delisted_date"] = None
            elif event_type == "RELISTED":
                state["active"] = True
                state["delisted_date"] = None
            elif event_type in _INACTIVE_EVENTS:
                state["active"] = False
                state["delisted_date"] = row.effective_date
            if row.security_type:
                state["security_type"] = row.security_type.upper()
            if row.security_name:
                state["name"] = row.security_name
            if row.exchange:
                state["exchange"] = row.exchange.upper()
            state["events"].append({
                "id": row.id,
                "event_type": event_type,
                "effective_date": row.effective_date.isoformat(),
                "source": row.source,
                "source_ref": row.source_ref,
                "source_available_at": row.source_available_at.isoformat() if row.source_available_at else None,
            })
        return states

    def _latest_daily(
        self,
        rows: Iterable[Any],
        *,
        day: date,
        cutoff: datetime,
        exact_date: bool,
        date_field: str,
        status_field: str,
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            row_date = getattr(row, date_field)
            if row_date is None:
                continue
            if exact_date and row_date != day:
                continue
            if not exact_date and row_date > day:
                continue
            if not visible(row.source_available_at, cutoff):
                continue
            code = _code(row.code)
            if code:
                grouped[code].append(row)
        result: dict[str, dict[str, Any]] = {}
        for code, values in grouped.items():
            latest = max(values, key=lambda item: (getattr(item, date_field) or date.min, item.id))
            result[code] = {
                "status": str(getattr(latest, status_field) or "UNKNOWN").upper(),
                "row_id": latest.id,
                "trade_date": getattr(latest, date_field),
                "source": latest.source,
                "source_ref": latest.source_ref,
                "source_available_at": latest.source_available_at,
                "quality_status": str(getattr(latest, "quality_status") or "VALID").upper(),
                "is_name_derived": bool(getattr(latest, "is_name_derived", False)),
            }
        return result

    def classification_by_code(self, day: date, cutoff: datetime) -> dict[str, dict[str, Any]]:
        return self._latest_daily(
            self.classification_rows,
            day=day,
            cutoff=cutoff,
            exact_date=True,
            date_field="trade_date",
            status_field="classification",
        )

    def trading_status_by_code(self, day: date, cutoff: datetime) -> dict[str, dict[str, Any]]:
        return self._latest_daily(
            self.trading_rows,
            day=day,
            cutoff=cutoff,
            exact_date=True,
            date_field="trade_date",
            status_field="status",
        )

    def valuation_by_code(self, day: date, cutoff: datetime) -> dict[str, dict[str, Any]]:
        return self._latest_daily(
            self.valuation_rows,
            day=day,
            cutoff=cutoff,
            exact_date=False,
            date_field="trade_date",
            status_field="quality_status",
        )

    def basis_by_code(self, day: date, cutoff: datetime) -> dict[str, dict[str, Any]]:
        return self._latest_daily(
            self.basis_rows,
            day=day,
            cutoff=cutoff,
            exact_date=False,
            date_field="trade_date",
            status_field="basis",
        )

    def fundamental_by_code(self, day: date, cutoff: datetime) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[tuple[datetime, FundamentalReport]]] = defaultdict(list)
        for row in self.fundamental_rows:
            visible_at = fundamental_visible_at(row)
            if visible_at is not None and visible_at <= cutoff:
                grouped[_code(row.code)].append((visible_at, row))
        result: dict[str, dict[str, Any]] = {}
        for code, values in grouped.items():
            visible_at, row = max(values, key=lambda item: (item[0], item[1].revision_number or 0, item[1].id))
            result[code] = {
                "available": True,
                "report_period": row.report_period,
                "report_type": row.report_type,
                "published_at": row.published_at,
                "visible_at": visible_at,
                "revision_number": row.revision_number,
                "is_restatement": row.is_restatement,
                "roe": row.roe,
                "revenue": row.revenue,
                "revenue_yoy": row.revenue_yoy,
                "net_profit": row.net_profit,
                "net_profit_yoy": row.net_profit_yoy,
                "gross_margin": row.gross_margin,
                "debt_ratio": row.debt_ratio,
                "operating_cash_flow": row.operating_cash_flow,
                "eps": row.eps,
                "source": row.source,
                "source_ref": row.source_ref,
                "source_available_at": row.source_available_at,
                "quality_status": row.quality_status,
            }
        return result

    def etf_metadata_by_code(self, day: date, cutoff: datetime) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[EtfMetadataHistory]] = defaultdict(list)
        for row in self.etf_rows:
            if row.effective_date > day or not visible(row.source_available_at, cutoff):
                continue
            code = _code(row.code)
            if code:
                grouped[code].append(row)
        result: dict[str, dict[str, Any]] = {}
        for code, values in grouped.items():
            row = max(values, key=lambda item: (item.effective_date, item.id))
            result[code] = {
                "available": True,
                "effective_date": row.effective_date,
                "category": row.category,
                "index_code": row.index_code,
                "benchmark_code": row.benchmark_code,
                "fund_type": row.fund_type,
                "sector_theme_json": row.sector_theme_json,
                "inception_date": row.inception_date,
                "source": row.source,
                "source_ref": row.source_ref,
                "source_available_at": row.source_available_at,
                "quality_status": row.quality_status,
            }
        return result

    def bars_by_code(self, day: date, cutoff: datetime) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self.bars:
            if row.trade_date > day:
                continue
            available = _naive_utc(row.available_at)
            if available is None or available > cutoff:
                continue
            if str(row.adjustment or "QFQ").upper() != "QFQ":
                continue
            if str(row.quality_status or "VALID").upper() not in {"VALID", "DEGRADED"}:
                continue
            code = _code(row.code)
            if code:
                grouped[code].append(_serialize_bar(row))
        for values in grouped.values():
            values.sort(key=lambda item: item["trade_date"])
        return dict(grouped)

    def quote_rows(self, day: date, cutoff: datetime) -> list[dict[str, Any]]:
        """Build EOD close quote rows from the daily bar cache."""

        rows: list[dict[str, Any]] = []
        for bar in self.bars:
            if bar.trade_date != day:
                continue
            available = _naive_utc(bar.available_at)
            if available is None or available > cutoff:
                continue
            if str(bar.adjustment or "QFQ").upper() != "QFQ":
                continue
            if str(bar.quality_status or "VALID").upper() not in {"VALID", "DEGRADED"}:
                continue
            code = _code(bar.code)
            if not code or bar.close is None or bar.close <= 0:
                continue
            rows.append({
                "code": code,
                "price": bar.close,
                "close": bar.close,
                "prev_close": bar.prev_close,
                "amount": bar.amount,
                "volume": bar.volume,
                "turnover_rate": bar.turnover_rate,
                "quality_status": str(bar.quality_status or "VALID").upper(),
                "provider": bar.provider or "daily_bar_cache",
                "available_at": bar.available_at,
                "source": "daily_bar_cache_close",
                "price_basis": "QFQ",
                "fetched_at": bar.fetched_at,
                "metadata": bar.metadata_json or {},
            })
        return sorted(rows, key=lambda item: item["code"])

    def held_codes(self, day: date, cutoff: datetime) -> set[str]:
        snapshot = self.latest_snapshot(day, cutoff)
        if snapshot is None:
            return set()
        return {
            normalize_security_code(item.code)
            for item in self.holding_rows
            if item.snapshot_id == snapshot.id and normalize_security_code(item.code)
        }

    def latest_snapshot(self, day: date, cutoff: datetime) -> PortfolioSnapshot | None:
        candidates = [
            row for row in self.portfolio_snapshots
            if _naive_utc(row.snapshot_time) is not None
            and _naive_utc(row.snapshot_time) <= cutoff
            and str(row.status or "").lower() in {"confirmed"}
        ]
        return max(candidates, key=lambda item: (_naive_utc(item.snapshot_time) or datetime.min, item.id), default=None)

    def holdings_for(self, snapshot_id: int) -> list[HoldingItem]:
        return sorted(
            [row for row in self.holding_rows if row.snapshot_id == snapshot_id],
            key=lambda item: (str(item.code or ""), item.id),
        )

    def is_st(self, code: str, day: date, cutoff: datetime) -> bool:
        row = self.classification_by_code(day, cutoff).get(code)
        return bool(row and row["status"] in _ST_CLASSIFICATIONS)

    def is_suspended(self, code: str, day: date, cutoff: datetime) -> bool:
        row = self.trading_status_by_code(day, cutoff).get(code)
        return bool(row and row["status"] in _INVALID_STATUSES)


def _trading_calendar(db: Session, *, through: date, market: str) -> list[date]:
    return list(db.execute(
        select(TradingCalendar.trade_date).where(
            TradingCalendar.market == market,
            TradingCalendar.trade_date <= through,
            TradingCalendar.is_open.is_(True),
        ).order_by(TradingCalendar.trade_date.asc())
    ).scalars())


def load_recompute_pit_dataset(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    market: str = "CN",
    lookback_trading_days: int = 750,
    include_candidates: bool = False,
    include_portfolio: bool = False,
    portfolio_id: int | None = None,
) -> RecomputePitDataset:
    """Load one frozen PIT source set with a small constant number of queries."""

    market = str(market or "CN").upper()
    calendar_dates = _trading_calendar(db, through=end_date, market=market)
    visible_dates = [day for day in calendar_dates if start_date <= day <= end_date]
    warmup_start = (
        calendar_dates[max(0, len(calendar_dates) - lookback_trading_days)]
        if len(calendar_dates) >= lookback_trading_days
        else calendar_dates[0]
        if calendar_dates
        else start_date
    )
    end_cutoff = eod_cutoff(end_date)
    dataset = RecomputePitDataset(
        market=market,
        start_date=start_date,
        end_date=end_date,
        warmup_start_date=warmup_start,
        calendar_dates=calendar_dates,
    )
    dataset.query_count += 1
    dataset.lifecycle_rows = list(db.execute(
        select(SecurityLifecycleEvent).where(
            SecurityLifecycleEvent.market == market,
            SecurityLifecycleEvent.effective_date <= end_date,
        ).order_by(SecurityLifecycleEvent.effective_date.asc(), SecurityLifecycleEvent.id.asc())
    ).scalars())
    dataset.query_count += 1
    dataset.classification_rows = list(db.execute(
        select(SecurityClassificationDaily).where(
            SecurityClassificationDaily.market == market,
            SecurityClassificationDaily.trade_date <= end_date,
        ).order_by(SecurityClassificationDaily.trade_date.asc(), SecurityClassificationDaily.id.asc())
    ).scalars())
    dataset.query_count += 1
    dataset.trading_rows = list(db.execute(
        select(SecurityTradingStatusDaily).where(
            SecurityTradingStatusDaily.market == market,
            SecurityTradingStatusDaily.trade_date <= end_date,
        ).order_by(SecurityTradingStatusDaily.trade_date.asc(), SecurityTradingStatusDaily.id.asc())
    ).scalars())
    dataset.query_count += 1
    all_bars = list(db.execute(
        select(DailyBarCache).where(
            DailyBarCache.market == market,
            DailyBarCache.trade_date >= warmup_start,
            DailyBarCache.trade_date <= end_date,
            DailyBarCache.adjustment == "QFQ",
        ).order_by(DailyBarCache.trade_date.asc(), DailyBarCache.code.asc(), DailyBarCache.id.asc())
    ).scalars())
    dataset.bars = [
        row for row in all_bars
        if _naive_utc(row.available_at) is not None
        and _naive_utc(row.available_at) <= end_cutoff
    ]
    dataset.query_count += 1
    dataset.basis_rows = list(db.execute(
        select(PriceBasisMetadata).where(
            PriceBasisMetadata.market == market,
            PriceBasisMetadata.trade_date <= end_date,
        ).order_by(PriceBasisMetadata.trade_date.asc(), PriceBasisMetadata.id.asc())
    ).scalars())
    dataset.query_count += 1
    dataset.benchmark_rows = list(db.execute(
        select(AllAMedianIndexDaily).where(
            AllAMedianIndexDaily.market == market,
            AllAMedianIndexDaily.trade_date <= end_date,
            AllAMedianIndexDaily.quality_status.in_(("VALID", "DEGRADED")),
        ).order_by(AllAMedianIndexDaily.trade_date.asc(), AllAMedianIndexDaily.id.asc())
    ).scalars())
    dataset.query_count += 1
    if include_candidates:
        dataset.valuation_rows = list(db.execute(
            select(SecurityValuationDaily).where(
                SecurityValuationDaily.market == market,
                SecurityValuationDaily.trade_date <= end_date,
            ).order_by(SecurityValuationDaily.trade_date.asc(), SecurityValuationDaily.id.asc())
        ).scalars())
        dataset.fundamental_rows = list(db.execute(
            select(FundamentalReport).where(
                FundamentalReport.market == market,
            ).order_by(FundamentalReport.code.asc(), FundamentalReport.id.asc())
        ).scalars())
        dataset.etf_rows = list(db.execute(
            select(EtfMetadataHistory).where(
                EtfMetadataHistory.market == market,
                EtfMetadataHistory.effective_date <= end_date,
            ).order_by(EtfMetadataHistory.effective_date.asc(), EtfMetadataHistory.id.asc())
        ).scalars())
        dataset.query_count += 3
    if include_portfolio:
        snapshot_query = select(PortfolioSnapshot).where(
                PortfolioSnapshot.snapshot_time <= end_cutoff,
                PortfolioSnapshot.status.in_(("confirmed", "CONFIRMED")),
        )
        if portfolio_id is not None:
            snapshot_query = snapshot_query.where(PortfolioSnapshot.portfolio_id == portfolio_id)
        snapshots = list(db.execute(
            snapshot_query.order_by(PortfolioSnapshot.snapshot_time.asc(), PortfolioSnapshot.id.asc())
        ).scalars())
        dataset.portfolio_snapshots = snapshots
        dataset.query_count += 1
        if snapshots:
            snapshot_ids = [row.id for row in snapshots]
            dataset.holding_rows = list(db.execute(
                select(HoldingItem).where(HoldingItem.snapshot_id.in_(snapshot_ids))
                .order_by(HoldingItem.snapshot_id.asc(), HoldingItem.code.asc(), HoldingItem.id.asc())
            ).scalars())
            dataset.query_count += 1
    return dataset


def expected_calendar_dates(dataset: RecomputePitDataset) -> list[date]:
    return [day for day in dataset.calendar_dates if dataset.start_date <= day <= dataset.end_date]


__all__ = [
    "RecomputePitDataset",
    "eod_cutoff",
    "expected_calendar_dates",
    "load_recompute_pit_dataset",
]
