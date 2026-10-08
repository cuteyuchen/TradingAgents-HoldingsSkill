"""Durable Fuyao credentials shared by HTTP requests and background workers."""
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..security import decrypt_secret
from ..v2_models import MarketProviderSetting, User


def _setting(db: Session) -> MarketProviderSetting | None:
    # Provider primitives also run before init_db or against older local DBs.
    if not inspect(db.get_bind()).has_table(MarketProviderSetting.__tablename__):
        return None
    return db.get(MarketProviderSetting, "fuyao")


def can_manage_market_settings(db: Session, user_id: int) -> bool:
    # The first registered account owns this self-hosted instance. Ordinary
    # accounts must not replace the credential used by every user's workers.
    owner_id = db.scalar(select(User.id).order_by(User.id).limit(1))
    return user_id == owner_id


def fuyao_config_status(db: Session, user_id: int) -> dict:
    row = _setting(db)
    source = "system" if row else "environment" if settings.FUYAO_API_KEY else "none"
    return {
        "configured": source != "none",
        "source": source,
        "can_manage": can_manage_market_settings(db, user_id),
        "updated_at": row.updated_at if row else None,
    }


def get_fuyao_api_key() -> str:
    with SessionLocal() as db:
        row = _setting(db)
        if row is not None:
            # Unreadable stored credentials fail closed rather than silently
            # substituting another credential after APP_SECRET_KEY changes.
            return decrypt_secret(row.encrypted_api_key)
    return settings.FUYAO_API_KEY
