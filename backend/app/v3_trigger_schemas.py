from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TriggerPlanCreate(BaseModel):
    portfolio_id: int
    target_type: Literal["HOLDING", "PORTFOLIO", "CANDIDATE", "EVENT"] = "HOLDING"
    target_key: str = Field(min_length=1, max_length=128)
    trigger_type: str = Field(min_length=1, max_length=32)
    metric: str = Field(min_length=1, max_length=64)
    operator: Literal["GT", "GTE", "LT", "LTE", "CROSS_ABOVE", "CROSS_BELOW"]
    threshold: float
    secondary_threshold: float | None = None
    priority: Literal["P0", "P1", "P2", "P3"] = "P1"
    debounce_cycles: int = Field(default=2, ge=1, le=20)
    debounce_seconds: int = Field(default=180, ge=0, le=86400)
    cooldown_seconds: int = Field(default=1800, ge=0, le=604800)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TriggerPlanUpdate(BaseModel):
    enabled: bool | None = None
    threshold: float | None = None
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    debounce_cycles: int | None = Field(default=None, ge=1, le=20)
    debounce_seconds: int | None = Field(default=None, ge=0, le=86400)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=604800)
    expires_at: datetime | None = None


class MonitorRunOnceRequest(BaseModel):
    portfolio_id: int | None = None
    dry_run: bool = False
