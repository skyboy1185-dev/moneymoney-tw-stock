import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
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
    ChipFlowAlertRules,
    enrich_day_trading_large_order_confirmation,
    evaluate_large_order_surge,
)
from app.services.chip_flow_accumulator import ChipFlowAccumulator
from app.services.chip_flow_repository import ChipFlowRepository
from app.services.chip_flow_types import (
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
ALERT_STOCK = ThemeStock("2330", "台積電", "上市", "半導體", ("AI",))


def alert_snapshot(
    minute: int,
    *,
    buy_shares: int,
    sell_shares: int,
) -> SimpleNamespace:
    snapshot_time = datetime(2026, 7, 29, 10, minute, tzinfo=TAIPEI)
    return SimpleNamespace(
        snapshot_time=snapshot_time,
        large_buy_shares=buy_shares,
        large_sell_shares=sell_shares,
        large_net_shares=buy_shares - sell_shares,
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
    assert result["recentNetBuyLots"] == 25
    assert result["positiveSteps"] == 3


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
