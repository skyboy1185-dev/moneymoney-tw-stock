import math
import random
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from .indicators import calculate_indicators


STOCKS = [
    {"symbol": "2330", "name": "台積電", "industry": "半導體", "market": "上市", "base": 1125, "peRatio": 24.8, "dividendYield": 1.72, "priceToBook": 7.2, "eps": 46.3, "marketCap": 28.9e12},
    {"symbol": "2317", "name": "鴻海", "industry": "電子零組件", "market": "上市", "base": 181, "peRatio": 13.2, "dividendYield": 3.1, "priceToBook": 1.55, "eps": 12.4, "marketCap": 2.54e12},
    {"symbol": "2454", "name": "聯發科", "industry": "半導體", "market": "上市", "base": 1430, "peRatio": 21.5, "dividendYield": 3.55, "priceToBook": 5.1, "eps": 68.2, "marketCap": 2.31e12},
    {"symbol": "2308", "name": "台達電", "industry": "電子零組件", "market": "上市", "base": 468, "peRatio": 31.7, "dividendYield": 1.62, "priceToBook": 6.3, "eps": 15.8, "marketCap": 1.24e12},
    {"symbol": "2881", "name": "富邦金", "industry": "金融保險", "market": "上市", "base": 91.6, "peRatio": 11.3, "dividendYield": 4.18, "priceToBook": 1.31, "eps": 8.5, "marketCap": 1.18e12},
    {"symbol": "2882", "name": "國泰金", "industry": "金融保險", "market": "上市", "base": 68.5, "peRatio": 10.8, "dividendYield": 3.82, "priceToBook": 1.22, "eps": 6.1, "marketCap": 0.94e12},
    {"symbol": "2382", "name": "廣達", "industry": "電腦及週邊", "market": "上市", "base": 292, "peRatio": 19.6, "dividendYield": 4.02, "priceToBook": 4.8, "eps": 16.7, "marketCap": 1.13e12},
    {"symbol": "3008", "name": "大立光", "industry": "光電", "market": "上市", "base": 2510, "peRatio": 16.4, "dividendYield": 3.2, "priceToBook": 3.1, "eps": 182.5, "marketCap": 0.34e12},
    {"symbol": "5274", "name": "信驊", "industry": "半導體", "market": "上櫃", "base": 5220, "peRatio": 49.2, "dividendYield": 0.91, "priceToBook": 17.4, "eps": 91.8, "marketCap": 0.215e12},
    {"symbol": "6488", "name": "環球晶", "industry": "半導體", "market": "上櫃", "base": 437, "peRatio": 18.9, "dividendYield": 3.65, "priceToBook": 2.7, "eps": 26.4, "marketCap": 0.206e12},
    {"symbol": "8069", "name": "元太", "industry": "光電", "market": "上櫃", "base": 246, "peRatio": 29.7, "dividendYield": 1.83, "priceToBook": 5.2, "eps": 8.6, "marketCap": 0.282e12},
    {"symbol": "6669", "name": "緯穎", "industry": "電腦及週邊", "market": "上市", "base": 2830, "peRatio": 22.3, "dividendYield": 2.31, "priceToBook": 7.8, "eps": 118.6, "marketCap": 0.495e12},
    {"symbol": "1301", "name": "台塑", "industry": "塑膠工業", "market": "上市", "base": 48.6, "peRatio": 38.1, "dividendYield": 2.52, "priceToBook": 1.08, "eps": 1.2, "marketCap": 0.31e12},
    {"symbol": "2603", "name": "長榮", "industry": "航運業", "market": "上市", "base": 196, "peRatio": 6.8, "dividendYield": 8.44, "priceToBook": 1.42, "eps": 32.8, "marketCap": 0.42e12},
]

NEWS = [
    {"id": "n1", "title": "晶圓代工與先進封裝供應鏈維持高能見度", "summary": "AI 伺服器需求帶動先進製程與封裝相關供應鏈關注度。", "category": "半導體", "symbols": ["2330", "2454"], "source": "展示新聞中心", "publishedAt": "2026-07-24T12:20:00+08:00", "sentiment": "positive"},
    {"id": "n2", "title": "伺服器供應鏈觀察出貨與匯率變化", "summary": "市場關注下半年伺服器出貨節奏、零組件供應與匯率影響。", "category": "電腦及週邊", "symbols": ["2317", "2382", "6669"], "source": "展示新聞中心", "publishedAt": "2026-07-24T11:05:00+08:00", "sentiment": "neutral"},
    {"id": "n3", "title": "金融股除息行情與資產品質成焦點", "summary": "投資人持續評估股利政策、利差與資產品質表現。", "category": "金融保險", "symbols": ["2881", "2882"], "source": "展示新聞中心", "publishedAt": "2026-07-24T09:40:00+08:00", "sentiment": "neutral"},
    {"id": "n4", "title": "航運報價波動，市場關注旺季需求", "summary": "運價與供需變化使航運族群波動放大，需留意風險。", "category": "航運業", "symbols": ["2603"], "source": "展示新聞中心", "publishedAt": "2026-07-23T16:10:00+08:00", "sentiment": "negative"},
    {"id": "n5", "title": "電子紙應用擴張帶動光電族群話題", "summary": "零售、物流與低耗能顯示應用持續擴張。", "category": "光電", "symbols": ["8069", "3008"], "source": "展示新聞中心", "publishedAt": "2026-07-23T14:35:00+08:00", "sentiment": "positive"},
]


def _business_dates(count: int) -> list[str]:
    cursor = date(2026, 7, 24)
    result: list[str] = []
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(result))


def find_stock(query: str) -> dict | None:
    normalized = query.strip().lower()
    return next(
        (item for item in STOCKS if item["symbol"] == normalized or item["name"].lower() == normalized),
        next((item for item in STOCKS if normalized in item["symbol"] or normalized in item["name"].lower()), None),
    )


@lru_cache(maxsize=32)
def stock_payload(symbol: str) -> dict | None:
    stock = find_stock(symbol)
    if not stock:
        return None
    rng = random.Random(int(stock["symbol"]) * 97)
    dates = _business_dates(420)
    price = float(stock["base"]) * 0.72
    candles = []
    for index, trade_date in enumerate(dates):
        movement = 0.00055 + math.sin(index / 23) * 0.0025 + (rng.random() - 0.5) * 0.028
        opening = price * (1 + (rng.random() - 0.5) * 0.01)
        price = max(5, price * (1 + movement))
        high = max(opening, price) * (1 + rng.random() * 0.012)
        low = min(opening, price) * (1 - rng.random() * 0.012)
        candles.append({
            "symbol": stock["symbol"], "name": stock["name"], "date": trade_date,
            "open": round(opening, 2), "high": round(high, 2), "low": round(low, 2),
            "close": round(price, 2), "volume": round(1_500_000 + rng.random() * 32_000_000),
        })
    scale = float(stock["base"]) / candles[-1]["close"]
    for candle in candles:
        for field in ("open", "high", "low", "close"):
            candle[field] = round(candle[field] * scale, 2)
    meta = {key: value for key, value in stock.items() if key != "base"}
    return {
        "meta": meta,
        "prices": candles,
        "indicators": calculate_indicators(candles),
        "updatedAt": datetime.now(UTC).isoformat(),
        "dataMode": "demo",
        "dataNotice": "展示模式／模擬資料；可透過 MarketDataProvider 更換正式台股資料來源。",
    }


def screener_rows(strategy: str = "macd_entry") -> list[dict]:
    rows = []
    for stock in STOCKS:
        payload = stock_payload(stock["symbol"])
        if not payload:
            continue
        latest = payload["prices"][-1]
        previous = payload["prices"][-2]
        indicator = payload["indicators"][-1]
        matched = {
            "macd_entry": indicator["macdSignal"] == "entry",
            "macd_exit": indicator["macdSignal"] == "exit",
            "above_ma20": indicator["ma20"] is not None and latest["close"] > indicator["ma20"],
            "bullish_alignment": all(indicator[key] is not None for key in ("ma5", "ma20", "ma60"))
            and indicator["ma5"] > indicator["ma20"] > indicator["ma60"],
        }.get(strategy, True)
        if matched:
            rows.append({
                "symbol": stock["symbol"], "name": stock["name"], "industry": stock["industry"],
                "market": stock["market"], "price": latest["close"],
                "changePercent": round((latest["close"] - previous["close"]) / previous["close"] * 100, 2),
                "volume": latest["volume"], "ma5": indicator["ma5"], "ma20": indicator["ma20"],
                "ma60": indicator["ma60"], "dif": indicator["dif"], "signal": indicator["signal"],
                "histogram": indicator["histogram"], "latestSignal": indicator["macdSignal"],
                "signalDate": latest["date"], "flags": [strategy],
            })
    return sorted(rows, key=lambda item: item["changePercent"], reverse=True)


def industry_hotspots() -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for stock in STOCKS:
        payload = stock_payload(stock["symbol"])
        if not payload:
            continue
        latest, previous = payload["prices"][-1], payload["prices"][-2]
        change = (latest["close"] - previous["close"]) / previous["close"] * 100
        groups.setdefault(stock["industry"], []).append({
            "symbol": stock["symbol"], "name": stock["name"], "changePercent": round(change, 2),
        })
    result = []
    for industry, members in groups.items():
        average = sum(item["changePercent"] for item in members) / len(members)
        result.append({
            "industry": industry,
            "changePercent": round(average, 2),
            "momentum": round(min(100, max(0, 50 + average * 12)), 0),
            "stockCount": len(members),
            "leaders": sorted(members, key=lambda item: item["changePercent"], reverse=True)[:3],
            "status": "強勢" if average >= 1 else "偏多" if average > 0 else "整理" if average > -1 else "偏弱",
        })
    return sorted(result, key=lambda item: item["changePercent"], reverse=True)
