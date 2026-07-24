from fastapi import APIRouter, Query

from ..services.mock_market import NEWS, industry_hotspots

router = APIRouter(tags=["content"])


@router.get("/industries/hotspots")
def get_industry_hotspots() -> dict:
    return {
        "items": industry_hotspots(),
        "updatedAt": "2026-07-24T13:30:00+08:00",
        "dataMode": "demo",
    }


@router.get("/news")
def get_news(
    category: str = Query(default=""),
    keyword: str = Query(default="", max_length=80),
) -> dict:
    normalized = keyword.strip().lower()
    items = [
        item for item in NEWS
        if (not category or item["category"] == category)
        and (not normalized or normalized in item["title"].lower() or normalized in item["summary"].lower()
             or any(normalized in symbol for symbol in item["symbols"]))
    ]
    return {
        "items": items,
        "categories": sorted({item["category"] for item in NEWS}),
        "dataMode": "demo",
        "message": "新聞為展示資料，不代表即時新聞。",
    }
