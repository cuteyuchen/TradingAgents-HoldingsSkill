"""Pydantic schemas for V2 authentication and model settings."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

ModelPurpose = Literal["vision", "analysis", "deep_analysis"]


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
