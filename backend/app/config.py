"""Application configuration loaded from environment variables."""
import os
from functools import lru_cache


class Settings:
    """Single-user settings. Read from environment with sensible defaults."""

    # Auth: single static bearer token. Generated on first run if unset.
    ADVISOR_TOKEN: str = os.getenv("ADVISOR_TOKEN", "")

    # Database: SQLite single file, zero-ops.
    DB_PATH: str = os.getenv("ADVISOR_DB_PATH", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "advisor.db"
    ))
    ARTIFACTS_DIR: str = os.getenv("ADVISOR_ARTIFACTS_DIR", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "artifacts"
    ))
    SQLITE_JOURNAL_MODE: str = os.getenv("ADVISOR_SQLITE_JOURNAL_MODE", "").upper()

    # Server.
    HOST: str = os.getenv("ADVISOR_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("ADVISOR_PORT", "8000"))

    # CORS: allow the local frontend origin by default.
    CORS_ORIGINS: list[str] = [
        o.strip() for o in os.getenv("ADVISOR_CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",")
        if o.strip()
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
