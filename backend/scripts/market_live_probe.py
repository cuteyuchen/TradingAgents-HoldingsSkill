"""Auditable live verification for the V3 MARKET-2 instrument facade.

The probe deliberately talks to :class:`InstrumentMarketService` only. It does
not call providers directly, persist portfolio/analysis data, or include wire
payloads in its output. Run it manually from ``backend/``.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Callable

from app.database import SessionLocal, init_db
from app.market.instruments import InstrumentLookupError, InstrumentMarketService
from app.market.models import CHINA_TZ


DEFAULT_CODES = ("600519.SH", "159915.SZ", "000300.SH", "000001.SH", "000001.SZ")
DEFAULT_INCLUDE = ("quote", "bars", "order-book", "capital-flow")
INCLUDE_ALIASES = {
    "quote": "quote",
    "quotes": "quote",
    "bars": "bars",
    "order-book": "order_book",
    "order_book": "order_book",
    "capital-flow": "capital_flow",
    "capital_flow": "capital_flow",
}
_SECRET = re.compile(r"(api[_-]?key|authorization|cookie|secret|credential|token|://[^/\s]*@)", re.I)


def _sanitize(value: Any) -> Any:
    """Second-pass redaction for already normalized public contract data."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if _SECRET.search(key_text) or key_text.lower() in {"raw", "headers", "endpoint", "url"}:
                continue
            cleaned = _sanitize(item)
            if isinstance(cleaned, str) and _SECRET.search(cleaned):
                continue
            result[key_text] = cleaned
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return _sanitize(value.model_dump(mode="json"))
    return _sanitize(value)


def _status_bucket(value: Any) -> str:
    status = str(getattr(value, "status", "") or "").lower()
    if status == "unsupported":
        # An explicit capability boundary is not a provider failure.
        return "degraded"
    if status in {"unavailable", "empty"}:
        return "failed"
    quality = str(getattr(value, "quality", "F") or "F").upper()
    return "passed" if quality in {"A", "B"} and status in {"available", "degraded", "stale"} else "degraded"


def _parse_now(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=CHINA_TZ)
    return value.astimezone(UTC)


def _parse_include(raw: str | None) -> tuple[str, ...]:
    values = raw.split(",") if raw else list(DEFAULT_INCLUDE)
    result = tuple(dict.fromkeys(INCLUDE_ALIASES[item.strip().lower()] for item in values if item.strip().lower() in INCLUDE_ALIASES))
    if not result:
        raise ValueError("--include must contain quote, bars, order-book, or capital-flow")
    return result


def run_probe(
    *,
    codes: list[str] | tuple[str, ...] = DEFAULT_CODES,
    include: tuple[str, ...] = DEFAULT_INCLUDE,
    output: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    init_db()
    with SessionLocal() as db:
        service = InstrumentMarketService(db, now=(lambda: now) if now else None)
        session = service.session_service.resolve_session(now)
        report: dict[str, Any] = {
            "probe_version": "market-live-probe-v1",
            "started_at": started.isoformat(),
            "completed_at": None,
            "market_session": _sanitize(session.to_dict()),
            "provider_profile": service.adapters.profile,
            "instruments": [],
            "summary": {"passed": 0, "degraded": 0, "failed": 0},
        }
        for raw_code in codes:
            item: dict[str, Any] = {"requested_code": raw_code, "results": {}}
            try:
                metadata = service.metadata(raw_code)
                item["instrument"] = _json_model(metadata.identity)
            except InstrumentLookupError as exc:
                item.update({"status": "failed", "error_code": "MASTER_DATA_MISSING" if exc.code == "INSTRUMENT_NOT_FOUND" else exc.code})
                report["summary"]["failed"] += 1
                report["instruments"].append(_sanitize(item))
                continue
            except Exception:
                item.update({"status": "failed", "error_code": "MASTER_DATA_MISSING"})
                report["summary"]["failed"] += 1
                report["instruments"].append(_sanitize(item))
                continue

            buckets: list[str] = []
            operations: dict[str, Callable[[], Any]] = {
                "quote": lambda: service.quote(raw_code),
                "bars": lambda: service.bars(raw_code, interval="1d", adjustment="none", limit=30),
                "order_book": lambda: service.order_book(raw_code),
                "capital_flow": lambda: service.capital_flow(raw_code),
            }
            for name in include:
                try:
                    result = operations[name]()
                    item["results"][name] = _json_model(result)
                    bucket = _status_bucket(result)
                except InstrumentLookupError as exc:
                    item["results"][name] = {"status": "failed", "error_code": exc.code}
                    bucket = "failed"
                except Exception:
                    # Never emit provider exception text or traceback details.
                    item["results"][name] = {"status": "failed", "error_code": "PROBE_OPERATION_FAILED"}
                    bucket = "failed"
                buckets.append(bucket)
            item["status"] = "failed" if "failed" in buckets else "degraded" if "degraded" in buckets else "passed"
            report["summary"][item["status"]] += 1
            report["instruments"].append(_sanitize(item))
        report["completed_at"] = datetime.now(UTC).isoformat()

    payload = _sanitize(report)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify live providers through InstrumentMarketService")
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES), help="comma-separated canonical or qualified codes")
    parser.add_argument("--include", default=",".join(DEFAULT_INCLUDE), help="comma-separated quote,bars,order-book,capital-flow")
    parser.add_argument("--output", type=Path, help="write sanitized JSON report")
    parser.add_argument("--now", help="deterministic session simulation time; never label it as live")
    args = parser.parse_args()
    report = run_probe(
        codes=[item.strip() for item in args.codes.split(",") if item.strip()],
        include=_parse_include(args.include),
        output=args.output,
        now=_parse_now(args.now),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
