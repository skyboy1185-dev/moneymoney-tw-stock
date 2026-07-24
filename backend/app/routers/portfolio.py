import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import WatchlistItem
from ..schemas import WatchlistCreate
from ..services.mock_market import find_stock, stock_payload

router = APIRouter(prefix="/watchlist", tags=["portfolio"])


def _user_id(x_user_id: str = Header(min_length=8, max_length=80)) -> str:
    return x_user_id


def _serialize(item: WatchlistItem) -> dict:
    payload = stock_payload(item.symbol)
    latest = payload["prices"][-1]["close"] if payload else item.added_price
    return {
        "id": item.id, "symbol": item.symbol, "name": item.name,
        "addedAt": item.added_at.isoformat(), "addedPrice": item.added_price,
        "latestPrice": latest,
        "returnPercent": round((latest - item.added_price) / item.added_price * 100, 2),
        "addedScore": item.added_score, "currentScore": item.added_score,
        "scoreChange": 0, "originalRobotId": item.original_robot_id,
        "originalRobotName": item.original_robot_name,
        "originalReasons": json.loads(item.original_reasons_json),
        "status": "剛加入觀察", "matchesOriginalStrategy": True,
        "invalidReasons": [], "updatedAt": datetime.now(UTC).isoformat(),
    }


@router.get("")
def list_watchlist(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(
        select(WatchlistItem).where(WatchlistItem.user_id == user_id).order_by(WatchlistItem.added_at.desc())
    ).all()
    return {"items": [_serialize(item) for item in items], "updatedAt": datetime.now(UTC).isoformat()}


@router.post("", status_code=201)
def add_watchlist(
    body: WatchlistCreate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    stock = find_stock(body.symbol)
    payload = stock_payload(body.symbol)
    if not stock or not payload:
        raise HTTPException(status_code=404, detail="找不到股票")
    item = WatchlistItem(
        user_id=user_id, symbol=stock["symbol"], name=stock["name"],
        added_at=datetime.now(UTC), added_price=payload["prices"][-1]["close"],
        added_score=body.added_score, original_robot_id=body.robot_id,
        original_robot_name=body.robot_name,
        original_reasons_json=json.dumps(body.reasons, ensure_ascii=False),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="股票已在自選清單") from error
    db.refresh(item)
    return _serialize(item)


@router.delete("/{symbol}", status_code=204)
def delete_watchlist(
    symbol: str,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> Response:
    item = db.scalar(select(WatchlistItem).where(
        WatchlistItem.user_id == user_id, WatchlistItem.symbol == symbol,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="自選項目不存在")
    db.delete(item)
    db.commit()
    return Response(status_code=204)
