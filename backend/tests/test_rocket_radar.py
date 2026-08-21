from datetime import UTC, date, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.adaptive_schemas import (
    AdaptiveIndustryInput, AdaptiveMarketMetrics, AdaptiveScanPayload, AdaptiveStockInput,
)
from app.database import Base
from app.models import (
    RocketAccount, RocketCandidate, RocketDailyPortfolio, RocketNotification,
    RocketPosition, RocketSignal, RocketTrade,
)
from app.services.rocket_scoring import (
    chase_risk_score, classify_rocket_market, rank_rocket_candidates,
)
from app.services.rocket_service import performance_payload, process_rocket_scan
from app.services.rocket_trading import RocketEvent, record_rocket_event
from app.services.rocket_automation import _buy_email_message


AT = datetime(2026, 8, 10, 2, 30, tzinfo=UTC)


def stock(code: str = "2382", *, price: float = 100, overheated: bool = False) -> AdaptiveStockInput:
    return AdaptiveStockInput(
        stock_code=code, stock_name=f"測試{code}", market_type="上市", industry_code="25",
        main_industry="電腦及週邊設備", sub_industry="AI Server", listing_date=date(2000, 1, 1),
        is_electronic=True, data_completeness=1, quote_source="TWSE MIS", quote_timestamp=AT,
        price=price, open=price * .99, high=price * 1.01, low=price * .98,
        volume_shares=5_000_000, average_volume_20d_shares=2_000_000,
        average_turnover_20d=350_000_000, return_1d=9 if overheated else 2,
        return_3d=20 if overheated else 5, return_5d=28 if overheated else 8,
        return_20d=12, gap_percent=8 if overheated else 1,
        consecutive_strong_up_days=3 if overheated else 1,
        consecutive_long_bullish_days=2 if overheated else 0,
        is_highest_volume_20d=overheated,
        market_return_20d=3, electronic_return_20d=5,
        relative_strength_market=9, relative_strength_electronic=7,
        ma5=price * .99, ma10=price * .97, ma20=price * .94, ma60=price * .88,
        ma5_slope=1, ma20_slope=.8, ma60_slope=.4, atr14=3, atr20_ratio=3,
        rsi14=88 if overheated else 68, range_low=price * .9, range_high=price * .99,
        range_amplitude=10, range_position=.98, breakout_20d=True, breakout_60d=True,
        breakout_percent=1, distance_to_high_percent=0, volume_ratio_5d=1.7,
        volume_ratio_20d=2, close_location=.8, upper_shadow_ratio=.7 if overheated else .1,
        higher_low=True, volume_contracting=False, down_volume_less_than_up=True,
        foreign_net_5d=10_000, trust_net_5d=5_000,
        holder_400_change=.8, holder_1000_change=.5, retail_holder_change=-.2,
        trailing_eps=10, industry_strength_score=90, industry_rank_percentile=.05,
        industry_continuation_days=3, same_industry_strong_count=5,
    )


def payload(*stocks: AdaptiveStockInput, at: datetime = AT) -> AdaptiveScanPayload:
    return AdaptiveScanPayload(
        market=AdaptiveMarketMetrics(
            trade_date=at.astimezone().date(), updated_at=at, market_open=True, official_data=True,
            taiex_close=25000, taiex_return_1d=1.2, taiex_return_5d=4,
            taiex_above_ma5=True, taiex_above_ma20=True, taiex_above_ma60=True,
            ma5_slope=.5, ma20_slope=.4, ma60_slope=.2, volume_ratio_20d=1.2,
            advance_ratio=68, limit_down_count=0, taiex_breakout_20d=True,
        ),
        industries=[AdaptiveIndustryInput(
            sub_industry="AI Server", return_1d=3, return_3d=6, return_5d=10,
            return_20d=18, advance_ratio=80, new_high_ratio=30, volume_growth=60,
            foreign_net_buy=10_000, investment_trust_net_buy=5_000,
            large_holder_change=1, continuation_days=3,
        )],
        stocks=list(stocks), data_sources=["official-test"],
    )


def database() -> tuple:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_market_regime_and_dynamic_exposure() -> None:
    regime = classify_rocket_market(payload(stock()).market)
    assert regime.key == "strong_bull"
    assert regime.exposure_pct == 90


def test_chase_risk_blocks_overheated_stock_even_with_high_rocket_score() -> None:
    hot = stock(overheated=True)
    chase, reasons = chase_risk_score(hot)
    _, _, picks = rank_rocket_candidates(payload(hot))
    assert chase > 75
    assert reasons
    assert picks[0].rocket_score >= 85
    assert picks[0].status == "overheated"


def test_missing_holder_data_is_marked_and_available_weights_are_normalized() -> None:
    item = stock()
    item.holder_400_change = None
    item.holder_1000_change = None
    item.retail_holder_change = None
    _, _, picks = rank_rocket_candidates(payload(item))
    assert "大戶資料暫無" in picks[0].missing_data
    assert picks[0].components["籌碼強度"] is None
    assert picks[0].data_availability_pct == 85


def test_scan_atomically_creates_position_trade_account_and_web_notification() -> None:
    engine, db = database()
    with db:
        result = process_rocket_scan(db, payload(stock()))
        account = db.get(RocketAccount, 1)
        position = db.scalar(select(RocketPosition))
        trade = db.scalar(select(RocketTrade))
        notification = db.scalar(select(RocketNotification).where(RocketNotification.notification_type == "BUY"))
        daily = db.scalar(select(RocketDailyPortfolio))
        candidate = db.scalar(select(RocketCandidate))
    engine.dispose()
    assert result["lineNotifications"] == 0
    assert account is not None and float(account.cash) < 1_000_000
    assert position is not None and position.remaining_quantity > 0 and position.add_stage == 1
    assert trade is not None and trade.action == "BUY"
    assert notification is not None and notification.priority == 2
    assert daily is not None and candidate is not None and candidate.is_top5


def test_notification_deduplication_uses_one_signal_and_one_message() -> None:
    engine, db = database()
    event = RocketEvent(
        key="same-event", event_type="WARNING", timestamp=AT, stock_code="2382", stock_name="廣達",
        title="風險", message="CHASE Risk 升高", reason="測試", new_status="WARNING",
    )
    with db:
        assert record_rocket_event(db, event)
        db.flush()
        assert not record_rocket_event(db, event)
        db.commit()
        signals = list(db.scalars(select(RocketSignal)).all())
        notifications = list(db.scalars(select(RocketNotification)).all())
    engine.dispose()
    assert len(signals) == 1
    assert len(notifications) == 1


def test_buy_email_message_has_clear_quantity_and_amount() -> None:
    notification = RocketNotification(
        id=9, dedupe_key="position:1:BUY:stage:1", created_at=AT,
        stock_code="2317", stock_name="鴻海", notification_type="BUY", priority=2,
        title="飆股雷達｜買進訊號", message="2317 鴻海｜200.00 元模擬買進 2,000 股",
        quantity=2_000, amount=400_000, reason="突破後站穩", is_read=False,
    )
    message = _buy_email_message(notification)
    assert "2317 鴻海" in message
    assert "2,000 股（2 張）" in message
    assert "NT$400,000" in message
    assert "突破後站穩" in message


def test_stop_loss_closes_position_and_only_closed_positions_enter_win_rate() -> None:
    engine, db = database()
    with db:
        process_rocket_scan(db, payload(stock()))
        position = db.scalar(select(RocketPosition).where(RocketPosition.status == "open"))
        assert position is not None
        stopped = stock(price=float(position.stop_loss_price) * .98)
        stopped.breakout_20d = False
        stopped.breakout_60d = False
        stopped.range_high = 100
        stopped.return_1d = -5
        process_rocket_scan(db, payload(stopped, at=AT.replace(hour=3)))
        db.refresh(position)
        stats = performance_payload(db)
        stop_trade = db.scalar(select(RocketTrade).where(RocketTrade.action == "STOP_LOSS"))
    engine.dispose()
    assert position.status == "closed"
    assert stop_trade is not None
    assert stats["totalTrades"] == 1
    assert stats["losingTrades"] == 1
    assert stats["winRate"] == 0
