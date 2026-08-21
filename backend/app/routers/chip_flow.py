from datetime import date
import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.chip_flow_alerts import electronic_chip_flow_alert_monitor
from ..services.chip_flow_service import chip_flow_service


router = APIRouter(prefix="/stocks", tags=["chip-flow"])


@router.get("/chip-flow/electronic-alerts")
async def get_electronic_chip_flow_alerts(
    pinned: str | None = Query(default=None, max_length=160),
    tracking: str | None = Query(default=None, max_length=160),
    client_id: str | None = Query(default=None, alias="clientId", max_length=64),
) -> dict[str, object]:
    pinned_symbols = None
    if pinned is not None:
        pinned_symbols = tuple(dict.fromkeys(
            symbol for symbol in pinned.split(",")
            if symbol.isdigit() and len(symbol) == 4
        ))[:20]
    tracking_symbols = None
    if tracking is not None:
        tracking_symbols = tuple(dict.fromkeys(
            symbol for symbol in tracking.split(",")
            if symbol.isdigit() and len(symbol) == 4
        ))[:20]
    return electronic_chip_flow_alert_monitor.payload(
        pinned_symbols=pinned_symbols,
        tracking_symbols=tracking_symbols,
        client_id=(
            client_id
            if client_id is not None and re.fullmatch(r"[A-Za-z0-9_-]{8,64}", client_id)
            else "legacy"
        ),
    )


@router.get("/{stock_id}/chip-flow/intraday")
async def get_intraday_chip_flow(
    stock_id: str,
    trade_date: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not stock_id.isdigit() or not 4 <= len(stock_id) <= 6:
        return {
            "stockId": stock_id,
            "status": "invalid_symbol",
            "series": [],
            "latest": None,
            "statusMessage": "股票代號格式不正確。",
        }
    return await chip_flow_service.get_intraday(stock_id, db, trade_date)
