"""V2 identity and model-configuration tables.

These tables intentionally live beside the legacy analysis/archive models so the
V1 archive API can keep operating while the V2 product surface is introduced.
"""
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    model_providers: Mapped[list["ModelProvider"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    model_profiles: Mapped[list["ModelProfile"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    device_info: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class ModelProvider(Base):
    __tablename__ = "model_providers"
    __table_args__ = (
        UniqueConstraint("user_id", "display_name", name="uq_model_provider_user_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    base_url: Mapped[str | None] = mapped_column(String(512))
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(back_populates="model_providers")
    profiles: Mapped[list["ModelProfile"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )


class ModelProfile(Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "purpose", "model_name", name="uq_model_profile_user_purpose_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("model_providers.id", ondelete="CASCADE"),
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    model_name: Mapped[str] = mapped_column(String(128))
    parameters_json: Mapped[dict | None] = mapped_column(JSON)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_health_status: Mapped[str | None] = mapped_column(String(32))
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(back_populates="model_profiles")
    provider: Mapped[ModelProvider] = relationship(back_populates="profiles")
