"""V2 portfolios, holdings screenshot uploads, confirmation, and snapshots."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.holdings_service import normalize_payload, parse_payload_dict, parse_upload
from ..services.storage import resolve_storage_path, save_holding_image
from ..v2_dependencies import get_current_user
from ..v2_models import HoldingItem, HoldingUpload, Portfolio, PortfolioSnapshot, User
from ..v2_schemas import (
    HoldingInput,
    ParsedHoldingsPayload,
    ParsedHoldingsUpdate,
    PortfolioCreate,
    PortfolioResponse,
    PortfolioUpdate,
    SnapshotResponse,
    UploadResponse,
)

router = APIRouter(prefix="/api/v2", tags=["v2-portfolios"])


def _get_portfolio(db: Session, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return row


def _get_upload(db: Session, user_id: int, upload_id: int) -> HoldingUpload:
    row = db.query(HoldingUpload).filter(HoldingUpload.id == upload_id, HoldingUpload.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return row


def _get_snapshot(db: Session, user_id: int, snapshot_id: int) -> PortfolioSnapshot:
    row = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.id == snapshot_id, PortfolioSnapshot.user_id == user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Snapshot not found.")
    return row


def _unset_portfolio_defaults(db: Session, user_id: int, keep_id: int | None = None) -> None:
    query = db.query(Portfolio).filter(Portfolio.user_id == user_id, Portfolio.is_default.is_(True))
    if keep_id is not None:
        query = query.filter(Portfolio.id != keep_id)
    for row in query.all():
        row.is_default = False


def _portfolio_response(db: Session, row: Portfolio) -> PortfolioResponse:
    latest = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.portfolio_id == row.id, PortfolioSnapshot.status == "confirmed")
        .order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc())
        .first()
    )
    return PortfolioResponse(
        id=row.id,
        name=row.name,
        market=row.market,
        currency=row.currency,
        is_default=row.is_default,
        latest_snapshot_id=latest.id if latest else None,
        latest_snapshot_time=latest.snapshot_time if latest else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _upload_response(row: HoldingUpload) -> UploadResponse:
    parsed = ParsedHoldingsPayload.model_validate(row.parsed_json) if row.parsed_json else None
    return UploadResponse(
        id=row.id,
        portfolio_id=row.portfolio_id,
        original_filename=row.original_filename,
        mime_type=row.mime_type,
        parsing_status=row.parsing_status,
        parsed=parsed,
        validation_errors=list(row.validation_errors or []),
        error_message=row.error_message,
        screenshot_url=f"/api/v2/uploads/{row.id}/image",
        confirmed_at=row.confirmed_at,
        created_at=row.created_at,
    )


def _holding_schema(row: HoldingItem) -> HoldingInput:
    return HoldingInput(
        code=row.code or "",
        name=row.name,
        market=row.market,
        qty=row.qty,
        available_qty=row.available_qty,
        cost=row.cost,
        price=row.screenshot_price,
        market_value=row.market_value,
        pnl=row.pnl_ratio,
        pnl_amount=row.pnl_amount,
        weight=row.weight,
        extra=row.extra_json or {},
    )


def _snapshot_response(row: PortfolioSnapshot) -> SnapshotResponse:
    return SnapshotResponse(
        id=row.id,
        portfolio_id=row.portfolio_id,
        upload_id=row.upload_id,
        source=row.source,
        snapshot_time=row.snapshot_time,
        status=row.status,
        total_assets=row.total_assets,
        total_market_value=row.total_market_value,
        broker_available_cash=row.broker_available_cash,
        corrected_unused_funds=row.corrected_unused_funds,
        repo_or_standard_bond_value=row.repo_or_standard_bond_value,
        holdings=[_holding_schema(item) for item in row.holdings],
    )


@router.get("/portfolios", response_model=list[PortfolioResponse])
def list_portfolios(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PortfolioResponse]:
    rows = db.query(Portfolio).filter(Portfolio.user_id == current_user.id).order_by(Portfolio.id.asc()).all()
    return [_portfolio_response(db, row) for row in rows]


@router.post("/portfolios", response_model=PortfolioResponse, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    payload: PortfolioCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioResponse:
    duplicate = db.query(Portfolio).filter(Portfolio.user_id == current_user.id, Portfolio.name == payload.name).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="Portfolio name already exists.")
    first = db.query(Portfolio).filter(Portfolio.user_id == current_user.id).first() is None
    make_default = payload.is_default or first
    if make_default:
        _unset_portfolio_defaults(db, current_user.id)
    row = Portfolio(
        user_id=current_user.id,
        name=payload.name,
        market=payload.market,
        currency=payload.currency,
        is_default=make_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _portfolio_response(db, row)


@router.patch("/portfolios/{portfolio_id}", response_model=PortfolioResponse)
def update_portfolio(
    portfolio_id: int,
    payload: PortfolioUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioResponse:
    row = _get_portfolio(db, current_user.id, portfolio_id)
    fields = payload.model_fields_set
    if "name" in fields and payload.name is not None:
        duplicate = (
            db.query(Portfolio)
            .filter(Portfolio.user_id == current_user.id, Portfolio.name == payload.name, Portfolio.id != row.id)
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Portfolio name already exists.")
        row.name = payload.name
    if "market" in fields and payload.market is not None:
        row.market = payload.market
    if "currency" in fields and payload.currency is not None:
        row.currency = payload.currency
    if "is_default" in fields and payload.is_default is not None:
        row.is_default = payload.is_default
        if payload.is_default:
            _unset_portfolio_defaults(db, current_user.id, keep_id=row.id)
    db.commit()
    db.refresh(row)
    return _portfolio_response(db, row)


@router.delete("/portfolios/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    row = _get_portfolio(db, current_user.id, portfolio_id)
    db.delete(row)
    db.commit()


@router.post("/portfolios/{portfolio_id}/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload(
    portfolio_id: int,
    background_tasks: BackgroundTasks,
    screenshot: UploadFile = File(...),
    holdings_json: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResponse:
    _get_portfolio(db, current_user.id, portfolio_id)
    storage_path, mime_type, digest, _content = await save_holding_image(current_user.id, screenshot)
    parsed_json = None
    validation_errors: list[str] = []
    parsing_status = "uploaded"
    if holdings_json:
        try:
            raw = json.loads(holdings_json)
            if isinstance(raw, list):
                raw = {"holdings": raw}
            parsed, validation_errors = parse_payload_dict(raw)
            parsed_json = parsed.model_dump(mode="json")
            parsing_status = "waiting_confirmation"
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = HoldingUpload(
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        original_filename=screenshot.filename or "holdings-image",
        storage_path=storage_path,
        mime_type=mime_type,
        sha256=digest,
        parsing_status=parsing_status,
        parsed_json=parsed_json,
        validation_errors=validation_errors,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if not holdings_json:
        background_tasks.add_task(parse_upload, row.id)
    return _upload_response(row)


@router.get("/uploads/{upload_id}", response_model=UploadResponse)
def get_upload(
    upload_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResponse:
    return _upload_response(_get_upload(db, current_user.id, upload_id))


@router.get("/uploads/{upload_id}/image")
def get_upload_image(
    upload_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = _get_upload(db, current_user.id, upload_id)
    path = resolve_storage_path(row.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image file not found.")
    return FileResponse(path, media_type=row.mime_type, filename=row.original_filename)


@router.post("/uploads/{upload_id}/parse", response_model=UploadResponse)
def retry_parse_upload(
    upload_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResponse:
    row = _get_upload(db, current_user.id, upload_id)
    if row.confirmed_at:
        raise HTTPException(status_code=409, detail="Confirmed upload cannot be parsed again.")
    row.parsing_status = "uploaded"
    row.error_message = None
    db.commit()
    db.refresh(row)
    background_tasks.add_task(parse_upload, row.id)
    return _upload_response(row)


@router.patch("/uploads/{upload_id}/parsed-holdings", response_model=UploadResponse)
def update_parsed_holdings(
    upload_id: int,
    payload: ParsedHoldingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResponse:
    row = _get_upload(db, current_user.id, upload_id)
    if row.confirmed_at:
        raise HTTPException(status_code=409, detail="Confirmed upload is immutable.")
    parsed, errors = normalize_payload(payload.parsed)
    row.parsed_json = parsed.model_dump(mode="json")
    row.validation_errors = errors
    row.parsing_status = "waiting_confirmation"
    row.error_message = None
    db.commit()
    db.refresh(row)
    return _upload_response(row)


@router.post("/uploads/{upload_id}/confirm", response_model=SnapshotResponse, status_code=status.HTTP_201_CREATED)
def confirm_upload(
    upload_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SnapshotResponse:
    row = _get_upload(db, current_user.id, upload_id)
    existing = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.upload_id == row.id).first()
    if existing:
        return _snapshot_response(existing)
    if not row.parsed_json:
        raise HTTPException(status_code=409, detail="Upload has no parsed holdings.")
    parsed, errors = parse_payload_dict(row.parsed_json)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "Please correct holdings before confirmation.", "errors": errors})

    snapshot = PortfolioSnapshot(
        user_id=current_user.id,
        portfolio_id=row.portfolio_id,
        upload_id=row.id,
        source="screenshot",
        snapshot_time=datetime.now(UTC),
        total_assets=parsed.total_assets,
        total_market_value=parsed.total_market_value,
        broker_available_cash=parsed.broker_available_cash,
        corrected_unused_funds=parsed.corrected_unused_funds,
        repo_or_standard_bond_value=parsed.repo_or_standard_bond_value,
        status="confirmed",
        raw_json=parsed.model_dump(mode="json"),
    )
    db.add(snapshot)
    db.flush()
    for holding in parsed.holdings:
        qty = holding.qty
        available = holding.available_qty
        unavailable = max(qty - available, 0) if qty is not None and available is not None else None
        db.add(
            HoldingItem(
                snapshot_id=snapshot.id,
                code=holding.code or None,
                name=holding.name,
                market=holding.market,
                qty=qty,
                available_qty=available,
                unavailable_qty=unavailable,
                cost=holding.cost,
                screenshot_price=holding.price,
                market_value=holding.market_value,
                pnl_ratio=holding.pnl,
                pnl_amount=holding.pnl_amount,
                weight=holding.weight,
                extra_json=holding.extra,
            )
        )
    row.confirmed_at = datetime.now(UTC)
    row.parsing_status = "confirmed"
    db.commit()
    db.refresh(snapshot)
    return _snapshot_response(snapshot)


@router.get("/portfolios/{portfolio_id}/snapshots", response_model=list[SnapshotResponse])
def list_snapshots(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SnapshotResponse]:
    _get_portfolio(db, current_user.id, portfolio_id)
    rows = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.portfolio_id == portfolio_id, PortfolioSnapshot.user_id == current_user.id)
        .order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc())
        .all()
    )
    return [_snapshot_response(row) for row in rows]


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotResponse)
def get_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SnapshotResponse:
    return _snapshot_response(_get_snapshot(db, current_user.id, snapshot_id))
