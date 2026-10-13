"""Settlement rules shared by real-account projection and shadow execution."""
from __future__ import annotations

from collections.abc import Mapping

from ..market_models import SecurityMaster


def settlement_policy(security: SecurityMaster | None, security_type: str) -> str:
    """Only explicit ETF metadata can establish T+0; unknown rules stay T+1."""

    kind = str(security_type or "UNKNOWN").upper()
    if kind == "STOCK":
        return "T_PLUS_1"
    if kind != "ETF":
        return "T_PLUS_1_CONSERVATIVE"
    metadata = security.raw_metadata_json if security and isinstance(security.raw_metadata_json, Mapping) else {}
    for key in ("settlement", "settlement_policy", "trading_rule", "etf_settlement"):
        value = str(metadata.get(key) or "").upper().replace("+", "_PLUS_").replace("-", "_")
        if value in {"T_0", "T_PLUS_0", "T0", "TPLUS0"}:
            return "T_PLUS_0"
        if value in {"T_1", "T_PLUS_1", "T1", "TPLUS1"}:
            return "T_PLUS_1"
    return "T_PLUS_1_CONSERVATIVE"
