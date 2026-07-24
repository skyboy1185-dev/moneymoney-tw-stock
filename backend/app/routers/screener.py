from fastapi import APIRouter, Query

from ..services.mock_market import screener_rows

router = APIRouter(prefix="/screener", tags=["screener"])


@router.get("")
def run_screener(
    strategy: str = Query(default="macd_entry", pattern=r"^(macd_entry|macd_exit|above_ma20|bullish_alignment|all)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    rows = screener_rows(strategy)
    start = (page - 1) * page_size
    return {
        "items": rows[start:start + page_size],
        "total": len(rows),
        "page": page,
        "pageSize": page_size,
        "strategy": strategy,
        "dataMode": "demo",
        "message": "展示模式／模擬資料",
    }
