import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
import math
from types import SimpleNamespace
from typing import cast
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import ChipFlowSnapshot
from app.services.chip_flow_provider import (
    FugleRealtimeTradeProvider,
    RealtimeTradeCapabilities,
)
from app.services.chip_flow_service import ChipFlowService
from app.services.chip_flow_alerts import (
    EXTRA_PINNED_TRACKING_LIMIT,
    FAST_POPULAR_LIMIT,
    MAX_CONCURRENT_STOCK_SCANS,
    MOMENTUM_RANK_LIMIT,
    ChipFlowAlertRules,
    ElectronicChipFlowAlertMonitor,
    _momentum_rank_score,
    analyze_large_order_ranking,
    analyze_large_order_momentum,
    analyze_large_order_short_momentum,
    build_market_order_pulse,
    enrich_day_trading_large_order_confirmation,
    evaluate_large_order_surge,
    evaluate_large_order_short_surge,
)
from app.services.chip_flow_accumulator import ChipFlowAccumulator
from app.services.chip_flow_repository import ChipFlowRepository
from app.services.chip_flow_types import (
    ChipFlowSnapshotData,
    ChipFlowTotals,
    NormalizedTradeTick,
    OrderSize,
    TradeDirection,
    TradeSession,
)
from app.services.dynamic_order_threshold import DynamicOrderThresholdCalculator
from app.services.order_size_classifier import OrderSizeClassifier
from app.services.trade_direction_classifier import TradeDirectionClassifier
from app.services.theme_stock_universe import ThemeStock


TAIPEI = ZoneInfo("Asia/Taipei")
DAY = date(2026, 7, 29)


def test_momentum_rank_score_prioritizes_recent_increase_over_static_session_size() -> None:
    increasing = {
        "rankingBasis": "session",
        "sessionNetBuyLots": 70,
        "sessionLargeBuyLots": 90,
        "recentNetBuyLots": 28,
        "momentumChangeLots": 32,
        "positiveSteps": 4,
        "sessionBuySellRatio": 3.0,
        "currentQualifies": True,
        "rankingFillReason": "net",
        "trend": "strengthening",
    }
    static_large = {
        "rankingBasis": "session",
        "sessionNetBuyLots": 220,
        "sessionLargeBuyLots": 260,
        "recentNetBuyLots": 2,
        "momentumChangeLots": 0,
        "positiveSteps": 0,
        "sessionBuySellRatio": 3.0,
        "currentQualifies": True,
        "rankingFillReason": "net",
    }

    assert _momentum_rank_score(increasing, "long") > _momentum_rank_score(static_large, "long")


def test_short_momentum_rank_score_uses_directional_recent_increase() -> None:
    increasing_short = {
        "rankingBasis": "session",
        "sessionNetSellLots": 70,
        "sessionLargeSellLots": 90,
        "recentNetSellLots": 28,
        "momentumChangeLots": -32,
        "negativeSteps": 4,
        "sessionSellBuyRatio": 3.0,
        "currentQualifies": True,
        "rankingFillReason": "net",
        "trend": "strengthening",
    }
    static_large_short = {
        "rankingBasis": "session",
        "sessionNetSellLots": 220,
        "sessionLargeSellLots": 260,
        "recentNetSellLots": 2,
        "momentumChangeLots": 0,
        "negativeSteps": 0,
        "sessionSellBuyRatio": 3.0,
        "currentQualifies": True,
        "rankingFillReason": "net",
    }

    assert _momentum_rank_score(increasing_short, "short") > _momentum_rank_score(static_large_short, "short")


def test_snapshot_exposes_small_order_buy_and_sell_totals() -> None:
    snapshot_time = datetime(2026, 7, 29, 10, 0, tzinfo=TAIPEI)
    snapshot = ChipFlowSnapshotData(
        stock_id="2330",
        trade_date=DAY,
        snapshot_time=snapshot_time,
        totals=ChipFlowTotals(small_buy_shares=8_000, small_sell_shares=3_000),
        updated_at=snapshot_time,
    )

    assert snapshot.small_buy_shares == 8_000
    assert snapshot.small_sell_shares == 3_000
    assert snapshot.small_net_shares == 5_000
ALERT_STOCK = ThemeStock("2330", "台積電", "上市", "半導體", ("AI",))


def alert_snapshot(
    minute: int,
    *,
    buy_shares: int,
    sell_shares: int,
    small_net_shares: int = 0,
    small_buy_shares: int | None = None,
    small_sell_shares: int | None = None,
) -> SimpleNamespace:
    snapshot_time = datetime(2026, 7, 29, 10, minute, tzinfo=TAIPEI)
    inferred_small_buy_shares = max(0, small_net_shares)
    inferred_small_sell_shares = max(0, -small_net_shares)
    return SimpleNamespace(
        snapshot_time=snapshot_time,
        large_buy_shares=buy_shares,
        large_sell_shares=sell_shares,
        large_net_shares=buy_shares - sell_shares,
        small_buy_shares=(
            inferred_small_buy_shares if small_buy_shares is None else small_buy_shares
        ),
        small_sell_shares=(
            inferred_small_sell_shares if small_sell_shares is None else small_sell_shares
        ),
        small_net_shares=small_net_shares,
        updated_at=snapshot_time,
    )


def tick(
    trade_id: str,
    *,
    price: str = "100",
    shares: int = 1_000,
    bid: str | None = None,
    ask: str | None = None,
    previous: str | None = None,
    at: datetime | None = None,
    trade_date: date = DAY,
    session: TradeSession = TradeSession.REGULAR,
) -> NormalizedTradeTick:
    return NormalizedTradeTick(
        id=trade_id,
        stock_id="3138",
        trade_date=trade_date,
        timestamp=at or datetime(2026, 7, 29, 9, 1, 5, tzinfo=TAIPEI),
        price=Decimal(price),
        volume_shares=shares,
        bid_price=Decimal(bid) if bid is not None else None,
        ask_price=Decimal(ask) if ask is not None else None,
        previous_price=Decimal(previous) if previous is not None else None,
        session=session,
    )


def accumulator() -> ChipFlowAccumulator:
    return ChipFlowAccumulator(
        "3138",
        DAY,
        TradeDirectionClassifier(),
        OrderSizeClassifier(2_000_000, 500_000),
    )


def test_trade_at_ask_is_buy() -> None:
    assert TradeDirectionClassifier().classify(
        tick("ask", price="101", bid="100", ask="101")
    ) == TradeDirection.BUY


def test_trade_at_bid_is_sell() -> None:
    assert TradeDirectionClassifier().classify(
        tick("bid", price="100", bid="100", ask="101")
    ) == TradeDirection.SELL


def test_mid_trade_above_previous_is_buy() -> None:
    assert TradeDirectionClassifier().classify(
        tick("up", price="100.5", bid="100", ask="101", previous="100")
    ) == TradeDirection.BUY


def test_mid_trade_below_previous_is_sell() -> None:
    assert TradeDirectionClassifier().classify(
        tick("down", price="100.5", bid="100", ask="101", previous="101")
    ) == TradeDirection.SELL


def test_flat_trade_reuses_previous_direction() -> None:
    assert TradeDirectionClassifier().classify(
        tick("flat", price="100.5", bid="100", ask="101", previous="100.5"),
        previous_direction=TradeDirection.SELL,
    ) == TradeDirection.SELL


def test_unclassifiable_trade_is_unknown() -> None:
    assert TradeDirectionClassifier().classify(
        tick("unknown", bid=None, ask=None, previous=None)
    ) == TradeDirection.UNKNOWN


def test_exact_two_million_trade_is_large() -> None:
    classifier = OrderSizeClassifier(2_000_000, 500_000)
    assert classifier.classify(tick("large", price="100", shares=20_000)) == OrderSize.LARGE


def test_499900_trade_is_small() -> None:
    classifier = OrderSizeClassifier(2_000_000, 500_000)
    assert classifier.classify(tick("small", price="100", shares=4_999)) == OrderSize.SMALL


def test_large_net_is_minus_16000_shares() -> None:
    flow = accumulator()
    flow.process(tick("buy", price="200", shares=12_000, ask="200"))
    flow.process(tick("sell", price="200", shares=28_000, bid="200"))
    assert flow.totals.large_net_shares == -16_000
    assert flow.totals.large_net_shares / 1_000 == -16


def test_duplicate_trade_id_is_counted_once() -> None:
    flow = accumulator()
    item = tick("same", price="100", shares=20_000, ask="100")
    assert flow.process(item) is True
    assert flow.process(item) is False
    assert flow.totals.large_buy_shares == 20_000


def test_new_trade_date_resets_accumulation() -> None:
    flow = accumulator()
    flow.process(tick("day-one", price="100", shares=20_000, ask="100"))
    next_day = date(2026, 7, 30)
    flow.process(tick(
        "day-two",
        price="100",
        shares=20_000,
        bid="100",
        trade_date=next_day,
        at=datetime(2026, 7, 30, 9, 1, 5, tzinfo=TAIPEI),
    ))
    assert flow.trade_date == next_day
    assert flow.totals.large_buy_shares == 0
    assert flow.totals.large_sell_shares == 20_000


def test_odd_lot_is_preserved_as_fractional_lot() -> None:
    flow = accumulator()
    flow.process(tick("odd", price="100", shares=500, ask="100"))
    assert flow.totals.small_buy_shares == 500
    assert flow.totals.small_net_shares / 1_000 == 0.5


def test_large_order_surge_requires_recent_continuous_net_buying() -> None:
    result = evaluate_large_order_surge(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=20_000, sell_shares=10_000),
            alert_snapshot(2, buy_shares=30_000, sell_shares=12_000),
            alert_snapshot(4, buy_shares=42_000, sell_shares=14_000),
            alert_snapshot(5, buy_shares=50_000, sell_shares=15_000),
        ],
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert result is not None
    assert result["symbol"] == "2330"
    assert result["market"] == "上市"
    assert result["recentNetBuyLots"] == 25
    assert result["positiveSteps"] == 3


def test_large_order_short_surge_requires_persistent_net_selling() -> None:
    result = evaluate_large_order_short_surge(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=10_000, sell_shares=20_000),
            alert_snapshot(2, buy_shares=12_000, sell_shares=30_000),
            alert_snapshot(4, buy_shares=14_000, sell_shares=42_000),
            alert_snapshot(5, buy_shares=15_000, sell_shares=50_000),
        ],
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert result is not None
    assert result["recentNetSellLots"] == 25
    assert result["negativeSteps"] == 3


def test_large_order_ranking_assigns_stock_to_net_buy_side_only() -> None:
    rows = [
        alert_snapshot(0, buy_shares=30_000, sell_shares=10_000),
        alert_snapshot(5, buy_shares=80_000, sell_shares=30_000),
    ]

    long_result = analyze_large_order_ranking(
        ALERT_STOCK,
        rows,
        ChipFlowAlertRules(),
        direction="long",
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )
    short_result = analyze_large_order_ranking(
        ALERT_STOCK,
        rows,
        ChipFlowAlertRules(),
        direction="short",
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert long_result is not None
    assert long_result["rankingFillReason"] == "net"
    assert long_result["sessionNetBuyLots"] == 50
    assert short_result is None


def test_large_order_ranking_assigns_stock_to_net_sell_side_only() -> None:
    rows = [
        alert_snapshot(0, buy_shares=10_000, sell_shares=30_000),
        alert_snapshot(5, buy_shares=30_000, sell_shares=80_000),
    ]

    long_result = analyze_large_order_ranking(
        ALERT_STOCK,
        rows,
        ChipFlowAlertRules(),
        direction="long",
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )
    short_result = analyze_large_order_ranking(
        ALERT_STOCK,
        rows,
        ChipFlowAlertRules(),
        direction="short",
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert long_result is None
    assert short_result is not None
    assert short_result["rankingFillReason"] == "net"
    assert short_result["sessionNetSellLots"] == 50


def test_large_order_ranking_rejects_offsetting_two_way_flow() -> None:
    rows = [
        alert_snapshot(0, buy_shares=40_000, sell_shares=38_000),
        alert_snapshot(5, buy_shares=100_000, sell_shares=90_000),
    ]

    assert analyze_large_order_ranking(
        ALERT_STOCK,
        rows,
        ChipFlowAlertRules(),
        direction="long",
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    ) is None
    assert analyze_large_order_ranking(
        ALERT_STOCK,
        rows,
        ChipFlowAlertRules(),
        direction="short",
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    ) is None


def test_large_order_threshold_adapts_to_gross_flow_and_exposes_freshness() -> None:
    result = evaluate_large_order_surge(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=20_000, sell_shares=10_000),
            alert_snapshot(2, buy_shares=100_000, sell_shares=30_000),
            alert_snapshot(5, buy_shares=220_000, sell_shares=60_000),
        ],
        ChipFlowAlertRules(),
    )

    assert result is not None
    assert result["recentGrossLargeLots"] == 250
    assert result["effectiveNetThresholdLots"] == 30
    assert result["lastLargeOrderAt"] == "2026-07-29T10:05:00+08:00"


def test_large_order_signal_rejects_balanced_two_way_block_trading() -> None:
    result = evaluate_large_order_surge(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=10_000, sell_shares=5_000),
            alert_snapshot(2, buy_shares=30_000, sell_shares=15_000),
            alert_snapshot(5, buy_shares=70_000, sell_shares=45_000),
        ],
        ChipFlowAlertRules(),
    )

    assert result is None


def test_short_momentum_exposes_independent_bar_history() -> None:
    result = analyze_large_order_short_momentum(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=10_000, sell_shares=20_000, small_net_shares=-1_000),
            alert_snapshot(2, buy_shares=12_000, sell_shares=30_000, small_net_shares=-5_000),
            alert_snapshot(4, buy_shares=14_000, sell_shares=42_000, small_net_shares=-10_000),
            alert_snapshot(5, buy_shares=15_000, sell_shares=50_000, small_net_shares=-14_000),
        ],
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert result is not None
    assert result["direction"] == "short"
    assert result["currentQualifies"] is True
    assert result["recentNetSellLots"] == 25
    assert result["simultaneousIncrease"] is True
    assert result["history"]


def test_large_and_small_order_increase_exposes_combined_force() -> None:
    result = analyze_large_order_momentum(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=20_000, sell_shares=10_000, small_net_shares=1_000),
            alert_snapshot(2, buy_shares=30_000, sell_shares=12_000, small_net_shares=5_000),
            alert_snapshot(4, buy_shares=42_000, sell_shares=14_000, small_net_shares=10_000),
            alert_snapshot(5, buy_shares=50_000, sell_shares=15_000, small_net_shares=14_000),
        ],
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert result is not None
    assert result["simultaneousIncrease"] is True
    assert result["recentNetBuyLots"] == 25
    assert result["recentSmallNetBuyLots"] == 13
    assert result["combinedNetBuyLots"] == 38


def test_market_order_pulse_classifies_strengthening_bull_flow() -> None:
    rows = [
        alert_snapshot(0, buy_shares=20_000, sell_shares=10_000, small_net_shares=0),
        alert_snapshot(2, buy_shares=30_000, sell_shares=12_000, small_net_shares=2_000),
        alert_snapshot(4, buy_shares=42_000, sell_shares=14_000, small_net_shares=5_000),
        alert_snapshot(5, buy_shares=60_000, sell_shares=15_000, small_net_shares=9_000),
    ]

    result = build_market_order_pulse(
        {"2330": rows},
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 5, 30, tzinfo=TAIPEI),
    )

    assert result["directionLabel"] == "多方"
    assert result["trendLabel"] == "多方持續增強"
    assert result["largeNetLots"] == 35
    assert result["largeChangeLots"] == 17
    assert result["coverageCount"] == 1


def test_momentum_exposes_large_and_retail_buy_sell_accumulation() -> None:
    result = analyze_large_order_momentum(
        ALERT_STOCK,
        [
            alert_snapshot(
                0,
                buy_shares=20_000,
                sell_shares=10_000,
                small_net_shares=3_000,
                small_buy_shares=8_000,
                small_sell_shares=5_000,
            ),
            alert_snapshot(
                2,
                buy_shares=30_000,
                sell_shares=12_000,
                small_net_shares=7_000,
                small_buy_shares=14_000,
                small_sell_shares=7_000,
            ),
            alert_snapshot(
                4,
                buy_shares=42_000,
                sell_shares=14_000,
                small_net_shares=12_000,
                small_buy_shares=22_000,
                small_sell_shares=10_000,
            ),
            alert_snapshot(
                5,
                buy_shares=50_000,
                sell_shares=15_000,
                small_net_shares=15_000,
                small_buy_shares=28_000,
                small_sell_shares=13_000,
            ),
        ],
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert result is not None
    assert result["recentBuyLots"] == 30
    assert result["recentSellLots"] == 5
    assert result["recentSmallBuyLots"] == 20
    assert result["recentSmallSellLots"] == 8
    assert result["dayLargeBuyLots"] == 50
    assert result["dayLargeSellLots"] == 15
    assert result["daySmallBuyLots"] == 28
    assert result["daySmallSellLots"] == 13


def test_day_trading_candidate_requires_fresh_continuous_large_order_buying() -> None:
    rows = [
        alert_snapshot(0, buy_shares=20_000, sell_shares=10_000),
        alert_snapshot(2, buy_shares=30_000, sell_shares=12_000),
        alert_snapshot(4, buy_shares=42_000, sell_shares=14_000),
        alert_snapshot(5, buy_shares=50_000, sell_shares=15_000),
    ]

    class RepositoryStub:
        def list_for_day(self, stock_id: str, trade_date: date) -> list[SimpleNamespace]:
            return rows if stock_id == "2330" and trade_date == DAY else []

    candidates = enrich_day_trading_large_order_confirmation(
        [{"symbol": "2330", "reasons": [], "warnings": []}],
        cast(ChipFlowRepository, RepositoryStub()),
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert candidates[0]["largeOrderDataAvailable"] is True
    assert candidates[0]["largeOrderContinuousBuy"] is True
    assert candidates[0]["largeOrderRecentNetLots"] == 25
    assert candidates[0]["largeOrderPositiveSteps"] == 3
    assert "近 5 分鐘大單淨買超 +25 張" in candidates[0]["reasons"][0]


def test_dynamic_momentum_stock_can_confirm_for_day_trading() -> None:
    rows = [
        alert_snapshot(0, buy_shares=20_000, sell_shares=10_000),
        alert_snapshot(2, buy_shares=30_000, sell_shares=12_000),
        alert_snapshot(4, buy_shares=42_000, sell_shares=14_000),
        alert_snapshot(5, buy_shares=50_000, sell_shares=15_000),
    ]

    class RepositoryStub:
        def list_for_day(self, stock_id: str, trade_date: date) -> list[SimpleNamespace]:
            return rows if stock_id == "3481" and trade_date == DAY else []

    candidates = enrich_day_trading_large_order_confirmation(
        [{
            "symbol": "3481", "stockName": "群創", "market": "上市",
            "themes": ["熱門股"], "reasons": [], "warnings": [],
        }],
        cast(ChipFlowRepository, RepositoryStub()),
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert candidates[0]["largeOrderDataAvailable"] is True
    assert candidates[0]["largeOrderContinuousBuy"] is True


def test_short_candidate_requires_fresh_continuous_large_order_selling() -> None:
    rows = [
        alert_snapshot(0, buy_shares=10_000, sell_shares=20_000),
        alert_snapshot(2, buy_shares=12_000, sell_shares=30_000),
        alert_snapshot(4, buy_shares=14_000, sell_shares=42_000),
        alert_snapshot(5, buy_shares=15_000, sell_shares=50_000),
    ]

    class RepositoryStub:
        def list_for_day(self, stock_id: str, trade_date: date) -> list[SimpleNamespace]:
            return rows if stock_id == "2330" and trade_date == DAY else []

    candidates = enrich_day_trading_large_order_confirmation(
        [{"symbol": "2330", "direction": "short", "reasons": [], "warnings": []}],
        cast(ChipFlowRepository, RepositoryStub()),
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI),
    )

    assert candidates[0]["largeOrderContinuousSell"] is True
    assert candidates[0]["largeOrderContinuousBuy"] is False
    assert candidates[0]["largeOrderRecentNetLots"] == -25
    assert candidates[0]["largeOrderStatus"] == "大戶持續加空"


def test_large_order_surge_rejects_sell_dominated_window() -> None:
    result = evaluate_large_order_surge(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=20_000, sell_shares=5_000),
            alert_snapshot(2, buy_shares=40_000, sell_shares=24_000),
            alert_snapshot(5, buy_shares=60_000, sell_shares=40_000),
        ],
        ChipFlowAlertRules(),
    )

    assert result is None


def test_large_order_surge_rejects_stale_intraday_snapshot() -> None:
    result = evaluate_large_order_surge(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=20_000, sell_shares=5_000),
            alert_snapshot(2, buy_shares=40_000, sell_shares=7_000),
            alert_snapshot(5, buy_shares=60_000, sell_shares=9_000),
        ],
        ChipFlowAlertRules(max_stale_minutes=10),
        as_of=datetime(2026, 7, 29, 10, 16, tzinfo=TAIPEI),
    )

    assert result is None


def test_large_order_momentum_records_occurrences_and_reinforces_strength() -> None:
    result = analyze_large_order_momentum(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=20_000, sell_shares=10_000),
            alert_snapshot(2, buy_shares=30_000, sell_shares=12_000),
            alert_snapshot(4, buy_shares=42_000, sell_shares=14_000),
            alert_snapshot(5, buy_shares=60_000, sell_shares=16_000),
            alert_snapshot(6, buy_shares=80_000, sell_shares=17_000),
            alert_snapshot(7, buy_shares=105_000, sell_shares=18_000),
        ],
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 8, tzinfo=TAIPEI),
    )

    assert result is not None
    assert result["occurrenceCount"] == 4
    assert result["trend"] == "strengthening"
    assert result["reinforced"] is True
    assert result["trendStreak"] >= 2
    assert len(cast(list[object], result["history"])) == 4


def test_large_order_momentum_warns_when_started_flow_suddenly_fades() -> None:
    result = analyze_large_order_momentum(
        ALERT_STOCK,
        [
            alert_snapshot(0, buy_shares=20_000, sell_shares=10_000),
            alert_snapshot(2, buy_shares=30_000, sell_shares=12_000),
            alert_snapshot(4, buy_shares=42_000, sell_shares=14_000),
            alert_snapshot(5, buy_shares=50_000, sell_shares=15_000),
            alert_snapshot(6, buy_shares=51_000, sell_shares=35_000),
        ],
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 7, tzinfo=TAIPEI),
    )

    assert result is not None
    assert result["occurrenceCount"] == 2
    assert result["isWarning"] is True
    assert result["trend"] == "fading"
    assert result["alertLevel"] == "critical"
    assert result["momentumChangeLots"] == -19


def test_pinned_large_order_momentum_keeps_tracking_after_alert_lifecycle() -> None:
    snapshots = [
        alert_snapshot(0, buy_shares=20_000, sell_shares=10_000),
        alert_snapshot(2, buy_shares=30_000, sell_shares=12_000),
        alert_snapshot(4, buy_shares=42_000, sell_shares=14_000),
        alert_snapshot(5, buy_shares=50_000, sell_shares=15_000),
        alert_snapshot(21, buy_shares=51_000, sell_shares=20_000),
        alert_snapshot(23, buy_shares=51_500, sell_shares=25_000),
        alert_snapshot(25, buy_shares=52_000, sell_shares=30_000),
    ]

    expired = analyze_large_order_momentum(
        ALERT_STOCK,
        snapshots,
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 26, tzinfo=TAIPEI),
    )
    tracked = analyze_large_order_momentum(
        ALERT_STOCK,
        snapshots,
        ChipFlowAlertRules(),
        as_of=datetime(2026, 7, 29, 10, 26, tzinfo=TAIPEI),
        keep_tracking=True,
    )

    assert expired is None
    assert tracked is not None
    assert tracked["currentQualifies"] is False
    assert tracked["updatedAt"] == datetime(2026, 7, 29, 10, 25, tzinfo=TAIPEI).isoformat()


def test_pinned_symbols_are_isolated_by_browser_client() -> None:
    monitor = ElectronicChipFlowAlertMonitor()
    now = datetime(2026, 7, 29, 10, 0, tzinfo=TAIPEI)

    monitor.set_pinned_symbols("browser-one", ["2330"], now)
    monitor.set_pinned_symbols("browser-two", ["2317"], now)

    assert set(monitor._client_pinned_stocks("browser-one")) == {"2330"}
    assert set(monitor._client_pinned_stocks("browser-two")) == {"2317"}
    assert set(monitor._active_pinned_stocks(now)) == {"2330", "2317"}


def test_disposed_stocks_are_excluded_from_momentum_scans_and_pins() -> None:
    class RestrictionStub:
        state = {"status": "healthy", "lastRefreshAt": "2026-07-29T09:00:00+08:00"}

        def is_disposed(self, symbol: object) -> bool:
            return str(symbol) == "2330"

        def market_restrictions_available(self, market: object) -> bool:
            return True

    monitor = ElectronicChipFlowAlertMonitor(
        restriction_service=RestrictionStub(),  # type: ignore[arg-type]
    )
    allowed = ThemeStock("2317", "鴻海", "上市", "電子零組件", ("AI",))
    monitor._stocks = (ALERT_STOCK, allowed)
    now = datetime(2026, 7, 29, 10, 0, tzinfo=TAIPEI)

    monitor.set_pinned_symbols("browser-one", ["2330", "2317"], now)
    payload = monitor.payload(now=now, client_id="browser-one")

    assert [stock.symbol for stock in monitor.stock_universe_snapshot()] == ["2317"]
    assert set(monitor._client_pinned_stocks("browser-one")) == {"2317"}
    assert payload["candidateCount"] == 1
    assert payload["disposedExcludedCount"] == 1
    assert payload["disposedExcludedSymbols"] == ["2330"]


def test_momentum_monitor_wakes_at_opening_bell() -> None:
    monitor = ElectronicChipFlowAlertMonitor()

    assert monitor._idle_sleep_seconds(
        datetime(2026, 7, 29, 8, 59, 45, tzinfo=TAIPEI),
    ) == 15
    assert monitor._idle_sleep_seconds(
        datetime(2026, 7, 29, 8, 30, tzinfo=TAIPEI),
    ) == 30
    assert monitor._is_market_open(
        datetime(2026, 7, 29, 9, 0, tzinfo=TAIPEI),
    )


def test_inactive_browser_pins_expire_after_one_day() -> None:
    monitor = ElectronicChipFlowAlertMonitor()
    now = datetime(2026, 7, 29, 10, 0, tzinfo=TAIPEI)
    monitor.set_pinned_symbols("browser-one", ["2330"], now)

    active = monitor._active_pinned_stocks(now + timedelta(hours=23))
    expired = monitor._active_pinned_stocks(now + timedelta(hours=25))

    assert set(active) == {"2330"}
    assert expired == {}


def test_expanded_tracking_is_prioritized_and_expires_quickly() -> None:
    monitor = ElectronicChipFlowAlertMonitor()
    stocks = tuple(
        ThemeStock(str(4000 + index), f"測試{index}", "上市", "電子零組件", ("熱門股",))
        for index in range(30)
    )
    monitor._stocks = stocks
    monitor._fast_symbols = tuple(stock.symbol for stock in stocks[:10])
    now = datetime.now(TAIPEI)
    tracked_symbols = [stock.symbol for stock in stocks[10:22]]

    monitor.set_tracking_symbols("browser-one", tracked_symbols, now)
    batch = {stock.symbol for stock in monitor._next_scan_batch()}

    assert set(tracked_symbols) <= batch
    assert set(monitor._active_tracking_stocks(now + timedelta(seconds=14))) == set(tracked_symbols)
    assert monitor._active_tracking_stocks(now + timedelta(seconds=16)) == {}


def test_expanded_tracking_is_isolated_by_browser_client() -> None:
    monitor = ElectronicChipFlowAlertMonitor()
    now = datetime(2026, 7, 29, 10, 0, tzinfo=TAIPEI)

    monitor.set_tracking_symbols("browser-one", ["2330"], now)
    monitor.set_tracking_symbols("browser-two", ["2317"], now)

    assert set(monitor._client_tracking_stocks("browser-one")) == {"2330"}
    assert set(monitor._client_tracking_stocks("browser-two")) == {"2317"}
    assert set(monitor._active_tracking_stocks(now)) == {"2330", "2317"}


def test_payload_ranks_long_momentum_top_ten_and_tracks_it_when_collapsed() -> None:
    stocks = tuple(
        ThemeStock(str(4000 + index), f"Stock {index}", "銝?", "AI", ("AI",))
        for index in range(12)
    )

    def rows(force_lots: int) -> list[SimpleNamespace]:
        return [
            alert_snapshot(0, buy_shares=20_000, sell_shares=5_000),
            alert_snapshot(
                2,
                buy_shares=20_000 + force_lots * 500,
                sell_shares=6_000,
            ),
            alert_snapshot(
                5,
                buy_shares=20_000 + force_lots * 1_000,
                sell_shares=7_000,
            ),
        ]

    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            return {
                stock_id: rows(20 + int(stock_id) - 4000)
                for stock_id in stock_ids
            }

    monitor = ElectronicChipFlowAlertMonitor(service=ServiceStub())  # type: ignore[arg-type]
    monitor._stocks = stocks
    now = datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI)

    payload = monitor.payload(now=now, client_id="browser-one")
    ranked_symbols = [alert["symbol"] for alert in payload["alerts"]]

    assert len(ranked_symbols) == MOMENTUM_RANK_LIMIT
    assert payload["longCount"] == 12
    assert [alert["rank"] for alert in payload["alerts"]] == list(range(1, 11))
    assert ranked_symbols == [str(symbol) for symbol in range(4011, 4001, -1)]
    assert payload["autoTopTrackingCount"] == MOMENTUM_RANK_LIMIT
    assert set(monitor.high_frequency_symbols_snapshot(now)) == set(ranked_symbols)


def test_payload_keeps_long_top_ten_rankings_when_no_strict_alerts() -> None:
    stocks = tuple(
        ThemeStock(str(4000 + index), f"Stock {index}", "上市", "AI", ("AI",))
        for index in range(12)
    )

    def rows(force_lots: int) -> list[SimpleNamespace]:
        buy_shares = 20_000 + force_lots * 1_000
        sell_shares = 20_000
        return [
            alert_snapshot(0, buy_shares=buy_shares, sell_shares=sell_shares),
            alert_snapshot(2, buy_shares=buy_shares, sell_shares=sell_shares),
            alert_snapshot(5, buy_shares=buy_shares, sell_shares=sell_shares),
        ]

    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            return {
                stock_id: rows(20 + int(stock_id) - 4000)
                for stock_id in stock_ids
            }

    monitor = ElectronicChipFlowAlertMonitor(service=ServiceStub())  # type: ignore[arg-type]
    monitor._stocks = stocks
    now = datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI)

    payload = monitor.payload(now=now, client_id="browser-one")
    ranked_symbols = [alert["symbol"] for alert in payload["longRankings"]]

    assert payload["alerts"] == []
    assert payload["longCount"] == 0
    assert len(ranked_symbols) == MOMENTUM_RANK_LIMIT
    assert payload["longRankingCount"] == 12
    assert [alert["rank"] for alert in payload["longRankings"]] == list(range(1, 11))
    assert ranked_symbols == [str(symbol) for symbol in range(4011, 4001, -1)]
    assert all(alert["currentQualifies"] is False for alert in payload["longRankings"])
    assert set(monitor.high_frequency_symbols_snapshot(now)) == set(ranked_symbols)


def test_payload_ranks_opening_cumulative_buying_when_recent_window_is_flat() -> None:
    stocks = tuple(
        ThemeStock(str(4050 + index), f"Flat {index}", "上市", "AI", ("AI",))
        for index in range(12)
    )

    def rows(force_lots: int) -> list[SimpleNamespace]:
        buy_shares = 20_000 + force_lots * 1_000
        sell_shares = 10_000
        return [
            alert_snapshot(0, buy_shares=20_000, sell_shares=sell_shares),
            alert_snapshot(2, buy_shares=buy_shares, sell_shares=sell_shares),
            alert_snapshot(5, buy_shares=buy_shares, sell_shares=sell_shares),
            alert_snapshot(10, buy_shares=buy_shares, sell_shares=sell_shares),
        ]

    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            return {
                stock_id: rows(20 + int(stock_id) - 4050)
                for stock_id in stock_ids
            }

    monitor = ElectronicChipFlowAlertMonitor(service=ServiceStub())  # type: ignore[arg-type]
    monitor._stocks = stocks
    now = datetime(2026, 7, 29, 10, 10, tzinfo=TAIPEI)

    payload = monitor.payload(now=now, client_id="browser-one")
    ranked_symbols = [alert["symbol"] for alert in payload["longRankings"]]

    assert len(ranked_symbols) == MOMENTUM_RANK_LIMIT
    assert ranked_symbols == [str(symbol) for symbol in range(4061, 4051, -1)]
    assert all(alert["rankingBasis"] == "session" for alert in payload["longRankings"])
    assert payload["longRankings"][0]["sessionNetBuyLots"] > 0


def test_payload_keeps_opening_cumulative_rankings_after_close() -> None:
    stocks = tuple(
        ThemeStock(str(4070 + index), f"After {index}", "上市", "AI", ("AI",))
        for index in range(12)
    )

    def rows(force_lots: int) -> list[SimpleNamespace]:
        return [
            alert_snapshot(0, buy_shares=20_000, sell_shares=5_000),
            alert_snapshot(5, buy_shares=20_000 + force_lots * 1_000, sell_shares=7_000),
        ]

    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            return {
                stock_id: rows(20 + int(stock_id) - 4070)
                for stock_id in stock_ids
            }

    monitor = ElectronicChipFlowAlertMonitor(service=ServiceStub())  # type: ignore[arg-type]
    monitor._stocks = stocks
    now = datetime(2026, 7, 29, 14, 0, tzinfo=TAIPEI)

    payload = monitor.payload(now=now, client_id="browser-one")

    assert payload["marketOpen"] is False
    assert payload["alerts"] == []
    assert len(payload["longRankings"]) == MOMENTUM_RANK_LIMIT
    assert payload["longRankings"][0]["symbol"] == "4081"


def test_payload_ranks_short_momentum_top_ten() -> None:
    stocks = tuple(
        ThemeStock(str(4100 + index), f"Short {index}", "銝?", "AI", ("AI",))
        for index in range(12)
    )

    def rows(force_lots: int) -> list[SimpleNamespace]:
        return [
            alert_snapshot(0, buy_shares=5_000, sell_shares=20_000),
            alert_snapshot(
                2,
                buy_shares=6_000,
                sell_shares=20_000 + force_lots * 500,
            ),
            alert_snapshot(
                5,
                buy_shares=7_000,
                sell_shares=20_000 + force_lots * 1_000,
            ),
        ]

    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            return {
                stock_id: rows(20 + int(stock_id) - 4100)
                for stock_id in stock_ids
            }

    monitor = ElectronicChipFlowAlertMonitor(service=ServiceStub())  # type: ignore[arg-type]
    monitor._stocks = stocks
    now = datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI)

    payload = monitor.payload(now=now, client_id="browser-one")
    ranked_symbols = [alert["symbol"] for alert in payload["shortAlerts"]]

    assert len(ranked_symbols) == MOMENTUM_RANK_LIMIT
    assert payload["shortCount"] == 12
    assert [alert["rank"] for alert in payload["shortAlerts"]] == list(range(1, 11))
    assert ranked_symbols == [str(symbol) for symbol in range(4111, 4101, -1)]


def test_payload_keeps_short_top_ten_rankings_when_no_strict_alerts() -> None:
    stocks = tuple(
        ThemeStock(str(4100 + index), f"Short {index}", "上市", "AI", ("AI",))
        for index in range(12)
    )

    def rows(force_lots: int) -> list[SimpleNamespace]:
        buy_shares = 20_000
        sell_shares = 20_000 + force_lots * 1_000
        return [
            alert_snapshot(0, buy_shares=buy_shares, sell_shares=sell_shares),
            alert_snapshot(2, buy_shares=buy_shares, sell_shares=sell_shares),
            alert_snapshot(5, buy_shares=buy_shares, sell_shares=sell_shares),
        ]

    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            return {
                stock_id: rows(20 + int(stock_id) - 4100)
                for stock_id in stock_ids
            }

    monitor = ElectronicChipFlowAlertMonitor(service=ServiceStub())  # type: ignore[arg-type]
    monitor._stocks = stocks
    now = datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI)

    payload = monitor.payload(now=now, client_id="browser-one")
    ranked_symbols = [alert["symbol"] for alert in payload["shortRankings"]]

    assert payload["shortAlerts"] == []
    assert payload["shortCount"] == 0
    assert len(ranked_symbols) == MOMENTUM_RANK_LIMIT
    assert payload["shortRankingCount"] == 12
    assert [alert["rank"] for alert in payload["shortRankings"]] == list(range(1, 11))
    assert ranked_symbols == [str(symbol) for symbol in range(4111, 4101, -1)]
    assert all(alert["currentQualifies"] is False for alert in payload["shortRankings"])


def test_pins_add_ten_extra_high_frequency_symbols_above_auto_top_ten() -> None:
    top_stocks = tuple(
        ThemeStock(str(4200 + index), f"Top {index}", "銝?", "AI", ("AI",))
        for index in range(10)
    )
    pinned_stocks = tuple(
        ThemeStock(str(4300 + index), f"Pin {index}", "銝?", "AI", ("AI",))
        for index in range(12)
    )

    def top_rows(stock_id: str) -> list[SimpleNamespace]:
        force_lots = 20 + int(stock_id) - 4200
        return [
            alert_snapshot(0, buy_shares=20_000, sell_shares=5_000),
            alert_snapshot(
                2,
                buy_shares=20_000 + force_lots * 500,
                sell_shares=6_000,
            ),
            alert_snapshot(
                5,
                buy_shares=20_000 + force_lots * 1_000,
                sell_shares=7_000,
            ),
        ]

    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            return {
                stock_id: top_rows(stock_id) if stock_id.startswith("42") else []
                for stock_id in stock_ids
            }

    monitor = ElectronicChipFlowAlertMonitor(service=ServiceStub())  # type: ignore[arg-type]
    monitor._stocks = (*top_stocks, *pinned_stocks)
    pinned_symbols = [
        top_stocks[0].symbol,
        top_stocks[1].symbol,
        *[stock.symbol for stock in pinned_stocks],
    ]
    now = datetime(2026, 7, 29, 10, 6, tzinfo=TAIPEI)

    payload = monitor.payload(
        now=now,
        pinned_symbols=pinned_symbols,
        client_id="browser-one",
    )
    high_frequency_symbols = set(monitor.high_frequency_symbols_snapshot(now))

    assert payload["autoTopTrackingCount"] == MOMENTUM_RANK_LIMIT
    assert payload["extraPinnedTrackingLimit"] == EXTRA_PINNED_TRACKING_LIMIT
    assert payload["extraPinnedTrackingCount"] == EXTRA_PINNED_TRACKING_LIMIT
    assert set(stock.symbol for stock in top_stocks) <= high_frequency_symbols
    assert set(stock.symbol for stock in pinned_stocks[:10]) <= high_frequency_symbols
    assert pinned_stocks[10].symbol not in high_frequency_symbols
    assert pinned_stocks[11].symbol not in high_frequency_symbols


def test_layered_scan_batch_prioritizes_hot_fast_and_background_stocks() -> None:
    monitor = ElectronicChipFlowAlertMonitor()
    stocks = tuple(
        ThemeStock(str(4000 + index), f"測試{index}", "上市", "電子零組件", ("熱門股",))
        for index in range(60)
    )
    monitor._stocks = stocks
    monitor._fast_symbols = tuple(stock.symbol for stock in stocks[:FAST_POPULAR_LIMIT])
    monitor._hot_symbols = {stocks[55].symbol}

    batch = monitor._next_scan_batch()
    symbols = {stock.symbol for stock in batch}

    assert stocks[55].symbol in symbols
    assert len(symbols & set(monitor._fast_symbols)) == 4
    assert len(symbols - set(monitor._fast_symbols) - monitor._hot_symbols) == 1


def test_fast_fifty_complete_one_rotation_in_thirteen_batches() -> None:
    monitor = ElectronicChipFlowAlertMonitor()
    stocks = tuple(
        ThemeStock(str(4000 + index), f"測試{index}", "上市", "電子零組件", ("熱門股",))
        for index in range(60)
    )
    monitor._stocks = stocks
    monitor._fast_symbols = tuple(stock.symbol for stock in stocks[:FAST_POPULAR_LIMIT])

    scanned = {
        stock.symbol
        for _ in range(13)
        for stock in monitor._next_scan_batch()
        if stock.symbol in monitor._fast_symbols
    }

    assert scanned == set(monitor._fast_symbols)


def test_three_hundred_stock_background_pool_completes_within_ninety_seconds() -> None:
    monitor = ElectronicChipFlowAlertMonitor()
    stocks = tuple(
        ThemeStock(str(4000 + index), f"測試{index}", "上市", "市場熱門", ("熱門股",))
        for index in range(300)
    )
    monitor._stocks = stocks
    monitor._fast_symbols = tuple(stock.symbol for stock in stocks[:FAST_POPULAR_LIMIT])
    background_symbols = {stock.symbol for stock in stocks[FAST_POPULAR_LIMIT:]}
    batches = math.ceil(90 / monitor.scan_interval_seconds)

    scanned = {
        stock.symbol
        for _ in range(batches)
        for stock in monitor._next_scan_batch()
        if stock.symbol in background_symbols
    }

    assert scanned == background_symbols


def test_alert_payload_uses_live_memory_snapshots_for_browser_polls() -> None:
    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            return {symbol: [] for symbol in stock_ids}

    monitor = ElectronicChipFlowAlertMonitor(service=ServiceStub())  # type: ignore[arg-type]
    monitor._stocks = (ALERT_STOCK,)
    now = datetime(2026, 7, 29, 10, 0, tzinfo=TAIPEI)
    first = monitor.payload(now=now, client_id="browser-one")
    second = monitor.payload(now=now, client_id="browser-two")

    assert first["status"] == "warming"
    assert second["status"] == "warming"


def test_empty_popular_refresh_preserves_last_successful_universe() -> None:
    popular = tuple(
        ThemeStock(str(4000 + index), f"熱門{index}", "上市", "市場熱門", ("熱門股",))
        for index in range(200)
    )

    class PopularProviderStub:
        def __init__(self) -> None:
            self.responses = [popular, ()]

        async def fetch(self) -> tuple[ThemeStock, ...]:
            return self.responses.pop(0)

    monitor = ElectronicChipFlowAlertMonitor(
        popular_stock_provider=PopularProviderStub(),  # type: ignore[arg-type]
    )
    first_at = datetime(2026, 7, 29, 9, 0, tzinfo=TAIPEI)
    asyncio.run(monitor._refresh_universe(first_at))
    first_symbols = {stock.symbol for stock in monitor._stocks}

    asyncio.run(monitor._refresh_universe(first_at + timedelta(minutes=16)))

    assert {stock.symbol for stock in monitor._stocks} == first_symbols
    assert monitor._universe_status == "degraded"
    assert "保留上一版" in str(monitor._universe_notice)


def test_multiple_browsers_share_one_momentum_payload_computation() -> None:
    class ServiceStub:
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(available=True, source="test-stream"),
        )

        def __init__(self) -> None:
            self.snapshot_calls = 0

        def alert_snapshots_snapshot(
            self,
            stock_ids: list[str],
            trade_date: date,
        ) -> dict[str, list[SimpleNamespace]]:
            self.snapshot_calls += 1
            return {symbol: [] for symbol in stock_ids}

    class RestrictionStub:
        state = {"status": "healthy", "lastRefreshAt": "2026-07-29T09:00:00+08:00"}

        def is_disposed(self, symbol: object) -> bool:
            return False

        def market_restrictions_available(self, market: object) -> bool:
            return True

    service = ServiceStub()
    monitor = ElectronicChipFlowAlertMonitor(
        service=service,  # type: ignore[arg-type]
        restriction_service=RestrictionStub(),  # type: ignore[arg-type]
    )
    monitor._stocks = (ALERT_STOCK,)
    now = datetime(2026, 7, 29, 10, 0, 0, tzinfo=TAIPEI)

    first = monitor.payload(now=now, pinned_symbols=(), client_id="browser-one")
    second = monitor.payload(
        now=now + timedelta(milliseconds=500),
        pinned_symbols=(),
        client_id="browser-two",
    )

    assert service.snapshot_calls == 1
    assert first["payloadCacheHit"] is False
    assert second["payloadCacheHit"] is True
    assert second["payloadCacheHits"] == 1


def test_background_scan_limits_concurrent_stock_jobs() -> None:
    monitor = ElectronicChipFlowAlertMonitor()
    stocks = tuple(
        ThemeStock(str(4000 + index), f"測試{index}", "上市", "市場熱門", ("熱門股",))
        for index in range(20)
    )
    active = 0
    maximum_active = 0

    async def fake_scan(
        stock: ThemeStock,
        trade_date: date,
        pinned_symbols: set[str],
    ) -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    monitor._next_scan_batch = lambda: list(stocks)  # type: ignore[method-assign]
    monitor._scan_stock = fake_scan  # type: ignore[method-assign]

    asyncio.run(monitor._scan_next(DAY))

    assert maximum_active == MAX_CONCURRENT_STOCK_SCANS


def test_unknown_trade_keeps_volume_but_not_buy_sell_totals() -> None:
    flow = accumulator()
    flow.process(tick("unknown-volume", price="100", shares=1_000))
    assert flow.totals.unknown_shares == 1_000
    assert flow.totals.classified_shares == 0
    assert flow.totals.retail_control_ratio is None


def test_each_minute_is_a_cumulative_snapshot() -> None:
    flow = accumulator()
    flow.process(tick(
        "minute-one",
        price="100",
        shares=20_000,
        ask="100",
        at=datetime(2026, 7, 29, 9, 1, 5, tzinfo=TAIPEI),
    ))
    flow.process(tick(
        "minute-two",
        price="100",
        shares=30_000,
        bid="100",
        at=datetime(2026, 7, 29, 9, 2, 5, tzinfo=TAIPEI),
    ))
    snapshots = list(flow.snapshots.values())
    assert snapshots[0].totals.large_net_shares == 20_000
    assert snapshots[1].totals.large_net_shares == -10_000


def test_dynamic_large_threshold_uses_regular_session_p99() -> None:
    start = datetime(2026, 7, 29, 9, 1, tzinfo=TAIPEI)
    trades = [
        tick(
            f"p99-{index}",
            price="100",
            shares=10_000 * index,
            at=start + timedelta(seconds=index),
        )
        for index in range(1, 101)
    ]
    trades.extend([
        tick(
            "odd-lot-outlier",
            price="1000",
            shares=1_000_000,
            at=datetime(2026, 7, 29, 10, 0, tzinfo=TAIPEI),
            session=TradeSession.ODD_LOT,
        ),
        tick(
            "closing-outlier",
            price="1000",
            shares=1_000_000,
            at=datetime(2026, 7, 29, 13, 30, tzinfo=TAIPEI),
        ),
    ])

    result = DynamicOrderThresholdCalculator(
        2_000_000,
        percentile=0.99,
        min_samples=100,
    ).calculate(trades)

    assert result.mode == "dynamic_percentile"
    assert result.sample_count == 100
    assert result.amount == 99_000_000


def test_dynamic_large_threshold_keeps_floor_when_sample_is_small() -> None:
    result = DynamicOrderThresholdCalculator(
        2_000_000,
        percentile=0.99,
        min_samples=100,
    ).calculate([
        tick("only", price="100", shares=50_000),
    ])

    assert result.mode == "fixed_floor"
    assert result.sample_count == 1
    assert result.amount == 2_000_000


class CompleteTestTradeProvider:
    capabilities = RealtimeTradeCapabilities(
        source="test-only ticks",
        available=True,
        complete_intraday_ticks=True,
        has_trade_id=True,
        has_bid_ask_at_trade=True,
        has_source_side=False,
        reason="",
        missing_fields=(),
    )

    def __init__(self, ticks: list[NormalizedTradeTick]):
        self.ticks = ticks

    async def get_trade_ticks(self, stock_id: str, trade_date: date):
        return [
            item for item in self.ticks
            if item.stock_id == stock_id and item.trade_date == trade_date
        ]

    def subscribe(self, stock_id, callback) -> None:
        del stock_id, callback

    def unsubscribe(self, stock_id) -> None:
        del stock_id


class IncrementalTestTradeProvider(CompleteTestTradeProvider):
    def __init__(self, batches: list[list[NormalizedTradeTick]]):
        super().__init__([])
        self.batches = list(batches)

    async def drain_trade_ticks(self, stock_id: str, trade_date: date):
        if not self.batches:
            return []
        return [
            item for item in self.batches.pop(0)
            if item.stock_id == stock_id and item.trade_date == trade_date
        ]


def test_service_persists_and_upserts_minute_snapshots_for_reload() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    provider = CompleteTestTradeProvider([
        tick(
            "buy-one",
            price="200",
            shares=12_000,
            ask="200",
            at=datetime(2026, 7, 29, 9, 1, 5, tzinfo=TAIPEI),
        ),
        tick(
            "sell-one",
            price="200",
            shares=28_000,
            bid="200",
            at=datetime(2026, 7, 29, 9, 2, 5, tzinfo=TAIPEI),
        ),
    ])
    with Session(engine) as db:
        first = asyncio.run(ChipFlowService(provider).get_intraday("3138", db, DAY))
        assert len(first["series"]) == 2
        assert first["latest"]["largeNetLots"] == -16

        reloaded = asyncio.run(ChipFlowService(provider).get_intraday("3138", db, DAY))
        assert len(reloaded["series"]) == 2
        assert reloaded["latest"]["largeNetShares"] == -16_000


def test_service_accumulates_incremental_batches_without_retaining_trade_ids() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    provider = IncrementalTestTradeProvider([
        [tick(
            "first", price="100", shares=20_000, ask="100",
            at=datetime(2026, 7, 29, 9, 1, 5, tzinfo=TAIPEI),
        )],
        [tick(
            "second", price="200", shares=10_000, bid="200",
            at=datetime(2026, 7, 29, 9, 2, 5, tzinfo=TAIPEI),
        )],
    ])
    service = ChipFlowService(provider)

    with Session(engine) as db:
        asyncio.run(service.get_intraday("3138", db, DAY))
        result = asyncio.run(service.get_intraday("3138", db, DAY))

    assert len(result["series"]) == 2
    assert result["latest"]["largeNetShares"] == 10_000
    assert service._accumulators[("3138", DAY)].seen_trade_ids == set()


def test_service_replaces_stale_same_day_snapshots_with_reconstructed_series() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    provider = CompleteTestTradeProvider([
        tick(
            "fresh",
            price="100",
            shares=20_000,
            ask="100",
            at=datetime(2026, 7, 29, 9, 1, 5, tzinfo=TAIPEI),
        ),
    ])
    with Session(engine) as db:
        db.add(ChipFlowSnapshot(
            trade_date=DAY,
            stock_id="3138",
            snapshot_time=datetime(2026, 7, 29, 9, 0, tzinfo=TAIPEI),
            large_buy_shares=999_000,
            large_sell_shares=0,
            large_net_shares=999_000,
            medium_buy_shares=0,
            medium_sell_shares=0,
            medium_net_shares=0,
            small_buy_shares=0,
            small_sell_shares=0,
            small_net_shares=0,
            unknown_shares=0,
            updated_at=datetime(2026, 7, 29, 9, 0, tzinfo=TAIPEI),
        ))
        db.add(ChipFlowSnapshot(
            trade_date=DAY,
            stock_id="3138",
            snapshot_time=datetime(2026, 7, 29, 10, 30, tzinfo=TAIPEI),
            large_buy_shares=888_000,
            large_sell_shares=0,
            large_net_shares=888_000,
            medium_buy_shares=0,
            medium_sell_shares=0,
            medium_net_shares=0,
            small_buy_shares=0,
            small_sell_shares=0,
            small_net_shares=0,
            unknown_shares=0,
            updated_at=datetime(2026, 7, 29, 10, 30, tzinfo=TAIPEI),
        ))
        db.commit()

        result = asyncio.run(ChipFlowService(provider).get_intraday("3138", db, DAY))

        assert len(result["series"]) == 1
        assert result["latest"]["time"] == "09:01"
        assert result["latest"]["largeBuyShares"] == 20_000
        assert result["latest"]["updatedAt"].endswith("+08:00")


def test_service_excludes_closing_auction_and_after_hours_trades() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    provider = CompleteTestTradeProvider([
        tick(
            "active-buy",
            price="100",
            shares=20_000,
            ask="100",
            at=datetime(2026, 7, 29, 13, 29, 30, tzinfo=TAIPEI),
        ),
        tick(
            "closing-sell",
            price="100",
            shares=100_000,
            bid="100",
            at=datetime(2026, 7, 29, 13, 30, tzinfo=TAIPEI),
        ),
        tick(
            "after-hours-sell",
            price="100",
            shares=200_000,
            bid="100",
            at=datetime(2026, 7, 29, 14, 30, tzinfo=TAIPEI),
        ),
    ])

    with Session(engine) as db:
        result = asyncio.run(ChipFlowService(provider).get_intraday("3138", db, DAY))

    assert result["latest"]["time"] == "13:29"
    assert result["latest"]["largeNetLots"] == 20
    assert result["excludedClosingAuctionLots"] == 100
    assert result["excludedAfterHoursLots"] == 200
    assert result["largeOrderThresholdMode"] == "fixed_floor"


def test_fugle_provider_normalizes_regular_and_odd_lot_shares() -> None:
    today = datetime.now(TAIPEI).date()
    regular_time = datetime(
        today.year, today.month, today.day, 9, 1, 5, tzinfo=TAIPEI
    )
    odd_time = regular_time.replace(second=10)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        is_odd_lot = request.url.params.get("type") == "oddlot"
        offset = int(request.url.params["offset"])
        if offset:
            data = []
        elif is_odd_lot:
            data = [{
                "serial": 7,
                "time": int(odd_time.timestamp() * 1_000_000),
                "price": 100,
                "size": 500,
                "bid": 99.5,
                "ask": 100,
            }]
        else:
            data = [{
                "serial": 7,
                "time": int(regular_time.timestamp() * 1_000_000),
                "price": 100,
                "size": 20,
                "bid": 99.5,
                "ask": 100,
            }]
        return httpx.Response(
            200,
            json={"date": today.isoformat(), "symbol": "3138", "data": data},
        )

    provider = FugleRealtimeTradeProvider(
        "test-key",
        page_size=2,
        transport=httpx.MockTransport(handler),
    )
    ticks = asyncio.run(provider.get_trade_ticks("3138", today))

    assert [item.volume_shares for item in ticks] == [20_000, 500]
    assert ticks[0].id.endswith(":regular:7")
    assert ticks[1].id.endswith(":oddlot:7")
    assert ticks[0].session == TradeSession.REGULAR
    assert ticks[1].session == TradeSession.ODD_LOT
    assert all(request.headers["X-API-KEY"] == "test-key" for request in requests)


def test_fugle_provider_uses_incremental_offset_and_deduplicates_serial() -> None:
    today = datetime.now(TAIPEI).date()
    at = datetime(today.year, today.month, today.day, 9, 1, tzinfo=TAIPEI)
    observed_offsets: list[int] = []
    phase = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        offset = int(request.url.params["offset"])
        observed_offsets.append(offset)
        if offset == 0:
            rows = [{
                "serial": 1,
                "time": int(at.timestamp() * 1_000_000),
                "price": 100,
                "size": 1,
                "bid": 99,
                "ask": 100,
            }]
            phase = 1
        elif offset == 1 and phase == 1:
            rows = [{
                "serial": 2,
                "time": int(at.replace(second=1).timestamp() * 1_000_000),
                "price": 101,
                "size": 1,
                "bid": 100,
                "ask": 101,
            }]
            phase = 2
        else:
            rows = []
        return httpx.Response(
            200,
            json={"date": today.isoformat(), "symbol": "3138", "data": rows},
        )

    provider = FugleRealtimeTradeProvider(
        "test-key",
        page_size=10,
        include_odd_lot=False,
        transport=httpx.MockTransport(handler),
    )
    first = asyncio.run(provider.get_trade_ticks("3138", today))
    second = asyncio.run(provider.get_trade_ticks("3138", today))
    third = asyncio.run(provider.get_trade_ticks("3138", today))

    assert len(first) == 1
    assert len(second) == 2
    assert len(third) == 2
    assert observed_offsets == [0, 1, 2]
    assert len({item.id for item in third}) == 2


def test_fugle_provider_backs_off_after_rate_limit_without_repeating_requests() -> None:
    today = datetime.now(TAIPEI).date()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"}, json={"message": "rate limited"})

    provider = FugleRealtimeTradeProvider(
        "test-key",
        include_odd_lot=False,
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.get_trade_ticks("3138", today)) == []
    assert asyncio.run(provider.get_trade_ticks("2308", today)) == []
    assert len(requests) == 1


def test_fugle_provider_continues_after_fugle_500_row_api_cap() -> None:
    today = datetime.now(TAIPEI).date()
    at = datetime(today.year, today.month, today.day, 9, 1, tzinfo=TAIPEI)
    observed_offsets: list[int] = []

    def row(serial: int) -> dict[str, int]:
        return {
            "serial": serial,
            "time": int(at.replace(second=serial % 60).timestamp() * 1_000_000),
            "price": 100,
            "size": 1,
            "bid": 99,
            "ask": 100,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        observed_offsets.append(offset)
        rows = (
            [row(serial) for serial in range(1, 501)] if offset == 0
            else [row(501)] if offset == 500
            else []
        )
        return httpx.Response(
            200,
            json={"date": today.isoformat(), "symbol": "2308", "data": rows},
        )

    provider = FugleRealtimeTradeProvider(
        "test-key",
        page_size=1_000,
        include_odd_lot=False,
        transport=httpx.MockTransport(handler),
    )
    ticks = asyncio.run(provider.get_trade_ticks("2308", today))

    assert len(ticks) == 501
    assert observed_offsets == [0, 500]


def test_fugle_provider_without_key_stays_unavailable() -> None:
    provider = FugleRealtimeTradeProvider("")
    assert provider.capabilities.available is False
    assert provider.capabilities.complete_intraday_ticks is False
    assert asyncio.run(provider.get_trade_ticks("3138", DAY)) == []
