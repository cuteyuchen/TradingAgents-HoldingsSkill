"""Holdings screenshot parsing and deterministic normalization."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from ..database import SessionLocal
from ..v2_models import HoldingUpload, ModelProfile
from ..v2_schemas import HoldingInput, ParsedHoldingsPayload
from .model_client import ModelCallError, call_model, parse_json_result
from .storage import resolve_storage_path

logger = logging.getLogger(__name__)

VISION_PROMPT = """
你是券商持仓截图解析器。只依据图片内容输出 JSON，不提供投资建议。
顶层结构必须为：
{
  "holdings": [{
    "code": "六位证券代码",
    "name": "名称",
    "market": "SH/SZ/BJ/HK/UNKNOWN",
    "qty": 总持仓数量,
    "available_qty": 当前可卖数量,
    "cost": 成本价,
    "price": 截图现价,
    "market_value": 市值,
    "pnl": 盈亏比例小数，例如 -27.73% 写成 -0.2773,
    "pnl_amount": 盈亏金额,
    "weight": 仓位比例小数
  }],
  "total_assets": 账户总资产,
  "total_market_value": 总市值,
  "broker_available_cash": 截图显示的可用资金,
  "repo_or_standard_bond_value": 国债逆回购或标准券金额,
  "excluded_items": [],
  "notes": []
}
规则：
1. 盈亏双行字段通常第一行是金额、第二行是百分比，不能混淆。
2. 新标准券、标准券、国债逆回购不放入 holdings，放入 excluded_items 或 repo_or_standard_bond_value。
3. 看不清的数字填 null，不得猜测。
4. available_qty 只是当前可卖数量，不能把 qty-available_qty 当作已经卖出。
5. 只返回 JSON 对象。
""".strip()


def _number(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        if isinstance(value, str):
            text = value.replace(",", "").replace("%", "").strip()
            parsed = float(text)
            return parsed / 100 if "%" in value else parsed
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_payload(payload: ParsedHoldingsPayload) -> tuple[ParsedHoldingsPayload, list[str]]:
    normalized: list[HoldingInput] = []
    errors: list[str] = []
    seen: set[str] = set()
    excluded = list(payload.excluded_items)

    for index, holding in enumerate(payload.holdings):
        name = (holding.name or "").strip()
        if name in {"新标准券", "标准券"} or "逆回购" in name:
            excluded.append({"name": name, "reason": "standard_bond_or_repo", "market_value": holding.market_value})
            continue
        code = holding.code.strip().upper()
        digits = "".join(ch for ch in code if ch.isdigit())
        if len(digits) >= 6:
            code = digits[-6:]
        if not code:
            errors.append(f"第 {index + 1} 行缺少证券代码")
            continue
        if code in seen:
            errors.append(f"证券代码 {code} 重复")
            continue
        seen.add(code)
        qty = _number(holding.qty)
        available = _number(holding.available_qty)
        if qty is not None and qty < 0:
            errors.append(f"{code} 总持仓不能为负数")
        if available is not None and available < 0:
            errors.append(f"{code} 可用数量不能为负数")
        if qty is not None and available is not None and available > qty:
            errors.append(f"{code} 可用数量大于总持仓")
        unavailable = max((qty or 0) - (available or 0), 0) if qty is not None and available is not None else None
        extra = dict(holding.extra)
        if unavailable is not None:
            extra["unavailable_qty"] = unavailable
            if unavailable > 0:
                extra["availability_note"] = "不可用数量可能来自挂单、冻结或 T+1，不按已卖出处理。"
        normalized.append(
            HoldingInput(
                code=code,
                name=name or None,
                market=holding.market,
                qty=qty,
                available_qty=available,
                cost=_number(holding.cost),
                price=_number(holding.price),
                market_value=_number(holding.market_value),
                pnl=_number(holding.pnl),
                pnl_amount=_number(holding.pnl_amount),
                weight=_number(holding.weight),
                extra=extra,
            )
        )

    corrected = payload.corrected_unused_funds
    if corrected is None and payload.total_assets is not None and payload.total_market_value is not None:
        corrected = payload.total_assets - payload.total_market_value

    result = ParsedHoldingsPayload(
        holdings=normalized,
        total_assets=_number(payload.total_assets),
        total_market_value=_number(payload.total_market_value),
        broker_available_cash=_number(payload.broker_available_cash),
        corrected_unused_funds=_number(corrected),
        repo_or_standard_bond_value=_number(payload.repo_or_standard_bond_value),
        excluded_items=excluded,
        notes=payload.notes,
    )
    if not result.holdings:
        errors.append("没有识别到有效的股票或 ETF 持仓")
    return result, errors


def parse_payload_dict(raw: dict[str, Any]) -> tuple[ParsedHoldingsPayload, list[str]]:
    try:
        payload = ParsedHoldingsPayload.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"持仓 JSON 格式错误: {exc}") from exc
    return normalize_payload(payload)


def parse_upload(upload_id: int) -> None:
    db = SessionLocal()
    try:
        upload = db.query(HoldingUpload).filter(HoldingUpload.id == upload_id).first()
        if upload is None:
            return
        upload.parsing_status = "vision_parsing"
        upload.error_message = None
        db.commit()

        profile = (
            db.query(ModelProfile)
            .filter(
                ModelProfile.user_id == upload.user_id,
                ModelProfile.purpose == "vision",
                ModelProfile.is_default.is_(True),
            )
            .first()
        )
        if profile is None:
            upload.parsing_status = "needs_model"
            upload.error_message = "请先在设置中配置默认识图模型，或手工录入持仓。"
            db.commit()
            return

        upload.vision_model_profile_id = profile.id
        image = resolve_storage_path(upload.storage_path).read_bytes()
        result = call_model(
            profile,
            [
                {"role": "system", "content": "严格提取图片中的持仓数据，不得编造。"},
                {"role": "user", "content": VISION_PROMPT},
            ],
            image_bytes=image,
            image_mime=upload.mime_type,
            json_mode=True,
        )
        parsed, errors = parse_payload_dict(parse_json_result(result))
        upload.parsed_json = parsed.model_dump(mode="json")
        upload.validation_errors = errors
        upload.parsing_status = "waiting_confirmation"
        db.commit()
    except (ModelCallError, ValueError, OSError, json.JSONDecodeError) as exc:
        logger.exception("Failed to parse holding upload %s", upload_id)
        upload = db.query(HoldingUpload).filter(HoldingUpload.id == upload_id).first()
        if upload is not None:
            upload.parsing_status = "failed"
            upload.error_message = str(exc)[:2000]
            db.commit()
    finally:
        db.close()
