"""Explicit quote-provider fallback orchestration."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from time import monotonic

from ..codes import exchange_for_code, normalize_security_code
from ..models import DataQualityStatus, NormalizedQuote
from .base import QuoteProvider, apply_quote_validation
from .health import ProviderHealthRegistry, get_runtime_provider_health_registry


class ProviderCircuitOpen(RuntimeError):
    """Raised when a single provider is blocked by its runtime circuit."""


def _provider_name(provider: QuoteProvider) -> str:
    return str(
        getattr(provider, "name", provider.__class__.__name__)
        or provider.__class__.__name__
    ).lower()


def _quote_freshness_seconds() -> float:
    try:
        from ...config import settings

        return float(settings.QUOTE_FRESHNESS_SECONDS)
    except (ImportError, AttributeError, TypeError, ValueError):
        return 90.0


def _normalized_batch(
    batch: Mapping[str, NormalizedQuote],
    *,
    provider_name: str,
    now: datetime,
) -> dict[str, NormalizedQuote]:
    if not isinstance(batch, Mapping):
        raise TypeError("provider returned a non-mapping quote batch")
    normalized: dict[str, NormalizedQuote] = {}
    for key, raw_quote in batch.items():
        try:
            quote = (
                deepcopy(raw_quote)
                if isinstance(raw_quote, NormalizedQuote)
                else NormalizedQuote.from_mapping(raw_quote, provider=provider_name)
            )
        except (TypeError, ValueError):
            continue
        code = normalize_security_code(quote.code or key)
        if not code:
            continue
        quote.code = code
        quote.provider = provider_name
        normalized[code] = apply_quote_validation(
            quote,
            now=now,
            max_age_seconds=_quote_freshness_seconds(),
        )
    return normalized


class HealthTrackedQuoteProvider(QuoteProvider):
    """Decorate a single adapter with process-wide health accounting.

    Fallback chains do their own per-source accounting because they need to
    distinguish partial responses.  This decorator is for direct provider
    routes (for example an explicitly requested ``tencent`` route).
    """

    def __init__(self, provider: QuoteProvider, *, health: ProviderHealthRegistry | None = None) -> None:
        self.provider = provider
        self.health = health or get_runtime_provider_health_registry()
        self.name = _provider_name(provider)
        self.endpoint = str(getattr(provider, "endpoint", "") or "")
        self.last_errors: list[dict[str, object]] = []
        self.last_provider_counts: dict[str, int] = {}
        self.last_provider_endpoints: dict[str, str] = {}
        self.last_fallback_level = 0
        self.last_latency_ms: dict[str, float] = {}
        self.last_provider_attempts: list[dict[str, object]] = []

    def __getattr__(self, name: str):
        return getattr(self.provider, name)

    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        requested = list(dict.fromkeys(normalize_security_code(code) for code in codes if normalize_security_code(code)))
        self.last_errors = []
        self.last_provider_counts = {}
        self.last_provider_endpoints = {self.name: self.endpoint} if self.endpoint else {}
        self.last_latency_ms = {}
        self.last_provider_attempts = []
        if not requested:
            return {}
        if not self.health.allow(self.name):
            self.last_errors = [{"provider": self.name, "error_code": "circuit_open", "message": "provider circuit is open"}]
            self.last_provider_attempts = [
                {
                    "provider": self.name,
                    "endpoint": self.endpoint or None,
                    "status": "circuit_open",
                    "latency_ms": None,
                    "contribution_count": 0,
                }
            ]
            raise ProviderCircuitOpen("provider circuit is open")
        started = monotonic()
        try:
            raw_result = self.provider.get_quotes(requested) or {}
            if not isinstance(raw_result, Mapping):
                raise TypeError("provider returned a non-mapping quote batch")
            result = _normalized_batch(raw_result, provider_name=self.name, now=datetime.now(UTC))
            requested_set = set(requested)
            for code in sorted(set(result) - requested_set):
                result.pop(code, None)
                self.last_errors.append(
                    {
                        "provider": self.name,
                        "code": code,
                        "error_code": "unexpected_quote",
                        "message": "provider returned a quote outside the requested universe",
                    }
                )
        except Exception as exc:
            latency_ms = (monotonic() - started) * 1000
            self.health.record_failure(self.name, str(exc), latency_ms=latency_ms)
            self.last_errors = [{"provider": self.name, "error_code": "provider_failure", "message": str(exc)}]
            self.last_latency_ms = {self.name: latency_ms}
            self.last_provider_attempts = [
                {
                    "provider": self.name,
                    "endpoint": self.endpoint or None,
                    "status": "failure",
                    "latency_ms": latency_ms,
                    "contribution_count": 0,
                }
            ]
            raise
        latency_ms = (monotonic() - started) * 1000
        usable = sum(
            quote.quality_status in {DataQualityStatus.VALID, DataQualityStatus.DEGRADED}
            for quote in result.values()
        )
        if usable:
            self.health.record_success(self.name, latency_ms=latency_ms)
        else:
            self.health.record_failure(self.name, "all_quotes_unusable", latency_ms=latency_ms)
        self.last_errors = [
            *list(getattr(self.provider, "last_errors", []) or []),
            *self.last_errors,
        ]
        if not usable:
            self.last_errors.append({"provider": self.name, "error_code": "all_quotes_unusable", "message": "provider returned no usable quotes"})
        self.last_provider_counts = {self.name: usable} if usable else {}
        self.last_latency_ms = {self.name: latency_ms}
        self.last_provider_attempts = [
            {
                "provider": self.name,
                "endpoint": self.endpoint or None,
                "status": "success" if usable else "unusable",
                "latency_ms": latency_ms,
                "contribution_count": usable,
            }
        ]
        return result

    def get_all_a_share_quotes(self, universe: Iterable[str]) -> dict[str, NormalizedQuote]:
        return self.get_quotes(universe)

    def get_run_metadata(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "provider_counts": dict(self.last_provider_counts),
            "provider_endpoints": dict(self.last_provider_endpoints),
            "provider_attempts": list(self.last_provider_attempts),
            "fallback_level": 0,
            "fallback_errors": list(self.last_errors),
        }


class FallbackQuoteProvider(QuoteProvider):
    """Try providers in order and preserve the primary error in diagnostics."""

    name = "fallback"

    def __init__(
        self,
        providers: Iterable[QuoteProvider],
        *,
        health: ProviderHealthRegistry | None = None,
        allow_stale: bool = False,
    ) -> None:
        self.providers = [
            provider.provider if isinstance(provider, HealthTrackedQuoteProvider) else provider
            for provider in providers
        ]
        self.health = health or get_runtime_provider_health_registry()
        self.allow_stale = bool(allow_stale)
        self.last_errors: list[dict[str, object]] = []
        self.last_provider_counts: dict[str, int] = {}
        self.last_provider_endpoints: dict[str, str] = {}
        self.last_fallback_level: int = 0
        self.last_latency_ms: dict[str, float] = {}
        self.last_provider_attempts: list[dict[str, object]] = []

    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        requested = list(dict.fromkeys(normalize_security_code(code) for code in codes if normalize_security_code(code)))
        remaining = requested[:]
        results: dict[str, NormalizedQuote] = {}
        errors: list[dict[str, object]] = []
        provider_counts: dict[str, int] = {}
        provider_endpoints: dict[str, str] = {}
        provider_latencies: dict[str, float] = {}
        provider_attempts: list[dict[str, object]] = []
        self.last_errors = []
        self.last_provider_counts = {}
        self.last_provider_endpoints = {}
        self.last_fallback_level = 0
        self.last_latency_ms = {}
        self.last_provider_attempts = []
        for level, provider in enumerate(self.providers):
            provider_name = _provider_name(provider)
            if not remaining:
                break
            if not self.health.allow(provider_name):
                errors.append({"provider": provider_name, "error_code": "circuit_open", "message": "provider circuit is open"})
                provider_attempts.append(
                    {
                        "provider": provider_name,
                        "fallback_level": level,
                        "endpoint": str(getattr(provider, "endpoint", "") or "") or None,
                        "status": "circuit_open",
                        "latency_ms": None,
                        "contribution_count": 0,
                    }
                )
                continue
            started = monotonic()
            provider_endpoints[provider_name] = str(getattr(provider, "endpoint", "") or "")
            try:
                batch = provider.get_quotes(remaining) or {}
                if not isinstance(batch, Mapping):
                    raise TypeError("provider returned a non-mapping quote batch")
                # Normalize inside the guarded provider boundary so malformed
                # adapter output contributes to circuit health like a transport failure.
                by_code = _normalized_batch(
                    batch,
                    provider_name=provider_name,
                    now=datetime.now(UTC),
                )
            except Exception as exc:
                latency_ms = (monotonic() - started) * 1000
                provider_latencies[provider_name] = latency_ms
                self.health.record_failure(provider_name, str(exc), latency_ms=latency_ms)
                errors.append({"provider": provider_name, "error_code": "provider_failure", "message": str(exc)})
                provider_attempts.append(
                    {
                        "provider": provider_name,
                        "fallback_level": level,
                        "endpoint": provider_endpoints[provider_name] or None,
                        "status": "failure",
                        "latency_ms": latency_ms,
                        "contribution_count": 0,
                    }
                )
                continue
            latency_ms = (monotonic() - started) * 1000
            provider_latencies[provider_name] = latency_ms
            # Some adapters return symbols such as ``sh600519`` while others
            # use the internal six-digit code; ``by_code`` is canonical here.
            next_remaining: list[str] = []
            usable_count = 0
            for code in remaining:
                quote = by_code.get(code)
                if quote is None:
                    next_remaining.append(code)
                    continue
                unusable = {DataQualityStatus.MISSING, DataQualityStatus.INVALID, DataQualityStatus.CONFLICT}
                if not self.allow_stale:
                    unusable.add(DataQualityStatus.STALE)
                if quote.quality_status in unusable:
                    # Preserve the provider diagnostic but let the next
                    # source attempt a fresh quote.
                    if quote.errors:
                        errors.extend(
                            {
                                "provider": provider_name,
                                "code": code,
                                "error_code": "quote_unusable",
                                "message": message,
                            }
                            for message in quote.errors
                        )
                    next_remaining.append(code)
                    continue
                quote = deepcopy(quote)
                quote.code = code
                quote.fallback_level = level
                quote.provider = provider_name
                results[code] = quote
                usable_count += 1
                provider_counts[provider_name] = provider_counts.get(provider_name, 0) + 1
            remaining = next_remaining
            if usable_count:
                self.health.record_success(provider_name, latency_ms=latency_ms)
            else:
                # A transport-level 200 with only MISSING/INVALID quotes is
                # still a provider failure for circuit-breaker purposes.
                reason = "all_quotes_unusable"
                self.health.record_failure(provider_name, reason, latency_ms=latency_ms)
                errors.append({"provider": provider_name, "error_code": reason, "message": "provider returned no usable quotes"})
            provider_attempts.append(
                {
                    "provider": provider_name,
                    "fallback_level": level,
                    "endpoint": provider_endpoints[provider_name] or None,
                    "status": "success" if usable_count else "unusable",
                    "latency_ms": latency_ms,
                    "contribution_count": usable_count,
                }
            )
        for code in remaining:
            errors.append({"code": code, "error_code": "all_providers_failed", "message": "no compatible quote returned"})
            results[code] = NormalizedQuote(
                code=code,
                exchange=exchange_for_code(code),
                provider=self.name,
                fallback_level=max(len(self.providers) - 1, 0),
                quality_status=DataQualityStatus.MISSING,
                errors=[
                    str(item["message"])
                    for item in errors
                    if item.get("code") in {None, code} and item.get("message")
                ],
                metadata={"provider_errors": [item for item in errors if item.get("code") in {None, code}]},
            )
        # Attach the primary failure to fallback quotes for auditability, while
        # retaining structured errors on the provider object itself.
        for quote in results.values():
            prior_errors = [
                str(item.get("message"))
                for item in errors
                if item.get("provider")
                and item.get("provider") != quote.provider
                and item.get("code") in {None, quote.code}
                and item.get("message")
            ]
            quote.errors.extend(item for item in prior_errors if item not in quote.errors)
        self.last_errors = errors
        self.last_provider_counts = provider_counts
        self.last_provider_endpoints = {key: value for key, value in provider_endpoints.items() if value}
        self.last_fallback_level = max(
            [int(item.fallback_level or 0) for item in results.values()],
            default=0,
        )
        self.last_latency_ms = provider_latencies
        self.last_provider_attempts = provider_attempts
        return results

    def get_all_a_share_quotes(self, universe: Iterable[str]) -> dict[str, NormalizedQuote]:
        return self.get_quotes(universe)

    def get_run_metadata(self) -> dict[str, object]:
        providers = list(self.last_provider_counts)
        actual_provider = providers[0] if len(providers) == 1 else "mixed" if providers else self.name
        return {
            "provider": actual_provider,
            "provider_counts": dict(self.last_provider_counts),
            "provider_endpoints": dict(self.last_provider_endpoints),
            "provider_attempts": list(self.last_provider_attempts),
            "fallback_level": self.last_fallback_level,
            "fallback_errors": list(self.last_errors),
        }
