"""Derived current account facts: confirmed snapshot anchor plus confirmed ledger events.

The snapshot stays the authoritative baseline.  Ledger rows only describe
events that happened *after* that baseline, so a newer screenshot never
double-counts a trade that the screenshot already reflects.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market.codes import normalize_security_code
from ..portfolio_models import TradeLedgerEntry
from ..v2_models import PortfolioSnapshot

ACCOUNT_STATE_VERSION = "portfolio-account-v1"
CHINA_TZ = ZoneInfo("Asia/Shanghai")

_CASH_IN_TYPES = frozenset({"CASH_IN", "DIVIDEND", "TRANSFER_IN"})
_CASH_OUT_TYPES = frozenset({"CASH_OUT", "FEE", "TAX", "TRANSFER_OUT"})
_POSITION_TRANSFER_TYPES = frozenset({"TRANSFER_IN", "TRANSFER_OUT", "CORPORATE_ACTION"})


def _utc_naive(value: datetime | None) -> datetime:
    value = value or datetime.now(UTC)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _shanghai_date(value: datetime) -> date:
    return _utc_naive(value).replace(tzinfo=UTC).astimezone(CHINA_TZ).date()


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def account_version_for(snapshot_id: int, entries: list[TradeLedgerEntry]) -> str:
    """Deterministic version of the derived account: anchor plus applied ledger facts."""

    digest = hashlib.sha256()
    digest.update(str(snapshot_id).encode("utf-8"))
    for entry in entries:
        stamp = entry.updated_at.isoformat() if isinstance(entry.updated_at, datetime) else str(entry.updated_at or "")
        digest.update(f"|{entry.id}:{entry.status}:{stamp}".encode("utf-8"))
    return f"{ACCOUNT_STATE_VERSION}:{snapshot_id}:{digest.hexdigest()[:16]}"


def confirmed_ledger_entries_after_snapshot(
    db: Session,
    *,
    portfolio_id: int,
    snapshot: PortfolioSnapshot,
    as_of: datetime,
) -> list[TradeLedgerEntry]:
    """Confirmed, already-available events strictly after the snapshot baseline."""

    cutoff = _utc_naive(as_of)
    return list(
        db.execute(
            select(TradeLedgerEntry)
            .where(
                TradeLedgerEntry.portfolio_id == portfolio_id,
                TradeLedgerEntry.status == "CONFIRMED",
                TradeLedgerEntry.executed_at > snapshot.snapshot_time,
                TradeLedgerEntry.available_at <= cutoff,
            )
            .order_by(TradeLedgerEntry.executed_at.asc(), TradeLedgerEntry.id.asc())
        )
        .scalars()
        .all()
    )


def _base_position(item: Any) -> dict[str, Any]:
    return {
        "code": normalize_security_code(item.code) or None,
        "name": item.name,
        "qty": _number(item.qty),
        "available_qty": _number(item.available_qty),
        "cost": _number(item.cost),
        "base_qty": _number(item.qty),
        "base_available_qty": _number(item.available_qty),
        "qty_delta": 0.0,
        "available_delta": 0.0,
        "open_buy_qty": 0.0,
        "flags": [],
        "snapshot_item": True,
    }


def _cash_effect(entry: TradeLedgerEntry) -> tuple[float | None, list[str]]:
    flags: list[str] = []
    amount = _number(entry.net_amount)
    if amount is None:
        amount = _number(entry.gross_amount)
    if amount is None:
        return None, [f"LEDGER_AMOUNT_MISSING:{entry.id}"]
    if entry.net_amount is None:
        flags.append(f"LEDGER_NET_AMOUNT_FROM_GROSS:{entry.id}")
    if entry.fees is None and entry.taxes is None:
        flags.append(f"LEDGER_COST_COMPONENTS_UNKNOWN:{entry.id}")
    return amount, flags


def derive_account_state(
    snapshot: PortfolioSnapshot,
    entries: list[TradeLedgerEntry],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Pure derivation so the result can be replayed and unit-tested without IO."""

    moment = _utc_naive(as_of)
    local_date = _shanghai_date(moment)
    positions: dict[str, dict[str, Any]] = {}
    for item in snapshot.holdings:
        row = _base_position(item)
        key = row["code"] or f"name:{row['name']}"
        positions[key] = row
    flags: list[str] = []
    applied: list[int] = []
    unapplied: list[dict[str, Any]] = []
    cash = _number(snapshot.broker_available_cash)
    cash_delta = 0.0
    cash_known = cash is not None
    pending_sell_proceeds = 0.0

    for entry in entries:
        applied.append(entry.id)
        entry_type = str(entry.entry_type or "").upper()
        code = normalize_security_code(entry.security_code) or None
        quantity = _number(entry.quantity)
        trade_date = entry.trade_date if isinstance(entry.trade_date, date) else _shanghai_date(entry.executed_at)
        settled = trade_date < local_date
        if entry_type == "TRADE" and entry.side in {"BUY", "SELL"} and code and quantity:
            key = code
            row = positions.get(key)
            if row is None and entry.side == "SELL":
                flags.append(f"LEDGER_SELL_WITHOUT_SNAPSHOT_POSITION:{code}")
                row = {
                    "code": code, "name": entry.security_name, "qty": 0.0, "available_qty": 0.0,
                    "cost": None, "base_qty": 0.0, "base_available_qty": 0.0,
                    "qty_delta": 0.0, "available_delta": 0.0, "open_buy_qty": 0.0,
                    "flags": [], "snapshot_item": False,
                }
                positions[key] = row
            elif row is None:
                row = {
                    "code": code, "name": entry.security_name, "qty": 0.0, "available_qty": 0.0,
                    "cost": _number(entry.price), "base_qty": 0.0, "base_available_qty": 0.0,
                    "qty_delta": 0.0, "available_delta": 0.0, "open_buy_qty": 0.0,
                    "flags": [], "snapshot_item": False,
                }
                positions[key] = row
            if entry.side == "BUY":
                row["qty"] = (row["qty"] or 0.0) + quantity
                row["qty_delta"] += quantity
                if settled:
                    row["available_qty"] = (row["available_qty"] or 0.0) + quantity
                    row["available_delta"] += quantity
                else:
                    row["open_buy_qty"] += quantity
                    row["flags"].append("T1_BUY_SETTLEMENT_PENDING")
                amount, amount_flags = _cash_effect(entry)
                flags.extend(amount_flags)
                if amount is not None and cash_known:
                    cash -= amount
                    cash_delta -= amount
                elif amount is not None:
                    flags.append(f"LEDGER_CASH_EFFECT_WITHOUT_BASE_CASH:{entry.id}")
            else:
                row["qty"] = (row["qty"] or 0.0) - quantity
                row["qty_delta"] -= quantity
                if row["available_qty"] is None:
                    flags.append(f"LEDGER_AVAILABLE_UNKNOWN_FOR_SELL:{code}")
                    row["flags"].append("AVAILABLE_UNKNOWN")
                else:
                    row["available_qty"] = row["available_qty"] - quantity
                    row["available_delta"] -= quantity
                amount, amount_flags = _cash_effect(entry)
                flags.extend(amount_flags)
                if amount is not None:
                    if settled:
                        if cash_known:
                            cash += amount
                            cash_delta += amount
                        else:
                            flags.append(f"LEDGER_CASH_EFFECT_WITHOUT_BASE_CASH:{entry.id}")
                    else:
                        pending_sell_proceeds += amount
                        row["flags"].append("T1_SELL_PROCEEDS_PENDING")
            if row["qty"] is not None and row["qty"] < -1e-9:
                flags.append(f"LEDGER_NEGATIVE_POSITION:{code}")
                row["flags"].append("NEGATIVE_POSITION")
            if row["available_qty"] is not None and row["available_qty"] < -1e-9:
                flags.append(f"LEDGER_SELL_EXCEEDS_AVAILABLE:{code}")
                row["flags"].append("SELL_EXCEEDS_AVAILABLE")
            continue
        if code and entry_type in _POSITION_TRANSFER_TYPES and quantity:
            row = positions.get(code)
            if row is None:
                row = {
                    "code": code, "name": entry.security_name, "qty": 0.0, "available_qty": 0.0,
                    "cost": None, "base_qty": 0.0, "base_available_qty": 0.0,
                    "qty_delta": 0.0, "available_delta": 0.0, "open_buy_qty": 0.0,
                    "flags": [], "snapshot_item": False,
                }
                positions[code] = row
            direction = -1.0 if entry_type == "TRANSFER_OUT" else 1.0
            delta = direction * quantity
            row["qty"] = (row["qty"] or 0.0) + delta
            row["qty_delta"] += delta
            row["available_qty"] = (row["available_qty"] or 0.0) + delta
            row["available_delta"] += delta
            if row["qty"] is not None and row["qty"] < -1e-9:
                flags.append(f"LEDGER_NEGATIVE_POSITION:{code}")
                row["flags"].append("NEGATIVE_POSITION")
            continue
        if entry_type in _CASH_IN_TYPES or entry_type in _CASH_OUT_TYPES:
            amount, amount_flags = _cash_effect(entry)
            flags.extend(amount_flags)
            if amount is None:
                continue
            sign = 1.0 if entry_type in _CASH_IN_TYPES else -1.0
            if cash_known:
                cash += sign * amount
                cash_delta += sign * amount
            else:
                flags.append(f"LEDGER_CASH_EFFECT_WITHOUT_BASE_CASH:{entry.id}")
            continue
        if _number(entry.net_amount) is not None or _number(entry.gross_amount) is not None:
            unapplied.append({
                "entry_id": entry.id,
                "entry_type": entry_type,
                "reason": "unclassified_cash_effect",
            })
            flags.append(f"LEDGER_EFFECT_UNCLASSIFIED:{entry.id}")
    for row in positions.values():
        base_qty = row.get("base_qty")
        base_available = row.get("base_available_qty")
        if row.get("qty") is not None and base_qty is not None and abs(row["qty"] - base_qty) < 1e-9 and not row["flags"]:
            row["source"] = "snapshot"
        elif row.get("snapshot_item"):
            row["source"] = "snapshot+ledger"
        else:
            row["source"] = "ledger"
        if base_available is not None and row.get("available_qty") is not None and row["available_qty"] > row["qty"] + 1e-9:
            row["available_qty"] = row["qty"]
            row["flags"].append("AVAILABLE_CLAMPED_TO_QTY")
    cash_state = {
        "available": cash if cash_known else None,
        "base_broker_available_cash": _number(snapshot.broker_available_cash),
        "net_ledger_cash_delta": cash_delta,
        "pending_sell_proceeds": pending_sell_proceeds,
        "frozen": None,
        "frozen_available": False,
        "other_reserved": _number(snapshot.repo_or_standard_bond_value),
    }
    if not cash_known:
        flags.append("LEDGER_BASE_CASH_UNKNOWN")
    if pending_sell_proceeds:
        flags.append("SELL_PROCEEDS_PENDING_T1")
    return {
        "version": ACCOUNT_STATE_VERSION,
        "snapshot_id": snapshot.id,
        "snapshot_time": snapshot.snapshot_time.isoformat(),
        "as_of": moment.isoformat(),
        "positions": list(positions.values()),
        "cash": cash_state,
        "applied_entry_ids": applied,
        "unapplied_entries": unapplied,
        "flags": list(dict.fromkeys(flags)),
        "account_version": account_version_for(snapshot.id, entries),
        "entry_count": len(entries),
    }


def build_account_state(
    db: Session,
    *,
    portfolio_id: int,
    snapshot: PortfolioSnapshot,
    as_of: datetime,
) -> dict[str, Any]:
    entries = confirmed_ledger_entries_after_snapshot(
        db, portfolio_id=portfolio_id, snapshot=snapshot, as_of=as_of
    )
    return derive_account_state(snapshot, entries, as_of=as_of)


__all__ = [
    "ACCOUNT_STATE_VERSION",
    "account_version_for",
    "build_account_state",
    "confirmed_ledger_entries_after_snapshot",
    "derive_account_state",
]
