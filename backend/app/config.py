"""Application configuration loaded from environment variables."""
import os
from functools import lru_cache


class Settings:
    """Runtime settings for the legacy archive API and the V2 application."""

    # Legacy archive auth. Kept during the V1 -> V2 migration window.
    ADVISOR_TOKEN: str = os.getenv("ADVISOR_TOKEN", "")

    # V2 application security. Production deployments must override this value.
    APP_SECRET_KEY: str = os.getenv("APP_SECRET_KEY", "dev-only-change-me")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "15"))
    JWT_REFRESH_TOKEN_DAYS: int = int(os.getenv("JWT_REFRESH_TOKEN_DAYS", "30"))
    ALLOW_REGISTRATION: bool = os.getenv("ALLOW_REGISTRATION", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Database: SQLite single file for local/self-hosted use. PostgreSQL support
    # will be introduced after the V2 schema is fully migrated through Alembic.
    DB_PATH: str = os.getenv(
        "ADVISOR_DB_PATH",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "advisor.db",
        ),
    )
    ARTIFACTS_DIR: str = os.getenv(
        "ADVISOR_ARTIFACTS_DIR",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "artifacts",
        ),
    )
    SQLITE_JOURNAL_MODE: str = os.getenv("ADVISOR_SQLITE_JOURNAL_MODE", "").upper()

    # Server.
    HOST: str = os.getenv("ADVISOR_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("ADVISOR_PORT", "8000"))

    # CORS: allow the local frontend origin by default.
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "ADVISOR_CORS_ORIGINS",
            "http://localhost:5173,http://localhost:8080",
        ).split(",")
        if origin.strip()
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
