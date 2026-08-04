from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
from typing import Any, Protocol, Sequence, cast
from zoneinfo import ZoneInfo

from .chip_flow_repository import ChipFlowRepository
from .chip_flow_service import ChipFlowService, chip_flow_service
from .theme_stock_universe import (
    ELECTRONIC_ALERT_STOCKS,
    THEME_STOCKS_BY_SYMBOL,
    ThemeStock,
)


TAIPEI = ZoneInfo("Asia/Taipei")
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(13, 30)
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
    def updated_at(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class ChipFlowAlertRules:
    window_minutes: int = 5
    min_recent_net_lots: float = 10.0
    min_buy_sell_ratio: float = 1.5
    min_positive_steps: int = 2
    max_stale_minutes: int = 10


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=TAIPEI) if value.tzinfo is None else value.astimezone(TAIPEI)


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

    cutoff = latest_time - timedelta(minutes=rules.window_minutes)
    reference = ordered[0]
    for item in ordered:
        if _aware(item.snapshot_time) <= cutoff:
            reference = item
        else:
            break
    recent_points = [
        item for item in ordered
        if _aware(item.snapshot_time) >= _aware(reference.snapshot_time)
    ]
    if len(recent_points) < 3:
        return None

    recent_buy_shares = max(0, latest.large_buy_shares - reference.large_buy_shares)
    recent_sell_shares = max(0, latest.large_sell_shares - reference.large_sell_shares)
    recent_net_shares = latest.large_net_shares - reference.large_net_shares
    positive_steps = sum(
        current.large_net_shares > previous.large_net_shares
        for previous, current in zip(recent_points, recent_points[1:])
    )
    buy_sell_ratio = (
        recent_buy_shares / recent_sell_shares
        if recent_sell_shares > 0
        else None if recent_buy_shares == 0
        else 99.0
    )
    if (
        latest.large_net_shares <= 0
        or recent_net_shares < rules.min_recent_net_lots * 1_000
        or buy_sell_ratio is None
        or buy_sell_ratio < rules.min_buy_sell_ratio
        or positive_steps < rules.min_positive_steps
    ):
        return None

    return {
        "symbol": stock.symbol,
        "name": stock.name,
        "industry": stock.industry,
        "time": latest_time.strftime("%H:%M"),
        "largeNetLots": round(latest.large_net_shares / 1_000, 2),
        "recentNetBuyLots": round(recent_net_shares / 1_000, 2),
        "recentBuyLots": round(recent_buy_shares / 1_000, 2),
        "recentSellLots": round(recent_sell_shares / 1_000, 2),
        "buySellRatio": round(buy_sell_ratio, 2),
        "positiveSteps": positive_steps,
        "updatedAt": _aware(latest.updated_at).isoformat(),
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
    enriched: list[dict[str, Any]] = []
    for original in candidates:
        item = dict(original)
        symbol = str(item.get("symbol", ""))
        stock = THEME_STOCKS_BY_SYMBOL.get(symbol)
        rows = repository.list_for_day(symbol, current.date()) if stock is not None else []
        latest = rows[-1] if rows else None
        latest_time = _aware(latest.snapshot_time) if latest is not None else None
        fresh = (
            latest_time is not None
            and current - latest_time <= timedelta(minutes=rules.max_stale_minutes)
        )
        data_available = len(rows) >= 3 and fresh
        alert = (
            evaluate_large_order_surge(
                stock,
                cast(Sequence[ChipFlowAlertSnapshot], rows),
                rules,
                as_of=current,
            )
            if stock is not None and data_available else None
        )
        continuous = alert is not None
        latest_net_lots = (
            round(float(latest.large_net_shares) / 1_000, 2)
            if latest is not None else 0.0
        )
        recent_net_lots = float(str(alert["recentNetBuyLots"])) if alert else 0.0
        positive_steps = int(str(alert["positiveSteps"])) if alert else 0
        buy_sell_ratio = float(str(alert["buySellRatio"])) if alert else None
        status = (
            "大單持續敲進"
            if continuous
            else "大單資料暖機／延遲"
            if not data_available
            else "大單尚未持續敲進"
        )
        reasons = list(item.get("reasons") or [])
        warnings = list(item.get("warnings") or [])
        if continuous:
            reasons.append(
                f"近 {rules.window_minutes} 分鐘大單淨買超 +{recent_net_lots:g} 張，"
                f"連續增加 {positive_steps} 次"
            )
        elif data_available:
            warnings.append(
                f"大單未達持續敲進標準（近 {rules.window_minutes} 分鐘至少淨買超 "
                f"{rules.min_recent_net_lots:g} 張、買賣比 {rules.min_buy_sell_ratio:g}）"
            )
        else:
            warnings.append("等待足夠且未逾時的逐筆成交大單資料")
        item.update({
            "largeOrderDataAvailable": data_available,
            "largeOrderContinuousBuy": continuous,
            "largeOrderStatus": status,
            "largeOrderNetLots": latest_net_lots,
            "largeOrderRecentNetLots": recent_net_lots,
            "largeOrderBuySellRatio": buy_sell_ratio,
            "largeOrderPositiveSteps": positive_steps,
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


class ElectronicChipFlowAlertMonitor:
    def __init__(self, service: ChipFlowService = chip_flow_service):
        from ..config import get_settings

        settings = get_settings()
        self.service = service
        self.rules = ChipFlowAlertRules(
            window_minutes=settings.chip_flow_alert_window_minutes,
            min_recent_net_lots=settings.chip_flow_alert_min_recent_net_lots,
            min_buy_sell_ratio=settings.chip_flow_alert_min_buy_sell_ratio,
            min_positive_steps=settings.chip_flow_alert_min_positive_steps,
            max_stale_minutes=settings.chip_flow_alert_max_stale_minutes,
        )
        self.scan_interval_seconds = max(
            2.0,
            settings.chip_flow_electronic_scan_interval_seconds,
        )
        self._task: asyncio.Task[None] | None = None
        self._index = 0
        self._scan_date: date | None = None
        self._scanned_symbols: set[str] = set()
        self._last_error: str | None = None

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

    @staticmethod
    def _is_market_open(now: datetime) -> bool:
        local = _aware(now)
        return local.weekday() < 5 and MARKET_OPEN <= local.time() < MARKET_CLOSE

    def _reset_for_day(self, trade_date: date) -> None:
        if self._scan_date == trade_date:
            return
        self._scan_date = trade_date
        self._index = 0
        self._scanned_symbols.clear()
        self._last_error = None

    async def _scan_next(self, trade_date: date) -> None:
        from ..database import SessionLocal

        stock = ELECTRONIC_ALERT_STOCKS[self._index]
        self._index = (self._index + 1) % len(ELECTRONIC_ALERT_STOCKS)
        try:
            with SessionLocal() as db:
                await self.service.get_intraday(stock.symbol, db, trade_date)
            self._scanned_symbols.add(stock.symbol)
            self._last_error = None
        except Exception as error:
            self._last_error = str(error)
            logger.exception(
                "electronic chip-flow scan failed",
                extra={"stock_id": stock.symbol, "trade_date": trade_date.isoformat()},
            )

    async def _run(self) -> None:
        while True:
            now = datetime.now(TAIPEI)
            self._reset_for_day(now.date())
            capabilities = self.service.provider.capabilities
            is_trading_day = now.weekday() < 5
            after_close_scan_pending = (
                is_trading_day
                and now.time() >= MARKET_CLOSE
                and len(self._scanned_symbols) < len(ELECTRONIC_ALERT_STOCKS)
            )
            if capabilities.available and (
                self._is_market_open(now) or after_close_scan_pending
            ):
                await self._scan_next(now.date())
                await asyncio.sleep(self.scan_interval_seconds)
            else:
                await asyncio.sleep(30)

    def payload(self, repository: ChipFlowRepository, now: datetime | None = None) -> dict[str, object]:
        current = _aware(now or datetime.now(TAIPEI))
        self._reset_for_day(current.date())
        market_open = self._is_market_open(current)
        capabilities = self.service.provider.capabilities
        alerts = []
        for stock in ELECTRONIC_ALERT_STOCKS:
            rows = repository.list_for_day(stock.symbol, current.date())
            alert = evaluate_large_order_surge(
                stock,
                cast(Sequence[ChipFlowAlertSnapshot], rows),
                self.rules,
                as_of=current if market_open else None,
            )
            if alert is not None:
                alerts.append(alert)
        alerts.sort(
            key=lambda item: (
                float(item["recentNetBuyLots"]),
                float(item["largeNetLots"]),
            ),
            reverse=True,
        )
        scanned_count = len(self._scanned_symbols)
        if not capabilities.available:
            status = "unavailable"
        elif not scanned_count:
            status = "warming"
        elif market_open:
            status = "realtime" if scanned_count == len(ELECTRONIC_ALERT_STOCKS) else "scanning"
        else:
            status = "closed" if scanned_count == len(ELECTRONIC_ALERT_STOCKS) else "scanning"
        return {
            "tradeDate": current.date().isoformat(),
            "status": status,
            "marketOpen": market_open,
            "source": capabilities.source,
            "isEstimate": True,
            "windowMinutes": self.rules.window_minutes,
            "minRecentNetLots": self.rules.min_recent_net_lots,
            "minBuySellRatio": self.rules.min_buy_sell_ratio,
            "minPositiveSteps": self.rules.min_positive_steps,
            "scannedCount": scanned_count,
            "candidateCount": len(ELECTRONIC_ALERT_STOCKS),
            "alerts": alerts[:8],
            "lastError": self._last_error,
            "notice": "大單狂進依逐筆成交方向與動態大單門檻推估，不代表真實投資人身分。",
            "updatedAt": current.isoformat(),
        }


electronic_chip_flow_alert_monitor = ElectronicChipFlowAlertMonitor()
