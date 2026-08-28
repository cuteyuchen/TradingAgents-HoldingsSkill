"""Compatibility exports for the canonical :mod:`app.market.codes` helpers."""
from __future__ import annotations

from .codes import exchange_for_code, normalize_code, normalize_security_code, provider_symbol


def resolve_exchange(value: object, exchange: str | None = None) -> str | None:
    """Resolve explicit exchange hints, otherwise infer from the code."""

    hint = str(exchange or "").strip().upper()
    if hint in {"SH", "SSE"}:
        return "SSE"
    if hint in {"SZ", "SZSE"}:
        return "SZSE"
    if hint in {"BJ", "BSE"}:
        return "BSE"
    return exchange_for_code(value)


def security_symbol(code: object, exchange: str | None = None) -> str:
    normalized = normalize_security_code(code)
    resolved = resolve_exchange(code, exchange)
    suffix = {"SSE": ".SH", "SZSE": ".SZ", "BSE": ".BJ"}.get(resolved or "")
    return f"{normalized}{suffix}" if normalized and suffix else normalized


__all__ = ["exchange_for_code", "normalize_code", "normalize_security_code", "provider_symbol", "resolve_exchange", "security_symbol"]
