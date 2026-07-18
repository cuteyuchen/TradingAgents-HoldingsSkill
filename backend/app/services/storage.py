"""Local artifact storage with path traversal and image-size protections."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from ..config import settings

ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def root_dir() -> Path:
    root = Path(settings.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_storage_path(relative_path: str) -> Path:
    root = root_dir()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid storage path") from exc
    return path


def _signature_matches(content: bytes, mime_type: str) -> bool:
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if mime_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    return False


async def save_holding_image(user_id: int, upload: UploadFile) -> tuple[str, str, str, bytes]:
    mime_type = (upload.content_type or "").lower()
    if mime_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Only PNG, JPEG, WEBP, and GIF images are supported.")
    content = await upload.read(settings.MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty.")
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded image is too large.")
    if not _signature_matches(content, mime_type):
        raise HTTPException(status_code=400, detail="Image content does not match its MIME type.")

    suffix = ALLOWED_IMAGE_TYPES[mime_type]
    relative = Path("v2") / str(user_id) / "uploads" / f"{uuid.uuid4().hex}{suffix}"
    target = resolve_storage_path(relative.as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, target)
    return relative.as_posix(), mime_type, hashlib.sha256(content).hexdigest(), content
