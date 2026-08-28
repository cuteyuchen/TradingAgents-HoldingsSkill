"""Phase L availability items for the Phase I replay manifest."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .coverage import historical_data_coverage
from .models import (
    EtfMetadataHistory,
    FundamentalReport,
    PriceBasisMetadata,
    SecurityClassificationDaily,
    SecurityLifecycleEvent,
    SecurityTradingStatusDaily,
    SecurityValuationDaily,
)


def _coverage_status(status: str, *, leakage_blocked: bool = False) -> str:
    if status in {"FULL", "PARTIAL", "DATA_GAP", "UNSUPPORTED"}:
        return status
    return "LEAKAGE_BLOCKED" if leakage_blocked else "PARTIAL"


def _capability(status: str) -> str:
    if status == "FULL":
        return "FULL"
    if status == "PARTIAL":
        return "PARTIAL"
    return "DATA_GAP"


def pit_recompute_gate(
    db: Session,
    *,
    scope: str,
    start_date: date,
    end_date: date,
    market: str = "CN",
) -> dict[str, Any]:
    """Fail-closed gate for DETERMINISTIC_RECOMPUTE.

    Daily state tables must cover the whole requested trading range.  Event
    tables must contain rows (a sparse event calendar cannot prove absence of
    change).  Missing rows always block deterministic recompute.
    """

    items = history_manifest_items(
        db,
        start_date=start_date,
        end_date=end_date,
        market=market,
    )
    if items is None:
        return {
            "status": "LEAKAGE_BLOCKED",
            "reason": "historical_pit_tables_unavailable",
            "missing_inputs": ["security_lifecycle", "trading_status", "st_classification", "valuation", "fundamentals", "price_basis"],
        }
    scope = str(scope or "MARKET").upper()
    required: dict[str, list[tuple[str, str]]] = {
        "MARKET": [
            ("historical_security_state", "ROWS"),
            ("historical_trading_status", "DAILY"),
            ("historical_st_state", "DAILY"),
        ],
        "CANDIDATE": [
            ("historical_security_state", "ROWS"),
            ("historical_trading_status", "DAILY"),
            ("historical_st_state", "DAILY"),
            ("historical_valuation", "DAILY"),
            ("fundamental_publication", "PUBLICATION"),
            ("price_basis", "DAILY"),
        ],
        "BAR_FACTOR": [
            ("historical_security_state", "ROWS"),
            ("price_basis", "DAILY"),
        ],
        "PORTFOLIO_DECISION": [],
        "MEMORY_DECISION": [],
    }
    checks = required.get(scope, required["MARKET"])
    missing: list[str] = []
    partial: list[str] = []
    for key, requirement in checks:
        item = items.get(key) or {}
        status = str(item.get("status") or "DATA_GAP")
        if requirement == "DAILY" and status != "FULL":
            if status in {"DATA_GAP", "LEAKAGE_BLOCKED", "UNSUPPORTED"}:
                missing.append(key)
            else:
                partial.append(key)
        elif requirement == "ROWS" and status == "DATA_GAP":
            missing.append(key)
        elif requirement == "PUBLICATION" and status != "FULL":
            if status in {"DATA_GAP", "LEAKAGE_BLOCKED", "UNSUPPORTED"}:
                missing.append(key)
            else:
                partial.append(key)
    if missing:
        return {
            "status": "DATA_GAP" if any(
                str((items.get(key) or {}).get("status")) == "DATA_GAP"
                for key in missing
            ) else "LEAKAGE_BLOCKED",
            "reason": "missing_required_pit_inputs",
            "missing_inputs": missing,
            "partial_inputs": partial,
        }
    if partial:
        return {
            "status": "PARTIAL",
            "reason": "partial_required_pit_inputs",
            "missing_inputs": [],
            "partial_inputs": partial,
        }
    return {
        "status": "FULL",
        "reason": "required_pit_inputs_available",
        "missing_inputs": [],
        "partial_inputs": [],
    }


def history_manifest_items(
    db: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    market: str = "CN",
) -> dict[str, Any] | None:
    """Return manifest entries only when the Phase L tables exist."""

    table_names = set(inspect(db.get_bind()).get_table_names())
    required = {
        "security_lifecycle_events",
        "security_trading_status_daily",
        "security_classification_daily",
        "security_valuation_daily",
        "fundamental_reports",
        "etf_metadata_history",
        "price_basis_metadata",
    }
    if not required.issubset(table_names):
        return None

    coverage = historical_data_coverage(
        db,
        start_date=start_date,
        end_date=end_date,
        market=market,
    )
    by_type = {item["data_type"]: item for item in coverage["items"]}

    lifecycle = by_type["security_lifecycle"]
    trading = by_type["trading_status"]
    classification = by_type["st_classification"]
    valuation = by_type["valuation"]
    fundamentals = by_type["fundamentals"]
    etf = by_type["etf_metadata"]
    price_basis = by_type["price_basis"]

    lifecycle_status = _coverage_status(lifecycle["status"], leakage_blocked=True)
    trading_status = _coverage_status(trading["status"])
    classification_status = _coverage_status(classification["status"])
    valuation_status = _coverage_status(valuation["status"])
    fundamentals_status = _coverage_status(fundamentals["status"])
    etf_status = _coverage_status(etf["status"])
    price_basis_status = _coverage_status(price_basis["status"])

    pit_statuses = [lifecycle_status, trading_status, classification_status]
    if all(item == "FULL" for item in pit_statuses):
        universe_status = "FULL"
    elif any(item == "DATA_GAP" for item in pit_statuses):
        universe_status = "DATA_GAP"
    elif any(item in {"PARTIAL", "LEAKAGE_BLOCKED"} for item in pit_statuses):
        universe_status = "PARTIAL"
    else:
        universe_status = "PARTIAL"

    items: dict[str, Any] = {
        "historical_security_state": {
            "name": "historical_security_state",
            "status": lifecycle_status,
            "row_count": lifecycle["row_count"],
            "earliest_supported_at": lifecycle["earliest_supported_at"],
            "latest_supported_at": lifecycle["latest_supported_at"],
            "coverage": lifecycle["coverage"],
            "reason": lifecycle["reason"],
            "capabilities": {
                "listing_delisting_pit": _capability(lifecycle_status),
                "DETERMINISTIC_RECOMPUTE": _capability(lifecycle_status),
            },
            "source_fields": [
                "market",
                "code",
                "event_type",
                "effective_date",
                "source_available_at",
                "source",
                "source_ref",
                "quality_status",
            ],
            "availability_field": "source_available_at",
            "lineage_field": "source_ref",
        },
        "security_lifecycle": {
            "name": "security_lifecycle",
            "status": lifecycle_status,
            "row_count": lifecycle["row_count"],
            "earliest_supported_at": lifecycle["earliest_supported_at"],
            "latest_supported_at": lifecycle["latest_supported_at"],
            "coverage": lifecycle["coverage"],
            "reason": lifecycle["reason"],
            "capabilities": {
                "listing_age": _capability(lifecycle_status),
                "delist_survivorship": _capability(lifecycle_status),
                "DETERMINISTIC_RECOMPUTE": _capability(lifecycle_status),
            },
            "source_fields": [
                "market",
                "code",
                "event_type",
                "effective_date",
                "source_available_at",
                "source",
                "source_ref",
                "quality_status",
            ],
            "availability_field": "source_available_at",
            "lineage_field": "source_ref",
        },
        "historical_trading_status": {
            "name": "historical_trading_status",
            "status": trading_status,
            "row_count": trading["row_count"],
            "distinct_trade_dates": trading["known_dates"],
            "earliest_supported_at": trading["earliest_supported_at"],
            "latest_supported_at": trading["latest_supported_at"],
            "coverage": trading["coverage"],
            "reason": trading["reason"],
            "capabilities": {
                "suspension_pit": _capability(trading_status),
                "DETERMINISTIC_RECOMPUTE": _capability(trading_status),
            },
            "source_fields": [
                "market",
                "code",
                "trade_date",
                "status",
                "source_available_at",
                "source",
                "source_ref",
                "quality_status",
            ],
            "availability_field": "source_available_at",
            "lineage_field": "source_ref",
        },
        "historical_st_state": {
            "name": "historical_st_state",
            "status": classification_status,
            "row_count": classification["row_count"],
            "distinct_trade_dates": classification["known_dates"],
            "earliest_supported_at": classification["earliest_supported_at"],
            "latest_supported_at": classification["latest_supported_at"],
            "coverage": classification["coverage"],
            "reason": classification["reason"],
            "capabilities": {
                "st_pit": _capability(classification_status),
                "DETERMINISTIC_RECOMPUTE": _capability(classification_status),
            },
            "source_fields": [
                "market",
                "code",
                "trade_date",
                "classification",
                "is_name_derived",
                "source_available_at",
                "source",
                "source_ref",
                "quality_status",
            ],
            "availability_field": "source_available_at",
            "lineage_field": "source_ref",
        },
        "historical_valuation": {
            "name": "historical_valuation",
            "status": valuation_status,
            "row_count": valuation["row_count"],
            "distinct_trade_dates": valuation["known_dates"],
            "earliest_supported_at": valuation["earliest_supported_at"],
            "latest_supported_at": valuation["latest_supported_at"],
            "coverage": valuation["coverage"],
            "reason": valuation["reason"],
            "capabilities": {
                "valuation_pit": _capability(valuation_status),
                "DETERMINISTIC_RECOMPUTE": _capability(valuation_status),
            },
            "source_fields": [
                "market",
                "code",
                "trade_date",
                "pe_ttm",
                "pb",
                "ps_ttm",
                "dividend_yield",
                "source_available_at",
                "source",
                "source_ref",
                "quality_status",
            ],
            "availability_field": "source_available_at",
            "lineage_field": "source_ref",
        },
        "valuation": {
            "name": "valuation",
            "status": valuation_status,
            "row_count": valuation["row_count"],
            "distinct_trade_dates": valuation["known_dates"],
            "earliest_supported_at": valuation["earliest_supported_at"],
            "latest_supported_at": valuation["latest_supported_at"],
            "coverage": valuation["coverage"],
            "reason": valuation["reason"],
            "capabilities": {"DETERMINISTIC_RECOMPUTE": _capability(valuation_status)},
        },
        "fundamental_publication": {
            "name": "fundamental_publication",
            "status": fundamentals_status,
            "row_count": fundamentals["row_count"],
            "known_publications": fundamentals["known_publications"],
            "missing_publications": fundamentals["missing_publications"],
            "earliest_supported_at": fundamentals["earliest_supported_at"],
            "latest_supported_at": fundamentals["latest_supported_at"],
            "coverage": fundamentals["coverage"],
            "reason": fundamentals["reason"],
            "capabilities": {
                "publication_time_pit": _capability(fundamentals_status),
                "restatement_history": "PARTIAL" if fundamentals["row_count"] else "DATA_GAP",
                "DETERMINISTIC_RECOMPUTE": _capability(fundamentals_status),
            },
            "source_fields": [
                "market",
                "code",
                "report_period",
                "report_type",
                "published_at",
                "revision_number",
                "is_restatement",
                "source",
                "source_ref",
                "quality_status",
            ],
            "availability_field": "published_at",
            "lineage_field": "source_ref",
        },
        "fundamentals": {
            "name": "fundamentals",
            "status": fundamentals_status,
            "row_count": fundamentals["row_count"],
            "known_publications": fundamentals["known_publications"],
            "missing_publications": fundamentals["missing_publications"],
            "earliest_supported_at": fundamentals["earliest_supported_at"],
            "latest_supported_at": fundamentals["latest_supported_at"],
            "coverage": fundamentals["coverage"],
            "reason": fundamentals["reason"],
            "capabilities": {"DETERMINISTIC_RECOMPUTE": _capability(fundamentals_status)},
        },
        "etf_metadata": {
            "name": "etf_metadata",
            "status": etf_status,
            "row_count": etf["row_count"],
            "distinct_codes": etf["distinct_codes"],
            "earliest_supported_at": etf["earliest_supported_at"],
            "latest_supported_at": etf["latest_supported_at"],
            "coverage": etf["coverage"],
            "reason": etf["reason"],
            "capabilities": {
                "etf_metadata_history": "PARTIAL" if etf["row_count"] else "DATA_GAP",
                "current_backfill": "FORBIDDEN",
                "DETERMINISTIC_RECOMPUTE": "PARTIAL" if etf["row_count"] else "DATA_GAP",
            },
            "source_fields": [
                "market",
                "code",
                "effective_date",
                "category",
                "index_code",
                "benchmark_code",
                "fund_type",
                "source_available_at",
                "source",
                "source_ref",
                "quality_status",
            ],
            "availability_field": "source_available_at",
            "lineage_field": "source_ref",
        },
        "price_basis": {
            "name": "price_basis",
            "status": price_basis_status,
            "row_count": price_basis["row_count"],
            "distinct_trade_dates": price_basis["known_dates"],
            "earliest_supported_at": price_basis["earliest_supported_at"],
            "latest_supported_at": price_basis["latest_supported_at"],
            "coverage": price_basis["coverage"],
            "reason": price_basis["reason"],
            "capabilities": {
                "daily_bars": "QFQ_DECLARED",
                "source_availability": _capability(price_basis_status),
                "DETERMINISTIC_RECOMPUTE": _capability(price_basis_status),
            },
            "source_fields": [
                "market",
                "code",
                "trade_date",
                "basis",
                "adjustment_factor",
                "source_available_at",
                "source",
                "source_ref",
                "quality_status",
            ],
            "availability_field": "source_available_at",
            "lineage_field": "source_ref",
        },
        "historical_universe": {
            "name": "historical_universe",
            "status": universe_status,
            "row_count": lifecycle["row_count"],
            "coverage": None,
            "reason": (
                "ST, suspension, delisting and active status now have effective-dated history"
                if universe_status != "DATA_GAP"
                else "historical lifecycle/status rows missing in requested range"
            ),
            "capabilities": {
                "point_in_time_universe": _capability(universe_status),
                "DETERMINISTIC_RECOMPUTE": _capability(universe_status),
            },
        },
        "survivorship": {
            "status": universe_status,
            "survivorship_status": (
                "PIT_LIFECYCLE" if lifecycle_status != "DATA_GAP" else "DATA_GAP"
            ),
            "reason": (
                "historical lifecycle facts permit delisted securities to remain in pre-delisting history"
                if lifecycle_status != "DATA_GAP"
                else "no historical lifecycle facts"
            ),
        },
        "point_in_time_universe": {
            "status": universe_status,
            "universe_version": "pit-universe-v1",
            "reason": (
                "historical universe resolver is available"
                if universe_status != "DATA_GAP"
                else "historical state unavailable for requested range"
            ),
            "capabilities": {"DETERMINISTIC_RECOMPUTE": _capability(universe_status)},
        },
        "factor_point_in_time": {
            "production_snapshots": "PARTIAL",
            "fundamentals": fundamentals_status,
            "valuation": valuation_status,
            "security_lifecycle": lifecycle_status,
            "trading_status": trading_status,
            "st_classification": classification_status,
        },
        "etf_constituents": {
            "status": "UNSUPPORTED",
            "reason": "historical ETF constituent snapshots are not persisted",
            "capabilities": {"DETERMINISTIC_RECOMPUTE": "UNSUPPORTED"},
        },
        "industry": {
            "status": "UNSUPPORTED",
            "reason": "only current SecurityMaster classification is available; historical classification is not persisted",
            "capabilities": {"DETERMINISTIC_RECOMPUTE": "UNSUPPORTED"},
        },
    }
    return items


__all__ = ["history_manifest_items", "pit_recompute_gate"]
