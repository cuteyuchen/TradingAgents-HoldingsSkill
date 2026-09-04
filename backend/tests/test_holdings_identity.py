"""Phase O.2 holding encoding and canonical-identity contracts."""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_identity_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_ARTIFACTS_DIR", os.path.join(BACKEND_DIR, "data", f"test_identity_artifacts_{os.getpid()}"))
os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)

from app.market_models import SecurityMaster
from app.services.holding_identity import (
    AMBIGUOUS,
    INVALID,
    RESOLVED,
    UNRESOLVED,
    normalize_security_name,
    resolve_holding_identity,
    resolve_payload_identities,
)
from app.routers.fuyao_v3 import _resolved_holding_rows
from app.services.holdings_service import parse_payload_dict
from app.services.security_master import ETF, STOCK, upsert_security
from app.v2_models import AnalysisJob, HoldingItem, PortfolioSnapshot, User
from app.v2_schemas import ParsedHoldingsPayload


@pytest.fixture
def security_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SecurityMaster.__table__.create(engine)
    with Session(engine) as db:
        yield db


def _seed(db: Session, code: str, exchange: str, name: str, security_type: str = STOCK) -> None:
    upsert_security(db, {"code": code, "exchange": exchange, "name": name, "security_type": security_type})


def _seed_history(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    code: str,
    exchange: str,
    name: str,
    alias: str,
    security_type: str,
    when: datetime,
) -> None:
    master = db.query(SecurityMaster).filter(
        SecurityMaster.code == code,
        SecurityMaster.exchange == exchange,
    ).one()
    PortfolioSnapshot.__table__.create(db.get_bind(), checkfirst=True)
    HoldingItem.__table__.create(db.get_bind(), checkfirst=True)
    snapshot = PortfolioSnapshot(
        user_id=user_id,
        portfolio_id=portfolio_id,
        snapshot_time=when,
        status="confirmed",
    )
    db.add(snapshot)
    db.flush()
    db.add(
        HoldingItem(
            snapshot_id=snapshot.id,
            code=code,
            name=name,
            extra_json={
                "security_id": master.id,
                "canonical_code": f"{code}.{'SH' if exchange == 'SSE' else 'SZ'}",
                "ocr_name": alias,
                "name": name,
                "display_name": name,
                "asset_type": security_type,
                "exchange": exchange,
                "resolution_status": RESOLVED,
                "resolution_source": "portfolio_history",
                "resolution_confidence": 1.0,
            },
        )
    )
    db.commit()


def test_same_portfolio_history_resolves_short_alias(security_db):
    _seed(security_db, "159915", "SZSE", "创业板ETF", ETF)
    _seed_history(
        security_db,
        user_id=7,
        portfolio_id=11,
        code="159915",
        exchange="SZSE",
        name="创业板ETF",
        alias="创业板",
        security_type=ETF,
        when=datetime(2026, 1, 1),
    )
    holding = parse_payload_dict({"holdings": [{"name": "创业板"}]})[0].holdings[0]

    resolved = resolve_holding_identity(
        security_db,
        holding,
        allow_remote=False,
        user_id=7,
        portfolio_id=11,
    )

    assert resolved.resolution_status == RESOLVED
    assert resolved.canonical_code == "159915.SZ"
    assert resolved.resolution_source == "portfolio_history"
    assert resolved.name == "创业板ETF"
    assert resolved.extra["ocr_name"] == "创业板"
    assert resolved.security_id == security_db.query(SecurityMaster).filter(SecurityMaster.code == "159915").one().id


def test_conflicting_history_alias_is_ambiguous_not_last_winner(security_db):
    _seed(security_db, "600001", "SSE", "同名验收股票A", STOCK)
    _seed(security_db, "000001", "SZSE", "同名验收股票A", STOCK)
    _seed_history(
        security_db,
        user_id=7,
        portfolio_id=11,
        code="600001",
        exchange="SSE",
        name="同名验收股票A",
        alias="同名股票",
        security_type=STOCK,
        when=datetime(2026, 1, 2),
    )
    _seed_history(
        security_db,
        user_id=7,
        portfolio_id=11,
        code="000001",
        exchange="SZSE",
        name="同名验收股票A",
        alias="同名股票",
        security_type=STOCK,
        when=datetime(2026, 1, 1),
    )
    holding = parse_payload_dict({"holdings": [{"name": "同名股票"}]})[0].holdings[0]

    resolved = resolve_holding_identity(
        security_db,
        holding,
        allow_remote=False,
        user_id=7,
        portfolio_id=11,
    )

    assert resolved.resolution_status == AMBIGUOUS
    assert resolved.canonical_code is None
    assert resolved.resolution_source == "portfolio_history_ambiguous"
    assert len(resolved.extra["identity_candidates"]) == 2


def test_delisted_history_is_not_auto_reused(security_db):
    _seed(security_db, "159915", "SZSE", "创业板ETF", ETF)
    _seed_history(
        security_db,
        user_id=7,
        portfolio_id=11,
        code="159915",
        exchange="SZSE",
        name="创业板ETF",
        alias="历史旧名",
        security_type=ETF,
        when=datetime(2026, 1, 1),
    )
    master = security_db.query(SecurityMaster).filter(SecurityMaster.code == "159915").one()
    master.status = "DELISTED"
    security_db.commit()
    holding = parse_payload_dict({"holdings": [{"name": "历史旧名"}]})[0].holdings[0]

    resolved = resolve_holding_identity(
        security_db,
        holding,
        allow_remote=False,
        user_id=7,
        portfolio_id=11,
    )

    assert resolved.resolution_status == UNRESOLVED
    assert resolved.canonical_code is None


def test_history_is_scoped_to_same_user_and_portfolio(security_db):
    _seed(security_db, "159915", "SZSE", "创业板ETF", ETF)
    _seed_history(
        security_db,
        user_id=7,
        portfolio_id=11,
        code="159915",
        exchange="SZSE",
        name="创业板ETF",
        alias="历史旧名",
        security_type=ETF,
        when=datetime(2026, 1, 1),
    )
    holding = parse_payload_dict({"holdings": [{"name": "历史旧名"}]})[0].holdings[0]

    resolved = resolve_holding_identity(
        security_db,
        holding,
        allow_remote=False,
        user_id=8,
        portfolio_id=12,
    )

    assert resolved.resolution_status == UNRESOLVED
    assert resolved.canonical_code is None

    resolved_other_portfolio = resolve_holding_identity(
        security_db,
        holding,
        allow_remote=False,
        user_id=7,
        portfolio_id=12,
    )
    assert resolved_other_portfolio.resolution_status == UNRESOLVED


def test_controlled_rank_resolves_fund_suffix_variant_but_not_bare_short_name(security_db):
    _seed(security_db, "512480", "SSE", "半导体ETF", ETF)
    _seed(security_db, "159915", "SZSE", "创业板ETF", ETF)
    security_db.commit()

    suffix_variant = parse_payload_dict({"holdings": [{"name": "半导体NF"}]})[0].holdings[0]
    resolved = resolve_holding_identity(security_db, suffix_variant, allow_remote=False)
    assert resolved.resolution_status == RESOLVED
    assert resolved.canonical_code == "512480.SH"
    assert resolved.resolution_source == "name_ranked_local"

    bare_short = parse_payload_dict({"holdings": [{"name": "创业板"}]})[0].holdings[0]
    ambiguous = resolve_holding_identity(security_db, bare_short, allow_remote=False)
    assert ambiguous.resolution_status == AMBIGUOUS
    assert ambiguous.canonical_code is None


def test_unicode_names_survive_parser_json_and_db_roundtrip(security_db):
    names = [
        ("159915.SZ", "创业板ETF", "SZSE", ETF),
        ("515880.SH", "通信ETF", "SSE", ETF),
        ("512400.SH", "有色ETF", "SSE", ETF),
        ("512480.SH", "半导体ETF", "SSE", ETF),
        ("588000.SH", "科创50ETF", "SSE", ETF),
        ("512100.SH", "中证1000ETF", "SSE", ETF),
        ("510300.SH", "沪深300ETF", "SSE", ETF),
        ("600519.SH", "贵州茅台", "SSE", STOCK),
        ("300750.SZ", "宁德时代", "SZSE", STOCK),
    ]
    for code, name, exchange, security_type in names:
        _seed(security_db, code[:6], exchange, name, security_type)
    security_db.commit()

    parsed, errors = parse_payload_dict({"holdings": [{"code": code, "name": name} for code, name, _, _ in names]})
    encoded = json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
    decoded = ParsedHoldingsPayload.model_validate(json.loads(encoded.decode("utf-8")))
    resolved, issues = resolve_payload_identities(security_db, decoded, allow_remote=False)

    assert errors == []
    assert issues == []
    assert [item.name for item in resolved.holdings] == [name for _, name, _, _ in names]
    assert all(item.resolution_status == RESOLVED for item in resolved.holdings)
    assert [item.canonical_code for item in resolved.holdings] == [code for code, _, _, _ in names]

    db_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    PortfolioSnapshot.__table__.create(db_engine)
    HoldingItem.__table__.create(db_engine)
    with Session(db_engine) as db:
        snapshot = PortfolioSnapshot(user_id=1, portfolio_id=1, status="confirmed", raw_json=resolved.model_dump(mode="json"))
        db.add(snapshot)
        db.flush()
        db.add(
            HoldingItem(
                snapshot_id=snapshot.id,
                code="159915",
                name="创业板ETF",
                extra_json=resolved.holdings[0].model_dump(mode="json"),
            )
        )
        db.commit()
        loaded = db.query(HoldingItem).one()
        assert loaded.name == "创业板ETF"
        assert loaded.extra_json["canonical_code"] == "159915.SZ"


def test_model_transport_decodes_utf8_before_json_or_sse_parsing():
    from app.services.model_client import _response_json_utf8, _sse_payloads

    body = json.dumps({"message": "创业板ETF", "choices": []}, ensure_ascii=False).encode("utf-8")

    class JsonResponse:
        content = body

        def json(self):
            raise AssertionError("transport JSON decoder must not override UTF-8 bytes")

    assert _response_json_utf8(JsonResponse())["message"] == "创业板ETF"

    class SseResponse:
        def iter_lines(self, *, decode_unicode):
            assert decode_unicode is False
            return [f"data: {json.dumps({'name': '通信ETF'}, ensure_ascii=False)}".encode("utf-8"), b"data: [DONE]"]

    assert list(_sse_payloads(SseResponse())) == [{"name": "通信ETF"}]


def test_name_normalization_is_exact_and_deterministic(security_db):
    _seed(security_db, "159915", "SZSE", "创业板ETF", ETF)
    security_db.commit()
    holding = parse_payload_dict({"holdings": [{"name": "  创业板　ETF （场内） "}]})[0].holdings[0]

    resolved = resolve_holding_identity(security_db, holding, allow_remote=False)

    assert normalize_security_name(" 创业板　ETF （场内） ") == normalize_security_name("创业板ETF")
    assert resolved.resolution_status == RESOLVED
    assert resolved.canonical_code == "159915.SZ"
    assert resolved.name == "创业板ETF"


def test_ambiguous_unknown_invalid_and_wrong_type_fail_closed(security_db):
    _seed(security_db, "600001", "SSE", "同名ETF", ETF)
    _seed(security_db, "000001", "SZSE", "同名ETF", ETF)
    _seed(security_db, "600519", "SSE", "贵州茅台", STOCK)
    security_db.commit()

    ambiguous = resolve_holding_identity(
        security_db,
        parse_payload_dict({"holdings": [{"name": "同名ETF"}]})[0].holdings[0],
        allow_remote=False,
    )
    unknown = resolve_holding_identity(
        security_db,
        parse_payload_dict({"holdings": [{"name": "不存在的验收标的"}]})[0].holdings[0],
        allow_remote=False,
    )
    invalid = resolve_holding_identity(
        security_db,
        parse_payload_dict({"holdings": [{"code": "999999", "name": "未知"}]})[0].holdings[0],
        allow_remote=False,
    )
    wrong_type = resolve_holding_identity(
        security_db,
        parse_payload_dict({"holdings": [{"code": "600519", "name": "贵州茅台", "asset_type": ETF}]})[0].holdings[0],
        allow_remote=False,
    )

    assert ambiguous.resolution_status == AMBIGUOUS
    assert len(ambiguous.extra["identity_candidates"]) == 2
    assert unknown.resolution_status == UNRESOLVED
    assert invalid.resolution_status == INVALID
    assert wrong_type.resolution_status == INVALID


def test_sh_sz_bj_and_etf_constraints(security_db):
    _seed(security_db, "600519", "SSE", "贵州茅台", STOCK)
    _seed(security_db, "300750", "SZSE", "宁德时代", STOCK)
    _seed(security_db, "920001", "BSE", "北交测试", STOCK)
    _seed(security_db, "159915", "SZSE", "创业板ETF", ETF)
    security_db.commit()

    payload, _ = parse_payload_dict(
        {
            "holdings": [
                {"code": "600519.SH", "name": "OCR名称"},
                {"code": "300750.SZ", "name": "OCR名称"},
                {"code": "920001.BJ", "name": "OCR名称"},
                {"code": "159915.SZ", "name": "创业板ETF", "asset_type": ETF},
            ]
        }
    )
    resolved, issues = resolve_payload_identities(security_db, payload, allow_remote=False)

    assert issues == []
    assert [item.canonical_code for item in resolved.holdings] == ["600519.SH", "300750.SZ", "920001.BJ", "159915.SZ"]
    assert resolved.holdings[-1].asset_type == ETF
    assert resolved.holdings[-1].exchange == "SZSE"


def test_invalid_or_conflicting_submitted_code_never_falls_back_to_name(security_db):
    _seed(security_db, "600519", "SSE", "贵州茅台", STOCK)
    _seed(security_db, "159915", "SZSE", "创业板ETF", ETF)
    security_db.commit()

    malformed = parse_payload_dict({"holdings": [{"code": "not-a-code", "name": "贵州茅台"}]})[0].holdings[0]
    result = resolve_holding_identity(security_db, malformed, allow_remote=False)
    assert result.resolution_status == INVALID
    assert result.canonical_code is None

    exchange_mismatch = parse_payload_dict(
        {"holdings": [{"code": "600519.SH", "exchange": "SZ", "name": "贵州茅台"}]}
    )[0].holdings[0]
    result = resolve_holding_identity(security_db, exchange_mismatch, allow_remote=False)
    assert result.resolution_status == INVALID

    conflicting_tokens = parse_payload_dict(
        {"holdings": [{"code": "159915", "canonical_code": "600519.SH", "name": "创业板ETF"}]}
    )[0].holdings[0]
    result = resolve_holding_identity(security_db, conflicting_tokens, allow_remote=False)
    assert result.resolution_status == INVALID


def test_unresolved_holding_does_not_expose_unverified_canonical_code(security_db):
    holding = parse_payload_dict({"holdings": [{"code": "600519", "name": "不存在的验收标的"}]})[0].holdings[0]
    assert holding.canonical_code is None
    resolved = resolve_holding_identity(security_db, holding, allow_remote=False)
    assert resolved.resolution_status == INVALID
    assert resolved.canonical_code is None

    bare_canonical = parse_payload_dict(
        {"holdings": [{"canonical_code": "600519", "name": "不存在的验收标的"}]}
    )[0].holdings[0]
    assert bare_canonical.canonical_code is None
    assert bare_canonical.extra["submitted_canonical_code"] == "600519"


def test_quote_inputs_skip_unresolved_snapshot_rows(security_db):
    _seed(security_db, "600519", "SSE", "贵州茅台", STOCK)
    security_db.commit()
    master = security_db.query(SecurityMaster).filter(SecurityMaster.code == "600519").one()
    rows = _resolved_holding_rows(
        security_db,
        [
            HoldingItem(
                code="600519",
                name="错误名称",
                qty=100,
                extra_json={
                    "canonical_code": "600519.SH",
                    "security_id": master.id,
                    "exchange": "SSE",
                    "asset_type": STOCK,
                },
            ),
            HoldingItem(code="300750", name="未确认证券", qty=100, extra_json={}),
        ],
    )

    assert rows == [{"code": "600519", "name": "贵州茅台", "qty": 100, "market_value": None, "cost": None}]


def test_snapshot_audit_rejects_mismatched_security_id_and_code(security_db):
    _seed(security_db, "600519", "SSE", "贵州茅台", STOCK)
    _seed(security_db, "159915", "SZSE", "创业板ETF", ETF)
    security_db.commit()
    stock = security_db.query(SecurityMaster).filter(SecurityMaster.code == "600519").one()

    db_engine = security_db.get_bind()
    PortfolioSnapshot.__table__.create(db_engine, checkfirst=True)
    HoldingItem.__table__.create(db_engine, checkfirst=True)
    snapshot = PortfolioSnapshot(user_id=1, portfolio_id=1, status="confirmed")
    security_db.add(snapshot)
    security_db.flush()
    security_db.add(
        HoldingItem(
            snapshot_id=snapshot.id,
            code="159915",
            name="贵州茅台",
            extra_json={"security_id": stock.id, "canonical_code": "159915.SZ", "asset_type": ETF},
        )
    )
    security_db.commit()
    security_db.refresh(snapshot)
    issues = __import__("app.services.holding_identity", fromlist=["snapshot_identity_issues"]).snapshot_identity_issues(
        security_db, snapshot
    )
    assert issues and issues[0]["status"] == INVALID


def test_snapshot_audit_accepts_either_verified_canonical_or_security_id(security_db):
    _seed(security_db, "600519", "SSE", "贵州茅台", STOCK)
    security_db.commit()
    master = security_db.query(SecurityMaster).filter(SecurityMaster.code == "600519").one()

    db_engine = security_db.get_bind()
    PortfolioSnapshot.__table__.create(db_engine, checkfirst=True)
    HoldingItem.__table__.create(db_engine, checkfirst=True)
    snapshot = PortfolioSnapshot(user_id=1, portfolio_id=1, status="confirmed")
    security_db.add(snapshot)
    security_db.flush()
    security_db.add_all(
        [
            HoldingItem(
                snapshot_id=snapshot.id,
                code="600519",
                name="旧显示名",
                extra_json={"canonical_code": "600519.SH", "asset_type": STOCK},
            ),
            HoldingItem(
                snapshot_id=snapshot.id,
                code=None,
                name="旧显示名",
                extra_json={"security_id": master.id, "asset_type": STOCK},
            ),
        ]
    )
    security_db.commit()
    security_db.refresh(snapshot)
    assert __import__("app.services.holding_identity", fromlist=["snapshot_identity_issues"]).snapshot_identity_issues(
        security_db, snapshot
    ) == []


def test_fuyao_search_is_used_after_local_master_miss(security_db):
    calls: list[dict[str, object]] = []

    class FakeFuyao:
        def search(self, query, *, exchange=None, asset_type=None, limit=50):
            calls.append({"query": query, "exchange": exchange, "asset_type": asset_type, "limit": limit})
            return [{"thscode": "300750.SZ", "name": "宁德时代", "exchange": "SZ", "asset_type": "stock"}]

    holding = parse_payload_dict({"holdings": [{"name": "宁德时代"}]})[0].holdings[0]
    resolved = resolve_holding_identity(security_db, holding, fuyao_provider=FakeFuyao(), allow_remote=True)

    assert resolved.resolution_status == RESOLVED
    assert resolved.canonical_code == "300750.SZ"
    assert calls == [{"query": "宁德时代", "exchange": None, "asset_type": None, "limit": 50}]
    assert security_db.query(SecurityMaster).filter(SecurityMaster.code == "300750").one().name == "宁德时代"


def test_confirm_rejects_unresolved_without_persisting_snapshot():
    from fastapi.testclient import TestClient

    from app.database import SessionLocal, init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    suffix = uuid.uuid4().hex
    email = f"identity-{suffix}@example.com"
    assert client.post("/api/v2/auth/register", json={"email": email, "password": "password123"}).status_code == 201
    login = client.post("/api/v2/auth/login", json={"email": email, "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    portfolio = client.post("/api/v2/portfolios", headers=headers, json={"name": f"身份验收-{suffix[:8]}"})
    portfolio_id = portfolio.json()["id"]
    before = client.get(f"/api/v2/portfolios/{portfolio_id}/snapshots", headers=headers)
    assert before.status_code == 200

    upload = client.post(
        f"/api/v2/portfolios/{portfolio_id}/uploads",
        headers=headers,
        data={"holdings_json": json.dumps({"holdings": [{"name": "不存在的验收标的", "qty": 100}]}, ensure_ascii=False)},
        files={"screenshot": ("identity.png", b"\x89PNG\r\n\x1a\n" + b"test-image", "image/png")},
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["identity_issues"][0]["status"] == UNRESOLVED
    assert "charset=utf-8" in upload.headers.get("content-type", "").lower()
    assert "不存在的验收标的".encode("utf-8") in upload.content

    blocked = client.post(f"/api/v2/uploads/{upload.json()['id']}/confirm", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "holding_identity_unresolved"
    assert "未确认证券身份" in blocked.json()["detail"]["message"]
    after = client.get(f"/api/v2/portfolios/{portfolio_id}/snapshots", headers=headers)
    assert after.status_code == 200
    assert after.json() == before.json()
    with SessionLocal() as db:
        assert db.query(PortfolioSnapshot).filter(PortfolioSnapshot.portfolio_id == portfolio_id).count() == 0

        user = db.query(User).filter(User.email == email).one()
        old_snapshot = PortfolioSnapshot(
            user_id=user.id,
            portfolio_id=portfolio_id,
            status="confirmed",
            raw_json={"holdings": [{"name": "历史未解析持仓"}]},
        )
        db.add(old_snapshot)
        db.flush()
        db.add(HoldingItem(snapshot_id=old_snapshot.id, code=None, name="历史未解析持仓", extra_json={}))
        old_job = AnalysisJob(
            user_id=user.id,
            portfolio_id=portfolio_id,
            snapshot_id=old_snapshot.id,
            trigger_type="manual",
            status="queued",
            current_stage="queued",
        )
        db.add(old_job)
        db.commit()
        old_snapshot_id = old_snapshot.id
        old_job_id = old_job.id

    analysis_entry = client.post(
        "/api/v2/analysis/jobs",
        headers=headers,
        json={"snapshot_id": old_snapshot_id, "mode": "deep", "notify": False},
    )
    assert analysis_entry.status_code == 409
    assert analysis_entry.json()["detail"]["code"] == "unresolved_security_identity"

    from app.services.analysis_engine import run_analysis_job

    run_analysis_job(old_job_id)
    with SessionLocal() as db:
        blocked_job = db.get(AnalysisJob, old_job_id)
        assert blocked_job.status == "failed"
        assert blocked_job.current_stage == "blocked"
        assert blocked_job.error_code == "unresolved_security_identity"
