from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import LimitUpAiNotification, LimitUpAiPosition, LimitUpAiSettings, LimitUpAiSnapshot, LimitUpAiTrade
from .chip_flow_alerts import electronic_chip_flow_alert_monitor, enrich_day_trading_large_order_confirmation
from .chip_flow_repository import ChipFlowRepository
from .day_trading import day_trading_engine
from .day_trading_restrictions import day_trading_restrictions
from .official_market_data import StockQuoteRequest, official_market_data_provider
from .popular_stock_universe import OfficialPopularStockProvider


TAIPEI = ZoneInfo("Asia/Taipei")
SNAPSHOT_LIMIT = 20
FULL_MARKET_SIGNAL_CACHE_SECONDS = 10
FULL_MARKET_QUOTE_BATCH_SIZE = 80
FULL_MARKET_SIGNAL_RETENTION_SECONDS = 120
POPULAR_UNIVERSE_CACHE_SECONDS = 90
_FULL_MARKET_SIGNAL_CACHE: tuple[datetime, list[dict[str, Any]]] | None = None
_FULL_MARKET_SIGNAL_BY_SYMBOL_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}
_FULL_MARKET_QUOTE_CURSOR = 0
_POPULAR_UNIVERSE_CACHE: tuple[datetime, tuple[Any, ...]] | None = None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dec(value: float | int) -> Decimal:
    return Decimal(str(round(float(value), 4)))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _scale(value: float, low: float, high: float, points: float) -> float:
    if high <= low:
        return 0.0
    return _clamp((value - low) / (high - low) * points, 0, points)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _trading_date(now: datetime) -> date:
    return now.astimezone(TAIPEI).date()


def _notification_payload(item: LimitUpAiNotification) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": item.notification_type,
        "priority": item.priority,
        "title": item.title,
        "message": item.message,
        "symbol": item.symbol,
        "stockName": item.stock_name,
        "setupType": item.setup_type,
        "price": float(item.price) if item.price is not None else None,
        "quantity": item.quantity,
        "amount": float(item.amount) if item.amount is not None else None,
        "realizedPnl": float(item.realized_pnl) if item.realized_pnl is not None else None,
        "score": float(item.score) if item.score is not None else None,
        "reason": item.reason,
        "isRead": item.is_read,
        "readAt": item.read_at.isoformat() if item.read_at else None,
        "createdAt": item.created_at.isoformat(),
    }


def _notify(
    db: Session,
    *,
    user_id: str,
    dedupe_key: str,
    notification_type: str,
    title: str,
    message: str,
    reason: str,
    created_at: datetime,
    priority: int = 4,
    symbol: str | None = None,
    stock_name: str | None = None,
    setup_type: str | None = None,
    price: float | Decimal | None = None,
    quantity: int | None = None,
    amount: float | Decimal | None = None,
    realized_pnl: float | Decimal | None = None,
    score: float | Decimal | None = None,
) -> LimitUpAiNotification | None:
    exists = db.scalar(select(LimitUpAiNotification.id).where(
        LimitUpAiNotification.user_id == user_id,
        LimitUpAiNotification.dedupe_key == dedupe_key,
    ))
    if exists is not None:
        return None
    item = LimitUpAiNotification(
        user_id=user_id,
        dedupe_key=dedupe_key[:180],
        notification_type=notification_type[:30],
        priority=priority,
        title=title[:160],
        message=message,
        symbol=symbol[:12] if symbol else None,
        stock_name=stock_name[:80] if stock_name else None,
        setup_type=setup_type[:40] if setup_type else None,
        price=_dec(float(price)) if price is not None else None,
        quantity=quantity,
        amount=_dec(float(amount)) if amount is not None else None,
        realized_pnl=_dec(float(realized_pnl)) if realized_pnl is not None else None,
        score=_dec(float(score)) if score is not None else None,
        reason=reason,
        created_at=created_at.astimezone(UTC),
    )
    db.add(item)
    return item


def ensure_limit_up_settings(db: Session, user_id: str, now: datetime | None = None) -> LimitUpAiSettings:
    item = db.scalar(select(LimitUpAiSettings).where(LimitUpAiSettings.user_id == user_id))
    if item is not None:
        return item
    item = LimitUpAiSettings(user_id=user_id, updated_at=now or datetime.now(UTC))
    db.add(item)
    db.flush()
    return item


def settings_payload(item: LimitUpAiSettings) -> dict[str, Any]:
    return {
        "capital": float(item.capital),
        "minPrice": float(item.min_price),
        "maxPrice": float(item.max_price),
        "minAverageTurnover20d": float(item.min_average_turnover_20d),
        "minVolumeRatio20d": float(item.min_volume_ratio_20d),
        "firstPositionPct": float(item.first_position_pct),
        "maxPositionPct": float(item.max_position_pct),
        "maxPositions": item.max_positions,
        "maxLossPerTradePct": float(item.max_loss_per_trade_pct),
        "maxDailyLossPct": float(item.max_daily_loss_pct),
        "maxConsecutiveStops": item.max_consecutive_stops,
        "overnightTotalPct": float(item.overnight_total_pct),
        "overnightSinglePct": float(item.overnight_single_pct),
        "excludeLockedLimitUp": item.exclude_locked_limit_up,
        "soundEnabled": item.sound_enabled,
        "updatedAt": item.updated_at.isoformat(),
    }


def _limit_up_price(previous_close: float) -> float:
    return round(previous_close * 1.1, 2) if previous_close > 0 else 0.0


def _estimated_volume_ratio(signal: dict[str, Any]) -> float:
    if signal.get("volumeRatio20d") is not None:
        return _num(signal.get("volumeRatio20d"))
    return round(max(0.0, _num(signal.get("volumeScore")) / 50), 2)


def _order_book_score(signal: dict[str, Any]) -> tuple[float, list[str]]:
    bid_volumes = [_num(value) for value in signal.get("bidVolumes", []) if _num(value) > 0]
    ask_volumes = [_num(value) for value in signal.get("askVolumes", []) if _num(value) > 0]
    spread = _num(signal.get("spreadPercentage"), 99)
    reasons: list[str] = []
    if bid_volumes and ask_volumes:
        bid_total = sum(bid_volumes[:5])
        ask_total = sum(ask_volumes[:5])
        support = bid_total / max(1.0, ask_total)
        score = _scale(support, 0.6, 2.2, 6) + _scale(_num(signal.get("largeOrderForce")), 0, 300, 4)
        reasons.append(f"五檔委買/委賣 {support:.2f} 倍")
        return _clamp(score, 0, 10), reasons
    score = _scale(_num(signal.get("largeOrderForce")), 0, 300, 7) + max(0, 3 - spread * 3)
    reasons.append("五檔量不足，使用大單與價差估算")
    return _clamp(score, 0, 10), reasons


def score_limit_up_candidate(
    signal: dict[str, Any],
    settings: LimitUpAiSettings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    local = current.astimezone(TAIPEI)
    price = _num(signal.get("price"))
    previous_close = _num(signal.get("previousClose"))
    change = _num(signal.get("changePercent"))
    volume = _num(signal.get("volume"))
    turnover = _num(signal.get("turnover"), price * volume)
    estimated_average_turnover = _num(signal.get("averageTurnover20d"), turnover)
    volume_ratio = _estimated_volume_ratio(signal)
    limit_price = _limit_up_price(previous_close)
    limit_distance = (limit_price - price) / limit_price * 100 if limit_price else 99.0
    vwap_ok = "站上" in str(signal.get("vwapStatus", "")) or _num(signal.get("vwapDeviationPercent")) >= 0
    higher_lows = "低點" in str(signal.get("fiveMinuteStructure", "")) or bool(signal.get("fiveMinuteLongSetup"))
    retest_ok = bool(signal.get("entryRetestConfirmed") or signal.get("fiveMinuteLongRetest"))
    large_buy = bool(signal.get("largeOrderContinuousBuy")) or _num(signal.get("largeOrderForce")) >= 80
    not_locked = limit_distance >= 0.3 and change < 9.7

    failures: list[str] = []
    warnings: list[str] = []
    if price < float(settings.min_price) or price > float(settings.max_price):
        failures.append(f"股價不在 {float(settings.min_price):.0f}～{float(settings.max_price):.0f} 元")
    if day_trading_restrictions.is_disposed(signal.get("symbol")) or signal.get("tradeRestricted"):
        failures.append("處置/限制交易股票排除")
    if estimated_average_turnover < float(settings.min_average_turnover_20d):
        failures.append("20日均成交金額或替代成交金額不足 1 億")
    if volume_ratio < float(settings.min_volume_ratio_20d):
        failures.append("預估量比未達 1.8 倍")
    if not signal.get("quoteIsRealtime", False):
        warnings.append("行情非即時，只列觀察")
    if _num(signal.get("spreadPercentage"), 99) > 1.0:
        failures.append("買賣價差過大，可能不好成交")
    if settings.exclude_locked_limit_up and not not_locked:
        failures.append("已接近/鎖住漲停，第一版不追無法合理成交")

    order_score, order_reasons = _order_book_score(signal)
    components = {
        "即時量價動能": round(
            _scale(change, 1, 8, 9)
            + _scale(volume_ratio, 1, 3, 8)
            + _scale(_num(signal.get("confirmationScore")), 30, 90, 8),
            2,
        ),
        "族群強度": round(_scale(_num(signal.get("industryScore"), _num(signal.get("marketAlignment"))), 20, 90, 15), 2),
        "突破型態": round(
            (7 if signal.get("threeGateCrossed") else 0)
            + (5 if signal.get("fiveMinuteBreakout") else 0)
            + _scale(_num(signal.get("rangePositionPercent")), 50, 96, 3),
            2,
        ),
        "分時結構": round((6 if vwap_ok else 0) + (5 if higher_lows else 0) + (4 if retest_ok else 0), 2),
        "委買與成交力道": round(order_score, 2),
        "大盤環境": round(_scale(_num(signal.get("marketAlignment")), 20, 90, 10), 2),
        "題材延續性": 5.0 if signal.get("momentumUniverseMember", True) else 2.0,
    }
    risk_deduct = 0.0
    if change >= 7 and not (1 <= limit_distance <= 3):
        risk_deduct += 2
        warnings.append("漲幅已高，需等回踩或漲停前攻擊條件")
    if signal.get("dailyChaseBlocked") or signal.get("chaseBlocked"):
        risk_deduct += 2
        warnings.append("原當沖風控判定追價風險")
    if price < _num(signal.get("open"), price) and change > 3:
        risk_deduct += 1
        warnings.append("爆量後未守開盤價")
    score = _clamp(sum(components.values()) - min(5.0, risk_deduct), 0, 100)

    setup = "等待型態"
    setup_label = "等待買點"
    setup_reasons: list[str] = []
    minutes = local.hour * 60 + local.minute
    if 545 <= minutes <= 570 and 1 <= change <= 5 and vwap_ok and bool(signal.get("fiveMinuteBreakout")):
        setup, setup_label = "opening_breakout", "A 開盤強勢突破"
        setup_reasons.append("開盤 5～30 分鐘站上 VWAP 並突破")
    elif change >= 3 and vwap_ok and retest_ok and bool(signal.get("volumeStatus")):
        setup, setup_label = "midday_consolidation_breakout", "B 盤中整理再突破"
        setup_reasons.append("先上漲後回踩不破，再放量轉強")
    elif 1 <= limit_distance <= 3 and large_buy and vwap_ok:
        setup, setup_label = "pre_limit_attack", "C 漲停前攻擊"
        setup_reasons.append("距漲停 1～3%，連續大單買入")

    actionable = not failures and setup != "等待型態" and score >= 85 and large_buy
    if score >= 85:
        category = "attack"
        category_label = "漲停攻擊候選"
    elif score >= 75:
        category = "monitor"
        category_label = "強勢監控"
    elif score >= 65:
        category = "watch"
        category_label = "只觀察"
    else:
        category = "rejected"
        category_label = "剔除"

    stop_loss = min(price * 0.99, _num(signal.get("low"), price) * 0.995)
    target1 = price * 1.02
    target2 = price * 1.04
    rr = (target1 - price) / max(0.01, price - stop_loss)
    if rr < 2:
        warnings.append("預期風險報酬比未達 2:1")

    return {
        "id": str(signal.get("id") or f"{signal.get('symbol')}-{current.timestamp()}"),
        "symbol": str(signal.get("symbol") or ""),
        "stockName": str(signal.get("stockName") or ""),
        "market": str(signal.get("market") or ""),
        "rank": 999,
        "price": round(price, 2),
        "previousClose": round(previous_close, 2),
        "limitUpPrice": round(limit_price, 2),
        "limitDistancePercent": round(limit_distance, 2),
        "changePercent": round(change, 2),
        "volume": int(volume),
        "turnover": round(turnover),
        "estimatedAverageTurnover20d": round(estimated_average_turnover),
        "estimatedVolumeRatio20d": round(volume_ratio, 2),
        "score": round(score, 2),
        "category": category,
        "categoryLabel": category_label,
        "setupType": setup,
        "setupLabel": setup_label,
        "actionable": actionable,
        "stopLoss": round(stop_loss, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "riskRewardRatio": round(rr, 2),
        "components": components,
        "riskDeduction": round(min(5.0, risk_deduct), 2),
        "largeOrderForce": _num(signal.get("largeOrderForce")),
        "largeOrderContinuousBuy": bool(signal.get("largeOrderContinuousBuy")),
        "largeOrderStatus": signal.get("largeOrderStatus"),
        "vwapStatus": signal.get("vwapStatus"),
        "fiveMinuteStructure": signal.get("fiveMinuteStructure"),
        "orderBookEstimated": not (signal.get("bidVolumes") and signal.get("askVolumes")),
        "orderBook": {
            "bidPrices": signal.get("bidPrices", []),
            "bidVolumes": signal.get("bidVolumes", []),
            "askPrices": signal.get("askPrices", []),
            "askVolumes": signal.get("askVolumes", []),
        },
        "failures": failures,
        "warnings": [*warnings, *order_reasons],
        "reasons": [*setup_reasons, *order_reasons],
        "snapshotAt": current.isoformat(),
    }


def _rank(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        (item for item in candidates if item["category"] != "rejected"),
        key=lambda item: (
            item["score"],
            item["actionable"],
            -max(0.0, item["limitDistancePercent"]),
            item["largeOrderForce"],
        ),
        reverse=True,
    )
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
    return ranked


def _await_sync(coro):
    return asyncio.run(coro)


def _cached_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in rows]


def _popular_universe(now: datetime) -> tuple[Any, ...]:
    global _POPULAR_UNIVERSE_CACHE
    if (
        _POPULAR_UNIVERSE_CACHE is not None
        and now - _POPULAR_UNIVERSE_CACHE[0] <= timedelta(seconds=POPULAR_UNIVERSE_CACHE_SECONDS)
    ):
        return _POPULAR_UNIVERSE_CACHE[1]
    stocks = tuple(_await_sync(OfficialPopularStockProvider().fetch()))
    _POPULAR_UNIVERSE_CACHE = (now, stocks)
    return stocks


def _dedupe_stocks_by_symbol(stocks: tuple[Any, ...]) -> tuple[Any, ...]:
    deduped: dict[str, Any] = {}
    for stock in stocks:
        symbol = str(getattr(stock, "symbol", "") or "")
        if symbol and symbol not in deduped:
            deduped[symbol] = stock
    return tuple(deduped.values())


def _full_market_quote_slice(stocks: tuple[Any, ...]) -> tuple[Any, ...]:
    global _FULL_MARKET_QUOTE_CURSOR
    unique_stocks = _dedupe_stocks_by_symbol(stocks)
    if len(unique_stocks) <= FULL_MARKET_QUOTE_BATCH_SIZE:
        _FULL_MARKET_QUOTE_CURSOR = 0
        return unique_stocks
    start = _FULL_MARKET_QUOTE_CURSOR % len(unique_stocks)
    end = start + FULL_MARKET_QUOTE_BATCH_SIZE
    selected = (
        unique_stocks[start:end]
        if end <= len(unique_stocks)
        else (*unique_stocks[start:], *unique_stocks[:end - len(unique_stocks)])
    )
    _FULL_MARKET_QUOTE_CURSOR = end % len(unique_stocks)
    return tuple(selected)


def _quote_to_limit_up_signal(quote, market: str) -> dict[str, Any] | None:
    price = float(quote.price)
    previous_close = float(quote.previous_close)
    if price <= 0 or previous_close <= 0:
        return None
    limit_price = _limit_up_price(previous_close)
    limit_distance = (limit_price - price) / limit_price * 100 if limit_price else 99.0
    turnover = price * int(quote.volume)
    intraday_range = max(float(quote.high) - float(quote.low), price * 0.004)
    range_position = _clamp((price - float(quote.low)) / intraday_range * 100, 0, 100)
    volume_ratio_proxy = _clamp(turnover / 100_000_000, 0, 8)
    near_limit = 0 <= limit_distance <= 4.0
    strong_intraday = (
        quote.change_percent >= 3
        and range_position >= 65
        and turnover >= 80_000_000
    )
    if not (near_limit or strong_intraday):
        return None
    vwap_proxy_ok = price >= float(quote.open) or range_position >= 55
    large_order_force = _clamp(
        quote.change_percent * 22
        + volume_ratio_proxy * 28
        + max(0.0, 4.0 - limit_distance) * 24
        + (18 if range_position >= 75 else 0),
        0,
        360,
    )
    volume_score = _clamp(volume_ratio_proxy * 50, 0, 220)
    confirmation_score = _clamp(
        45
        + quote.change_percent * 4
        + volume_ratio_proxy * 6
        + (10 if near_limit else 0)
        + (8 if vwap_proxy_ok else 0),
        0,
        100,
    )
    return {
        "id": f"{quote.symbol}-limit-up-market",
        "symbol": quote.symbol,
        "stockName": quote.name,
        "market": market,
        "price": price,
        "previousClose": previous_close,
        "limitDistancePercent": round(limit_distance, 2),
        "open": float(quote.open),
        "high": float(quote.high),
        "low": float(quote.low),
        "changePercent": float(quote.change_percent),
        "volume": int(quote.volume),
        "turnover": round(turnover),
        "averageTurnover20d": max(turnover, 100_000_000 if turnover >= 100_000_000 else turnover),
        "volumeRatio20d": round(volume_ratio_proxy, 2),
        "volumeScore": volume_score,
        "volumeStatus": "量能放大估算" if volume_ratio_proxy >= 1.8 else "量能待放大",
        "confirmationScore": confirmation_score,
        "industryScore": 70 if near_limit else 55,
        "marketAlignment": 75 if quote.change_percent >= 3 else 60,
        "rangePositionPercent": round(range_position, 2),
        "vwapStatus": "站上VWAP估算" if vwap_proxy_ok else "VWAP估算偏弱",
        "vwapDeviationPercent": 0.6 if vwap_proxy_ok else -0.4,
        "fiveMinuteStructure": "高低點墊高估算" if range_position >= 60 else "分時結構待確認",
        "fiveMinuteBreakout": bool(near_limit or range_position >= 78),
        "fiveMinuteLongSetup": bool(range_position >= 60),
        "fiveMinuteLongRetest": bool(vwap_proxy_ok and quote.change_percent >= 3),
        "entryRetestConfirmed": bool(vwap_proxy_ok and range_position >= 65),
        "threeGateCrossed": bool(near_limit or quote.change_percent >= 4),
        "largeOrderForce": round(large_order_force, 2),
        "largeOrderContinuousBuy": large_order_force >= 100,
        "largeOrderDataAvailable": False,
        "largeOrderStatus": "全市場量價估算，等待大單明細確認",
        "spreadPercentage": (
            (float(quote.best_ask) - float(quote.best_bid)) / price * 100
            if quote.best_ask is not None and quote.best_bid is not None and price
            else 0.6
        ),
        "quoteIsRealtime": bool(quote.is_realtime),
        "bidPrices": list(quote.bid_prices),
        "bidVolumes": list(quote.bid_volumes),
        "askPrices": list(quote.ask_prices),
        "askVolumes": list(quote.ask_volumes),
        "quoteSource": quote.source,
        "quoteTimestamp": quote.quote_timestamp,
        "momentumUniverseMember": True,
    }


def _full_market_limit_up_signals(now: datetime) -> list[dict[str, Any]]:
    global _FULL_MARKET_SIGNAL_CACHE
    if (
        _FULL_MARKET_SIGNAL_CACHE is not None
        and now - _FULL_MARKET_SIGNAL_CACHE[0] <= timedelta(seconds=FULL_MARKET_SIGNAL_CACHE_SECONDS)
    ):
        return _cached_rows(_FULL_MARKET_SIGNAL_CACHE[1])
    stocks = _popular_universe(now)
    if not stocks:
        return []
    selected_stocks = _full_market_quote_slice(stocks)
    requests = [
        StockQuoteRequest(stock.symbol, stock.name, stock.market)
        for stock in selected_stocks
    ]
    quotes = _await_sync(official_market_data_provider.get_quotes(requests, force_refresh=True))
    by_symbol = {stock.symbol: stock for stock in selected_stocks}
    fresh_signals_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol, quote in quotes.items():
        stock = by_symbol.get(symbol)
        if stock is None:
            continue
        signal = _quote_to_limit_up_signal(quote, stock.market)
        if signal is not None:
            fresh_signals_by_symbol[symbol] = signal
    scanned_symbols = set(by_symbol)
    cutoff = now - timedelta(seconds=FULL_MARKET_SIGNAL_RETENTION_SECONDS)
    for symbol, (updated_at, _signal) in list(_FULL_MARKET_SIGNAL_BY_SYMBOL_CACHE.items()):
        if updated_at < cutoff or symbol in scanned_symbols:
            _FULL_MARKET_SIGNAL_BY_SYMBOL_CACHE.pop(symbol, None)
    for symbol, signal in fresh_signals_by_symbol.items():
        _FULL_MARKET_SIGNAL_BY_SYMBOL_CACHE[symbol] = (now, signal)
    signals = [dict(signal) for _updated_at, signal in _FULL_MARKET_SIGNAL_BY_SYMBOL_CACHE.values()]
    ranked = sorted(
        signals,
        key=lambda item: (
            item["changePercent"],
            -_num(item.get("limitDistancePercent"), 99),
            item["turnover"],
            item["largeOrderForce"],
        ),
        reverse=True,
    )
    _FULL_MARKET_SIGNAL_CACHE = (now, _cached_rows(ranked))
    return ranked


def scan_limit_up_candidates(
    db: Session,
    settings: LimitUpAiSettings,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = now or datetime.now(UTC)
    try:
        raw = _full_market_limit_up_signals(current)
    except Exception:
        raw = []
    if not raw:
        raw = day_trading_restrictions.filter_candidates(day_trading_engine.signals())
    enriched = enrich_day_trading_large_order_confirmation(
        raw,
        ChipFlowRepository(db),
        electronic_chip_flow_alert_monitor.rules,
        as_of=current,
    )
    candidates = _rank([score_limit_up_candidate(item, settings, now=current) for item in enriched])
    save_snapshots(db, candidates[:SNAPSHOT_LIMIT], current)
    return candidates


def save_snapshots(db: Session, candidates: list[dict[str, Any]], now: datetime) -> int:
    trading_date = _trading_date(now)
    snapshot_at = now.astimezone(UTC).replace(second=0, microsecond=0)
    if db.scalar(select(LimitUpAiSnapshot.id).where(
        LimitUpAiSnapshot.trading_date == trading_date,
        LimitUpAiSnapshot.snapshot_at == snapshot_at,
    ).limit(1)):
        return 0
    saved = 0
    for candidate in candidates:
        db.add(LimitUpAiSnapshot(
            signal_id=str(candidate["id"])[:120],
            trading_date=trading_date,
            snapshot_at=snapshot_at,
            symbol=str(candidate["symbol"])[:12],
            stock_name=str(candidate["stockName"])[:80],
            market=str(candidate["market"])[:20],
            rank=int(candidate["rank"]),
            category=str(candidate["category"])[:30],
            setup_type=str(candidate["setupType"])[:40],
            score=_dec(candidate["score"]),
            price=_dec(candidate["price"]),
            change_pct=_dec(candidate["changePercent"]),
            limit_distance_pct=_dec(candidate["limitDistancePercent"]),
            payload_json=_json(candidate),
        ))
        saved += 1
    if not saved:
        return 0
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return 0
    except SQLAlchemyError:
        db.rollback()
        return 0
    return saved


def _open_position(
    db: Session,
    user_id: str,
    settings: LimitUpAiSettings,
    candidate: dict[str, Any],
    now: datetime,
) -> None:
    open_count = db.scalar(select(func.count()).select_from(LimitUpAiPosition).where(
        LimitUpAiPosition.user_id == user_id,
        LimitUpAiPosition.status == "open",
    )) or 0
    if open_count >= settings.max_positions:
        return
    exists = db.scalar(select(LimitUpAiPosition).where(
        LimitUpAiPosition.user_id == user_id,
        LimitUpAiPosition.symbol == candidate["symbol"],
        LimitUpAiPosition.status == "open",
    ))
    if exists is not None:
        return
    budget = float(settings.capital) * float(settings.first_position_pct)
    quantity = int(budget // max(1.0, float(candidate["price"])) // 1000 * 1000)
    if quantity <= 0:
        return
    position = LimitUpAiPosition(
        user_id=user_id,
        symbol=candidate["symbol"],
        stock_name=candidate["stockName"],
        market=candidate["market"],
        setup_type=candidate["setupType"],
        entry_at=now,
        entry_price=_dec(candidate["price"]),
        current_price=_dec(candidate["price"]),
        quantity=quantity,
        remaining_quantity=quantity,
        stop_loss=_dec(candidate["stopLoss"]),
        target1=_dec(candidate["target1"]),
        target2=_dec(candidate["target2"]),
        highest_price=_dec(candidate["price"]),
        lowest_price=_dec(candidate["price"]),
        score_entry=_dec(candidate["score"]),
        score_current=_dec(candidate["score"]),
        latest_action="模擬買進：漲停飆股條件成立",
        payload_json=_json(candidate),
        updated_at=now,
    )
    db.add(position)
    db.flush()
    trade = LimitUpAiTrade(
        position_id=position.id,
        user_id=user_id,
        symbol=position.symbol,
        stock_name=position.stock_name,
        action="BUY",
        setup_type=position.setup_type,
        price=position.entry_price,
        quantity=quantity,
        gross_amount=_dec(float(position.entry_price) * quantity),
        reason="漲停飆股 AI 模擬進場",
        executed_at=now,
    )
    db.add(trade)
    db.flush()
    _notify(
        db,
        user_id=user_id,
        dedupe_key=f"buy:{position.id}",
        notification_type="BUY",
        priority=1,
        title=f"專抓漲停飆股AI 買進：{position.symbol} {position.stock_name}",
        message=f"模擬買進 {quantity:,} 股，價格 {float(position.entry_price):.2f}，評分 {float(position.score_entry):.1f}。",
        reason=f"{candidate['setupLabel']}；距漲停 {candidate['limitDistancePercent']:.2f}%；大單力道 {candidate['largeOrderForce']:.0f}",
        created_at=now,
        symbol=position.symbol,
        stock_name=position.stock_name,
        setup_type=position.setup_type,
        price=position.entry_price,
        quantity=quantity,
        amount=float(position.entry_price) * quantity,
        score=position.score_entry,
    )


def _sell_position(db: Session, position: LimitUpAiPosition, quantity: int, price: float, reason: str, now: datetime) -> None:
    quantity = max(0, min(quantity, position.remaining_quantity))
    if quantity <= 0:
        return
    pnl = (price - float(position.entry_price)) * quantity
    position.remaining_quantity -= quantity
    position.realized_pnl = _dec(float(position.realized_pnl) + pnl)
    if position.remaining_quantity <= 0:
        position.status = "closed"
        position.exit_at = now
        position.exit_price = _dec(price)
    action = "SELL" if position.status == "closed" else "REDUCE"
    trade = LimitUpAiTrade(
        position_id=position.id,
        user_id=position.user_id,
        symbol=position.symbol,
        stock_name=position.stock_name,
        action=action,
        setup_type=position.setup_type,
        price=_dec(price),
        quantity=quantity,
        gross_amount=_dec(price * quantity),
        realized_pnl=_dec(pnl),
        reason=reason,
        executed_at=now,
    )
    db.add(trade)
    db.flush()
    notification_type = "SELL" if action == "SELL" else "TAKE_PROFIT"
    if pnl < 0:
        notification_type = "STOP_LOSS"
    action_label = "停損" if notification_type == "STOP_LOSS" else "出場" if notification_type == "SELL" else "停利"
    _notify(
        db,
        user_id=position.user_id,
        dedupe_key=f"trade:{trade.id}",
        notification_type=notification_type,
        priority=1 if notification_type in {"SELL", "STOP_LOSS"} else 2,
        title=f"專抓漲停飆股AI {action_label}：{position.symbol} {position.stock_name}",
        message=f"{'全部出場' if action == 'SELL' else '分批賣出'} {quantity:,} 股，價格 {price:.2f}，本次損益 {pnl:,.0f} 元。",
        reason=reason,
        created_at=now,
        symbol=position.symbol,
        stock_name=position.stock_name,
        setup_type=position.setup_type,
        price=price,
        quantity=quantity,
        amount=price * quantity,
        realized_pnl=pnl,
        score=position.score_current,
    )


def manage_positions(db: Session, user_id: str, candidates: list[dict[str, Any]], now: datetime) -> None:
    by_symbol = {item["symbol"]: item for item in candidates}
    positions = db.scalars(select(LimitUpAiPosition).where(
        LimitUpAiPosition.user_id == user_id,
        LimitUpAiPosition.status == "open",
    )).all()
    local = now.astimezone(TAIPEI)
    for position in positions:
        candidate = by_symbol.get(position.symbol)
        price = float(candidate["price"]) if candidate else day_trading_engine.quote_for(position.symbol)
        if price is None:
            continue
        position.current_price = _dec(price)
        position.highest_price = _dec(max(float(position.highest_price), price))
        position.lowest_price = _dec(min(float(position.lowest_price), price))
        position.score_current = _dec(candidate["score"] if candidate else float(position.score_current))
        position.unrealized_pnl = _dec((price - float(position.entry_price)) * position.remaining_quantity)
        return_pct = (price - float(position.entry_price)) / float(position.entry_price) * 100
        if price <= float(position.stop_loss) or return_pct <= -1.2:
            _sell_position(db, position, position.remaining_quantity, price, "跌破停損或虧損達 1.2%", now)
            position.latest_action = "停損出場"
        elif position.take_profit_stage == 0 and return_pct >= 2:
            _sell_position(db, position, round(position.quantity * 0.3), price, "獲利 2% 先賣 30%", now)
            position.take_profit_stage = 1
            position.latest_action = "已停利 30%"
        elif position.take_profit_stage == 1 and return_pct >= 3.5:
            _sell_position(db, position, round(position.quantity * 0.3), price, "獲利 3.5% 再賣 30%", now)
            position.take_profit_stage = 2
            position.latest_action = "已停利 60%，剩餘移動停利"
        elif candidate and candidate["limitDistancePercent"] <= 0.5:
            position.latest_action = "接近漲停，監控封板/炸板"
        else:
            position.latest_action = "模擬持有"
        if time(13, 10) <= local.time() <= time(13, 25):
            score = overnight_score(candidate, price, position)
            position.overnight_score = _dec(score)
            position.overnight_hold_pct = _dec(0.5 if score >= 80 else 0.3 if score >= 70 else 0)
        position.updated_at = now


def overnight_score(candidate: dict[str, Any] | None, price: float, position: LimitUpAiPosition) -> float:
    if candidate is None:
        return 0.0
    score = 0.0
    score += 25 if candidate["limitDistancePercent"] <= 0.5 or price >= float(position.highest_price) * 0.99 else 0
    score += 15 if "站上" in str(candidate.get("vwapStatus", "")) else 0
    score += 15 if candidate.get("largeOrderContinuousBuy") else 0
    score += 15 if candidate["score"] >= 85 else 8 if candidate["score"] >= 75 else 0
    score += 10 if candidate["riskDeduction"] <= 2 else 0
    score += 10 if _num(candidate.get("limitDistancePercent")) <= 3 else 0
    score += 10 if candidate.get("category") == "attack" else 0
    if price <= float(position.highest_price) * 0.97:
        score = min(score, 69)
    return round(_clamp(score, 0, 100), 2)


def _notify_candidate_alerts(db: Session, user_id: str, candidates: list[dict[str, Any]], now: datetime) -> None:
    local = now.astimezone(TAIPEI)
    bucket_minute = local.minute - (local.minute % 5)
    bucket = local.replace(minute=bucket_minute, second=0, microsecond=0)
    for candidate in candidates[:10]:
        if not (candidate["actionable"] or (candidate["category"] == "attack" and candidate["limitDistancePercent"] <= 3)):
            continue
        alert_type = "ACTIONABLE" if candidate["actionable"] else "NEAR_LIMIT"
        _notify(
            db,
            user_id=user_id,
            dedupe_key=f"candidate:{alert_type}:{candidate['symbol']}:{bucket:%Y%m%d%H%M}",
            notification_type=alert_type,
            priority=2,
            title=f"專抓漲停飆股AI 候選：{candidate['symbol']} {candidate['stockName']}",
            message=f"{candidate['categoryLabel']}，評分 {candidate['score']:.1f}，距漲停 {candidate['limitDistancePercent']:.2f}%。",
            reason=(candidate["reasons"][0] if candidate["reasons"] else candidate["setupLabel"]),
            created_at=now,
            symbol=candidate["symbol"],
            stock_name=candidate["stockName"],
            setup_type=candidate["setupType"],
            price=candidate["price"],
            score=candidate["score"],
        )


def run_limit_up_cycle(db: Session, user_id: str, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    settings = ensure_limit_up_settings(db, user_id, current)
    candidates = scan_limit_up_candidates(db, settings, now=current)
    manage_positions(db, user_id, candidates, current)
    for candidate in candidates:
        if candidate["actionable"]:
            _open_position(db, user_id, settings, candidate, current)
    _notify_candidate_alerts(db, user_id, candidates, current)
    db.commit()
    return dashboard_payload(db, user_id, candidates=candidates, now=current)


def latest_snapshot_candidates(db: Session, now: datetime | None = None, limit: int = SNAPSHOT_LIMIT) -> list[dict[str, Any]]:
    current = now or datetime.now(UTC)
    trade_date = _trading_date(current)
    latest_snapshot_at = db.scalar(select(func.max(LimitUpAiSnapshot.snapshot_at)).where(
        LimitUpAiSnapshot.trading_date == trade_date,
    ))
    if latest_snapshot_at is None:
        return []
    rows = db.scalars(select(LimitUpAiSnapshot).where(
        LimitUpAiSnapshot.trading_date == trade_date,
        LimitUpAiSnapshot.snapshot_at == latest_snapshot_at,
    ).order_by(LimitUpAiSnapshot.rank.asc()).limit(limit)).all()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            candidates.append(payload)
    return candidates


def position_payload(item: LimitUpAiPosition) -> dict[str, Any]:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "stockName": item.stock_name,
        "market": item.market,
        "setupType": item.setup_type,
        "status": item.status,
        "entryAt": item.entry_at.isoformat(),
        "exitAt": item.exit_at.isoformat() if item.exit_at else None,
        "entryPrice": float(item.entry_price),
        "currentPrice": float(item.current_price),
        "exitPrice": float(item.exit_price) if item.exit_price else None,
        "quantity": item.quantity,
        "remainingQuantity": item.remaining_quantity,
        "stopLoss": float(item.stop_loss),
        "target1": float(item.target1),
        "target2": float(item.target2),
        "highestPrice": float(item.highest_price),
        "lowestPrice": float(item.lowest_price),
        "takeProfitStage": item.take_profit_stage,
        "scoreEntry": float(item.score_entry),
        "scoreCurrent": float(item.score_current),
        "overnightScore": float(item.overnight_score),
        "overnightHoldPct": float(item.overnight_hold_pct),
        "realizedPnl": float(item.realized_pnl),
        "unrealizedPnl": float(item.unrealized_pnl),
        "returnPercent": (float(item.current_price) - float(item.entry_price)) / float(item.entry_price) * 100,
        "latestAction": item.latest_action,
        "updatedAt": item.updated_at.isoformat(),
    }


def trade_payload(item: LimitUpAiTrade) -> dict[str, Any]:
    return {
        "id": item.id,
        "positionId": item.position_id,
        "symbol": item.symbol,
        "stockName": item.stock_name,
        "action": item.action,
        "setupType": item.setup_type,
        "price": float(item.price),
        "quantity": item.quantity,
        "grossAmount": float(item.gross_amount),
        "realizedPnl": float(item.realized_pnl),
        "reason": item.reason,
        "executedAt": item.executed_at.isoformat(),
    }


def limit_up_performance_payload(db: Session, user_id: str, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    settings = ensure_limit_up_settings(db, user_id, current)
    local_today = current.astimezone(TAIPEI).date()
    local_month = local_today.strftime("%Y-%m")
    trades = db.scalars(select(LimitUpAiTrade).where(
        LimitUpAiTrade.user_id == user_id,
    ).order_by(LimitUpAiTrade.executed_at.asc())).all()
    open_positions = db.scalars(select(LimitUpAiPosition).where(
        LimitUpAiPosition.user_id == user_id,
        LimitUpAiPosition.status == "open",
    )).all()
    open_unrealized = sum(float(item.unrealized_pnl) for item in open_positions)

    def trade_day(item: LimitUpAiTrade) -> date:
        return item.executed_at.astimezone(TAIPEI).date()

    def trade_month(item: LimitUpAiTrade) -> str:
        return trade_day(item).strftime("%Y-%m")

    def summarize(rows: list[LimitUpAiTrade], include_unrealized: bool) -> dict[str, Any]:
        realized_rows = [item for item in rows if item.action in {"SELL", "REDUCE"}]
        buy_rows = [item for item in rows if item.action == "BUY"]
        realized = sum(float(item.realized_pnl) for item in realized_rows)
        unrealized = open_unrealized if include_unrealized else 0.0
        wins = [float(item.realized_pnl) for item in realized_rows if float(item.realized_pnl) > 0]
        losses = [float(item.realized_pnl) for item in realized_rows if float(item.realized_pnl) < 0]
        return {
            "tradeCount": len(realized_rows),
            "buyCount": len(buy_rows),
            "sellCount": len(realized_rows),
            "winCount": len(wins),
            "lossCount": len(losses),
            "winRate": round(len(wins) / len(realized_rows) * 100, 2) if realized_rows else 0,
            "realizedPnl": round(realized, 2),
            "unrealizedPnl": round(unrealized, 2),
            "totalPnl": round(realized + unrealized, 2),
            "totalReturnPct": round((realized + unrealized) / max(1.0, float(settings.capital)) * 100, 4),
            "averageWin": round(sum(wins) / len(wins), 2) if wins else 0,
            "averageLoss": round(sum(losses) / len(losses), 2) if losses else 0,
            "maximumSingleLoss": round(min(losses), 2) if losses else 0,
            "openPositionCount": len(open_positions) if include_unrealized else 0,
        }

    today_rows = [item for item in trades if trade_day(item) == local_today]
    month_rows = [item for item in trades if trade_month(item) == local_month]
    return {
        "today": summarize(today_rows, True),
        "month": summarize(month_rows, True),
        "all": summarize(list(trades), True),
        "period": local_month,
        "updatedAt": current.isoformat(),
    }


def list_limit_up_notifications(
    db: Session,
    user_id: str,
    *,
    limit: int = 80,
    notification_type: str | None = None,
    unread_only: bool = False,
) -> dict[str, Any]:
    query = select(LimitUpAiNotification).where(LimitUpAiNotification.user_id == user_id)
    if notification_type:
        query = query.where(LimitUpAiNotification.notification_type == notification_type)
    if unread_only:
        query = query.where(LimitUpAiNotification.is_read.is_(False))
    rows = db.scalars(query.order_by(LimitUpAiNotification.created_at.desc()).limit(limit)).all()
    unread = db.scalar(select(func.count()).select_from(LimitUpAiNotification).where(
        LimitUpAiNotification.user_id == user_id,
        LimitUpAiNotification.is_read.is_(False),
    )) or 0
    return {"items": [_notification_payload(item) for item in rows], "unreadCount": unread}


def unread_limit_up_notification_count(db: Session, user_id: str) -> int:
    return int(db.scalar(select(func.count()).select_from(LimitUpAiNotification).where(
        LimitUpAiNotification.user_id == user_id,
        LimitUpAiNotification.is_read.is_(False),
    )) or 0)


def mark_limit_up_notification_read(db: Session, user_id: str, notification_id: int, now: datetime | None = None) -> bool:
    item = db.scalar(select(LimitUpAiNotification).where(
        LimitUpAiNotification.id == notification_id,
        LimitUpAiNotification.user_id == user_id,
    ))
    if item is None:
        return False
    if not item.is_read:
        item.is_read = True
        item.read_at = now or datetime.now(UTC)
        db.commit()
    return True


def mark_all_limit_up_notifications_read(db: Session, user_id: str, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    rows = db.scalars(select(LimitUpAiNotification).where(
        LimitUpAiNotification.user_id == user_id,
        LimitUpAiNotification.is_read.is_(False),
    )).all()
    for item in rows:
        item.is_read = True
        item.read_at = current
    db.commit()
    return len(rows)


def dashboard_payload(
    db: Session,
    user_id: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    settings = ensure_limit_up_settings(db, user_id, current)
    if candidates is None:
        candidates = latest_snapshot_candidates(db, current)
    positions = db.scalars(select(LimitUpAiPosition).where(
        LimitUpAiPosition.user_id == user_id,
    ).order_by(LimitUpAiPosition.updated_at.desc()).limit(100)).all()
    trades = db.scalars(select(LimitUpAiTrade).where(
        LimitUpAiTrade.user_id == user_id,
    ).order_by(LimitUpAiTrade.executed_at.desc()).limit(200)).all()
    realized = sum(float(item.realized_pnl) for item in trades)
    open_positions = [item for item in positions if item.status == "open"]
    unrealized = sum(float(item.unrealized_pnl) for item in open_positions)
    closed_sells = [item for item in trades if item.action in {"SELL", "REDUCE"}]
    wins = [item for item in closed_sells if float(item.realized_pnl) > 0]
    losses = [item for item in closed_sells if float(item.realized_pnl) < 0]
    performance = limit_up_performance_payload(db, user_id, current)
    unread = unread_limit_up_notification_count(db, user_id)
    latest_notifications = list_limit_up_notifications(db, user_id, limit=30)["items"]
    return {
        "updatedAt": current.isoformat(),
        "settings": settings_payload(settings),
        "summary": {
            "candidateCount": len(candidates),
            "attackCount": sum(1 for item in candidates if item["category"] == "attack"),
            "actionableCount": sum(1 for item in candidates if item["actionable"]),
            "openPositionCount": len(open_positions),
            "realizedPnl": round(realized, 2),
            "unrealizedPnl": round(unrealized, 2),
            "totalPnl": round(realized + unrealized, 2),
            "winRate": round(len(wins) / len(closed_sells) * 100, 2) if closed_sells else 0,
        },
        "candidates": candidates[:SNAPSHOT_LIMIT],
        "nearEntries": [item for item in candidates if item["actionable"] or item["category"] == "attack"][:10],
        "watchlist": [item for item in candidates if item["category"] in {"monitor", "watch"}][:20],
        "positions": [position_payload(item) for item in positions],
        "trades": [trade_payload(item) for item in trades],
        "limitMonitors": [item for item in candidates if item["limitDistancePercent"] <= 3][:20],
        "overnightEvaluations": [position_payload(item) for item in open_positions if float(item.overnight_score) > 0],
        "performance": performance,
        "notifications": latest_notifications,
        "unreadCount": unread,
        "dataNotice": "第一版使用即時行情、大單連續性、VWAP/5分K與五檔快照估算；逐筆主動買盤不足時會標示估算。",
    }


def replay_today(db: Session, user_id: str, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    trade_date = _trading_date(current)
    rows = db.scalars(select(LimitUpAiSnapshot).where(
        LimitUpAiSnapshot.trading_date == trade_date,
    ).order_by(LimitUpAiSnapshot.snapshot_at.desc(), LimitUpAiSnapshot.rank.asc()).limit(500)).all()
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row.signal_id in seen:
            continue
        seen.add(row.signal_id)
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            items.append(payload)
    return {
        "tradingDate": trade_date.isoformat(),
        "items": items,
        "total": len(items),
        "attackTotal": sum(1 for item in items if item.get("category") == "attack"),
        "actionableTotal": sum(1 for item in items if item.get("actionable")),
        "updatedAt": current.isoformat(),
    }
