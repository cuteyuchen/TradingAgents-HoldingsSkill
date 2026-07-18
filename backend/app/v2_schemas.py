"""Pydantic schemas for the V2 product API."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

ModelPurpose = Literal["vision", "analysis", "deep_analysis"]
AnalysisMode = Literal["quick", "deep"]
NotificationType = Literal["dingtalk", "wecom"]


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str | None = Field(default=None, min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_info: str | None = Field(default=None, max_length=255)


class RefreshRequest(BaseModel):
    refresh_token: str
    device_info: str | None = Field(default=None, max_length=255)


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    username: str | None
    status: str
    timezone: str = "Asia/Shanghai"
    created_at: datetime
    last_login_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ModelProviderCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, max_length=4096)
    enabled: bool = True


class ModelProviderUpdate(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    enabled: bool | None = None


class ModelProviderResponse(BaseModel):
    id: int
    provider: str
    display_name: str
    base_url: str | None
    enabled: bool
    has_api_key: bool
    api_key_masked: str | None
    created_at: datetime
    updated_at: datetime


class ModelProfileCreate(BaseModel):
    provider_id: int
    purpose: ModelPurpose
    model_name: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class ModelProfileUpdate(BaseModel):
    provider_id: int | None = None
    purpose: ModelPurpose | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    parameters: dict[str, Any] | None = None
    is_default: bool | None = None


class ModelProfileResponse(BaseModel):
    id: int
    provider_id: int
    purpose: ModelPurpose
    model_name: str
    parameters: dict[str, Any]
    is_default: bool
    last_health_status: str | None
    last_health_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ModelHealthResponse(BaseModel):
    status: str
    message: str
    latency_ms: int | None = None
    raw_excerpt: str | None = None


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    market: str = Field(default="A_SHARE", max_length=16)
    currency: str = Field(default="CNY", max_length=8)
    is_default: bool = False


class PortfolioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    market: str | None = Field(default=None, max_length=16)
    currency: str | None = Field(default=None, max_length=8)
    is_default: bool | None = None


class PortfolioResponse(BaseModel):
    id: int
    name: str
    market: str
    currency: str
    is_default: bool
    latest_snapshot_id: int | None = None
    latest_snapshot_time: datetime | None = None
    created_at: datetime
    updated_at: datetime


class HoldingInput(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str | None = Field(default=None, max_length=64)
    market: str | None = Field(default=None, max_length=16)
    qty: float | None = None
    available_qty: float | None = None
    cost: float | None = None
    price: float | None = None
    market_value: float | None = None
    pnl: float | None = None
    pnl_amount: float | None = None
    weight: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        text = value.strip().upper()
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits[-6:] if len(digits) >= 6 else text


class ParsedHoldingsPayload(BaseModel):
    holdings: list[HoldingInput] = Field(default_factory=list)
    total_assets: float | None = None
    total_market_value: float | None = None
    broker_available_cash: float | None = None
    corrected_unused_funds: float | None = None
    repo_or_standard_bond_value: float | None = None
    excluded_items: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class UploadResponse(BaseModel):
    id: int
    portfolio_id: int
    original_filename: str
    mime_type: str
    parsing_status: str
    parsed: ParsedHoldingsPayload | None = None
    validation_errors: list[str] = Field(default_factory=list)
    error_message: str | None = None
    screenshot_url: str
    confirmed_at: datetime | None = None
    created_at: datetime


class ParsedHoldingsUpdate(BaseModel):
    parsed: ParsedHoldingsPayload


class SnapshotResponse(BaseModel):
    id: int
    portfolio_id: int
    upload_id: int | None
    source: str
    snapshot_time: datetime
    status: str
    total_assets: float | None
    total_market_value: float | None
    broker_available_cash: float | None
    corrected_unused_funds: float | None
    repo_or_standard_bond_value: float | None
    holdings: list[HoldingInput]


class AnalysisJobCreate(BaseModel):
    snapshot_id: int
    mode: AnalysisMode = "deep"
    checkpoint: str | None = Field(default=None, max_length=16)
    notify: bool = True


class AnalysisJobResponse(BaseModel):
    id: int
    portfolio_id: int
    snapshot_id: int
    trigger_type: str
    checkpoint: str | None
    mode: str
    status: str
    progress_percent: int
    current_stage: str
    notify: bool
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    retry_count: int
    run_id: int | None = None
    created_at: datetime


class AnalysisRunSummary(BaseModel):
    id: int
    job_id: int
    portfolio_snapshot_id: int
    data_quality_grade: str | None
    summary: str | None
    final_rating: str | None
    cash_target: str | None
    confidence: str | None
    created_at: datetime


class AnalysisRunDetail(AnalysisRunSummary):
    structured_result: dict[str, Any]
    markdown: str


class ScheduleCreate(BaseModel):
    portfolio_id: int
    name: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=35, ge=0, le=59)
    checkpoint: str = Field(default="09:35", max_length=16)
    mode: AnalysisMode = "deep"
    enabled: bool = True
    stale_snapshot_days: int = Field(default=3, ge=0, le=30)
    notify: bool = True
    max_consecutive_failures: int = Field(default=3, ge=1, le=20)


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    timezone: str | None = Field(default=None, max_length=64)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    checkpoint: str | None = Field(default=None, max_length=16)
    mode: AnalysisMode | None = None
    enabled: bool | None = None
    stale_snapshot_days: int | None = Field(default=None, ge=0, le=30)
    notify: bool | None = None
    max_consecutive_failures: int | None = Field(default=None, ge=1, le=20)


class ScheduleResponse(BaseModel):
    id: int
    portfolio_id: int
    name: str
    timezone: str
    hour: int
    minute: int
    checkpoint: str
    mode: str
    enabled: bool
    stale_snapshot_days: int
    notify: bool
    max_consecutive_failures: int
    consecutive_failures: int
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NotificationChannelCreate(BaseModel):
    type: NotificationType
    name: str = Field(min_length=1, max_length=128)
    webhook: str = Field(min_length=10, max_length=2048)
    secret: str | None = Field(default=None, max_length=512)
    enabled: bool = True


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    webhook: str | None = Field(default=None, min_length=10, max_length=2048)
    secret: str | None = Field(default=None, max_length=512)
    clear_secret: bool = False
    enabled: bool | None = None


class NotificationChannelResponse(BaseModel):
    id: int
    type: NotificationType
    name: str
    enabled: bool
    webhook_masked: str
    has_secret: bool
    last_test_status: str | None
    last_test_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SimpleMessage(BaseModel):
    status: str
    message: str
