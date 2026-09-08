from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.long_term_automation import _within_maintenance_window


def test_same_day_maintenance_is_limited_to_the_selection_window() -> None:
    taipei = ZoneInfo("Asia/Taipei")
    assert not _within_maintenance_window(datetime(2026, 9, 8, 9, 14, tzinfo=taipei))
    assert _within_maintenance_window(datetime(2026, 9, 8, 9, 15, tzinfo=taipei))
    assert _within_maintenance_window(datetime(2026, 9, 8, 9, 59, tzinfo=taipei))
    assert not _within_maintenance_window(datetime(2026, 9, 8, 10, 0, tzinfo=taipei))
