"""Small Provider registry/factory for the Phase B quote chains."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ...config import settings
from .base import QuoteProvider
from .eastmoney import EastmoneyBatchQuoteProvider
from .fallback import FallbackQuoteProvider, HealthTrackedQuoteProvider
from .health import (
    ProviderHealthRegistry,
    get_runtime_provider_health_registry,
)
from .inmemory import InMemoryQuoteProvider
from .acceptance import AcceptanceQuoteProvider
from .tencent import TencentQuoteProvider
from .fuyao import FallbackKLineProvider, FuyaoKLineProvider, FuyaoQuoteProvider


ProviderBuilder = Callable[..., QuoteProvider]


_PROVIDER_ALIASES = {
    "eastmoney": "eastmoney_batch",
    "em": "eastmoney_batch",
    "qq": "tencent",
    "qt_gtimg_cn": "tencent",
    "ths": "fuyao",
    "tonghuashun": "fuyao",
}


def _canonical_name(value: str) -> str:
    canonical = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _PROVIDER_ALIASES.get(canonical, canonical)


class ProviderRegistry:
    """Register and instantiate Providers without coupling to application config."""

    def __init__(self, builders: Mapping[str, ProviderBuilder] | None = None) -> None:
        self._builders: dict[str, ProviderBuilder] = {}
        for name, builder in (builders or {}).items():
            self.register(name, builder)

    def register(self, name: str, builder: ProviderBuilder) -> None:
        canonical = _canonical_name(name)
        if not canonical:
            raise ValueError("provider name is required")
        self._builders[canonical] = builder

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))

    def create(self, name: str, **kwargs: Any) -> QuoteProvider:
        canonical = _canonical_name(name)
        builder = self._builders.get(canonical)
        if builder is None:
            raise ValueError(f"unknown quote provider: {name}")
        value = builder(**kwargs)
        if not isinstance(value, QuoteProvider):
            raise TypeError(f"provider builder {canonical!r} did not return QuoteProvider")
        return value


DEFAULT_PROVIDER_REGISTRY = ProviderRegistry(
    {
        "tencent": TencentQuoteProvider,
        "eastmoney": EastmoneyBatchQuoteProvider,
        "eastmoney_batch": EastmoneyBatchQuoteProvider,
        "fuyao": FuyaoQuoteProvider,
        "inmemory": InMemoryQuoteProvider,
        "memory": InMemoryQuoteProvider,
        "fixture": InMemoryQuoteProvider,
        "acceptance": AcceptanceQuoteProvider,
    }
)


class QuoteProviderFactory:
    """Build explicit critical-holding and all-A fallback chains."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        health: ProviderHealthRegistry | None = None,
        provider_options: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.registry = registry or DEFAULT_PROVIDER_REGISTRY
        self.health = health or get_runtime_provider_health_registry()
        self.provider_options = _configured_provider_options()
        for name, options in (provider_options or {}).items():
            self.provider_options.setdefault(_canonical_name(name), {}).update(options)

    def _create_adapter(self, name: str, **kwargs: Any) -> QuoteProvider:
        canonical = _canonical_name(name)
        if settings.ACCEPTANCE_MODE and canonical not in {
            "acceptance",
            "inmemory",
            "memory",
            "fixture",
        }:
            # Acceptance runs are hermetic even when a developer happens to
            # have a production API key in the host environment.
            return self.registry.create("acceptance")
        options = dict(self.provider_options.get(canonical, {}))
        options.update(kwargs)
        return self.registry.create(name, **options)

    def create(self, name: str, **kwargs: Any) -> QuoteProvider:
        """Create a health-tracked direct Provider using the shared registry."""

        return HealthTrackedQuoteProvider(self._create_adapter(name, **kwargs), health=self.health)

    def build_chain(
        self,
        providers: Iterable[str | QuoteProvider],
        *,
        health: ProviderHealthRegistry | None = None,
    ) -> QuoteProvider:
        instances: list[QuoteProvider] = []
        for provider in providers:
            if isinstance(provider, QuoteProvider):
                instances.append(provider)
            else:
                instances.append(self._create_adapter(provider))
        if not instances:
            raise ValueError("at least one quote provider is required")
        if len(instances) == 1:
            if isinstance(instances[0], HealthTrackedQuoteProvider):
                return instances[0]
            return HealthTrackedQuoteProvider(instances[0], health=health or self.health)
        return FallbackQuoteProvider(instances, health=health or self.health)

    def build_critical_quote_chain(
        self,
        *,
        primary: str | None = None,
        fallbacks: Iterable[str] | None = None,
        health: ProviderHealthRegistry | None = None,
    ) -> QuoteProvider:
        if settings.ACCEPTANCE_MODE:
            return self.build_chain(("acceptance",), health=health)
        primary = primary or settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER
        fallbacks = tuple(
            fallbacks if fallbacks is not None else settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS
        )
        return self.build_chain((primary, *tuple(fallbacks)), health=health)

    def build_all_a_quote_chain(
        self,
        *,
        primary: str | None = None,
        fallbacks: Iterable[str] | None = None,
        health: ProviderHealthRegistry | None = None,
    ) -> QuoteProvider:
        if settings.ACCEPTANCE_MODE:
            return self.build_chain(("acceptance",), health=health)
        primary = primary or settings.MARKET_QUOTE_ALL_A_PRIMARY_PROVIDER
        fallbacks = tuple(
            fallbacks if fallbacks is not None else settings.MARKET_QUOTE_ALL_A_FALLBACK_PROVIDERS
        )
        return self.build_chain((primary, *tuple(fallbacks)), health=health)


def create_quote_provider(name: str, **kwargs: Any) -> QuoteProvider:
    """Convenience constructor backed by the default registry."""

    canonical = _canonical_name(name)
    options = dict(kwargs)
    if canonical == "tencent" and "request" not in options and "transport" in options:
        options["request"] = options.pop("transport")
    elif canonical == "eastmoney_batch" and "transport" not in options and "request" in options:
        options["transport"] = options.pop("request")
    factory = QuoteProviderFactory()
    return factory.create(canonical, **options)


def _configured_provider_options() -> dict[str, dict[str, Any]]:
    """Read adapter knobs from application settings without importing them at module load."""

    try:
        from ...config import settings

        return {
            "eastmoney_batch": {
                "min_interval_seconds": settings.EASTMONEY_MIN_INTERVAL_SECONDS,
            },
            "fuyao": {
                "base_url": settings.FUYAO_BASE_URL,
                "api_key": settings.FUYAO_API_KEY,
                "connect_timeout": settings.FUYAO_CONNECT_TIMEOUT_SECONDS,
                "read_timeout": settings.FUYAO_READ_TIMEOUT_SECONDS,
                "max_retries": settings.FUYAO_MAX_RETRIES,
                "min_interval_seconds": settings.FUYAO_MIN_INTERVAL_SECONDS,
            },
        }
    except (ImportError, AttributeError):
        return {}


def make_provider(
    name: str,
    *,
    transport: Any = None,
    request: Any = None,
    timeout: float | None = None,
    health: ProviderHealthRegistry | None = None,
    **kwargs: Any,
) -> QuoteProvider:
    """Compatibility constructor used by the first Provider draft.

    ``transport`` and ``request`` are aliases because Tencent's adapter calls
    its injected callable ``request`` while Eastmoney calls it ``transport``.
    The function remains side-effect free: constructing a provider never makes
    an HTTP request.
    """

    options = dict(kwargs)
    if timeout is not None:
        options["timeout"] = timeout
    if request is not None and transport is None:
        transport = request
    canonical = _canonical_name(name)
    if canonical in {"tencent", "qq", "qt_gtimg_cn"} and transport is not None:
        options["request"] = transport
    elif canonical in {"eastmoney", "eastmoney_batch", "em"} and transport is not None:
        options["transport"] = transport
    elif canonical == "fuyao" and transport is not None:
        options["transport"] = transport
    provider = QuoteProviderFactory(health=health).create(
        canonical,
        **options,
    )
    return provider


def build_provider_chain(
    providers: Iterable[str | QuoteProvider],
    *,
    health: ProviderHealthRegistry | None = None,
    provider_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> QuoteProvider:
    return QuoteProviderFactory(health=health, provider_options=provider_options).build_chain(
        providers, health=health
    )


def build_quote_provider(
    *,
    route: str = "all_a",
    primary: str | None = None,
    fallbacks: Iterable[str] | None = None,
    transport: Any = None,
    request: Any = None,
    timeout: float | None = None,
    health: ProviderHealthRegistry | None = None,
) -> QuoteProvider:
    """Build a named route while preserving the early registry signature."""

    route_name = _canonical_name(route)
    if primary is None:
        primary = (
            settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER
            if route_name in {"critical", "holding", "quote_critical"}
            else settings.MARKET_QUOTE_ALL_A_PRIMARY_PROVIDER
        )
    if fallbacks is None:
        fallbacks = (
            settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS
            if route_name in {"critical", "holding", "quote_critical"}
            else settings.MARKET_QUOTE_ALL_A_FALLBACK_PROVIDERS
        )
    names = list(dict.fromkeys([primary, *fallbacks]))
    instances = [
        make_provider(name, transport=transport, request=request, timeout=timeout, health=health)
        for name in names
    ]
    if len(instances) == 1:
        return instances[0]
    return FallbackQuoteProvider(instances, health=health or get_runtime_provider_health_registry())


def build_critical_quote_provider(
    *,
    primary: str | None = None,
    fallbacks: Iterable[str] | None = None,
    health: ProviderHealthRegistry | None = None,
    provider_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> QuoteProvider:
    return QuoteProviderFactory(health=health, provider_options=provider_options).build_critical_quote_chain(
        primary=primary, fallbacks=fallbacks, health=health
    )


def build_all_a_quote_provider(
    *,
    primary: str | None = None,
    fallbacks: Iterable[str] | None = None,
    health: ProviderHealthRegistry | None = None,
    provider_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> QuoteProvider:
    return QuoteProviderFactory(health=health, provider_options=provider_options).build_all_a_quote_chain(
        primary=primary, fallbacks=fallbacks, health=health
    )


def build_kline_provider(
    *,
    name: str | None = None,
    fallback: bool = True,
    fuyao_options: Mapping[str, Any] | None = None,
) -> Any:
    """Build the explicit historical provider chain used by sync jobs."""

    selected = _canonical_name(name or settings.HISTORICAL_KLINE_PROVIDER)
    if selected in {"fuyao", "fuyao_historical"}:
        primary = FuyaoKLineProvider(**dict(fuyao_options or {}))
        if not fallback:
            return primary
        from ..engine.history import LegacyMarketDataHistoryProvider

        return FallbackKLineProvider([primary, LegacyMarketDataHistoryProvider()])
    if selected in {"eastmoney", "eastmoney_daily_qfq", "legacy", "legacy_eastmoney"}:
        from ..engine.history import LegacyMarketDataHistoryProvider

        return LegacyMarketDataHistoryProvider()
    raise ValueError(f"unknown kline provider: {name}")


# Naming variants used by early Phase B callers.
get_quote_provider = create_quote_provider
build_quote_chain = build_provider_chain


__all__ = [
    "DEFAULT_PROVIDER_REGISTRY",
    "ProviderRegistry",
    "QuoteProviderFactory",
    "build_all_a_quote_provider",
    "build_kline_provider",
    "build_provider_chain",
    "build_quote_provider",
    "build_quote_chain",
    "build_critical_quote_provider",
    "create_quote_provider",
    "get_quote_provider",
    "make_provider",
]
