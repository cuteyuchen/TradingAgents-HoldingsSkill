"""Deterministic Fuyao context analytics for the personal workbench.

This module intentionally produces evidence and explanations only.  It never
computes or mutates the frozen Market Score, Candidate Score, Portfolio Gate,
or Shadow contracts.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
import math
from threading import RLock
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .codes import normalize_security_code
from .models import NormalizedQuote
from .providers.fuyao import FuyaoDataProvider, FuyaoQuoteProvider, _rows
from .providers.fuyao_client import (
    ERROR_DATA_MISSING,
    ERROR_PERMISSION,
    ERROR_RATE_LIMIT,
    FuyaoAPIError,
    FuyaoClient,
    client_from_settings,
)
from .providers.factory import build_critical_quote_provider


CHINA_TZ = ZoneInfo("Asia/Shanghai")
PIT_CURRENT_ALLOWED = "CURRENT_ANALYSIS_ALLOWED"
PIT_HISTORICAL_UNKNOWN = "HISTORICAL_PIT_NOT_PROVEN"
PIT_NON_PIT_CONTEXT = "NON_PIT_CONTEXT"

CAPABILITIES = (
    "quotes",
    "calendar",
    "historical",
    "market_dumps",
    "corporate_actions",
    "financials",
    "valuation",
    "index",
    "fund",
    "special_data",
)
CAPABILITY_PROBE_ENDPOINTS = {
    "quotes": ("/api/a-share/prices/snapshot", {"thscodes": "600519.SH"}),
    "calendar": ("/api/a-share/calendar/trading-days", None),
    "historical": (
        "/api/a-share/prices/historical",
        {"thscode": "600519.SH", "interval": "1d", "start": 1735689600000, "end": 1735862400000, "adjust": "none"},
    ),
    "market_dumps": ("/api/dump/market-dumps", None),
    "corporate_actions": ("/api/a-share/corporate-actions/adjustment-factors", {"thscode": "600519.SH"}),
    "financials": ("/api/a-share/financials/income-statements", {"thscode": "600519.SH", "period": "annual", "limit": 1}),
    "valuation": ("/api/a-share/valuations/snapshot", {"thscodes": "600519.SH"}),
    "index": ("/api/a-share-index/catalog/ths-index-list", {"tag": "industry"}),
    "fund": ("/api/fund/market/snapshot", {"thscode": "510300.SH"}),
    "special_data": ("/api/a-share/special-data/limit-up-pool", {"page": 1, "size": 1}),
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "null"):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _code(value: Any) -> str:
    return normalize_security_code(value)


def _data(response: Any) -> Mapping[str, Any]:
    value = getattr(response, "data", response)
    return value if isinstance(value, Mapping) else {}


def _source_error(error: Exception) -> dict[str, Any]:
    if isinstance(error, FuyaoAPIError):
        return error.to_dict()
    return {"category": "UPSTREAM_FAILURE", "message": error.__class__.__name__.lower()}


def _trend(current: Any, previous: Any) -> str:
    now = _number(current)
    before = _number(previous)
    if now is None or before is None:
        return "数据不足"
    if math.isclose(now, before, rel_tol=0.01, abs_tol=1e-9):
        return "稳定"
    return "改善" if now > before else "走弱"


def _latest_and_previous(rows: Iterable[Mapping[str, Any]], field: str) -> tuple[float | None, float | None]:
    ordered = sorted(
        (row for row in rows if isinstance(row, Mapping)),
        key=lambda row: (
            str(row.get("period_end_ms") or row.get("report_date_ms") or row.get("period_end") or ""),
        ),
    )
    values = [_number(row.get(field)) for row in ordered]
    values = [value for value in values if value is not None]
    return (values[-1], values[-2] if len(values) > 1 else None) if values else (None, None)


def normalize_valuation(response_or_data: Any) -> list[dict[str, Any]]:
    data = _data(response_or_data)
    result: list[dict[str, Any]] = []
    for raw in _rows(data):
        code = _code(raw.get("thscode") or raw.get("ticker"))
        if not code:
            continue
        result.append(
            {
                "code": code,
                "thscode": raw.get("thscode"),
                "pe_ttm": _number(raw.get("pe_ttm")),
                "pe_mrq": _number(raw.get("pe_mrq")),
                "pb_mrq": _number(raw.get("pb_mrq")),
                "ps_ttm": _number(raw.get("ps_ttm")),
                "pcf_ttm": _number(raw.get("pcf_ttm")),
            }
        )
    return result


def summarize_valuation(response_or_data: Any) -> dict[str, Any]:
    rows = normalize_valuation(response_or_data)
    current = rows[0] if rows else None
    metric_count = sum(current.get(key) is not None for key in ("pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm")) if current else 0
    return {
        "status": "AVAILABLE" if metric_count else "MISSING",
        "label": "中性" if metric_count else "数据不足",
        "current": current,
        "historical_position": "历史样本不足",
        "industry_relative": "历史样本不足",
        "historical_sample_count": 0,
        "pit_status": PIT_CURRENT_ALLOWED,
        "historical_pit_status": PIT_HISTORICAL_UNKNOWN,
        "missing_aware": metric_count == 0,
        "source": "fuyao",
    }


def summarize_financials(
    financials: Mapping[str, Any] | None,
    indicators: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    financials = financials or {}
    income = _rows(_data(financials.get("income")))
    balance = _rows(_data(financials.get("balance")))
    cash_flow = _rows(_data(financials.get("cash_flow")))
    revenue, revenue_previous = _latest_and_previous(income, "operating_income")
    profit, profit_previous = _latest_and_previous(income, "net_profit")
    operating_cash, operating_cash_previous = _latest_and_previous(cash_flow, "act_cash_flow_net")
    total_assets, _ = _latest_and_previous(balance, "assets_total")
    total_debt, _ = _latest_and_previous(balance, "total_debt")
    debt_ratio = total_debt / total_assets if total_debt is not None and total_assets and total_assets > 0 else None
    indicator_summary = summarize_indicators(indicators)
    return {
        "status": "AVAILABLE" if any(value is not None for value in (revenue, profit, operating_cash, total_assets)) else "MISSING",
        "growth": {"label": _trend(revenue, revenue_previous), "latest_revenue": revenue, "previous_revenue": revenue_previous},
        "profitability": {"label": _trend(profit, profit_previous), "latest_net_profit": profit, "previous_net_profit": profit_previous},
        "balance_sheet": {"label": "正常" if debt_ratio is None or debt_ratio < 0.8 else "需关注", "debt_ratio": debt_ratio, "total_assets": total_assets},
        "cash_flow": {"label": "正常" if operating_cash is not None and operating_cash >= 0 else "需关注" if operating_cash is not None else "数据不足", "latest_operating_cash_flow": operating_cash, "previous_operating_cash_flow": operating_cash_previous},
        "indicators": indicator_summary,
        "pit_status": PIT_CURRENT_ALLOWED,
        "historical_pit_status": PIT_HISTORICAL_UNKNOWN,
        "evidence_status": PIT_NON_PIT_CONTEXT,
        "source": "fuyao",
    }


def summarize_indicators(response_or_data: Any) -> dict[str, Any]:
    data = _data(response_or_data)
    groups: dict[str, dict[str, Any]] = {}
    for ability in data.get("abilities") or []:
        if not isinstance(ability, Mapping):
            continue
        key = str(ability.get("ability_id") or ability.get("ability") or ability.get("id") or "unknown").lower()
        values: dict[str, Any] = {}
        for item in ability.get("indicators") or []:
            if not isinstance(item, Mapping):
                continue
            indicator_id = str(item.get("index_id") or item.get("indicator_id") or "").strip()
            if indicator_id:
                values[indicator_id] = _number(item.get("value"))
        if values:
            groups[key] = {"values": values, "available": True}
    labels = {
        "growth": "成长",
        "profitability": "盈利",
        "solvency": "偿债",
        "operation": "营运",
        "cash-flow": "现金流质量",
        "cash_flow": "现金流质量",
    }
    return {
        labels.get(key, key): value
        for key, value in groups.items()
    }


def fundamental_summary(
    financials: Mapping[str, Any] | None,
    indicators: Mapping[str, Any] | None,
    valuation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    financial = summarize_financials(financials, indicators)
    valuation_summary = valuation or summarize_valuation({})
    return {
        "growth": financial["growth"]["label"],
        "profitability": financial["profitability"]["label"],
        "cash_flow": financial["cash_flow"]["label"],
        "valuation": valuation_summary.get("label", "数据不足"),
        "status": "AVAILABLE" if financial["status"] == "AVAILABLE" or valuation_summary.get("status") == "AVAILABLE" else "MISSING",
        "pit_status": PIT_CURRENT_ALLOWED,
        "historical_pit_status": PIT_HISTORICAL_UNKNOWN,
        "evidence": {"financials": financial, "valuation": valuation_summary},
    }


def calculate_portfolio_contributions(
    holdings: Iterable[Mapping[str, Any]],
    quotes: Mapping[str, NormalizedQuote | Mapping[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    known_market_value = 0.0
    known_count = 0
    requested_count = 0
    for raw in holdings:
        code = _code(raw.get("code"))
        if not code:
            continue
        requested_count += 1
        quote = quotes.get(code)
        quote_dict = quote.to_dict() if isinstance(quote, NormalizedQuote) else dict(quote or {})
        quality = str(getattr(quote_dict.get("quality_status"), "value", quote_dict.get("quality_status") or "MISSING")).upper()
        quote_usable = quality in {"VALID", "DEGRADED"}
        price = _number(quote_dict.get("price") or quote_dict.get("last_price")) if quote_usable else None
        qty = _number(raw.get("qty"))
        market_value = price * qty if price is not None and qty is not None else None
        if market_value is not None and market_value >= 0:
            known_market_value += market_value
            known_count += 1
        rows.append({
            "code": code,
            "name": raw.get("name"),
            "qty": qty,
            "confirmed_market_value": _number(raw.get("market_value")),
            "current_price": price,
            "today_change_pct": _number(quote_dict.get("pct_change")) if quote_usable else None,
            "market_value": market_value,
            "contribution_pct": None,
            "quote_quality": quality,
            "provider": quote_dict.get("provider"),
        })
    for row in rows:
        if row["market_value"] is not None and known_market_value > 0 and row["today_change_pct"] is not None:
            row["contribution_pct"] = row["market_value"] / known_market_value * row["today_change_pct"]
    return {
        "items": rows,
        "requested_count": requested_count,
        "quoted_count": known_count,
        "coverage": known_count / requested_count if requested_count else 1.0,
        "known_market_value": known_market_value if known_count else None,
        "missing_quote_count": requested_count - known_count,
        "quality_status": "VALID" if requested_count == known_count else "DEGRADED" if known_count else "MISSING",
        "missing_aware": True,
    }


@dataclass(frozen=True, slots=True)
class DailyMarketBrief:
    risk: dict[str, Any]
    breadth: dict[str, Any]
    turnover: dict[str, Any]
    major_indices: list[dict[str, Any]]
    industry: dict[str, Any]
    sentiment: dict[str, Any]
    data_quality: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "breadth": self.breadth,
            "turnover": self.turnover,
            "major_indices": self.major_indices,
            "industry": self.industry,
            "sentiment": self.sentiment,
            "data_quality": self.data_quality,
        }


class _TTLCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}
        self._lock = RLock()

    def get(self, key: str, now: float) -> Any:
        with self._lock:
            value = self._values.get(key)
            if value is None or value[0] <= now:
                self._values.pop(key, None)
                return None
            return value[1]

    def set(self, key: str, value: Any, expires_at: float) -> Any:
        with self._lock:
            self._values[key] = (expires_at, value)
        return value


_cache = _TTLCache()


def _status_from_error(error: Exception) -> str:
    if isinstance(error, FuyaoAPIError):
        if error.code == 2003:
            return "未授权"
        if error.code == 2001:
            return "未配置"
        if error.code == 4001:
            return "限流"
        if error.code == 3002:
            return "数据未就绪"
        if error.code == 3004:
            return "不支持"
    return "上游异常"


def _payload_count(data: Mapping[str, Any]) -> int | None:
    """Count an explicitly returned collection without turning absent data into zero."""

    pagination = data.get("pagination")
    if isinstance(pagination, Mapping) and pagination.get("total") is not None:
        return int(pagination["total"])
    for key in ("item", "items", "rows", "list", "stock_items", "data"):
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, Mapping):
            return len(value)
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            return len(list(value))
    rows = _rows(data)
    return len(rows) if rows else None


def probe_capabilities(
    client: FuyaoClient | None = None,
    *,
    probe: bool = False,
    now: Callable[[], float] | None = None,
) -> dict[str, Any]:
    client = client or client_from_settings()
    try:
        from ..config import settings
    except ImportError:
        settings = None
    if settings is not None and settings.ACCEPTANCE_MODE:
        return {
            "provider": "fuyao",
            "configured": False,
            "connection_status": "FIXTURE",
            "capabilities": {key: {"status": "FIXTURE", "code": None} for key in CAPABILITIES},
        }
    if not client.configured:
        return {
            "provider": "fuyao",
            "configured": False,
            "connection_status": "未配置",
            "capabilities": {key: {"status": "未配置", "code": None} for key in CAPABILITIES},
        }
    if not probe:
        return {
            "provider": "fuyao",
            "configured": True,
            "connection_status": "已配置",
            "capabilities": {key: {"status": "待探测", "code": None} for key in CAPABILITIES},
        }
    statuses: dict[str, Any] = {}
    for capability, (endpoint, params) in CAPABILITY_PROBE_ENDPOINTS.items():
        try:
            response = client.get(endpoint, params=params, capability=capability)
            statuses[capability] = {"status": "已连接", "code": response.code, "request_id": response.request_id, "latency_ms": response.latency_ms}
        except Exception as exc:
            code = exc.code if isinstance(exc, FuyaoAPIError) else None
            statuses[capability] = {"status": _status_from_error(exc), "code": code, "request_id": getattr(exc, "request_id", None), "message": getattr(exc, "safe_message", exc.__class__.__name__.lower())}
    connected = [value["status"] for value in statuses.values() if value["status"] == "已连接"]
    connection_status = "已连接" if connected else next(iter((value["status"] for value in statuses.values())), "上游异常")
    return {"provider": "fuyao", "configured": True, "connection_status": connection_status, "capabilities": statuses}


class FuyaoAnalyticsService:
    def __init__(
        self,
        *,
        provider: FuyaoDataProvider | None = None,
        client: FuyaoClient | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client or (provider.client if provider is not None else client_from_settings())
        self.provider = provider or FuyaoDataProvider(client=self.client)
        self.now = now or (lambda: datetime.now(CHINA_TZ))

    def market_brief(self, score: Mapping[str, Any] | None = None, *, force_refresh: bool = False) -> DailyMarketBrief:
        today = self.now().astimezone(CHINA_TZ).date().isoformat()
        key = f"market-brief:{today}"
        if not force_refresh:
            cached = _cache.get(key, datetime.now(UTC).timestamp())
            if cached is not None:
                return cached
        try:
            from ..config import settings
        except ImportError:
            settings = None
        if settings is not None and settings.ACCEPTANCE_MODE:
            fixture = DailyMarketBrief(
                risk={"market_score": (score or {}).get("display_score", 58.0), "regime": (score or {}).get("regime", "NEUTRAL"), "status": "FIXTURE"},
                breadth={"advance_ratio": ((score or {}).get("core_metrics") or {}).get("advance_ratio", 0.52), "included_count": ((score or {}).get("universe") or {}).get("included", 6)},
                turnover={"total_amount": ((score or {}).get("core_metrics") or {}).get("total_amount", 1_250_000_000.0), "top5_concentration": ((score or {}).get("core_metrics") or {}).get("top5_concentration", 0.31), "top_count": ((score or {}).get("core_metrics") or {}).get("top_count", 1)},
                major_indices=[
                    {"thscode": "000001.SH", "name": "上证指数", "price": 3388.2, "change_pct": 0.42, "source": "acceptance"},
                    {"thscode": "399001.SZ", "name": "深证成指", "price": 10642.8, "change_pct": 0.18, "source": "acceptance"},
                    {"thscode": "399006.SZ", "name": "创业板指", "price": 2186.5, "change_pct": -0.27, "source": "acceptance"},
                ],
                industry={"leaders": [{"name": "通信", "change_pct": 1.22}, {"name": "有色金属", "change_pct": 0.84}], "laggards": [{"name": "医药生物", "change_pct": -0.63}], "status": "FIXTURE"},
                sentiment={"limit_up_count": 43, "limit_down_count": 7, "hot_stock_count": 30, "abnormal_count": 12, "dragon_tiger_count": 18},
                data_quality={"provider": "acceptance", "configured": False, "status": "FIXTURE", "missing": []},
            )
            return _cache.set(key, fixture, datetime.now(UTC).timestamp() + 60)
        if not self.client.configured:
            brief = DailyMarketBrief(
                risk={"market_score": (score or {}).get("display_score"), "regime": (score or {}).get("regime"), "status": "数据受限"},
                breadth={"advance_ratio": ((score or {}).get("core_metrics") or {}).get("advance_ratio")},
                turnover={"total_amount": ((score or {}).get("core_metrics") or {}).get("total_amount"), "top5_concentration": ((score or {}).get("core_metrics") or {}).get("top5_concentration")},
                major_indices=[],
                industry={"leaders": [], "laggards": [], "status": "未配置"},
                sentiment={"limit_up_count": None, "limit_down_count": None, "hot_stock_count": None, "abnormal_count": None, "dragon_tiger_count": None},
                data_quality={"provider": "fuyao", "configured": False, "status": "DEGRADED", "missing": ["fuyao_api_key"]},
            )
            return _cache.set(key, brief, datetime.now(UTC).timestamp() + 30)

        errors: list[dict[str, Any]] = []
        major_indices: list[dict[str, Any]] = []
        try:
            response = self.provider.get_index_snapshot(("000001.SH", "399001.SZ", "399006.SZ"))
            data = _data(response)
            for raw in _rows(data):
                major_indices.append({
                    "thscode": raw.get("thscode"),
                    "name": raw.get("name") or raw.get("ticker"),
                    "price": _number(raw.get("last_price")),
                    "change_pct": _number(raw.get("price_change_ratio_pct")),
                    "source": "fuyao",
                })
        except Exception as exc:
            errors.append({"domain": "index", **_source_error(exc)})

        industry_rows: list[dict[str, Any]] = []
        try:
            catalog = _data(self.provider.get_index_catalog(tag="industry"))
            catalog_rows = _rows(catalog)[:12]
            codes = [str(row.get("thscode")) for row in catalog_rows if row.get("thscode")]
            snapshots = _rows(_data(self.provider.get_index_snapshot(codes))) if codes else []
            by_code = {str(row.get("thscode")): row for row in snapshots}
            for row in catalog_rows:
                code = str(row.get("thscode") or "")
                snap = by_code.get(code, {})
                industry_rows.append({
                    "thscode": code or None,
                    "name": row.get("name") or row.get("index_name"),
                    "change_pct": _number(snap.get("price_change_ratio_pct")),
                    "breadth": _number(row.get("breadth")),
                })
            industry_rows.sort(key=lambda item: item["change_pct"] if item["change_pct"] is not None else -math.inf, reverse=True)
        except Exception as exc:
            errors.append({"domain": "industry", **_source_error(exc)})

        sentiment: dict[str, Any] = {"limit_up_count": None, "limit_down_count": None, "hot_stock_count": None, "abnormal_count": None, "dragon_tiger_count": None}
        for field, endpoint, params in (
            ("limit_up_count", "limit-up-pool", {"page": 1, "size": 200}),
            ("limit_down_count", "limit-down-pool", {"page": 1, "size": 200}),
            ("hot_stock_count", "hot-stock-list", {"period": "day"}),
            ("abnormal_count", "anomaly-analysis-list", {}),
            ("dragon_tiger_count", "dragon-tiger-list", {"board_type": "all", "date": today}),
        ):
            try:
                data = _data(self.provider.get_special(endpoint, params=params))
                explicit_count = _number(data.get("count")) if isinstance(data, Mapping) else None
                if explicit_count is not None:
                    sentiment[field] = int(explicit_count)
                elif isinstance(data, Mapping):
                    sentiment[field] = _payload_count(data)
            except Exception as exc:
                errors.append({"domain": field, **_source_error(exc)})

        core = (score or {}).get("core_metrics") or {}
        brief = DailyMarketBrief(
            risk={"market_score": (score or {}).get("display_score"), "regime": (score or {}).get("regime"), "status": (score or {}).get("quality_status") or "MISSING"},
            breadth={"advance_ratio": core.get("advance_ratio"), "included_count": ((score or {}).get("universe") or {}).get("included")},
            turnover={"total_amount": core.get("total_amount"), "top5_concentration": core.get("top5_concentration"), "top_count": core.get("top_count")},
            major_indices=major_indices,
            industry={"leaders": industry_rows[:3], "laggards": list(reversed(industry_rows[-3:])) if industry_rows else [], "status": "AVAILABLE" if industry_rows else "MISSING"},
            sentiment=sentiment,
            data_quality={"provider": "fuyao", "configured": True, "status": "VALID" if not errors else "DEGRADED", "errors": errors},
        )
        return _cache.set(key, brief, datetime.now(UTC).timestamp() + 60)

    def security_context(self, code: str) -> dict[str, Any]:
        normalized = normalize_security_code(code)
        if not normalized:
            raise ValueError("invalid_security_code")
        try:
            from ..config import settings
        except ImportError:
            settings = None
        try:
            quote_provider = build_critical_quote_provider()
            quote = quote_provider.get_quotes([normalized]).get(normalized)
            quote_payload = quote.to_dict() if quote else None
        except Exception as exc:
            quote_payload = {"code": normalized, "quality_status": "MISSING", "error": _source_error(exc)}
        if not self.client.configured or (settings is not None and settings.ACCEPTANCE_MODE):
            valuation = summarize_valuation({})
            financial = summarize_financials({})
            return {
                "code": normalized,
                "quote": quote_payload,
                "fundamental_summary": fundamental_summary({}, {}, valuation),
                "valuation": valuation,
                "financials": financial,
                "pit_status": PIT_CURRENT_ALLOWED,
                "historical_pit_status": PIT_HISTORICAL_UNKNOWN,
                "evidence": {"status": "FIXTURE" if settings is not None and settings.ACCEPTANCE_MODE else "未配置"},
            }

        valuation_response = None
        try:
            valuation_response = self.provider.get_valuation([normalized])
            valuation = summarize_valuation(valuation_response)
        except Exception as exc:
            valuation = {**summarize_valuation({}), "error": _source_error(exc)}
        financial_responses: dict[str, Any] = {}
        for name, endpoint in (("income", "/api/a-share/financials/income-statements"), ("balance", "/api/a-share/financials/balance-sheets"), ("cash_flow", "/api/a-share/financials/cash-flow-statements")):
            try:
                financial_responses[name] = self.provider.get(endpoint, params={"thscode": f"{normalized}.{'SH' if normalized.startswith(('5', '6', '9')) else 'SZ' if normalized.startswith(('0', '1', '2', '3')) else 'BJ'}", "period": "annual", "limit": 4}, capability="financials")
            except Exception as exc:
                financial_responses[name] = {"error": _source_error(exc)}
        try:
            report = next((value for value in financial_responses.values() if not isinstance(value, Mapping) or "error" not in value), None)
            indicators = self.provider.get_indicators(normalized, f"{self.now().year - 1}-4")
        except Exception:
            indicators = None
        financial = summarize_financials(financial_responses, indicators)
        return {
            "code": normalized,
            "quote": quote_payload,
            "fundamental_summary": fundamental_summary(financial_responses, indicators, valuation),
            "valuation": valuation,
            "financials": financial,
            "pit_status": PIT_CURRENT_ALLOWED,
            "historical_pit_status": PIT_HISTORICAL_UNKNOWN,
            "evidence": {"source": "fuyao", "financial_report": report is not None if 'report' in locals() else False},
        }


__all__ = [
    "CAPABILITIES",
    "DailyMarketBrief",
    "FuyaoAnalyticsService",
    "calculate_portfolio_contributions",
    "fundamental_summary",
    "normalize_valuation",
    "probe_capabilities",
    "summarize_financials",
    "summarize_indicators",
    "summarize_valuation",
]
