from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, time
import logging
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..config import get_settings
from ..database import BackgroundSessionLocal as SessionLocal
from ..strong_stock_models import (
    StrongStockDataRun, StrongStockNotification, StrongStockOrder, StrongStockPosition,
    StrongStockRanking, StrongStockSetting,
)
from .adaptive_electronic_automation import fetch_adaptive_scan_payload
from .day_trading_schedule import is_twse_trading_day
from .gmail_messaging import gmail_notification_dispatcher
from .official_market_data import StockQuoteRequest, official_market_data_provider
from .strong_stock import (
    fill_pending_orders, merged_config, monitor_positions, notify, queue_paper_orders, scan_and_persist,
    snapshot_equity, strategy_health,
)


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")


class StrongStockAutomation:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._last_intraday_minute: tuple[object, int] | None = None
        self._last_close_attempt_at: datetime | None = None
        self._state: dict[str, object] = {
            "status": "stopped", "lastRunAt": None, "lastSuccessAt": None,
            "lastError": None, "lastResult": None, "nextCheckSeconds": 60,
        }

    @property
    def state(self) -> dict[str, object]:
        return dict(self._state)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._state["status"] = "running"
        self._task = asyncio.create_task(self._run(), name="strong-stock-automation")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._state["status"] = "stopped"

    async def run_once(self, now: datetime | None = None, *, force: bool = False) -> dict[str, object]:
        current = now or datetime.now(UTC)
        local = current.astimezone(TAIPEI)
        with SessionLocal() as db:
            first_setting = db.scalar(select(StrongStockSetting).order_by(StrongStockSetting.updated_at.desc()).limit(1))
            try:
                schedule_config = merged_config(json.loads(first_setting.config_json)) if first_setting else merged_config()
            except (TypeError, ValueError):
                schedule_config = merged_config()
        ready_start = time.fromisoformat(str(schedule_config["dataReadyStartTime"]))
        ready_end = time.fromisoformat(str(schedule_config["dataReadyEndTime"]))
        holidays = set()
        for raw in get_settings().twse_holidays.split(","):
            try:
                holidays.add(datetime.fromisoformat(raw.strip()).date())
            except ValueError:
                continue
        self._state["lastRunAt"] = current.isoformat()
        result: dict[str, object]
        if not force and not is_twse_trading_day(local.date(), holidays):
            result = {"status": "waiting_trading_day"}
        elif force or ready_start <= local.time() <= ready_end:
            if not force:
                queued: dict[str, int] = {}
                with SessionLocal() as db:
                    completed = db.scalar(select(StrongStockDataRun.id).where(
                        StrongStockDataRun.trade_date == local.date(),
                        StrongStockDataRun.status == "COMPLETED",
                    ).limit(1))
                    if completed:
                        users = list(db.scalars(select(StrongStockSetting.user_id).where(StrongStockSetting.paper_enabled.is_(True))).all())
                        queued = {uid: queue_paper_orders(db, uid, local.date()) for uid in users}
                        for uid in users:
                            snapshot_equity(db, uid, local.date())
                        db.commit()
                if completed:
                    result = {"status": "already_completed", "tradeDate": local.date().isoformat(), "queued": queued}
                    self._state.update({"status": "running", "lastSuccessAt": datetime.now(UTC).isoformat(), "lastResult": result, "lastError": None})
                    await self._dispatch_email()
                    return result
                poll_minutes = max(1, int(str(schedule_config["dataReadyPollMinutes"])))
                if self._last_close_attempt_at and (current - self._last_close_attempt_at).total_seconds() < poll_minutes * 60:
                    result = {"status": "waiting_data_poll", "nextCheckMinutes": poll_minutes}
                    self._state.update({"status": "running", "lastResult": result, "lastError": None})
                    return result
                self._last_close_attempt_at = current
            payload = await fetch_adaptive_scan_payload(timeout_seconds=180)
            if not force and (payload.market.trade_date != local.date() or payload.market.market_open or len(payload.stocks) < 60):
                result = {"status": "waiting_complete_close_data", "payloadTradeDate": payload.market.trade_date.isoformat()}
            else:
                with SessionLocal() as db:
                    settings = list(db.scalars(select(StrongStockSetting).where(StrongStockSetting.paper_enabled.is_(True))).all())
                    users = [row.user_id for row in settings]
                    try:
                        scan_config = merged_config(json.loads(settings[0].config_json)) if settings else merged_config()
                    except (TypeError, ValueError):
                        scan_config = merged_config()
                    scan = scan_and_persist(db, payload, current, scan_config)
                    queued = {uid: queue_paper_orders(db, uid, payload.market.trade_date) for uid in users}
                    for uid in users:
                        snapshot_equity(db, uid, payload.market.trade_date)
                        strategy_health(db, uid, apply_guard=True)
                        notify(db, uid, f"daily-report:{payload.market.trade_date}", "DAILY_REPORT", "【強勢股策略｜每日收盤報告】", f"完成 {scan['ranked']} 檔股票評分，符合完整進場條件 {scan['entryReady']} 檔，隔日模擬委託 {queued[uid]} 筆。")
                    db.commit()
                result = {"status": "completed", **scan, "queued": queued}
                await self._dispatch_email()
        elif time(9, 0) <= local.time() <= time(13, 30):
            minute_key = (local.date(), local.hour * 60 + local.minute)
            if not force and self._last_intraday_minute == minute_key:
                result = {"status": "intraday_wait"}
            else:
                from .strong_stock import dec
                with SessionLocal() as db:
                    users = list(db.scalars(select(StrongStockSetting.user_id).where(StrongStockSetting.paper_enabled.is_(True))).all())
                    pending = list(db.scalars(select(StrongStockOrder).where(StrongStockOrder.status == "PENDING")).all())
                    positions = list(db.scalars(select(StrongStockPosition).where(StrongStockPosition.status == "OPEN")).all())
                    names = {row.symbol: row.name for row in [*pending, *positions]}
                    requests: list[StockQuoteRequest] = []
                    for symbol, name in names.items():
                        ranking = db.scalar(select(StrongStockRanking).where(
                            StrongStockRanking.symbol == symbol,
                        ).order_by(StrongStockRanking.trade_date.desc()).limit(1))
                        if ranking:
                            requests.append(StockQuoteRequest(symbol=symbol, name=name, market=ranking.market))
                quotes = await official_market_data_provider.get_quotes(requests, force_refresh=True) if requests else {}
                decimal_prices = {symbol: dec(quote.price) for symbol, quote in quotes.items() if quote.is_realtime}
                with SessionLocal() as db:
                    fills = {uid: fill_pending_orders(db, uid, decimal_prices, local) for uid in users}
                    exits = {uid: monitor_positions(db, uid, decimal_prices, local) for uid in users}
                self._last_intraday_minute = minute_key
                result = {"status": "intraday_monitor", "users": len(users), "fills": fills, "exits": exits}
        else:
            result = {"status": "waiting_schedule", "next": "09:00持倉監控／14:30盤後資料檢查"}
        self._state.update({"status": "running", "lastSuccessAt": datetime.now(UTC).isoformat(), "lastResult": result, "lastError": None})
        return result

    async def _dispatch_email(self) -> None:
        if not gmail_notification_dispatcher.configured:
            return
        with SessionLocal() as db:
            rows = list(db.scalars(select(StrongStockNotification).where(StrongStockNotification.email_sent.is_(False)).order_by(StrongStockNotification.created_at).limit(20)).all())
        for row in rows:
            sent = await gmail_notification_dispatcher.dispatch(
                event_type=f"strong_stock_{row.event_type.lower()}", action=row.title,
                message=row.message, dedupe_key=f"strong-stock-email:{row.event_key}",
                signal_id=row.event_key, symbol="", channel_name="強勢股策略",
            )
            if sent:
                with SessionLocal() as db:
                    stored = db.get(StrongStockNotification, row.id)
                    if stored:
                        stored.email_sent = True; db.commit()

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Strong stock automation failed")
                self._state.update({"status": "error", "lastError": str(exc)[:500]})
                try:
                    with SessionLocal() as db:
                        db.add(StrongStockDataRun(
                            id=str(uuid4()), trade_date=datetime.now(TAIPEI).date(), status="FAILED",
                            error_message=str(exc)[:500], source_json="{}", missing_json="[]",
                            completed_at=datetime.now(UTC),
                        ))
                        db.commit()
                except Exception:
                    logger.exception("Unable to persist strong-stock scheduler failure")
            await asyncio.sleep(60)


strong_stock_automation = StrongStockAutomation()
