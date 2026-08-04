from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.routers import ai_stock


def test_ai_stock_settings_and_dashboard_api() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def session_dependency():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(ai_stock.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = session_dependency
    headers = {"x-user-id": "api-test-user"}
    with TestClient(app) as client:
        automation = client.get("/api/v1/ai-stock-automation/status")
        assert automation.status_code == 200
        assert "lastScanStatus" in automation.json()

        settings = client.get("/api/v1/portfolio/settings", headers=headers)
        assert settings.status_code == 200
        assert settings.json()["totalCapital"] == 1_000_000

        updated = client.put("/api/v1/portfolio/settings", headers=headers, json={
            "total_capital": "1500000",
            "minimum_cash_percentage": "20",
            "max_total_exposure": "80",
            "max_position_percentage": "20",
            "max_industry_percentage": "35",
            "max_risk_per_trade": "0.5",
            "max_portfolio_risk": "3",
            "maximum_add_on_count": 2,
            "initial_entry_ratio": "40",
            "first_add_on_ratio": "30",
            "second_add_on_ratio": "30",
            "allow_add_on": True,
            "prohibit_averaging_down": True,
            "daily_summary_enabled": True,
        })
        assert updated.status_code == 200
        assert updated.json()["totalCapital"] == 1_500_000

        dashboard = client.get("/api/v1/ai-stock-dashboard", headers=headers)
        assert dashboard.status_code == 200
        assert dashboard.json()["waiting"] == []
        assert dashboard.json()["positions"] == []
        assert dashboard.json()["disclaimer"] == "僅供研究參考，不構成投資建議。"


def test_sync_rejects_stale_quote_without_creating_monitor() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def session_dependency():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(ai_stock.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = session_dependency
    payload = {
        "items": [{
            "signal_id": "ai-20260724-2317-trend-start",
            "symbol": "2317", "stock_name": "鴻海", "market": "上市", "industry": "電子",
            "strategy_name": "波段起漲 Bot", "secondary_strategies": ["多頭回檔 Bot"],
            "total_score": "90", "strategy_fit": "88", "market_fit": "80", "health_score": "82",
            "current_price": "252.5", "entry_min": "250", "entry_max": "253",
            "stop_loss": "245", "target_1": "265", "target_2": "275",
            "risk_reward_ratio": "2", "reasons": ["A", "B", "C"], "warnings": [],
            "quote_source": "TWSE MIS", "quote_timestamp": "2026-07-24T13:30:00+08:00",
            "expired_at": datetime.now(UTC).isoformat(),
        }],
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ai-stock-monitor/sync",
            headers={"x-user-id": "stale-test-user"},
            json=payload,
        )
        assert response.status_code == 200
        assert response.json()["count"] == 0
