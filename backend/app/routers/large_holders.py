from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import LargeHolderMonitor, WatchlistItem
from ..schemas import LargeHolderMonitorCreate
from ..services.large_holders import (
    fetch_latest_distribution_bundle,
    get_large_holder_history,
    get_large_holder_rankings,
    persist_latest_distribution,
)
from ..services.official_market_data import StockQuoteRequest, official_market_data_provider

router = APIRouter(prefix="/large-holders", tags=["large-holders"])


def _user_id_optional(x_user_id: str | None = Header(default=None, max_length=80)) -> str | None:
    return x_user_id if x_user_id and len(x_user_id) >= 8 else None


def _user_id(x_user_id: str = Header(min_length=8, max_length=80)) -> str:
    return x_user_id


@router.get("/rankings")
async def rankings(
    type: str = Query(default="over400", pattern=r"^(over400|over1000)$"),
    limit: int = Query(default=20, ge=1, le=20),
    market: str = Query(default="all", pattern=r"^(all|listed|otc)$"),
    industry: str = Query(default="", max_length=80),
    keyword: str = Query(default="", max_length=80),
    minAverageTurnover: float = Query(default=30_000_000, ge=0),
    excludeEtf: bool = Query(default=True),
    reportDate: str = Query(default="", max_length=10),
    sortBy: str = Query(default="changePoint", pattern=r"^(changePoint)$"),
    sortOrder: str = Query(default="desc", pattern=r"^(desc)$"),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    # Reserved parameters are validated now so clients keep a stable API as the
    # PostgreSQL official-history store grows.
    _ = (excludeEtf, reportDate, sortBy, sortOrder)
    sync_result = None
    if refresh:
        try:
            rows, directory = await fetch_latest_distribution_bundle()
            sync_result = persist_latest_distribution(db, rows, directory)
        except (httpx.HTTPError, ValueError) as error:
            sync_result = {"status": "failed", "message": str(error)}
    payload = get_large_holder_rankings(
        db, type, limit, market, industry.strip(), keyword.strip(), minAverageTurnover,
    )
    if payload.get("dataMode") == "official_tdcc":
        requests = [
            StockQuoteRequest(item["stockCode"], item["stockName"], item["market"])
            for item in payload["items"]
        ]
        quotes = await official_market_data_provider.get_quotes(requests)
        for item in payload["items"]:
            quote = quotes.get(item["stockCode"])
            if quote is None:
                continue
            item["latestPrice"] = quote.price
            item["quoteSource"] = quote.source
            item["quoteTimestamp"] = quote.quote_timestamp
    if sync_result is not None:
        payload["syncResult"] = sync_result
    return payload


@router.get("/stocks/{stock_code}/history")
def stock_history(
    stock_code: str,
    weeks: int = Query(default=12, ge=2, le=52),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return get_large_holder_history(db, stock_code, weeks)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="找不到此股票的大戶持股歷史") from error


@router.post("/sync")
async def sync_tdcc(db: Session = Depends(get_db)) -> dict:
    try:
        rows, directory = await fetch_latest_distribution_bundle()
        result = persist_latest_distribution(db, rows, directory)
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="TDCC 官方資料暫時無法連線") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        **result,
        "source": "臺灣集中保管結算所 OpenAPI",
        "sourceUrl": "https://openapi.tdcc.com.tw/v1/opendata/1-5",
        "updatedAt": datetime.now(UTC).isoformat(),
    }


@router.get("/monitors")
def list_monitors(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    items = db.scalars(select(LargeHolderMonitor).where(
        LargeHolderMonitor.user_id == user_id,
        LargeHolderMonitor.active.is_(True),
    ).order_by(LargeHolderMonitor.added_at.desc())).all()
    return {
        "items": [{
            "id": item.id, "stockCode": item.stock_code, "stockName": item.stock_name,
            "type": item.monitor_type, "lineEnabled": item.line_enabled,
            "addedAt": item.added_at.isoformat(),
        } for item in items],
        "updatedAt": datetime.now(UTC).isoformat(),
    }


@router.post("/monitors", status_code=201)
def add_monitor(
    body: LargeHolderMonitorCreate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    now = datetime.now(UTC)
    if body.action == "watchlist":
        existing = db.scalar(select(WatchlistItem).where(
            WatchlistItem.user_id == user_id, WatchlistItem.symbol == body.stock_code,
        ))
        if existing:
            return {"status": "duplicate", "message": "股票已在自選清單"}
        db.add(WatchlistItem(
            user_id=user_id, symbol=body.stock_code, name=body.stock_name,
            added_at=now, added_price=body.current_price or 1, added_score=0,
            original_robot_id="large_holder_ranking",
            original_robot_name="大戶持股增加榜",
            original_reasons_json='["大戶持股比例週增榜加入"]',
        ))
        db.commit()
        return {"status": "created", "message": "已加入自選觀察"}

    item = db.scalar(select(LargeHolderMonitor).where(
        LargeHolderMonitor.user_id == user_id,
        LargeHolderMonitor.stock_code == body.stock_code,
    ))
    if item:
        item.active = True
        item.monitor_type = body.monitor_type
        item.line_enabled = item.line_enabled or body.action == "line"
        item.updated_at = now
        status = "updated"
    else:
        item = LargeHolderMonitor(
            user_id=user_id, stock_code=body.stock_code, stock_name=body.stock_name,
            monitor_type=body.monitor_type, line_enabled=body.action == "line",
            active=True, added_at=now, updated_at=now,
        )
        db.add(item)
        status = "created"
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="股票已在大戶 AI 觀察清單") from error
    db.refresh(item)
    return {
        "status": status, "id": item.id, "lineEnabled": item.line_enabled,
        "message": "LINE 大戶籌碼通知已開啟" if item.line_enabled else "已加入 AI 觀察",
    }


@router.delete("/monitors/{stock_code}", status_code=204)
def remove_monitor(
    stock_code: str,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> Response:
    item = db.scalar(select(LargeHolderMonitor).where(
        LargeHolderMonitor.user_id == user_id,
        LargeHolderMonitor.stock_code == stock_code,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="AI 觀察項目不存在")
    item.active = False
    item.updated_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)
