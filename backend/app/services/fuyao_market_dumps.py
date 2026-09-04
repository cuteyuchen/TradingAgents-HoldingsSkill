"""Fuyao market-dump bootstrap and corporate-action persistence.

The dump path is deliberately separate from request-time quote collection. A
download is staged beside its final file, validated, and only then atomically
replaced. Parquet parsing is optional at import time so contract tests can
inject a small row reader without requiring a network or a large dependency.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..history.models import SecurityLifecycleEvent
from ..market.codes import exchange_for_code, normalize_security_code
from ..market.engine.history import NormalizedDailyBar
from ..market.providers.fuyao import _rows
from ..market.providers.fuyao_client import FuyaoClient, FuyaoResponse, client_from_settings
from .daily_bar_cache import upsert_daily_bars


CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DOWNLOAD_TIMEOUT = (5.0, 120.0)


class DumpValidationError(ValueError):
    """Raised when a downloaded dump cannot be trusted as a complete file."""


class DumpSpec:
    def __init__(self, dump_id: str, kind: str, endpoint: str, required_columns: frozenset[str]) -> None:
        self.dump_id = dump_id
        self.kind = kind
        self.endpoint = endpoint
        self.required_columns = required_columns


DUMP_SPECS: dict[str, DumpSpec] = {
    "daily_k": DumpSpec(
        "a_share_daily_k_1d_none_10y",
        "daily_k",
        "/api/dump/market-dumps/daily-k/download-url",
        frozenset({"thscode", "date_ms", "open_price", "high_price", "low_price", "close_price", "volume", "turnover"}),
    ),
    "daily_k_10d": DumpSpec(
        "a_share_daily_k_1d_none_10d",
        "daily_k",
        "/api/dump/market-dumps/daily-k-10d/download-url",
        frozenset({"thscode", "date_ms", "open_price", "high_price", "low_price", "close_price", "volume", "turnover"}),
    ),
    "adjustment_factors": DumpSpec(
        "a_share_adjustment_factors_event_none_all",
        "adjustment_factors",
        "/api/dump/market-dumps/adjustment-factors/download-url",
        frozenset({"thscode", "ex_date_ms", "dividend_per_share", "per_share_bonus", "allotment_ratio", "allotment_price"}),
    ),
}


def _timestamp_ms(value: Any) -> datetime | None:
    try:
        if value in (None, ""):
            return None
        return datetime.fromtimestamp(int(value) / 1000, tz=CHINA_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _iter_parquet_rows(path: Path) -> Iterable[Mapping[str, Any]]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise DumpValidationError("parquet_reader_unavailable") from exc
    parquet_file = parquet.ParquetFile(path)
    for batch in parquet_file.iter_batches(batch_size=4096):
        yield from batch.to_pylist()


def _reader_for(path: Path, reader: Callable[[Path], Iterable[Mapping[str, Any]]] | None) -> Iterable[Mapping[str, Any]]:
    return reader(path) if reader is not None else _iter_parquet_rows(path)


def validate_dump_file(
    path: Path,
    spec: DumpSpec,
    *,
    reader: Callable[[Path], Iterable[Mapping[str, Any]]] | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate schema, uniqueness, row count, date range, and optional checksum."""

    if not path.exists() or path.stat().st_size <= 0:
        raise DumpValidationError("empty_dump")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    sha256 = digest.hexdigest()
    if expected_sha256 and sha256.lower() != str(expected_sha256).strip().lower():
        raise DumpValidationError("checksum_mismatch")

    row_count = 0
    duplicate_count = 0
    seen: set[tuple[str, int]] = set()
    minimum: int | None = None
    maximum: int | None = None
    columns: set[str] = set()
    for raw in _reader_for(path, reader):
        if not isinstance(raw, Mapping):
            raise DumpValidationError("malformed_row")
        if not columns:
            columns = {str(key) for key in raw}
            missing = spec.required_columns - columns
            if missing:
                raise DumpValidationError(f"schema_missing:{','.join(sorted(missing))}")
        code_key = str(raw.get("thscode") or "").strip().upper()
        time_key = raw.get("date_ms") if spec.kind == "daily_k" else raw.get("ex_date_ms")
        try:
            timestamp = int(time_key)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DumpValidationError("invalid_timestamp") from exc
        key = (code_key, timestamp)
        if key in seen:
            duplicate_count += 1
        seen.add(key)
        row_count += 1
        minimum = timestamp if minimum is None else min(minimum, timestamp)
        maximum = timestamp if maximum is None else max(maximum, timestamp)
    if row_count <= 0:
        raise DumpValidationError("empty_rows")
    if duplicate_count:
        raise DumpValidationError("duplicate_primary_key")
    return {
        "dump_id": spec.dump_id,
        "kind": spec.kind,
        "row_count": row_count,
        "min_timestamp_ms": minimum,
        "max_timestamp_ms": maximum,
        "min_date": _timestamp_ms(minimum).date().isoformat() if minimum is not None else None,
        "max_date": _timestamp_ms(maximum).date().isoformat() if maximum is not None else None,
        "sha256": sha256,
        "columns": sorted(columns),
    }


def _download_stream(url: str, path: Path, timeout: tuple[float, float]) -> None:
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)


def _download_url(response: FuyaoResponse) -> tuple[str, str | None]:
    data = response.data
    if isinstance(data, str):
        return data, None
    if not isinstance(data, Mapping):
        raise DumpValidationError("download_url_missing")
    url = next((data.get(key) for key in ("download_url", "presigned_url", "url") if data.get(key)), None)
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise DumpValidationError("download_url_missing")
    checksum = data.get("sha256") or data.get("checksum")
    return url, str(checksum) if checksum else None


def stage_market_dump(
    kind: str,
    destination: str | Path,
    *,
    client: FuyaoClient | None = None,
    downloader: Callable[[str, Path, tuple[float, float]], None] | None = None,
    reader: Callable[[Path], Iterable[Mapping[str, Any]]] | None = None,
    timeout: tuple[float, float] = DEFAULT_DOWNLOAD_TIMEOUT,
) -> dict[str, Any]:
    """Fetch a short-lived presigned URL, validate a temp file, then replace atomically."""

    spec = DUMP_SPECS.get(str(kind).strip().lower())
    if spec is None:
        raise ValueError(f"unknown_dump_kind:{kind}")
    client = client or client_from_settings()
    response = client.get(spec.endpoint, capability="market_dumps")
    url, expected_sha256 = _download_url(response)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", suffix=".part", dir=target.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        (downloader or _download_stream)(url, temporary_path, timeout)
        manifest = validate_dump_file(temporary_path, spec, reader=reader, expected_sha256=expected_sha256)
        os.replace(temporary_path, target)
        temporary_path = None
        manifest.update({"path": str(target), "request_id": response.request_id, "endpoint": response.endpoint})
        return manifest
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def import_daily_k_dump(
    db: Session,
    path: str | Path,
    *,
    reader: Callable[[Path], Iterable[Mapping[str, Any]]] | None = None,
    dump_id: str = "a_share_daily_k_1d_none_10y",
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """Import raw (unadjusted) dump rows into the existing daily-bar cache."""

    spec = DUMP_SPECS["daily_k"] if "10d" not in dump_id else DUMP_SPECS["daily_k_10d"]
    path = Path(path)
    fetched = fetched_at or datetime.now(UTC)
    bars: list[NormalizedDailyBar] = []
    imported = 0
    skipped = 0
    for raw in _reader_for(path, reader):
        code = normalize_security_code(raw.get("thscode") or raw.get("ticker"))
        timestamp = _timestamp_ms(raw.get("date_ms"))
        if not code or timestamp is None:
            skipped += 1
            continue
        bars.append(NormalizedDailyBar(
            code=code,
            trade_date=timestamp.astimezone(CHINA_TZ).date(),
            exchange=exchange_for_code(code),
            open=_number(raw.get("open_price")),
            high=_number(raw.get("high_price")),
            low=_number(raw.get("low_price")),
            close=_number(raw.get("close_price")),
            volume=_number(raw.get("volume")),
            amount=_number(raw.get("turnover")),
            adjustment="NONE",
            provider="fuyao_dump",
            fetched_at=fetched,
            available_at=None,
            quality_status="VALID" if raw.get("close_price") not in (None, "", "-") else "MISSING",
            metadata={
                "dump_id": dump_id,
                "thscode": raw.get("thscode"),
                "source_timestamp_ms": raw.get("date_ms"),
                "currency": raw.get("currency") or "CNY",
                "interval": raw.get("interval") or "1d",
                "adjusted": raw.get("adjusted") or "none",
            },
        ))
        if len(bars) >= 1000:
            imported += upsert_daily_bars(db, bars, source="fuyao_dump")
            bars.clear()
    if bars:
        imported += upsert_daily_bars(db, bars, source="fuyao_dump")
    return {"dump_id": dump_id, "imported_rows": imported, "skipped_rows": skipped, "adjustment": "NONE", "provider": "fuyao_dump", "spec_kind": spec.kind}


def persist_fuyao_corporate_actions(
    db: Session,
    response: FuyaoResponse | Mapping[str, Any],
    *,
    captured_at: datetime | None = None,
) -> dict[str, int]:
    """Persist the documented raw event stream without deriving a second adjustment model."""

    data = getattr(response, "data", response)
    rows = _rows(data)
    source = "fuyao"
    endpoint = getattr(response, "endpoint", "/api/a-share/corporate-actions/adjustment-factors")
    request_id = getattr(response, "request_id", None)
    captured = captured_at or datetime.now(UTC)
    inserted = 0
    updated = 0
    skipped = 0
    for raw in rows:
        code = normalize_security_code(raw.get("thscode") or raw.get("ticker"))
        event_timestamp = _timestamp_ms(raw.get("ex_date_ms"))
        if not code or event_timestamp is None:
            skipped += 1
            continue
        effective_date = event_timestamp.astimezone(CHINA_TZ).date()
        source_ref = f"fuyao:{code}:{int(raw.get('ex_date_ms'))}"
        row = db.execute(select(SecurityLifecycleEvent).where(
            SecurityLifecycleEvent.market == "CN",
            SecurityLifecycleEvent.code == code,
            SecurityLifecycleEvent.effective_date == effective_date,
            SecurityLifecycleEvent.event_type == "CORPORATE_ACTION",
            SecurityLifecycleEvent.source == source,
            SecurityLifecycleEvent.source_ref == source_ref,
        )).scalar_one_or_none()
        values = {
            "market": "CN",
            "exchange": exchange_for_code(code),
            "code": code,
            "event_type": "CORPORATE_ACTION",
            "effective_date": effective_date,
            "effective_at": event_timestamp.astimezone(UTC),
            "source_available_at": None,
            "captured_at": captured,
            "ingested_at": captured,
            "source": source,
            "source_ref": source_ref,
            "source_lineage_json": {
                "provider": source,
                "endpoint": endpoint,
                "request_id": request_id,
                "pit_status": "HISTORICAL_PIT_UNKNOWN",
            },
            "quality_status": "VALID",
            "payload_json": dict(raw),
        }
        if row is None:
            db.add(SecurityLifecycleEvent(**values))
            inserted += 1
        else:
            for key, value in values.items():
                setattr(row, key, value)
            updated += 1
    db.flush()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


__all__ = [
    "DUMP_SPECS",
    "DumpSpec",
    "DumpValidationError",
    "import_daily_k_dump",
    "persist_fuyao_corporate_actions",
    "stage_market_dump",
    "validate_dump_file",
]
