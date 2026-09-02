"""Phase B market-data domain primitives.

This package deliberately contains no database, HTTP router, or portfolio
decision code.  Providers translate source-specific payloads into the small
contracts exported here; callers can therefore replace a provider without
leaking vendor field names into business logic.
"""

from .codes import exchange_for_code, normalize_security_code
from .health import (
    CircuitBreaker,
    CircuitState,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderHealthTracker,
)
from .models import (
    DataQualityStatus,
    NormalizedQuote,
    QuoteComparison,
    QuoteSnapshot,
    QuoteValidation,
)
from .quality import (
    compare_quotes,
    freshness_seconds,
    is_stale,
    validate_normalized_quote,
    validate_quote,
)
from .providers import (
    DEFAULT_PROVIDER_REGISTRY,
    EastmoneyBatchQuoteProvider,
    FallbackKLineProvider,
    FallbackQuoteProvider,
    HealthTrackedQuoteProvider,
    ProviderCircuitOpen,
    InMemoryQuoteProvider,
    ProviderRegistry,
    QuoteProvider,
    QuoteProviderFactory,
    TencentQuoteProvider,
    FuyaoCalendarProvider,
    FuyaoDataProvider,
    FuyaoKLineProvider,
    FuyaoQuoteProvider,
    FuyaoSecurityProvider,
    build_all_a_quote_provider,
    build_critical_quote_provider,
    build_provider_chain,
    build_quote_provider,
    build_kline_provider,
    create_quote_provider,
    make_provider,
    get_runtime_provider_health_registry,
    reset_runtime_provider_health_registry,
)

__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "DataQualityStatus",
    "DEFAULT_PROVIDER_REGISTRY",
    "EastmoneyBatchQuoteProvider",
    "FallbackKLineProvider",
    "FallbackQuoteProvider",
    "HealthTrackedQuoteProvider",
    "ProviderCircuitOpen",
    "InMemoryQuoteProvider",
    "NormalizedQuote",
    "ProviderHealth",
    "ProviderHealthStatus",
    "ProviderHealthTracker",
    "ProviderRegistry",
    "QuoteProvider",
    "QuoteProviderFactory",
    "QuoteComparison",
    "QuoteSnapshot",
    "QuoteValidation",
    "compare_quotes",
    "build_all_a_quote_provider",
    "build_critical_quote_provider",
    "build_provider_chain",
    "build_quote_provider",
    "build_kline_provider",
    "create_quote_provider",
    "exchange_for_code",
    "freshness_seconds",
    "is_stale",
    "normalize_security_code",
    "validate_normalized_quote",
    "validate_quote",
    "TencentQuoteProvider",
    "FuyaoCalendarProvider",
    "FuyaoDataProvider",
    "FuyaoKLineProvider",
    "FuyaoQuoteProvider",
    "FuyaoSecurityProvider",
    "make_provider",
    "get_runtime_provider_health_registry",
    "reset_runtime_provider_health_registry",
]
