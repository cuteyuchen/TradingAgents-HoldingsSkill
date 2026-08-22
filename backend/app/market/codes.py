"""Canonical A-share security-code helpers."""
from __future__ import annotations

import re


_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_EXCHANGE_PREFIX_RE = re.compile(r"^(SH|SZ|BJ)[.\-_/ ]*(\d{6})$")
_EXCHANGE_SUFFIX_RE = re.compile(r"^(\d{6})[.\-_/ ]*(SH|SZ|BJ)$")


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
    if text.replace(" ", "") not in {digits, f"SH{digits}", f"SZ{digits}", f"BJ{digits}", f"{digits}.SH", f"{digits}.SZ", f"{digits}.BJ"}:
        # Keep support for common prefixes/suffixes with punctuation while
        # rejecting arbitrary strings containing a code.
        compact = re.sub(r"[.\-_/ ]", "", text)
        if compact not in {digits, f"SH{digits}", f"SZ{digits}", f"BJ{digits}"}:
            return ""
    return digits


def exchange_for_code(code: object) -> str | None:
    """Infer the exchange for a six-digit mainland security code."""

    normalized = normalize_security_code(code)
    if not normalized:
        return None
    if normalized.startswith(("5", "6", "9")):
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
