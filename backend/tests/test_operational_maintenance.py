from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from app.services.operational_maintenance import (
    OperationalMaintenanceAutomation,
    next_daily_cleanup_after,
)


TAIPEI = ZoneInfo("Asia/Taipei")


class _SessionFactory:
    def __call__(self):
        return self

    def __enter__(self):
        return object()

    def __exit__(self, *_):
        return False


def test_next_daily_cleanup_after_uses_today_before_cleanup_time() -> None:
    now = datetime(2026, 8, 31, 7, 0, tzinfo=UTC)  # 15:00 Taipei

    next_run = next_daily_cleanup_after(now, clock=time(15, 35), timezone=TAIPEI)

    assert next_run.astimezone(TAIPEI) == datetime(2026, 8, 31, 15, 35, tzinfo=TAIPEI)


def test_next_daily_cleanup_after_rolls_to_tomorrow_after_cleanup_time() -> None:
    now = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)  # 16:00 Taipei

    next_run = next_daily_cleanup_after(now, clock=time(15, 35), timezone=TAIPEI)

    assert next_run.astimezone(TAIPEI) == datetime(2026, 9, 1, 15, 35, tzinfo=TAIPEI)


def test_run_once_records_cleanup_result(monkeypatch) -> None:
    def cleanup(**kwargs):
        assert kwargs == {"retention_days": 3, "intraday_snapshot_retention_hours": 2}
        return {"limit_up_ai_snapshots": 3}

    monkeypatch.setattr(
        "app.services.operational_maintenance.database_runtime_status",
        lambda session, expected_host: {
            "databaseSizeMB": 42.5,
            "host": "postgres-bml6.railway.internal",
            "matchesExpectedHost": True,
        },
    )
    automation = OperationalMaintenanceAutomation(
        session_factory=_SessionFactory(),
        cleanup=cleanup,
    )

    result = automation._run_once_sync(datetime(2026, 8, 31, 7, 35, tzinfo=UTC))

    assert result["status"] == "completed"
    assert result["deleted"] == {"limit_up_ai_snapshots": 3}
    assert result["databaseSizeMB"] == 42.5
    assert automation.state["lastError"] is None


def test_run_once_catches_cleanup_failure() -> None:
    def cleanup(**kwargs):
        raise RuntimeError("disk check failed")

    automation = OperationalMaintenanceAutomation(
        session_factory=_SessionFactory(),
        cleanup=cleanup,
    )

    result = automation._run_once_sync(datetime(2026, 8, 31, 7, 35, tzinfo=UTC))

    assert result == {"status": "error", "error": "disk check failed"}
    assert automation.state["status"] == "running"
    assert automation.state["lastError"] == "disk check failed"
