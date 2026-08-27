from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import LimitUpAiNotification, LimitUpAiPosition, LimitUpAiSettings
from app.services.limit_up_ai import (
    _open_position,
    _sell_position,
    limit_up_performance_payload,
    list_limit_up_notifications,
    mark_all_limit_up_notifications_read,
    mark_limit_up_notification_read,
    score_limit_up_candidate,
)


NOW = datetime(2026, 8, 27, 2, 15, tzinfo=UTC)


def _settings() -> LimitUpAiSettings:
    return LimitUpAiSettings(
        user_id="test-user",
        capital=3_000_000,
        min_price=20,
        max_price=500,
        min_average_turnover_20d=100_000_000,
        min_volume_ratio_20d=1.8,
        first_position_pct=.10,
        max_position_pct=.20,
        max_positions=3,
        max_loss_per_trade_pct=.005,
        max_daily_loss_pct=.01,
        max_consecutive_stops=3,
        overnight_total_pct=.30,
        overnight_single_pct=.15,
        exclude_locked_limit_up=True,
        sound_enabled=False,
        updated_at=NOW,
    )


def _candidate() -> dict[str, object]:
    return score_limit_up_candidate({
        "id": "4939-long-test",
        "symbol": "4939",
        "stockName": "測試股",
        "market": "上市",
        "price": 107.0,
        "previousClose": 100.0,
        "open": 102.0,
        "changePercent": 7.0,
        "volume": 2_500_000,
        "turnover": 267_500_000,
        "volumeScore": 95,
        "confirmationScore": 88,
        "industryScore": 85,
        "marketAlignment": 80,
        "rangePositionPercent": 92,
        "vwapStatus": "站上VWAP",
        "vwapDeviationPercent": 1.2,
        "fiveMinuteStructure": "高低點墊高",
        "fiveMinuteBreakout": True,
        "fiveMinuteLongRetest": True,
        "entryRetestConfirmed": True,
        "threeGateCrossed": True,
        "largeOrderForce": 260,
        "largeOrderContinuousBuy": True,
        "largeOrderDataAvailable": True,
        "spreadPercentage": 0.2,
        "quoteIsRealtime": True,
        "bidVolumes": [300_000, 220_000, 180_000],
        "askVolumes": [120_000, 100_000, 80_000],
    }, _settings(), now=NOW)


def _session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_trade_flow_creates_deduped_buy_sell_notifications_and_performance() -> None:
    with _session() as db:
        settings = _settings()
        db.add(settings)
        db.commit()
        candidate = _candidate()

        _open_position(db, "test-user", settings, candidate, NOW)
        _open_position(db, "test-user", settings, candidate, NOW)
        db.commit()

        buy_notifications = db.scalars(select(LimitUpAiNotification)).all()
        assert len(buy_notifications) == 1
        assert buy_notifications[0].notification_type == "BUY"

        position = db.scalar(select(LimitUpAiPosition))
        assert position is not None
        _sell_position(db, position, position.remaining_quantity, 110.0, "測試出場", NOW)
        db.commit()

        notifications = list_limit_up_notifications(db, "test-user")
        assert notifications["unreadCount"] == 2
        assert [item["type"] for item in notifications["items"]] == ["SELL", "BUY"]

        performance = limit_up_performance_payload(db, "test-user", NOW)
        assert performance["today"]["buyCount"] == 1
        assert performance["today"]["tradeCount"] == 1
        assert performance["today"]["realizedPnl"] > 0
        assert performance["today"]["winRate"] == 100

        first_id = notifications["items"][0]["id"]
        assert mark_limit_up_notification_read(db, "test-user", first_id, NOW) is True
        assert list_limit_up_notifications(db, "test-user")["unreadCount"] == 1
        assert mark_all_limit_up_notifications_read(db, "test-user", NOW) == 1
        assert list_limit_up_notifications(db, "test-user")["unreadCount"] == 0
