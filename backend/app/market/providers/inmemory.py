"""Deterministic, network-free quote Provider used by tests and local fixtures."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from ..codes import exchange_for_code, normalize_security_code
from ..models import DataQualityStatus, NormalizedQuote
from .base import QuoteProvider


class InMemoryQuoteProvider(QuoteProvider):
    """Serve a quote fixture with the same batch contract as live Providers.

    Fixtures may be a mapping keyed by code, an iterable of mappings, or
    already-normalized quote objects.  Returned objects are copies so fallback
    orchestration cannot mutate the fixture stored by a test.
    """

    name = "inmemory"

    def __init__(
        self,
        quotes: Mapping[str, Any] | Iterable[Any] | None = None,
        *,
        provider: str = "inmemory",
        include_missing: bool = True,
    ) -> None:
        self.name = str(provider or "inmemory").strip().lower() or "inmemory"
        self.include_missing = bool(include_missing)
        self._quotes: dict[str, NormalizedQuote] = {}
        self._load(quotes or {})

    def _load(self, quotes: Mapping[str, Any] | Iterable[Any]) -> None:
        items: Iterable[tuple[Any, Any]]
        if isinstance(quotes, Mapping):
            items = quotes.items()
        else:
            items = ((None, item) for item in quotes)
        for key, raw in items:
            if isinstance(raw, NormalizedQuote):
                quote = deepcopy(raw)
                if not quote.provider:
                    quote.provider = self.name
            elif isinstance(raw, Mapping):
                data = dict(raw)
                if key is not None and not data.get("code"):
                    data["code"] = key
                quote = NormalizedQuote.from_mapping(data, provider=self.name)
            else:
                continue
            code = normalize_security_code(quote.code or key)
            if not code:
                continue
            quote.code = code
            if quote.exchange is None:
                quote.exchange = exchange_for_code(code)
            if not quote.provider:
                quote.provider = self.name
            self._quotes[code] = quote

    @property
    def fixture_size(self) -> int:
        return len(self._quotes)

    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        normalized = list(dict.fromkeys(normalize_security_code(code) for code in codes if normalize_security_code(code)))
        now = datetime.now(UTC)
        if not normalized:
            return {}
        result: dict[str, NormalizedQuote] = {}
        for code in normalized:
            quote = self._quotes.get(code)
            if quote is not None:
                result[code] = deepcopy(quote)
            elif self.include_missing:
                result[code] = NormalizedQuote(
                    code=code,
                    exchange=exchange_for_code(code),
                    provider=self.name,
                    fetched_at=now,
                    quality_status=DataQualityStatus.MISSING,
                    errors=["quote_missing"],
                )
        return result

    def get_all_a_share_quotes(self, universe: Iterable[str]) -> dict[str, NormalizedQuote]:
        return self.get_quotes(universe)


# Fixture-oriented compatibility aliases.
FixtureQuoteProvider = InMemoryQuoteProvider
MemoryQuoteProvider = InMemoryQuoteProvider


__all__ = ["FixtureQuoteProvider", "InMemoryQuoteProvider", "MemoryQuoteProvider"]
