"""Provider interfaces and concrete quote adapters."""

from .base import (
    CalendarProvider,
    KLineProvider,
    MarketQuoteSnapshot,
    NormalizedQuote,
    QuoteProvider,
    SecurityProvider,
    build_quote_snapshot,
)
from .eastmoney import EastmoneyBatchQuoteProvider, EastmoneyQuoteProvider, parse_eastmoney_row
from .fallback import FallbackQuoteProvider, HealthTrackedQuoteProvider, ProviderCircuitOpen
from .factory import (
    DEFAULT_PROVIDER_REGISTRY,
    ProviderRegistry,
    QuoteProviderFactory,
    build_all_a_quote_provider,
    build_critical_quote_provider,
    build_provider_chain,
    build_quote_chain,
    create_quote_provider,
    get_quote_provider,
    make_provider,
    build_quote_provider,
)
from .health import (
    CircuitBreaker,
    ProviderHealthRegistry,
    get_runtime_provider_health_registry,
    reset_runtime_provider_health_registry,
    runtime_provider_health_snapshot,
)
from .inmemory import FixtureQuoteProvider, InMemoryQuoteProvider, MemoryQuoteProvider
from .acceptance import AcceptanceQuoteProvider
from .tencent import TencentQuoteProvider
from .identity import (
    EastmoneyCalendarProvider,
    EastmoneySecurityProvider,
    OfficialCNCalendarProvider,
    build_calendar_provider,
    build_security_provider,
)

__all__ = [
    "CalendarProvider",
    "CircuitBreaker",
    "EastmoneyBatchQuoteProvider",
    "EastmoneyQuoteProvider",
    "FixtureQuoteProvider",
    "FallbackQuoteProvider",
    "HealthTrackedQuoteProvider",
    "ProviderCircuitOpen",
    "InMemoryQuoteProvider",
    "KLineProvider",
    "MarketQuoteSnapshot",
    "NormalizedQuote",
    "ProviderHealthRegistry",
    "get_runtime_provider_health_registry",
    "reset_runtime_provider_health_registry",
    "runtime_provider_health_snapshot",
    "ProviderRegistry",
    "QuoteProviderFactory",
    "QuoteProvider",
    "SecurityProvider",
    "TencentQuoteProvider",
    "EastmoneyCalendarProvider",
    "EastmoneySecurityProvider",
    "OfficialCNCalendarProvider",
    "build_calendar_provider",
    "build_security_provider",
    "DEFAULT_PROVIDER_REGISTRY",
    "build_all_a_quote_provider",
    "build_critical_quote_provider",
    "build_provider_chain",
    "build_quote_chain",
    "build_quote_snapshot",
    "build_quote_provider",
    "create_quote_provider",
    "get_quote_provider",
    "make_provider",
    "MemoryQuoteProvider",
    "AcceptanceQuoteProvider",
    "parse_eastmoney_row",
]
