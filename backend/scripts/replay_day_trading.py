from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.services.day_trading import MockDayTradingEngine
from app.services.official_market_data import OfficialStockQuote
from app.services.theme_stock_universe import THEME_STOCKS


TAIPEI = ZoneInfo("Asia/Taipei")
@dataclass(frozen=True)
class MinuteBar:
    at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class SymbolSeries:
    symbol: str
    name: str
    previous_close: float
    bars: tuple[MinuteBar, ...]


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _parse_chart(payload: dict[str, Any], symbol: str, name: str, replay_date: date) -> SymbolSeries:
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    timestamps = result.get("timestamp") or []
    quote_values = result["indicators"]["quote"][0]
    opens = quote_values.get("open") or []
    highs = quote_values.get("high") or []
    lows = quote_values.get("low") or []
    closes = quote_values.get("close") or []
    volumes = quote_values.get("volume") or []
    bars: list[MinuteBar] = []
    for index, timestamp in enumerate(timestamps):
        values = [
            _number(items[index]) if index < len(items) else None
            for items in (opens, highs, lows, closes)
        ]
        if any(value is None for value in values):
            continue
        at = datetime.fromtimestamp(timestamp, TAIPEI).replace(second=0, microsecond=0)
        if at.date() != replay_date or not time(9, 0) <= at.time() <= time(13, 30):
            continue
        bars.append(MinuteBar(
            at=at,
            open=values[0] or 0,
            high=values[1] or 0,
            low=values[2] or 0,
            close=values[3] or 0,
            volume=max(0, int(_number(volumes[index]) or 0)) if index < len(volumes) else 0,
        ))
    previous_close = _number(meta.get("chartPreviousClose") or meta.get("previousClose"))
    if not bars or previous_close is None or previous_close <= 0:
        raise ValueError(f"{symbol} 沒有可用的 {replay_date.isoformat()} 分鐘行情")
    return SymbolSeries(symbol, name, previous_close, tuple(bars))


async def _fetch_series(
    client: httpx.AsyncClient,
    ticker: str,
    symbol: str,
    name: str,
    replay_date: date,
) -> SymbolSeries:
    start = datetime.combine(replay_date, time.min, TAIPEI)
    end = start + timedelta(days=1)
    endpoint = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
        f"?interval=1m&period1={int(start.timestamp())}&period2={int(end.timestamp())}&events=history"
    )
    response = await client.get(endpoint)
    response.raise_for_status()
    return _parse_chart(response.json(), symbol, name, replay_date)


def _signal_eligible(item: dict[str, Any], at: datetime) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if at.time() < time(9, 3) or at.time() >= time(13, 20):
        failures.append("不在正式掃描時段")
    if item.get("status") != "confirmed":
        failures.append("條件尚未確認")
    if str(item.get("action", "")).startswith(("等待", "觀望", "禁止", "行情異常")):
        failures.append("尚未形成進場指令")
    if float(item.get("confidenceScore", 0)) < 75:
        failures.append("信心分數未達 75")
    if float(item.get("healthScore", 0)) < 70:
        failures.append("健康度未達 70")
    if float(item.get("riskRewardRatio", 0)) < 1.5:
        failures.append("風險報酬比不足")
    if float(item.get("volume", 0)) < 500_000:
        failures.append("成交量不足")
    if float(item.get("turnover", 0)) < 50_000_000:
        failures.append("成交金額不足")
    if item.get("chaseBlocked"):
        failures.append("觸發禁止追價")
    if float(item.get("stopDistancePercent", 999)) > 3:
        failures.append("停損距離超過 3%")
    if item.get("direction") == "short":
        failures.append("歷史分鐘線不含券源，放空僅列候選")
    return not failures, failures


def _simulate_first_long_entries(
    eligible_events: list[dict[str, Any]],
    bars_by_time: dict[str, dict[datetime, MinuteBar]],
    replay_date: date,
) -> list[dict[str, Any]]:
    """Simulate one lot from each symbol's first eligible long replay signal."""
    trades: list[dict[str, Any]] = []
    traded_symbols: set[str] = set()
    forced_exit_at = datetime.combine(replay_date, time(13, 25), TAIPEI)
    for event in eligible_events:
        symbol = str(event["symbol"])
        if symbol in traded_symbols:
            continue
        traded_symbols.add(symbol)
        signal_at = datetime.fromisoformat(str(event["time"]))
        entry = float(event["price"])
        stop = float(event["stopLoss"])
        target_1 = float(event["target1"])
        target_2 = float(event["target2"])
        remaining = 1_000
        exits: list[dict[str, Any]] = []
        target_1_hit = False
        future_bars = [
            bar for at, bar in sorted(bars_by_time[symbol].items())
            if signal_at < at <= forced_exit_at
        ]
        for bar in future_bars:
            # A one-minute bar cannot reveal intrabar ordering. Stop-first is the
            # conservative assumption when a stop and target are both touched.
            if bar.low <= stop:
                exits.append({
                    "time": bar.at.isoformat(),
                    "price": stop,
                    "quantity": remaining,
                    "reason": "停損",
                })
                remaining = 0
                break
            if not target_1_hit and bar.high >= target_1:
                quantity = remaining // 2
                exits.append({
                    "time": bar.at.isoformat(),
                    "price": target_1,
                    "quantity": quantity,
                    "reason": "第一目標減碼 50%",
                })
                remaining -= quantity
                target_1_hit = True
            if target_1_hit and remaining and bar.high >= target_2:
                exits.append({
                    "time": bar.at.isoformat(),
                    "price": target_2,
                    "quantity": remaining,
                    "reason": "第二目標全部出場",
                })
                remaining = 0
                break
        if remaining:
            closing_bar = future_bars[-1] if future_bars else bars_by_time[symbol][signal_at]
            exits.append({
                "time": closing_bar.at.isoformat(),
                "price": closing_bar.close,
                "quantity": remaining,
                "reason": "13:25 收盤前模擬出場",
            })
        gross_profit = sum(
            (float(exit_item["price"]) - entry) * int(exit_item["quantity"])
            for exit_item in exits
        )
        trades.append({
            "symbol": symbol,
            "name": event["name"],
            "direction": "long",
            "entryTime": event["time"],
            "entryPrice": entry,
            "quantity": 1_000,
            "stopLoss": stop,
            "target1": target_1,
            "target2": target_2,
            "exits": exits,
            "grossProfit": round(gross_profit, 2),
            "grossReturnPercent": round(gross_profit / (entry * 1_000) * 100, 3),
        })
    return trades


async def replay(replay_date: date) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Replay"},
    ) as client:
        stock_tasks = [
            _fetch_series(
                client,
                f"{stock.symbol}.{'TW' if stock.market == '上市' else 'TWO'}",
                stock.symbol,
                stock.name,
                replay_date,
            )
            for stock in THEME_STOCKS
        ]
        index_task = _fetch_series(client, "^TWII", "t00", "加權指數", replay_date)
        fetched = await asyncio.gather(*stock_tasks, index_task, return_exceptions=True)

    failures = [str(item) for item in fetched if isinstance(item, Exception)]
    series = [item for item in fetched if isinstance(item, SymbolSeries)]
    by_symbol = {item.symbol: item for item in series}
    if "t00" not in by_symbol:
        raise RuntimeError("缺少加權指數分鐘行情，無法重播")

    bars_by_time = {
        symbol: {bar.at: bar for bar in item.bars}
        for symbol, item in by_symbol.items()
    }
    cumulative_volume = {symbol: 0 for symbol in by_symbol}
    day_open: dict[str, float] = {}
    day_high: dict[str, float] = {}
    day_low: dict[str, float] = {}
    latest_bar: dict[str, MinuteBar] = {}
    engine = MockDayTradingEngine()
    events: list[dict[str, Any]] = []
    last_state: dict[str, tuple[str, str, bool]] = {}
    minute = datetime.combine(replay_date, time(9, 0), TAIPEI)
    close = datetime.combine(replay_date, time(13, 30), TAIPEI)

    while minute <= close:
        quotes: dict[str, OfficialStockQuote] = {}
        for symbol, symbol_series in by_symbol.items():
            bar = bars_by_time[symbol].get(minute)
            if bar is None:
                continue
            latest_bar[symbol] = bar
            cumulative_volume[symbol] += bar.volume
            day_open.setdefault(symbol, bar.open)
            day_high[symbol] = max(day_high.get(symbol, bar.high), bar.high)
            day_low[symbol] = min(day_low.get(symbol, bar.low), bar.low)
            change = bar.close - symbol_series.previous_close
            quotes[symbol] = OfficialStockQuote(
                symbol=symbol,
                name=symbol_series.name,
                price=bar.close,
                previous_close=symbol_series.previous_close,
                open=day_open[symbol],
                high=day_high[symbol],
                low=day_low[symbol],
                volume=cumulative_volume[symbol],
                change=change,
                change_percent=change / symbol_series.previous_close * 100,
                quote_timestamp=minute.isoformat(),
                # The isolated replay engine must see each historical bar as the
                # live sample that would have arrived at that minute; otherwise
                # its production warm-up gate intentionally rejects all history.
                # The report keeps the real historical source and simulation mode.
                source="TWSE MIS",
                is_realtime=True,
            )
        engine.update_official_quotes(quotes)
        for item in engine.signals(now=minute):
            eligible, qualification_failures = _signal_eligible(item, minute)
            state = (str(item["direction"]), str(item["action"]), eligible)
            symbol = str(item["symbol"])
            if state == last_state.get(symbol):
                continue
            last_state[symbol] = state
            if not eligible and not (
                item.get("status") == "confirmed"
                and not str(item.get("action", "")).startswith("等待")
            ):
                continue
            events.append({
                "time": minute.isoformat(),
                "symbol": symbol,
                "name": item["stockName"],
                "direction": item["direction"],
                "action": item["action"],
                "price": item["price"],
                "confidenceScore": item["confidenceScore"],
                "healthScore": item["healthScore"],
                "marketAlignment": item["marketAlignment"],
                "eligibleLongReplaySignal": eligible,
                "qualificationFailures": qualification_failures,
                "entryMin": item["entryMin"],
                "entryMax": item["entryMax"],
                "stopLoss": item["stopLoss"],
                "target1": item["target1"],
                "target2": item["target2"],
                "reasons": item["reasons"],
            })
        minute += timedelta(minutes=1)

    index_series = by_symbol["t00"]
    index_first = index_series.bars[0]
    index_last = index_series.bars[-1]
    eligible_events = [item for item in events if item["eligibleLongReplaySignal"]]
    simulated_trades = _simulate_first_long_entries(eligible_events, bars_by_time, replay_date)
    return {
        "replayDate": replay_date.isoformat(),
        "session": "09:00-13:30 Asia/Taipei",
        "mode": "historical_replay_simulation",
        "source": "Yahoo Finance 1 分鐘歷史 K 線；收盤價可另與 TWSE MIS 核對",
        "limitations": [
            "歷史分鐘線沒有買一／賣一價差，未執行價差風控",
            "歷史分鐘線沒有券源與融券資格，放空訊號只列候選，不列可執行訊號",
            "目前當沖引擎策略模板只涵蓋 6669、2317、2454、2330、2382、2603",
            "績效以每檔第一個合格做多訊號收盤價成交 1 張、停損優先、13:25 強制出場計算",
            "損益為未扣手續費與交易稅的毛損益，且沒有寫入正式持倉資料",
        ],
        "coverage": {
            "requestedSymbols": len(THEME_STOCKS),
            "loadedSymbols": len([symbol for symbol in by_symbol if symbol != "t00"]),
            "failedSeries": failures,
            "indexMinutes": len(index_series.bars),
            "firstTimestamp": index_first.at.isoformat(),
            "lastTimestamp": index_last.at.isoformat(),
        },
        "market": {
            "indexOpen": index_first.open,
            "indexClose": index_last.close,
            "indexChange": round(index_last.close - index_series.previous_close, 2),
            "indexChangePercent": round(
                (index_last.close - index_series.previous_close) / index_series.previous_close * 100,
                2,
            ),
            "indexHigh": max(bar.high for bar in index_series.bars),
            "indexLow": min(bar.low for bar in index_series.bars),
        },
        "summary": {
            "stateChangesRecorded": len(events),
            "eligibleLongReplaySignals": len(eligible_events),
            "symbolsWithEligibleSignals": sorted({str(item["symbol"]) for item in eligible_events}),
            "simulatedTrades": len(simulated_trades),
            "grossProfit": round(sum(float(item["grossProfit"]) for item in simulated_trades), 2),
        },
        "eligibleSignals": eligible_events,
        "simulatedTrades": simulated_trades,
        "candidateStateChanges": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="重播指定台股交易日的當沖機器人訊號")
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(replay(args.date))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
