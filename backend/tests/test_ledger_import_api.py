"""API-level checks for derived account state and statement import dedupe."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest


def _headers(client, email: str) -> dict[str, str]:
    password = "password123"
    assert client.post("/api/v2/auth/register", json={"email": email, "password": password}).status_code == 201
    login = client.post("/api/v2/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _confirmed_snapshot(client, headers: dict[str, str], portfolio_id: int, holdings: dict) -> int:
    upload = client.post(
        f"/api/v2/portfolios/{portfolio_id}/uploads",
        headers=headers,
        data={"holdings_json": json.dumps(holdings, ensure_ascii=False)},
        files={"screenshot": ("holdings.png", b"\x89PNG\r\n\x1a\n" + b"test-image", "image/png")},
    )
    assert upload.status_code == 201, upload.text
    snapshot = client.post(f"/api/v2/uploads/{upload.json()['id']}/confirm", headers=headers)
    assert snapshot.status_code == 201, snapshot.text
    return snapshot.json()["id"]


def test_ledger_entry_updates_derived_state_and_import_dedupes(monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import SessionLocal, init_db
    from app.main import app
    from app.portfolio import risk as risk_module
    from app.services.security_master import STOCK, upsert_security

    monkeypatch.setattr(
        risk_module,
        "_default_quote_loader",
        lambda codes: {"quotes": [{"code": code, "price": 100.0, "quality_status": "VALID"} for code in codes]},
    )
    init_db()
    with SessionLocal() as db:
        upsert_security(db, {"code": "600519", "exchange": "SSE", "name": "贵州茅台", "security_type": STOCK})
        db.commit()
    client = TestClient(app)
    suffix = uuid.uuid4().hex
    headers = _headers(client, f"ledger-import-{suffix}@example.com")
    portfolio = client.post("/api/v2/portfolios", headers=headers, json={"name": f"Account-{suffix[:8]}"})
    assert portfolio.status_code == 201, portfolio.text
    portfolio_id = portfolio.json()["id"]

    snapshot_id = _confirmed_snapshot(
        client,
        headers,
        portfolio_id,
        {
            "holdings": [{
                "code": "600519", "name": "贵州茅台", "qty": 1000, "available_qty": 1000,
                "cost": 10, "price": 10, "market_value": 10_000,
            }],
            "total_assets": 30_000,
            "total_market_value": 10_000,
            "broker_available_cash": 20_000,
            "excluded_items": [],
            "notes": [],
        },
    )

    sell = client.post(
        f"/api/v3/portfolios/{portfolio_id}/ledger",
        headers=headers,
        json={
            "entry_type": "TRADE", "security_code": "600519", "security_name": "贵州茅台",
            "side": "SELL", "quantity": 300, "price": 10, "fees": 5, "taxes": 0,
            "executed_at": datetime.now(UTC).isoformat(), "broker_order_id": "API-SELL-1",
            "source": "MANUAL",
        },
    )
    assert sell.status_code == 201, sell.text

    state = client.get(f"/api/v3/portfolios/{portfolio_id}/state", headers=headers)
    assert state.status_code == 200, state.text
    payload = state.json()
    position = payload["positions"][0]
    assert position["qty"] == 700
    assert position["available_qty"] == 700
    assert payload["cash"] == 20_000
    assert payload["pending_sell_proceeds"] == pytest.approx(2_995)
    assert payload["account_version"].startswith("portfolio-account-v1:")
    assert payload["account_derivation"]["applied_entry_ids"] == [sell.json()["id"]]

    snapshot = client.get(f"/api/v2/snapshots/{snapshot_id}", headers=headers)
    assert snapshot.status_code == 200
    assert snapshot.json()["holdings"][0]["qty"] == 1000

    csv_content = (
        "成交日期,证券代码,证券名称,买卖方向,成交数量,成交价格,手续费,印花税,合同编号\n"
        "2026-10-02,600519,贵州茅台,卖出,300,10,5,0,API-SELL-1\n"
        "2026-10-02,600519,贵州茅台,买入,100,9,5,0,API-BUY-2\n"
    ).encode("utf-8-sig")
    preview = client.post(
        f"/api/v3/portfolios/{portfolio_id}/ledger/import/preview",
        headers=headers,
        files={"file": ("trades.csv", csv_content, "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    assert preview_payload["summary"] == {"total": 2, "ready": 1, "duplicates": 1, "invalid": 0}
    assert preview_payload["rows"][0]["status"] == "DUPLICATE"
    assert preview_payload["rows"][0]["duplicate_kind"] == "LEDGER_ORDER_ID"
    ready_rows = [row["normalized"] for row in preview_payload["rows"] if row["status"] == "READY"]

    commit = client.post(
        f"/api/v3/portfolios/{portfolio_id}/ledger/import",
        headers=headers,
        json={"source_ref": preview_payload["source_ref"], "rows": ready_rows},
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["created"] == 1
    again = client.post(
        f"/api/v3/portfolios/{portfolio_id}/ledger/import",
        headers=headers,
        json={"source_ref": preview_payload["source_ref"], "rows": ready_rows},
    )
    assert again.status_code == 200, again.text
    assert again.json()["created"] == 0
    assert again.json()["skipped"] == 1

    entries = client.get(f"/api/v3/portfolios/{portfolio_id}/ledger", headers=headers)
    assert entries.status_code == 200
    imported = [row for row in entries.json() if row["source"] == "CSV_IMPORT"]
    assert len(imported) == 1
    assert imported[0]["broker_order_id"] == "API-BUY-2"
