"""File-backed analysis archives for advice Markdown, holdings JSON, and screenshot."""
import base64
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_token
from ..config import settings
from ..database import get_db
from ..schemas import ArchiveCreated, ArchiveSummary

router = APIRouter(prefix="/api/v1/archives", tags=["archives"])


# /*********************** 文件归档路径 *********************/
def _artifacts_root() -> Path:
    root = Path(settings.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _archive_dir(archive_id: int) -> Path:
    root = _artifacts_root()
    target = (root / str(archive_id)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="invalid archive path") from exc
    return target


def _safe_screenshot_name(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        content_suffix = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(upload.content_type or "", ".bin")
        suffix = content_suffix
    return f"screenshot{suffix}"


# /*********************** 持仓可用数量语义 *********************/
def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_holding_availability(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    normalized = dict(item)
    qty = _as_float(normalized.get("qty"))
    available_qty = _as_float(normalized.get("available_qty"))
    if qty is None or available_qty is None:
        return normalized
    unavailable_qty = max(qty - available_qty, 0)
    normalized["unavailable_qty"] = unavailable_qty
    if unavailable_qty > 0:
        normalized["availability_note"] = (
            f"不可用数量 {unavailable_qty:g}，原因可能是挂单、冻结或 T+1 限制；不推断为已卖出。"
        )
    return normalized


def _normalize_holdings(data: Any) -> Any:
    if isinstance(data, list):
        return [_normalize_holding_availability(item) for item in data]
    if isinstance(data, dict) and isinstance(data.get("holdings"), list):
        normalized = dict(data)
        normalized["holdings"] = [_normalize_holding_availability(item) for item in data["holdings"]]
        return normalized
    return data


def _holdings_count(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict) and isinstance(data.get("holdings"), list):
        return len(data["holdings"])
    return 0


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else text


def _holdings_list(data: Any) -> list[dict[str, Any]]:
    rows = data.get("holdings") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)]


def _holding_identity(item: dict[str, Any]) -> tuple[str, str | None]:
    code = _normalize_code(item.get("code") or item.get("symbol"))
    name = item.get("name")
    return code, str(name) if name is not None else None


def _extract_advice_excerpt(advice_md: str, max_lines: int = 18) -> str:
    keywords = ("买入", "加仓", "持有", "减仓", "卖出", "触发", "止损", "候选", "轮动", "观察")
    selected: list[str] = []
    for raw_line in advice_md.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(keyword in line for keyword in keywords):
            selected.append(line)
        if len(selected) >= max_lines:
            break
    if not selected:
        selected = [line.strip() for line in advice_md.splitlines() if line.strip()][:6]
    return "\n".join(selected)


def _archive_codes(archive: models.Archive) -> set[str]:
    return {
        code
        for code, _name in (_holding_identity(item) for item in _holdings_list(archive.holdings_json))
        if code
    }


def _archive_context_payload(
    archive: models.Archive,
    advice_md: str,
    include_advice: bool,
) -> dict[str, Any]:
    holdings = []
    for item in _holdings_list(archive.holdings_json):
        code, name = _holding_identity(item)
        holdings.append(
            {
                "code": code,
                "name": name,
                "qty": item.get("qty"),
                "available_qty": item.get("available_qty"),
                "unavailable_qty": item.get("unavailable_qty"),
                "cost": item.get("cost"),
                "price": item.get("price"),
                "market_value": item.get("market_value"),
                "pnl": item.get("pnl"),
                "pnl_amount": item.get("pnl_amount"),
                "data_quality": item.get("data_quality"),
                "availability_note": item.get("availability_note"),
            }
        )
    payload = {
        "id": archive.id,
        "timestamp": archive.timestamp.isoformat(),
        "checkpoint": archive.checkpoint,
        "holdings_source": archive.holdings_source,
        "data_quality_grade": archive.data_quality_grade,
        "title": archive.title,
        "holdings_count": len(holdings),
        "has_screenshot": bool(archive.screenshot_filename),
        "holdings": holdings,
        "advice_excerpt": _extract_advice_excerpt(advice_md),
    }
    if include_advice:
        payload["advice_md"] = advice_md
    return payload


# /*********************** 请求解析 *********************/
def _parse_json_text(raw: str | bytes, field_name: str) -> Any:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field_name} json") from exc


def _parse_timestamp(meta: dict[str, Any]) -> datetime:
    raw = meta.get("timestamp")
    if not raw:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid meta.timestamp") from exc


# /*********************** 文件读写 *********************/
async def _write_upload(path: Path, upload: UploadFile) -> None:
    content = await upload.read()
    path.write_bytes(content)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"archive file missing: {path.name}") from exc


def _screenshot_payload(archive: models.Archive) -> dict | None:
    if not archive.screenshot_filename:
        return None
    path = _archive_dir(archive.id) / archive.screenshot_filename
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=500, detail="screenshot file missing") from exc
    mime = archive.screenshot_mime_type or "application/octet-stream"
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "filename": archive.screenshot_original_name or archive.screenshot_filename,
        "mime_type": mime,
        "data_url": f"data:{mime};base64,{encoded}",
    }


# /*********************** 接口 *********************/
@router.post("", response_model=ArchiveCreated, status_code=status.HTTP_201_CREATED)
async def create_archive(
    screenshot: UploadFile = File(...),
    holdings_json: UploadFile = File(...),
    advice_md: UploadFile = File(...),
    meta: str | None = Form(None),
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
) -> ArchiveCreated:
    meta_obj = _parse_json_text(meta, "meta") if meta else {}
    if not isinstance(meta_obj, dict):
        raise HTTPException(status_code=400, detail="meta must be a JSON object")

    holdings_obj = _normalize_holdings(_parse_json_text(await holdings_json.read(), "holdings_json"))
    screenshot_filename = _safe_screenshot_name(screenshot)
    archive = models.Archive(
        timestamp=_parse_timestamp(meta_obj),
        checkpoint=meta_obj.get("checkpoint"),
        holdings_source=meta_obj.get("holdings_source"),
        data_quality_grade=meta_obj.get("data_quality_grade"),
        title=meta_obj.get("title"),
        meta=meta_obj,
        holdings_json=holdings_obj,
        advice_filename="advice.md",
        holdings_filename="holdings.json",
        screenshot_filename=screenshot_filename,
        screenshot_mime_type=screenshot.content_type,
        screenshot_original_name=screenshot.filename,
    )
    db.add(archive)
    db.flush()

    target = _archive_dir(archive.id)
    try:
        target.mkdir(parents=True, exist_ok=True)
        await _write_upload(target / screenshot_filename, screenshot)
        (target / "holdings.json").write_text(
            json.dumps(holdings_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await _write_upload(target / "advice.md", advice_md)
        db.commit()
    except Exception:
        db.rollback()
        shutil.rmtree(target, ignore_errors=True)
        raise

    db.refresh(archive)
    return ArchiveCreated(id=archive.id)


@router.get("", response_model=list[ArchiveSummary])
def list_archives(
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
) -> list[ArchiveSummary]:
    rows = db.query(models.Archive).order_by(models.Archive.timestamp.desc(), models.Archive.id.desc()).all()
    return [
        ArchiveSummary(
            id=row.id,
            timestamp=row.timestamp,
            checkpoint=row.checkpoint,
            holdings_source=row.holdings_source,
            data_quality_grade=row.data_quality_grade,
            title=row.title,
            holdings_count=_holdings_count(row.holdings_json),
            has_screenshot=bool(row.screenshot_filename),
        )
        for row in rows
    ]


@router.get("/context")
def get_archive_context(
    codes: str | None = Query(None, description="Comma-separated stock codes"),
    limit: int = Query(5, ge=1, le=20),
    include_advice: bool = Query(False),
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
) -> dict[str, Any]:
    requested_codes = {
        normalized
        for raw in (codes or "").split(",")
        if (normalized := _normalize_code(raw))
    }
    query = db.query(models.Archive).order_by(models.Archive.timestamp.desc(), models.Archive.id.desc())
    rows = query.all() if requested_codes else query.limit(limit).all()

    selected: list[models.Archive] = []
    for row in rows:
        if requested_codes and not (_archive_codes(row) & requested_codes):
            continue
        selected.append(row)
        if len(selected) >= limit:
            break

    archive_payloads: list[dict[str, Any]] = []
    timeline_by_code: dict[str, list[dict[str, Any]]] = {}
    latest_by_code: dict[str, dict[str, Any]] = {}
    same_day_advice: list[dict[str, Any]] = []
    today = datetime.now(UTC).date()

    for row in selected:
        advice_md = _read_text(_archive_dir(row.id) / row.advice_filename)
        advice_excerpt = _extract_advice_excerpt(advice_md)
        archive_payloads.append(_archive_context_payload(row, advice_md, include_advice))
        if row.timestamp.date() == today:
            same_day_advice.append(
                {
                    "archive_id": row.id,
                    "timestamp": row.timestamp.isoformat(),
                    "checkpoint": row.checkpoint,
                    "data_quality_grade": row.data_quality_grade,
                    "title": row.title,
                    "advice_excerpt": advice_excerpt,
                }
            )

        for item in _holdings_list(row.holdings_json):
            code, name = _holding_identity(item)
            if not code or (requested_codes and code not in requested_codes):
                continue
            timeline_item = {
                "archive_id": row.id,
                "timestamp": row.timestamp.isoformat(),
                "checkpoint": row.checkpoint,
                "data_quality_grade": row.data_quality_grade,
                "title": row.title,
                "code": code,
                "name": name,
                "qty": item.get("qty"),
                "available_qty": item.get("available_qty"),
                "unavailable_qty": item.get("unavailable_qty"),
                "cost": item.get("cost"),
                "price": item.get("price"),
                "market_value": item.get("market_value"),
                "pnl": item.get("pnl"),
                "pnl_amount": item.get("pnl_amount"),
                "advice_excerpt": advice_excerpt,
            }
            timeline_by_code.setdefault(code, []).append(timeline_item)
            latest_by_code.setdefault(code, timeline_item)

    return {
        "archives": archive_payloads,
        "timeline_by_code": timeline_by_code,
        "latest_by_code": latest_by_code,
        "same_day_advice": same_day_advice,
    }


@router.get("/{archive_id}")
def get_archive(
    archive_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
) -> dict:
    archive = db.get(models.Archive, archive_id)
    if not archive:
        raise HTTPException(status_code=404, detail="archive not found")
    advice_path = _archive_dir(archive.id) / archive.advice_filename
    return {
        "id": archive.id,
        "timestamp": archive.timestamp.isoformat(),
        "checkpoint": archive.checkpoint,
        "holdings_source": archive.holdings_source,
        "data_quality_grade": archive.data_quality_grade,
        "title": archive.title,
        "meta": archive.meta,
        "holdings": archive.holdings_json,
        "advice_md": _read_text(advice_path),
        "screenshot": _screenshot_payload(archive),
    }


@router.delete("/{archive_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_archive(
    archive_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
) -> None:
    archive = db.get(models.Archive, archive_id)
    if not archive:
        raise HTTPException(status_code=404, detail="archive not found")
    target = _archive_dir(archive.id)
    db.delete(archive)
    db.commit()
    shutil.rmtree(target, ignore_errors=True)
