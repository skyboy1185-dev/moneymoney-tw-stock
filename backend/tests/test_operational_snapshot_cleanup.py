from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app import database
from app.services.day_trading_candidate_snapshots import save_candidate_snapshots
from app.services.day_trading_schedule import TradingScheduleConfig
from app.services.limit_up_ai import save_snapshots


class DiskFullSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.rolled_back = False

    def scalar(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    def flush(self) -> None:
        raise SQLAlchemyError("No space left on device")

    def rollback(self) -> None:
        self.rolled_back = True


def _limit_candidate() -> dict[str, Any]:
    return {
        "id": "2330-limit",
        "symbol": "2330",
        "stockName": "台積電",
        "market": "上市",
        "rank": 1,
        "category": "attack",
        "setupType": "pre_limit_attack",
        "score": 90,
        "price": 1200,
        "changePercent": 8,
        "limitDistancePercent": 1.2,
    }


def _day_candidate() -> dict[str, Any]:
    return {
        "id": "2330-day",
        "symbol": "2330",
        "stockName": "台積電",
        "market": "上市",
        "direction": "long",
        "rank": 1,
        "isOfficialRecommendation": True,
        "confidenceScore": 90,
        "healthScore": 88,
        "confirmationScore": 80,
        "largeOrderForce": 200,
        "riskRewardRatio": 2.5,
        "liquidityScore": 90,
    }


def test_limit_up_snapshot_disk_error_rolls_back_without_raising() -> None:
    db = DiskFullSession()

    saved = save_snapshots(db, [_limit_candidate()], datetime(2026, 8, 31, 2, 39, 44, tzinfo=UTC))

    assert saved == 0
    assert db.rolled_back is True


def test_day_candidate_snapshot_disk_error_rolls_back_without_raising() -> None:
    db = DiskFullSession()

    saved = save_candidate_snapshots(
        db,
        [_day_candidate()],
        config=TradingScheduleConfig(),
        snapshot_at=datetime(2026, 8, 31, 2, 39, 44, tzinfo=UTC),
    )

    assert saved == 0
    assert db.rolled_back is True


def test_operational_cleanup_prunes_intraday_snapshots_by_hours(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(database, "engine", engine)
    now = datetime.now(UTC)
    old_date = date.today() - timedelta(days=3)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE chip_flow_snapshots (trade_date DATE)"))
        connection.execute(text("CREATE TABLE day_trading_signals (generated_at DATETIME)"))
        connection.execute(text("CREATE TABLE day_trading_candidate_snapshots (trading_date DATE, snapshot_at DATETIME)"))
        connection.execute(text("CREATE TABLE limit_up_ai_snapshots (trading_date DATE, snapshot_at DATETIME)"))
        connection.execute(text("INSERT INTO chip_flow_snapshots VALUES (:day)"), {"day": old_date.isoformat()})
        connection.execute(text("INSERT INTO day_trading_signals VALUES (:at)"), {"at": (now - timedelta(days=8)).isoformat()})
        for table in ("day_trading_candidate_snapshots", "limit_up_ai_snapshots"):
            connection.execute(
                text(f"INSERT INTO {table} VALUES (:day, :at)"),
                {"day": date.today().isoformat(), "at": (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")},
            )
            connection.execute(
                text(f"INSERT INTO {table} VALUES (:day, :at)"),
                {"day": date.today().isoformat(), "at": now.strftime("%Y-%m-%d %H:%M:%S")},
            )

    deleted = database.cleanup_expired_operational_data(retention_days=1, intraday_snapshot_retention_hours=2)

    assert deleted["chip_flow_snapshots"] == 1
    assert deleted["day_trading_signals"] == 1
    assert deleted["day_trading_candidate_snapshots"] == 1
    assert deleted["limit_up_ai_snapshots"] == 1
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM day_trading_candidate_snapshots")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM limit_up_ai_snapshots")) == 1
