import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time
import logging
from collections.abc import Awaitable, Callable
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import get_settings
from .chip_flow_accumulator import ChipFlowAccumulator
from .chip_flow_provider import FugleRealtimeTradeProvider, RealtimeTradeProvider
from .chip_flow_repository import ChipFlowRepository
from .chip_flow_types import NormalizedTradeTick
from .chip_flow_types import ChipFlowSnapshotData
from .dynamic_order_threshold import (
    DynamicOrderThreshold,
    DynamicOrderThresholdCalculator,
)
from .order_size_classifier import OrderSizeClassifier
from .trade_direction_classifier import TradeDirectionClassifier


TAIPEI = ZoneInfo("Asia/Taipei")
REGULAR_SESSION_START = time(9, 0)
CONTINUOUS_SESSION_END = time(13, 30)
logger = logging.getLogger(__name__)


def _lots(shares: int) -> float:
    return shares / 1_000


@dataclass(frozen=True, slots=True)
class ExcludedTradeStats:
    before_open_shares: int = 0
    closing_auction_shares: int = 0
    after_hours_shares: int = 0


def _split_session_ticks(
    ticks: list[NormalizedTradeTick],
) -> tuple[list[NormalizedTradeTick], ExcludedTradeStats]:
    active: list[NormalizedTradeTick] = []
    before_open_shares = 0
    closing_auction_shares = 0
    after_hours_shares = 0
    for tick in ticks:
        local_time = tick.timestamp.astimezone(TAIPEI).time().replace(tzinfo=None)
        if local_time < REGULAR_SESSION_START:
            before_open_shares += tick.volume_shares
        elif local_time < CONTINUOUS_SESSION_END:
            active.append(tick)
        elif local_time == CONTINUOUS_SESSION_END:
            closing_auction_shares += tick.volume_shares
        else:
            after_hours_shares += tick.volume_shares
    return active, ExcludedTradeStats(
        before_open_shares=before_open_shares,
        closing_auction_shares=closing_auction_shares,
        after_hours_shares=after_hours_shares,
    )


def _snapshot_payload(item) -> dict[str, object]:
    if hasattr(item, "totals"):
        totals = item.totals
        snapshot_time = item.snapshot_time
        updated_at = item.updated_at
    else:
        totals = item
        snapshot_time = item.snapshot_time
        updated_at = item.updated_at
    large_buy = totals.large_buy_shares
    large_sell = totals.large_sell_shares
    medium_buy = totals.medium_buy_shares
    medium_sell = totals.medium_sell_shares
    small_buy = totals.small_buy_shares
    small_sell = totals.small_sell_shares
    classified = large_buy + large_sell + medium_buy + medium_sell + small_buy + small_sell
    retail_ratio = (small_buy + small_sell) / classified * 100 if classified else None
    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=TAIPEI)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=TAIPEI)
    return {
        "time": snapshot_time.astimezone(TAIPEI).strftime("%H:%M"),
        "snapshotTime": snapshot_time.astimezone(TAIPEI).isoformat(),
        "largeBuyShares": large_buy,
        "largeSellShares": large_sell,
        "largeNetShares": large_buy - large_sell,
        "largeBuyLots": _lots(large_buy),
        "largeSellLots": _lots(large_sell),
        "largeNetLots": _lots(large_buy - large_sell),
        "mediumBuyShares": medium_buy,
        "mediumSellShares": medium_sell,
        "mediumNetShares": medium_buy - medium_sell,
        "smallBuyShares": small_buy,
        "smallSellShares": small_sell,
        "smallNetShares": small_buy - small_sell,
        "smallBuyLots": _lots(small_buy),
        "smallSellLots": _lots(small_sell),
        "smallNetLots": _lots(small_buy - small_sell),
        "unknownShares": totals.unknown_shares,
        "retailControlRatio": round(retail_ratio, 2) if retail_ratio is not None else None,
        "updatedAt": updated_at.astimezone(TAIPEI).isoformat(),
    }


class ChipFlowService:
    def __init__(self, provider: RealtimeTradeProvider | None = None):
        settings = get_settings()
        self.provider = provider or FugleRealtimeTradeProvider(
            settings.fugle_marketdata_api_key,
            base_url=settings.fugle_marketdata_base_url,
            page_size=settings.fugle_chip_flow_page_size,
            max_pages=settings.fugle_chip_flow_max_pages,
            include_odd_lot=settings.fugle_chip_flow_include_odd_lot,
            timeout_seconds=settings.fugle_chip_flow_timeout_seconds,
            min_request_interval_seconds=(
                settings.fugle_chip_flow_min_request_interval_seconds
            ),
        )
        self.large_order_amount = settings.chip_flow_large_order_amount
        self.small_order_amount = settings.chip_flow_small_order_amount
        self.direction_classifier = TradeDirectionClassifier()
        self.threshold_calculator = DynamicOrderThresholdCalculator(
            self.large_order_amount,
            percentile=settings.chip_flow_dynamic_large_order_percentile,
            min_samples=settings.chip_flow_dynamic_large_order_min_samples,
            enabled=settings.chip_flow_dynamic_large_order_enabled,
        )
        self._accumulators: dict[tuple[str, date], ChipFlowAccumulator] = {}
        self._thresholds: dict[tuple[str, date], DynamicOrderThreshold] = {}
        self._threshold_warmup_ticks: dict[tuple[str, date], list[NormalizedTradeTick]] = {}
        self._excluded_stats: dict[tuple[str, date], ExcludedTradeStats] = {}
        self._locks: dict[tuple[str, date], asyncio.Lock] = {}

    def alert_snapshots_snapshot(
        self,
        stock_ids: list[str],
        trade_date: date,
    ) -> dict[str, list[ChipFlowSnapshotData]]:
        """Copy live accumulators for the ticker without checking out a DB connection."""
        result: dict[str, list[ChipFlowSnapshotData]] = {}
        for stock_id in stock_ids:
            accumulator = self._accumulators.get((stock_id, trade_date))
            if accumulator is None:
                result[stock_id] = []
                continue
            # process() mutates one OrderedDict synchronously. A browser thread can
            # exceptionally meet that tiny mutation window, so retry the copy.
            for _ in range(3):
                try:
                    result[stock_id] = list(accumulator.snapshots.values())
                    break
                except RuntimeError:
                    continue
            else:
                result[stock_id] = []
        return result

    def threshold_snapshot(
        self,
        stock_id: str,
        trade_date: date,
    ) -> DynamicOrderThreshold:
        """Return the active per-stock large-order threshold without touching the DB."""
        return self._thresholds.get(
            (stock_id, trade_date),
            DynamicOrderThreshold(
                amount=self.large_order_amount,
                mode="fixed_floor",
                percentile=self.threshold_calculator.percentile,
                sample_count=0,
            ),
        )

    async def get_intraday(
        self,
        stock_id: str,
        db: Session,
        trade_date: date | None = None,
    ) -> dict[str, object]:
        current_date = trade_date or datetime.now(TAIPEI).date()
        capabilities = self.provider.capabilities
        repository = ChipFlowRepository(db)
        provider_error: str | None = None
        if capabilities.available and capabilities.complete_intraday_ticks:
            key = (stock_id, current_date)
            stale_keys = [
                item for item in self._accumulators
                if item[0] == stock_id and item[1] != current_date
            ]
            for stale_key in stale_keys:
                self._accumulators.pop(stale_key, None)
                self._thresholds.pop(stale_key, None)
                self._threshold_warmup_ticks.pop(stale_key, None)
                self._excluded_stats.pop(stale_key, None)
                self._locks.pop(stale_key, None)
            try:
                async with self._locks.setdefault(key, asyncio.Lock()):
                    incremental_loader = cast(
                        Callable[[str, date], Awaitable[list[NormalizedTradeTick]]] | None,
                        getattr(self.provider, "drain_trade_ticks", None),
                    )
                    ticks = sorted(
                        await incremental_loader(stock_id, current_date)
                        if callable(incremental_loader)
                        else await self.provider.get_trade_ticks(stock_id, current_date),
                        key=lambda tick: (tick.timestamp, tick.id),
                    )
                    active_ticks, excluded_stats = _split_session_ticks(ticks)
                    incremental = callable(incremental_loader)
                    accumulator = self._accumulators.get(key) if incremental else None
                    previous_threshold = self._thresholds.get(key)
                    rebuild_ticks: list[NormalizedTradeTick] | None = None
                    if incremental and self.threshold_calculator.enabled and (
                        previous_threshold is None
                        or previous_threshold.mode != "dynamic_percentile"
                    ):
                        warmup_ticks = self._threshold_warmup_ticks.setdefault(key, [])
                        warmup_ticks.extend(active_ticks)
                        threshold = self.threshold_calculator.calculate(warmup_ticks)
                        if (
                            accumulator is None
                            or previous_threshold is None
                            or threshold.amount != previous_threshold.amount
                        ):
                            rebuild_ticks = list(warmup_ticks)
                        if threshold.mode == "dynamic_percentile":
                            self._threshold_warmup_ticks.pop(key, None)
                    else:
                        threshold = previous_threshold or self.threshold_calculator.calculate(active_ticks)

                    if accumulator is None or rebuild_ticks is not None:
                        accumulator = ChipFlowAccumulator(
                            stock_id,
                            current_date,
                            self.direction_classifier,
                            OrderSizeClassifier(
                                threshold.amount,
                                self.small_order_amount,
                            ),
                        )
                        for tick in rebuild_ticks if rebuild_ticks is not None else active_ticks:
                            accumulator.process(tick)
                    else:
                        for tick in active_ticks:
                            accumulator.process(tick)
                    snapshots = list(accumulator.snapshots.values())
                    if snapshots:
                        await asyncio.to_thread(repository.replace_day, snapshots)
                    else:
                        await asyncio.to_thread(repository.delete_day, stock_id, current_date)
                    self._accumulators[key] = accumulator
                    self._thresholds[key] = threshold
                    if incremental:
                        prior_excluded = self._excluded_stats.get(key, ExcludedTradeStats())
                        self._excluded_stats[key] = ExcludedTradeStats(
                            before_open_shares=(
                                prior_excluded.before_open_shares + excluded_stats.before_open_shares
                            ),
                            closing_auction_shares=(
                                prior_excluded.closing_auction_shares + excluded_stats.closing_auction_shares
                            ),
                            after_hours_shares=(
                                prior_excluded.after_hours_shares + excluded_stats.after_hours_shares
                            ),
                        )
                        # The provider cursor already guarantees incremental delivery.
                        # Releasing IDs prevents 300 active symbols growing without bound.
                        accumulator.seen_trade_ids.clear()
                    else:
                        self._excluded_stats[key] = excluded_stats
            except Exception as error:
                await asyncio.to_thread(db.rollback)
                provider_error = str(error)
                logger.exception(
                    "chip-flow provider refresh failed",
                    extra={"stock_id": stock_id, "trade_date": current_date.isoformat()},
                )

        # SQLAlchemy is synchronous. Keep pool waits and large snapshot reads off
        # the asyncio loop so the health checks and in-memory radar stay responsive.
        rows = await asyncio.to_thread(repository.list_for_day, stock_id, current_date)
        series = [_snapshot_payload(row) for row in rows]
        latest = series[-1] if series else None
        key = (stock_id, current_date)
        threshold = self.threshold_snapshot(stock_id, current_date)
        excluded_stats = self._excluded_stats.get(key, ExcludedTradeStats())
        status = (
            "disconnected" if provider_error
            else "realtime" if capabilities.available and series
            else "delayed" if series
            else "no_data" if capabilities.available
            else "awaiting_provider"
        )
        return {
            "stockId": stock_id,
            "tradeDate": current_date.isoformat(),
            "status": status,
            "source": capabilities.source,
            "isEstimate": True,
            "providerCapabilities": {
                "completeIntradayTicks": capabilities.complete_intraday_ticks,
                "hasTradeId": capabilities.has_trade_id,
                "hasBidAskAtTrade": capabilities.has_bid_ask_at_trade,
                "hasSourceSide": capabilities.has_source_side,
            },
            "missingFields": list(capabilities.missing_fields),
            "largeOrderThreshold": threshold.amount,
            "largeOrderThresholdMode": threshold.mode,
            "largeOrderThresholdPercentile": round(threshold.percentile * 100, 2),
            "largeOrderThresholdSampleCount": threshold.sample_count,
            "smallOrderThreshold": self.small_order_amount,
            "excludedBeforeOpenShares": excluded_stats.before_open_shares,
            "excludedBeforeOpenLots": _lots(excluded_stats.before_open_shares),
            "excludedClosingAuctionShares": excluded_stats.closing_auction_shares,
            "excludedClosingAuctionLots": _lots(excluded_stats.closing_auction_shares),
            "excludedAfterHoursShares": excluded_stats.after_hours_shares,
            "excludedAfterHoursLots": _lots(excluded_stats.after_hours_shares),
            "latest": latest,
            "series": series,
            "notice": (
                "大單門檻採當日連續交易整股單筆成交金額分布動態估算；"
                "13:30 集合競價獨立排除，盤後成交不納入。"
                "大小單不代表真實投資人身分，亦可能受到拆單影響。"
            ),
            "statusMessage": (
                f"逐筆行情暫時中斷：{provider_error}" if provider_error
                else capabilities.reason if not capabilities.available or series
                else
                "目前尚無足夠逐筆成交資料，暫時無法計算大小單買賣超。"
            ),
            "updatedAt": latest["updatedAt"] if latest else datetime.now(TAIPEI).isoformat(),
        }


chip_flow_service = ChipFlowService()
