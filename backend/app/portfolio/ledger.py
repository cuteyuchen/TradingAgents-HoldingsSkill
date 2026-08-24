"""Immutable-by-revision Trade Ledger services."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market.codes import normalize_security_code
from ..market_models import SecurityMaster
from ..portfolio_models import TradeLedgerEntry, TradeLedgerRevision

ENTRY_TYPES = frozenset({
    "TRADE", "CASH_IN", "CASH_OUT", "DIVIDEND", "FEE", "TAX",
    "TRANSFER_IN", "TRANSFER_OUT", "CORPORATE_ACTION", "OTHER",
})
LEDGER_SOURCES = frozenset({"MANUAL", "CSV_IMPORT", "BROKER_IMPORT", "SNAPSHOT_RECONCILIATION"})
LEDGER_STATUSES = frozenset({"CONFIRMED", "PENDING_REVIEW", "VOIDED"})
MATERIALIZED_FIELDS = frozenset({
    "entry_type", "security_code", "security_name", "side", "quantity", "price",
    "gross_amount", "fees", "taxes", "net_amount", "currency", "executed_at",
    "trade_date", "available_at", "source", "source_ref", "broker_order_id", "notes",
})


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    raise ValueError("executed_at is required")


def _as_date(value: Any, fallback: datetime) -> date:
    if isinstance(value, date):
        return value
    return fallback.date()


def _positive(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is required") from exc
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric ledger field is invalid") from exc


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _security_exists(db: Session, code: str) -> bool:
    return db.execute(
        select(SecurityMaster.id).where(SecurityMaster.market == "CN", SecurityMaster.code == code).limit(1)
    ).scalar_one_or_none() is not None


def _entry_values(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    entry_type = str(payload.get("entry_type") or "").upper()
    if entry_type not in ENTRY_TYPES:
        raise ValueError("unsupported entry_type")
    source = str(payload.get("source") or "MANUAL").upper()
    if source not in LEDGER_SOURCES:
        raise ValueError("unsupported source")
    executed_at = _as_datetime(payload.get("executed_at"))
    trade_date = _as_date(payload.get("trade_date"), executed_at)
    available_at = payload.get("available_at")
    available_at = _as_datetime(available_at) if available_at is not None else executed_at
    code = normalize_security_code(payload.get("security_code")) or None
    side = str(payload.get("side") or "").upper() or None
    quantity = _optional_number(payload.get("quantity"))
    price = _optional_number(payload.get("price"))
    status = str(payload.get("status") or "CONFIRMED").upper()
    if status not in LEDGER_STATUSES - {"VOIDED"}:
        raise ValueError("new ledger entry must be CONFIRMED or PENDING_REVIEW")
    if entry_type == "TRADE":
        if not code or side not in {"BUY", "SELL"}:
            raise ValueError("TRADE requires security_code and BUY or SELL side")
        quantity = _positive(quantity, "quantity")
        price = _positive(price, "price")
        if not _security_exists(db, code):
            status = "PENDING_REVIEW"
    gross_amount = _optional_number(payload.get("gross_amount"))
    if entry_type == "TRADE" and gross_amount is None:
        gross_amount = quantity * price  # type: ignore[operator]
    fees = _optional_number(payload.get("fees"))
    taxes = _optional_number(payload.get("taxes"))
    net_amount = _optional_number(payload.get("net_amount"))
    if net_amount is None and gross_amount is not None:
        costs = (fees or 0.0) + (taxes or 0.0)
        net_amount = gross_amount + costs if side == "BUY" else gross_amount - costs
    return {
        "entry_type": entry_type,
        "security_code": code,
        "security_name": payload.get("security_name"),
        "side": side,
        "quantity": quantity,
        "price": price,
        "gross_amount": gross_amount,
        "fees": fees,
        "taxes": taxes,
        "net_amount": net_amount,
        "currency": str(payload.get("currency") or "CNY").upper(),
        "executed_at": executed_at,
        "trade_date": trade_date,
        "available_at": available_at,
        "source": source,
        "source_ref": payload.get("source_ref"),
        "broker_order_id": payload.get("broker_order_id"),
        "idempotency_key": payload.get("idempotency_key"),
        "status": status,
        "notes": payload.get("notes"),
    }


def create_ledger_entry(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    payload: dict[str, Any],
) -> tuple[TradeLedgerEntry, bool]:
    """Create an actual-action fact, returning an existing idempotent row when present."""

    key = str(payload.get("idempotency_key") or "").strip() or None
    if key:
        existing = db.execute(
            select(TradeLedgerEntry).where(
                TradeLedgerEntry.portfolio_id == portfolio_id,
                TradeLedgerEntry.idempotency_key == key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
    values = _entry_values(db, payload)
    entry = TradeLedgerEntry(
        user_id=user_id,
        portfolio_id=portfolio_id,
        analysis_run_id=payload.get("analysis_run_id"),
        trigger_event_id=payload.get("trigger_event_id"),
        **values,
    )
    db.add(entry)
    db.flush()
    return entry, True


def revise_ledger_entry(
    db: Session,
    *,
    entry: TradeLedgerEntry,
    user_id: int,
    changes: dict[str, Any],
    reason: str,
) -> TradeLedgerEntry:
    """Record an append-only revision before updating the materialized view."""

    if entry.status == "VOIDED":
        raise ValueError("voided ledger entry cannot be revised")
    if not reason or not reason.strip():
        raise ValueError("revision reason is required")
    unknown = set(changes) - MATERIALIZED_FIELDS
    if unknown:
        raise ValueError(f"unsupported revision fields: {sorted(unknown)}")
    merged = {field: getattr(entry, field) for field in MATERIALIZED_FIELDS}
    merged.update(changes)
    values = _entry_values(db, merged)
    values.pop("idempotency_key", None)
    before = {field: getattr(entry, field) for field in MATERIALIZED_FIELDS}
    revision_no = (db.scalar(
        select(func.max(TradeLedgerRevision.revision_no)).where(TradeLedgerRevision.ledger_entry_id == entry.id)
    ) or 0) + 1
    db.add(TradeLedgerRevision(
        ledger_entry_id=entry.id,
        revision_no=revision_no,
        changes_json=_json_safe({"before": before, "changes": changes, "after": values}),
        reason=reason.strip(),
        created_by_user_id=user_id,
    ))
    for field, value in values.items():
        if field in MATERIALIZED_FIELDS:
            setattr(entry, field, value)
    db.flush()
    return entry


def void_ledger_entry(db: Session, *, entry: TradeLedgerEntry, user_id: int, reason: str) -> TradeLedgerEntry:
    if entry.status == "VOIDED":
        return entry
    if not reason or not reason.strip():
        raise ValueError("void reason is required")
    revision_no = (db.scalar(
        select(func.max(TradeLedgerRevision.revision_no)).where(TradeLedgerRevision.ledger_entry_id == entry.id)
    ) or 0) + 1
    db.add(TradeLedgerRevision(
        ledger_entry_id=entry.id,
        revision_no=revision_no,
        changes_json={"before_status": entry.status, "after_status": "VOIDED"},
        reason=reason.strip(),
        created_by_user_id=user_id,
    ))
    entry.status = "VOIDED"
    db.flush()
    return entry


def confirm_ledger_entry(db: Session, *, entry: TradeLedgerEntry, user_id: int, reason: str) -> TradeLedgerEntry:
    """Confirm a security-master-pending fact through an auditable revision."""

    if entry.status == "VOIDED":
        raise ValueError("voided ledger entry cannot be confirmed")
    if entry.status != "PENDING_REVIEW":
        raise ValueError("only pending review ledger entries can be confirmed")
    if not reason or not reason.strip():
        raise ValueError("confirmation reason is required")
    revision_no = (db.scalar(
        select(func.max(TradeLedgerRevision.revision_no)).where(TradeLedgerRevision.ledger_entry_id == entry.id)
    ) or 0) + 1
    db.add(TradeLedgerRevision(
        ledger_entry_id=entry.id,
        revision_no=revision_no,
        changes_json={"before_status": "PENDING_REVIEW", "after_status": "CONFIRMED"},
        reason=reason.strip(),
        created_by_user_id=user_id,
    ))
    entry.status = "CONFIRMED"
    db.flush()
    return entry


def transaction_cost_estimate(*, side: str | None, gross_amount: float | None, commission_bps: float | None, minimum_commission: float | None, sell_tax_bps: float | None) -> float | None:
    """Return a transparent estimate only when the configured inputs exist."""

    if gross_amount is None or commission_bps is None or minimum_commission is None:
        return None
    commission = max(float(gross_amount) * commission_bps / 10_000, minimum_commission)
    sell_tax = float(gross_amount) * sell_tax_bps / 10_000 if str(side or "").upper() == "SELL" and sell_tax_bps is not None else 0.0
    return commission + sell_tax


__all__ = [
    "ENTRY_TYPES",
    "LEDGER_SOURCES",
    "LEDGER_STATUSES",
    "create_ledger_entry",
    "confirm_ledger_entry",
    "revise_ledger_entry",
    "transaction_cost_estimate",
    "void_ledger_entry",
]
