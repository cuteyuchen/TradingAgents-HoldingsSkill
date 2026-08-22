"""Explicit quote-provider fallback orchestration."""
from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy

from ..codes import exchange_for_code, normalize_security_code
from ..models import DataQualityStatus, NormalizedQuote
from .base import QuoteProvider
from .health import ProviderHealthRegistry


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
        self.providers = list(providers)
        self.health = health or ProviderHealthRegistry()
        self.allow_stale = bool(allow_stale)
        self.last_errors: list[dict[str, object]] = []

    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        requested = list(dict.fromkeys(normalize_security_code(code) for code in codes if normalize_security_code(code)))
        remaining = requested[:]
        results: dict[str, NormalizedQuote] = {}
        errors: list[dict[str, object]] = []
        for level, provider in enumerate(self.providers):
            provider_name = str(getattr(provider, "name", provider.__class__.__name__) or provider.__class__.__name__).lower()
            if not remaining:
                break
            if not self.health.allow(provider_name):
                errors.append({"provider": provider_name, "error_code": "circuit_open", "message": "provider circuit is open"})
                continue
            try:
                batch = provider.get_quotes(remaining) or {}
                self.health.record_success(provider_name)
            except Exception as exc:
                self.health.record_failure(provider_name, str(exc))
                errors.append({"provider": provider_name, "error_code": "provider_failure", "message": str(exc)})
                continue
            # Normalize provider keys once.  Some adapters return symbols such
            # as ``sh600519`` while others use the internal six-digit code.
            by_code: dict[str, NormalizedQuote] = {}
            for key, raw_quote in batch.items():
                try:
                    quote = raw_quote if isinstance(raw_quote, NormalizedQuote) else NormalizedQuote.from_mapping(raw_quote, provider=provider_name)
                except (TypeError, ValueError):
                    continue
                code = normalize_security_code(quote.code or key)
                if code:
                    by_code[code] = quote
            next_remaining: list[str] = []
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
            remaining = next_remaining
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
        return results

    def get_all_a_share_quotes(self, universe: Iterable[str]) -> dict[str, NormalizedQuote]:
        return self.get_quotes(universe)
