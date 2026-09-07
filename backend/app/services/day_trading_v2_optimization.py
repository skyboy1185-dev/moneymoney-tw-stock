from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable, Mapping, Sequence

from .day_trading_v2 import dec, money


@dataclass(frozen=True)
class HealthWindow:
    trade_count: int
    net_pnl: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal
    win_rate: Decimal
    payoff_ratio: Decimal | None
    max_drawdown: Decimal
    consecutive_losses: int


@dataclass(frozen=True)
class HealthDiagnosis:
    status: str
    reasons: tuple[str, ...]
    recommended_action: str
    capital_multiplier: Decimal
    risk_multiplier: Decimal
    metrics: Mapping[str, object]


def _max_drawdown(pnls: Sequence[Decimal]) -> Decimal:
    equity = peak = Decimal("0")
    drawdown = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return money(drawdown)


def calculate_health_window(trades: Iterable[Mapping[str, object]]) -> HealthWindow:
    rows = list(trades)
    pnls = [dec(row.get("netPnl", row.get("net_pnl", 0))) for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    pf = (gross_profit / gross_loss).quantize(Decimal("0.01")) if gross_loss else None
    expectancy = money(sum(pnls, Decimal("0")) / len(pnls)) if pnls else Decimal("0")
    win_rate = (Decimal(len(wins)) / len(pnls) * 100).quantize(Decimal("0.1")) if pnls else Decimal("0")
    avg_win = gross_profit / len(wins) if wins else Decimal("0")
    avg_loss = gross_loss / len(losses) if losses else Decimal("0")
    payoff = (avg_win / avg_loss).quantize(Decimal("0.01")) if avg_loss else None
    streak = longest = 0
    for value in pnls:
        streak = streak + 1 if value < 0 else 0
        longest = max(longest, streak)
    return HealthWindow(len(pnls), money(sum(pnls, Decimal("0"))), pf, expectancy, win_rate, payoff, _max_drawdown(pnls), longest)


def diagnose_health(
    recent_trades: Sequence[Mapping[str, object]], *, trading_day_pnls: Sequence[object] = (),
    actual_slippage_bps: object | None = None, assumed_slippage_bps: object | None = None,
    baseline: Mapping[str, object] | None = None, config: Mapping[str, object] | None = None,
    consecutive_alert_days: int = 0, month_trades: Sequence[Mapping[str, object]] = (),
    historical_trades: Sequence[Mapping[str, object]] = (),
    regime_trades: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
) -> HealthDiagnosis:
    cfg = config or {}
    latest10 = calculate_health_window(recent_trades[-10:])
    latest20 = calculate_health_window(recent_trades[-20:])
    latest50 = calculate_health_window(recent_trades[-50:])
    reasons: list[str] = []
    sufficient = latest20.trade_count >= int(cfg.get("healthMinTrades", 20))
    if sufficient:
        if latest20.net_pnl < 0:
            reasons.append("最近20筆淨損益為負")
        if latest20.profit_factor is not None and latest20.profit_factor < dec(cfg.get("healthMinProfitFactor", "1")):
            reasons.append("最近20筆獲利因子低於1")
        if latest20.expectancy < 0:
            reasons.append("最近20筆平均期望值為負")
        if latest20.consecutive_losses >= int(cfg.get("healthConsecutiveLossLimit", 5)):
            reasons.append("連續虧損超過策略健康限制")
    day_values = [dec(value) for value in trading_day_pnls[-20:]]
    if len(day_values) >= 20 and _max_drawdown(day_values) > dec(cfg.get("health20DayDrawdownLimit", "60000")):
        reasons.append("最近20個交易日最大回撤超過限制")
    if latest20.trade_count >= 10 and actual_slippage_bps is not None and assumed_slippage_bps is not None:
        if dec(actual_slippage_bps) >= dec(assumed_slippage_bps) * dec(cfg.get("healthSlippageMultiple", "2")):
            reasons.append("實際成交滑價明顯高於假設")
    if sufficient and baseline:
        baseline_win = dec(baseline.get("winRate", 0))
        baseline_payoff = dec(baseline.get("payoffRatio", 0))
        if baseline_win and latest20.win_rate < baseline_win * Decimal("0.75"):
            reasons.append("近期勝率明顯低於歷史基準")
        if baseline_payoff and latest20.payoff_ratio is not None and latest20.payoff_ratio < baseline_payoff * Decimal("0.75"):
            reasons.append("近期賺賠比明顯低於歷史基準")
    regime_metrics: dict[str, object] = {}
    for regime, trades in (regime_trades or {}).items():
        window = calculate_health_window(trades)
        regime_metrics[regime] = health_window_dict(window)
        if window.trade_count >= int(cfg.get("healthMinRegimeTrades", 10)) and window.expectancy < 0:
            reasons.append(f"{regime}盤勢下持續虧損")
    status = "ALERT" if reasons else "NORMAL" if sufficient or len(day_values) >= 20 else "INSUFFICIENT"
    pause = status == "ALERT" and consecutive_alert_days >= int(cfg.get("healthPauseAfterAlertDays", 2)) - 1
    action = "PAUSE" if pause else "REDUCE" if status == "ALERT" else "NONE"
    multiplier = Decimal("0") if pause else dec(cfg.get("healthAlertRiskMultiplier", "0.5")) if status == "ALERT" else Decimal("1")
    day5 = calculate_health_window({"netPnl": value} for value in day_values[-5:])
    day20 = calculate_health_window({"netPnl": value} for value in day_values[-20:])
    metrics = {
        "last10": health_window_dict(latest10), "last20": health_window_dict(latest20),
        "last50": health_window_dict(latest50), "last5TradingDays": health_window_dict(day5),
        "last20TradingDays": health_window_dict(day20),
        "month": health_window_dict(calculate_health_window(month_trades)),
        "historical": health_window_dict(calculate_health_window(historical_trades)),
        "byMarketRegime": regime_metrics,
        "tradingDayCount": len(day_values), "baselineAvailable": bool(baseline),
        "actualSlippageBps": str(dec(actual_slippage_bps)) if actual_slippage_bps is not None else None,
        "assumedSlippageBps": str(dec(assumed_slippage_bps)) if assumed_slippage_bps is not None else None,
    }
    return HealthDiagnosis(status, tuple(reasons), action, multiplier, multiplier, metrics)


def health_window_dict(window: HealthWindow) -> dict[str, object]:
    return {
        "tradeCount": window.trade_count, "netPnl": str(window.net_pnl),
        "profitFactor": str(window.profit_factor) if window.profit_factor is not None else None,
        "expectancy": str(window.expectancy), "winRate": str(window.win_rate),
        "payoffRatio": str(window.payoff_ratio) if window.payoff_ratio is not None else None,
        "maxDrawdown": str(window.max_drawdown), "consecutiveLosses": window.consecutive_losses,
    }


def parameter_checksum(parameters: Mapping[str, object]) -> str:
    payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def bounded_parameter_candidates(
    champion: Mapping[str, object], whitelist: Mapping[str, tuple[object, object]],
    changes: Sequence[Decimal] = (Decimal("-0.20"), Decimal("-0.10"), Decimal("0.10"), Decimal("0.20")),
) -> list[dict[str, Decimal]]:
    base = {key: dec(value) for key, value in champion.items() if key in whitelist}
    candidates: list[dict[str, Decimal]] = [dict(base)]
    for key, value in base.items():
        low, high = map(dec, whitelist[key])
        for change in changes:
            row = dict(base)
            row[key] = max(low, min(high, value * (Decimal("1") + change)))
            if row not in candidates:
                candidates.append(row)
    return candidates


def walk_forward_splits(
    trading_days: Sequence[date], *, train_days: int = 120, validation_days: int = 20,
    oos_days: int = 20, step_days: int = 20,
) -> list[dict[str, tuple[date, date]]]:
    days = sorted(set(trading_days))
    width = train_days + validation_days + oos_days
    result: list[dict[str, tuple[date, date]]] = []
    for start in range(0, len(days) - width + 1, step_days):
        train_end = start + train_days - 1
        validation_end = train_end + validation_days
        oos_end = validation_end + oos_days
        result.append({
            "train": (days[start], days[train_end]),
            "validation": (days[train_end + 1], days[validation_end]),
            "oos": (days[validation_end + 1], days[oos_end]),
        })
    return result


def candidate_passes(
    result: Mapping[str, object], champion: Mapping[str, object], *, config: Mapping[str, object] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    cfg = config or {}
    failures: list[str] = []
    checks = (
        (int(result.get("tradeCount", 0)) < int(cfg.get("optimizationMinOosTrades", 100)), "樣本外交易不足100筆"),
        (dec(result.get("netPnl", 0)) <= 0, "樣本外淨損益未為正"),
        (dec(result.get("profitFactor", 0)) < dec(cfg.get("optimizationMinProfitFactor", "1.2")), "樣本外獲利因子低於1.2"),
        (dec(result.get("netPnl", 0)) < dec(champion.get("netPnl", 0)), "淨損益低於正式策略"),
        (dec(result.get("payoffRatio", 0)) < dec(champion.get("payoffRatio", 0)), "賺賠比低於正式策略"),
        (dec(result.get("maxDrawdown", 0)) > dec(champion.get("maxDrawdown", 0)), "最大回撤高於正式策略"),
        (dec(result.get("topTwoProfitSharePct", 100)) > dec(cfg.get("optimizationMaxTopTwoProfitSharePct", "35")), "過度依賴少數極端獲利"),
        (dec(result.get("topSymbolProfitSharePct", 100)) > dec(cfg.get("optimizationMaxSymbolProfitSharePct", "25")), "過度依賴單一股票"),
        (dec(result.get("topSectorProfitSharePct", 100)) > dec(cfg.get("optimizationMaxSectorProfitSharePct", "40")), "過度依賴單一產業"),
        (int(result.get("monthCount", 0)) < 3, "樣本外月份不足三個月"),
        (dec(result.get("nonNegativeMonthPct", 0)) < dec(cfg.get("optimizationMinNonNegativeMonthPct", "60")), "跨月份穩定度不足"),
        (dec(result.get("maxParticipationPct", 100)) > dec(cfg.get("optimizationMaxParticipationPct", "5")), "市場容量不足300萬元操作"),
        (not bool(result.get("neighborStable", False)), "參數小幅變動後不穩定"),
    )
    failures.extend(message for failed, message in checks if failed)
    return not failures, tuple(failures)


def challenger_ready(
    *, full_trading_days: int, trade_count: int, net_pnl: object,
    max_drawdown: object, champion_max_drawdown: object, error_count: int,
) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    if full_trading_days < 10 and trade_count < 30:
        failures.append("尚未完成10個交易日或30筆模擬交易")
    if dec(net_pnl) <= 0:
        failures.append("完整成本後淨損益未為正")
    if dec(max_drawdown) > dec(champion_max_drawdown):
        failures.append("最大回撤高於正式策略")
    if error_count:
        failures.append("挑戰者存在資料、下單或風控錯誤")
    return not failures, tuple(failures)


def next_version(existing: Sequence[str], base_major: int = 2, base_minor: int = 0) -> str:
    patches = []
    for version in existing:
        parts = version.split(".")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
            if int(parts[0]) == base_major and int(parts[1]) == base_minor:
                patches.append(int(parts[2]))
    return f"{base_major}.{base_minor}.{max(patches, default=-1) + 1}"
