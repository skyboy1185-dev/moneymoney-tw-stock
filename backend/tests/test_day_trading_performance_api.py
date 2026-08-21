from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import DayTradingPosition, DayTradingTrade
from app.routers import day_trading


def trade(exit_time: datetime, profit: float, direction: str = "long") -> DayTradingTrade:
    return DayTradingTrade(
        user_id="monthly-test-user", symbol="3231", stock_name="緯創", direction=direction,
        entry_time=exit_time, entry_price=100, exit_time=exit_time, exit_price=101,
        quantity=1, fee=100, tax=50, slippage=20, profit=profit,
        return_percentage=profit / 1000, max_profit=max(0, profit), max_loss=min(0, profit),
        entry_reason="測試進場", exit_reason="測試出場",
    )


def test_monthly_performance_and_trades_only_include_selected_taipei_month(monkeypatch) -> None:
    monkeypatch.setattr(day_trading, "_daily_period", lambda: (
        "2026-08-04",
        datetime(2026, 8, 3, 16, 0, tzinfo=UTC),
        datetime(2026, 8, 4, 16, 0, tzinfo=UTC),
    ))
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            trade(datetime(2026, 8, 3, 2, 0, tzinfo=UTC), 1_000),
            trade(datetime(2026, 8, 4, 3, 0, tzinfo=UTC), -400, "short"),
            trade(datetime(2026, 7, 31, 3, 0, tzinfo=UTC), 9_999),
            DayTradingPosition(
                user_id="monthly-test-user", symbol="6669", stock_name="緯穎",
                direction="long", entry_price=6000, quantity=1,
                opened_at=datetime(2026, 8, 4, 1, 30, tzinfo=UTC),
                stop_loss=5800, target_1=6300, target_2=6500,
                current_price=6050, unrealized_profit=50_000, status="open",
            ),
        ])
        db.commit()

    def session_dependency():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(day_trading.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = session_dependency
    headers = {"x-user-id": "monthly-test-user"}
    with TestClient(app) as client:
        performance = client.get("/api/v1/day-trading/performance?month=2026-08", headers=headers)
        trades = client.get("/api/v1/day-trading/trades?month=2026-08", headers=headers)

    assert performance.status_code == 200
    report = performance.json()
    assert report["period"] == "2026-08"
    assert report["tradeCount"] == 2
    assert report["wins"] == 1
    assert report["losses"] == 1
    assert report["realizedProfit"] == 600
    assert report["unrealizedProfit"] == 50_000
    assert report["totalPnl"] == 50_600
    assert report["longRealizedProfit"] == 1_000
    assert report["longUnrealizedProfit"] == 50_000
    assert report["longTotalPnl"] == 51_000
    assert report["longTradeCount"] == 1
    assert report["longOpenPositionCount"] == 1
    assert report["shortRealizedProfit"] == -400
    assert report["shortUnrealizedProfit"] == 0
    assert report["shortTotalPnl"] == -400
    assert report["shortTradeCount"] == 1
    assert report["shortOpenPositionCount"] == 0
    assert report["tradingCost"] == 340
    assert report["today"]["tradeDate"] == "2026-08-04"
    assert report["today"]["tradeCount"] == 1
    assert report["today"]["realizedProfit"] == -400
    assert report["today"]["unrealizedProfit"] == 50_000
    assert report["today"]["totalPnl"] == 49_600
    assert report["today"]["longRealizedProfit"] == 0
    assert report["today"]["longUnrealizedProfit"] == 50_000
    assert report["today"]["longTotalPnl"] == 50_000
    assert report["today"]["shortRealizedProfit"] == -400
    assert report["today"]["shortUnrealizedProfit"] == 0
    assert report["today"]["shortTotalPnl"] == -400
    assert report["today"]["tradingCost"] == 170
    assert trades.status_code == 200
    assert trades.json()["period"] == "2026-08"
    assert len(trades.json()["items"]) == 2


def test_automation_performance_starts_on_august_fourth() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        before = trade(datetime(2026, 8, 3, 2, 0, tzinfo=UTC), -9_999)
        before.user_id = "system-automation"
        included = trade(datetime(2026, 8, 4, 3, 0, tzinfo=UTC), -400)
        included.user_id = "system-automation"
        db.add_all([before, included])
        db.commit()

    def session_dependency():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(day_trading.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = session_dependency
    headers = {"x-user-id": "system-automation"}
    with TestClient(app) as client:
        performance = client.get("/api/v1/day-trading/performance?month=2026-08", headers=headers)
        trades = client.get("/api/v1/day-trading/trades?month=2026-08", headers=headers)

    assert performance.status_code == 200
    assert performance.json()["performanceStartDate"] == "2026-08-04"
    assert performance.json()["strategy"]["key"] == "fixed_2_lots"
    assert performance.json()["capitalPlan"] is None
    assert performance.json()["realizedProfit"] == -400
    assert len(trades.json()["items"]) == 1


def test_dynamic_strategy_has_independent_performance_and_capital_plan() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        original = trade(datetime(2026, 8, 17, 3, 0, tzinfo=UTC), 9_999)
        original.user_id = "system-automation"
        dynamic = trade(datetime(2026, 8, 17, 3, 0, tzinfo=UTC), 600)
        dynamic.user_id = "system-automation-5m"
        db.add_all([original, dynamic])
        db.commit()

    def session_dependency():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(day_trading.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = session_dependency
    headers = {"x-user-id": "system-automation-5m"}
    with TestClient(app) as client:
        performance = client.get("/api/v1/day-trading/performance?month=2026-08", headers=headers)

    assert performance.status_code == 200
    report = performance.json()
    assert report["performanceStartDate"] == "2026-08-17"
    assert report["strategy"]["key"] == "dynamic_5m"
    assert report["realizedProfit"] == 600
    assert report["capitalPlan"]["dailyCapital"] == 5_000_000
