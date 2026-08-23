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
    STATIC_DIR: str = os.getenv(
        "ADVISOR_STATIC_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"),
    )
    SQLITE_JOURNAL_MODE: str = os.getenv("ADVISOR_SQLITE_JOURNAL_MODE", "").upper()
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(12 * 1024 * 1024)))

    # 模型调用超时与重试。
    # 推理型模型（如 o 系列、DeepSeek-R1、QwQ、GLM-Z1）在返回首个 token 之前
    # 会先思考很长时间，非流式请求在这段时间内不会产生任何字节，因此读超时
    # 必须按"最长思考时间"来设置，而不是按"正常响应时间"。
    # MODEL_CONNECT_TIMEOUT 只覆盖 TCP 建连，用于快速发现网络不可达。
    MODEL_CONNECT_TIMEOUT: float = float(os.getenv("MODEL_CONNECT_TIMEOUT", "15"))
    # 默认读超时，模型档案里的 timeout 参数可以覆盖它。
    MODEL_READ_TIMEOUT: float = float(os.getenv("MODEL_READ_TIMEOUT", "600"))
    # 流式模式下允许的最长静默间隔：开启流式后读超时按"两个数据块之间的间隔"
    # 计算，只要模型持续吐字就不会触发，因此这个值可以比总耗时小很多。
    MODEL_STREAM_IDLE_TIMEOUT: float = float(os.getenv("MODEL_STREAM_IDLE_TIMEOUT", "180"))
    # 是否默认使用流式请求。流式能让长思考过程持续产生数据，从根本上避免
    # 因"思考时间久"而误判超时；个别不支持 SSE 的网关可以关掉。
    MODEL_STREAM_DEFAULT: bool = _bool_env("MODEL_STREAM_DEFAULT", "true")
    # 超时或连接中断后的自动重试次数（仅对可安全重试的网络错误生效）。
    MODEL_MAX_RETRIES: int = int(os.getenv("MODEL_MAX_RETRIES", "1"))

    # Analysis and scheduler.
    ANALYSIS_HISTORY_LIMIT: int = int(os.getenv("ANALYSIS_HISTORY_LIMIT", "5"))
    SCHEDULER_ENABLED: bool = _bool_env("SCHEDULER_ENABLED", "true")
    SCHEDULER_INTERVAL_SECONDS: int = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "60"))
    PUBLIC_APP_URL: str = os.getenv("PUBLIC_APP_URL", "http://localhost:8080").rstrip("/")

    # Phase B market-data foundation. Provider order is centralized here so
    # adapters and business services never each invent their own fallback chain.
    MARKET_QUOTE_ALL_A_PRIMARY_PROVIDER: str = os.getenv(
        "MARKET_QUOTE_ALL_A_PRIMARY_PROVIDER", "eastmoney_batch"
    ).strip().lower()
    MARKET_QUOTE_ALL_A_FALLBACK_PROVIDERS: tuple[str, ...] = tuple(
        value.strip().lower()
        for value in os.getenv("MARKET_QUOTE_ALL_A_FALLBACK_PROVIDERS", "tencent").split(",")
        if value.strip()
    )
    MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER: str = os.getenv(
        "MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER", "tencent"
    ).strip().lower()
    MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS: tuple[str, ...] = tuple(
        value.strip().lower()
        for value in os.getenv("MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS", "eastmoney_batch").split(",")
        if value.strip()
    )
    PROVIDER_FAILURE_THRESHOLD: int = int(os.getenv("PROVIDER_FAILURE_THRESHOLD", "3"))
    PROVIDER_CIRCUIT_COOLDOWN_SECONDS: float = float(
        os.getenv("PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "60")
    )
    QUOTE_FRESHNESS_SECONDS: float = float(os.getenv("QUOTE_FRESHNESS_SECONDS", "90"))
    MARKET_SNAPSHOT_FRESHNESS_SECONDS: float = float(
        os.getenv("MARKET_SNAPSHOT_FRESHNESS_SECONDS", "120")
    )
    MARKET_QUOTE_CONFLICT_THRESHOLD_PCT: float = float(
        os.getenv("MARKET_QUOTE_CONFLICT_THRESHOLD_PCT", "0.5")
    )
    EASTMONEY_MIN_INTERVAL_SECONDS: float = float(
        os.getenv("EASTMONEY_MIN_INTERVAL_SECONDS", "0.8")
    )
    SECURITY_MASTER_SYNC_ENABLED: bool = _bool_env("SECURITY_MASTER_SYNC_ENABLED", "false")
    CALENDAR_SYNC_ENABLED: bool = _bool_env("CALENDAR_SYNC_ENABLED", "false")
    # Identity data lifecycle.  The offline calendar bootstrap is safe to run
    # on every startup; remote providers are always asynchronous and opt-in.
    CALENDAR_BOOTSTRAP_ENABLED: bool = _bool_env("CALENDAR_BOOTSTRAP_ENABLED", "true")
    CALENDAR_SYNC_PROVIDER: str = os.getenv("CALENDAR_SYNC_PROVIDER", "eastmoney_sse_calendar").strip().lower()
    CALENDAR_SYNC_LOOKBACK_DAYS: int = int(os.getenv("CALENDAR_SYNC_LOOKBACK_DAYS", "370"))
    SECURITY_MASTER_SYNC_PROVIDER: str = os.getenv(
        "SECURITY_MASTER_SYNC_PROVIDER", "eastmoney_security"
    ).strip().lower()
    # Sync routes are global-data mutation endpoints, so they require both an
    # explicit feature flag and a separate operator token.  JWT login alone is
    # intentionally insufficient for these writes.
    MARKET_IDENTITY_SYNC_TOKEN: str = os.getenv("MARKET_IDENTITY_SYNC_TOKEN", "").strip()
    DAILY_BAR_SYNC_ENABLED: bool = _bool_env("DAILY_BAR_SYNC_ENABLED", "false")

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
