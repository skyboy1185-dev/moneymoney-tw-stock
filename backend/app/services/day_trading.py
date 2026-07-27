import math
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .official_market_data import OfficialStockQuote


DISCLAIMER = "僅供研究參考，不構成投資建議。所有交易均須由使用者自行確認。"
DATA_NOTICE = "展示模式，非即時行情"
LIVE_DATA_NOTICE = "價格與盤中技術條件由 TWSE MIS 實際行情樣本計算；僅供研究參考，不構成投資建議。"
TAIPEI = ZoneInfo("Asia/Taipei")


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
        self._quote_history: dict[str, list[OfficialStockQuote]] = {}
        self._signal_windows: dict[str, tuple[datetime, datetime, str]] = {}

    def trigger(self, scenario: str) -> None:
        with self._lock:
            self._scenario = scenario

    def update_official_quotes(self, quotes: dict[str, OfficialStockQuote]) -> None:
        if not quotes:
            return
        with self._lock:
            for symbol, quote in quotes.items():
                previous = self._official_quotes.get(symbol)
                self._official_quotes[symbol] = quote
                history = self._quote_history.setdefault(symbol, [])
                if history and history[-1].quote_timestamp[:10] != quote.quote_timestamp[:10]:
                    history.clear()
                is_new_sample = (
                    previous is None
                    or previous.quote_timestamp != quote.quote_timestamp
                    or previous.price != quote.price
                    or previous.volume != quote.volume
                )
                if is_new_sample:
                    history.append(quote)
                    if len(history) > 1_500:
                        del history[:-1_500]
                    self._tick += 1

    def apply_official_quotes(self, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            quotes = dict(self._official_quotes)
            histories = {
                symbol: list(values)
                for symbol, values in self._quote_history.items()
            }
        regime = self.market_regime()
        for item in signals:
            quote = quotes.get(str(item["symbol"]))
            if quote is None:
                continue
            history = histories.get(quote.symbol, [])
            metrics = self._live_metrics(history)
            price = quote.price
            vwap = float(metrics["vwap"] or price)
            trend_1m = float(metrics["trend1m"])
            trend_5m = float(metrics["trend5m"])
            active_force = float(metrics["activeForce"])
            volume_accelerating = bool(metrics["volumeAccelerating"])
            above_vwap = price >= vwap
            vwap_up = bool(metrics["vwapUp"])
            breakout = bool(metrics["breakout"])
            breakdown = bool(metrics["breakdown"])
            direction = (
                "long"
                if above_vwap and trend_1m >= 0 and trend_5m >= 0
                else "short"
                if not above_vwap and trend_1m <= 0 and trend_5m <= 0
                else "long"
                if quote.change_percent >= 0
                else "short"
            )
            market_score = float(regime["score"])
            market_alignment = round(max(0, min(
                100,
                50 + market_score / 2 if direction == "long" else 50 - market_score / 2,
            )))
            score_metrics: dict[str, float | bool] = {
                "vwap_up": vwap_up,
                "above_vwap": above_vwap,
                "breakout": breakout,
                "volume": volume_accelerating,
                "active_buy": active_force >= 15,
                "large_buy": False,
                "short_trend": trend_1m > 0 and trend_5m >= 0,
                "market_fit": market_alignment >= 55,
                "industry_fit": False,
            } if direction == "long" else {
                "vwap_down": not vwap_up,
                "below_vwap": not above_vwap,
                "breakdown": breakdown,
                "volume": volume_accelerating,
                "active_sell": active_force <= -15,
                "large_sell": False,
                "short_trend": trend_1m < 0 and trend_5m <= 0,
                "market_fit": market_alignment >= 55,
                "industry_fit": False,
            }
            confidence = (
                long_signal_score(score_metrics)
                if direction == "long"
                else short_signal_score(score_metrics)
            )
            health = round(max(0, min(100, confidence * .65 + metrics["qualityScore"] * .35)))
            deviation = ((price - vwap) / vwap * 100) if vwap else 0
            chase_blocked = (
                direction == "long" and deviation > 2.5
            ) or (
                direction == "short" and deviation < -2.5
            )
            confirmed = bool(metrics["qualified"]) and confidence >= 75 and not chase_blocked
            if direction == "long":
                action = (
                    "突破買進"
                    if confirmed and breakout
                    else "回踩買進"
                    if confirmed and above_vwap and abs(deviation) <= .8
                    else "等待突破"
                )
            else:
                action = (
                    "跌破放空"
                    if confirmed and breakdown
                    else "反彈放空"
                    if confirmed and not above_vwap and abs(deviation) <= .8
                    else "等待跌破"
                )
            day_range = max(quote.high - quote.low, price * .008)
            risk_distance = min(price * .025, max(price * .008, day_range * .18))
            entry_min = price * (.998 if direction == "long" else .997)
            entry_max = price * (1.002 if direction == "long" else 1.001)
            stop_loss = price - risk_distance if direction == "long" else price + risk_distance
            target_1 = price + risk_distance * 1.5 if direction == "long" else price - risk_distance * 1.5
            target_2 = price + risk_distance * 2.5 if direction == "long" else price - risk_distance * 2.5
            spread_percentage = (
                (quote.best_ask - quote.best_bid) / price * 100
                if quote.best_ask is not None and quote.best_bid is not None and price
                else 999
            )
            exact_trade = quote.source == "TWSE MIS"
            official_strategy = bool(metrics["qualified"]) and exact_trade and quote.is_realtime
            warnings: list[str] = []
            if not metrics["qualified"]:
                warnings.append(str(metrics["qualificationMessage"]))
            if quote.source == "TWSE MIS 五檔參考價":
                warnings.append("目前為五檔參考價，等待最新成交價")
            if chase_blocked:
                warnings.append("價格距離監控期間 VWAP 過遠，禁止追價")
            if direction == "short":
                warnings.append("放空資格與券源尚待券商確認")
            if not volume_accelerating:
                warnings.append("近期量能尚未明顯增加")
            reasons = [
                f"價格{'站上' if above_vwap else '跌破'}監控期間 VWAP {vwap:,.2f}",
                f"1 分鐘趨勢 {trend_1m:+.2f}%",
                f"5 分鐘趨勢 {trend_5m:+.2f}%",
                f"主動買賣力道推估 {active_force:+.0f}",
                "突破監控區間高點" if breakout else "跌破監控區間低點" if breakdown else "尚未突破監控區間",
            ]
            item.update({
                "stockName": quote.name,
                "direction": direction,
                "directionLabel": "做多" if direction == "long" else "放空",
                "action": action,
                "price": round(price, 2),
                "previousClose": round(quote.previous_close, 2),
                "open": round(quote.open, 2),
                "high": round(quote.high, 2),
                "low": round(quote.low, 2),
                "change": round(quote.change, 2),
                "changePercent": round(quote.change_percent, 2),
                "volume": quote.volume,
                "turnover": round(price * quote.volume),
                "entryMin": round(entry_min, 2),
                "entryMax": round(entry_max, 2),
                "stopLoss": round(stop_loss, 2),
                "target1": round(target_1, 2),
                "target2": round(target_2, 2),
                "confidenceScore": confidence,
                "healthScore": health,
                "riskRewardRatio": 2.5,
                "vwapStatus": (
                    "站上且向上" if above_vwap and vwap_up
                    else "跌破且向下" if not above_vwap and not vwap_up
                    else "VWAP 附近震盪"
                ),
                "volumeStatus": "近期量能增加" if volume_accelerating else "近期量能普通",
                "largeOrderForce": round(active_force),
                "industryStrength": "未串接族群即時資料",
                "spreadPercentage": round(spread_percentage, 4),
                "tradingEligible": True,
                "shortEligible": False,
                "shortAvailabilityKnown": False,
                "tradeRestricted": False,
                "nearLimitDown": quote.change_percent <= -8.5,
                "excessiveNegativeDeviation": deviation <= -5,
                "chaseBlocked": chase_blocked,
                "stopDistancePercent": round(risk_distance / price * 100, 2),
                "marketAlignment": market_alignment,
                "confirmationScore": round(metrics["confirmationScore"]),
                "volumeScore": round(metrics["volumeScore"]),
                "activeForce": round(abs(active_force)),
                "industryScore": 0,
                "liquidityScore": min(100, round(quote.volume / 50_000)),
                "reasons": reasons,
                "warnings": warnings,
                "quoteTimestamp": quote.quote_timestamp,
                "dataSource": quote.source,
                "dataMode": "official" if official_strategy else "warming_up",
                "dataNotice": (
                    LIVE_DATA_NOTICE
                    if official_strategy
                    else f"價格取自 {quote.source}；正在累積實際行情樣本，暫不產生正式訊號。"
                ),
                "quoteIsRealtime": quote.is_realtime,
                "quoteStatus": "盤中行情" if quote.is_realtime else "最近有效行情／收盤",
                "status": "confirmed" if confirmed else "temporary",
                "liveSampleCount": len(history),
            })
            if official_strategy:
                item["id"] = str(item["id"]).replace("mock-", "live-", 1)
        return signals

    @staticmethod
    def _live_metrics(history: list[OfficialStockQuote]) -> dict[str, Any]:
        if not history:
            return {
                "qualified": False,
                "qualificationMessage": "尚未收到實際盤中行情",
                "vwap": None,
                "vwapUp": False,
                "trend1m": 0.0,
                "trend5m": 0.0,
                "activeForce": 0.0,
                "volumeAccelerating": False,
                "breakout": False,
                "breakdown": False,
                "qualityScore": 0,
                "confirmationScore": 0,
                "volumeScore": 0,
            }
        samples = sorted(history, key=lambda item: item.quote_timestamp)
        latest = samples[-1]
        latest_time = datetime.fromisoformat(latest.quote_timestamp)
        same_day = [
            item for item in samples
            if item.quote_timestamp[:10] == latest.quote_timestamp[:10]
        ]
        first_time = datetime.fromisoformat(same_day[0].quote_timestamp)

        def prior(seconds: int) -> OfficialStockQuote:
            threshold = latest_time - timedelta(seconds=seconds)
            eligible = [
                item for item in same_day
                if datetime.fromisoformat(item.quote_timestamp) <= threshold
            ]
            return eligible[-1] if eligible else same_day[0]

        one_minute = prior(60)
        five_minute = prior(300)
        trend_1m = (
            (latest.price - one_minute.price) / one_minute.price * 100
            if one_minute.price else 0
        )
        trend_5m = (
            (latest.price - five_minute.price) / five_minute.price * 100
            if five_minute.price else 0
        )
        weighted_value = 0.0
        weighted_volume = 0
        buy_volume = 0
        sell_volume = 0
        recent_volume = 0
        previous_volume = 0
        midpoint = latest_time - timedelta(seconds=60)
        previous_midpoint = latest_time - timedelta(seconds=120)
        old_weighted_value = 0.0
        old_weighted_volume = 0
        new_weighted_value = 0.0
        new_weighted_volume = 0
        for previous, current in zip(same_day, same_day[1:]):
            delta_volume = max(0, current.volume - previous.volume)
            if delta_volume <= 0:
                continue
            weighted_value += current.price * delta_volume
            weighted_volume += delta_volume
            current_time = datetime.fromisoformat(current.quote_timestamp)
            if current.price > previous.price:
                buy_volume += delta_volume
            elif current.price < previous.price:
                sell_volume += delta_volume
            if current_time >= midpoint:
                recent_volume += delta_volume
                new_weighted_value += current.price * delta_volume
                new_weighted_volume += delta_volume
            elif current_time >= previous_midpoint:
                previous_volume += delta_volume
                old_weighted_value += current.price * delta_volume
                old_weighted_volume += delta_volume
        vwap = weighted_value / weighted_volume if weighted_volume else latest.price
        old_vwap = old_weighted_value / old_weighted_volume if old_weighted_volume else vwap
        new_vwap = new_weighted_value / new_weighted_volume if new_weighted_volume else vwap
        signed_total = buy_volume + sell_volume
        active_force = (
            (buy_volume - sell_volume) / signed_total * 100
            if signed_total else 0
        )
        prior_prices = [item.price for item in same_day[:-1]]
        span_seconds = (latest_time - first_time).total_seconds()
        exact_samples = [
            item for item in same_day
            if item.source == "TWSE MIS" and item.is_realtime
        ]
        qualified = (
            len(exact_samples) >= 12
            and span_seconds >= 180
            and weighted_volume > 0
        )
        qualification_message = (
            "實際行情樣本已完成暖機"
            if qualified
            else f"實際行情暖機中：{len(exact_samples)}/12 筆，累積 {max(0, round(span_seconds))}/180 秒"
        )
        sample_quality = min(100, len(exact_samples) / 12 * 50 + span_seconds / 180 * 50)
        confirmation = min(100, abs(trend_1m) * 35 + abs(trend_5m) * 20 + abs(active_force) * .45)
        volume_score = min(100, 50 + (
            (recent_volume / previous_volume - 1) * 40
            if previous_volume else 0
        ))
        return {
            "qualified": qualified,
            "qualificationMessage": qualification_message,
            "vwap": vwap,
            "vwapUp": new_vwap > old_vwap,
            "trend1m": trend_1m,
            "trend5m": trend_5m,
            "activeForce": active_force,
            "volumeAccelerating": recent_volume > max(1, previous_volume) * 1.15,
            "breakout": bool(prior_prices) and latest.price >= max(prior_prices),
            "breakdown": bool(prior_prices) and latest.price <= min(prior_prices),
            "qualityScore": sample_quality,
            "confirmationScore": confirmation,
            "volumeScore": max(0, volume_score),
        }

    def _now(self) -> datetime:
        return datetime.now(UTC)

    @property
    def sample_count(self) -> int:
        with self._lock:
            live_counts = [
                len(history)
                for symbol, history in self._quote_history.items()
                if symbol != "t00"
            ]
            return max(live_counts, default=self._tick)

    def market_regime(self) -> dict[str, Any]:
        now = self._now()
        with self._lock:
            scenario = self._scenario
            quotes = dict(self._official_quotes)
            histories = {
                symbol: list(values)
                for symbol, values in self._quote_history.items()
            }
        has_official_quotes = bool(quotes)
        data_status = "normal"
        delay = 1.2
        direction = "bull"
        score = 72
        if scenario == "data_delay":
            data_status, delay, direction, score = "severe_delay", 12.0, "data_anomaly", 0
        elif scenario == "disconnect":
            data_status, delay, direction, score = "disconnected", 30.0, "data_anomaly", 0
        index_quote = quotes.get("t00")
        live_market = index_quote is not None and scenario is None
        pool_quotes = [
            quote for symbol, quote in quotes.items()
            if symbol != "t00"
        ]
        if live_market and index_quote is not None:
            try:
                delay = max(0, (
                    now.astimezone(TAIPEI)
                    - datetime.fromisoformat(index_quote.quote_timestamp)
                ).total_seconds())
            except ValueError:
                delay = 999
            data_status = (
                "normal"
                if index_quote.source == "TWSE MIS" and index_quote.is_realtime and delay <= 20
                else "severe_delay"
                if index_quote.source == "TWSE MIS" and delay <= 120
                else "source_error"
            )
            index_metrics = self._live_metrics(histories.get("t00", []))
            advancers = sum(quote.change > 0 for quote in pool_quotes)
            decliners = sum(quote.change < 0 for quote in pool_quotes)
            flat = max(0, len(pool_quotes) - advancers - decliners)
            breadth_score = (
                (advancers - decliners) / max(1, advancers + decliners + flat) * 25
            )
            index_component = max(-45, min(45, index_quote.change_percent * 20))
            trend_component = max(-30, min(30, float(index_metrics["trend5m"]) * 18))
            score = round(max(-100, min(100, index_component + trend_component + breadth_score)))
            direction = (
                "strong_bull" if score >= 60
                else "bull" if score >= 20
                else "strong_bear" if score <= -60
                else "bear" if score <= -20
                else "sideways"
            )
            pool_forces = [
                float(self._live_metrics(histories.get(quote.symbol, []))["activeForce"])
                for quote in pool_quotes
            ]
            active_force = (
                sum(pool_forces) / len(pool_forces)
                if pool_forces else 0
            )
            preferred = "做多" if score >= 20 else "放空" if score <= -20 else "觀望"
            direction_label = {
                "strong_bull": "強多",
                "bull": "偏多",
                "sideways": "震盪",
                "bear": "偏空",
                "strong_bear": "強空",
            }[direction]
            one_minute = float(index_metrics["trend1m"])
            five_minute = float(index_metrics["trend5m"])
            reasons = [
                f"加權指數漲跌 {index_quote.change_percent:+.2f}%",
                f"加權指數 1 分鐘趨勢 {one_minute:+.2f}%",
                f"加權指數 5 分鐘趨勢 {five_minute:+.2f}%",
                f"掃描股票上漲／下跌 {advancers}／{decliners}",
                f"主動買賣力道為抽樣 Tick Rule 推估 {active_force:+.0f}",
            ]
            environment_score = max(0, min(100, 70 + abs(score) * .2 - (0 if data_status == "normal" else 70)))
            live_metrics = {
                "weightedIndex": round(index_quote.price, 2),
                "otcIndex": "尚未串接",
                "indexFutures": "尚未串接",
                "vwap": round(float(index_metrics["vwap"] or index_quote.price), 2),
                "oneMinuteTrend": f"{one_minute:+.2f}%",
                "fiveMinuteTrend": f"{five_minute:+.2f}%",
                "fifteenMinuteTrend": "樣本累積中",
                "advancers": advancers,
                "decliners": decliners,
                "limitUp": sum(quote.change_percent >= 9.5 for quote in pool_quotes),
                "limitDown": sum(quote.change_percent <= -9.5 for quote in pool_quotes),
                "largeOrderForce": f"{active_force:+.0f}（抽樣推估）",
                "smallOrderForce": "尚未串接",
                "relativeVolume": round(sum(float(
                    self._live_metrics(histories.get(quote.symbol, []))["volumeScore"]
                ) for quote in pool_quotes) / max(1, len(pool_quotes)) / 50, 2),
                "strongIndustries": ["尚未串接完整族群"],
                "weakIndustries": ["尚未串接完整族群"],
                "breadth": round((advancers / max(1, len(pool_quotes))) * 100, 1),
                "volatility": "高" if abs(five_minute) >= 1 else "中等" if abs(five_minute) >= .35 else "低",
            }
            return {
                "direction": direction,
                "directionLabel": direction_label,
                "score": score,
                "environmentScore": round(environment_score),
                "environmentLabel": "適合交易" if data_status == "normal" else "停止新訊號",
                "preferredDirection": preferred,
                "shortRestriction": "放空資格與券源必須由券商確認",
                "risk": "中高" if abs(score) >= 60 else "中",
                "longPermission": max(0, min(100, 50 + score // 2)),
                "shortPermission": max(0, min(100, 50 - score // 2)),
                "suitableStrategies": (
                    ["突破買進", "回踩買進"] if score >= 20
                    else ["跌破放空", "反彈放空"] if score <= -20
                    else ["等待突破", "等待跌破"]
                ),
                "forbiddenStrategies": ["無量追價", "急跌追空", "資料延遲時進場"],
                "reasons": reasons,
                "dataStatus": data_status,
                "dataDelaySeconds": round(delay, 1),
                "dataSource": "TWSE MIS 實際行情＋抽樣 Tick Rule 推估",
                "marketOpen": True,
                "session": "09:00～13:30",
                "updatedAt": now.isoformat(),
                "metrics": live_metrics,
                "mode": "official" if data_status == "normal" else "warming_up",
                "dataNotice": LIVE_DATA_NOTICE,
            }
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
            "mode": "demo",
            "dataNotice": DATA_NOTICE,
        }

    def _signal_window(
        self,
        key: str,
        now: datetime,
        validity: timedelta = timedelta(minutes=5),
    ) -> tuple[datetime, datetime, str]:
        with self._lock:
            current = self._signal_windows.get(key)
            if current is not None and current[1] > now:
                return current
            generated_at = now
            expires_at = now + validity
            signal_id = f"{key}-{generated_at.strftime('%Y%m%dT%H%M%S')}"
            window = (generated_at, expires_at, signal_id)
            self._signal_windows[key] = window
            return window

    def signals(self, now: datetime | None = None) -> list[dict[str, Any]]:
        with self._lock:
            self._tick += 1
            tick = self._tick
            scenario = self._scenario
        now = now or self._now()
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
            base_id = str(item["id"])
            lifecycle_key = f"{base_id}:{item['action']}:{item.get('status', 'confirmed')}"
            generated_at, expires_at, signal_id = self._signal_window(lifecycle_key, now)
            item.update({
                "id": signal_id,
                "rank": index + 1, "generatedAt": generated_at.isoformat(),
                "expiresAt": expires_at.isoformat(), "quoteTimestamp": now.isoformat(),
                "serverNow": now.isoformat(),
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
