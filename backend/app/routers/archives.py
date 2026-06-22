"""File-backed analysis archives for advice Markdown, holdings JSON, and screenshot."""
import base64
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
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
