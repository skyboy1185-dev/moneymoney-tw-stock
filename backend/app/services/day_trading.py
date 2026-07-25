import math
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from .official_market_data import OfficialStockQuote


DISCLAIMER = "僅供研究參考，不構成投資建議。所有交易均須由使用者自行確認。"
DATA_NOTICE = "展示模式，非即時行情"


def long_signal_score(metrics: dict[str, float | bool]) -> int:
    weights = {
        "vwap_up": 15, "above_vwap": 15, "breakout": 15, "volume": 10,
        "active_buy": 15, "large_buy": 10, "short_trend": 10,
        "market_fit": 5, "industry_fit": 5,
    }
    return min(100, round(sum(weight for key, weight in weights.items() if metrics.get(key))))


def short_signal_score(metrics: dict[str, float | bool]) -> int:
    weights = {
        "vwap_down": 15, "below_vwap": 15, "breakdown": 15, "volume": 10,
        "active_sell": 15, "large_sell": 10, "short_trend": 10,
        "market_fit": 5, "industry_fit": 5,
    }
    return min(100, round(sum(weight for key, weight in weights.items() if metrics.get(key))))


def is_signal_expired(expires_at: datetime, now: datetime | None = None) -> bool:
    return expires_at <= (now or datetime.now(UTC))


def entry_allowed(data_delay_seconds: float, daily_loss_reached: bool, consecutive_losses: int, limit: int) -> bool:
    return data_delay_seconds <= 8 and not daily_loss_reached and consecutive_losses < limit


def evaluate_position(
    direction: str,
    price: float,
    stop_loss: float,
    target_1: float,
    target_2: float,
    trailing_stop: float | None = None,
    data_status: str = "normal",
) -> dict[str, str]:
    if data_status in {"severe_delay", "disconnected", "source_error"}:
        return {"level": "emergency", "action": "禁止依賴舊價，立即人工確認", "reason": "行情資料異常"}
    if direction == "long":
        if price <= stop_loss:
            return {"level": "emergency", "action": "立即全部賣出", "reason": "跌破停損價"}
        if trailing_stop is not None and price <= trailing_stop:
            return {"level": "important", "action": "全部賣出", "reason": "觸發移動停利"}
        if price >= target_2:
            return {"level": "important", "action": "全部賣出", "reason": "到達第二停利價"}
        if price >= target_1:
            return {"level": "important", "action": "減碼 50%", "reason": "到達第一停利價"}
        return {"level": "normal", "action": "續抱多單", "reason": "原始條件仍有效"}
    if price >= stop_loss:
        return {"level": "emergency", "action": "立即全部回補", "reason": "突破停損價"}
    if trailing_stop is not None and price >= trailing_stop:
        return {"level": "important", "action": "全部回補", "reason": "觸發移動停利"}
    if price <= target_2:
        return {"level": "important", "action": "全部回補", "reason": "到達第二回補價"}
    if price <= target_1:
        return {"level": "important", "action": "回補 50%", "reason": "到達第一回補價"}
    return {"level": "normal", "action": "續抱空單", "reason": "原始條件仍有效"}


EVENT_PRIORITY = {
    "emergency_exit": 0,
    "exit_warning": 1,
    "data_disconnected": 2,
    "data_delay": 3,
    "position_update": 4,
    "signal_expired": 5,
    "new_signal": 6,
    "signal_update": 7,
    "quote_update": 8,
    "market_update": 9,
}


def prioritize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda item: EVENT_PRIORITY.get(str(item.get("type")), 99))


class MockDayTradingEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tick = 0
        self._scenario: str | None = None
        self._emitted_keys: set[str] = set()
        self._official_quotes: dict[str, OfficialStockQuote] = {}

    def trigger(self, scenario: str) -> None:
        with self._lock:
            self._scenario = scenario

    def update_official_quotes(self, quotes: dict[str, OfficialStockQuote]) -> None:
        if not quotes:
            return
        with self._lock:
            self._official_quotes.update(quotes)

    def apply_official_quotes(self, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            quotes = dict(self._official_quotes)
        for item in signals:
            quote = quotes.get(str(item["symbol"]))
            if quote is None:
                continue
            mock_price = float(item.get("price") or 0)
            ratio = quote.price / mock_price if mock_price > 0 else 1
            for key in ("entryMin", "entryMax", "stopLoss", "target1", "target2"):
                value = item.get(key)
                if isinstance(value, (int, float)):
                    item[key] = round(float(value) * ratio, 2)
            item.update({
                "stockName": quote.name,
                "price": round(quote.price, 2),
                "previousClose": round(quote.previous_close, 2),
                "open": round(quote.open, 2),
                "high": round(quote.high, 2),
                "low": round(quote.low, 2),
                "change": round(quote.change, 2),
                "changePercent": round(quote.change_percent, 2),
                "volume": quote.volume,
                "turnover": round(quote.price * quote.volume),
                "quoteTimestamp": quote.quote_timestamp,
                "dataSource": quote.source,
                "dataMode": "official_quote_demo_strategy",
                "dataNotice": (
                    "價格、漲跌與成交量取自 TWSE MIS；策略分數、進出場區間與技術條件仍為展示計算。"
                ),
                "quoteIsRealtime": quote.is_realtime,
                "quoteStatus": "盤中行情" if quote.is_realtime else "最近有效行情／收盤",
            })
            warnings = list(item.get("warnings", []))
            strategy_notice = "策略條件仍為展示計算，不可直接作為交易依據"
            if strategy_notice not in warnings:
                item["warnings"] = [strategy_notice, *warnings]
        return signals

    def _now(self) -> datetime:
        return datetime.now(UTC)

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._tick

    def market_regime(self) -> dict[str, Any]:
        now = self._now()
        with self._lock:
            scenario = self._scenario
            has_official_quotes = bool(self._official_quotes)
        data_status = "normal"
        delay = 1.2
        direction = "bull"
        score = 72
        if scenario == "data_delay":
            data_status, delay, direction, score = "severe_delay", 12.0, "data_anomaly", 0
        elif scenario == "disconnect":
            data_status, delay, direction, score = "disconnected", 30.0, "data_anomaly", 0
        return {
            "direction": direction,
            "directionLabel": "偏多" if direction == "bull" else "資料異常",
            "score": score,
            "environmentScore": 78 if data_status == "normal" else 0,
            "environmentLabel": "適合交易" if data_status == "normal" else "停止新訊號",
            "preferredDirection": "做多",
            "shortRestriction": "只允許高信心弱勢股",
            "risk": "中",
            "longPermission": 85,
            "shortPermission": 45,
            "suitableStrategies": ["突破買進", "回踩買進", "弱勢股跌破放空"],
            "forbiddenStrategies": ["無量追價", "急跌追空"],
            "reasons": ["指數站上 VWAP", "上漲家數高於下跌家數", "大單買盤增加", "5 分 K 均線偏多"],
            "dataStatus": data_status,
            "dataDelaySeconds": delay,
            "dataSource": (
                "TWSE MIS 個股報價＋Mock 大盤策略"
                if has_official_quotes
                else "Mock Streaming Data"
            ),
            "marketOpen": True,
            "session": "09:00～13:30",
            "updatedAt": now.isoformat(),
            "metrics": {
                "weightedIndex": 28742.3, "otcIndex": 312.8, "indexFutures": 28761.0,
                "vwap": 28692.5, "oneMinuteTrend": "向上", "fiveMinuteTrend": "偏多",
                "fifteenMinuteTrend": "震盪偏多", "advancers": 612, "decliners": 403,
                "limitUp": 18, "limitDown": 3, "largeOrderForce": 68, "smallOrderForce": 41,
                "relativeVolume": 1.18, "strongIndustries": ["半導體", "電腦及週邊"],
                "weakIndustries": ["塑膠", "航運"], "breadth": 60.3, "volatility": "中等",
            },
        }

    def signals(self) -> list[dict[str, Any]]:
        with self._lock:
            self._tick += 1
            tick = self._tick
            scenario = self._scenario
        now = self._now()
        wave = math.sin(tick / 4)
        templates = [
            {
                "id": "mock-long-6669", "symbol": "6669", "stockName": "緯穎", "market": "上市",
                "direction": "long", "directionLabel": "做多", "action": "回踩買進",
                "price": 5730 + wave * 8, "entryMin": 5700, "entryMax": 5730, "stopLoss": 5640,
                "target1": 5820, "target2": 5900, "confidenceScore": 84, "healthScore": 81,
                "riskRewardRatio": 2.1, "changePercent": 2.35, "volume": 1_842_000,
                "turnover": 10_548_000_000, "vwapStatus": "站上且向上", "volumeStatus": "量能增加",
                "largeOrderForce": 76, "industryStrength": "強勢",
                "spreadPercentage": 0.12, "tradingEligible": True, "shortEligible": False,
                "shortAvailabilityKnown": True, "tradeRestricted": False, "nearLimitDown": False,
                "excessiveNegativeDeviation": False, "chaseBlocked": False,
                "stopDistancePercent": 1.57, "marketAlignment": 92, "confirmationScore": 88,
                "volumeScore": 82, "activeForce": 78, "industryScore": 85, "liquidityScore": 90,
                "reasons": ["站上 VWAP", "突破早盤高點", "5 分 K 均線向上", "成交量高於同期平均", "大盤偏多"],
                "warnings": ["距離 VWAP 稍遠", "短線漲幅較大", "不建議直接追價"],
            },
            {
                "id": "mock-short-2317", "symbol": "2317", "stockName": "鴻海", "market": "上市",
                "direction": "short", "directionLabel": "放空", "action": "跌破 177.5 放空",
                "price": 177.8 - wave * 0.25, "entryMin": 177.2, "entryMax": 177.5, "stopLoss": 179.0,
                "target1": 175.0, "target2": 172.5, "confidenceScore": 82, "healthScore": 79,
                "riskRewardRatio": 2.3, "changePercent": -1.48, "volume": 38_620_000,
                "turnover": 6_874_000_000, "vwapStatus": "跌破且向下", "volumeStatus": "賣量增加",
                "largeOrderForce": -72, "industryStrength": "轉弱",
                "spreadPercentage": 0.18, "tradingEligible": True, "shortEligible": True,
                "shortAvailabilityKnown": True, "tradeRestricted": False, "nearLimitDown": False,
                "excessiveNegativeDeviation": False, "chaseBlocked": False,
                "stopDistancePercent": 0.84, "marketAlignment": 55, "confirmationScore": 86,
                "volumeScore": 91, "activeForce": 82, "industryScore": 74, "liquidityScore": 95,
                "reasons": ["跌破 VWAP", "跌破早盤低點", "主動賣盤增加", "反彈無法站回壓力", "產業同步轉弱"],
                "warnings": ["須確認可放空資格", "接近短線支撐"],
            },
            {
                "id": "mock-wait-2454", "symbol": "2454", "stockName": "聯發科", "market": "上市",
                "direction": "long", "directionLabel": "做多", "action": "等待突破",
                "price": 1428 + wave * 2, "entryMin": 1432, "entryMax": 1438, "stopLoss": 1410,
                "target1": 1460, "target2": 1485, "confidenceScore": 74, "healthScore": 76,
                "riskRewardRatio": 1.8, "changePercent": 0.71, "volume": 4_870_000,
                "turnover": 6_954_000_000, "vwapStatus": "VWAP 上方", "volumeStatus": "量能普通",
                "largeOrderForce": 42, "industryStrength": "偏強",
                "spreadPercentage": 0.21, "tradingEligible": True, "shortEligible": False,
                "shortAvailabilityKnown": True, "tradeRestricted": False, "nearLimitDown": False,
                "excessiveNegativeDeviation": False, "chaseBlocked": False,
                "stopDistancePercent": 1.26, "marketAlignment": 80, "confirmationScore": 62,
                "volumeScore": 58, "activeForce": 52, "industryScore": 70, "liquidityScore": 88,
                "reasons": ["VWAP 向上", "產業偏強", "大盤偏多"],
                "warnings": ["突破量尚未確認"],
            },
            {
                "id": "mock-long-2330", "symbol": "2330", "stockName": "台積電", "market": "上市",
                "direction": "long", "directionLabel": "做多", "action": "突破買進",
                "price": 1120 + wave * 2, "entryMin": 1116, "entryMax": 1121, "stopLoss": 1102,
                "target1": 1148, "target2": 1172, "confidenceScore": 87, "healthScore": 84,
                "riskRewardRatio": 2.0, "changePercent": 1.25, "volume": 24_600_000,
                "turnover": 27_552_000_000, "vwapStatus": "站上且向上", "volumeStatus": "量價齊揚",
                "largeOrderForce": 71, "industryStrength": "強勢",
                "spreadPercentage": 0.09, "tradingEligible": True, "shortEligible": False,
                "shortAvailabilityKnown": True, "tradeRestricted": False, "nearLimitDown": False,
                "excessiveNegativeDeviation": False, "chaseBlocked": False,
                "stopDistancePercent": 1.61, "marketAlignment": 89, "confirmationScore": 86,
                "volumeScore": 88, "activeForce": 75, "industryScore": 90, "liquidityScore": 98,
                "reasons": ["站上 VWAP", "突破盤中壓力", "半導體族群同步走強"],
                "warnings": ["展示模式價格", "仍須等待成交量延續"],
            },
            {
                "id": "mock-long-2382", "symbol": "2382", "stockName": "廣達", "market": "上市",
                "direction": "long", "directionLabel": "做多", "action": "回踩買進",
                "price": 302.5 + wave * 0.8, "entryMin": 300.5, "entryMax": 303.0, "stopLoss": 296.0,
                "target1": 311.0, "target2": 318.0, "confidenceScore": 83, "healthScore": 80,
                "riskRewardRatio": 1.9, "changePercent": 1.68, "volume": 18_300_000,
                "turnover": 5_535_750_000, "vwapStatus": "回踩 VWAP 不破", "volumeStatus": "回檔量縮",
                "largeOrderForce": 64, "industryStrength": "偏強",
                "spreadPercentage": 0.13, "tradingEligible": True, "shortEligible": False,
                "shortAvailabilityKnown": True, "tradeRestricted": False, "nearLimitDown": False,
                "excessiveNegativeDeviation": False, "chaseBlocked": False,
                "stopDistancePercent": 2.15, "marketAlignment": 84, "confirmationScore": 81,
                "volumeScore": 80, "activeForce": 70, "industryScore": 82, "liquidityScore": 94,
                "reasons": ["回踩 VWAP 有撐", "5 分 K 均線向上", "伺服器族群偏強"],
                "warnings": ["展示模式價格", "跌破 VWAP 則訊號失效"],
            },
            {
                "id": "mock-short-2603", "symbol": "2603", "stockName": "長榮", "market": "上市",
                "direction": "short", "directionLabel": "放空", "action": "反彈放空",
                "price": 206.0 - wave * 0.5, "entryMin": 205.5, "entryMax": 206.5, "stopLoss": 210.0,
                "target1": 199.5, "target2": 195.0, "confidenceScore": 80, "healthScore": 77,
                "riskRewardRatio": 1.8, "changePercent": -1.12, "volume": 15_900_000,
                "turnover": 3_275_400_000, "vwapStatus": "反彈未站回", "volumeStatus": "賣量增加",
                "largeOrderForce": -61, "industryStrength": "轉弱",
                "spreadPercentage": 0.16, "tradingEligible": True, "shortEligible": True,
                "shortAvailabilityKnown": True, "tradeRestricted": False, "nearLimitDown": False,
                "excessiveNegativeDeviation": False, "chaseBlocked": False,
                "stopDistancePercent": 1.94, "marketAlignment": 68, "confirmationScore": 79,
                "volumeScore": 82, "activeForce": 73, "industryScore": 76, "liquidityScore": 92,
                "reasons": ["反彈未站回 VWAP", "航運族群同步轉弱", "主動賣盤增加"],
                "warnings": ["展示模式價格", "須確認券源與放空限制"],
            },
        ]
        if scenario == "long_signal":
            templates[0]["action"] = "突破買進"
            templates[0]["confidenceScore"] = 92
        if scenario == "short_signal":
            templates[1]["action"] = "反彈放空"
            templates[1]["confidenceScore"] = 91
        if scenario in {"data_delay", "disconnect"}:
            for item in templates:
                item["action"] = "行情異常"
                item["status"] = "blocked"
                item["warnings"] = ["行情資料異常，目前停止產生新進場訊號"]
        for index, item in enumerate(templates):
            generated_at = now - timedelta(seconds=20 + index * 8)
            expires_at = now + timedelta(minutes=3 + index)
            item.update({
                "rank": index + 1, "generatedAt": generated_at.isoformat(),
                "expiresAt": expires_at.isoformat(), "quoteTimestamp": now.isoformat(),
                "status": item.get("status", "confirmed"), "dataSource": "mock_stream",
                "dataMode": "demo", "dataNotice": DATA_NOTICE, "disclaimer": DISCLAIMER,
                "dataStatus": (
                    "disconnected" if scenario == "disconnect"
                    else "severe_delay" if scenario == "data_delay"
                    else "normal"
                ),
                "liveSampleCount": tick,
            })
            item["price"] = round(float(item["price"]), 2)
        return self.apply_official_quotes(templates)

    def quote_for(self, symbol: str) -> float | None:
        signal = next((item for item in self.signals() if item["symbol"] == symbol), None)
        return None if signal is None else float(signal["price"])

    def consume_scenario(self) -> str | None:
        with self._lock:
            scenario = self._scenario
            if scenario not in {"data_delay", "disconnect"}:
                self._scenario = None
            return scenario

    def event_key_once(self, key: str) -> bool:
        with self._lock:
            if key in self._emitted_keys:
                return False
            self._emitted_keys.add(key)
            return True


day_trading_engine = MockDayTradingEngine()
