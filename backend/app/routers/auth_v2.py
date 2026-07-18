"""V2 username/password and token authentication endpoints."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..security import (
    create_access_token,
    ensure_utc,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from ..v2_dependencies import get_current_user
from ..v2_models import RefreshToken, User
from ..v2_schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)

router = APIRouter(prefix="/api/v2/auth", tags=["v2-auth"])


def _issue_token_pair(db: Session, user: User, device_info: str | None = None) -> TokenPair:
    access_token, expires_in = create_access_token(user.id)
    raw_refresh_token = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh_token),
            expires_at=refresh_token_expiry(),
            device_info=device_info,
        )
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=raw_refresh_token,
        expires_in=expires_in,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="Registration is disabled.")

    email = payload.email.lower()
    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(status_code=409, detail="Email is already registered.")
    if payload.username and db.query(User).filter(User.username == payload.username).first() is not None:
        raise HTTPException(status_code=409, detail="Username is already registered.")

    user = User(
        email=email,
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user is None or user.status != "active" or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    user.last_login_at = datetime.now(UTC)
    token_pair = _issue_token_pair(db, user, payload.device_info)
    db.commit()
    return token_pair


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    token = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == hash_refresh_token(payload.refresh_token))
        .first()
    )
    now = datetime.now(UTC)
    if token is None or token.revoked_at is not None or ensure_utc(token.expires_at) <= now:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")

    user = db.get(User, token.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="User is unavailable.")

    token.revoked_at = now
    token_pair = _issue_token_pair(db, user, payload.device_info or token.device_info)
    db.commit()
    return token_pair


@router.post("/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    token = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == hash_refresh_token(payload.refresh_token))
        .first()
    )
    if token is not None and token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        db.commit()
    return {"status": "ok"}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
