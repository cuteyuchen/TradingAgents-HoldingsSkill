"""Deterministic market facts for the isolated browser acceptance environment."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from ..codes import exchange_for_code, normalize_security_code
from ...clock import utc_now
from ...config import settings
from ..models import NormalizedQuote
from .base import QuoteProvider


class AcceptanceQuoteProvider(QuoteProvider):
    """Generate stable values without network access or production fallback."""

    name = "acceptance"

    @staticmethod
    def _trade_date(now: datetime) -> date:
        try:
            return date.fromisoformat(settings.ACCEPTANCE_TRADE_DATE)
        except ValueError:
            return now.date()

    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        if not settings.ACCEPTANCE_MODE:
            raise RuntimeError("Acceptance quote provider requires ACCEPTANCE_MODE=true")
        now = utc_now()
        trade_date = self._trade_date(now)
        result: dict[str, NormalizedQuote] = {}
        for raw_code in codes:
            code = normalize_security_code(raw_code)
            if not code or code == "999999":
                continue
            seed = sum((index + 1) * ord(char) for index, char in enumerate(code))
            price = round(8.0 + (seed % 1700) / 100, 2)
            previous = round(price * 0.99, 2)
            names = {
                "600519": "贵州茅台",
                "000001": "平安银行",
                "601318": "中国平安",
                "510300": "沪深300ETF",
                "159915": "创业板ETF",
                "300750": "宁德时代",
            }
            result[code] = NormalizedQuote(
                code=code,
                exchange=exchange_for_code(code),
                name=names.get(code, f"验收标的 {code}"),
                security_type="ETF" if code.startswith(("15", "16", "51", "56", "58")) else "STOCK",
                price=price,
                prev_close=previous,
                open=previous,
                high=round(price * 1.01, 2),
                low=round(price * 0.99, 2),
                pct_change=round((price / previous - 1) * 100, 4),
                volume=1000000 + seed,
                amount=100000000 + seed * 100,
                turnover_rate=2.5,
                trade_date=trade_date,
                source_timestamp=now,
                fetched_at=now,
                provider=self.name,
                raw_reference="acceptance://quote-fixture",
                metadata={"fixture": "phase-o.1", "deterministic": True},
            )
        return result


__all__ = ["AcceptanceQuoteProvider"]
