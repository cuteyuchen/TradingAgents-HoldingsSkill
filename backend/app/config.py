"""Application configuration loaded from environment variables."""
import os
from functools import lru_cache


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


def _choice_int_env(name: str, default: int, allowed: set[int]) -> int:
    value = int(os.getenv(name, str(default)))
    if value not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")
    return value


class Settings:
    """Runtime settings for the legacy archive API and the V2 application."""

    # Release metadata. Production images should inject these at build time;
    # the resolver never fabricates a SHA when no value is supplied.
    APP_VERSION: str = os.getenv("APP_VERSION", "0.3.0")
    APP_GIT_SHA: str = os.getenv("APP_GIT_SHA", "").strip()
    APP_GIT_REF: str = os.getenv("APP_GIT_REF", "").strip()
    APP_BUILD_TIME: str = os.getenv("APP_BUILD_TIME", "").strip()
    APP_ENV: str = os.getenv("APP_ENV", "development").strip().lower() or "development"

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
    BACKUP_DIR: str = os.getenv(
        "ADVISOR_BACKUP_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "backups"),
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
    CHECKPOINT_CATCHUP_MINUTES: int = max(0, int(os.getenv("CHECKPOINT_CATCHUP_MINUTES", "15")))
    ANALYSIS_CLAIM_LEASE_MINUTES: int = max(
        1, int(os.getenv("ANALYSIS_CLAIM_LEASE_MINUTES", "10"))
    )
    REVIEW_CLAIM_LEASE_MINUTES: int = max(
        1, int(os.getenv("REVIEW_CLAIM_LEASE_MINUTES", "10"))
    )
    NOTIFICATION_DISPATCH_LEASE_MINUTES: int = max(
        1, int(os.getenv("NOTIFICATION_DISPATCH_LEASE_MINUTES", "5"))
    )
    NOTIFICATION_DEFAULT_COOLDOWN_MINUTES: int = max(
        0, int(os.getenv("NOTIFICATION_DEFAULT_COOLDOWN_MINUTES", "60"))
    )
    PUBLIC_APP_URL: str = os.getenv("PUBLIC_APP_URL", "http://localhost:8080").rstrip("/")

    # Phase K release-readiness and self-hosted operations.
    BACKUP_SCHEDULE_ENABLED: bool = _bool_env("BACKUP_SCHEDULE_ENABLED", "true")
    BACKUP_SCHEDULE_TIME: str = os.getenv("BACKUP_SCHEDULE_TIME", "20:45").strip() or "20:45"
    BACKUP_RETENTION_DAILY: int = max(0, int(os.getenv("BACKUP_RETENTION_DAILY", "7")))
    BACKUP_RETENTION_WEEKLY: int = max(0, int(os.getenv("BACKUP_RETENTION_WEEKLY", "4")))
    BACKUP_RETENTION_PRE_UPGRADE: int = max(0, int(os.getenv("BACKUP_RETENTION_PRE_UPGRADE", "5")))
    BACKUP_RETENTION_MANUAL: int = max(0, int(os.getenv("BACKUP_RETENTION_MANUAL", "0")))
    BACKUP_DEGRADED_HOURS: float = max(0.0, float(os.getenv("BACKUP_DEGRADED_HOURS", "36")))
    BACKUP_BLOCKED_HOURS: float = max(0.0, float(os.getenv("BACKUP_BLOCKED_HOURS", "72")))
    DISK_DEGRADED_RATIO: float = float(os.getenv("DISK_DEGRADED_RATIO", "0.10"))
    DISK_BLOCKED_RATIO: float = float(os.getenv("DISK_BLOCKED_RATIO", "0.03"))
    DIAGNOSTIC_LOG_LINES: int = max(100, int(os.getenv("DIAGNOSTIC_LOG_LINES", "2000")))
    SYSTEM_MAINTENANCE_QUICK_CHECK_ENABLED: bool = _bool_env(
        "SYSTEM_MAINTENANCE_QUICK_CHECK_ENABLED", "true"
    )

    # Phase D deterministic realtime monitor and trigger engine.  The monitor
    # is opt-in so an upgraded deployment does not immediately start remote
    # quote traffic before local market data is ready.
    REALTIME_MONITOR_ENABLED: bool = _bool_env("REALTIME_MONITOR_ENABLED", "false")
    MONITOR_INTERVAL_SECONDS: int = max(60, int(os.getenv("MONITOR_INTERVAL_SECONDS", "60")))
    MARKET_SCORE_INTERVAL_MINUTES: int = _choice_int_env(
        "MARKET_SCORE_INTERVAL_MINUTES", 5, {1, 5, 10, 15, 30}
    )
    TRIGGER_MARKET_SCORE_WINDOW_MINUTES: int = int(os.getenv("TRIGGER_MARKET_SCORE_WINDOW_MINUTES", "15"))
    TRIGGER_MARKET_SCORE_BASELINE_TOLERANCE_MINUTES: int = int(
        os.getenv("TRIGGER_MARKET_SCORE_BASELINE_TOLERANCE_MINUTES", "5")
    )
    TRIGGER_MARKET_SCORE_DELTA_SOFT: float = float(os.getenv("TRIGGER_MARKET_SCORE_DELTA_SOFT", "8"))
    TRIGGER_MARKET_SCORE_DELTA_HARD: float = float(os.getenv("TRIGGER_MARKET_SCORE_DELTA_HARD", "15"))
    TRIGGER_DEFAULT_DEBOUNCE_CYCLES: int = int(os.getenv("TRIGGER_DEFAULT_DEBOUNCE_CYCLES", "2"))
    TRIGGER_DEFAULT_DEBOUNCE_SECONDS: int = int(os.getenv("TRIGGER_DEFAULT_DEBOUNCE_SECONDS", "180"))
    TRIGGER_DEFAULT_COOLDOWN_SECONDS: int = int(os.getenv("TRIGGER_DEFAULT_COOLDOWN_SECONDS", "1800"))
    TRIGGER_DETECTED_EXPIRY_SECONDS: int = int(os.getenv("TRIGGER_DETECTED_EXPIRY_SECONDS", "600"))
    TRIGGER_AUTO_FAST_ANALYSIS_ENABLED: bool = _bool_env("TRIGGER_AUTO_FAST_ANALYSIS_ENABLED", "true")

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

    # Phase E Portfolio Operating System.  Hard caps remain defined by the
    # decision contract; these settings only control market-history and cost
    # calculation inputs shared by the portfolio services.
    PORTFOLIO_CORRELATION_LOOKBACK_DAYS: int = int(os.getenv("PORTFOLIO_CORRELATION_LOOKBACK_DAYS", "60"))
    PORTFOLIO_CORRELATION_MIN_SAMPLES: int = int(os.getenv("PORTFOLIO_CORRELATION_MIN_SAMPLES", "40"))
    PORTFOLIO_HIGH_CORRELATION_THRESHOLD: float = float(
        os.getenv("PORTFOLIO_HIGH_CORRELATION_THRESHOLD", "0.80")
    )
    PORTFOLIO_TRADING_DAYS_PER_YEAR: int = int(os.getenv("PORTFOLIO_TRADING_DAYS_PER_YEAR", "242"))
    PORTFOLIO_BROKER_COMMISSION_BPS: float | None = (
        float(os.environ["PORTFOLIO_BROKER_COMMISSION_BPS"])
        if os.getenv("PORTFOLIO_BROKER_COMMISSION_BPS") not in {None, ""}
        else None
    )
    PORTFOLIO_MINIMUM_COMMISSION: float | None = (
        float(os.environ["PORTFOLIO_MINIMUM_COMMISSION"])
        if os.getenv("PORTFOLIO_MINIMUM_COMMISSION") not in {None, ""}
        else None
    )
    PORTFOLIO_SELL_TAX_BPS: float | None = (
        float(os.environ["PORTFOLIO_SELL_TAX_BPS"])
        if os.getenv("PORTFOLIO_SELL_TAX_BPS") not in {None, ""}
        else None
    )

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


def validate_realtime_monitor_settings(value: Settings = settings) -> bool:
    if value.MONITOR_INTERVAL_SECONDS < 60:
        raise ValueError("MONITOR_INTERVAL_SECONDS must be at least 60")
    if value.MARKET_SCORE_INTERVAL_MINUTES not in {1, 5, 10, 15, 30}:
        raise ValueError("MARKET_SCORE_INTERVAL_MINUTES is unsupported")
    if value.TRIGGER_MARKET_SCORE_DELTA_SOFT >= value.TRIGGER_MARKET_SCORE_DELTA_HARD:
        raise ValueError("TRIGGER_MARKET_SCORE_DELTA_SOFT must be below the hard threshold")
    if value.TRIGGER_DEFAULT_DEBOUNCE_CYCLES < 1:
        raise ValueError("TRIGGER_DEFAULT_DEBOUNCE_CYCLES must be at least 1")
    if value.TRIGGER_DEFAULT_DEBOUNCE_SECONDS < 0:
        raise ValueError("TRIGGER_DEFAULT_DEBOUNCE_SECONDS cannot be negative")
    if value.TRIGGER_DEFAULT_COOLDOWN_SECONDS < 0:
        raise ValueError("TRIGGER_DEFAULT_COOLDOWN_SECONDS cannot be negative")
    if value.TRIGGER_DETECTED_EXPIRY_SECONDS <= 0:
        raise ValueError("TRIGGER_DETECTED_EXPIRY_SECONDS must be positive")
    if value.TRIGGER_MARKET_SCORE_WINDOW_MINUTES <= 0:
        raise ValueError("TRIGGER_MARKET_SCORE_WINDOW_MINUTES must be positive")
    if not 0 <= value.TRIGGER_MARKET_SCORE_BASELINE_TOLERANCE_MINUTES < value.TRIGGER_MARKET_SCORE_WINDOW_MINUTES:
        raise ValueError("TRIGGER_MARKET_SCORE_BASELINE_TOLERANCE_MINUTES must be below the window")
    if value.PORTFOLIO_CORRELATION_LOOKBACK_DAYS <= 0:
        raise ValueError("PORTFOLIO_CORRELATION_LOOKBACK_DAYS must be positive")
    if value.PORTFOLIO_CORRELATION_MIN_SAMPLES < 2:
        raise ValueError("PORTFOLIO_CORRELATION_MIN_SAMPLES must be at least 2")
    if not 0 < value.PORTFOLIO_HIGH_CORRELATION_THRESHOLD <= 1:
        raise ValueError("PORTFOLIO_HIGH_CORRELATION_THRESHOLD must be in (0, 1]")
    if value.PORTFOLIO_TRADING_DAYS_PER_YEAR <= 0:
        raise ValueError("PORTFOLIO_TRADING_DAYS_PER_YEAR must be positive")
    if not 0 < value.DISK_BLOCKED_RATIO < value.DISK_DEGRADED_RATIO < 1:
        raise ValueError("DISK_BLOCKED_RATIO must be below DISK_DEGRADED_RATIO and both must be in (0, 1)")
    if value.BACKUP_BLOCKED_HOURS < value.BACKUP_DEGRADED_HOURS:
        raise ValueError("BACKUP_BLOCKED_HOURS must not be below BACKUP_DEGRADED_HOURS")
    try:
        hour_text, minute_text = value.BACKUP_SCHEDULE_TIME.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except ValueError as exc:
        raise ValueError("BACKUP_SCHEDULE_TIME must use HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("BACKUP_SCHEDULE_TIME must be a valid HH:MM clock time")
    return True


validate_realtime_monitor_settings()
