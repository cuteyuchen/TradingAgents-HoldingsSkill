"""Read ledger facts as they were known, including later revisions and voids."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..history.time import to_utc_naive
from ..portfolio_models import TradeLedgerEntry, TradeLedgerRevision


def ledger_facts_at(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime) -> list[SimpleNamespace]:
    cutoff = to_utc_naive(as_of)
    entries = db.scalars(select(TradeLedgerEntry).where(
        TradeLedgerEntry.user_id == user_id,
        TradeLedgerEntry.portfolio_id == portfolio_id,
        TradeLedgerEntry.created_at <= cutoff,
    )).all()
    revisions = db.scalars(select(TradeLedgerRevision).where(
        TradeLedgerRevision.ledger_entry_id.in_([entry.id for entry in entries]),
        TradeLedgerRevision.created_at > cutoff,
    ).order_by(TradeLedgerRevision.revision_no.desc())).all()
    by_entry: dict[int, list] = {}
    for revision in revisions:
        by_entry.setdefault(revision.ledger_entry_id, []).append(revision)
    facts = []
    for entry in entries:
        values = {column.name: getattr(entry, column.name) for column in TradeLedgerEntry.__table__.columns}
        for revision in by_entry.get(entry.id, []):
            change = revision.changes_json or {}
            values.update(change.get("before") or {})
            if "before_status" in change:
                values["status"] = change["before_status"]
            if "before_analysis_run_id" in change:
                values["analysis_run_id"] = change["before_analysis_run_id"]
        for key in ("executed_at", "available_at", "created_at", "updated_at"):
            if isinstance(values.get(key), str):
                values[key] = to_utc_naive(datetime.fromisoformat(values[key]))
        if isinstance(values.get("trade_date"), str):
            values["trade_date"] = date.fromisoformat(values["trade_date"])
        if (values["status"] == "CONFIRMED" and values.get("available_at") is not None
                and values["available_at"] <= cutoff and values["executed_at"] <= cutoff):
            facts.append(SimpleNamespace(**values))
    return sorted(facts, key=lambda entry: (entry.executed_at, entry.id))
