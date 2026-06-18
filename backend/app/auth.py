"""Single-token bearer auth."""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

bearer_scheme = HTTPBearer(auto_error=False)


def ensure_token() -> str:
    """Generate and persist a token on first start if none is set.

    For Docker: the token should be injected via the ADVISOR_TOKEN env var.
    For local first run without a token, we generate one and print it once.
    """
    if settings.ADVISOR_TOKEN:
        return settings.ADVISOR_TOKEN
    # Generate a token for this process lifetime if none configured.
    # NOTE: this is non-persistent; for real use set ADVISOR_TOKEN in env/.env.
    generated = "adv_" + secrets.token_urlsafe(24)
    settings.ADVISOR_TOKEN = generated
    return generated


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """FastAPI dependency enforcing the bearer token on protected routes."""
    expected = settings.ADVISOR_TOKEN
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADVISOR_TOKEN is not configured on the server.",
        )
    if credentials is None or not secrets.compare_digest(
        credentials.credentials, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return expected
