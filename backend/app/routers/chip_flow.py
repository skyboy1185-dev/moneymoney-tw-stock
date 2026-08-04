from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.chip_flow_alerts import electronic_chip_flow_alert_monitor
from ..services.chip_flow_repository import ChipFlowRepository
from ..services.chip_flow_service import chip_flow_service


router = APIRouter(prefix="/stocks", tags=["chip-flow"])


@router.get("/chip-flow/electronic-alerts")
def get_electronic_chip_flow_alerts(
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return electronic_chip_flow_alert_monitor.payload(ChipFlowRepository(db))


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
