"""Historical data sync, idempotent import, and lease-fenced run handling.

Backtests never call this module.  Historical data preparation is an explicit,
manual/operator step that runs before a backtest is created.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..market.codes import normalize_security_code
from .models import (
    EtfMetadataHistory,
    FundamentalReport,
    HistoricalDataSyncRun,
    PriceBasisMetadata,
    SecurityClassificationDaily,
    SecurityLifecycleEvent,
    SecurityTradingStatusDaily,
    SecurityValuationDaily,
)

SYNC_STATUSES = (
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "UNSUPPORTED",
    "INSUFFICIENT_DATA",
)
SYNC_LEASE_MINUTES = 15
SYNC_BATCH_SIZE = 1_000
MAX_SYNC_DATE_SPAN_DAYS = 1_830

# No provider adapter in the current codebase can reconstruct these histories.
# Marking them unsupported is honest; operator imports still work.
UNSUPPORTED_PROVIDERS = {
    "AUTO",
    "UNKNOWN",
    "PROVIDER",
    "EASTMONEY",
    "TENCENT",
    "MOOTDX",
}

# Non-fundamental PIT facts are only usable when the source made them
# available at a known time.  Fundamentals use published_at as their PIT
# availability field and are validated separately.
_REQUIRE_AVAILABILITY_TYPES = {
    "security_lifecycle",
    "trading_status",
    "st_classification",
    "valuation",
    "etf_metadata",
    "price_basis",
}

_KEY_FIELDS = {
    "security_lifecycle": ("market", "code", "effective_date", "event_type", "source", "source_ref"),
    "trading_status": ("market", "code", "trade_date", "source", "source_ref"),
    "st_classification": ("market", "code", "trade_date", "source", "source_ref"),
    "valuation": ("market", "code", "trade_date", "source", "source_ref"),
    "fundamentals": ("market", "code", "report_period", "report_type", "source", "source_ref"),
    "etf_metadata": ("market", "code", "effective_date", "source", "source_ref"),
    "price_basis": ("market", "code", "trade_date", "source", "source_ref"),
}

_MODELS = {
    "security_lifecycle": SecurityLifecycleEvent,
    "trading_status": SecurityTradingStatusDaily,
    "st_classification": SecurityClassificationDaily,
    "valuation": SecurityValuationDaily,
    "fundamentals": FundamentalReport,
    "etf_metadata": EtfMetadataHistory,
    "price_basis": PriceBasisMetadata,
}

_DATE_FIELDS = {
    "effective_date",
    "trade_date",
    "report_period",
    "inception_date",
}

_DATETIME_FIELDS = {
    "effective_at",
    "source_available_at",
    "captured_at",
    "ingested_at",
    "announced_at",
    "published_at",
    "valuation_effective_at",
}

_GENERATED_FIELDS = {"id", "captured_at", "ingested_at", "created_at"}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _lease() -> datetime:
    return _now() + timedelta(minutes=SYNC_LEASE_MINUTES)


def _iso(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _to_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _to_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _content_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_iso,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalise_row(
    row: Mapping[str, Any],
    *,
    data_type: str,
    market: str,
    source: str,
) -> dict[str, Any]:
    model = _MODELS[data_type]
    columns = {column.name for column in model.__table__.columns}
    payload: dict[str, Any] = {}
    for key, value in dict(row).items():
        if key not in columns:
            continue
        if key in _DATE_FIELDS:
            value = _to_date(value)
        elif key in _DATETIME_FIELDS:
            value = _to_datetime(value)
        payload[key] = value
    payload.setdefault("market", market)
    payload["market"] = str(payload["market"] or market or "CN").upper()
    if "code" in payload:
        payload["code"] = normalize_security_code(payload["code"])
    if data_type in _REQUIRE_AVAILABILITY_TYPES and not payload.get("source_available_at"):
        raise ValueError("source_available_at_required_for_pit_fact")
    if data_type == "fundamentals" and not payload.get("published_at"):
        raise ValueError("published_at_required_for_fundamental_report")
    if (
        data_type == "fundamentals"
        and int(payload.get("revision_number") or 0) > 0
        and not payload.get("source_available_at")
    ):
        raise ValueError("fundamental_revision_requires_source_available_at")
    payload.setdefault("source", source)
    payload["source"] = str(payload["source"] or source)
    payload.setdefault("quality_status", "VALID")
    if "security_type" in payload and payload["security_type"]:
        payload["security_type"] = str(payload["security_type"]).upper()
    if "event_type" in payload and payload["event_type"]:
        payload["event_type"] = str(payload["event_type"]).upper()
    if "status" in payload and payload["status"]:
        payload["status"] = str(payload["status"]).upper()
    if "classification" in payload and payload["classification"]:
        payload["classification"] = str(payload["classification"]).upper()
    if "basis" in payload and payload["basis"]:
        payload["basis"] = str(payload["basis"]).upper()
    if not payload.get("source_ref"):
        stable = {
            key: _iso(payload.get(key))
            for key in _KEY_FIELDS[data_type]
            if key not in {"source", "source_ref"} and payload.get(key) is not None
        }
        stable.update({key: _iso(payload.get(key)) for key in sorted(set(payload) - _GENERATED_FIELDS) if payload.get(key) is not None})
        payload["source_ref"] = f"auto-{_content_hash(stable)[:24]}"
    return payload


def _changed_fields(model: type, existing: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for column in model.__table__.columns:
        key = column.name
        if key in _GENERATED_FIELDS or key in _KEY_FIELDS[_model_data_type(model)]:
            continue
        if key not in payload:
            continue
        current = getattr(existing, key, None)
        incoming = payload[key]
        if current != incoming and not (current is None and incoming is None):
            changes[key] = incoming
    return changes


def _model_data_type(model: type) -> str:
    return next(key for key, value in _MODELS.items() if value is model)


def _batch_flush(db: Session, counter: Counter[str]) -> None:
    if sum(counter.values()) % SYNC_BATCH_SIZE == 0:
        db.flush()


def import_historical_facts(
    db: Session,
    *,
    data_type: str,
    rows: Iterable[Mapping[str, Any]],
    source: str,
    provider: str | None = None,
    market: str = "CN",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Idempotently import one data type.  Returns per-row counters."""

    from ..history.coverage import normalise_data_type

    data_type = normalise_data_type(data_type)
    if data_type is None:
        raise ValueError("data_type_required")
    model = _MODELS[data_type]
    key_fields = _KEY_FIELDS[data_type]
    market = str(market or "CN").upper()
    source = str(source or provider or "UNKNOWN")
    counters: Counter[str] = Counter()
    for raw in rows:
        payload = _normalise_row(
            raw,
            data_type=data_type,
            market=market,
            source=source,
        )
        filters = {
            key: payload[key]
            for key in key_fields
            if payload.get(key) is not None
        }
        existing = db.execute(
            select(model).where(
                *(getattr(model, key) == value for key, value in filters.items())
            )
        ).scalar_one_or_none()
        if existing is None:
            counters["inserted"] += 1
            if not dry_run:
                db.add(model(**payload))
                _batch_flush(db, counters)
            continue
        if dry_run:
            counters["skipped"] += 1
            continue
        changes = _changed_fields(model, existing, payload)
        if not changes:
            counters["skipped"] += 1
            continue
        revised = dict(payload)
        base_ref = str(existing.source_ref or payload.get("source_ref") or "auto")
        content = {
            key: value
            for key, value in payload.items()
            if key not in _GENERATED_FIELDS
            and key not in {"source_ref", "revision_number", "is_restatement"}
        }
        derived_ref = f"{base_ref}#{_content_hash(content)[:16]}"
        revised["source_ref"] = derived_ref
        if data_type == "fundamentals":
            revision_filters = {
                key: value
                for key, value in filters.items()
                if key != "source_ref"
            }
            max_revision = int(
                db.execute(
                    select(func.max(FundamentalReport.revision_number)).where(
                        *(
                            getattr(FundamentalReport, key) == value
                            for key, value in revision_filters.items()
                        )
                    )
                ).scalar() or 0
            )
            revised["revision_number"] = int(revised.get("revision_number") or 0) or max_revision + 1
            if int(revised["revision_number"]) > 0:
                if not revised.get("source_available_at"):
                    raise ValueError("fundamental_revision_requires_source_available_at")
                revised["is_restatement"] = True
        revision_filters = dict(filters)
        revision_filters["source_ref"] = derived_ref
        revision_exists = db.execute(
            select(model).where(
                *(
                    getattr(model, key) == value
                    for key, value in revision_filters.items()
                )
            )
        ).scalar_one_or_none()
        if revision_exists is not None:
            counters["skipped"] += 1
            continue
        counters["inserted"] += 1
        db.add(model(**revised))
        _batch_flush(db, counters)
    if not dry_run:
        db.flush()
    return {
        "data_type": data_type,
        "dry_run": dry_run,
        "inserted_count": int(counters["inserted"]),
        "updated_count": int(counters["updated"]),
        "skipped_count": int(counters["skipped"]),
        "failed_count": 0,
        "provider": provider,
        "source": source,
        "market": market,
    }


def enqueue_history_sync(
    db: Session,
    *,
    data_type: str,
    start_date: date | None = None,
    end_date: date | None = None,
    market: str = "CN",
    provider: str | None = None,
    source: str | None = None,
) -> HistoricalDataSyncRun:
    from ..history.coverage import normalise_data_type

    data_type = normalise_data_type(data_type)
    if data_type is None:
        raise ValueError("data_type_required")
    if start_date is not None and end_date is not None:
        if start_date > end_date:
            raise ValueError("start_date_must_not_exceed_end_date")
        if (end_date - start_date).days > MAX_SYNC_DATE_SPAN_DAYS:
            raise ValueError("history_sync_date_range_too_large")
    row = HistoricalDataSyncRun(
        data_type=data_type,
        market=str(market or "CN").upper(),
        start_date=start_date,
        end_date=end_date,
        status="QUEUED",
        progress_percent=0,
        provider=provider,
        source=source,
        attempt_count=1,
    )
    db.add(row)
    db.flush()
    return row


def claim_history_sync_run(
    db: Session,
    run_id: int,
    generation: int | None = None,
) -> HistoricalDataSyncRun | None:
    row = db.get(HistoricalDataSyncRun, run_id)
    if row is None:
        return None
    expected_generation = generation if generation is not None else int(row.attempt_count or 1)
    updated = db.execute(
        update(HistoricalDataSyncRun)
        .where(
            HistoricalDataSyncRun.id == run_id,
            HistoricalDataSyncRun.status == "QUEUED",
            HistoricalDataSyncRun.attempt_count == expected_generation,
        )
        .values(
            status="RUNNING",
            started_at=_now(),
            lease_expires_at=_lease(),
            progress_percent=5,
        )
    ).rowcount
    if not updated:
        db.rollback()
        return None
    db.refresh(row)
    return row


def heartbeat_history_sync_run(
    db: Session,
    run_id: int,
    generation: int,
) -> bool:
    updated = db.execute(
        update(HistoricalDataSyncRun)
        .where(
            HistoricalDataSyncRun.id == run_id,
            HistoricalDataSyncRun.status == "RUNNING",
            HistoricalDataSyncRun.attempt_count == generation,
        )
        .values(lease_expires_at=_lease())
    ).rowcount
    return bool(updated)


def reclaim_stale_history_sync_runs(
    db: Session,
    *,
    now: datetime | None = None,
) -> list[HistoricalDataSyncRun]:
    cutoff = _now() if now is None else now
    rows = list(db.execute(select(HistoricalDataSyncRun).where(
        HistoricalDataSyncRun.status == "RUNNING",
        HistoricalDataSyncRun.lease_expires_at.is_not(None),
        HistoricalDataSyncRun.lease_expires_at < cutoff,
    ).order_by(HistoricalDataSyncRun.id.asc())).scalars())
    reclaimed: list[HistoricalDataSyncRun] = []
    for row in rows:
        generation = int(row.attempt_count or 0)
        updated = db.execute(
            update(HistoricalDataSyncRun)
            .where(
                HistoricalDataSyncRun.id == row.id,
                HistoricalDataSyncRun.status == "RUNNING",
                HistoricalDataSyncRun.attempt_count == generation,
                HistoricalDataSyncRun.lease_expires_at < cutoff,
            )
            .values(
                status="QUEUED",
                lease_expires_at=None,
                started_at=None,
                attempt_count=generation + 1,
            )
        ).rowcount
        if updated:
            db.refresh(row)
            reclaimed.append(row)
    if reclaimed:
        db.flush()
    return reclaimed


def complete_history_sync_run(
    db: Session,
    run: HistoricalDataSyncRun,
    generation: int,
    *,
    summary: Mapping[str, Any],
) -> bool:
    updated = db.execute(
        update(HistoricalDataSyncRun)
        .where(
            HistoricalDataSyncRun.id == run.id,
            HistoricalDataSyncRun.status == "RUNNING",
            HistoricalDataSyncRun.attempt_count == generation,
        )
        .values(
            status="COMPLETED",
            progress_percent=100,
            completed_at=_now(),
            lease_expires_at=None,
            fetched_count=int(summary.get("fetched_count") or 0),
            inserted_count=int(summary.get("inserted_count") or 0),
            updated_count=int(summary.get("updated_count") or 0),
            skipped_count=int(summary.get("skipped_count") or 0),
            failed_count=int(summary.get("failed_count") or 0),
            error_summary=summary.get("error_summary"),
            source_lineage_json=summary.get("source_lineage_json"),
            coverage_summary_json=summary.get("coverage_summary_json"),
        )
    ).rowcount
    if not updated:
        db.rollback()
        return False
    db.refresh(run)
    return True


def fail_history_sync_run(
    db: Session,
    run: HistoricalDataSyncRun,
    generation: int,
    *,
    error_summary: str,
) -> bool:
    updated = db.execute(
        update(HistoricalDataSyncRun)
        .where(
            HistoricalDataSyncRun.id == run.id,
            HistoricalDataSyncRun.status == "RUNNING",
            HistoricalDataSyncRun.attempt_count == generation,
        )
        .values(
            status="FAILED",
            completed_at=_now(),
            lease_expires_at=None,
            error_summary=error_summary,
        )
    ).rowcount
    if not updated:
        db.rollback()
        return False
    db.refresh(run)
    return True


def mark_history_sync_unsupported(
    db: Session,
    run: HistoricalDataSyncRun,
    generation: int,
    *,
    reason: str,
) -> bool:
    updated = db.execute(
        update(HistoricalDataSyncRun)
        .where(
            HistoricalDataSyncRun.id == run.id,
            HistoricalDataSyncRun.status == "RUNNING",
            HistoricalDataSyncRun.attempt_count == generation,
        )
        .values(
            status="UNSUPPORTED",
            completed_at=_now(),
            lease_expires_at=None,
            error_summary=reason,
        )
    ).rowcount
    if not updated:
        db.rollback()
        return False
    db.refresh(run)
    return True


def cancel_history_sync_run(
    db: Session,
    run_id: int,
) -> HistoricalDataSyncRun | None:
    row = db.get(HistoricalDataSyncRun, run_id)
    if row is None:
        return None
    if row.status in {"COMPLETED", "FAILED", "CANCELLED", "UNSUPPORTED", "INSUFFICIENT_DATA"}:
        return row
    row.status = "CANCELLED"
    row.completed_at = _now()
    row.lease_expires_at = None
    db.flush()
    return row


def execute_history_sync_run(
    db: Session,
    *,
    run_id: int,
    generation: int,
    rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute one claimed run.  No provider is called here."""

    run = db.get(HistoricalDataSyncRun, run_id)
    if run is None:
        raise ValueError("history_sync_run_not_found")
    if run.status != "RUNNING" or int(run.attempt_count or 0) != generation:
        raise RuntimeError("HISTORY_SYNC_LEASE_LOST")
    provider = str(run.provider or "").upper()
    rows = list(rows or [])
    if not rows and provider in UNSUPPORTED_PROVIDERS:
        mark_history_sync_unsupported(
            db,
            run,
            generation,
            reason=f"provider_{provider}_has_no_historical_adapter" if provider else "no_provider_adapter",
        )
        db.commit()
        return serialize_history_sync_run(run)
    if not rows:
        fail_history_sync_run(
            db,
            run,
            generation,
            error_summary="no_rows_provided_and_provider_cannot_supply_history",
        )
        db.commit()
        return serialize_history_sync_run(run)
    summary = import_historical_facts(
        db,
        data_type=run.data_type,
        rows=rows,
        source=run.source or run.provider or "OPERATOR_IMPORT",
        provider=run.provider,
        market=run.market,
    )
    from .coverage import historical_data_coverage

    coverage = historical_data_coverage(
        db,
        start_date=run.start_date,
        end_date=run.end_date,
        data_type=run.data_type,
        market=run.market,
    )
    complete_history_sync_run(
        db,
        run,
        generation,
        summary={
            **summary,
            "fetched_count": summary["inserted_count"] + summary["updated_count"] + summary["skipped_count"],
            "source_lineage_json": {
                "provider": run.provider,
                "source": run.source or "OPERATOR_IMPORT",
                "data_type": run.data_type,
                "market": run.market,
            },
            "coverage_summary_json": {
                "items": coverage["items"],
                "generated_at": coverage["generated_at"],
            },
        },
    )
    db.commit()
    return serialize_history_sync_run(run)


def run_history_sync(
    db: Session,
    *,
    data_type: str,
    start_date: date | None = None,
    end_date: date | None = None,
    market: str = "CN",
    provider: str | None = None,
    source: str | None = None,
    rows: Iterable[Mapping[str, Any]] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Convenience synchronous entry point for CLI/API operator imports."""

    from ..system.health import disk_status

    if str(disk_status().get("status") or "").upper() == "BLOCKED":
        raise RuntimeError("DISK_CRITICAL_HISTORY_BACKFILL_BLOCKED")
    run = enqueue_history_sync(
        db,
        data_type=data_type,
        start_date=start_date,
        end_date=end_date,
        market=market,
        provider=provider,
        source=source,
    )
    claimed = claim_history_sync_run(db, run.id, generation=int(run.attempt_count or 1))
    if claimed is None:
        db.rollback()
        raise RuntimeError("HISTORY_SYNC_CLAIM_CONFLICT")
    if dry_run:
        summary = import_historical_facts(
            db,
            data_type=claimed.data_type,
            rows=list(rows or []),
            source=source or provider or "OPERATOR_IMPORT",
            provider=provider,
            market=market,
            dry_run=True,
        )
        complete_history_sync_run(
            db,
            claimed,
            int(claimed.attempt_count or 1),
            summary={
                **summary,
                "fetched_count": sum(
                    summary[key] for key in ("inserted_count", "updated_count", "skipped_count")
                ),
                "source_lineage_json": {"dry_run": True},
            },
        )
        db.commit()
        return serialize_history_sync_run(claimed)
    return execute_history_sync_run(
        db,
        run_id=claimed.id,
        generation=int(claimed.attempt_count or 1),
        rows=rows,
    )


def serialize_history_sync_run(run: HistoricalDataSyncRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "data_type": run.data_type,
        "market": run.market,
        "start_date": run.start_date.isoformat() if run.start_date else None,
        "end_date": run.end_date.isoformat() if run.end_date else None,
        "status": run.status,
        "progress_percent": run.progress_percent,
        "fetched_count": run.fetched_count,
        "inserted_count": run.inserted_count,
        "updated_count": run.updated_count,
        "skipped_count": run.skipped_count,
        "failed_count": run.failed_count,
        "provider": run.provider,
        "source": run.source,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "lease_expires_at": run.lease_expires_at.isoformat() if run.lease_expires_at else None,
        "attempt_count": run.attempt_count,
        "error_summary": run.error_summary,
        "source_lineage_json": run.source_lineage_json,
        "coverage_summary_json": run.coverage_summary_json,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


__all__ = [
    "SYNC_STATUSES",
    "UNSUPPORTED_PROVIDERS",
    "cancel_history_sync_run",
    "claim_history_sync_run",
    "complete_history_sync_run",
    "enqueue_history_sync",
    "execute_history_sync_run",
    "fail_history_sync_run",
    "heartbeat_history_sync_run",
    "import_historical_facts",
    "mark_history_sync_unsupported",
    "reclaim_stale_history_sync_runs",
    "run_history_sync",
    "serialize_history_sync_run",
]
