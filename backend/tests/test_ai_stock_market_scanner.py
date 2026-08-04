import asyncio
from datetime import UTC, datetime

import httpx

from app.services.ai_stock_market_scanner import (
    AIStockMarketScanner,
    normalize_market_snapshot,
)


NOW = datetime(2026, 7, 31, 9, 30, tzinfo=UTC).astimezone()


def _featured_row(**changes: object) -> dict:
    row = {
        "signalId": "ai-20260731-2330-breakout",
        "symbol": "2330",
        "name": "台積電",
        "market": "上市",
        "industry": "半導體",
        "strategyName": "盤整突破 Bot",
        "secondaryStrategies": ["波段起漲 Bot"],
        "score": 88,
        "strategyFit": 84,
        "marketFit": 76,
        "healthScore": 82,
        "price": 100,
        "entryMin": 99,
        "entryMax": 101,
        "stopLoss": 95,
        "target1": 110,
        "target2": 118,
        "riskRewardRatio": 2,
        "reasons": ["站上關鍵價", "成交量增加", "趨勢轉強"],
        "riskTags": [],
        "hardRiskFailures": [],
        "priceSource": "TWSE MIS",
        "priceDate": "2026-07-31",
        "priceTime": "17:30:00",
        "quoteFresh": True,
        "isOfficialPrice": True,
        "volumeQualified": True,
        "liquidityQualified": True,
    }
    row.update(changes)
    return row


def test_closed_market_snapshot_never_creates_candidates() -> None:
    result = normalize_market_snapshot({
        "marketOpen": False,
        "marketStatus": "休市",
        "featured": [_featured_row()],
    }, NOW)

    assert result.status == "market_closed"
    assert result.items == ()
    assert result.market_status == "休市"


def test_snapshot_maps_only_formal_official_fresh_candidates() -> None:
    result = normalize_market_snapshot({
        "marketOpen": True,
        "marketStatus": "台股盤中",
        "featured": [
            _featured_row(),
            _featured_row(symbol="2317", signalId="ai-20260731-2317-breakout", quoteFresh=False),
            _featured_row(symbol="2382", signalId="ai-20260731-2382-breakout", score=74),
        ],
    }, NOW)

    assert result.status == "success"
    assert result.featured_count == 3
    assert len(result.items) == 1
    candidate = result.items[0]
    assert candidate.symbol == "2330"
    assert candidate.stock_name == "台積電"
    assert candidate.quote_source == "TWSE MIS"
    assert float(candidate.total_score) == 88


def test_scanner_fetches_live_endpoint_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ai"
        assert request.url.params["refresh"] == "1"
        assert request.headers["x-adaptive-scanner-token"] == "scanner-secret"
        return httpx.Response(200, json={
            "marketOpen": True,
            "marketStatus": "台股盤中",
            "featured": [_featured_row()],
        })

    scanner = AIStockMarketScanner(
        "https://frontend.example/api/ai?refresh=1",
        service_token="scanner-secret",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(scanner.scan(NOW))

    assert result.status == "success"
    assert len(result.items) == 1


def test_scanner_failure_is_reported_without_fake_candidates() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    scanner = AIStockMarketScanner(
        "https://frontend.example/api/ai?refresh=1",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(scanner.scan(NOW))

    assert result.status == "error"
    assert result.items == ()
    assert result.error is not None
