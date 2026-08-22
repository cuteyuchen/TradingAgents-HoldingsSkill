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
from .fallback import FallbackQuoteProvider
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
from .health import CircuitBreaker, ProviderHealthRegistry
from .inmemory import FixtureQuoteProvider, InMemoryQuoteProvider, MemoryQuoteProvider
from .tencent import TencentQuoteProvider

__all__ = [
    "CalendarProvider",
    "CircuitBreaker",
    "EastmoneyBatchQuoteProvider",
    "EastmoneyQuoteProvider",
    "FixtureQuoteProvider",
    "FallbackQuoteProvider",
    "InMemoryQuoteProvider",
    "KLineProvider",
    "MarketQuoteSnapshot",
    "NormalizedQuote",
    "ProviderHealthRegistry",
    "ProviderRegistry",
    "QuoteProviderFactory",
    "QuoteProvider",
    "SecurityProvider",
    "TencentQuoteProvider",
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
    "parse_eastmoney_row",
]
