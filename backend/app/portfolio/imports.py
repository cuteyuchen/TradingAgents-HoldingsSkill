"""Broker statement import: tolerant CSV parsing, preview, and dedupe keys."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market.codes import normalize_security_code
from ..portfolio_models import TradeLedgerEntry

CHINA_TZ = ZoneInfo("Asia/Shanghai")
MAX_IMPORT_ROWS = 2000

# canonical field -> header aliases (normalized: lowercase, no spaces)
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_date": ("成交日期", "交易日期", "发生日期", "日期", "date", "trade_date", "tradedate"),
    "executed_time": ("成交时间", "交易时间", "时间", "time", "executed_at", "成交时刻"),
    "security_code": ("证券代码", "股票代码", "证券编码", "代码", "code", "symbol", "ts_code"),
    "security_name": ("证券名称", "股票名称", "名称", "name", "证券简称"),
    "side": ("买卖方向", "买卖标志", "交易类别", "操作", "委托方向", "买卖", "side", "direction", "bs"),
    "quantity": ("成交数量", "成交股数", "成交量", "数量", "quantity", "qty", "shares", "volume"),
    "price": ("成交价格", "成交均价", "成交价", "价格", "price", "avg_price", "成交单价"),
    "gross_amount": ("成交金额", "成交额", "金额", "gross_amount", "amount", "turnover"),
    "fees": ("手续费", "佣金", "交易费用", "费用", "fees", "commission", "手续费费"),
    "taxes": ("印花税", "税费", "其它费", "其他费", "taxes", "stamp_tax", "stampduty"),
    "net_amount": ("发生金额", "净金额", "本次金额", "收付金额", "net_amount", "settlement_amount"),
    "broker_order_id": ("合同编号", "委托编号", "成交编号", "委托单号", "申请编号", "broker_order_id", "order_id"),
    "currency": ("币种", "货币", "currency"),
    "entry_hint": ("业务名称", "摘要", "业务类型", "交易类型", "发生业务", "summary", "business"),
}

SIDE_ALIASES = {
    "买入": "BUY", "买": "BUY", "证券买入": "BUY", "融资买入": "BUY", "buy": "BUY", "b": "BUY",
    "卖出": "SELL", "卖": "SELL", "证券卖出": "SELL", "融券卖出": "SELL", "sell": "SELL", "s": "SELL",
}

ENTRY_HINT_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("股息", "红利", "分红派息"), "DIVIDEND"),
    (("利息归本", "利息入账", "利息"), "CASH_IN"),
    (("银证转入", "银行转证券", "转入"), "TRANSFER_IN"),
    (("银证转出", "证券转银行", "转出"), "TRANSFER_OUT"),
    (("申购", "配号", "中签"), "OTHER"),
    (("手续费", "佣金", "费用"), "FEE"),
    (("印花税", "税额", "扣税"), "TAX"),
)

_FIELD_ORDER = (
    "trade_date", "executed_time", "security_code", "security_name", "side", "quantity",
    "price", "gross_amount", "fees", "taxes", "net_amount", "broker_order_id", "currency", "entry_hint",
)


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s\u3000_（）()]+", "", str(value or "")).lower()


def _decode(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace"), "utf-8-replace"


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:5])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        counts = {value: sample.count(value) for value in (",", "\t", ";", "|")}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def _detect_mapping(headers: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(header): header for header in headers if str(header).strip()}
    mapping: dict[str, str] = {}
    for field in _FIELD_ORDER:
        for alias in FIELD_ALIASES[field]:
            header = normalized.get(_normalize_header(alias))
            if header is not None:
                mapping[field] = header
                break
    return mapping


def _apply_mapping_overrides(
    mapping: dict[str, str], headers: list[str], overrides: dict[str, Any] | None
) -> tuple[dict[str, str], list[str]]:
    issues: list[str] = []
    available = {str(header).strip(): header for header in headers}
    for field, header in (overrides or {}).items():
        if field not in FIELD_ALIASES:
            issues.append(f"unsupported_mapping_field:{field}")
            continue
        if header is None or str(header).strip() == "":
            mapping.pop(field, None)
            continue
        target = available.get(str(header).strip())
        if target is None:
            issues.append(f"mapping_header_not_found:{field}:{header}")
            continue
        mapping[field] = target
    return mapping, issues


def _parse_number(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    cleaned = re.sub(r"[^0-9.]", "", text)
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d{4})[-/年.]?(\d{1,2})[-/月.]?(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _parse_time(value: Any) -> time | None:
    text = str(value or "").strip()
    match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if not match:
        return None
    hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return time(hour, minute, second)


def _row_get(raw: dict[str, str], mapping: dict[str, str], field: str) -> str:
    header = mapping.get(field)
    return str(raw.get(header, "") or "").strip() if header else ""


def _entry_type_from_hint(hint: str) -> str | None:
    text = str(hint or "").strip()
    if not text:
        return None
    for keywords, entry_type in ENTRY_HINT_ALIASES:
        if any(keyword in text for keyword in keywords):
            return entry_type
    return None


def natural_key(payload: dict[str, Any]) -> str:
    """Stable identity for dedupe: broker order id when present, else the trade tuple."""

    order_id = str(payload.get("broker_order_id") or "").strip()
    if order_id:
        return f"order:{order_id}"
    entry_type = str(payload.get("entry_type") or "").upper()
    code = normalize_security_code(payload.get("security_code")) or ""
    side = str(payload.get("side") or "").upper()
    trade_date = payload.get("trade_date")
    if trade_date in (None, "") and payload.get("executed_at"):
        try:
            moment = datetime.fromisoformat(str(payload["executed_at"]).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=CHINA_TZ)
            trade_date = moment.astimezone(CHINA_TZ).date()
        except ValueError:
            trade_date = None
    trade_date_text = trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date or "")
    quantity = payload.get("quantity")
    price = payload.get("price")
    amount = payload.get("net_amount")
    parts = [
        entry_type,
        code,
        side,
        trade_date_text,
        f"{float(quantity):.6f}" if isinstance(quantity, (int, float)) else "",
        f"{float(price):.6f}" if isinstance(price, (int, float)) else "",
        f"{float(amount):.2f}" if isinstance(amount, (int, float)) else "",
    ]
    return "|".join(parts)


def import_idempotency_key(source_ref: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(natural_key(payload).encode("utf-8")).hexdigest()[:32]
    return f"import:{source_ref}:{digest}"


def _existing_keys(db: Session, *, portfolio_id: int) -> tuple[set[str], dict[str, int]]:
    rows = db.execute(
        select(TradeLedgerEntry).where(
            TradeLedgerEntry.portfolio_id == portfolio_id,
            TradeLedgerEntry.status.in_(("CONFIRMED", "PENDING_REVIEW")),
        )
    ).scalars().all()
    keys: set[str] = set()
    order_index: dict[str, int] = {}
    for entry in rows:
        payload = {
            "entry_type": entry.entry_type,
            "security_code": entry.security_code,
            "side": entry.side,
            "trade_date": entry.trade_date,
            "quantity": entry.quantity,
            "price": entry.price,
            "net_amount": entry.net_amount,
            "broker_order_id": entry.broker_order_id,
        }
        key = natural_key(payload)
        keys.add(key)
        if entry.broker_order_id:
            order_index[str(entry.broker_order_id).strip()] = entry.id
    return keys, order_index


def _normalize_row(
    raw: dict[str, str],
    mapping: dict[str, str],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    code_value = _row_get(raw, mapping, "security_code")
    code = normalize_security_code(code_value) if code_value else None
    side_text = _row_get(raw, mapping, "side")
    side = SIDE_ALIASES.get(side_text.strip().lower()) or SIDE_ALIASES.get(side_text.strip())
    hint = _row_get(raw, mapping, "entry_hint")
    trade_date = _parse_date(_row_get(raw, mapping, "trade_date"))
    executed_time = _parse_time(_row_get(raw, mapping, "executed_time"))
    quantity = _parse_number(_row_get(raw, mapping, "quantity"))
    price = _parse_number(_row_get(raw, mapping, "price"))
    gross_amount = _parse_number(_row_get(raw, mapping, "gross_amount"))
    fees = _parse_number(_row_get(raw, mapping, "fees"))
    taxes = _parse_number(_row_get(raw, mapping, "taxes"))
    net_amount = _parse_number(_row_get(raw, mapping, "net_amount"))
    order_id = _row_get(raw, mapping, "broker_order_id") or None
    entry_type: str | None = None
    looks_like_trade = bool(code) and side in {"BUY", "SELL"} and quantity is not None
    if looks_like_trade:
        entry_type = "TRADE"
    else:
        entry_type = _entry_type_from_hint(hint)
        if entry_type is None and (net_amount is not None or gross_amount is not None or quantity is not None):
            issues.append({"field": "entry_type", "code": "unresolved_entry_type", "message": "无法判断这一行的业务类型"})
    if trade_date is None:
        issues.append({"field": "trade_date", "code": "missing_trade_date", "message": "缺少成交日期"})
    if entry_type == "TRADE":
        if not code:
            issues.append({"field": "security_code", "code": "missing_code", "message": "成交行缺少证券代码"})
        if side not in {"BUY", "SELL"}:
            issues.append({"field": "side", "code": "missing_side", "message": "成交行缺少买卖方向"})
        if quantity is None or quantity <= 0:
            issues.append({"field": "quantity", "code": "missing_quantity", "message": "成交行缺少数量"})
        if price is None or price <= 0:
            issues.append({"field": "price", "code": "missing_price", "message": "成交行缺少价格"})
    elif entry_type in {"CASH_IN", "CASH_OUT", "DIVIDEND", "FEE", "TAX", "TRANSFER_IN", "TRANSFER_OUT", "CORPORATE_ACTION", "OTHER"}:
        if net_amount is None and gross_amount is None:
            issues.append({"field": "net_amount", "code": "missing_amount", "message": "资金行缺少金额"})
    if issues:
        return None, issues
    executed_at = datetime.combine(
        trade_date,
        executed_time or time(15, 0),
        tzinfo=CHINA_TZ,
    ).astimezone(UTC)
    payload: dict[str, Any] = {
        "entry_type": entry_type,
        "security_code": code,
        "security_name": _row_get(raw, mapping, "security_name") or None,
        "side": side if entry_type == "TRADE" else None,
        "quantity": quantity if entry_type == "TRADE" else None,
        "price": price if entry_type == "TRADE" else None,
        "gross_amount": abs(gross_amount) if gross_amount is not None else None,
        "fees": abs(fees) if fees is not None else None,
        "taxes": abs(taxes) if taxes is not None else None,
        "net_amount": abs(net_amount) if net_amount is not None else None,
        "currency": (_row_get(raw, mapping, "currency") or "CNY").upper(),
        "executed_at": executed_at.isoformat(),
        "trade_date": trade_date.isoformat(),
        "broker_order_id": order_id,
    }
    if executed_time is None:
        payload["assumed_execution_time"] = "15:00 Asia/Shanghai (收盘时间占位)"
    return payload, []


def preview_import(
    db: Session,
    *,
    portfolio_id: int,
    content: bytes,
    mapping_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text, encoding = _decode(content)
    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        headers = [str(value).strip() for value in next(reader)]
    except StopIteration:
        return {
            "source_ref": hashlib.sha256(content).hexdigest()[:32],
            "encoding": encoding,
            "delimiter": delimiter,
            "headers": [],
            "mapping": {},
            "mapping_issues": ["empty_file"],
            "rows": [],
            "summary": {"total": 0, "ready": 0, "duplicates": 0, "invalid": 0},
        }
    mapping = _detect_mapping(headers)
    mapping, mapping_issues = _apply_mapping_overrides(mapping, headers, mapping_overrides)
    existing_keys, order_index = _existing_keys(db, portfolio_id=portfolio_id)
    rows: list[dict[str, Any]] = []
    seen_in_file: dict[str, int] = {}
    summary = {"total": 0, "ready": 0, "duplicates": 0, "invalid": 0}
    for row_number, values in enumerate(reader, start=2):
        if not any(str(value).strip() for value in values):
            continue
        raw = {headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))}
        payload, issues = _normalize_row(raw, mapping)
        duplicate_of: int | None = None
        duplicate_kind: str | None = None
        if payload is not None:
            key = natural_key(payload)
            order_id = str(payload.get("broker_order_id") or "").strip()
            if order_id and order_id in order_index:
                duplicate_of = order_index[order_id]
                duplicate_kind = "LEDGER_ORDER_ID"
            elif key in existing_keys:
                duplicate_kind = "LEDGER"
            elif key in seen_in_file:
                duplicate_of = seen_in_file[key]
                duplicate_kind = "IN_FILE"
            else:
                seen_in_file[key] = row_number
        status = "INVALID" if payload is None else "DUPLICATE" if duplicate_kind else "READY"
        summary["total"] += 1
        summary["invalid" if status == "INVALID" else "duplicates" if status == "DUPLICATE" else "ready"] += 1
        rows.append({
            "row_number": row_number,
            "status": status,
            "raw": raw,
            "normalized": payload,
            "issues": issues,
            "duplicate_kind": duplicate_kind,
            "duplicate_of_entry_id": duplicate_of,
        })
        if len(rows) >= MAX_IMPORT_ROWS:
            break
    return {
        "source_ref": hashlib.sha256(content).hexdigest()[:32],
        "encoding": encoding,
        "delimiter": delimiter,
        "headers": headers,
        "mapping": mapping,
        "mapping_issues": mapping_issues,
        "rows": rows,
        "summary": summary,
    }


__all__ = [
    "FIELD_ALIASES",
    "import_idempotency_key",
    "natural_key",
    "preview_import",
]
