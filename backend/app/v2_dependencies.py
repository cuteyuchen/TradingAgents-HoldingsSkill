"""FastAPI dependencies for authenticated V2 routes."""
import secrets

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .security import InvalidAccessToken, decode_access_token
from .v2_models import User
from .config import settings

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidAccessToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, user_id)
    if user is None or user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is unavailable.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_market_identity_sync(
    current_user: User = Depends(get_current_user),
    sync_token: str | None = Header(default=None, alias="X-Market-Identity-Sync-Token"),
    internal_token: str | None = Header(default=None, alias="X-Internal-Sync-Token"),
) -> User:
    """Protect global SecurityMaster/TradingCalendar mutations.

    A normal JWT identifies an application user, but does not grant the
    operator capability to mutate shared market identity data.  Deployments
    must explicitly enable the relevant sync flag and configure the separate
    internal token.  This keeps the API useful for read-only multi-user use
    while allowing a trusted bootstrap job to call the write endpoints.
    """

    if not (settings.SECURITY_MASTER_SYNC_ENABLED or settings.CALENDAR_SYNC_ENABLED):
        raise HTTPException(status_code=403, detail="market_identity_sync_disabled")
    configured = settings.MARKET_IDENTITY_SYNC_TOKEN
    presented = sync_token or internal_token
    if not configured or not presented or not secrets.compare_digest(presented, configured):
        raise HTTPException(status_code=403, detail="market_identity_sync_forbidden")
    return current_user


def require_daily_bar_sync(
    current_user: User = Depends(get_current_user),
    sync_token: str | None = Header(default=None, alias="X-Market-Identity-Sync-Token"),
    internal_token: str | None = Header(default=None, alias="X-Internal-Sync-Token"),
) -> User:
    """Protect the long-running, global daily-bar cache bootstrap."""

    if not settings.DAILY_BAR_SYNC_ENABLED:
        raise HTTPException(status_code=403, detail="daily_bar_sync_disabled")
    configured = settings.MARKET_IDENTITY_SYNC_TOKEN
    presented = sync_token or internal_token
    if not configured or not presented or not secrets.compare_digest(presented, configured):
        raise HTTPException(status_code=403, detail="daily_bar_sync_forbidden")
    return current_user
