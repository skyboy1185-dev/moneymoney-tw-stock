from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean

from ..adaptive_schemas import AdaptiveBacktestPrice, AdaptiveBacktestRequest


@dataclass(frozen=True)
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_percent: float
    reason: str
    holding_days: int


def _sma(values: list[float], end: int, period: int) -> float | None:
    if end + 1 < period:
        return None
    return mean(values[end - period + 1:end + 1])


def _atr(prices: list[AdaptiveBacktestPrice], end: int, period: int = 14) -> float | None:
    if end < period:
        return None
    ranges = []
    for index in range(end - period + 1, end + 1):
        previous = prices[index - 1].close
        row = prices[index]
        ranges.append(max(row.high - row.low, abs(row.high - previous), abs(row.low - previous)))
    return mean(ranges)


def _entry_signal(strategy: str, prices: list[AdaptiveBacktestPrice], index: int) -> bool:
    closes = [row.close for row in prices]
    volumes = [row.volume for row in prices]
    if index < 61:
        return False
    row = prices[index]
    ma5, ma20, ma60 = _sma(closes, index, 5), _sma(closes, index, 20), _sma(closes, index, 60)
    volume20 = _sma(volumes, index - 1, 20) or 0
    prior20_high = max(item.high for item in prices[index - 20:index])
    prior20_low = min(item.low for item in prices[index - 20:index])
    amplitude = (prior20_high / prior20_low - 1) * 100 if prior20_low else 999
    if strategy == "BREAKOUT":
        return bool(ma20 and ma60 and row.close > prior20_high * 1.01 and row.close > ma20 > ma60 and row.volume >= volume20 * 1.5)
    if strategy == "RANGE":
        position = (row.close - prior20_low) / max(.01, prior20_high - prior20_low)
        return bool(6 <= amplitude <= 15 and position <= .2 and ma20 and row.close >= ma20 * .95 and row.close > row.open)
    prior5_low = min(item.low for item in prices[index - 10:index - 5])
    recent5_low = min(item.low for item in prices[index - 5:index])
    return bool(ma5 and row.close > ma5 and recent5_low >= prior5_low and closes[index] > closes[index - 1] and row.volume >= volume20)


def run_backtest(request: AdaptiveBacktestRequest) -> dict:
    prices = sorted(request.prices, key=lambda row: row.date)
    if len(prices) < 80:
        raise ValueError("回測至少需要 80 個交易日")
    effective_cost = request.commission_rate * request.commission_discount
    buy_cost = effective_cost + request.slippage_rate
    sell_cost = effective_cost + request.tax_rate + request.slippage_rate
    trades: list[Trade] = []
    equity = 1.0
    curve = [equity]
    index = 61
    while index < len(prices) - 1:
        if not _entry_signal(request.strategy_type, prices, index):
            curve.append(equity)
            index += 1
            continue
        signal = prices[index]
        entry_row = prices[index + 1]
        entry = entry_row.open * (1 + request.slippage_rate)
        atr = _atr(prices, index) or entry * .03
        stop_limit = {"RANGE": .05, "BREAKOUT": .07, "RECOVERY": .08}[request.strategy_type]
        stop = max(entry * (1 - stop_limit), entry - atr * 2)
        target = entry + (entry - stop) * 2
        exit_index = min(len(prices) - 1, index + 21)
        exit_price = prices[exit_index].close * (1 - request.slippage_rate)
        reason = "持有期到期"
        for cursor in range(index + 1, min(len(prices), index + 22)):
            row = prices[cursor]
            # If both prices are touched on one daily bar, conservatively assume stop first.
            if row.low <= stop:
                exit_index, exit_price, reason = cursor, stop * (1 - request.slippage_rate), "停損"
                break
            if row.high >= target:
                exit_index, exit_price, reason = cursor, target * (1 - request.slippage_rate), "目標價"
                break
        gross = exit_price / entry - 1
        net = gross - buy_cost - sell_cost
        equity *= 1 + net
        trades.append(Trade(
            entry_row.date.isoformat(), prices[exit_index].date.isoformat(),
            round(entry, 4), round(exit_price, 4), round(net * 100, 4),
            reason, exit_index - index,
        ))
        curve.append(equity)
        index = exit_index + 1

    returns = [item.return_percent for item in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    peak = curve[0]
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    years = max(1 / 252, len(prices) / 252)
    annualized = (equity ** (1 / years) - 1) * 100
    benchmark = None
    if request.benchmark_prices:
        benchmark_rows = sorted(request.benchmark_prices, key=lambda row: row.date)
        if len(benchmark_rows) >= 2:
            benchmark = (benchmark_rows[-1].close / benchmark_rows[0].close - 1) * 100
    payoff = (mean(wins) / abs(mean(losses))) if wins and losses and mean(losses) else None
    return {
        "stockCode": request.stock_code, "stockName": request.stock_name,
        "strategyType": request.strategy_type, "years": request.years,
        "fromDate": prices[0].date.isoformat(), "toDate": prices[-1].date.isoformat(),
        "totalReturn": round((equity - 1) * 100, 2), "annualizedReturn": round(annualized, 2),
        "winRate": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "averageProfit": round(mean(wins), 2) if wins else 0,
        "averageLoss": round(mean(losses), 2) if losses else 0,
        "payoffRatio": round(payoff, 2) if payoff is not None else None,
        "maximumDrawdown": round(max_drawdown * 100, 2),
        "averageHoldingDays": round(mean([item.holding_days for item in trades]), 1) if trades else 0,
        "signalCount": len(trades),
        "falseBreakoutRatio": round(sum(1 for item in trades if item.reason == "停損") / len(trades) * 100, 2) if trades else 0,
        "stopLossRatio": round(sum(1 for item in trades if item.reason == "停損") / len(trades) * 100, 2) if trades else 0,
        "benchmarkReturn": round(benchmark, 2) if benchmark is not None else None,
        "costs": {"commissionRate": request.commission_rate, "commissionDiscount": request.commission_discount, "taxRate": request.tax_rate, "slippageRate": request.slippage_rate},
        "trades": [item.__dict__ for item in trades[-100:]],
        "methodology": "訊號使用當日收盤以前資料，於下一交易日開盤成交；同日同時觸及停損與停利時保守假設先停損。",
        "limitations": ["單一現存股票回測仍可能有生存者偏差", "未含除權息還原時可能影響長期報酬", "漲跌停無法成交日需由更完整逐筆資料另行驗證"],
    }
