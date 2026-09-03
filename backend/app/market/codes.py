"""Canonical A-share security-code helpers."""
from __future__ import annotations

import re


_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_EXCHANGE_PREFIX_RE = re.compile(r"^(SH|SSE|SZ|SZSE|BJ|BSE)[.\-_/ ]*(\d{6})$")
_EXCHANGE_SUFFIX_RE = re.compile(r"^(\d{6})[.\-_/ ]*(SH|SSE|SZ|SZSE|BJ|BSE)$")

_EXCHANGE_ALIASES = {
    "SH": "SSE",
    "SSE": "SSE",
    "SZ": "SZSE",
    "SZSE": "SZSE",
    "BJ": "BSE",
    "BSE": "BSE",
}


def normalize_security_code(value: object) -> str:
    """Return the six-digit internal code for common A-share spellings.

    Supported examples include ``600519``, ``sh600519``, ``SH600519`` and
    ``600519.SH``.  Invalid/ambiguous values return an empty string rather than
    leaking a vendor-specific symbol into the normalized layer.
    """

    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = _EXCHANGE_PREFIX_RE.fullmatch(text)
    if match:
        return match.group(2)
    match = _EXCHANGE_SUFFIX_RE.fullmatch(text)
    if match:
        return match.group(1)
    match = _CODE_RE.search(text)
    if not match:
        return ""
    # A six-digit code embedded in a longer run (for example an account id)
    # is not a safe security identifier.
    digits = match.group(1)
    compact = re.sub(r"[.\-_/ ]", "", text)
    if compact not in {
        digits,
        *(f"{prefix}{digits}" for prefix in _EXCHANGE_ALIASES),
    }:
        # Keep support for common prefixes/suffixes with punctuation while
        # rejecting arbitrary strings containing a code.
        return ""
    return digits


def exchange_hint(value: object) -> str | None:
    """Return an explicit exchange token from a qualified security code."""

    text = str(value or "").strip().upper()
    if not text:
        return None
    match = _EXCHANGE_PREFIX_RE.fullmatch(text)
    if match:
        return _EXCHANGE_ALIASES[match.group(1)]
    match = _EXCHANGE_SUFFIX_RE.fullmatch(text)
    if match:
        return _EXCHANGE_ALIASES[match.group(2)]
    return None


def normalize_exchange(value: object) -> str | None:
    """Normalize exchange aliases without consulting locale or a provider."""

    if value is None:
        return None
    text = str(value).strip().upper()
    return _EXCHANGE_ALIASES.get(text, text or None)


def canonical_security_code(value: object, exchange: object = None) -> str:
    """Return one exchange-qualified identity such as ``600519.SH``."""

    code = normalize_security_code(value)
    if not code:
        return ""
    resolved_exchange = exchange_hint(value) or normalize_exchange(exchange) or exchange_for_code(code)
    suffix = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(resolved_exchange or "")
    return f"{code}.{suffix}" if suffix else ""


def exchange_for_code(code: object) -> str | None:
    """Infer the exchange for a six-digit mainland security code."""

    normalized = normalize_security_code(code)
    if not normalized:
        return None
    # Beijing Stock Exchange started using the 920xxx range in 2024.  Check it
    # before Shanghai's 900xxx B-share range and do not classify every 9xxxxx
    # code as Shanghai.
    if normalized.startswith("920"):
        return "BSE"
    if normalized.startswith(("5", "6", "900")):
        return "SSE"
    # Shenzhen-listed ETFs commonly use the 15/16/18 ranges in addition to
    # ordinary 0/2/3-prefixed shares.
    if normalized.startswith(("0", "1", "2", "3")):
        return "SZSE"
    if normalized.startswith(("4", "8")):
        return "BSE"
    return None


def provider_symbol(code: object, provider: str) -> str:
    """Build the exchange-prefixed symbol used by common quote endpoints."""

    normalized = normalize_security_code(code)
    if not normalized:
        return ""
    exchange = exchange_for_code(normalized)
    prefix = {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}.get(exchange or "", "")
    provider_name = provider.lower().strip()
    if provider_name in {"tencent", "sina", "qt.gtimg.cn", "hq.sinajs.cn"}:
        return f"{prefix}{normalized}" if prefix else normalized
    return normalized


# Compatibility spelling for callers migrating from the old facade.
normalize_code = normalize_security_code
