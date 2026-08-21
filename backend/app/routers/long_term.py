from typing import Literal

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.long_term_automation import long_term_selection_automation
from ..services.adaptive_electronic_automation import fetch_adaptive_scan_payload
from ..services.long_term_benchmarks import stored_stock_directory
from ..services.popular_stock_universe import OfficialPopularStockProvider
from ..services.long_term_selection import (
    list_long_term_trade_events,
    mark_long_term_trade_event_read,
    portfolio_payload,
    replace_long_term_position,
)
from ..services.long_term_backtest import (
    persisted_ytd_backtest_payload,
    ytd_backtest_payload,
)


router = APIRouter(prefix="/long-term", tags=["long-term-selection"])


@router.get("/status")
def status() -> dict[str, object]:
    return long_term_selection_automation.state


@router.get("/portfolio")
async def portfolio(
    mode: Literal["long_only", "focused_long"] = Query(default="long_only"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return await portfolio_payload(db, mode)


@router.get("/backtest/ytd")
async def backtest_ytd(
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not force and (persisted := persisted_ytd_backtest_payload(db)) is not None:
        return persisted
    try:
        payload = await fetch_adaptive_scan_payload()
    except Exception:
        popular = await OfficialPopularStockProvider().fetch()
        fallback_requests: list[tuple[str, str, str]] = [
            (item.symbol, item.name, item.market)
            for item in popular
        ]
        known_symbols = {symbol for symbol, _, _ in fallback_requests}
        for symbol, metadata in stored_stock_directory(db).items():
            if symbol in known_symbols:
                continue
            fallback_requests.append((
                symbol,
                str(metadata.get("name") or symbol),
                str(metadata.get("market") or "上市"),
            ))
        try:
            return await ytd_backtest_payload(
                db,
                force=force,
                fallback_requests=fallback_requests,
                universe_source="股票池來自證交所／櫃買中心成交熱門排行備援名單",
            )
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    try:
        return await ytd_backtest_payload(db, payload.stocks, force=force)
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/events")
def trade_events(
    mode: Literal["long_only", "focused_long"] = Query(default="long_only"),
    after_id: int = Query(default=0, ge=0, alias="afterId"),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return {"items": list_long_term_trade_events(db, mode, after_id, limit)}


@router.post("/events/{event_id}/read")
def mark_event_read(event_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    if not mark_long_term_trade_event_read(db, event_id, datetime.now(UTC)):
        raise HTTPException(status_code=404, detail="找不到長線交易訊息")
    return {"status": "read", "eventId": event_id}


@router.post("/positions/{position_id}/replace")
async def replace_position(position_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    payload = await fetch_adaptive_scan_payload()
    try:
        return replace_long_term_position(db, position_id, payload, datetime.now(UTC))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
