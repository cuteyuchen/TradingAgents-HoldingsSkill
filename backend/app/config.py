"""Application configuration loaded from environment variables."""
import os
from functools import lru_cache


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


class Settings:
    """Runtime settings for the legacy archive API and the V2 application."""

    # Legacy archive auth. Kept during the V1 -> V2 migration window.
    ADVISOR_TOKEN: str = os.getenv("ADVISOR_TOKEN", "")

    # V2 application security. Production deployments must override this value.
    APP_SECRET_KEY: str = os.getenv("APP_SECRET_KEY", "dev-only-change-me")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "15"))
    JWT_REFRESH_TOKEN_DAYS: int = int(os.getenv("JWT_REFRESH_TOKEN_DAYS", "30"))
    ALLOW_REGISTRATION: bool = _bool_env("ALLOW_REGISTRATION", "true")

    # Database and files.
    DB_PATH: str = os.getenv(
        "ADVISOR_DB_PATH",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "advisor.db"),
    )
    ARTIFACTS_DIR: str = os.getenv(
        "ADVISOR_ARTIFACTS_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "artifacts"),
    )
    SQLITE_JOURNAL_MODE: str = os.getenv("ADVISOR_SQLITE_JOURNAL_MODE", "").upper()
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(12 * 1024 * 1024)))

    # Analysis and scheduler.
    ANALYSIS_HISTORY_LIMIT: int = int(os.getenv("ANALYSIS_HISTORY_LIMIT", "5"))
    SCHEDULER_ENABLED: bool = _bool_env("SCHEDULER_ENABLED", "true")
    SCHEDULER_INTERVAL_SECONDS: int = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "60"))
    PUBLIC_APP_URL: str = os.getenv("PUBLIC_APP_URL", "http://localhost:8080").rstrip("/")

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
