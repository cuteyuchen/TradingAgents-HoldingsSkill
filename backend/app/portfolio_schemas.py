"""Phase E request and response schemas for the Portfolio Operating System."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .market.codes import normalize_security_code


LedgerEntryType = Literal[
    "TRADE", "CASH_IN", "CASH_OUT", "DIVIDEND", "FEE", "TAX",
    "TRANSFER_IN", "TRANSFER_OUT", "CORPORATE_ACTION", "OTHER",
]
LedgerSource = Literal["MANUAL", "CSV_IMPORT", "BROKER_IMPORT", "SNAPSHOT_RECONCILIATION"]


class TradeLedgerCreate(BaseModel):
    entry_type: LedgerEntryType
    security_code: str | None = Field(default=None, max_length=16)
    security_name: str | None = Field(default=None, max_length=128)
    side: Literal["BUY", "SELL"] | None = None
    quantity: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    gross_amount: float | None = Field(default=None, ge=0)
    fees: float | None = Field(default=None, ge=0)
    taxes: float | None = Field(default=None, ge=0)
    net_amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="CNY", max_length=8)
    executed_at: datetime
    trade_date: date | None = None
    available_at: datetime | None = None
    source: LedgerSource = "MANUAL"
    source_ref: str | None = Field(default=None, max_length=255)
    broker_order_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=4000)
    analysis_run_id: int | None = None
    trigger_event_id: int | None = None

    model_config = ConfigDict(allow_inf_nan=False)

    @field_validator("quantity", "price", "gross_amount", "fees", "taxes", "net_amount", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("ledger numeric fields cannot be booleans")
        return value

    @field_validator("security_code", mode="before")
    @classmethod
    def normalize_code(cls, value: Any) -> str | None:
        return normalize_security_code(value) or None


class TradeLedgerRevisionCreate(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=2000)


class TradeLedgerVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class TradeLedgerConfirm(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class LedgerImportCommit(BaseModel):
    source_ref: str = Field(min_length=4, max_length=64)
    rows: list[TradeLedgerCreate] = Field(default_factory=list, max_length=2000)
    label: str | None = Field(default=None, max_length=128)


class TradeLedgerEntryResponse(BaseModel):
    id: int
    portfolio_id: int
    entry_type: str
    security_code: str | None
    security_name: str | None
    side: str | None
    quantity: float | None
    price: float | None
    gross_amount: float | None
    fees: float | None
    taxes: float | None
    net_amount: float | None
    currency: str
    executed_at: datetime
    trade_date: date
    available_at: datetime
    source: str
    source_ref: str | None
    broker_order_id: str | None
    idempotency_key: str | None
    status: str
    notes: str | None
    analysis_run_id: int | None
    trigger_event_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TradeLedgerRevisionResponse(BaseModel):
    id: int
    ledger_entry_id: int
    revision_no: int
    changes_json: dict[str, Any]
    reason: str
    created_by_user_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PortfolioRiskCalculateRequest(BaseModel):
    as_of: datetime | None = None
    persist: bool = True

    model_config = ConfigDict(extra="forbid")
