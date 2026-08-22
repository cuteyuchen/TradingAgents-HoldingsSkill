"""Small Provider registry/factory for the Phase B quote chains."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .base import QuoteProvider
from .eastmoney import EastmoneyBatchQuoteProvider
from .fallback import FallbackQuoteProvider
from .health import ProviderHealthRegistry
from .inmemory import InMemoryQuoteProvider
from .tencent import TencentQuoteProvider


ProviderBuilder = Callable[..., QuoteProvider]


def _canonical_name(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


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
        "inmemory": InMemoryQuoteProvider,
        "memory": InMemoryQuoteProvider,
        "fixture": InMemoryQuoteProvider,
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
        self.health = health or ProviderHealthRegistry()
        self.provider_options = {
            _canonical_name(name): dict(options) for name, options in (provider_options or {}).items()
        }

    def create(self, name: str, **kwargs: Any) -> QuoteProvider:
        options = dict(self.provider_options.get(_canonical_name(name), {}))
        options.update(kwargs)
        return self.registry.create(name, **options)

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
                instances.append(self.create(provider))
        if not instances:
            raise ValueError("at least one quote provider is required")
        if len(instances) == 1:
            return instances[0]
        return FallbackQuoteProvider(instances, health=health or self.health)

    def build_critical_quote_chain(
        self,
        *,
        primary: str = "tencent",
        fallbacks: Iterable[str] = ("eastmoney_batch",),
        health: ProviderHealthRegistry | None = None,
    ) -> QuoteProvider:
        return self.build_chain((primary, *tuple(fallbacks)), health=health)

    def build_all_a_quote_chain(
        self,
        *,
        primary: str = "eastmoney_batch",
        fallbacks: Iterable[str] = ("tencent",),
        health: ProviderHealthRegistry | None = None,
    ) -> QuoteProvider:
        return self.build_chain((primary, *tuple(fallbacks)), health=health)


def create_quote_provider(name: str, **kwargs: Any) -> QuoteProvider:
    """Convenience constructor backed by the default registry."""

    canonical = _canonical_name(name)
    canonical = {"qq": "tencent", "qt_gtimg_cn": "tencent", "em": "eastmoney_batch"}.get(canonical, canonical)
    options = dict(kwargs)
    if canonical == "tencent" and "request" not in options and "transport" in options:
        options["request"] = options.pop("transport")
    elif canonical == "eastmoney_batch" and "transport" not in options and "request" in options:
        options["transport"] = options.pop("request")
    return QuoteProviderFactory().create(canonical, **options)


def make_provider(
    name: str,
    *,
    transport: Any = None,
    request: Any = None,
    timeout: float | None = None,
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
    return create_quote_provider(
        {"qq": "tencent", "qt_gtimg_cn": "tencent", "em": "eastmoney_batch"}.get(canonical, canonical),
        **options,
    )


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
        primary = "tencent" if route_name in {"critical", "holding", "quote_critical"} else "eastmoney_batch"
    if fallbacks is None:
        fallbacks = ("eastmoney_batch",) if route_name in {"critical", "holding", "quote_critical"} else ("tencent",)
    names = list(dict.fromkeys([primary, *fallbacks]))
    instances = [make_provider(name, transport=transport, request=request, timeout=timeout) for name in names]
    if len(instances) == 1:
        return instances[0]
    return FallbackQuoteProvider(instances, health=health or ProviderHealthRegistry())


def build_critical_quote_provider(
    *,
    primary: str = "tencent",
    fallbacks: Iterable[str] = ("eastmoney_batch",),
    health: ProviderHealthRegistry | None = None,
    provider_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> QuoteProvider:
    return QuoteProviderFactory(health=health, provider_options=provider_options).build_critical_quote_chain(
        primary=primary, fallbacks=fallbacks, health=health
    )


def build_all_a_quote_provider(
    *,
    primary: str = "eastmoney_batch",
    fallbacks: Iterable[str] = ("tencent",),
    health: ProviderHealthRegistry | None = None,
    provider_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> QuoteProvider:
    return QuoteProviderFactory(health=health, provider_options=provider_options).build_all_a_quote_chain(
        primary=primary, fallbacks=fallbacks, health=health
    )


# Naming variants used by early Phase B callers.
get_quote_provider = create_quote_provider
build_quote_chain = build_provider_chain


__all__ = [
    "DEFAULT_PROVIDER_REGISTRY",
    "ProviderRegistry",
    "QuoteProviderFactory",
    "build_all_a_quote_provider",
    "build_provider_chain",
    "build_quote_provider",
    "build_quote_chain",
    "build_critical_quote_provider",
    "create_quote_provider",
    "get_quote_provider",
    "make_provider",
]
