"""Deterministic holding-to-security identity resolution.

The portfolio snapshot keeps the existing table shape for compatibility.  The
identity contract is carried by ``HoldingInput`` and ``HoldingItem.extra_json``
and is always verified against the shared ``SecurityMaster`` before a snapshot
or analysis can become authoritative.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market.codes import (
    canonical_security_code,
    exchange_for_code,
    exchange_hint,
    normalize_exchange,
    normalize_security_code,
)
from ..market.providers.fuyao import FuyaoSecurityProvider
from ..market.providers.fuyao_client import FuyaoAPIError
from ..market_models import SecurityMaster
from ..v2_models import HoldingItem, PortfolioSnapshot
from ..v2_schemas import HoldingInput, ParsedHoldingsPayload
from .security_master import ETF, STOCK, upsert_security

RESOLVED = "RESOLVED"
AMBIGUOUS = "AMBIGUOUS"
UNRESOLVED = "UNRESOLVED"
INVALID = "INVALID"
IDENTITY_STATUSES = {RESOLVED, AMBIGUOUS, UNRESOLVED, INVALID}
SUPPORTED_MARKETS = {"CN", "A_SHARE", "A-SHARE", "CN_A_SHARE"}

STATUS_LABELS = {
    RESOLVED: "已匹配",
    AMBIGUOUS: "需要选择",
    UNRESOLVED: "未找到",
    INVALID: "代码无效",
}


class UnresolvedSecurityIdentityError(RuntimeError):
    """Raised when analysis is asked to use an incomplete snapshot."""

    code = "unresolved_security_identity"

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        super().__init__(f"{self.code}: {len(issues)} holding identity issue(s)")


def normalize_security_name(value: Any) -> str:
    """Normalize display names deterministically, without fuzzy matching."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u3000", " ")
    text = "".join(text.split())
    for suffix in ("（场内）", "(场内)", "场内"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip().casefold()


def _asset_type(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    if normalized in {"SHARE", "EQUITY", "A_SHARE", "A_SHARE_STOCK", "A_SHARE_EQUITY", "STOCK"}:
        return STOCK
    if normalized in {"ETF", "FUND_ETF", "EXCHANGE_TRADED_FUND", "EXCHANGE_TRADED_ETF"}:
        return ETF
    return normalized


def _asset_hint(holding: HoldingInput) -> str | None:
    explicit = _asset_type(holding.asset_type)
    if explicit:
        return explicit
    name = normalize_security_name(holding.name)
    return ETF if name.endswith("etf") else None


def _identity_tokens(holding: HoldingInput) -> list[tuple[str, str, str, str | None]]:
    """Return every submitted code token with deterministic normalization.

    ``extra`` is user-controlled transport metadata, so it is included only to
    detect stale/conflicting edits; it is never allowed to silently override a
    visible ``code`` or ``canonical_code`` field.
    """

    extra = holding.extra if isinstance(holding.extra, Mapping) else {}
    raw_values = (
        ("code", holding.code),
        ("canonical_code", holding.canonical_code),
        ("submitted_code", extra.get("submitted_code")),
        ("submitted_canonical_code", extra.get("submitted_canonical_code")),
    )
    tokens: list[tuple[str, str, str, str | None]] = []
    for label, value in raw_values:
        text = str(value or "").strip()
        if not text:
            continue
        tokens.append((label, text, normalize_security_code(text), exchange_hint(text)))
    return tokens


def _identity_input_error(holding: HoldingInput) -> tuple[str, str] | None:
    """Reject malformed or internally inconsistent identity fields."""

    tokens = _identity_tokens(holding)
    if not tokens:
        market = str(holding.market or "").strip().upper()
        if market and market not in SUPPORTED_MARKETS:
            return "unsupported_market", "仅支持 A 股持仓"
        return None

    invalid = [label for label, _text, normalized, _hint in tokens if not normalized]
    if invalid:
        return "invalid_code_format", "代码格式无效"

    normalized_codes = {normalized for _label, _text, normalized, _hint in tokens}
    if len(normalized_codes) > 1:
        return "conflicting_code_tokens", "提交的证券代码不一致"

    hints = {hint for _label, _text, _normalized, hint in tokens if hint}
    explicit_exchange = normalize_exchange(holding.exchange)
    if explicit_exchange and explicit_exchange not in {"SSE", "SZSE", "BSE"}:
        return "unsupported_exchange", "交易所无效"
    if explicit_exchange and hints and explicit_exchange not in hints:
        return "exchange_mismatch", "代码与交易所不一致"

    market = str(holding.market or "").strip().upper()
    if market and market not in SUPPORTED_MARKETS:
        return "unsupported_market", "仅支持 A 股持仓"
    return None


def _identity_raw_code(holding: HoldingInput) -> str:
    """Choose a validated raw token while retaining an explicit exchange hint."""

    tokens = _identity_tokens(holding)
    for _label, text, _normalized, hint in tokens:
        if hint:
            return text
    for label in ("canonical_code", "code", "submitted_canonical_code", "submitted_code"):
        for token_label, text, _normalized, _hint in tokens:
            if token_label == label:
                return text
    return ""


def _candidate_dict(row: SecurityMaster | Mapping[str, Any]) -> dict[str, Any] | None:
    if isinstance(row, SecurityMaster):
        if str(row.market or "CN").strip().upper() != "CN":
            return None
        if str(row.status or "ACTIVE").strip().upper() == "DELISTED":
            return None
        code = normalize_security_code(row.code)
        exchange = normalize_exchange(row.exchange)
        security_type = _asset_type(row.security_type)
        security_id = row.id
        name = row.name
    else:
        market = str(row.get("market") or "CN").strip().upper()
        if market != "CN":
            return None
        if str(row.get("status") or "ACTIVE").strip().upper() == "DELISTED":
            return None
        code = normalize_security_code(row.get("code") or row.get("symbol") or row.get("thscode"))
        exchange = normalize_exchange(row.get("exchange")) or exchange_for_code(code)
        security_type = _asset_type(row.get("security_type") or row.get("asset_type"))
        security_id = row.get("id") or row.get("security_id")
        name = row.get("name") or row.get("display_name")
    if not code or exchange not in {"SSE", "SZSE", "BSE"} or security_type not in {STOCK, ETF}:
        return None
    canonical = canonical_security_code(code, exchange)
    if not canonical:
        return None
    return {
        "security_id": int(security_id) if security_id is not None and str(security_id).isdigit() else None,
        "canonical_code": canonical,
        "code": code,
        "display_name": str(name).strip() if name else None,
        "name": str(name).strip() if name else None,
        "asset_type": security_type,
        "exchange": exchange,
    }


def _aliases(row: SecurityMaster) -> set[str]:
    values = {normalize_security_name(row.name)}
    metadata = row.raw_metadata_json if isinstance(row.raw_metadata_json, Mapping) else {}
    aliases = metadata.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if isinstance(aliases, list):
        values.update(normalize_security_name(item) for item in aliases)
    return {item for item in values if item}


def _local_code_rows(db: Session, code: str, exchange: str | None) -> list[SecurityMaster]:
    statement = select(SecurityMaster).where(
        SecurityMaster.market == "CN",
        SecurityMaster.code == code,
        SecurityMaster.status != "DELISTED",
    )
    if exchange:
        statement = statement.where(SecurityMaster.exchange == exchange)
    return list(db.execute(statement.order_by(SecurityMaster.id.asc())).scalars())


def _local_name_rows(db: Session, name: str, exchange: str | None, asset_type: str | None) -> list[SecurityMaster]:
    statement = select(SecurityMaster).where(
        SecurityMaster.market == "CN",
        SecurityMaster.status != "DELISTED",
    )
    if exchange:
        statement = statement.where(SecurityMaster.exchange == exchange)
    rows = list(db.execute(statement.order_by(SecurityMaster.code.asc(), SecurityMaster.id.asc())).scalars())
    normalized_name = normalize_security_name(name)
    return [
        row
        for row in rows
        if _asset_type(row.security_type) in ({STOCK, ETF} if asset_type is None else {asset_type})
        and normalized_name in _aliases(row)
    ]


def _remote_name_matches(rows: list[Mapping[str, Any]], name: str, asset_type: str | None, exchange: str | None) -> list[Mapping[str, Any]]:
    normalized_name = normalize_security_name(name)
    matches: list[Mapping[str, Any]] = []
    for row in rows:
        candidate = _candidate_dict(row)
        if candidate is None or candidate["display_name"] is None:
            continue
        if exchange and candidate["exchange"] != exchange:
            continue
        if asset_type and candidate["asset_type"] != asset_type:
            continue
        aliases = {normalize_security_name(candidate["display_name"])}
        metadata = row.get("raw_metadata_json") if isinstance(row, Mapping) else None
        if isinstance(metadata, Mapping):
            raw_aliases = metadata.get("aliases") or []
            if isinstance(raw_aliases, str):
                raw_aliases = [raw_aliases]
            if isinstance(raw_aliases, list):
                aliases.update(normalize_security_name(item) for item in raw_aliases)
        if normalized_name in aliases:
            matches.append(row)
    return matches


def _remote_code_matches(rows: list[Mapping[str, Any]], code: str, asset_type: str | None, exchange: str | None) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for row in rows:
        candidate = _candidate_dict(row)
        if candidate is None or candidate["code"] != code:
            continue
        if exchange and candidate["exchange"] != exchange:
            continue
        if asset_type and candidate["asset_type"] != asset_type:
            continue
        matches.append(row)
    return matches


def _unique_candidate_rows(rows: list[SecurityMaster | Mapping[str, Any]]) -> list[SecurityMaster | Mapping[str, Any]]:
    """Collapse duplicate provider rows by canonical identity."""

    unique: list[SecurityMaster | Mapping[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        candidate = _candidate_dict(row)
        if candidate is None:
            continue
        key = (candidate["canonical_code"], candidate["asset_type"], candidate["exchange"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _cache_remote_security(db: Session, raw: Mapping[str, Any]) -> SecurityMaster:
    candidate = _candidate_dict(raw)
    if candidate is None:
        raise ValueError("invalid remote security candidate")
    return upsert_security(
        db,
        {
            "market": "CN",
            "code": candidate["code"],
            "exchange": candidate["exchange"],
            "name": candidate["display_name"],
            "security_type": candidate["asset_type"],
            "source": "fuyao_security",
            "raw_metadata_json": dict(raw),
        },
    )


def _identity_extra(
    holding: HoldingInput,
    *,
    status: str,
    source: str,
    confidence: float | None,
    candidates: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    extra = dict(holding.extra or {})
    extra["resolution_status"] = status
    extra["resolution_source"] = source
    extra["resolution_confidence"] = confidence
    if status != RESOLVED:
        for key in ("canonical_code", "security_id", "code", "display_name", "name", "asset_type", "exchange"):
            extra.pop(key, None)
    if candidates is not None:
        extra["identity_candidates"] = candidates
    else:
        extra.pop("identity_candidates", None)
    if error:
        extra["identity_error"] = error
    else:
        extra.pop("identity_error", None)
    return extra


def _resolved_holding(holding: HoldingInput, row: SecurityMaster, *, source: str, confidence: float) -> HoldingInput:
    candidate = _candidate_dict(row)
    if candidate is None:
        raise ValueError("invalid security master candidate")
    extra = _identity_extra(holding, status=RESOLVED, source=source, confidence=confidence)
    if holding.name and row.name and normalize_security_name(holding.name) != normalize_security_name(row.name):
        extra["ocr_name"] = holding.name
    extra.update(candidate)
    values = holding.model_dump()
    values.update(
        code=row.code,
        canonical_code=candidate["canonical_code"],
        name=row.name or holding.name,
        display_name=row.name or holding.name,
        asset_type=candidate["asset_type"],
        exchange=candidate["exchange"],
        security_id=candidate["security_id"],
        resolution_status=RESOLVED,
        resolution_source=source,
        resolution_confidence=confidence,
        extra=extra,
    )
    return HoldingInput.model_validate(values)


def _unresolved_holding(
    holding: HoldingInput,
    *,
    status: str,
    source: str,
    candidates: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> HoldingInput:
    values = holding.model_dump()
    values.update(
        canonical_code=None,
        display_name=holding.display_name or holding.name,
        security_id=None,
        resolution_status=status,
        resolution_source=source,
        resolution_confidence=None,
        extra=_identity_extra(
            holding,
            status=status,
            source=source,
            confidence=None,
            candidates=candidates,
            error=error,
        ),
    )
    return HoldingInput.model_validate(values)


def _remote_search(
    provider: FuyaoSecurityProvider,
    query: str,
    *,
    exchange: str | None,
    asset_type: str | None,
) -> tuple[list[Mapping[str, Any]], str | None]:
    try:
        return provider.search(query, exchange=exchange, asset_type=asset_type, limit=50), None
    except (FuyaoAPIError, OSError, TimeoutError) as exc:
        return [], str(exc.__class__.__name__)
    except Exception as exc:  # provider adapters may surface requests exceptions directly
        return [], str(exc.__class__.__name__)


def resolve_holding_identity(
    db: Session,
    holding: HoldingInput,
    *,
    fuyao_provider: FuyaoSecurityProvider | None = None,
    allow_remote: bool = True,
) -> HoldingInput:
    """Resolve one holding using code-first, then exact-name matching."""

    identity_error = _identity_input_error(holding)
    if identity_error:
        source, message = identity_error
        return _unresolved_holding(holding, status=INVALID, source=source, error=message)

    raw_code = _identity_raw_code(holding)
    code = normalize_security_code(raw_code)
    explicit_exchange = exchange_hint(raw_code) or normalize_exchange(holding.exchange)
    inferred_exchange = exchange_for_code(code) if code else None
    if explicit_exchange and inferred_exchange and explicit_exchange != inferred_exchange:
        return _unresolved_holding(holding, status=INVALID, source="direct_code_exchange_mismatch", error="代码与交易所不一致")
    exchange = explicit_exchange or inferred_exchange
    asset_type = _asset_hint(holding)
    if asset_type not in {None, STOCK, ETF}:
        return _unresolved_holding(holding, status=INVALID, source="unsupported_asset_type", error="仅支持 A 股股票和场内 ETF")

    provider = fuyao_provider
    if code:
        local_rows = _unique_candidate_rows(
            [
                row
                for row in _local_code_rows(db, code, exchange)
                if not asset_type or _asset_type(row.security_type) == asset_type
            ]
        )
        if len(local_rows) == 1:
            return _resolved_holding(holding, local_rows[0], source="direct_code_local", confidence=1.0)
        if len(local_rows) > 1:
            candidates = [_candidate_dict(row) for row in local_rows]
            return _unresolved_holding(holding, status=AMBIGUOUS, source="direct_code_local_ambiguous", candidates=[item for item in candidates if item])
        if allow_remote:
            provider = provider or FuyaoSecurityProvider()
            rows, lookup_error = _remote_search(provider, code, exchange=exchange, asset_type=asset_type)
            if lookup_error:
                return _unresolved_holding(holding, status=UNRESOLVED, source="direct_code_fuyao_unavailable", error="证券查询暂不可用")
            matches = _unique_candidate_rows(_remote_code_matches(rows, code, asset_type, exchange))
            if len(matches) == 1:
                cached = _cache_remote_security(db, matches[0])
                return _resolved_holding(holding, cached, source="direct_code_fuyao", confidence=0.99)
            if len(matches) > 1:
                candidates = [_candidate_dict(item) for item in matches]
                return _unresolved_holding(holding, status=AMBIGUOUS, source="direct_code_fuyao_ambiguous", candidates=[item for item in candidates if item])
        return _unresolved_holding(holding, status=INVALID, source="direct_code_not_found", error="代码未通过 Security Master 验证")

    name = (holding.name or holding.display_name or "").strip()
    if not name:
        return _unresolved_holding(holding, status=UNRESOLVED, source="name_missing", error="缺少证券名称")
    local_rows = _unique_candidate_rows(_local_name_rows(db, name, normalize_exchange(holding.exchange), asset_type))
    if len(local_rows) == 1:
        return _resolved_holding(holding, local_rows[0], source="name_exact_local", confidence=0.98)
    if len(local_rows) > 1:
        candidates = [_candidate_dict(row) for row in local_rows]
        return _unresolved_holding(holding, status=AMBIGUOUS, source="name_exact_local_ambiguous", candidates=[item for item in candidates if item])

    if allow_remote:
        provider = provider or FuyaoSecurityProvider()
        rows, lookup_error = _remote_search(
            provider,
            name,
            exchange=normalize_exchange(holding.exchange),
            asset_type=asset_type,
        )
        if lookup_error:
            return _unresolved_holding(holding, status=UNRESOLVED, source="name_fuyao_unavailable", error="证券查询暂不可用")
        matches = _unique_candidate_rows(_remote_name_matches(rows, name, asset_type, normalize_exchange(holding.exchange)))
        if len(matches) == 1:
            cached = _cache_remote_security(db, matches[0])
            return _resolved_holding(holding, cached, source="name_exact_fuyao", confidence=0.97)
        if len(matches) > 1:
            candidates = [_candidate_dict(item) for item in matches]
            return _unresolved_holding(holding, status=AMBIGUOUS, source="name_exact_fuyao_ambiguous", candidates=[item for item in candidates if item])
    return _unresolved_holding(holding, status=UNRESOLVED, source="name_not_found", error="未找到唯一证券身份")


def payload_identity_issues(payload: ParsedHoldingsPayload) -> list[dict[str, Any]]:
    """Return review-safe identity issues without trusting display names."""

    issues: list[dict[str, Any]] = []
    for index, holding in enumerate(payload.holdings):
        status = str(holding.resolution_status or UNRESOLVED).upper()
        if status == RESOLVED and holding.canonical_code and holding.security_id:
            continue
        if status not in IDENTITY_STATUSES:
            status = UNRESOLVED
        extra = holding.extra or {}
        issues.append(
            {
                "index": index,
                "status": status,
                "label": STATUS_LABELS[status],
                "code": holding.canonical_code or holding.code or extra.get("submitted_code") or "",
                "name": holding.display_name or holding.name or "",
                "candidates": extra.get("identity_candidates") or [],
                "message": extra.get("identity_error") or STATUS_LABELS[status],
            }
        )
    return issues


def resolve_payload_identities(
    db: Session,
    payload: ParsedHoldingsPayload,
    *,
    fuyao_provider: FuyaoSecurityProvider | None = None,
    allow_remote: bool = True,
) -> tuple[ParsedHoldingsPayload, list[dict[str, Any]]]:
    resolved: list[HoldingInput] = []
    provider = fuyao_provider or (FuyaoSecurityProvider() if allow_remote else None)
    seen_codes: set[str] = set()
    for holding in payload.holdings:
        item = resolve_holding_identity(db, holding, fuyao_provider=provider, allow_remote=allow_remote)
        canonical = item.canonical_code if item.resolution_status == RESOLVED else None
        if canonical and canonical in seen_codes:
            item = _unresolved_holding(
                item,
                status=INVALID,
                source="duplicate_canonical_code",
                error="同一正式证券在持仓中重复",
            )
        if canonical:
            seen_codes.add(canonical)
        resolved.append(item)
    result = payload.model_copy(update={"holdings": resolved})
    return result, payload_identity_issues(result)


def audit_holding_item(db: Session, row: HoldingItem) -> dict[str, Any]:
    """Audit a persisted row using local identity facts only."""

    extra = dict(row.extra_json or {})
    row_code = str(row.code or "").strip()
    stored_canonical = str(extra.get("canonical_code") or "").strip()
    security_id = extra.get("security_id")
    # An existing authoritative snapshot may carry either a qualified
    # canonical code or a SecurityMaster id.  A bare code alone is not an
    # authority; keep it auditable but block analysis until it can be verified.
    canonical_raw = stored_canonical if exchange_hint(stored_canonical) else (
        row_code if exchange_hint(row_code) else ""
    )
    if not canonical_raw and security_id in (None, ""):
        return {"status": UNRESOLVED, "source": "snapshot_audit_missing_identity_authority", "master": None}

    raw_values = (
        ("row_code", row_code),
        ("canonical_code", stored_canonical),
        ("submitted_code", extra.get("submitted_code")),
        ("submitted_canonical_code", extra.get("submitted_canonical_code")),
    )
    tokens: list[tuple[str, str, str, str | None]] = []
    for label, value in raw_values:
        text = str(value or "").strip()
        if text:
            tokens.append((label, text, normalize_security_code(text), exchange_hint(text)))
    invalid_tokens = [label for label, _text, normalized, _hint in tokens if not normalized]
    if invalid_tokens:
        return {"status": INVALID, "source": "snapshot_audit_invalid_code", "master": None}

    normalized_codes = {normalized for _label, _text, normalized, _hint in tokens}
    if len(normalized_codes) > 1:
        return {"status": INVALID, "source": "snapshot_audit_conflicting_code", "master": None}
    code = next(iter(normalized_codes), "")
    hints = {hint for _label, _text, _normalized, hint in tokens if hint}
    stored_exchange = normalize_exchange(extra.get("exchange"))
    if stored_exchange and stored_exchange not in {"SSE", "SZSE", "BSE"}:
        return {"status": INVALID, "source": "snapshot_audit_invalid_exchange", "master": None}
    if stored_exchange and hints and stored_exchange not in hints:
        return {"status": INVALID, "source": "snapshot_audit_exchange_mismatch", "master": None}
    exchange = next(iter(hints), None) or stored_exchange or (exchange_for_code(code) if code else None)
    expected_type = _asset_type(extra.get("asset_type") or extra.get("security_type"))
    if expected_type not in {None, STOCK, ETF}:
        return {"status": INVALID, "source": "snapshot_audit_asset_type_mismatch", "master": None}

    master = None
    if security_id not in (None, ""):
        try:
            master = db.get(SecurityMaster, int(security_id))
        except (TypeError, ValueError):
            return {"status": INVALID, "source": "snapshot_audit_invalid_security_id", "master": None}
        if master is None:
            return {"status": INVALID, "source": "snapshot_audit_missing_security_id", "master": None}
    elif code:
        rows = _unique_candidate_rows(_local_code_rows(db, code, exchange))
        if len(rows) == 1:
            master = rows[0]
        elif len(rows) > 1:
            return {"status": AMBIGUOUS, "source": "snapshot_audit_ambiguous", "master": None}
    if master is None:
        status = INVALID if code else UNRESOLVED
        return {"status": status, "source": "snapshot_audit_missing", "master": None}

    candidate = _candidate_dict(master)
    if candidate is None:
        return {"status": INVALID, "source": "snapshot_audit_invalid_master", "master": None}
    if code and candidate["code"] != code:
        return {"status": INVALID, "source": "snapshot_audit_code_mismatch", "master": None}
    if canonical_raw and canonical_security_code(canonical_raw) != candidate["canonical_code"]:
        return {"status": INVALID, "source": "snapshot_audit_canonical_code_mismatch", "master": None}
    if exchange and candidate["exchange"] != exchange:
        return {"status": INVALID, "source": "snapshot_audit_exchange_mismatch", "master": None}
    if expected_type and expected_type != candidate["asset_type"]:
        return {"status": INVALID, "source": "snapshot_audit_asset_type_mismatch", "master": None}
    if security_id is not None and candidate.get("security_id") != int(security_id):
        return {"status": INVALID, "source": "snapshot_audit_security_id_mismatch", "master": None}
    return {
        "status": RESOLVED,
        "source": extra.get("resolution_source") or "snapshot_audit_local",
        "confidence": extra.get("resolution_confidence") or 1.0,
        "master": master,
        **candidate,
    }


def snapshot_identity_issues(db: Session, snapshot: PortfolioSnapshot) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, row in enumerate(snapshot.holdings):
        audit = audit_holding_item(db, row)
        if audit["status"] == RESOLVED:
            continue
        issues.append(
            {
                "index": index,
                "status": audit["status"],
                "label": STATUS_LABELS[audit["status"]],
                "code": row.code or (row.extra_json or {}).get("canonical_code") or "",
                "name": row.name or "",
                "message": STATUS_LABELS[audit["status"]],
            }
        )
    return issues


__all__ = [
    "AMBIGUOUS",
    "IDENTITY_STATUSES",
    "INVALID",
    "RESOLVED",
    "STATUS_LABELS",
    "UNRESOLVED",
    "UnresolvedSecurityIdentityError",
    "audit_holding_item",
    "normalize_security_name",
    "payload_identity_issues",
    "resolve_holding_identity",
    "resolve_payload_identities",
    "snapshot_identity_issues",
]
