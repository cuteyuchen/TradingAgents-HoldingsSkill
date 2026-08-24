"""Confirmed snapshot comparison and ledger reconciliation."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..portfolio_models import PortfolioSnapshotDiff, TradeLedgerEntry
from ..v2_models import PortfolioSnapshot
from .config import PORTFOLIO_DIFF_VERSION


def snapshot_cash(snapshot: PortfolioSnapshot) -> float | None:
    return snapshot.broker_available_cash if snapshot.broker_available_cash is not None else snapshot.corrected_unused_funds


def _holding_key(item: Any) -> str:
    return str(item.code or "").strip() or f"name:{str(item.name or '').strip()}"


def _holdings(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    return {_holding_key(item): item for item in snapshot.holdings if _holding_key(item) != "name:"}


def calculate_snapshot_diff(before: PortfolioSnapshot, after: PortfolioSnapshot) -> dict[str, Any]:
    """Compare quantities only; market-value movement never implies a trade."""

    before_rows = _holdings(before)
    after_rows = _holdings(after)
    keys = sorted(set(before_rows) | set(after_rows))
    positions: list[dict[str, Any]] = []
    for key in keys:
        old = before_rows.get(key)
        new = after_rows.get(key)
        old_qty = old.qty if old is not None else 0.0
        new_qty = new.qty if new is not None else 0.0
        qty_delta = (new_qty or 0.0) - (old_qty or 0.0)
        direction = "BUY" if qty_delta > 0 else "SELL" if qty_delta < 0 else None
        positions.append({
            "code": (new.code if new is not None else old.code) if (new is not None or old is not None) else None,
            "name": (new.name if new is not None else old.name) if (new is not None or old is not None) else None,
            "before_qty": old_qty,
            "after_qty": new_qty,
            "qty_delta": qty_delta,
            "before_available_qty": old.available_qty if old is not None else None,
            "after_available_qty": new.available_qty if new is not None else None,
            "available_qty_delta": ((new.available_qty or 0.0) - (old.available_qty or 0.0)) if new is not None and old is not None else None,
            "before_cost": old.cost if old is not None else None,
            "after_cost": new.cost if new is not None else None,
            "before_weight": old.weight if old is not None else None,
            "after_weight": new.weight if new is not None else None,
            "before_market_value": old.market_value if old is not None else None,
            "after_market_value": new.market_value if new is not None else None,
            "possible_direction": direction,
            "confirmed_trade": False,
            "status": "UNCONFIRMED" if direction else "NO_QUANTITY_CHANGE",
        })
    return {
        "before_snapshot_id": before.id,
        "after_snapshot_id": after.id,
        "before_snapshot_time": before.snapshot_time.isoformat(),
        "after_snapshot_time": after.snapshot_time.isoformat(),
        "positions": positions,
        "cash_change": _delta(snapshot_cash(before), snapshot_cash(after)),
        "total_assets_change": _delta(before.total_assets, after.total_assets),
        "total_market_value_change": _delta(before.total_market_value, after.total_market_value),
        "calculation_version": PORTFOLIO_DIFF_VERSION,
    }


def _delta(before: float | None, after: float | None) -> float | None:
    return None if before is None or after is None else after - before


def reconcile_snapshot_diff_with_ledger(
    db: Session,
    *,
    before: PortfolioSnapshot,
    after: PortfolioSnapshot,
    diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare quantity deltas with actual confirmed ledger events, never mutate either fact."""

    diff = diff or calculate_snapshot_diff(before, after)
    rows = db.execute(select(TradeLedgerEntry).where(
        TradeLedgerEntry.portfolio_id == after.portfolio_id,
        TradeLedgerEntry.status == "CONFIRMED",
        TradeLedgerEntry.available_at > before.snapshot_time,
        TradeLedgerEntry.available_at <= after.snapshot_time,
    )).scalars().all()
    ledger_delta: dict[str, float] = defaultdict(float)
    non_trade_codes: set[str] = set()
    for entry in rows:
        if entry.entry_type == "TRADE" and entry.security_code and entry.quantity is not None:
            ledger_delta[entry.security_code] += entry.quantity if entry.side == "BUY" else -entry.quantity
        elif entry.security_code:
            non_trade_codes.add(entry.security_code)
    ledger_delta_all = dict(ledger_delta)
    quantity_rows = [row for row in diff.get("positions") or [] if row.get("qty_delta")]
    if not quantity_rows and not ledger_delta:
        status = "MATCHED"
    else:
        matches = 0
        mismatch = 0
        unexplained = 0
        non_trade_change = 0
        for row in quantity_rows:
            code = str(row.get("code") or "")
            expected = float(row.get("qty_delta") or 0.0)
            actual = ledger_delta.pop(code, 0.0)
            if abs(expected - actual) < 1e-8:
                matches += 1
            elif code in non_trade_codes:
                mismatch += 1
                non_trade_change += 1
            elif actual:
                mismatch += 1
            else:
                unexplained += 1
        extra = any(abs(value) > 1e-8 for value in ledger_delta.values())
        if non_trade_change and non_trade_change == len(quantity_rows) and not ledger_delta_all:
            status = "NON_TRADE_CHANGE"
        elif unexplained and not mismatch and not extra:
            status = "UNEXPLAINED"
        elif mismatch or extra:
            status = "PARTIAL" if matches else "DATA_CONFLICT"
        elif quantity_rows and matches == len(quantity_rows):
            status = "MATCHED"
        else:
            status = "NON_TRADE_CHANGE"
    return {
        "status": status,
        "ledger_entries_considered": [entry.id for entry in rows],
        "ledger_quantity_delta": ledger_delta_all,
        "snapshot_quantity_changes": quantity_rows,
    }


def upsert_snapshot_diff(db: Session, *, before: PortfolioSnapshot, after: PortfolioSnapshot) -> PortfolioSnapshotDiff:
    diff = calculate_snapshot_diff(before, after)
    reconciliation = reconcile_snapshot_diff_with_ledger(db, before=before, after=after, diff=diff)
    diff["reconciliation"] = reconciliation
    existing = db.execute(select(PortfolioSnapshotDiff).where(
        PortfolioSnapshotDiff.before_snapshot_id == before.id,
        PortfolioSnapshotDiff.after_snapshot_id == after.id,
    )).scalar_one_or_none()
    if existing is None:
        existing = PortfolioSnapshotDiff(
            user_id=after.user_id,
            portfolio_id=after.portfolio_id,
            before_snapshot_id=before.id,
            after_snapshot_id=after.id,
            diff_json=diff,
            reconciliation_status=reconciliation["status"],
            calculation_version=PORTFOLIO_DIFF_VERSION,
        )
        db.add(existing)
    else:
        existing.diff_json = diff
        existing.reconciliation_status = reconciliation["status"]
    db.flush()
    return existing


__all__ = [
    "calculate_snapshot_diff",
    "reconcile_snapshot_diff_with_ledger",
    "snapshot_cash",
    "upsert_snapshot_diff",
]
