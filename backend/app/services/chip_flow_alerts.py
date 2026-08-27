from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
import math
from typing import Any, Iterable, Protocol, Sequence, cast
from zoneinfo import ZoneInfo

from .chip_flow_repository import ChipFlowRepository
from .chip_flow_service import ChipFlowService, chip_flow_service
from .day_trading_restrictions import (
    DayTradingRestrictionService,
    day_trading_restrictions,
)
from .theme_stock_universe import (
    CPO_THEME,
    ELECTRONIC_ALERT_STOCKS,
    PACKAGING_TEST_THEME,
    POWER_THEME,
    THEME_STOCKS_BY_SYMBOL,
    ThemeStock,
)
from .popular_stock_universe import (
    POPULAR_ALERT_FALLBACK_STOCKS,
    OfficialPopularStockProvider,
    merge_momentum_stocks,
)


TAIPEI = ZoneInfo("Asia/Taipei")
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(13, 30)
PINNED_CLIENT_TTL = timedelta(hours=24)
EXPANDED_TRACKING_TTL = timedelta(seconds=15)
FAST_POPULAR_LIMIT = 50
FAST_POPULAR_BATCH_SIZE = 4
PRIORITY_CYCLE_TARGET_SECONDS = 5
HOT_CYCLE_TARGET_SECONDS = 10
BACKGROUND_CYCLE_TARGET_SECONDS = 90
CANDIDATE_TARGET = 300
MOMENTUM_RANK_LIMIT = 10
EXTRA_PINNED_TRACKING_LIMIT = 10
MAX_PRIORITY_BATCH_SIZE = 20
MAX_HOT_BATCH_SIZE = 8
MAX_BACKGROUND_BATCH_SIZE = 8
MAX_CONCURRENT_STOCK_SCANS = 4
logger = logging.getLogger(__name__)


class ChipFlowAlertSnapshot(Protocol):
    @property
    def snapshot_time(self) -> datetime: ...

    @property
    def large_buy_shares(self) -> int: ...

    @property
    def large_sell_shares(self) -> int: ...

    @property
    def large_net_shares(self) -> int: ...

    @property
    def small_buy_shares(self) -> int: ...

    @property
    def small_sell_shares(self) -> int: ...

    @property
    def small_net_shares(self) -> int: ...

    @property
    def updated_at(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class ChipFlowAlertRules:
    window_minutes: int = 5
    min_recent_net_lots: float = 10.0
    min_buy_sell_ratio: float = 1.5
    min_positive_steps: int = 2
    max_stale_minutes: int = 10
    lifecycle_minutes: int = 15
    sudden_drop_ratio: float = 0.35
    min_momentum_change_lots: float = 2.0
    min_sudden_drop_lots: float = 5.0


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=TAIPEI) if value.tzinfo is None else value.astimezone(TAIPEI)


def _metrics_series(
    ordered: Sequence[ChipFlowAlertSnapshot],
    rules: ChipFlowAlertRules,
) -> list[dict[str, object]]:
    """Calculate every rolling window in O(n) for low-latency ticker polling."""
    if len(ordered) < 3:
        return []
    times = [_aware(item.snapshot_time) for item in ordered]
    last_large_order_times: list[datetime | None] = [
        times[0]
        if ordered[0].large_buy_shares > 0 or ordered[0].large_sell_shares > 0
        else None
    ]
    positive_prefix = [0]
    negative_prefix = [0]
    small_positive_prefix = [0]
    for previous, current in zip(ordered, ordered[1:]):
        changed = (
            current.large_buy_shares != previous.large_buy_shares
            or current.large_sell_shares != previous.large_sell_shares
        )
        last_large_order_times.append(
            _aware(current.snapshot_time) if changed else last_large_order_times[-1]
        )
        positive_prefix.append(
            positive_prefix[-1]
            + int(current.large_net_shares > previous.large_net_shares)
        )
        negative_prefix.append(
            negative_prefix[-1]
            + int(current.large_net_shares < previous.large_net_shares)
        )
        small_positive_prefix.append(
            small_positive_prefix[-1]
            + int(current.small_net_shares > previous.small_net_shares)
        )

    result: list[dict[str, object]] = []
    reference_index = 0
    window = timedelta(minutes=rules.window_minutes)
    for index in range(2, len(ordered)):
        latest = ordered[index]
        latest_time = times[index]
        cutoff = latest_time - window
        while (
            reference_index + 1 <= index
            and times[reference_index + 1] <= cutoff
        ):
            reference_index += 1
        if index - reference_index + 1 < 3:
            continue
        reference = ordered[reference_index]
        recent_buy_shares = max(0, latest.large_buy_shares - reference.large_buy_shares)
        recent_sell_shares = max(0, latest.large_sell_shares - reference.large_sell_shares)
        recent_small_buy_shares = max(0, latest.small_buy_shares - reference.small_buy_shares)
        recent_small_sell_shares = max(0, latest.small_sell_shares - reference.small_sell_shares)
        recent_net_shares = latest.large_net_shares - reference.large_net_shares
        recent_small_net_shares = latest.small_net_shares - reference.small_net_shares
        positive_steps = positive_prefix[index] - positive_prefix[reference_index]
        negative_steps = negative_prefix[index] - negative_prefix[reference_index]
        small_positive_steps = small_positive_prefix[index] - small_positive_prefix[reference_index]
        buy_sell_ratio = (
            recent_buy_shares / recent_sell_shares
            if recent_sell_shares > 0
            else None if recent_buy_shares == 0
            else 99.0
        )
        sell_buy_ratio = (
            recent_sell_shares / recent_buy_shares
            if recent_buy_shares > 0
            else None if recent_sell_shares == 0
            else 99.0
        )
        recent_gross_large_lots = (recent_buy_shares + recent_sell_shares) / 1_000
        effective_net_threshold_lots = max(
            rules.min_recent_net_lots,
            min(100.0, recent_gross_large_lots * 0.12),
        )
        large_order_offsetting = (
            recent_buy_shares > 0
            and recent_sell_shares > 0
            and recent_gross_large_lots >= rules.min_recent_net_lots * 2
            and abs(recent_net_shares) / (recent_buy_shares + recent_sell_shares) <= 0.2
        )
        qualifies = (
            latest.large_net_shares > 0
            and recent_net_shares >= effective_net_threshold_lots * 1_000
            and buy_sell_ratio is not None
            and buy_sell_ratio >= rules.min_buy_sell_ratio
            and positive_steps >= rules.min_positive_steps
            and not large_order_offsetting
        )
        short_qualifies = (
            latest.large_net_shares < 0
            and recent_net_shares <= -effective_net_threshold_lots * 1_000
            and sell_buy_ratio is not None
            and sell_buy_ratio >= rules.min_buy_sell_ratio
            and negative_steps >= rules.min_positive_steps
            and not large_order_offsetting
        )
        simultaneous_increase = (
            recent_net_shares > 0
            and recent_small_net_shares > 0
            and positive_steps > 0
            and small_positive_steps > 0
        )
        result.append({
            "snapshotTime": latest_time,
            "updatedAt": _aware(latest.updated_at),
            "largeNetLots": round(latest.large_net_shares / 1_000, 2),
            "dayLargeBuyLots": round(latest.large_buy_shares / 1_000, 2),
            "dayLargeSellLots": round(latest.large_sell_shares / 1_000, 2),
            "daySmallBuyLots": round(latest.small_buy_shares / 1_000, 2),
            "daySmallSellLots": round(latest.small_sell_shares / 1_000, 2),
            "recentNetBuyLots": round(recent_net_shares / 1_000, 2),
            "recentNetSellLots": round(max(0, -recent_net_shares) / 1_000, 2),
            "recentSmallNetBuyLots": round(recent_small_net_shares / 1_000, 2),
            "combinedNetBuyLots": round((recent_net_shares + recent_small_net_shares) / 1_000, 2),
            "recentBuyLots": round(recent_buy_shares / 1_000, 2),
            "recentSellLots": round(recent_sell_shares / 1_000, 2),
            "recentSmallBuyLots": round(recent_small_buy_shares / 1_000, 2),
            "recentSmallSellLots": round(recent_small_sell_shares / 1_000, 2),
            "buySellRatio": round(buy_sell_ratio, 2) if buy_sell_ratio is not None else 0.0,
            "sellBuyRatio": round(sell_buy_ratio, 2) if sell_buy_ratio is not None else 0.0,
            "positiveSteps": positive_steps,
            "negativeSteps": negative_steps,
            "smallPositiveSteps": small_positive_steps,
            "recentGrossLargeLots": round(recent_gross_large_lots, 2),
            "effectiveNetThresholdLots": round(effective_net_threshold_lots, 2),
            "largeOrderOffsetting": large_order_offsetting,
            "lastLargeOrderAt": last_large_order_times[index],
            "simultaneousIncrease": simultaneous_increase,
            "qualifies": qualifies,
            "shortQualifies": short_qualifies,
        })
    return result


def _public_alert(stock: ThemeStock, metrics: dict[str, object]) -> dict[str, object]:
    snapshot_time = cast(datetime, metrics["snapshotTime"])
    updated_at = cast(datetime, metrics["updatedAt"])
    return {
        "symbol": stock.symbol,
        "name": stock.name,
        "market": stock.market,
        "industry": stock.industry,
        "themes": list(stock.themes),
        "time": snapshot_time.strftime("%H:%M"),
        "largeNetLots": metrics["largeNetLots"],
        "dayLargeBuyLots": metrics["dayLargeBuyLots"],
        "dayLargeSellLots": metrics["dayLargeSellLots"],
        "daySmallBuyLots": metrics["daySmallBuyLots"],
        "daySmallSellLots": metrics["daySmallSellLots"],
        "recentNetBuyLots": metrics["recentNetBuyLots"],
        "recentSmallNetBuyLots": metrics["recentSmallNetBuyLots"],
        "combinedNetBuyLots": metrics["combinedNetBuyLots"],
        "recentBuyLots": metrics["recentBuyLots"],
        "recentSellLots": metrics["recentSellLots"],
        "recentSmallBuyLots": metrics["recentSmallBuyLots"],
        "recentSmallSellLots": metrics["recentSmallSellLots"],
        "buySellRatio": metrics["buySellRatio"],
        "positiveSteps": metrics["positiveSteps"],
        "smallPositiveSteps": metrics["smallPositiveSteps"],
        "recentGrossLargeLots": metrics["recentGrossLargeLots"],
        "effectiveNetThresholdLots": metrics["effectiveNetThresholdLots"],
        "largeOrderOffsetting": metrics["largeOrderOffsetting"],
        "lastLargeOrderAt": (
            cast(datetime, metrics["lastLargeOrderAt"]).isoformat()
            if metrics["lastLargeOrderAt"] is not None else None
        ),
        "simultaneousIncrease": metrics["simultaneousIncrease"],
        "updatedAt": updated_at.isoformat(),
    }


def build_market_order_pulse(
    rows_by_symbol: dict[str, Sequence[ChipFlowAlertSnapshot]],
    rules: ChipFlowAlertRules,
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Aggregate fresh monitored-stock flow without implying futures trader identity."""
    latest_metrics: list[dict[str, object]] = []
    previous_metrics: list[dict[str, object]] = []
    latest_update: datetime | None = None
    current = _aware(as_of)
    for rows in rows_by_symbol.values():
        series = _metrics_series(
            sorted(rows, key=lambda item: _aware(item.snapshot_time)),
            rules,
        )
        if not series:
            continue
        latest = series[-1]
        updated_at = cast(datetime, latest["updatedAt"])
        if current - updated_at > timedelta(minutes=rules.max_stale_minutes):
            continue
        latest_metrics.append(latest)
        previous_metrics.append(series[-2] if len(series) > 1 else latest)
        latest_update = max(latest_update, updated_at) if latest_update else updated_at

    if not latest_metrics:
        return {
            "status": "warming",
            "direction": "neutral",
            "directionLabel": "資料暖機中",
            "trend": "neutral",
            "trendLabel": "多空資料待補",
            "largeNetLots": 0.0,
            "largeChangeLots": 0.0,
            "smallNetLots": 0.0,
            "smallChangeLots": 0.0,
            "combinedNetLots": 0.0,
            "coverageCount": 0,
            "updatedAt": None,
            "isEstimate": True,
            "source": "監控池逐筆成交方向聚合推估",
        }

    def total(items: Sequence[dict[str, object]], field: str) -> float:
        return sum(float(str(item[field])) for item in items)

    large_net = total(latest_metrics, "recentNetBuyLots")
    previous_large_net = total(previous_metrics, "recentNetBuyLots")
    small_net = total(latest_metrics, "recentSmallNetBuyLots")
    previous_small_net = total(previous_metrics, "recentSmallNetBuyLots")
    large_change = large_net - previous_large_net
    small_change = small_net - previous_small_net
    gross_large = total(latest_metrics, "recentGrossLargeLots")
    combined = large_net + small_net * 0.25
    combined_change = large_change + small_change * 0.25
    direction_band = max(10.0, gross_large * 0.02)
    change_band = max(3.0, direction_band * 0.25)

    if combined >= direction_band:
        direction, direction_label = "bullish", "多方"
        if combined_change >= change_band:
            trend, trend_label = "bull_strengthening", "多方持續增強"
        elif combined_change <= -change_band:
            trend, trend_label = "bull_weakening", "多方轉弱"
        else:
            trend, trend_label = "bull_stable", "多方維持"
    elif combined <= -direction_band:
        direction, direction_label = "bearish", "空方"
        if combined_change <= -change_band:
            trend, trend_label = "bear_strengthening", "空方持續轉強"
        elif combined_change >= change_band:
            trend, trend_label = "bear_weakening", "空方轉弱"
        else:
            trend, trend_label = "bear_stable", "空方維持"
    else:
        direction, direction_label = "neutral", "盤整"
        trend, trend_label = "neutral", "多空拉鋸"

    return {
        "status": "realtime",
        "direction": direction,
        "directionLabel": direction_label,
        "trend": trend,
        "trendLabel": trend_label,
        "largeNetLots": round(large_net, 2),
        "largeChangeLots": round(large_change, 2),
        "smallNetLots": round(small_net, 2),
        "smallChangeLots": round(small_change, 2),
        "combinedNetLots": round(combined, 2),
        "coverageCount": len(latest_metrics),
        "updatedAt": latest_update.isoformat() if latest_update else None,
        "isEstimate": True,
        "source": "監控池逐筆成交方向聚合推估",
    }


def evaluate_large_order_surge(
    stock: ThemeStock,
    snapshots: Sequence[ChipFlowAlertSnapshot],
    rules: ChipFlowAlertRules,
    *,
    as_of: datetime | None = None,
) -> dict[str, object] | None:
    if len(snapshots) < 3:
        return None
    ordered = sorted(snapshots, key=lambda item: _aware(item.snapshot_time))
    latest = ordered[-1]
    latest_time = _aware(latest.snapshot_time)
    if as_of is not None and _aware(as_of) - latest_time > timedelta(minutes=rules.max_stale_minutes):
        return None
    series = _metrics_series(ordered, rules)
    metrics = series[-1] if series else None
    if metrics is None or not bool(metrics["qualifies"]):
        return None
    return _public_alert(stock, metrics)


def evaluate_large_order_short_surge(
    stock: ThemeStock,
    snapshots: Sequence[ChipFlowAlertSnapshot],
    rules: ChipFlowAlertRules,
    *,
    as_of: datetime | None = None,
) -> dict[str, object] | None:
    """Confirm persistent large-order selling for a short-entry candidate."""
    if len(snapshots) < 3:
        return None
    ordered = sorted(snapshots, key=lambda item: _aware(item.snapshot_time))
    latest_time = _aware(ordered[-1].snapshot_time)
    if as_of is not None and _aware(as_of) - latest_time > timedelta(minutes=rules.max_stale_minutes):
        return None
    series = _metrics_series(ordered, rules)
    metrics = series[-1] if series else None
    if metrics is None or not bool(metrics["shortQualifies"]):
        return None
    return {
        **_public_alert(stock, metrics),
        "recentNetSellLots": metrics["recentNetSellLots"],
        "sellBuyRatio": metrics["sellBuyRatio"],
        "negativeSteps": metrics["negativeSteps"],
    }


def analyze_large_order_momentum(
    stock: ThemeStock,
    snapshots: Sequence[ChipFlowAlertSnapshot],
    rules: ChipFlowAlertRules,
    *,
    as_of: datetime | None = None,
    keep_tracking: bool = False,
) -> dict[str, object] | None:
    """Keep a detected surge alive and classify its subsequent momentum."""
    if len(snapshots) < 3:
        return None
    ordered = sorted(snapshots, key=lambda item: _aware(item.snapshot_time))
    latest_time = _aware(ordered[-1].snapshot_time)
    if as_of is not None and _aware(as_of) - latest_time > timedelta(minutes=rules.max_stale_minutes):
        return None
    metrics = _metrics_series(ordered, rules)
    qualifying = [item for item in metrics if bool(item["qualifies"])]
    if not qualifying:
        return None
    last_detected_at = cast(datetime, qualifying[-1]["snapshotTime"])
    if (
        not keep_tracking
        and latest_time - last_detected_at > timedelta(minutes=rules.lifecycle_minutes)
    ):
        return None

    first_detected_at = cast(datetime, qualifying[0]["snapshotTime"])
    cycle_started_at = first_detected_at
    for prior, current in zip(qualifying, qualifying[1:]):
        prior_time = cast(datetime, prior["snapshotTime"])
        current_time = cast(datetime, current["snapshotTime"])
        if current_time - prior_time > timedelta(minutes=rules.lifecycle_minutes):
            cycle_started_at = current_time
    lifecycle = [
        item for item in metrics
        if cast(datetime, item["snapshotTime"]) >= cycle_started_at
    ]
    cycle_qualifying_count = sum(bool(item["qualifies"]) for item in lifecycle)
    latest = lifecycle[-1]
    previous = lifecycle[-2] if len(lifecycle) > 1 else latest
    current_momentum = float(latest["recentNetBuyLots"])
    previous_momentum = float(previous["recentNetBuyLots"])
    momentum_change = round(current_momentum - previous_momentum, 2)
    momentum_change_percent = (
        round(momentum_change / abs(previous_momentum) * 100, 1)
        if previous_momentum else 0.0
    )
    peak_momentum = max(float(item["recentNetBuyLots"]) for item in lifecycle)
    changes = [
        float(current["recentNetBuyLots"]) - float(prior["recentNetBuyLots"])
        for prior, current in zip(lifecycle, lifecycle[1:])
    ]
    threshold = rules.min_momentum_change_lots
    strengthening_streak = 0
    weakening_streak = 0
    for change in reversed(changes):
        if change >= threshold and weakening_streak == 0:
            strengthening_streak += 1
        else:
            break
    for change in reversed(changes):
        if change <= -threshold and strengthening_streak == 0:
            weakening_streak += 1
        else:
            break

    sudden_drop = (
        previous_momentum > 0
        and momentum_change <= -max(
            rules.min_sudden_drop_lots,
            previous_momentum * rules.sudden_drop_ratio,
        )
    )
    materially_faded = (
        current_momentum <= 0
        or (
            peak_momentum >= rules.min_recent_net_lots
            and current_momentum <= peak_momentum * 0.45
        )
    )
    is_warning = sudden_drop or materially_faded or (
        weakening_streak >= 2 and current_momentum <= peak_momentum * 0.7
    )
    reinforced = (
        bool(latest["qualifies"])
        and strengthening_streak >= 2
        and current_momentum >= peak_momentum * 0.85
    )
    simultaneous_increase = bool(latest["simultaneousIncrease"])
    recent_small_momentum = float(latest["recentSmallNetBuyLots"])
    combined_momentum = float(latest["combinedNetBuyLots"])

    if is_warning and materially_faded:
        trend, trend_label, alert_level = "fading", "大單急退", "critical"
        message = f"大單動能由高峰 {peak_momentum:g} 張降至 {current_momentum:g} 張，留意買盤退潮。"
    elif is_warning:
        trend, trend_label, alert_level = "weakening", "連續轉弱", "warning"
        message = f"大單動能連續減少 {max(1, weakening_streak)} 次，本次 {momentum_change:+g} 張。"
    elif reinforced:
        trend, trend_label, alert_level = "strengthening", "持續轉強", "positive"
        message = f"大單動能連續增強 {strengthening_streak} 次，目前近 {rules.window_minutes} 分鐘 +{current_momentum:g} 張。"
    elif cycle_qualifying_count <= 2:
        trend, trend_label, alert_level = "starting", "剛啟動", "info"
        message = f"首次偵測大單啟動，近 {rules.window_minutes} 分鐘 +{current_momentum:g} 張。"
    elif not bool(latest["qualifies"]) or momentum_change <= -threshold:
        trend, trend_label, alert_level = "weakening", "動能轉弱", "warning"
        message = f"大單仍在追蹤，但本次動能較前次 {momentum_change:+g} 張。"
    else:
        trend, trend_label, alert_level = "sustained", "維持強勢", "positive"
        message = f"大單維持強勢，今日已符合條件 {len(qualifying)} 次。"

    if simultaneous_increase:
        message += (
            f" 大小單同步增加：大單 {current_momentum:+g} 張、"
            f"小單 {recent_small_momentum:+g} 張、合力 {combined_momentum:+g} 張。"
        )

    history: list[dict[str, object]] = []
    previous_value: float | None = None
    for item in lifecycle[-20:]:
        value = float(item["recentNetBuyLots"])
        change = 0.0 if previous_value is None else round(value - previous_value, 2)
        history.append({
            "time": cast(datetime, item["snapshotTime"]).strftime("%H:%M"),
            "recentNetBuyLots": value,
            "recentSmallNetBuyLots": float(item["recentSmallNetBuyLots"]),
            "combinedNetBuyLots": float(item["combinedNetBuyLots"]),
            "changeLots": change,
            "qualified": bool(item["qualifies"]),
            "simultaneousIncrease": bool(item["simultaneousIncrease"]),
        })
        previous_value = value

    return {
        **_public_alert(stock, latest),
        "occurrenceCount": len(qualifying),
        "firstDetectedAt": first_detected_at.isoformat(),
        "cycleStartedAt": cycle_started_at.isoformat(),
        "lastDetectedAt": last_detected_at.isoformat(),
        "peakRecentNetBuyLots": round(peak_momentum, 2),
        "momentumChangeLots": momentum_change,
        "momentumChangePercent": momentum_change_percent,
        "trend": trend,
        "trendLabel": trend_label,
        "trendStreak": max(strengthening_streak, weakening_streak),
        "alertLevel": alert_level,
        "isWarning": is_warning,
        "reinforced": reinforced,
        "simultaneousIncrease": simultaneous_increase,
        "currentQualifies": bool(latest["qualifies"]),
        "message": message,
        "history": history,
    }


def analyze_large_order_short_momentum(
    stock: ThemeStock,
    snapshots: Sequence[ChipFlowAlertSnapshot],
    rules: ChipFlowAlertRules,
    *,
    as_of: datetime | None = None,
    keep_tracking: bool = False,
) -> dict[str, object] | None:
    """Track persistent large-order selling as an independent short-side radar."""
    if len(snapshots) < 3:
        return None
    ordered = sorted(snapshots, key=lambda item: _aware(item.snapshot_time))
    latest_time = _aware(ordered[-1].snapshot_time)
    if as_of is not None and _aware(as_of) - latest_time > timedelta(minutes=rules.max_stale_minutes):
        return None
    metrics = _metrics_series(ordered, rules)
    qualifying = [item for item in metrics if bool(item["shortQualifies"])]
    if not qualifying:
        return None
    last_detected_at = cast(datetime, qualifying[-1]["snapshotTime"])
    if not keep_tracking and latest_time - last_detected_at > timedelta(minutes=rules.lifecycle_minutes):
        return None

    first_detected_at = cast(datetime, qualifying[0]["snapshotTime"])
    cycle_started_at = first_detected_at
    for prior, current in zip(qualifying, qualifying[1:]):
        prior_time = cast(datetime, prior["snapshotTime"])
        current_time = cast(datetime, current["snapshotTime"])
        if current_time - prior_time > timedelta(minutes=rules.lifecycle_minutes):
            cycle_started_at = current_time
    lifecycle = [
        item for item in metrics
        if cast(datetime, item["snapshotTime"]) >= cycle_started_at
    ]
    latest = lifecycle[-1]
    previous = lifecycle[-2] if len(lifecycle) > 1 else latest
    current_force = float(latest["recentNetSellLots"])
    previous_force = float(previous["recentNetSellLots"])
    force_change = round(current_force - previous_force, 2)
    peak_force = max(float(item["recentNetSellLots"]) for item in lifecycle)
    changes = [
        float(current["recentNetSellLots"]) - float(prior["recentNetSellLots"])
        for prior, current in zip(lifecycle, lifecycle[1:])
    ]
    threshold = rules.min_momentum_change_lots
    strengthening_streak = 0
    weakening_streak = 0
    for change in reversed(changes):
        if change >= threshold:
            strengthening_streak += 1
        else:
            break
    for change in reversed(changes):
        if change <= -threshold:
            weakening_streak += 1
        else:
            break

    current_qualifies = bool(latest["shortQualifies"])
    materially_faded = current_force <= 0 or (
        peak_force >= rules.min_recent_net_lots and current_force <= peak_force * 0.45
    )
    is_warning = materially_faded or weakening_streak >= 2
    reinforced = current_qualifies and strengthening_streak >= 2 and current_force >= peak_force * 0.85
    simultaneous_selling = (
        float(latest["recentNetBuyLots"]) < 0
        and float(latest["recentSmallNetBuyLots"]) < 0
    )
    occurrence_count = sum(bool(item["shortQualifies"]) for item in lifecycle)

    if is_warning and materially_faded:
        trend, trend_label, alert_level = "fading", "賣壓急退", "warning"
        message = f"空方大單由高峰 {peak_force:g} 張降至 {current_force:g} 張，追空力道退潮。"
    elif is_warning:
        trend, trend_label, alert_level = "weakening", "賣壓轉弱", "warning"
        message = f"空方大單連續減弱 {weakening_streak} 次，本次賣壓變化 {force_change:+g} 張。"
    elif reinforced:
        trend, trend_label, alert_level = "strengthening", "持續加空", "critical"
        message = f"大戶賣壓連續增強 {strengthening_streak} 次，近 {rules.window_minutes} 分鐘賣超 {current_force:g} 張。"
    elif occurrence_count <= 2:
        trend, trend_label, alert_level = "starting", "空方啟動", "info"
        message = f"首次偵測空方大單啟動，近 {rules.window_minutes} 分鐘賣超 {current_force:g} 張。"
    elif not current_qualifies or force_change <= -threshold:
        trend, trend_label, alert_level = "weakening", "賣壓轉弱", "warning"
        message = f"空方仍在追蹤，本次賣壓較前次 {force_change:+g} 張。"
    else:
        trend, trend_label, alert_level = "sustained", "空方維持", "critical"
        message = f"大戶賣壓維持，今日已符合加空條件 {len(qualifying)} 次。"
    if simultaneous_selling:
        message += " 大單與小單同步偏空。"

    history = [{
        "time": cast(datetime, item["snapshotTime"]).strftime("%H:%M"),
        "recentNetBuyLots": float(item["recentNetBuyLots"]),
        "recentSmallNetBuyLots": float(item["recentSmallNetBuyLots"]),
        "combinedNetBuyLots": float(item["combinedNetBuyLots"]),
        "changeLots": round(float(item["recentNetSellLots"]), 2),
        "qualified": bool(item["shortQualifies"]),
        "simultaneousIncrease": (
            float(item["recentNetBuyLots"]) < 0
            and float(item["recentSmallNetBuyLots"]) < 0
        ),
    } for item in lifecycle[-20:]]

    return {
        **_public_alert(stock, latest),
        "direction": "short",
        "recentNetSellLots": round(current_force, 2),
        "sellBuyRatio": latest["sellBuyRatio"],
        "negativeSteps": latest["negativeSteps"],
        "occurrenceCount": len(qualifying),
        "firstDetectedAt": first_detected_at.isoformat(),
        "cycleStartedAt": cycle_started_at.isoformat(),
        "lastDetectedAt": last_detected_at.isoformat(),
        "peakRecentNetBuyLots": round(-peak_force, 2),
        "momentumChangeLots": round(-force_change, 2),
        "momentumChangePercent": round(force_change / previous_force * 100, 1) if previous_force else 0.0,
        "trend": trend,
        "trendLabel": trend_label,
        "trendStreak": max(strengthening_streak, weakening_streak),
        "alertLevel": alert_level,
        "isWarning": is_warning,
        "reinforced": reinforced,
        "simultaneousIncrease": simultaneous_selling,
        "currentQualifies": current_qualifies,
        "message": message,
        "history": history,
    }


def enrich_day_trading_large_order_confirmation(
    candidates: list[dict[str, Any]],
    repository: ChipFlowRepository,
    rules: ChipFlowAlertRules,
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    """Attach real tick-derived continuous large-order confirmation to candidates."""
    current = _aware(as_of)
    symbols = [str(candidate.get("symbol", "")) for candidate in candidates]
    list_many = getattr(repository, "list_many_for_day", None)
    raw_rows_by_symbol = (
        list_many([symbol for symbol in symbols if symbol], current.date())
        if callable(list_many)
        else None
    )
    rows_by_symbol = raw_rows_by_symbol if isinstance(raw_rows_by_symbol, dict) else None
    enriched: list[dict[str, Any]] = []
    for original in candidates:
        item = dict(original)
        symbol = str(item.get("symbol", ""))
        stock = THEME_STOCKS_BY_SYMBOL.get(symbol)
        if stock is None and symbol:
            stock = ThemeStock(
                symbol=symbol,
                name=str(item.get("stockName") or symbol),
                market=str(item.get("market") or "上市"),
                industry=str(item.get("industry") or "市場熱門"),
                themes=tuple(str(theme) for theme in (item.get("themes") or ("熱門股",))),
            )
        rows = (
            rows_by_symbol.get(symbol, [])
            if rows_by_symbol is not None
            else repository.list_for_day(symbol, current.date())
            if stock is not None
            else []
        )
        latest = rows[-1] if rows else None
        latest_time = _aware(latest.snapshot_time) if latest is not None else None
        fresh = (
            latest_time is not None
            and current - latest_time <= timedelta(minutes=rules.max_stale_minutes)
        )
        data_available = len(rows) >= 3 and fresh
        direction = str(item.get("direction") or "long")
        alert = (
            (evaluate_large_order_short_surge if direction == "short" else evaluate_large_order_surge)(
                stock,
                cast(Sequence[ChipFlowAlertSnapshot], rows),
                rules,
                as_of=current,
            )
            if stock is not None and data_available else None
        )
        continuous = alert is not None
        continuous_buy = continuous and direction != "short"
        continuous_sell = continuous and direction == "short"
        latest_net_lots = (
            round(float(latest.large_net_shares) / 1_000, 2)
            if latest is not None else 0.0
        )
        recent_net_lots = (
            -float(str(alert["recentNetSellLots"]))
            if alert and direction == "short"
            else float(str(alert["recentNetBuyLots"])) if alert else 0.0
        )
        directional_steps = int(str(
            alert["negativeSteps"] if direction == "short" else alert["positiveSteps"]
        )) if alert else 0
        directional_ratio = float(str(
            alert["sellBuyRatio"] if direction == "short" else alert["buySellRatio"]
        )) if alert else None
        status = (
            "大戶持續加空" if continuous_sell else "大戶持續加多"
            if continuous
            else "大單資料暖機／延遲"
            if not data_available
            else "大戶尚未持續加空" if direction == "short" else "大戶尚未持續加多"
        )
        reasons = list(item.get("reasons") or [])
        warnings = list(item.get("warnings") or [])
        if continuous_sell:
            reasons.append(
                f"近 {rules.window_minutes} 分鐘大單淨賣超 {abs(recent_net_lots):g} 張，"
                f"連續加空 {directional_steps} 次"
            )
        elif continuous_buy:
            reasons.append(
                f"近 {rules.window_minutes} 分鐘大單淨買超 +{recent_net_lots:g} 張，"
                f"連續加多 {directional_steps} 次"
            )
        elif data_available:
            warnings.append(
                f"大戶未達持續{'加空' if direction == 'short' else '加多'}標準（近 "
                f"{rules.window_minutes} 分鐘至少淨{'賣' if direction == 'short' else '買'}超 "
                f"{rules.min_recent_net_lots:g} 張、方向比 {rules.min_buy_sell_ratio:g}）"
            )
        else:
            warnings.append("等待足夠且未逾時的逐筆成交大單資料")
        item.update({
            "largeOrderDataAvailable": data_available,
            "largeOrderContinuousBuy": continuous_buy,
            "largeOrderContinuousSell": continuous_sell,
            "largeOrderStatus": status,
            "largeOrderNetLots": latest_net_lots,
            "largeOrderRecentNetLots": recent_net_lots,
            "largeOrderBuySellRatio": directional_ratio,
            "largeOrderPositiveSteps": directional_steps if direction != "short" else 0,
            "largeOrderNegativeSteps": directional_steps if direction == "short" else 0,
            "largeOrderDirectionalSteps": directional_steps,
            "largeOrderUpdatedAt": (
                _aware(latest.updated_at).isoformat() if latest is not None else None
            ),
            "largeOrderForce": recent_net_lots,
            "largeOrderIsEstimate": True,
            "reasons": reasons,
            "warnings": warnings,
        })
        enriched.append(item)
    return enriched


def _alert_float(alert: dict[str, object], field: str, default: float = 0.0) -> float:
    value = alert.get(field, default)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _momentum_rank_score(alert: dict[str, object], direction: str) -> float:
    short_side = direction == "short"
    force = (
        _alert_float(alert, "recentNetSellLots")
        if short_side else max(0.0, _alert_float(alert, "recentNetBuyLots"))
    )
    ratio = _alert_float(alert, "sellBuyRatio" if short_side else "buySellRatio")
    steps = _alert_float(alert, "negativeSteps" if short_side else "positiveSteps")
    day_force = (
        max(0.0, -_alert_float(alert, "largeNetLots"))
        if short_side else max(0.0, _alert_float(alert, "largeNetLots"))
    )
    score = 0.0
    if bool(alert.get("currentQualifies")):
        score += 10_000
    if not bool(alert.get("isWarning")):
        score += 2_000
    if bool(alert.get("reinforced")) or alert.get("trend") == "strengthening":
        score += 1_000
    if bool(alert.get("simultaneousIncrease")):
        score += 400
    score += _alert_float(alert, "occurrenceCount") * 120
    score += _alert_float(alert, "trendStreak") * 80
    score += steps * 60
    score += force * 12
    score += min(ratio, 99.0) * 8
    score += day_force * 0.5
    if bool(alert.get("isWarning")):
        score -= 500
    return round(score, 2)


def _rank_momentum_alerts(
    alerts: Sequence[dict[str, object]],
    direction: str,
) -> list[dict[str, object]]:
    ranked = sorted(
        (dict(alert) for alert in alerts),
        key=lambda item: (
            _momentum_rank_score(item, direction),
            str(item.get("symbol", "")),
        ),
        reverse=True,
    )
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
        item["rankScore"] = _momentum_rank_score(item, direction)
    return ranked


class ElectronicChipFlowAlertMonitor:
    def __init__(
        self,
        service: ChipFlowService = chip_flow_service,
        popular_stock_provider: OfficialPopularStockProvider | None = None,
        restriction_service: DayTradingRestrictionService | None = None,
    ):
        from ..config import get_settings

        settings = get_settings()
        self.service = service
        self.popular_stock_provider = popular_stock_provider or OfficialPopularStockProvider()
        self.restriction_service = restriction_service or day_trading_restrictions
        self.rules = ChipFlowAlertRules(
            window_minutes=settings.chip_flow_alert_window_minutes,
            min_recent_net_lots=settings.chip_flow_alert_min_recent_net_lots,
            min_buy_sell_ratio=settings.chip_flow_alert_min_buy_sell_ratio,
            min_positive_steps=settings.chip_flow_alert_min_positive_steps,
            max_stale_minutes=settings.chip_flow_alert_max_stale_minutes,
            lifecycle_minutes=settings.chip_flow_alert_lifecycle_minutes,
            sudden_drop_ratio=settings.chip_flow_alert_sudden_drop_ratio,
            min_momentum_change_lots=settings.chip_flow_alert_min_momentum_change_lots,
            min_sudden_drop_lots=settings.chip_flow_alert_min_sudden_drop_lots,
        )
        self.scan_interval_seconds = max(
            2.0,
            settings.chip_flow_electronic_scan_interval_seconds,
        )
        self._task: asyncio.Task[None] | None = None
        self._index = 0
        self._hot_index = 0
        self._pinned_index = 0
        self._popular_index = 0
        self._scan_sequence = 0
        self._scan_date: date | None = None
        self._scanned_symbols: set[str] = set()
        self._cycle_scanned_symbols: set[str] = set()
        self._hot_symbols: set[str] = set()
        self._auto_top_tracking_symbols: set[str] = set()
        self._day_trading_priority_symbols: set[str] = set()
        self._pinned_clients: dict[str, tuple[datetime, dict[str, ThemeStock]]] = {}
        self._tracking_clients: dict[str, tuple[datetime, dict[str, ThemeStock]]] = {}
        self._last_full_scan_at: datetime | None = None
        self._last_error: str | None = None
        self._last_scan_attempt_at: dict[str, datetime] = {}
        self._last_scan_completed_at: dict[str, datetime] = {}
        self._last_scan_errors: dict[str, str] = {}
        self._stocks, self._popular_symbols = merge_momentum_stocks(())
        self._fast_symbols = tuple(
            stock.symbol for stock in POPULAR_ALERT_FALLBACK_STOCKS
            if stock.symbol in self._popular_symbols
        )
        self._universe_updated_at: datetime | None = None
        self._last_successful_universe_at: datetime | None = None
        self._last_valid_official_popular: tuple[ThemeStock, ...] = ()
        self._universe_status = "fallback"
        self._universe_notice = "熱門排行尚未成功載入，目前保留內建監控池。"
        self._scan_semaphore = asyncio.Semaphore(MAX_CONCURRENT_STOCK_SCANS)
        self._payload_cache: dict[tuple[object, ...], dict[str, object]] = {}
        self._payload_cache_hits = 0
        self._payload_cache_misses = 0

    def _stock_is_allowed(self, stock: ThemeStock) -> bool:
        if self.restriction_service.is_disposed(stock.symbol):
            return False
        restriction_state = self.restriction_service.state
        # Unit/bootstrap callers can inspect the static universe before the first
        # background refresh. After any refresh attempt, fail closed for a market
        # whose official disposal feed is unavailable.
        if restriction_state.get("lastRefreshAt") is None:
            return True
        return self.restriction_service.market_restrictions_available(stock.market)

    def _eligible_stocks(self) -> tuple[ThemeStock, ...]:
        return tuple(stock for stock in self._stocks if self._stock_is_allowed(stock))

    def _active_pinned_stocks(self, now: datetime | None = None) -> dict[str, ThemeStock]:
        current = _aware(now or datetime.now(TAIPEI))
        expired = [
            client_id
            for client_id, (last_seen, _) in self._pinned_clients.items()
            if current - last_seen > PINNED_CLIENT_TTL
        ]
        for client_id in expired:
            del self._pinned_clients[client_id]
        return {
            symbol: stock
            for _, stocks in self._pinned_clients.values()
            for symbol, stock in stocks.items()
            if self._stock_is_allowed(stock)
        }

    def _client_pinned_stocks(self, client_id: str) -> dict[str, ThemeStock]:
        client = self._pinned_clients.get(client_id)
        return {
            symbol: stock for symbol, stock in client[1].items()
            if self._stock_is_allowed(stock)
        } if client is not None else {}

    def set_pinned_symbols(
        self,
        client_id: str,
        symbols: Sequence[str],
        now: datetime | None = None,
    ) -> None:
        """Keep one browser's pins isolated while sharing the priority scanner."""
        current = _aware(now or datetime.now(TAIPEI))
        active_pins = self._active_pinned_stocks(current)
        available = {stock.symbol: stock for stock in self._stocks}
        available.update(active_pins)
        next_pinned: dict[str, ThemeStock] = {}
        for symbol in symbols:
            stock = available.get(symbol)
            if stock is None:
                stock = THEME_STOCKS_BY_SYMBOL.get(symbol)
            if stock is not None and self._stock_is_allowed(stock):
                next_pinned[symbol] = stock
        previous_pinned = self._client_pinned_stocks(client_id)
        if next_pinned:
            self._pinned_clients[client_id] = (current, next_pinned)
        else:
            self._pinned_clients.pop(client_id, None)
        if set(previous_pinned) != set(next_pinned):
            self._payload_cache.clear()
        if not self._active_pinned_stocks(current):
            self._pinned_index = 0

    def _active_tracking_stocks(self, now: datetime | None = None) -> dict[str, ThemeStock]:
        current = _aware(now or datetime.now(TAIPEI))
        expired = [
            client_id
            for client_id, (last_seen, _) in self._tracking_clients.items()
            if current - last_seen > EXPANDED_TRACKING_TTL
        ]
        for client_id in expired:
            del self._tracking_clients[client_id]
        return {
            symbol: stock
            for _, stocks in self._tracking_clients.values()
            for symbol, stock in stocks.items()
            if self._stock_is_allowed(stock)
        }

    def _client_tracking_stocks(self, client_id: str) -> dict[str, ThemeStock]:
        client = self._tracking_clients.get(client_id)
        return {
            symbol: stock for symbol, stock in client[1].items()
            if self._stock_is_allowed(stock)
        } if client is not None else {}

    def set_tracking_symbols(
        self,
        client_id: str,
        symbols: Sequence[str],
        now: datetime | None = None,
    ) -> None:
        """Temporarily prioritize cards visible in an expanded browser panel."""
        current = _aware(now or datetime.now(TAIPEI))
        available = {stock.symbol: stock for stock in self._stocks}
        next_tracking = {
            symbol: stock
            for symbol in symbols
            if (stock := available.get(symbol)) is not None and self._stock_is_allowed(stock)
        }
        previous = self._client_tracking_stocks(client_id)
        if next_tracking:
            self._tracking_clients[client_id] = (current, next_tracking)
        else:
            self._tracking_clients.pop(client_id, None)
        if set(previous) != set(next_tracking):
            self._payload_cache.clear()

    def _extra_pinned_tracking_stocks(
        self,
        auto_top_symbols: set[str],
        now: datetime | None = None,
        client_id: str | None = None,
    ) -> dict[str, ThemeStock]:
        current = _aware(now or datetime.now(TAIPEI))
        self._active_pinned_stocks(current)
        selected: dict[str, ThemeStock] = {}
        for pinned_client_id, (_, stocks) in self._pinned_clients.items():
            if client_id is not None and pinned_client_id != client_id:
                continue
            extra_count = 0
            for symbol, stock in stocks.items():
                if symbol in auto_top_symbols or not self._stock_is_allowed(stock):
                    continue
                selected.setdefault(symbol, stock)
                extra_count += 1
                if extra_count >= EXTRA_PINNED_TRACKING_LIMIT:
                    break
        return selected

    def _high_frequency_symbols(
        self,
        eligible_symbols: set[str],
        now: datetime | None = None,
    ) -> set[str]:
        auto_top_symbols = self._auto_top_tracking_symbols & eligible_symbols
        extra_pinned_symbols = set(
            self._extra_pinned_tracking_stocks(auto_top_symbols, now),
        )
        symbols = (
            auto_top_symbols
            | self._day_trading_priority_symbols
            | extra_pinned_symbols
            | set(self._active_tracking_stocks(now))
        ) & eligible_symbols
        return symbols

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="electronic-chip-flow-alert-monitor")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def stock_universe_snapshot(self) -> tuple[ThemeStock, ...]:
        """Return the exact stock pool currently monitored by the momentum radar."""
        return self._eligible_stocks()

    def set_day_trading_priority_symbols(self, symbols: Iterable[str]) -> None:
        """Promote active day-trading candidates and positions without changing membership."""
        available = {stock.symbol for stock in self._eligible_stocks()}
        self._day_trading_priority_symbols = {
            str(symbol) for symbol in symbols if str(symbol) in available
        }

    def high_frequency_symbols_snapshot(
        self,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        available = {stock.symbol for stock in self._eligible_stocks()}
        symbols = self._high_frequency_symbols(available, now)
        return tuple(sorted(symbols))

    @staticmethod
    def _is_market_open(now: datetime) -> bool:
        local = _aware(now)
        return local.weekday() < 5 and MARKET_OPEN <= local.time() < MARKET_CLOSE

    @staticmethod
    def _idle_sleep_seconds(now: datetime) -> float:
        """Wake exactly at the opening bell instead of up to 30 seconds late."""
        local = _aware(now)
        if local.weekday() < 5 and local.time() < MARKET_OPEN:
            opening = datetime.combine(local.date(), MARKET_OPEN, tzinfo=TAIPEI)
            return max(0.1, min(30.0, (opening - local).total_seconds()))
        return 30.0

    def _reset_for_day(self, trade_date: date) -> None:
        if self._scan_date == trade_date:
            return
        self._scan_date = trade_date
        self._index = 0
        self._hot_index = 0
        self._pinned_index = 0
        self._popular_index = 0
        self._scan_sequence = 0
        self._scanned_symbols.clear()
        self._cycle_scanned_symbols.clear()
        self._hot_symbols.clear()
        self._auto_top_tracking_symbols.clear()
        self._day_trading_priority_symbols.clear()
        self._last_full_scan_at = None
        self._last_error = None
        self._last_scan_attempt_at.clear()
        self._last_scan_completed_at.clear()
        self._last_scan_errors.clear()

    async def _refresh_universe(self, now: datetime) -> None:
        current = _aware(now)
        if (
            self._universe_updated_at is not None
            and current - self._universe_updated_at < timedelta(minutes=15)
        ):
            return
        self._universe_updated_at = current
        try:
            official_popular = await self.popular_stock_provider.fetch()
            if official_popular:
                self._last_valid_official_popular = tuple(official_popular)
                self._last_successful_universe_at = current
                self._universe_status = "healthy"
                self._universe_notice = None
            else:
                self._universe_status = "degraded"
                self._universe_notice = (
                    "官方熱門排行本次回傳空值，已保留上一版監控池，未將候選股縮減。"
                )
            source_popular = (
                tuple(official_popular)
                if official_popular else self._last_valid_official_popular
            )
            if source_popular:
                stocks, self._popular_symbols = merge_momentum_stocks(source_popular)
            else:
                # Cold-start failure must not replace a previously expanded pool
                # with the smaller static fallback universe.
                stocks = self._stocks
            merged = {stock.symbol: stock for stock in stocks}
            for symbol, stock in self._active_pinned_stocks(current).items():
                merged.setdefault(symbol, stock)
            self._stocks = tuple(merged.values())
            if len(self._stocks) < CANDIDATE_TARGET and self._universe_status == "healthy":
                self._universe_status = "degraded"
                self._universe_notice = (
                    f"官方排行目前僅能組成 {len(self._stocks)}/{CANDIDATE_TARGET} 檔監控池；"
                    "系統保留現有名單並持續重試，不以空值補成假資料。"
                )
            active_symbols = {stock.symbol for stock in self._stocks}
            ranked_popular = dict.fromkeys(
                stock.symbol
                for stock in (*POPULAR_ALERT_FALLBACK_STOCKS, *source_popular)
                if stock.symbol in active_symbols and stock.symbol in self._popular_symbols
            )
            self._fast_symbols = tuple(ranked_popular)[:FAST_POPULAR_LIMIT]
            self._scanned_symbols.intersection_update(active_symbols)
            self._cycle_scanned_symbols.intersection_update(active_symbols)
            self._hot_symbols.intersection_update(active_symbols)
            self._auto_top_tracking_symbols.intersection_update(active_symbols)
            self._day_trading_priority_symbols.intersection_update(active_symbols)
            self._payload_cache.clear()
        except Exception as error:
            self._universe_status = "degraded"
            self._universe_notice = f"熱門排行更新失敗，已保留上一版監控池：{error}"
            logger.warning("popular stock universe refresh failed", exc_info=True)

    @staticmethod
    def _take_rotating(
        pool: Sequence[ThemeStock],
        index: int,
        count: int,
    ) -> tuple[list[ThemeStock], int]:
        if not pool or count <= 0:
            return [], 0
        selected = [pool[(index + offset) % len(pool)] for offset in range(min(count, len(pool)))]
        return selected, (index + len(selected)) % len(pool)

    def _next_scan_batch(self) -> list[ThemeStock]:
        eligible_stocks = self._eligible_stocks()
        tracking_stocks = self._active_tracking_stocks()
        auto_top_symbols = self._auto_top_tracking_symbols
        extra_pinned_stocks = self._extra_pinned_tracking_stocks(auto_top_symbols)
        priority_symbols = (
            self._day_trading_priority_symbols
            | auto_top_symbols
            | extra_pinned_stocks.keys()
            | tracking_stocks.keys()
        )
        hot_symbols = self._hot_symbols - priority_symbols
        by_symbol = {stock.symbol: stock for stock in eligible_stocks}
        priority_stocks = [
            stock for stock in eligible_stocks
            if stock.symbol in priority_symbols
        ]
        hot_stocks = [
            stock for stock in eligible_stocks
            if stock.symbol in hot_symbols
        ]
        fast_stocks = [by_symbol[symbol] for symbol in self._fast_symbols if symbol in by_symbol]
        background_stocks = [
            stock for stock in eligible_stocks
            if stock.symbol not in set(self._fast_symbols)
            and stock.symbol not in priority_symbols
            and stock.symbol not in hot_symbols
        ]
        priority_batch_size = min(MAX_PRIORITY_BATCH_SIZE, len(priority_stocks)) if priority_stocks else 0
        hot_batch_size = min(
            MAX_HOT_BATCH_SIZE,
            max(1, math.ceil(
                len(hot_stocks) * self.scan_interval_seconds
                / HOT_CYCLE_TARGET_SECONDS
            )),
        )
        background_batch_size = min(
            MAX_BACKGROUND_BATCH_SIZE,
            max(1, math.ceil(
                len(background_stocks) * self.scan_interval_seconds
                / BACKGROUND_CYCLE_TARGET_SECONDS
            )),
        )
        if priority_stocks:
            # The provider shares a paced REST budget. When a user is actively
            # viewing cards, keep discovery alive but finish the visible batch
            # before spending the cycle on broad-market scans.
            hot_batch_size = min(hot_batch_size, 1)
            background_batch_size = min(background_batch_size, 1)
        priority_batch, self._pinned_index = self._take_rotating(
            priority_stocks, self._pinned_index, priority_batch_size,
        )
        hot_batch, self._hot_index = self._take_rotating(
            hot_stocks, self._hot_index, hot_batch_size,
        )
        fast_batch, self._popular_index = self._take_rotating(
            fast_stocks, self._popular_index, 1 if priority_stocks else FAST_POPULAR_BATCH_SIZE,
        )
        background_batch, self._index = self._take_rotating(
            background_stocks or list(eligible_stocks), self._index, background_batch_size,
        )
        self._scan_sequence += 1
        return list({stock.symbol: stock for stock in (
            *priority_batch, *hot_batch, *fast_batch, *background_batch,
        )}.values())

    async def _scan_stock(
        self,
        stock: ThemeStock,
        trade_date: date,
        pinned_symbols: set[str],
    ) -> str | None:
        from ..database import BackgroundSessionLocal as SessionLocal

        attempted_at = datetime.now(TAIPEI)
        self._last_scan_attempt_at[stock.symbol] = attempted_at
        try:
            with SessionLocal() as db:
                await self.service.get_intraday(stock.symbol, db, trade_date)
                # get_intraday has finished persistence; release its read transaction
                # before doing alert analysis so the connection returns immediately.
                db.close()
            rows = self.service.alert_snapshots_snapshot([stock.symbol], trade_date)[stock.symbol]
            momentum = analyze_large_order_momentum(
                stock,
                cast(Sequence[ChipFlowAlertSnapshot], rows),
                self.rules,
                as_of=datetime.now(TAIPEI),
            )
            short_momentum = analyze_large_order_short_momentum(
                stock,
                cast(Sequence[ChipFlowAlertSnapshot], rows),
                self.rules,
                as_of=datetime.now(TAIPEI),
            )
            if momentum is None and short_momentum is None and stock.symbol not in pinned_symbols:
                self._hot_symbols.discard(stock.symbol)
            else:
                self._hot_symbols.add(stock.symbol)
            self._scanned_symbols.add(stock.symbol)
            self._cycle_scanned_symbols.add(stock.symbol)
            self._last_scan_completed_at[stock.symbol] = datetime.now(TAIPEI)
            self._last_scan_errors.pop(stock.symbol, None)
            eligible_symbols = {item.symbol for item in self._eligible_stocks()}
            if (
                eligible_symbols
                and len(self._cycle_scanned_symbols & eligible_symbols) >= len(eligible_symbols)
            ):
                self._last_full_scan_at = datetime.now(TAIPEI)
                self._cycle_scanned_symbols.clear()
            return None
        except Exception as error:
            self._last_scan_errors[stock.symbol] = str(error)
            logger.exception(
                "electronic chip-flow scan failed",
                extra={"stock_id": stock.symbol, "trade_date": trade_date.isoformat()},
            )
            return f"{stock.symbol}: {error}"

    def _enrich_runtime_alert(
        self,
        alert: dict[str, object],
        current: datetime,
        high_frequency_symbols: set[str],
    ) -> dict[str, object]:
        symbol = str(alert["symbol"])
        scanned_at = self._last_scan_completed_at.get(symbol)
        trade_at_raw = alert.get("updatedAt")
        large_order_at_raw = alert.get("lastLargeOrderAt")
        trade_at = _aware(datetime.fromisoformat(str(trade_at_raw))) if trade_at_raw else None
        large_order_at = (
            _aware(datetime.fromisoformat(str(large_order_at_raw)))
            if large_order_at_raw else None
        )

        def age_seconds(value: datetime | None) -> int | None:
            return max(0, round((current - value).total_seconds())) if value else None

        scan_age = age_seconds(scanned_at)
        trade_age = age_seconds(trade_at)
        large_order_age = age_seconds(large_order_at)
        stale_after = 15 if symbol in high_frequency_symbols else 120
        if scan_age is None:
            state, label = "warming", "資料暖機中"
        elif scan_age > stale_after:
            state, label = "stale", f"掃描延遲 {scan_age} 秒"
        elif bool(alert.get("largeOrderOffsetting")):
            state, label = "offsetting", "多空大單互相抵銷"
        elif large_order_age is None or large_order_age > 20:
            state, label = "no_new_large_order", "暫無新大單成交"
        else:
            state, label = "active", "大單持續更新"

        threshold_snapshot = getattr(self.service, "threshold_snapshot", None)
        threshold: Any = (
            threshold_snapshot(symbol, current.date())
            if callable(threshold_snapshot) else None
        )
        return {
            **alert,
            "lastScannedAt": scanned_at.isoformat() if scanned_at else None,
            "scanAgeSeconds": scan_age,
            "lastTradeAt": trade_at.isoformat() if trade_at else None,
            "tradeAgeSeconds": trade_age,
            "largeOrderAgeSeconds": large_order_age,
            "dataState": state,
            "dataStateLabel": label,
            "scanError": self._last_scan_errors.get(symbol),
            "largeOrderThresholdAmount": threshold.amount if threshold else None,
            "largeOrderThresholdMode": threshold.mode if threshold else None,
            "largeOrderThresholdPercentile": (
                round(threshold.percentile * 100, 2) if threshold else None
            ),
            "largeOrderThresholdSampleCount": threshold.sample_count if threshold else None,
        }

    async def _scan_next(self, trade_date: date) -> None:
        batch = self._next_scan_batch()
        eligible_symbols = {stock.symbol for stock in self._eligible_stocks()}
        priority_symbols = self._high_frequency_symbols(eligible_symbols)

        async def scan_limited(stock: ThemeStock) -> str | None:
            # A batch can contain hot, pinned, fast and background stocks at once.
            # Keep it below the SQLAlchemy pool capacity so browser reads retain
            # connections and a slow provider response cannot create a DB stampede.
            async with self._scan_semaphore:
                return await self._scan_stock(stock, trade_date, priority_symbols)

        results = await asyncio.gather(*(
            scan_limited(stock)
            for stock in batch
        ))
        errors = [error for error in results if error]
        self._last_error = errors[-1] if errors else None
        self._payload_cache.clear()

    async def _run(self) -> None:
        while True:
            now = datetime.now(TAIPEI)
            self._reset_for_day(now.date())
            try:
                await self.restriction_service.refresh(now)
            except Exception:
                logger.exception("day-trading restriction refresh failed for momentum radar")
            await self._refresh_universe(now)
            capabilities = self.service.provider.capabilities
            if capabilities.available and self._is_market_open(now):
                await self._scan_next(now.date())
                await asyncio.sleep(self.scan_interval_seconds)
            else:
                await asyncio.sleep(self._idle_sleep_seconds(now))

    def payload(
        self,
        now: datetime | None = None,
        pinned_symbols: Sequence[str] | None = None,
        tracking_symbols: Sequence[str] | None = None,
        client_id: str = "legacy",
    ) -> dict[str, object]:
        current = _aware(now or datetime.now(TAIPEI))
        if pinned_symbols is not None:
            self.set_pinned_symbols(client_id, pinned_symbols, current)
        if tracking_symbols is not None:
            self.set_tracking_symbols(client_id, tracking_symbols, current)
        all_pinned_stocks = self._active_pinned_stocks(current)
        client_pinned_stocks = self._client_pinned_stocks(client_id)
        all_tracking_stocks = self._active_tracking_stocks(current)
        client_tracking_stocks = self._client_tracking_stocks(client_id)
        self._reset_for_day(current.date())
        market_open = self._is_market_open(current)
        capabilities = self.service.provider.capabilities
        rate_limit_retry_seconds = float(getattr(
            self.service.provider,
            "rate_limit_retry_seconds",
            0.0,
        ))
        restriction_state = self.restriction_service.state
        cache_bucket_seconds = 2 if market_open else 30
        cache_key = (
            current.date().isoformat(),
            market_open,
            int(current.timestamp() // cache_bucket_seconds),
            self._scan_sequence,
            self._universe_updated_at.isoformat() if self._universe_updated_at else None,
            restriction_state.get("lastRefreshAt"),
            tuple(client_pinned_stocks),
            tuple(sorted(all_pinned_stocks)),
            tuple(client_tracking_stocks),
            tuple(sorted(all_tracking_stocks)),
        )
        cached_payload = self._payload_cache.get(cache_key)
        if cached_payload is not None:
            self._payload_cache_hits += 1
            return {
                **cached_payload,
                "payloadCacheHit": True,
                "payloadCacheHits": self._payload_cache_hits,
                "payloadCacheMisses": self._payload_cache_misses,
            }
        self._payload_cache_misses += 1
        alerts = []
        short_alerts = []
        tracked_alerts = []
        tracked_short_alerts = []
        market_pulse: dict[str, object] = {
            "status": "closed",
            "direction": "neutral",
            "directionLabel": "盤後",
            "trend": "neutral",
            "trendLabel": "現貨收盤，停止更新",
            "largeNetLots": 0.0,
            "largeChangeLots": 0.0,
            "smallNetLots": 0.0,
            "smallChangeLots": 0.0,
            "combinedNetLots": 0.0,
            "coverageCount": 0,
            "updatedAt": None,
            "isEstimate": True,
            "source": "監控池逐筆成交方向聚合推估",
        }
        stocks = list(self._eligible_stocks())
        eligible_symbols = {stock.symbol for stock in stocks}
        if not market_open and self._auto_top_tracking_symbols:
            self._auto_top_tracking_symbols.clear()
        high_frequency_symbols = self._high_frequency_symbols(eligible_symbols, current)
        if market_open:
            rows_by_symbol = self.service.alert_snapshots_snapshot(
                [stock.symbol for stock in stocks],
                current.date(),
            )
            market_pulse = build_market_order_pulse(
                cast(dict[str, Sequence[ChipFlowAlertSnapshot]], rows_by_symbol),
                self.rules,
                as_of=current,
            )
            for stock in stocks:
                rows = rows_by_symbol.get(stock.symbol, [])
                alert = analyze_large_order_momentum(
                    stock,
                    cast(Sequence[ChipFlowAlertSnapshot], rows),
                    self.rules,
                    as_of=current,
                )
                if alert is not None:
                    alerts.append(alert)
                    self._hot_symbols.add(stock.symbol)
                else:
                    if stock.symbol not in all_pinned_stocks:
                        self._hot_symbols.discard(stock.symbol)
                short_alert = analyze_large_order_short_momentum(
                    stock,
                    cast(Sequence[ChipFlowAlertSnapshot], rows),
                    self.rules,
                    as_of=current,
                )
                if short_alert is not None:
                    short_alerts.append(short_alert)
                    self._hot_symbols.add(stock.symbol)
                if stock.symbol in client_pinned_stocks:
                    tracked = analyze_large_order_momentum(
                        stock,
                        cast(Sequence[ChipFlowAlertSnapshot], rows),
                        self.rules,
                        as_of=current,
                        keep_tracking=True,
                    )
                    if tracked is not None:
                        tracked_alerts.append(tracked)
                    tracked_short = analyze_large_order_short_momentum(
                        stock,
                        cast(Sequence[ChipFlowAlertSnapshot], rows),
                        self.rules,
                        as_of=current,
                        keep_tracking=True,
                    )
                    if tracked_short is not None:
                        tracked_short_alerts.append(tracked_short)
        alerts = _rank_momentum_alerts(alerts, "long")
        short_alerts = _rank_momentum_alerts(short_alerts, "short")
        long_rank_by_symbol = {str(item["symbol"]): item for item in alerts}
        short_rank_by_symbol = {str(item["symbol"]): item for item in short_alerts}

        def ranked_tracking_alert(
            alert: dict[str, object],
            rank_by_symbol: dict[str, dict[str, object]],
        ) -> dict[str, object]:
            ranked = rank_by_symbol.get(str(alert.get("symbol")))
            return {**alert, **({"rank": ranked["rank"], "rankScore": ranked["rankScore"]} if ranked else {})}

        tracked_alerts = [
            ranked_tracking_alert(alert, long_rank_by_symbol)
            for alert in tracked_alerts
        ]
        tracked_short_alerts = [
            ranked_tracking_alert(alert, short_rank_by_symbol)
            for alert in tracked_short_alerts
        ]
        top_alerts = alerts[:MOMENTUM_RANK_LIMIT]
        top_short_alerts = short_alerts[:MOMENTUM_RANK_LIMIT]
        next_auto_top_tracking_symbols = {
            str(item["symbol"])
            for item in (*top_alerts, *top_short_alerts)
        }
        if market_open:
            self._auto_top_tracking_symbols = (
                next_auto_top_tracking_symbols & eligible_symbols
            )
        else:
            self._auto_top_tracking_symbols.clear()
        high_frequency_symbols = self._high_frequency_symbols(eligible_symbols, current)
        client_extra_pinned_stocks = self._extra_pinned_tracking_stocks(
            self._auto_top_tracking_symbols,
            current,
            client_id,
        )

        def enrich_alerts(items: Sequence[dict[str, object]]) -> list[dict[str, object]]:
            return [
                self._enrich_runtime_alert(item, current, high_frequency_symbols)
                for item in items
            ]

        top_alerts = enrich_alerts(top_alerts)
        top_short_alerts = enrich_alerts(top_short_alerts)
        tracked_alerts = enrich_alerts(tracked_alerts)
        tracked_short_alerts = enrich_alerts(tracked_short_alerts)
        scanned_count = len(self._scanned_symbols & eligible_symbols)
        if not capabilities.available:
            status = "unavailable"
        elif not market_open:
            status = "closed"
        elif not scanned_count:
            status = "warming"
        else:
            status = "realtime" if scanned_count == len(stocks) else "scanning"
        payload: dict[str, object] = {
            "tradeDate": current.date().isoformat(),
            "status": status,
            "marketOpen": market_open,
            "source": capabilities.source,
            "providerRateLimited": rate_limit_retry_seconds > 0,
            "providerRetrySeconds": math.ceil(rate_limit_retry_seconds),
            "isEstimate": True,
            "windowMinutes": self.rules.window_minutes,
            "minRecentNetLots": self.rules.min_recent_net_lots,
            "minBuySellRatio": self.rules.min_buy_sell_ratio,
            "minPositiveSteps": self.rules.min_positive_steps,
            "scannedCount": scanned_count,
            "baselineCycleScannedCount": len(self._cycle_scanned_symbols & eligible_symbols),
            "baselineCycleTargetSeconds": BACKGROUND_CYCLE_TARGET_SECONDS,
            "lastFullScanAt": self._last_full_scan_at.isoformat() if self._last_full_scan_at else None,
            "candidateCount": len(stocks),
            "candidateTarget": CANDIDATE_TARGET,
            "candidateCoveragePercent": round(
                len(stocks) / CANDIDATE_TARGET * 100, 1,
            ),
            "universeStatus": self._universe_status,
            "universeNotice": self._universe_notice,
            "lastSuccessfulUniverseAt": (
                self._last_successful_universe_at.isoformat()
                if self._last_successful_universe_at else None
            ),
            "disposedExcludedCount": sum(
                self.restriction_service.is_disposed(stock.symbol)
                for stock in self._stocks
            ),
            "disposedExcludedSymbols": sorted(
                stock.symbol for stock in self._stocks
                if self.restriction_service.is_disposed(stock.symbol)
            ),
            "restrictionStatus": self.restriction_service.state["status"],
            "payloadCacheHit": False,
            "payloadCacheHits": self._payload_cache_hits,
            "payloadCacheMisses": self._payload_cache_misses,
            "popularCandidateCount": sum(stock.symbol in self._popular_symbols for stock in stocks),
            "fastCandidateCount": len(self._fast_symbols),
            "cpoCandidateCount": sum(CPO_THEME in stock.themes for stock in stocks),
            "packagingTestCandidateCount": sum(PACKAGING_TEST_THEME in stock.themes for stock in stocks),
            "powerCandidateCount": sum(POWER_THEME in stock.themes for stock in stocks),
            "popularUniverseSource": "證交所／櫃買中心成交金額排行",
            "popularUniverseUpdatedAt": (
                self._universe_updated_at.isoformat() if self._universe_updated_at else None
            ),
            "hotScanCount": len(self._hot_symbols),
            "highFrequencyTrackingCount": len(high_frequency_symbols),
            "pinnedTrackingCount": len(client_pinned_stocks),
            "expandedTrackingCount": len(client_tracking_stocks),
            "rankingLimit": MOMENTUM_RANK_LIMIT,
            "longCount": len(alerts),
            "autoTopTrackingCount": len(self._auto_top_tracking_symbols),
            "extraPinnedTrackingLimit": EXTRA_PINNED_TRACKING_LIMIT,
            "extraPinnedTrackingCount": len(client_extra_pinned_stocks),
            "refreshSeconds": round(self.scan_interval_seconds, 1),
            "warningCount": sum(bool(item["isWarning"]) for item in alerts),
            "strengtheningCount": sum(bool(item["reinforced"]) for item in alerts),
            "jointIncreaseCount": sum(bool(item["simultaneousIncrease"]) for item in alerts),
            "marketPulse": market_pulse,
            "alerts": top_alerts,
            "trackedAlerts": tracked_alerts,
            "shortCount": len(short_alerts),
            "shortStrengtheningCount": sum(bool(item["reinforced"]) for item in short_alerts),
            "shortAlerts": top_short_alerts,
            "trackedShortAlerts": tracked_short_alerts,
            "lastError": self._last_error,
            "notice": (
                "多空數量是近段時間依逐筆成交方向累積的成交張數；"
                "大單採動態金額門檻，小單僅作散戶動向推估；處置股已由掃描入口排除，"
                "數值不代表真實投資人身分或未平倉口數。"
            ),
            "updatedAt": current.isoformat(),
        }
        if len(self._payload_cache) >= 128:
            self._payload_cache.clear()
        self._payload_cache[cache_key] = payload
        return payload


electronic_chip_flow_alert_monitor = ElectronicChipFlowAlertMonitor()
