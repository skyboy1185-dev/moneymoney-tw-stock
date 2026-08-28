import math
import threading
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .official_market_data import OfficialStockQuote
from .day_trading_schedule import (
    MAX_LONG_CHASE_CHANGE_PERCENT,
    MIN_OFFICIAL_CONFIDENCE_SCORE,
    MIN_OFFICIAL_CONFIRMATION_SCORE,
)
from .popular_stock_universe import merge_momentum_stocks
from .three_gate_price import (
    ThreeGatePrice,
    evaluate_opening_three_gate_retest,
    evaluate_three_gate_direction,
)
from .theme_stock_universe import (
    ThemeStock,
)


DISCLAIMER = "僅供研究參考，不構成投資建議。所有交易均須由使用者自行確認。"
DATA_NOTICE = "展示模式，非即時行情"
LIVE_DATA_NOTICE = "價格與盤中技術條件由 TWSE MIS 實際行情樣本計算；僅供研究參考，不構成投資建議。"
TAIPEI = ZoneInfo("Asia/Taipei")
LIVE_QUOTE_MAX_DELAY_SECONDS = 20
DEGRADED_INDEX_DELAY_SECONDS = 60
MIN_DEGRADED_POOL_COVERAGE_RATIO = 0.80
SOURCE_INTERRUPTION_SECONDS = 300
EXTREME_RANGE_EDGE_PERCENT = 10.0
RETEST_RANGE_EDGE_PERCENT = 25.0
DIRECT_ENTRY_MAX_VWAP_DEVIATION_PERCENT = 1.5
THEME_REFERENCE_PRICES = {
    "2308": 468.0,
    "2313": 199.5,
    "2314": 12.5,
    "3491": 1155.0,
    "6285": 249.5,
    "2368": 895.0,
    "3037": 848.0,
    "3189": 710.0,
    "8046": 1075.0,
    "2327": 625.0,
    "2492": 272.0,
    "3026": 585.0,
    "2337": 125.5,
    "2344": 160.0,
    "2408": 436.0,
    "8299": 1820.0,
    "1802": 52.5,
    "1815": 75.2,
    "5340": 68.9,
    "2379": 762.0,
    "3034": 518.0,
    "3443": 4050.0,
    "3661": 3460.0,
    "5269": 1385.0,
}


def _quote_delay_seconds(now: datetime, quote: OfficialStockQuote) -> float:
    try:
        quote_time = datetime.fromisoformat(quote.quote_timestamp)
    except (TypeError, ValueError):
        return 999.0
    if quote_time.tzinfo is None:
        quote_time = quote_time.replace(tzinfo=TAIPEI)
    return max(0.0, (now.astimezone(TAIPEI) - quote_time).total_seconds())


def _stock_seed_signal(stock: ThemeStock, wave: float) -> dict[str, Any]:
    reference = THEME_REFERENCE_PRICES.get(stock.symbol, 100.0)
    current = reference * (1 + wave * .001)
    risk_distance = max(current * .012, .1)
    theme_label = "／".join(stock.themes)
    return {
        "id": f"mock-theme-{stock.symbol}",
        "symbol": stock.symbol,
        "stockName": stock.name,
        "market": stock.market,
        "themes": list(stock.themes),
        "direction": "long",
        "directionLabel": "做多",
        "action": "等待突破",
        "price": current,
        "entryMin": current * .998,
        "entryMax": current * 1.002,
        "stopLoss": current - risk_distance,
        "target1": current + risk_distance * 1.5,
        "target2": current + risk_distance * 2.5,
        "confidenceScore": 0,
        "healthScore": 0,
        "riskRewardRatio": 2.5,
        "changePercent": 0,
        "volume": 0,
        "turnover": 0,
        "vwapStatus": "等待實際行情",
        "volumeStatus": "等待實際行情",
        "largeOrderForce": 0,
        "industryStrength": theme_label,
        "spreadPercentage": 999,
        "tradingEligible": True,
        "shortEligible": False,
        "shortAvailabilityKnown": False,
        "tradeRestricted": False,
        "nearLimitDown": False,
        "excessiveNegativeDeviation": False,
        "chaseBlocked": False,
        "stopDistancePercent": 1.2,
        "marketAlignment": 50,
        "confirmationScore": 0,
        "volumeScore": 0,
        "activeForce": 0,
        "industryScore": 0,
        "liquidityScore": 0,
        "reasons": [f"屬於大單動能雷達股票池（{theme_label}）", "等待實際盤中行情完成暖機"],
        "warnings": ["尚未取得足夠實際行情樣本"],
        "momentumUniverseMember": True,
    }


def long_signal_score(metrics: dict[str, float | bool]) -> int:
    weights = {
        "vwap_up": 15, "above_vwap": 15, "breakout": 15, "volume": 10,
        "active_buy": 15, "large_buy": 10, "short_trend": 10,
        "market_fit": 5, "industry_fit": 5,
        "above_open": 10, "five_minute_structure": 10,
        "five_minute_ma": 10, "bollinger_retest": 5,
    }
    return min(100, round(sum(weight for key, weight in weights.items() if metrics.get(key))))


def short_signal_score(metrics: dict[str, float | bool]) -> int:
    weights = {
        "vwap_down": 15, "below_vwap": 15, "breakdown": 15, "volume": 10,
        "active_sell": 15, "large_sell": 10, "short_trend": 10,
        "market_fit": 5, "industry_fit": 5,
        "below_open": 10, "five_minute_structure": 10,
        "five_minute_ma": 10,
    }
    return min(100, round(sum(weight for key, weight in weights.items() if metrics.get(key))))


def entry_timing_guard(
    *,
    direction: str,
    price: float,
    day_low: float,
    day_high: float,
    vwap: float,
    change_percent: float,
    five_minute_retest_confirmed: bool,
) -> dict[str, Any]:
    """Block direct entries at intraday extremes until a completed 5m retest."""
    day_range = max(0.0, day_high - day_low)
    range_position = (
        max(0.0, min(100.0, (price - day_low) / day_range * 100))
        if day_range > 0 else 50.0
    )
    vwap_deviation = ((price - vwap) / vwap * 100) if vwap else 0.0
    extreme_range = (
        range_position >= 100 - EXTREME_RANGE_EDGE_PERCENT
        if direction == "long"
        else range_position <= EXTREME_RANGE_EDGE_PERCENT
    )
    retest_zone = (
        range_position >= 100 - RETEST_RANGE_EDGE_PERCENT
        if direction == "long"
        else range_position <= RETEST_RANGE_EDGE_PERCENT
    )
    vwap_stretched = (
        vwap_deviation >= DIRECT_ENTRY_MAX_VWAP_DEVIATION_PERCENT
        if direction == "long"
        else vwap_deviation <= -DIRECT_ENTRY_MAX_VWAP_DEVIATION_PERCENT
    )
    daily_extreme = (
        direction == "long"
        and change_percent >= MAX_LONG_CHASE_CHANGE_PERCENT
        and extreme_range
    )
    retest_required = retest_zone or vwap_stretched
    blocked = extreme_range or daily_extreme or (
        retest_required and not five_minute_retest_confirmed
    )
    if extreme_range:
        reason = (
            "位於日內區間最高 10%，禁止直接追多，等待完整 5 分 K 拉回確認"
            if direction == "long"
            else "位於日內區間最低 10%，禁止直接追空，等待完整 5 分 K 反彈確認"
        )
    elif retest_required and not five_minute_retest_confirmed:
        reason = (
            "接近日內高檔或偏離 VWAP，等待完整 5 分 K 回測後再做多"
            if direction == "long"
            else "接近日內低檔或偏離 VWAP，等待完整 5 分 K 反彈後再放空"
        )
    else:
        reason = "進場位置與完整 5 分 K 回測條件合格"
    return {
        "blocked": blocked,
        "reason": reason,
        "rangePositionPercent": round(range_position, 2),
        "vwapDeviationPercent": round(vwap_deviation, 2),
        "extremeRangeBlocked": extreme_range,
        "vwapRetestRequired": vwap_stretched,
        "retestRequired": retest_required,
        "retestConfirmed": five_minute_retest_confirmed,
        "dailyExtremeBlocked": daily_extreme,
    }


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
    _PERSISTED_HISTORY_LIMIT = 180
    _LIVE_HISTORY_LIMIT = 420
    _LIVE_HISTORY_SAMPLE_SECONDS = 15

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tick = 0
        self._scenario: str | None = None
        self._emitted_keys: set[str] = set()
        self._official_quotes: dict[str, OfficialStockQuote] = {}
        self._quote_history: dict[str, list[OfficialStockQuote]] = {}
        self._three_gate_prices: dict[str, ThreeGatePrice] = {}
        self._three_gate_invalidations: dict[str, str] = {}
        self._signal_windows: dict[str, tuple[datetime, datetime, str]] = {}
        self._stock_universe, _ = merge_momentum_stocks(())

    def set_stock_universe(self, stocks: tuple[ThemeStock, ...]) -> None:
        """Use the momentum radar pool as the sole day-trading candidate universe."""
        deduplicated = tuple({stock.symbol: stock for stock in stocks}.values())
        if not deduplicated:
            return
        active_symbols = {stock.symbol for stock in deduplicated}
        with self._lock:
            self._stock_universe = deduplicated
            # The radar starts with a smaller fallback pool while its official
            # 300-stock ranking is loading. Do not discard restored history
            # during that brief startup window.
            if len(deduplicated) >= 200:
                self._official_quotes = {
                    symbol: quote
                    for symbol, quote in self._official_quotes.items()
                    if symbol == "t00" or symbol in active_symbols
                }
                self._quote_history = {
                    symbol: history
                    for symbol, history in self._quote_history.items()
                    if symbol == "t00" or symbol in active_symbols
                }
                self._three_gate_invalidations = {
                    symbol: trading_date
                    for symbol, trading_date in self._three_gate_invalidations.items()
                    if symbol in active_symbols
                }

    @property
    def stock_universe_symbols(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(stock.symbol for stock in self._stock_universe)

    @property
    def quote_coverage_count(self) -> int:
        with self._lock:
            universe = {stock.symbol for stock in self._stock_universe}
            return sum(symbol in self._official_quotes for symbol in universe)

    @property
    def warmed_symbol_count(self) -> int:
        with self._lock:
            universe = {stock.symbol for stock in self._stock_universe}
            histories = {
                symbol: list(history)
                for symbol, history in self._quote_history.items()
                if symbol in universe
            }
        warmed = 0
        for history in histories.values():
            buckets: set[int] = set()
            for quote in history:
                if quote.source != "TWSE MIS" or not quote.is_realtime:
                    continue
                try:
                    timestamp = datetime.fromisoformat(quote.quote_timestamp)
                except ValueError:
                    continue
                buckets.add(int(timestamp.timestamp() // 300))
            if max(0, len(buckets) - 1) >= 5:
                warmed += 1
        return warmed

    def trigger(self, scenario: str) -> None:
        with self._lock:
            self._scenario = scenario

    def update_official_quotes(self, quotes: dict[str, OfficialStockQuote]) -> None:
        if not quotes:
            return
        with self._lock:
            for symbol, quote in quotes.items():
                previous = self._official_quotes.get(symbol)
                if previous is not None:
                    try:
                        if datetime.fromisoformat(quote.quote_timestamp) < datetime.fromisoformat(
                            previous.quote_timestamp
                        ):
                            continue
                    except ValueError:
                        pass
                self._official_quotes[symbol] = quote
                history = self._quote_history.setdefault(symbol, [])
                if history and history[-1].quote_timestamp[:10] != quote.quote_timestamp[:10]:
                    history.clear()
                    self._three_gate_invalidations.pop(symbol, None)
                is_new_sample = (
                    previous is None
                    or previous.quote_timestamp != quote.quote_timestamp
                    or previous.price != quote.price
                    or previous.volume != quote.volume
                )
                if is_new_sample:
                    replace_latest = False
                    if history:
                        try:
                            latest_at = datetime.fromisoformat(history[-1].quote_timestamp)
                            quote_at = datetime.fromisoformat(quote.quote_timestamp)
                            elapsed = (quote_at - latest_at).total_seconds()
                            replace_latest = 0 <= elapsed < self._LIVE_HISTORY_SAMPLE_SECONDS
                        except ValueError:
                            replace_latest = False
                    if replace_latest:
                        history[-1] = quote
                    else:
                        history.append(quote)
                    if len(history) > self._LIVE_HISTORY_LIMIT:
                        del history[:-self._LIVE_HISTORY_LIMIT]
                    self._tick += 1

    def update_three_gate_prices(self, levels: dict[str, ThreeGatePrice]) -> None:
        if not levels:
            return
        with self._lock:
            self._three_gate_prices.update(levels)

    @property
    def three_gate_coverage_count(self) -> int:
        with self._lock:
            universe = {stock.symbol for stock in self._stock_universe}
            return sum(symbol in self._three_gate_prices for symbol in universe)

    def export_official_quote_history(self, now: datetime | None = None) -> dict[str, Any]:
        """Returns a compact, current-day warmup snapshot suitable for Redis."""
        trading_date = (now or self._now()).astimezone(TAIPEI).date().isoformat()
        with self._lock:
            histories = {
                symbol: list(values)
                for symbol, values in self._quote_history.items()
            }
        symbols: dict[str, dict[str, Any]] = {}
        for symbol, history in histories.items():
            buckets: dict[int, OfficialStockQuote] = {}
            for quote in history:
                try:
                    timestamp = datetime.fromisoformat(quote.quote_timestamp)
                except (TypeError, ValueError):
                    continue
                if timestamp.astimezone(TAIPEI).date().isoformat() != trading_date:
                    continue
                buckets[int(timestamp.timestamp() // 60)] = quote
            samples = [buckets[key] for key in sorted(buckets)][-self._PERSISTED_HISTORY_LIMIT:]
            if not samples:
                continue
            latest = samples[-1]
            symbols[symbol] = {
                "name": latest.name,
                "previousClose": latest.previous_close,
                "source": latest.source,
                "samples": [
                    [
                        quote.quote_timestamp,
                        quote.price,
                        quote.open,
                        quote.high,
                        quote.low,
                        quote.volume,
                        quote.change,
                        quote.change_percent,
                        quote.is_realtime,
                        quote.best_bid,
                        quote.best_ask,
                        list(quote.bid_prices),
                        list(quote.bid_volumes),
                        list(quote.ask_prices),
                        list(quote.ask_volumes),
                    ]
                    for quote in samples
                ],
            }
        return {"version": 1, "tradingDate": trading_date, "symbols": symbols}

    def restore_official_quote_history(
        self,
        payload: Any,
        now: datetime | None = None,
    ) -> int:
        """Restores only today's verified samples; stale quotes remain safety-gated."""
        trading_date = (now or self._now()).astimezone(TAIPEI).date().isoformat()
        if not isinstance(payload, dict) or payload.get("tradingDate") != trading_date:
            return 0
        raw_symbols = payload.get("symbols")
        if not isinstance(raw_symbols, dict):
            return 0
        restored: dict[str, list[OfficialStockQuote]] = {}
        for raw_symbol, raw_history in raw_symbols.items():
            if not isinstance(raw_history, dict):
                continue
            symbol = str(raw_symbol)
            name = str(raw_history.get("name") or symbol)
            source = str(raw_history.get("source") or "TWSE MIS")
            try:
                previous_close = float(raw_history.get("previousClose"))
            except (TypeError, ValueError):
                continue
            quotes: list[OfficialStockQuote] = []
            for sample in raw_history.get("samples", []):
                if not isinstance(sample, list) or len(sample) < 11:
                    continue
                try:
                    timestamp = str(sample[0])
                    parsed_timestamp = datetime.fromisoformat(timestamp)
                    if parsed_timestamp.astimezone(TAIPEI).date().isoformat() != trading_date:
                        continue
                    quotes.append(OfficialStockQuote(
                        symbol=symbol,
                        name=name,
                        price=float(sample[1]),
                        previous_close=previous_close,
                        open=float(sample[2]),
                        high=float(sample[3]),
                        low=float(sample[4]),
                        volume=int(sample[5]),
                        change=float(sample[6]),
                        change_percent=float(sample[7]),
                        quote_timestamp=timestamp,
                        source=source,
                        is_realtime=bool(sample[8]),
                        best_bid=float(sample[9]) if sample[9] is not None else None,
                        best_ask=float(sample[10]) if sample[10] is not None else None,
                        bid_prices=tuple(float(value) for value in sample[11]) if len(sample) > 11 and isinstance(sample[11], list) else (),
                        bid_volumes=tuple(int(value) for value in sample[12]) if len(sample) > 12 and isinstance(sample[12], list) else (),
                        ask_prices=tuple(float(value) for value in sample[13]) if len(sample) > 13 and isinstance(sample[13], list) else (),
                        ask_volumes=tuple(int(value) for value in sample[14]) if len(sample) > 14 and isinstance(sample[14], list) else (),
                    ))
                except (TypeError, ValueError):
                    continue
            if quotes:
                restored[symbol] = quotes[-self._PERSISTED_HISTORY_LIMIT:]
        with self._lock:
            for symbol, quotes in restored.items():
                existing = {
                    quote.quote_timestamp: quote
                    for quote in self._quote_history.get(symbol, [])
                    if quote.quote_timestamp[:10] == trading_date
                }
                existing.update({quote.quote_timestamp: quote for quote in quotes})
                merged = sorted(existing.values(), key=lambda quote: quote.quote_timestamp)
                self._quote_history[symbol] = merged[-self._PERSISTED_HISTORY_LIMIT:]
                self._official_quotes[symbol] = self._quote_history[symbol][-1]
            restored_count = max(
                (len(self._quote_history[symbol]) for symbol in restored),
                default=0,
            )
            self._tick = max(self._tick, restored_count)
        return restored_count

    def apply_official_quotes(self, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            quotes = dict(self._official_quotes)
            three_gate_prices = dict(self._three_gate_prices)
            three_gate_invalidations = dict(self._three_gate_invalidations)
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
            technical_direction = (
                "long"
                if metrics["fiveMinuteBullish"]
                else "short"
                if metrics["fiveMinuteBearish"]
                else "long"
                if above_vwap and trend_1m >= 0 and trend_5m >= 0
                else "short"
                if not above_vwap and trend_1m <= 0 and trend_5m <= 0
                else "long"
                if quote.change_percent >= 0
                else "short"
            )
            three_gate = three_gate_prices.get(quote.symbol)
            previous_intraday_price = history[-2].price if len(history) >= 2 else None
            previous_price = previous_intraday_price or quote.previous_close
            trading_date = quote.quote_timestamp[:10]
            previously_invalidated = three_gate_invalidations.get(quote.symbol) == trading_date
            three_gate_decision = (
                evaluate_three_gate_direction(price, previous_price, three_gate)
                if three_gate is not None
                else None
            )
            opening_retest = (
                evaluate_opening_three_gate_retest(
                    open_price=quote.open,
                    previous_close=quote.previous_close,
                    current_price=price,
                    previous_intraday_price=previous_intraday_price,
                    session_high=quote.high,
                    session_low=quote.low,
                    completed_bar_open=(
                        float(metrics["fiveMinuteLastCompletedOpen"])
                        if metrics["fiveMinuteLastCompletedOpen"] is not None else None
                    ),
                    completed_bar_close=(
                        float(metrics["fiveMinuteLastCompletedClose"])
                        if metrics["fiveMinuteLastCompletedClose"] is not None else None
                    ),
                    minimum_completed_close=(
                        float(metrics["fiveMinuteMinCompletedClose"])
                        if metrics["fiveMinuteMinCompletedClose"] is not None else None
                    ),
                    maximum_completed_close=(
                        float(metrics["fiveMinuteMaxCompletedClose"])
                        if metrics["fiveMinuteMaxCompletedClose"] is not None else None
                    ),
                    previously_invalidated=previously_invalidated,
                    three_gate=three_gate,
                )
                if three_gate is not None
                else None
            )
            if opening_retest is not None and opening_retest.invalidated and not previously_invalidated:
                with self._lock:
                    self._three_gate_invalidations[quote.symbol] = trading_date
            direction = technical_direction
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
                "above_open": bool(metrics["aboveOpen"]),
                "five_minute_structure": metrics["fiveMinuteHigherLows"],
                "five_minute_ma": metrics["fiveMinuteMaRising"],
                "bollinger_retest": metrics["fiveMinuteBollingerRetest"],
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
                "below_open": bool(metrics["belowOpen"]),
                "five_minute_structure": metrics["fiveMinuteLowerHighs"],
                "five_minute_ma": metrics["fiveMinuteMaFalling"],
            }
            confidence = (
                long_signal_score(score_metrics)
                if direction == "long"
                else short_signal_score(score_metrics)
            )
            health = round(max(0, min(100, confidence * .65 + metrics["qualityScore"] * .35)))
            retest_confirmed = bool(
                metrics["fiveMinuteLongRetest"]
                if direction == "long"
                else metrics["fiveMinuteShortRetest"]
            )
            timing = entry_timing_guard(
                direction=direction,
                price=price,
                day_low=quote.low,
                day_high=quote.high,
                vwap=vwap,
                change_percent=quote.change_percent,
                five_minute_retest_confirmed=retest_confirmed,
            )
            deviation = float(timing["vwapDeviationPercent"])
            vwap_chase_blocked = bool(timing["vwapRetestRequired"] and not retest_confirmed)
            daily_chase_blocked = bool(timing["dailyExtremeBlocked"])
            chase_blocked = bool(timing["blocked"])
            directional_setup = bool(
                metrics["fiveMinuteLongSetup"]
                if direction == "long"
                else metrics["fiveMinuteShortSetup"]
            )
            confirmation_score = round(metrics["confirmationScore"])
            directional_active_force = active_force >= 15 if direction == "long" else active_force <= -15
            directional_vwap_confirmed = (
                above_vwap and (vwap_up or retest_confirmed or metrics["fiveMinuteBreakout"] or metrics["fiveMinuteBollingerRetest"])
                if direction == "long"
                else (not above_vwap) and ((not vwap_up) or retest_confirmed or metrics["fiveMinuteBreakdown"])
            )
            three_gate_aligned = (
                three_gate_decision is not None
                and three_gate_decision.direction == direction
            )
            three_gate_opposed = (
                three_gate_decision is not None
                and three_gate_decision.direction != direction
            )
            three_gate_invalidated = bool(opening_retest is not None and opening_retest.invalidated)
            technical_confirmed = (
                bool(metrics["qualified"])
                and directional_setup
                and directional_vwap_confirmed
                and directional_active_force
                and confidence >= MIN_OFFICIAL_CONFIDENCE_SCORE
                and confirmation_score >= MIN_OFFICIAL_CONFIRMATION_SCORE
                and not chase_blocked
            )
            confirmed = technical_confirmed
            entry_confirmation_mode = "vwap_fallback"
            entry_confirmation_mode_label = (
                "VWAP＋5 分 K＋大單模式"
                if confirmed
                else "等待 VWAP＋5 分 K＋大單確認"
            )
            if chase_blocked:
                action = (
                    "禁止追多，等待完整 5 分 K 拉回確認"
                    if direction == "long"
                    else "禁止追空，等待完整 5 分 K 反彈確認"
                )
            elif direction == "long":
                action = (
                    "VWAP 回測確認買進"
                    if confirmed and retest_confirmed
                    else "5 分 K 突破買進"
                    if confirmed and metrics["fiveMinuteBreakout"]
                    else "5 分 K 布林回測買進"
                    if confirmed and metrics["fiveMinuteBollingerRetest"]
                    else "5 分 K 順勢買進"
                    if confirmed
                    else "等待 5 分 K 多方確認"
                )
            else:
                action = (
                    "VWAP 反彈轉弱放空"
                    if confirmed and retest_confirmed
                    else "5 分 K 跌破放空"
                    if confirmed and metrics["fiveMinuteBreakdown"]
                    else "5 分 K 順勢放空"
                    if confirmed
                    else "等待 5 分 K 空方確認"
                )
            day_range = max(quote.high - quote.low, price * .008)
            risk_distance = min(price * .025, max(price * .008, day_range * .18))
            planned_entry = price
            entry_min = planned_entry * (.998 if direction == "long" else .997)
            entry_max = planned_entry * (1.002 if direction == "long" else 1.001)
            stop_loss = planned_entry - risk_distance if direction == "long" else planned_entry + risk_distance
            target_1 = planned_entry + risk_distance * 1.5 if direction == "long" else planned_entry - risk_distance * 1.5
            target_2 = planned_entry + risk_distance * 2.5 if direction == "long" else planned_entry - risk_distance * 2.5
            spread_percentage = (
                (quote.best_ask - quote.best_bid) / price * 100
                if quote.best_ask is not None and quote.best_bid is not None and price
                else 999
            )
            exact_trade = quote.source == "TWSE MIS"
            official_strategy = (
                confirmed
                and bool(metrics["qualified"])
                and exact_trade
                and quote.is_realtime
            )
            data_notice = (
                LIVE_DATA_NOTICE
                if official_strategy
                else f"價格取自 {quote.source}；正在累積實際行情樣本，暫不產生正式訊號。"
            )
            warnings: list[str] = []
            if not metrics["qualified"]:
                warnings.append(str(metrics["qualificationMessage"]))
            if quote.source == "TWSE MIS 五檔參考價":
                warnings.append("目前為五檔參考價，等待最新成交價")
            if chase_blocked:
                warnings.append(str(timing["reason"]))
            if metrics["fiveMinuteReady"] and direction == "long" and not directional_setup:
                warnings.append("尚未符合 VWAP＋5 分 K 均線向上的多方結構")
            if metrics["fiveMinuteReady"] and direction == "short" and not directional_setup:
                warnings.append("尚未符合 VWAP＋5 分 K 均線向下的空方結構")
            if not volume_accelerating:
                warnings.append("近期量能尚未明顯增加")
            reasons = [
                entry_confirmation_mode_label,
                (
                    f"三關價參考：{three_gate_decision.status}（上 {three_gate.upper:,.2f}／中 {three_gate.middle:,.2f}／下 {three_gate.lower:,.2f}），不作進場卡控"
                    if three_gate_decision is not None and three_gate is not None
                    else "三關價未載入；當沖進場不再等待三關價"
                ),
                f"價格{'站上' if above_vwap else '跌破'}監控期間 VWAP {vwap:,.2f}",
                f"1 分鐘趨勢 {trend_1m:+.2f}%",
                f"5 分鐘趨勢 {trend_5m:+.2f}%",
                f"主動買賣力道推估 {active_force:+.0f}",
                "突破監控區間高點" if breakout else "跌破監控區間低點" if breakdown else "尚未突破監控區間",
                f"價格{'站上' if metrics['aboveOpen'] else '跌破'}開盤價 {quote.open:,.2f}",
                "5 分 K 低點墊高" if metrics["fiveMinuteHigherLows"] else "5 分 K 頭頭低" if metrics["fiveMinuteLowerHighs"] else "5 分 K 結構尚未確認",
                f"5 分 K MA3 / MA5：{metrics['fiveMinuteMaFast']:.2f} / {metrics['fiveMinuteMaSlow']:.2f}",
                f"日內區間位置 {timing['rangePositionPercent']:.1f}%（0% 為最低、100% 為最高）",
                "完整 5 分 K 回測已確認" if retest_confirmed else "尚未完成 5 分 K 回測確認",
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
                "dailyChaseBlocked": daily_chase_blocked,
                "chaseBlocked": chase_blocked,
                "rangePositionPercent": timing["rangePositionPercent"],
                "vwapDeviationPercent": timing["vwapDeviationPercent"],
                "extremeRangeBlocked": timing["extremeRangeBlocked"],
                "entryRetestRequired": timing["retestRequired"],
                "entryRetestConfirmed": timing["retestConfirmed"],
                "entryTimingStatus": timing["reason"],
                "stopDistancePercent": round(risk_distance / price * 100, 2),
                "marketAlignment": market_alignment,
                "confirmationScore": confirmation_score,
                "volumeScore": round(metrics["volumeScore"]),
                "activeForce": round(abs(active_force)),
                "industryScore": 0,
                "liquidityScore": min(100, round(quote.volume / 50_000)),
                "reasons": reasons,
                "warnings": warnings,
                "quoteTimestamp": quote.quote_timestamp,
                "dataSource": quote.source,
                "dataMode": "official" if official_strategy else "warming_up",
                "dataNotice": data_notice,
                "quoteIsRealtime": quote.is_realtime,
                "bestBid": quote.best_bid,
                "bestAsk": quote.best_ask,
                "bidPrices": list(quote.bid_prices),
                "bidVolumes": list(quote.bid_volumes),
                "askPrices": list(quote.ask_prices),
                "askVolumes": list(quote.ask_volumes),
                "quoteStatus": "盤中行情" if quote.is_realtime else "最近有效行情／收盤",
                "status": "confirmed" if confirmed else "temporary",
                "liveSampleCount": len(history),
                "fiveMinuteBarCount": metrics["fiveMinuteBarCount"],
                "fiveMinuteStructure": (
                    "低點墊高" if metrics["fiveMinuteHigherLows"]
                    else "頭頭低" if metrics["fiveMinuteLowerHighs"]
                    else "尚未確認"
                ),
                "fiveMinuteMaFast": round(float(metrics["fiveMinuteMaFast"]), 2),
                "fiveMinuteMaSlow": round(float(metrics["fiveMinuteMaSlow"]), 2),
                "fiveMinuteBollingerMiddle": (
                    round(float(metrics["fiveMinuteBollingerMiddle"]), 2)
                    if metrics["fiveMinuteBollingerMiddle"] is not None else None
                ),
                "fiveMinuteSetup": metrics["fiveMinuteSetup"],
                "entryConfirmationMode": entry_confirmation_mode,
                "entryConfirmationModeLabel": entry_confirmation_mode_label,
                "threeGateFallback": confirmed,
                "threeGateAligned": three_gate_aligned,
                "threeGateOpposed": three_gate_opposed,
                "threeGateReady": three_gate is not None,
                "threeGate": (
                    {
                        "sourceDate": three_gate.source_date,
                        "upper": three_gate.upper,
                        "middle": three_gate.middle,
                        "lower": three_gate.lower,
                    }
                    if three_gate is not None else None
                ),
                "threeGateDirection": three_gate_decision.direction if three_gate_decision else None,
                "threeGateLevel": three_gate_decision.level if three_gate_decision else None,
                "threeGatePosition": three_gate_decision.position if three_gate_decision else None,
                "threeGateCrossed": three_gate_decision.crossed if three_gate_decision else False,
                "threeGateStatus": three_gate_decision.status if three_gate_decision else "三關價資料載入中",
                "threeGateOpeningPattern": opening_retest.pattern if opening_retest else None,
                "threeGateRetestRequired": opening_retest.required if opening_retest else False,
                "threeGateRetestTouched": opening_retest.touched if opening_retest else False,
                "threeGateRetestReady": opening_retest.ready if opening_retest else False,
                "threeGateInvalidated": three_gate_invalidated,
                "threeGateEntryLevel": opening_retest.level if opening_retest else None,
                "threeGateEntryStatus": opening_retest.status if opening_retest else "三關價僅供參考",
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
                "fiveMinuteReady": False,
                "fiveMinuteBarCount": 0,
                "fiveMinuteLastCompletedOpen": None,
                "fiveMinuteLastCompletedClose": None,
                "fiveMinuteMinCompletedClose": None,
                "fiveMinuteMaxCompletedClose": None,
                "fiveMinuteHigherLows": False,
                "fiveMinuteLowerHighs": False,
                "fiveMinuteMaFast": 0.0,
                "fiveMinuteMaSlow": 0.0,
                "fiveMinuteMaRising": False,
                "fiveMinuteMaFalling": False,
                "aboveOpen": False,
                "belowOpen": False,
                "fiveMinuteBullish": False,
                "fiveMinuteBearish": False,
                "fiveMinuteBreakout": False,
                "fiveMinuteBreakdown": False,
                "fiveMinuteBollingerMiddle": None,
                "fiveMinuteBollingerRetest": False,
                "fiveMinuteLongRetest": False,
                "fiveMinuteShortRetest": False,
                "fiveMinuteLongSetup": False,
                "fiveMinuteShortSetup": False,
                "fiveMinuteBearishExit": False,
                "fiveMinuteSetup": "等待 5 分 K 暖機",
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

        five_minute_bars: list[dict[str, Any]] = []
        previous_exact: OfficialStockQuote | None = None
        for sample in exact_samples:
            sample_time = datetime.fromisoformat(sample.quote_timestamp)
            bucket = sample_time.replace(
                minute=(sample_time.minute // 5) * 5,
                second=0,
                microsecond=0,
            )
            delta_volume = (
                max(0, sample.volume - previous_exact.volume)
                if previous_exact is not None else 0
            )
            if not five_minute_bars or five_minute_bars[-1]["time"] != bucket:
                five_minute_bars.append({
                    "time": bucket,
                    "open": sample.price,
                    "high": sample.price,
                    "low": sample.price,
                    "close": sample.price,
                    "volume": delta_volume,
                })
            else:
                bar = five_minute_bars[-1]
                bar["high"] = max(float(bar["high"]), sample.price)
                bar["low"] = min(float(bar["low"]), sample.price)
                bar["close"] = sample.price
                bar["volume"] = int(bar["volume"]) + delta_volume
            previous_exact = sample

        # The latest bucket is still forming. Signals only use completed 5-minute bars.
        completed_bars = five_minute_bars[:-1] if five_minute_bars else []
        completed_closes = [float(bar["close"]) for bar in completed_bars]
        five_minute_ready = len(completed_bars) >= 5
        recent_three = completed_bars[-3:]
        higher_lows = len(recent_three) == 3 and all(
            float(recent_three[index]["low"]) < float(recent_three[index + 1]["low"])
            for index in range(2)
        )
        lower_highs = len(recent_three) == 3 and all(
            float(recent_three[index]["high"]) > float(recent_three[index + 1]["high"])
            for index in range(2)
        )
        ma_fast = (
            sum(completed_closes[-3:]) / 3
            if len(completed_closes) >= 3 else latest.price
        )
        ma_slow = (
            sum(completed_closes[-5:]) / 5
            if len(completed_closes) >= 5 else ma_fast
        )
        previous_ma_fast = (
            sum(completed_closes[-4:-1]) / 3
            if len(completed_closes) >= 4 else ma_fast
        )
        ma_rising = len(completed_closes) >= 4 and ma_fast > previous_ma_fast
        ma_falling = len(completed_closes) >= 4 and ma_fast < previous_ma_fast
        last_completed = completed_bars[-1] if completed_bars else None
        opening_price = latest.open if latest.open > 0 else same_day[0].price
        above_open = latest.price > opening_price
        below_open = latest.price < opening_price
        five_minute_breakout = (
            len(recent_three) == 3
            and latest.price > max(float(bar["high"]) for bar in recent_three)
        )
        five_minute_breakdown = (
            len(recent_three) == 3
            and latest.price < min(float(bar["low"]) for bar in recent_three)
        )
        bollinger_middle: float | None = None
        bollinger_retest = False
        if len(completed_closes) >= 10:
            window = completed_closes[-10:]
            bollinger_middle = sum(window) / len(window)
            variance = sum((value - bollinger_middle) ** 2 for value in window) / len(window)
            _bollinger_upper = bollinger_middle + 2 * math.sqrt(variance)
            _bollinger_lower = bollinger_middle - 2 * math.sqrt(variance)
            bollinger_retest = (
                float(last_completed["low"]) <= bollinger_middle
                and float(last_completed["close"]) >= bollinger_middle
                and latest.price >= bollinger_middle
            )
        five_minute_long_retest = bool(
            five_minute_ready
            and last_completed is not None
            and float(last_completed["low"]) <= ma_fast * 1.002
            and float(last_completed["close"]) >= ma_fast
            and float(last_completed["close"]) >= float(last_completed["open"])
            and latest.price >= ma_fast
            and trend_1m >= 0
        )
        five_minute_short_retest = bool(
            five_minute_ready
            and last_completed is not None
            and float(last_completed["high"]) >= ma_fast * .998
            and float(last_completed["close"]) <= ma_fast
            and float(last_completed["close"]) <= float(last_completed["open"])
            and latest.price <= ma_fast
            and trend_1m <= 0
        )
        five_minute_bullish = (
            five_minute_ready
            and above_open
            and ma_fast > ma_slow
            and ma_rising
            and (higher_lows or five_minute_breakout or bollinger_retest)
        )
        five_minute_bearish = (
            five_minute_ready
            and below_open
            and ma_fast < ma_slow
            and ma_falling
            and (lower_highs or five_minute_breakdown)
        )
        five_minute_setup = (
            "突破" if five_minute_bullish and five_minute_breakout
            else "布林中線回測" if five_minute_bullish and bollinger_retest
            else "低點墊高順勢" if five_minute_bullish and higher_lows
            else "空方轉弱" if five_minute_bearish
            else "等待多方確認"
        )
        qualified = (
            len(exact_samples) >= 12
            and span_seconds >= 180
            and weighted_volume > 0
            and five_minute_ready
        )
        qualification_message = (
            "實際行情與 5 分 K 樣本已完成暖機"
            if qualified
            else f"5 分 K 暖機中：{len(completed_bars)}/5 根完成 K；實際行情 {len(exact_samples)}/12 筆"
        )
        sample_quality = min(
            100,
            len(exact_samples) / 12 * 35
            + span_seconds / 180 * 25
            + len(completed_bars) / 5 * 40,
        )
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
            "fiveMinuteReady": five_minute_ready,
            "fiveMinuteBarCount": len(completed_bars),
            "fiveMinuteLastCompletedOpen": (
                float(last_completed["open"]) if last_completed is not None else None
            ),
            "fiveMinuteLastCompletedClose": (
                float(last_completed["close"]) if last_completed is not None else None
            ),
            "fiveMinuteMinCompletedClose": min(completed_closes) if completed_closes else None,
            "fiveMinuteMaxCompletedClose": max(completed_closes) if completed_closes else None,
            "fiveMinuteHigherLows": higher_lows,
            "fiveMinuteLowerHighs": lower_highs,
            "fiveMinuteMaFast": ma_fast,
            "fiveMinuteMaSlow": ma_slow,
            "fiveMinuteMaRising": ma_rising,
            "fiveMinuteMaFalling": ma_falling,
            "aboveOpen": above_open,
            "belowOpen": below_open,
            "fiveMinuteBullish": five_minute_bullish,
            "fiveMinuteBearish": five_minute_bearish,
            "fiveMinuteBreakout": five_minute_breakout,
            "fiveMinuteBreakdown": five_minute_breakdown,
            "fiveMinuteBollingerMiddle": bollinger_middle,
            "fiveMinuteBollingerRetest": bollinger_retest,
            "fiveMinuteLongRetest": five_minute_long_retest,
            "fiveMinuteShortRetest": five_minute_short_retest,
            "fiveMinuteLongSetup": five_minute_bullish,
            "fiveMinuteShortSetup": five_minute_bearish,
            "fiveMinuteBearishExit": five_minute_bearish,
            "fiveMinuteSetup": five_minute_setup,
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
        local_now = now.astimezone(TAIPEI)
        market_session_open = (
            local_now.weekday() < 5
            and time(9, 0) <= local_now.time() < time(13, 30)
        )
        with self._lock:
            scenario = self._scenario
            quotes = dict(self._official_quotes)
            histories = {
                symbol: list(values)
                for symbol, values in self._quote_history.items()
            }
            stock_universe_symbols = tuple(stock.symbol for stock in self._stock_universe)
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
        candidate_universe_count = len(stock_universe_symbols) or len(pool_quotes)
        universe_pool_quotes = [
            quotes[symbol]
            for symbol in stock_universe_symbols
            if symbol in quotes
        ] if stock_universe_symbols else pool_quotes
        fresh_pool_quotes = [
            quote for quote in universe_pool_quotes
            if quote.source == "TWSE MIS"
            and quote.is_realtime
            and _quote_delay_seconds(now, quote) <= DEGRADED_INDEX_DELAY_SECONDS
        ]
        quote_coverage_count = len(fresh_pool_quotes)
        quote_coverage_ratio = quote_coverage_count / max(1, candidate_universe_count)
        data_quality_mode = "live"
        data_quality_warning: str | None = None
        formal_block_reason: str | None = None
        if live_market and index_quote is not None:
            delay = _quote_delay_seconds(now, index_quote)
            if (
                index_quote.source == "TWSE MIS"
                and index_quote.is_realtime
                and delay <= LIVE_QUOTE_MAX_DELAY_SECONDS
            ):
                data_status = "normal"
                data_quality_mode = "live"
            elif (
                index_quote.source == "TWSE MIS"
                and delay <= DEGRADED_INDEX_DELAY_SECONDS
                and quote_coverage_ratio >= MIN_DEGRADED_POOL_COVERAGE_RATIO
                and quote_coverage_count > 0
            ):
                data_status = "normal"
                data_quality_mode = "index_delay"
                data_quality_warning = (
                    f"加權指數延遲 {delay:.0f} 秒；個股即時報價覆蓋 "
                    f"{quote_coverage_count}/{candidate_universe_count}，正式訊號仍以個股逐檔風控。"
                )
            elif index_quote.source == "TWSE MIS" and delay <= SOURCE_INTERRUPTION_SECONDS:
                data_status = "severe_delay"
                data_quality_mode = "index_severe_delay"
                formal_block_reason = (
                    f"加權指數延遲 {delay:.0f} 秒，且個股即時報價覆蓋 "
                    f"{quote_coverage_count}/{candidate_universe_count}；暫停正式訊號。"
                )
            else:
                data_status = "source_error"
                data_quality_mode = "source_error"
                formal_block_reason = "TWSE MIS 行情來源異常；暫停正式訊號。"
            if not market_session_open:
                data_status = "closed"
                data_quality_mode = "closed"
                data_quality_warning = None
                formal_block_reason = None
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
            if data_quality_warning:
                reasons.append(data_quality_warning)
            data_quality_penalty = (
                0
                if data_quality_mode == "live"
                else 12
                if data_quality_mode == "index_delay"
                else 70
            )
            environment_score = max(0, min(100, 70 + abs(score) * .2 - data_quality_penalty))
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
                "environmentLabel": (
                    "降級可用" if data_quality_mode == "index_delay"
                    else "適合交易" if data_status == "normal"
                    else "今日已收盤" if data_status == "closed"
                    else "停止新訊號"
                ),
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
                "dataQualityMode": data_quality_mode,
                "dataQualityWarning": data_quality_warning,
                "formalBlockReason": formal_block_reason,
                "quoteCoverageRatio": round(quote_coverage_ratio, 4),
                "quoteCoverageCount": quote_coverage_count,
                "candidateUniverseCount": candidate_universe_count,
                "dataSource": "TWSE MIS 實際行情＋抽樣 Tick Rule 推估",
                "marketOpen": market_session_open,
                "session": "09:00～13:30",
                "updatedAt": now.isoformat(),
                "metrics": live_metrics,
                "mode": "official" if index_quote.source == "TWSE MIS" else "warming_up",
                "dataNotice": LIVE_DATA_NOTICE,
            }
        return {
            "direction": direction,
            "directionLabel": "偏多" if direction == "bull" else "資料異常",
            "score": score,
            "environmentScore": 78 if data_status == "normal" and market_session_open else 0,
            "environmentLabel": (
                "今日已收盤" if not market_session_open
                else "適合交易" if data_status == "normal"
                else "停止新訊號"
            ),
            "preferredDirection": "做多",
            "shortRestriction": "只允許高信心弱勢股",
            "risk": "中",
            "longPermission": 85,
            "shortPermission": 45,
            "suitableStrategies": ["突破買進", "回踩買進", "弱勢股跌破放空"],
            "forbiddenStrategies": ["無量追價", "急跌追空"],
            "reasons": ["指數站上 VWAP", "上漲家數高於下跌家數", "大單買盤增加", "5 分 K 均線偏多"],
            "dataStatus": "closed" if not market_session_open else data_status,
            "dataDelaySeconds": delay,
            "dataQualityMode": "closed" if not market_session_open else "demo",
            "dataQualityWarning": None,
            "formalBlockReason": None if data_status == "normal" else "行情資料異常；暫停正式訊號。",
            "quoteCoverageRatio": 0,
            "quoteCoverageCount": 0,
            "candidateUniverseCount": 0,
            "dataSource": (
                "TWSE MIS 個股報價＋Mock 大盤策略"
                if has_official_quotes
                else "Mock Streaming Data"
            ),
            "marketOpen": market_session_open,
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
            stock_universe = tuple(self._stock_universe)
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
        template_by_symbol = {
            str(item["symbol"]): item
            for item in templates
        }
        templates = []
        for stock in stock_universe:
            item = dict(template_by_symbol.get(stock.symbol) or _stock_seed_signal(stock, wave))
            item["stockName"] = stock.name
            item["market"] = stock.market
            item["themes"] = list(stock.themes)
            item["momentumUniverseMember"] = True
            templates.append(item)
        if scenario == "long_signal" and templates:
            templates[0]["action"] = "突破買進"
            templates[0]["confidenceScore"] = 92
        if scenario == "short_signal" and len(templates) > 1:
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
        with self._lock:
            quote = self._official_quotes.get(symbol)
        return None if quote is None else float(quote.price)

    def quote_history_for(self, symbol: str, limit: int = 240) -> list[dict[str, object]]:
        """Return today's compact intraday price series for lightweight charts."""
        bounded_limit = max(1, min(limit, self._LIVE_HISTORY_LIMIT))
        today = self._now().astimezone(TAIPEI).date()
        with self._lock:
            history = list(self._quote_history.get(symbol, []))
        points: list[dict[str, object]] = []
        for quote in history:
            try:
                timestamp = datetime.fromisoformat(quote.quote_timestamp)
            except (TypeError, ValueError):
                continue
            if timestamp.astimezone(TAIPEI).date() != today:
                continue
            points.append({
                "timestamp": quote.quote_timestamp,
                "price": float(quote.price),
                "isRealtime": bool(quote.is_realtime),
            })
        return points[-bounded_limit:]

    def position_risk_for(self, symbol: str) -> dict[str, str] | None:
        """Return a long-position exit when completed 5-minute bars turn bearish."""
        with self._lock:
            history = list(self._quote_history.get(symbol, []))
        metrics = self._live_metrics(history)
        if not metrics["fiveMinuteBearishExit"]:
            return None
        return {
            "level": "important",
            "action": "5 分 K 轉弱，全部賣出",
            "reason": "跌破開盤價、5 分 K 短均線向下，且形成頭頭低或跌破近期低點",
        }

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
