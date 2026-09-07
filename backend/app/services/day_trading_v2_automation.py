"""Persistent schedule, heartbeat, paper scan and email loop for day-trading V2."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
import json
import logging
import os
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..database import SessionLocal
from ..day_trading_v2_models import (
    DayTradeV2CalendarHoliday, DayTradeV2CandidateState, DayTradeV2Notification,
    DayTradeV2RuntimeState, DayTradeV2ScheduleEvent, DayTradeV2Setting, DayTradeV2Trade,
)
from .day_trading_v2 import merged_config
from .day_trading_v2_schedule import at_time, event_schedule, is_trading_day, trading_phase
from .day_trading import day_trading_engine
from .gmail_messaging import gmail_notification_dispatcher
from .popular_stock_universe import OfficialPopularStockProvider, merge_momentum_stocks


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
EMAIL_EVENT_TYPES = {
    "BUY_FILLED", "SELL_FILLED", "ROBOT_HALTED", "EMERGENCY_STOP",
    "MARKET_DATA_INTERRUPTED", "BROKER_DISCONNECTED", "RISK_REDUCED", "DAILY_LOSS_LIMIT",
    "SYSTEM_HEARTBEAT_INTERRUPTED", "PREOPEN_READY", "OPENING_RANGE_READY", "HOURLY_SUMMARY", "DAILY_REPORT",
}


def _json(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def _configured_holidays(db) -> set[date]:
    values = set(db.scalars(select(DayTradeV2CalendarHoliday.holiday_date)).all())
    for raw in get_settings().twse_holidays.split(","):
        try:
            values.add(date.fromisoformat(raw.strip()))
        except ValueError:
            continue
    return values


def _twse_holiday_date(raw: object, year: int) -> date | None:
    value = str(raw).strip()
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.year == year else None
    except ValueError:
        match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", value)
        if not match:
            return None
        try:
            return date(year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None


class DayTradingV2Coordinator:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._worker_id = f"{os.getenv('RAILWAY_REPLICA_ID', 'local')}:{uuid4()}"

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="day-trading-v2-coordinator")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.run_cycle)
                await self.dispatch_pending()
            except Exception:
                logger.exception("day-trading-v2 coordinator cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1)
            except TimeoutError:
                pass

    def _refresh_calendar(self, db, year: int) -> None:
        cached = list(db.scalars(select(DayTradeV2CalendarHoliday)).all())
        if any(row.holiday_date.year == year for row in cached):
            return
        response = httpx.get(
            "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule",
            params={"response": "json", "queryYear": year - 1911}, timeout=8,
            headers={"User-Agent": "TWSE-day-trading-v2/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("stat") != "ok":
            raise RuntimeError("TWSE交易日曆回應異常")
        for item in payload.get("data", []):
            holiday = _twse_holiday_date(item[0] if item else "", year)
            if holiday is None or db.get(DayTradeV2CalendarHoliday, holiday):
                continue
            db.add(DayTradeV2CalendarHoliday(
                holiday_date=holiday, name=str(item[1] if len(item) > 1 else "休市"), source="TWSE",
            ))
        db.commit()

    def _runtime(self, db, user_id: str, mode: str, day: date, config: dict[str, object], now: datetime) -> DayTradeV2RuntimeState:
        runtime = db.scalar(select(DayTradeV2RuntimeState).where(
            DayTradeV2RuntimeState.user_id == user_id, DayTradeV2RuntimeState.mode == mode,
            DayTradeV2RuntimeState.trading_date == day,
        ))
        if runtime is None:
            runtime = DayTradeV2RuntimeState(user_id=user_id, mode=mode, trading_date=day, auto_start=bool(config["autoStart"]), status="WAITING")
            db.add(runtime)
            db.flush()
        lease_until = _aware(runtime.lease_until)
        if lease_until and lease_until > now and runtime.lease_owner not in {"", self._worker_id}:
            raise RuntimeError("runtime lease held by another worker")
        runtime.lease_owner = self._worker_id
        runtime.lease_until = now + timedelta(seconds=20)
        return runtime

    def run_cycle(self, now: datetime | None = None) -> int:
        from ..routers.day_trading_v2 import _scan_now

        current = now or datetime.now(UTC)
        local = current.astimezone(TAIPEI)
        with SessionLocal() as db:
            user_ids = list(db.scalars(select(DayTradeV2Setting.user_id).where(
                DayTradeV2Setting.trade_mode == "PAPER", DayTradeV2Setting.live_enabled.is_(False),
            ).limit(200)).all())
        processed = 0
        for user_id in user_ids:
            try:
                with SessionLocal() as db:
                    setting = db.get(DayTradeV2Setting, user_id)
                    if setting is None:
                        continue
                    config = merged_config(_json(setting.config_json, {}))
                    try:
                        self._refresh_calendar(db, local.year)
                    except Exception as exc:
                        logger.warning("TWSE calendar refresh failed; using cached/configured holidays: %s", exc)
                    holidays = _configured_holidays(db)
                    runtime = self._runtime(db, user_id, "PAPER", local.date(), config, current)
                    previous_heartbeat = _aware(runtime.heartbeat_at)
                    market_hours_now = at_time(local.date(), config["marketOpenTime"]) <= current < at_time(local.date(), config["marketCloseTime"])
                    if previous_heartbeat and market_hours_now and (current - previous_heartbeat).total_seconds() > int(config["heartbeatTimeoutSeconds"]):
                        runtime.order_allowed = False
                        runtime.latest_error = "系統心跳曾逾時，已重新啟動並重新檢查行情"
                        from ..routers.day_trading_v2 import _notification
                        _notification(
                            db, user_id=user_id, mode="PAPER",
                            event_id=f"heartbeat-interrupted:{user_id}:{local.date()}", event_type="SYSTEM_HEARTBEAT_INTERRUPTED",
                            title="系統心跳中斷警報", message="背景交易心跳超過允許時間；恢復後會先確認行情與風控，再允許新交易。",
                        )
                    if previous_heartbeat is None or (current - previous_heartbeat).total_seconds() >= int(config["heartbeatSeconds"]):
                        runtime.heartbeat_at = current
                    runtime.phase = trading_phase(current, config, holidays)
                    if not is_trading_day(local.date(), holidays):
                        runtime.status = "NON_TRADING_DAY"
                        runtime.scanning = runtime.order_allowed = runtime.receiving_quotes = False
                        db.commit()
                        processed += 1
                        continue
                    if current < at_time(local.date(), config["resetTime"]):
                        runtime.status = "WAITING"
                    elif runtime.auto_start and runtime.status == "WAITING":
                        runtime.status = "RUNNING"
                        runtime.started_at = current
                    completed = set(db.scalars(select(DayTradeV2ScheduleEvent.event_type).where(
                        DayTradeV2ScheduleEvent.user_id == user_id, DayTradeV2ScheduleEvent.mode == "PAPER",
                        DayTradeV2ScheduleEvent.trading_date == local.date(),
                    )).all())
                    for definition, scheduled in event_schedule(local.date(), config):
                        if scheduled > current or definition.event_type in completed:
                            continue
                        late = bool(definition.notification and runtime.started_at and scheduled < (_aware(runtime.started_at) or scheduled))
                        db.add(DayTradeV2ScheduleEvent(
                            user_id=user_id, mode="PAPER", trading_date=local.date(), event_type=definition.event_type,
                            scheduled_at=scheduled, status="SKIPPED_LATE" if late else "COMPLETED",
                            executed_at=current, payload_json="{}",
                        ))
                        self._execute_event(
                            db, user_id, runtime, definition.event_type, config, current,
                            emit_notification=not late,
                        )
                    future = next(((definition, scheduled) for definition, scheduled in event_schedule(local.date(), config) if scheduled > current), None)
                    runtime.next_event_type = future[0].event_type if future else ""
                    runtime.next_event_at = future[1] if future else None
                    active_market = at_time(local.date(), config["marketOpenTime"]) <= current < at_time(local.date(), config["marketCloseTime"])
                    can_scan = runtime.status in {"RUNNING", "PAUSED"} and active_market
                    monitor_positions = runtime.status in {"RUNNING", "PAUSED", "STOPPED", "EMERGENCY_STOP", "RISK_HALTED"} and active_market
                    should_scan = monitor_positions and (
                        runtime.last_scan_at is None
                        or (current - (_aware(runtime.last_scan_at) or current)).total_seconds() >= int(config["scanIntervalSeconds"])
                    )
                    runtime.scanning = can_scan
                    runtime.next_scan_at = current + timedelta(seconds=int(config["scanIntervalSeconds"])) if can_scan else None
                    db.commit()
                if should_scan:
                    with SessionLocal() as db:
                        _scan_now(user_id, db, coordinator_now=current)
                processed += 1
            except RuntimeError:
                continue
            except Exception as exc:
                logger.exception("day-trading-v2 cycle failed for user")
                with SessionLocal() as db:
                    runtime = db.scalar(select(DayTradeV2RuntimeState).where(
                        DayTradeV2RuntimeState.user_id == user_id, DayTradeV2RuntimeState.trading_date == local.date(),
                    ))
                    if runtime:
                        runtime.latest_error = f"{type(exc).__name__}: {str(exc)[:400]}"
                        runtime.order_allowed = False
                        db.commit()
        return processed

    def _execute_event(
        self, db, user_id: str, runtime: DayTradeV2RuntimeState, event_type: str,
        config: dict[str, object], now: datetime, *, emit_notification: bool = True,
    ) -> None:
        from ..routers.day_trading_v2 import _dashboard, _notification

        if event_type == "DAILY_RESET":
            runtime.initialized = True
            runtime.initialized_at = now
            runtime.scanned_stock_count = runtime.candidate_count = 0
            runtime.signal_count = runtime.order_count = runtime.skipped_count = runtime.completed_trade_count = 0
            runtime.latest_error = ""
        elif event_type == "UNIVERSE_LOADED":
            try:
                popular = asyncio.run(OfficialPopularStockProvider(include_financial=True).fetch())
                universe = tuple(popular[:300]) if popular else merge_momentum_stocks(())[0]
                day_trading_engine.set_stock_universe(universe)
                runtime.scanned_stock_count = len(universe)
            except Exception as exc:
                runtime.latest_error = f"股票池載入失敗：{str(exc)[:300]}"
                runtime.order_allowed = False
        elif event_type == "HISTORY_LOADED":
            # The shared market engine restores and warms today's quote history;
            # V2 consumes only its completed, verified one-minute bars.
            runtime.scanned_stock_count = max(runtime.scanned_stock_count, len(day_trading_engine.stock_universe_symbols))
        elif event_type == "HEALTH_CHECKED":
            regime = day_trading_engine.market_regime()
            if regime.get("dataStatus") != "normal":
                runtime.latest_error = "行情來源尚未就緒，開盤後會持續重試並禁止新交易"
                runtime.order_allowed = False
        elif event_type == "CANDIDATE_POOL_READY":
            runtime.candidate_count = sum(1 for item in day_trading_engine.signals() if item.get("direction") == "long")
        elif event_type == "MARKET_SCAN_STARTED" and runtime.auto_start and runtime.status in {"WAITING", "RUNNING"}:
            runtime.status = "RUNNING"
        elif event_type == "ENTRY_CLOSED":
            runtime.order_allowed = False
        elif event_type == "MARKET_SCAN_STOPPED":
            runtime.scanning = runtime.order_allowed = False
        elif event_type == "DAILY_REPORT":
            runtime.status = "COMPLETED"
            runtime.closed_at = now
        if not emit_notification or event_type not in {"PREOPEN_READY", "OPENING_RANGE_READY", "HOURLY_1000", "HOURLY_1100", "HOURLY_1200", "DAILY_REPORT"}:
            return
        dashboard = _dashboard(db, user_id)
        candidates = list(db.scalars(select(DayTradeV2CandidateState).where(
            DayTradeV2CandidateState.user_id == user_id, DayTradeV2CandidateState.trading_date == runtime.trading_date,
        ).order_by(DayTradeV2CandidateState.confidence.desc()).limit(10)).all())
        candidate_text = "、".join(f"{row.symbol} {row.confidence:.0f}分/{row.strategy_id or '比對中'}（{row.primary_reason or '觀察中'}）" for row in candidates) or "目前無候選股票"
        day_start = datetime.combine(runtime.trading_date, datetime.min.time(), TAIPEI).astimezone(UTC)
        day_end = day_start + timedelta(days=1)
        trades = list(db.scalars(select(DayTradeV2Trade).where(
            DayTradeV2Trade.user_id == user_id, DayTradeV2Trade.mode == "PAPER",
            DayTradeV2Trade.exit_fill_time >= day_start, DayTradeV2Trade.exit_fill_time < day_end,
        )).all())
        gross_pnl = sum((row.gross_pnl for row in trades), 0)
        costs = sum((row.buy_fee + row.sell_fee + row.transaction_tax + row.slippage + row.other_cost for row in trades), 0)
        robot_text = "、".join(
            f"{robot['name']} {robot['today']['netPnl']}元/{robot['today']['tradeCount']}筆"
            for robot in dashboard["robots"]
        )
        skip_text = "、".join(f"{item['reason']} {item['count']}次" for item in dashboard["skipReasons"][:5]) or "無"
        notification_type = event_type
        if event_type == "PREOPEN_READY":
            title = "【當沖機器人2｜08:55盤前準備完成】"
            enabled = "、".join(f"{robot['name']}:{'啟用' if robot['enabled'] else '停用'}" for robot in dashboard["robots"])
            message = f"系統：{runtime.status}｜機器人：{enabled}｜行情：TWSE MIS｜券商：未設定｜可用資金：{dashboard['availableCapital']}元｜最大日損：{config['dailyStopLoss']}元｜候選：{runtime.candidate_count}檔｜真實交易：不允許"
        elif event_type == "OPENING_RANGE_READY":
            title = "【當沖機器人2｜09:15開盤區間完成】"
            message = f"已掃描{runtime.scanned_stock_count}檔｜符合流動性候選{runtime.candidate_count}檔｜觀察前10名：{candidate_text}"
        elif event_type.startswith("HOURLY_"):
            title = f"【當沖機器人2｜{event_type[-4:-2]}:00盤中摘要】"
            positions = "、".join(f"{row['symbol']} {row['quantity']}股" for row in dashboard["positions"]) or "無"
            win_rate = f"{dashboard['today']['winRate']}%" if dashboard["today"]["tradeCount"] else "—"
            message = f"狀態：{runtime.status}｜訊號{runtime.signal_count}｜下單{runtime.order_count}｜完成交易{dashboard['today']['tradeCount']}｜持倉：{positions}｜勝率{win_rate}｜已實現{dashboard['realizedPnl']}元｜未實現{dashboard['unrealizedPnl']}元｜候選：{candidate_text}｜主要跳過：{skip_text}"
            notification_type = "HOURLY_SUMMARY"
        else:
            title = "【當沖機器人2｜13:40收盤報告】"
            win_rate = f"{dashboard['today']['winRate']}%" if dashboard["today"]["tradeCount"] else "—（無已完成交易）"
            message = f"掃描完成：是｜掃描{runtime.scanned_stock_count}檔｜觀察{runtime.candidate_count}｜正式訊號{runtime.signal_count}｜下單{runtime.order_count}｜成交{runtime.completed_trade_count}｜{dashboard['today']['winCount']}勝{dashboard['today']['lossCount']}敗｜勝率{win_rate}｜毛損益{gross_pnl}元｜成本{costs}元｜淨損益{dashboard['realizedPnl']}元｜機器人：{robot_text}｜主要未交易原因：{skip_text}｜異常：{runtime.latest_error or '無'}"
        _notification(db, user_id=user_id, mode="PAPER", event_id=f"schedule:{user_id}:{runtime.trading_date}:{event_type}", event_type=notification_type, title=title, message=message)

    async def dispatch_pending(self) -> int:
        if not gmail_notification_dispatcher.configured:
            return 0
        with SessionLocal() as db:
            rows = list(db.scalars(select(DayTradeV2Notification).where(
                DayTradeV2Notification.email_sent.is_(False), DayTradeV2Notification.event_type.in_(EMAIL_EVENT_TYPES),
            ).order_by(DayTradeV2Notification.created_at).limit(20)).all())
        delivered = 0
        for row in rows:
            with SessionLocal() as db:
                setting = db.get(DayTradeV2Setting, row.user_id)
                config = merged_config(_json(setting.config_json, {})) if setting else merged_config()
            enabled = bool(config["emailHourlySummary"]) if row.event_type == "HOURLY_SUMMARY" else bool(config["emailReady"]) if row.event_type == "PREOPEN_READY" else bool(config["emailOpeningRange"]) if row.event_type == "OPENING_RANGE_READY" else bool(config["emailCloseReport"]) if row.event_type == "DAILY_REPORT" else True
            if not enabled:
                sent = 1
            else:
                sent = await gmail_notification_dispatcher.dispatch(
                    event_type=f"day_trading_v2_{row.event_type.lower()}", action=row.event_type,
                    message=f"{row.title}\n\n{row.message}", dedupe_key=f"dtv2-email:{row.event_id}",
                    signal_id=row.event_id, channel_name="超強AI當沖系統",
                )
            with SessionLocal() as db:
                stored = db.get(DayTradeV2Notification, row.id)
                if stored:
                    stored.email_attempted_at = datetime.now(UTC)
                    if sent:
                        stored.email_sent = True
                        delivered += int(enabled)
                    db.commit()
        return delivered


day_trading_v2_notification_automation = DayTradingV2Coordinator()
