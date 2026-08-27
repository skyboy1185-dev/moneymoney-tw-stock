from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import LimitUpAiSettings, LimitUpAiSnapshot
from app.services import limit_up_ai as limit_up_ai_service
from app.services.limit_up_ai_automation import DEFAULT_USER_ID, LimitUpAiAutomation


NOW = datetime(2026, 8, 27, 2, 15, tzinfo=UTC)


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_dashboard_payload_reads_latest_snapshot_without_running_scan(monkeypatch) -> None:
    factory = _session_factory()

    def fail_scan(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("dashboard_payload must not run an active scan")

    monkeypatch.setattr(limit_up_ai_service, "scan_limit_up_candidates", fail_scan)
    candidate = {
        "id": "2330-test",
        "symbol": "2330",
        "stockName": "台積電",
        "market": "上市",
        "rank": 1,
        "category": "attack",
        "categoryLabel": "漲停攻擊候選",
        "setupType": "intraday_breakout",
        "setupLabel": "盤中整理後再突破",
        "actionable": True,
        "limitDistancePercent": 2.1,
    }
    with factory() as db:
        db.add(LimitUpAiSettings(user_id="test-user", updated_at=NOW))
        db.add(LimitUpAiSnapshot(
            signal_id="2330-test",
            trading_date=NOW.date(),
            snapshot_at=NOW,
            symbol="2330",
            stock_name="台積電",
            market="上市",
            rank=1,
            category="attack",
            setup_type="intraday_breakout",
            score=90,
            price=100,
            change_pct=5,
            limit_distance_pct=2.1,
            payload_json=json.dumps(candidate),
        ))
        db.commit()

        payload = limit_up_ai_service.dashboard_payload(db, "test-user", now=NOW)

    assert payload["summary"]["candidateCount"] == 1
    assert payload["summary"]["actionableCount"] == 1
    assert payload["candidates"][0]["symbol"] == "2330"


def test_limit_up_ai_automation_forced_scan_runs_demo_user() -> None:
    calls: list[str] = []

    def runner(db: Session, user_id: str, now: datetime | None) -> dict[str, object]:
        calls.append(user_id)
        return {"summary": {"candidateCount": 3, "actionableCount": 1, "openPositionCount": 0}}

    automation = LimitUpAiAutomation(session_factory=_session_factory(), runner=runner)

    result = asyncio.run(automation.run_once(NOW, force=True))

    assert calls == [DEFAULT_USER_ID]
    assert result["status"] == "scanned"
    assert automation.status()["lastUserCount"] == 1
    assert automation.status()["cycleCount"] == 1


def test_limit_up_ai_automation_skips_outside_market_session() -> None:
    calls: list[str] = []

    def runner(db: Session, user_id: str, now: datetime | None) -> dict[str, object]:
        calls.append(user_id)
        return {"summary": {}}

    automation = LimitUpAiAutomation(session_factory=_session_factory(), runner=runner)
    saturday = datetime(2026, 8, 29, 2, 15, tzinfo=UTC)

    result = asyncio.run(automation.run_once(saturday))

    assert calls == []
    assert result["status"] == "waiting_market_session"
