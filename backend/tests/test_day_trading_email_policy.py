from datetime import UTC, datetime, timedelta

from app.services.day_trading_email_policy import email_is_deliverable


def taipei(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 21, hour - 8, minute, tzinfo=UTC)


def test_entry_email_expires_after_two_minutes_and_never_sends_after_entry_close():
    created = taipei(10, 0)
    assert email_is_deliverable("BUY", created, created + timedelta(seconds=119))
    assert not email_is_deliverable("BUY", created, created + timedelta(seconds=121))
    assert not email_is_deliverable("BUY", taipei(13, 19), taipei(13, 21))


def test_exit_email_expires_after_ten_minutes_and_never_sends_after_market_close():
    created = taipei(13, 20)
    assert email_is_deliverable("EXIT", created, taipei(13, 29))
    assert not email_is_deliverable("EXIT", created, taipei(13, 31))


def test_close_report_is_the_only_routine_email_allowed_after_close():
    assert email_is_deliverable("DAILY_REPORT", taipei(13, 40), taipei(13, 45))
    assert not email_is_deliverable("HOURLY_SUMMARY", taipei(12, 0), taipei(13, 45))
    assert email_is_deliverable("SYSTEM_HEARTBEAT_INTERRUPTED", taipei(13, 44), taipei(13, 45))


def test_previous_day_event_is_never_replayed():
    assert not email_is_deliverable("DAILY_REPORT", taipei(13, 40), taipei(13, 40) + timedelta(days=1))
