"""Authenticated, typed instrument market APIs; provider routing stays below."""
from __future__ import annotations

from datetime import date
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..market.instrument_schemas import (
    MAX_BAR_LIMIT, Adjustment, BarInterval, BatchQuoteRequest, BatchQuoteResponse,
    InstrumentBarsResponse, InstrumentCapitalFlowResponse, InstrumentMarketSnapshotResponse,
    InstrumentMetadataResponse, InstrumentOrderBookResponse, InstrumentQuoteResponse,
)
from ..market.instruments import InstrumentLookupError, InstrumentMarketService
from ..v2_dependencies import get_current_user


router = APIRouter(
    prefix="/api/v3/market/instruments", tags=["v3-instrument-market"],
    dependencies=[Depends(get_current_user)],
)


def instrument_service(db: Session = Depends(get_db)) -> InstrumentMarketService:
    return InstrumentMarketService(db)


def _call(operation: Callable, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except InstrumentLookupError as exc:
        raise HTTPException(status_code=exc.http_status, detail={"code": exc.code}) from exc


@router.post("/quotes", response_model=BatchQuoteResponse)
def batch_quotes(body: BatchQuoteRequest, service: InstrumentMarketService = Depends(instrument_service)):
    return _call(service.batch_quotes, body.codes)


@router.get("/{code}", response_model=InstrumentMetadataResponse)
def metadata(code: str, service: InstrumentMarketService = Depends(instrument_service)):
    return _call(service.metadata, code)


@router.get("/{code}/quote", response_model=InstrumentQuoteResponse)
def quote(code: str, service: InstrumentMarketService = Depends(instrument_service)):
    return _call(service.quote, code)


@router.get("/{code}/bars", response_model=InstrumentBarsResponse)
def bars(
    code: str, interval: BarInterval = "1d", adjustment: Adjustment = "none",
    start: date | None = None, end: date | None = None,
    limit: int = Query(default=250, ge=1, le=MAX_BAR_LIMIT),
    service: InstrumentMarketService = Depends(instrument_service),
):
    return _call(service.bars, code, interval=interval, start=start, end=end, limit=limit, adjustment=adjustment)


@router.get("/{code}/order-book", response_model=InstrumentOrderBookResponse)
def order_book(code: str, service: InstrumentMarketService = Depends(instrument_service)):
    return _call(service.order_book, code)


@router.get("/{code}/capital-flow", response_model=InstrumentCapitalFlowResponse)
def capital_flow(code: str, service: InstrumentMarketService = Depends(instrument_service)):
    return _call(service.capital_flow, code)


@router.get("/{code}/snapshot", response_model=InstrumentMarketSnapshotResponse)
def snapshot(code: str, service: InstrumentMarketService = Depends(instrument_service)):
    return _call(service.snapshot, code)
