from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, date, datetime, time
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..config import get_settings
from ..database import BackgroundSessionLocal as SessionLocal
from ..models import AIStockAlert, AIStockMonitor, AIStockPosition, PatternRobotRun, PatternRobotSetting
from .ai_stock_line import (
    add_on_message,
    daily_position_summary_message,
    initial_entry_message,
    position_action_message,
    push_ai_stock_message,
)
from .ai_stock_market_scanner import AIStockMarketScanner, MarketScanResult
from .ai_stock_service import (
    ACTIVE_MONITOR_STATUSES,
    ACTIVE_POSITION_STATUSES,
    create_alert,
    decimal_value,
    get_portfolio_settings,
    monitor_entry_failures,
    monitor_payload,
    position_payload,
    quote_is_fresh,
    suggest_add_on,
    sync_recommendations,
    update_position_quote,
)
from .day_trading_schedule import is_twse_trading_day
from .official_market_data import StockQuoteRequest, official_market_data_provider


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")


def _market_session(now: datetime) -> str:
    local = now.astimezone(TAIPEI)
    holidays: set[date] = set()
    for raw in get_settings().twse_holidays.split(","):
        try:
            holidays.add(date.fromisoformat(raw.strip()))
        except ValueError:
            continue
    if not is_twse_trading_day(local.date(), holidays):
        return "closed"
    if time(9, 0) <= local.time() <= time(13, 30):
        return "open"
    if local.time() > time(13, 30):
        return "after_close"
    return "pre_open"


class AIStockAutomation:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._state = self._initial_state("stopped", 0)

    @staticmethod
    def _initial_state(status: str, restored: int) -> dict:
        return {
            "status": status,
            "lastRunAt": None,
            "restoredPositions": restored,
            "scanIntervalSeconds": max(10, get_settings().ai_stock_monitor_seconds),
            "lastScanAt": None,
            "lastScanStatus": "not_started",
            "lastScanError": None,
            "lastScanFeaturedCount": 0,
            "lastScanCandidateCount": 0,
            "lastSyncedCount": 0,
        }

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        with SessionLocal() as db:
            restored = len(db.scalars(select(AIStockPosition.id).where(
                AIStockPosition.position_status.in_(ACTIVE_POSITION_STATUSES),
            )).all())
        self._state = self._initial_state("running", restored)
        self._task = asyncio.create_task(self._run(), name="ai-stock-cross-day-monitor")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._state["status"] = "stopped"

    @property
    def state(self) -> dict:
        return dict(self._state)

    async def _scan_market(self, current: datetime) -> MarketScanResult:
        settings = get_settings()
        scanner = AIStockMarketScanner(
            settings.ai_stock_scanner_url,
            timeout_seconds=settings.ai_stock_scanner_timeout_seconds,
            service_token=settings.adaptive_electronic_scanner_token,
        )
        result = await scanner.scan(current)
        synced_count = 0
        if result.status == "success":
            with SessionLocal() as db:
                synced_count = len(sync_recommendations(
                    db,
                    settings.ai_stock_automation_user_id,
                    list(result.items),
                    now=current,
                ))
        self._state.update({
            "lastScanAt": result.fetched_at.isoformat(),
            "lastScanStatus": result.status,
            "lastScanError": result.error,
            "lastScanFeaturedCount": result.featured_count,
            "lastScanCandidateCount": len(result.items),
            "lastSyncedCount": synced_count,
        })
        return result

    async def _push_alert(
        self,
        alert_id: int,
        *,
        event_type: str,
        action: str,
        message: str,
        signal_id: str,
        symbol: str,
        priority: int,
    ) -> None:
        try:
            sent = await push_ai_stock_message(
                event_type=event_type, action=action, message=message,
                signal_id=signal_id, symbol=symbol, priority=priority,
            )
            with SessionLocal() as db:
                alert = db.get(AIStockAlert, alert_id)
                if alert:
                    alert.line_push_status = "sent" if sent else "deduplicated_or_disabled"
                    alert.sent_at = datetime.now(UTC) if sent else None
                    db.commit()
        except Exception:
            logger.exception("AI stock LINE push failed for %s", signal_id)
            with SessionLocal() as db:
                alert = db.get(AIStockAlert, alert_id)
                if alert:
                    alert.line_push_status = "failed"
                    db.commit()

    async def run_once(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        session = _market_session(current)
        if session == "open":
            local_date = current.astimezone(TAIPEI).date()
            with SessionLocal() as db:
                pattern_setting = db.get(PatternRobotSetting, 1)
                pattern_run = db.scalar(select(PatternRobotRun).where(
                    PatternRobotRun.trade_date == local_date,
                    PatternRobotRun.run_type == "OPEN_SCAN",
                ))
            # Opening order: pattern scan/reminder first, then the original AI
            # selection scan. A FAILED pattern run is recorded but never blocks
            # the original robot permanently; its own retry loop remains active.
            if pattern_setting and pattern_setting.enabled and (
                pattern_run is None or pattern_run.status == "RUNNING"
            ):
                self._state.update({
                    "lastScanAt": current.isoformat(),
                    "lastScanStatus": "waiting_pattern_scan",
                    "lastScanError": None,
                    "lastScanFeaturedCount": 0,
                    "lastScanCandidateCount": 0,
                    "lastSyncedCount": 0,
                })
            else:
                await self._scan_market(current)
        else:
            self._state.update({
                "lastScanAt": current.isoformat(),
                "lastScanStatus": f"skipped_{session}",
                "lastScanError": None,
                "lastScanFeaturedCount": 0,
                "lastScanCandidateCount": 0,
                "lastSyncedCount": 0,
            })
        with SessionLocal() as db:
            monitors = list(db.scalars(select(AIStockMonitor).where(
                AIStockMonitor.monitor_status.in_(ACTIVE_MONITOR_STATUSES),
            )).all())
            positions = list(db.scalars(select(AIStockPosition).where(
                AIStockPosition.position_status.in_(ACTIVE_POSITION_STATUSES),
            )).all())
            monitor_map = {item.id: item for item in db.scalars(select(AIStockMonitor)).all()}

        if session != "open":
            if session == "after_close":
                close_requests = [
                    StockQuoteRequest(
                        position.symbol,
                        position.stock_name,
                        monitor_map[position.monitor_id].market
                        if position.monitor_id in monitor_map else "上市",
                    )
                    for position in positions
                ]
                quotes = await official_market_data_provider.get_quotes(close_requests)
                for seed in positions:
                    with SessionLocal() as db:
                        position = db.get(AIStockPosition, seed.id)
                        if position is None or position.position_status not in ACTIVE_POSITION_STATUSES:
                            continue
                        monitor = db.get(AIStockMonitor, position.monitor_id)
                        quote = quotes.get(position.symbol)
                        if quote is not None:
                            quote_time = datetime.fromisoformat(quote.quote_timestamp)
                            update_position_quote(
                                position,
                                decimal_value(quote.price),
                                quote_valid=False,
                                now=current,
                            )
                            if monitor:
                                monitor.quote_source = quote.source
                                monitor.quote_timestamp = quote_time
                                monitor.current_price = decimal_value(quote.price)
                                monitor.updated_at = current
                        position.overnight_status = True
                        position.position_status = "overnight"
                        position.latest_action = "隔夜持有"
                        position.updated_at = current
                        db.commit()
                        settings = get_portfolio_settings(db, position.user_id)
                        if not settings.daily_summary_enabled:
                            continue
                        local_date = current.astimezone(TAIPEI).date().isoformat()
                        signal_id = f"position-{position.id}-daily-summary-{local_date}"
                        alert = create_alert(
                            db,
                            user_id=position.user_id,
                            monitor_id=position.monitor_id,
                            position_id=position.id,
                            signal_id=signal_id,
                            alert_type="daily_summary",
                            alert_level="general",
                            action="每日持倉摘要",
                            current_price=decimal_value(position.current_price),
                            reasons=["收盤後持倉仍未結束，隔日將自動恢復監控"],
                        )
                        if alert:
                            await self._push_alert(
                                alert.id,
                                event_type="closing_summary",
                                action="每日持倉摘要",
                                message=daily_position_summary_message(
                                    position_payload(position, monitor)
                                ),
                                signal_id=signal_id,
                                symbol=position.symbol,
                                priority=8,
                            )
            self._state["lastRunAt"] = current.isoformat()
            return

        requests: dict[str, StockQuoteRequest] = {}
        for monitor in monitors:
            requests[monitor.symbol] = StockQuoteRequest(monitor.symbol, monitor.stock_name, monitor.market)
        for position in positions:
            monitor = monitor_map.get(position.monitor_id)
            requests[position.symbol] = StockQuoteRequest(
                position.symbol, position.stock_name, monitor.market if monitor else "上市",
            )
        quotes = await official_market_data_provider.get_quotes(list(requests.values()))

        # Existing positions always run first: stop-loss and exits outrank add-ons and new entries.
        for position_seed in positions:
            quote = quotes.get(position_seed.symbol)
            with SessionLocal() as db:
                position = db.get(AIStockPosition, position_seed.id)
                if position is None or position.position_status not in ACTIVE_POSITION_STATUSES:
                    continue
                monitor = db.get(AIStockMonitor, position.monitor_id)
                quote_valid = False
                if quote is not None:
                    quote_time = datetime.fromisoformat(quote.quote_timestamp)
                    quote_valid = quote.is_realtime and quote_is_fresh(quote_time, current)
                    current_price = decimal_value(quote.price)
                    if monitor:
                        monitor.quote_source = quote.source
                        monitor.quote_timestamp = quote_time
                        monitor.current_price = current_price
                        monitor.updated_at = current
                else:
                    current_price = decimal_value(position.current_price)
                previous_action = position.latest_action
                if quote_valid and position.overnight_status:
                    position.overnight_status = False
                action, reasons = update_position_quote(
                    position, current_price, quote_valid=quote_valid, now=current,
                )
                db.commit()
                payload = position_payload(position, monitor)
                if action in {"立即停損", "建議全部賣出", "建議減碼 50%"} and action != previous_action:
                    priority = 0 if action == "立即停損" else 1 if action == "建議全部賣出" else 2
                    signal_id = f"position-{position.id}-{current.astimezone(TAIPEI).date().isoformat()}-{action}"
                    alert = create_alert(
                        db, user_id=position.user_id, monitor_id=position.monitor_id,
                        position_id=position.id, signal_id=signal_id,
                        alert_type="stop_loss" if action == "立即停損" else "exit",
                        alert_level="emergency" if action == "立即停損" else "important",
                        action=action, current_price=current_price, reasons=reasons,
                    )
                    if alert and position.line_exit_notifications:
                        await self._push_alert(
                            alert.id,
                            event_type="stop_loss" if action == "立即停損" else "long_exit",
                            action=action, message=position_action_message(payload, action, reasons),
                            signal_id=signal_id, symbol=position.symbol, priority=priority,
                        )
                elif action == "資料異常" and action != previous_action:
                    local_hour = current.astimezone(TAIPEI).strftime("%Y-%m-%d-%H")
                    signal_id = f"position-{position.id}-data-abnormal-{local_hour}"
                    alert = create_alert(
                        db,
                        user_id=position.user_id,
                        monitor_id=position.monitor_id,
                        position_id=position.id,
                        signal_id=signal_id,
                        alert_type="data_alert",
                        alert_level="important",
                        action="行情資料異常",
                        current_price=current_price,
                        reasons=reasons,
                    )
                    if alert and position.line_exit_notifications:
                        await self._push_alert(
                            alert.id,
                            event_type="data_alert",
                            action="行情資料異常",
                            message=position_action_message(payload, "行情資料異常", reasons),
                            signal_id=signal_id,
                            symbol=position.symbol,
                            priority=3,
                        )
        # Add-on checks are a separate second pass, after every position's exit check.
        for position_seed in positions:
            quote = quotes.get(position_seed.symbol)
            if quote is None:
                continue
            quote_time = datetime.fromisoformat(quote.quote_timestamp)
            if not quote.is_realtime or not quote_is_fresh(quote_time, current):
                continue
            with SessionLocal() as db:
                position = db.get(AIStockPosition, position_seed.id)
                if (
                    position is None
                    or position.position_status not in ACTIVE_POSITION_STATUSES
                    or position.latest_action != "續抱"
                ):
                    continue
                monitor = db.get(AIStockMonitor, position.monitor_id)
                settings = get_portfolio_settings(db, position.user_id)
                add_on = suggest_add_on(db, position, settings)
                if add_on is None:
                    continue
                add_on_data = {
                    "addOnNumber": add_on.add_on_number,
                    "suggestedPercentage": float(add_on.suggested_percentage),
                    "suggestedAmount": float(add_on.suggested_amount),
                    "suggestedQuantity": add_on.suggested_quantity,
                    "suggestedPriceMin": float(add_on.suggested_price_min),
                    "suggestedPriceMax": float(add_on.suggested_price_max),
                    "newStopLoss": float(add_on.new_stop_loss),
                }
                alert = create_alert(
                    db,
                    user_id=position.user_id,
                    monitor_id=position.monitor_id,
                    position_id=position.id,
                    signal_id=add_on.signal_id,
                    alert_type="add_on",
                    alert_level="confirmation",
                    action=f"第{add_on.add_on_number}次加碼確認",
                    current_price=decimal_value(position.current_price),
                    reasons=["順勢突破確認", "健康度與部位風險合格"],
                )
                if alert:
                    await self._push_alert(
                        alert.id,
                        event_type="ai_add_on",
                        action=f"第{add_on.add_on_number}次加碼確認",
                        message=add_on_message(position_payload(position, monitor), add_on_data),
                        signal_id=add_on.signal_id,
                        symbol=position.symbol,
                        priority=5,
                    )

        # New entry checks run only after every open position has completed its exit checks.
        for monitor_seed in monitors:
            quote = quotes.get(monitor_seed.symbol)
            with SessionLocal() as db:
                monitor = db.get(AIStockMonitor, monitor_seed.id)
                if monitor is None or monitor.monitor_status not in ACTIVE_MONITOR_STATUSES:
                    continue
                if monitor.expired_at <= current:
                    monitor.monitor_status = "expired"
                    monitor.updated_at = current
                    db.commit()
                    continue
                if quote is None:
                    monitor.monitor_status = "data_abnormal"
                    monitor.updated_at = current
                    db.commit()
                    continue
                quote_time = datetime.fromisoformat(quote.quote_timestamp)
                current_price = decimal_value(quote.price)
                monitor.current_price = current_price
                monitor.quote_timestamp = quote_time
                monitor.quote_source = quote.source
                monitor.updated_at = current
                was_status = monitor.monitor_status
                failures = monitor_entry_failures(monitor, quote, current)
                if not failures:
                    monitor.monitor_status = "buy_confirmed"
                elif current_price > decimal_value(monitor.entry_max):
                    monitor.monitor_status = "chase_blocked"
                elif current_price < decimal_value(monitor.entry_min) and all(
                    reason == "現價尚未進入建議進場區" for reason in failures
                ):
                    monitor.monitor_status = "waiting_breakout"
                elif any(
                    key in reason
                    for reason in failures
                    for key in ["行情", "買賣價差", "成交量不足", "成交金額不足"]
                ):
                    monitor.monitor_status = "data_abnormal"
                else:
                    monitor.monitor_status = "signal_weakened"
                db.commit()
                if monitor.monitor_status == "buy_confirmed" and was_status != "buy_confirmed":
                    payload = monitor_payload(monitor)
                    alert = create_alert(
                        db,
                        user_id=monitor.user_id,
                        monitor_id=monitor.id,
                        position_id=None,
                        signal_id=monitor.signal_id,
                        alert_type="initial_entry",
                        alert_level="confirmation",
                        action="初始買進確認",
                        current_price=current_price,
                        reasons=json_list(monitor.reasons_json),
                    )
                    if alert:
                        await self._push_alert(
                            alert.id,
                            event_type="ai_initial_entry",
                            action="初始買進確認",
                            message=initial_entry_message(payload),
                            signal_id=monitor.signal_id,
                            symbol=monitor.symbol,
                            priority=6,
                        )

        self._state["lastRunAt"] = current.isoformat()

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("AI stock automation cycle failed")
            await asyncio.sleep(max(10, get_settings().ai_stock_monitor_seconds))


def json_list(value: str) -> list[str]:
    try:
        parsed = __import__("json").loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except Exception:
        return []


ai_stock_automation = AIStockAutomation()
