from fastapi import APIRouter, HTTPException, Query

from ..services.mock_market import find_stock, stock_payload

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search")
def search_stocks(q: str = Query(min_length=1, max_length=40)) -> dict:
    stock = find_stock(q)
    return {"items": [] if stock is None else [{key: value for key, value in stock.items() if key != "base"}]}


@router.get("/{symbol}")
def get_stock(symbol: str) -> dict:
    payload = stock_payload(symbol)
    if payload is None:
        raise HTTPException(status_code=404, detail="找不到股票代號或名稱")
    return payload
