from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class ScheduledEvent:
    event_type: str
    config_key: str
    default_time: str
    notification: bool = False


SCHEDULED_EVENTS = (
    ScheduledEvent("DAILY_RESET", "resetTime", "08:30:00"),
    ScheduledEvent("UNIVERSE_LOADED", "universeLoadTime", "08:35:00"),
    ScheduledEvent("HISTORY_LOADED", "historyLoadTime", "08:40:00"),
    ScheduledEvent("HEALTH_CHECKED", "healthCheckTime", "08:45:00"),
    ScheduledEvent("CANDIDATE_POOL_READY", "candidatePoolTime", "08:50:00"),
    ScheduledEvent("PREOPEN_READY", "readyNotificationTime", "08:55:00", True),
    ScheduledEvent("MARKET_SCAN_STARTED", "marketOpenTime", "09:00:00"),
    ScheduledEvent("OPENING_RANGE_READY", "openingRangeReadyTime", "09:15:00", True),
    ScheduledEvent("HOURLY_1000", "summary1000Time", "10:00:00", True),
    ScheduledEvent("HOURLY_1100", "summary1100Time", "11:00:00", True),
    ScheduledEvent("HOURLY_1200", "summary1200Time", "12:00:00", True),
    ScheduledEvent("ENTRY_CLOSED", "latestEntryTime", "13:20:00"),
    ScheduledEvent("FORCED_CLOSE", "forcedCloseTime", "13:25:00"),
    ScheduledEvent("MARKET_SCAN_STOPPED", "marketCloseTime", "13:30:00"),
    ScheduledEvent("BROKER_SYNCED", "brokerSyncTime", "13:35:00"),
    ScheduledEvent("DAILY_REPORT", "closeReportTime", "13:40:00", True),
    ScheduledEvent("STRATEGY_HEALTH_DIAGNOSIS", "healthDiagnosisTime", "13:45:00"),
    ScheduledEvent("WEEKLY_STRATEGY_HEALTH", "weeklyHealthCheckTime", "14:00:00"),
)


def clock(value: object) -> time:
    raw = str(value)
    if len(raw) == 5:
        raw += ":00"
    return time.fromisoformat(raw)


def at_time(day: date, value: object) -> datetime:
    return datetime.combine(day, clock(value), TAIPEI).astimezone(UTC)


def is_trading_day(day: date, holidays: Iterable[date] = ()) -> bool:
    return day.weekday() < 5 and day not in set(holidays)


def event_schedule(day: date, config: Mapping[str, object]) -> list[tuple[ScheduledEvent, datetime]]:
    return [(event, at_time(day, config.get(event.config_key, event.default_time))) for event in SCHEDULED_EVENTS]


def trading_phase(now: datetime, config: Mapping[str, object], holidays: Iterable[date] = ()) -> str:
    local = now.astimezone(TAIPEI)
    if not is_trading_day(local.date(), holidays):
        return "NON_TRADING_DAY"
    current = local.time()
    boundaries = (
        (clock(config.get("resetTime", "08:30:00")), "INITIALIZING"),
        (clock(config.get("marketOpenTime", "09:00:00")), "OPENING_RANGE"),
        (clock(config.get("openingRangeReadyTime", "09:15:00")), "SCANNING"),
        (clock(config.get("latestEntryTime", "13:20:00")), "ENTRY_CLOSED"),
        (clock(config.get("forcedCloseTime", "13:25:00")), "FORCED_CLOSING"),
        (clock(config.get("marketCloseTime", "13:30:00")), "POST_CLOSE"),
        (clock(config.get("closeReportTime", "13:40:00")), "COMPLETED"),
    )
    phase = "BEFORE_INITIALIZATION"
    for boundary, next_phase in boundaries:
        if current >= boundary:
            phase = next_phase
    if current >= clock(config.get("closeReportTime", "13:40:00")):
        phase = "COMPLETED"
    return phase


def next_scheduled_event(now: datetime, config: Mapping[str, object], holidays: Iterable[date] = ()) -> tuple[str, datetime] | None:
    local_day = now.astimezone(TAIPEI).date()
    if not is_trading_day(local_day, holidays):
        return None
    return next(((event.event_type, scheduled) for event, scheduled in event_schedule(local_day, config) if scheduled > now), None)


def due_event_types(now: datetime, config: Mapping[str, object], completed: set[str], started_at: datetime | None = None) -> list[str]:
    """Return due events; old notification events are not replayed after a late boot."""
    day = now.astimezone(TAIPEI).date()
    due: list[str] = []
    for event, scheduled in event_schedule(day, config):
        if event.event_type in completed or scheduled > now:
            continue
        if event.notification and started_at is not None and scheduled < started_at:
            continue
        due.append(event.event_type)
    return due


def stale_reasons(*, now: datetime, heartbeat_at: datetime | None, quote_at: datetime | None, config: Mapping[str, object], market_hours: bool) -> list[str]:
    reasons: list[str] = []
    if heartbeat_at and (now - heartbeat_at).total_seconds() > int(config.get("heartbeatTimeoutSeconds", 45)):
        reasons.append("系統心跳逾時")
    if market_hours and (quote_at is None or (now - quote_at).total_seconds() > int(config.get("quoteTimeoutSeconds", 15))):
        reasons.append("行情中斷或延遲")
    return reasons


def simulated_day(scenario: str) -> dict[str, object]:
    base = ["08:30初始化", "08:55準備完成通知", "09:00開始掃描", "09:15開盤區間通知"]
    notifications = ["PREOPEN_READY", "OPENING_RANGE_READY"]
    orders = 0
    skips: list[str] = []
    if scenario == "NORMAL_SIGNAL":
        base += ["09:16正式訊號", "09:16買進委託", "10:02賣出成交"]
        orders = 1
    elif scenario == "NO_TRADE":
        skips = ["信心分數不足", "風險報酬比不足"]
    elif scenario == "QUOTE_INTERRUPTED":
        base += ["10:00行情中斷", "10:00停止新交易"]
        notifications.append("MARKET_DATA_INTERRUPTED")
    elif scenario == "BROKER_FAILED":
        base += ["09:16券商連線失敗", "09:16停止新交易"]
        skips = ["券商連線異常"]
        notifications.append("BROKER_DISCONNECTED")
    elif scenario == "DUPLICATE_SIGNAL":
        base += ["09:16最高分策略取得委託"]
        skips = ["重複訊號，未執行"] * 4
        orders = 1
    else:
        raise ValueError("unknown scenario")
    base += ["13:20停止新倉", "13:25強制平倉檢查", "13:40收盤報告"]
    notifications.append("DAILY_REPORT")
    return {"scenario": scenario, "records": base, "notifications": notifications, "orders": orders, "skipReasons": skips}
