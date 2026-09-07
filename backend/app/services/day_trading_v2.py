"""Core rules for the unified long-only intraday system.

The module is deliberately independent from HTTP and broker SDKs so the exact
same money, risk and strategy rules are used by backtest, paper and live modes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
ZERO = Decimal("0")
CENT = Decimal("0.01")

STRATEGIES = (
    ("OPENING_RANGE_BREAKOUT", "開盤15分鐘區間突破", Decimal("750000")),
    ("VWAP_TREND_PULLBACK", "VWAP多方趨勢回踩", Decimal("750000")),
    ("VOLUME_HIGH_BREAKOUT", "爆量突破前高", Decimal("600000")),
    ("FALSE_BREAKDOWN_REVERSAL", "支撐假跌破反轉", Decimal("300000")),
    ("AFTERNOON_STRENGTH_BREAKOUT", "尾盤強勢突破", Decimal("600000")),
)

DEFAULT_CONFIG: dict[str, object] = {
    "initialCapital": "3000000",
    "maxRiskPerTrade": "6000",
    "dailyReduceLoss": "12000",
    "dailyStopLoss": "24000",
    "monthlyMaxDrawdown": "150000",
    "maxRobotCapitalPct": "35",
    "maxOpenPositions": 3,
    "maxSectorPositions": 2,
    "maxConsecutiveLosses": 3,
    "latestEntryTime": "13:20:00",
    "forcedCloseTime": "13:25:00",
    "minimumConfidence": "80",
    "minimumRiskReward": "1.8",
    "minimumVolume": 100000,
    "minimumTurnover": "20000000",
    "maximumSpreadPct": "0.5",
    "maximumVwapDeviationPct": "1.5",
    "boardLotSize": 1000,
    "allowOddLots": False,
    "commissionRate": "0.001425",
    "commissionDiscount": "0.2",
    "minimumCommission": "20",
    "dayTradeTaxRate": "0.0015",
    "slippageBps": "5",
    "otherCost": "0",
    "forceCloseEnabled": True,
}


def dec(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def money(value: object) -> Decimal:
    return dec(value).quantize(CENT, rounding=ROUND_HALF_UP)


def merged_config(custom: Mapping[str, object] | None = None) -> dict[str, object]:
    result = dict(DEFAULT_CONFIG)
    if custom:
        result.update({key: value for key, value in custom.items() if key in result})
    return result


def calculate_position_size(
    *, price: object, stop_price: object, risk_budget: object, capital_limit: object,
    lot_size: int = 1000, allow_odd_lots: bool = False,
) -> int:
    entry = dec(price)
    risk_per_share = entry - dec(stop_price)
    if entry <= 0 or risk_per_share <= 0 or dec(risk_budget) <= 0 or dec(capital_limit) <= 0:
        return 0
    by_risk = int((dec(risk_budget) / risk_per_share).to_integral_value(rounding=ROUND_DOWN))
    by_capital = int((dec(capital_limit) / entry).to_integral_value(rounding=ROUND_DOWN))
    quantity = min(by_risk, by_capital)
    if not allow_odd_lots:
        quantity = (quantity // lot_size) * lot_size
    return max(quantity, 0)


@dataclass(frozen=True)
class Costs:
    buy_fee: Decimal
    sell_fee: Decimal
    transaction_tax: Decimal
    slippage: Decimal
    other_cost: Decimal
    total: Decimal


def calculate_costs(
    *, entry_price: object, exit_price: object, quantity: int,
    commission_rate: object = "0.001425", commission_discount: object = "0.2",
    minimum_commission: object = "20", tax_rate: object = "0.0015",
    slippage_bps: object = "5", other_cost: object = 0,
) -> Costs:
    buy_value = dec(entry_price) * quantity
    sell_value = dec(exit_price) * quantity
    minimum = dec(minimum_commission)
    effective_rate = dec(commission_rate) * dec(commission_discount)
    buy_fee = money(max(buy_value * effective_rate, minimum) if quantity else ZERO)
    sell_fee = money(max(sell_value * effective_rate, minimum) if quantity else ZERO)
    tax = money(sell_value * dec(tax_rate))
    slippage = money((buy_value + sell_value) * dec(slippage_bps) / Decimal("10000"))
    other = money(other_cost)
    total = money(buy_fee + sell_fee + tax + slippage + other)
    return Costs(buy_fee, sell_fee, tax, slippage, other, total)


def calculate_trade_result(*, entry_price: object, exit_price: object, quantity: int, **cost_options: object) -> dict[str, Decimal]:
    gross = money((dec(exit_price) - dec(entry_price)) * quantity)
    costs = calculate_costs(entry_price=entry_price, exit_price=exit_price, quantity=quantity, **cost_options)
    net = money(gross - costs.total)
    capital = dec(entry_price) * quantity
    net_return = (net / capital * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if capital else ZERO
    return {"grossPnl": gross, "netPnl": net, "netReturnPct": net_return, **asdict(costs)}


def performance(trades: Iterable[Mapping[str, object]], initial_capital: object = "3000000") -> dict[str, object]:
    rows = list(trades)
    pnls = [dec(row.get("netPnl", row.get("net_pnl", 0))) for row in rows]
    winners = [pnl for pnl in pnls if pnl > 0]
    losers = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(winners, ZERO)
    gross_loss = sum(losers, ZERO)
    total = len(pnls)
    win_rate = (Decimal(len(winners)) / total * 100).quantize(Decimal("0.1")) if total else ZERO
    profit_factor = (gross_profit / abs(gross_loss)).quantize(Decimal("0.01")) if gross_loss else None
    avg_win = money(gross_profit / len(winners)) if winners else ZERO
    avg_loss = money(gross_loss / len(losers)) if losers else ZERO
    payoff = (avg_win / abs(avg_loss)).quantize(Decimal("0.01")) if avg_loss else None
    net = money(sum(pnls, ZERO))
    initial = dec(initial_capital)
    return {
        "initialCapital": str(money(initial)), "endingCapital": str(money(initial + net)),
        "netPnl": str(net), "netReturnPct": str((net / initial * 100).quantize(Decimal("0.01")) if initial else ZERO),
        "tradeCount": total, "winCount": len(winners), "lossCount": len(losers),
        "winRate": str(win_rate), "sampleSufficient": total >= 10,
        "averageWin": str(avg_win), "averageLoss": str(avg_loss),
        "payoffRatio": str(payoff) if payoff is not None else None,
        "profitFactor": str(profit_factor) if profit_factor is not None else None,
        "maxWin": str(money(max(pnls, default=ZERO))), "maxLoss": str(money(min(pnls, default=ZERO))),
    }


def risk_status(realized_pnl: object, config: Mapping[str, object] | None = None) -> str:
    cfg = merged_config(config)
    loss = max(-dec(realized_pnl), ZERO)
    if loss >= dec(cfg["dailyStopLoss"]):
        return "HALTED"
    if loss >= dec(cfg["dailyReduceLoss"]):
        return "REDUCED"
    return "NORMAL"


def apply_execution_report(state: Mapping[str, object], *, execution_id: str, quantity: int, price: object) -> dict[str, object]:
    """Idempotently apply a broker fill to an order state."""
    seen = set(state.get("executionIds", []))
    if execution_id in seen:
        return dict(state)
    ordered = int(state.get("orderQuantity", 0))
    already = int(state.get("filledQuantity", 0))
    accepted = max(0, min(quantity, ordered - already))
    total_quantity = already + accepted
    previous_notional = dec(state.get("averagePrice", 0)) * already
    average = (previous_notional + dec(price) * accepted) / total_quantity if total_quantity else ZERO
    seen.add(execution_id)
    result = dict(state)
    result.update({
        "filledQuantity": total_quantity,
        "unfilledQuantity": max(ordered - total_quantity, 0),
        "averagePrice": str(average.quantize(Decimal("0.0001"))),
        "executionIds": sorted(seen),
        "status": "FILLED" if total_quantity >= ordered else "PARTIALLY_FILLED",
    })
    return result


def exit_action(
    *, current_price: object, stop_price: object, first_target_price: object,
    trailing_stop_price: object, first_target_filled: bool = False,
) -> dict[str, object] | None:
    current = dec(current_price)
    if current <= dec(stop_price):
        return {"reason": "停損", "percentage": 100}
    if trailing_stop_price and current <= dec(trailing_stop_price):
        return {"reason": "移動停利", "percentage": 100}
    if not first_target_filled and current >= dec(first_target_price):
        return {"reason": "第一段停利", "percentage": 50}
    return None


def market_gate_reasons(
    *, now: datetime, market_crashing: bool, quote_reliable: bool, volume: int,
    turnover: object, spread_pct: object, vwap_deviation_pct: object,
    blocked: bool, connection_ok: bool, available_capital: object,
    open_positions: int, sector_positions: int, realized_pnl: object,
    config: Mapping[str, object] | None = None,
) -> list[str]:
    cfg = merged_config(config)
    reasons: list[str] = []
    local = now.astimezone(TAIPEI) if now.tzinfo else now.replace(tzinfo=TAIPEI)
    cutoff = time.fromisoformat(str(cfg["latestEntryTime"]))
    checks = (
        (market_crashing, "大盤急跌"), (not quote_reliable, "即時行情不可靠"),
        (volume < int(cfg["minimumVolume"]), "成交量不足"),
        (dec(turnover) < dec(cfg["minimumTurnover"]), "成交金額不足"),
        (dec(spread_pct) > dec(cfg["maximumSpreadPct"]), "買賣價差過大"),
        (abs(dec(vwap_deviation_pct)) > dec(cfg["maximumVwapDeviationPct"]), "偏離VWAP過遠"),
        (blocked, "股票位於禁止名單"), (not connection_ok, "系統或券商連線異常"),
        (dec(available_capital) <= 0, "可用資金不足"),
        (open_positions >= int(cfg["maxOpenPositions"]), "超過同時持倉上限"),
        (sector_positions >= int(cfg["maxSectorPositions"]), "超過產業集中限制"),
        (risk_status(realized_pnl, cfg) == "HALTED", "已達單日虧損上限"),
        (local.time() >= cutoff, "超過允許建立新部位時間"),
    )
    return [reason for failed, reason in checks if failed]


@dataclass(frozen=True)
class MinuteBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    confidence: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    reasons: tuple[str, ...]


def _vwap(bars: Sequence[MinuteBar]) -> Decimal:
    volume = sum(bar.volume for bar in bars)
    if not volume:
        return bars[-1].close
    return sum((((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in bars), ZERO) / volume


def evaluate_strategies(bars: Sequence[MinuteBar], *, previous_high: object | None = None, previous_low: object | None = None) -> list[StrategySignal]:
    """Evaluate completed bars only. The last bar is the decision bar."""
    if len(bars) < 16:
        return []
    current = bars[-1]
    local_time = current.timestamp.astimezone(TAIPEI).time() if current.timestamp.tzinfo else current.timestamp.time()
    history = bars[:-1]
    current_vwap = _vwap(bars)
    previous_vwap = _vwap(history)
    avg_volume = Decimal(sum(bar.volume for bar in history[-15:])) / min(15, len(history))
    volume_ratio = Decimal(current.volume) / avg_volume if avg_volume else ZERO
    signals: list[StrategySignal] = []

    def add(strategy_id: str, base: int, stop: Decimal, reasons: tuple[str, ...]) -> None:
        entry = current.close
        if stop >= entry:
            return
        confidence = min(Decimal("100"), Decimal(base) + min(volume_ratio * 5, Decimal("15")))
        target = entry + (entry - stop) * Decimal("2")
        signals.append(StrategySignal(strategy_id, confidence.quantize(Decimal("0.1")), entry, stop, target, reasons))

    opening = [bar for bar in bars if time(9, 0) <= (bar.timestamp.astimezone(TAIPEI).time() if bar.timestamp.tzinfo else bar.timestamp.time()) < time(9, 15)]
    if opening and time(9, 15) <= local_time <= time(11, 0):
        opening_high = max(bar.high for bar in opening)
        if current.close > opening_high and current.close > current_vwap and volume_ratio >= Decimal("1.2"):
            add("OPENING_RANGE_BREAKOUT", 68, opening_high, ("突破開盤15分鐘區間", "站在VWAP之上", "突破量能放大"))

    if current.close >= current_vwap and current_vwap > previous_vwap and current.low <= current_vwap * Decimal("1.003") and current.close > current.open and volume_ratio >= Decimal("0.9"):
        add("VWAP_TREND_PULLBACK", 70, min(current.low, current_vwap * Decimal("0.997")), ("VWAP斜率向上", "回踩VWAP未破", "買盤重新出現"))

    resistance = max([bar.high for bar in history[-30:]] + ([dec(previous_high)] if previous_high else []))
    if current.close > resistance and volume_ratio >= Decimal("1.8"):
        add("VOLUME_HIGH_BREAKOUT", 72, resistance, ("爆量突破前高", "相對成交量明顯放大", "價格守住突破平台"))

    support = min([bar.low for bar in history[-20:]] + ([dec(previous_low)] if previous_low else []))
    recent = bars[-4:]
    if min(bar.low for bar in recent) < support and current.close > support and current.close > current.open and volume_ratio >= Decimal("1.0"):
        add("FALSE_BREAKDOWN_REVERSAL", 67, min(bar.low for bar in recent), ("短暫跌破支撐", "限定時間內站回", "反轉K棒確認"))

    if time(12, 0) <= local_time < time(13, 20) and current.close > current_vwap:
        afternoon = [bar for bar in history[-30:] if (bar.timestamp.astimezone(TAIPEI).time() if bar.timestamp.tzinfo else bar.timestamp.time()) >= time(12, 0)]
        if afternoon and current.close > max(bar.high for bar in afternoon) and volume_ratio >= Decimal("1.1"):
            add("AFTERNOON_STRENGTH_BREAKOUT", 69, max(current_vwap, min(bar.low for bar in afternoon[-10:])), ("午後維持VWAP之上", "突破午後整理區間", "量價結構偏多"))
    return signals


def resolve_duplicate_signals(signals: Iterable[StrategySignal]) -> tuple[StrategySignal | None, list[StrategySignal]]:
    ranked = sorted(signals, key=lambda item: item.confidence, reverse=True)
    return (ranked[0], ranked[1:]) if ranked else (None, [])


class BrokerAdapter(Protocol):
    name: str
    live: bool

    def connected(self) -> bool: ...
    def submit(self, order: Mapping[str, object]) -> Mapping[str, object]: ...
    def cancel_all(self) -> Sequence[Mapping[str, object]]: ...
    def synchronize(self) -> Mapping[str, object]: ...


class LiveTradingUnavailable(RuntimeError):
    pass


class DisabledLiveBrokerAdapter:
    name = "尚未設定券商"
    live = True

    def connected(self) -> bool:
        return False

    def submit(self, order: Mapping[str, object]) -> Mapping[str, object]:
        raise LiveTradingUnavailable("真實交易已鎖定：尚未設定並驗證券商API")

    def cancel_all(self) -> Sequence[Mapping[str, object]]:
        raise LiveTradingUnavailable("真實交易已鎖定：尚未設定並驗證券商API")

    def synchronize(self) -> Mapping[str, object]:
        return {"connected": False, "positions": [], "orders": [], "reason": "尚未設定券商API"}


def run_backtest(
    datasets: Mapping[str, Sequence[MinuteBar]], *, strategy_id: str = "ALL",
    config: Mapping[str, object] | None = None, portfolio: bool = True,
) -> dict[str, object]:
    """Minute backtest with next-bar fills and shared capital.

    Signals are calculated from completed bars through index ``i`` and filled
    at bar ``i + 1`` open. This makes future-bar access explicit and testable.
    """
    cfg = merged_config(config)
    initial = dec(cfg["initialCapital"])
    cash = initial
    trades: list[dict[str, object]] = []
    candidates: list[tuple[datetime, str, StrategySignal, MinuteBar, Sequence[MinuteBar]]] = []
    enabled = {item[0] for item in STRATEGIES} if strategy_id == "ALL" else {strategy_id}
    for symbol, bars in datasets.items():
        for index in range(15, len(bars) - 1):
            decision_bars = bars[: index + 1]
            next_bar = bars[index + 1]
            for signal in evaluate_strategies(decision_bars):
                if signal.strategy_id in enabled:
                    candidates.append((signal_time := decision_bars[-1].timestamp, symbol, signal, next_bar, bars[index + 1 :]))
    candidates.sort(key=lambda row: (row[0], -row[2].confidence, row[1]))
    occupied: list[tuple[datetime, str]] = []
    traded_keys: set[tuple[str, datetime.date]] = set()
    for signal_time, symbol, signal, fill_bar, future in candidates:
        day_key = (symbol, signal_time.date())
        if day_key in traded_keys or signal.confidence < dec(cfg["minimumConfidence"]):
            continue
        occupied = [(end, held_symbol) for end, held_symbol in occupied if end > signal_time]
        if portfolio and len(occupied) >= int(cfg["maxOpenPositions"]):
            continue
        allocation = next((amount for sid, _, amount in STRATEGIES if sid == signal.strategy_id), initial)
        capital_limit = min(cash, allocation if portfolio else initial)
        entry = fill_bar.open * (Decimal("1") + dec(cfg["slippageBps"]) / Decimal("10000"))
        risk_budget = dec(cfg["maxRiskPerTrade"])
        if risk_status(sum((dec(row["netPnl"]) for row in trades if row["exitTime"].date() == signal_time.date()), ZERO), cfg) == "REDUCED":
            risk_budget /= 2
        quantity = calculate_position_size(
            price=entry, stop_price=signal.stop_price, risk_budget=risk_budget,
            capital_limit=capital_limit, lot_size=int(cfg["boardLotSize"]),
            allow_odd_lots=bool(cfg["allowOddLots"]),
        )
        if quantity <= 0:
            continue
        exit_bar = future[-1]
        exit_reason = "收盤前強制平倉"
        for bar in future:
            if bar.low <= signal.stop_price:
                exit_bar, exit_reason = bar, "停損"
                break
            if bar.high >= signal.target_price:
                exit_bar, exit_reason = bar, "第一段停利"
                break
        raw_exit = signal.stop_price if exit_reason == "停損" else signal.target_price if exit_reason == "第一段停利" else exit_bar.close
        exit_price = raw_exit * (Decimal("1") - dec(cfg["slippageBps"]) / Decimal("10000"))
        result = calculate_trade_result(
            entry_price=entry, exit_price=exit_price, quantity=quantity,
            commission_rate=cfg["commissionRate"], commission_discount=cfg["commissionDiscount"],
            minimum_commission=cfg["minimumCommission"], tax_rate=cfg["dayTradeTaxRate"],
            slippage_bps=0, other_cost=cfg["otherCost"],
        )
        cash += result["netPnl"]
        trades.append({
            "symbol": symbol, "strategyId": signal.strategy_id, "signalTime": signal_time,
            "entryTime": fill_bar.timestamp, "entryPrice": str(money(entry)), "quantity": quantity,
            "exitTime": exit_bar.timestamp, "exitPrice": str(money(exit_price)), "exitReason": exit_reason,
            "grossPnl": str(result["grossPnl"]), "cost": str(result["total"]), "netPnl": str(result["netPnl"]),
        })
        occupied.append((exit_bar.timestamp, symbol))
        traded_keys.add(day_key)
    return {"summary": performance(trades, initial), "trades": trades, "endingCapital": str(money(cash))}
