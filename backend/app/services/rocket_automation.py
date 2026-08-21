from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, time
import logging
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select

from ..adaptive_schemas import AdaptiveScanPayload
from ..config import get_settings
from ..database import BackgroundSessionLocal as SessionLocal
from ..models import RocketNotification
from .day_trading_schedule import is_twse_trading_day
from .gmail_messaging import gmail_notification_dispatcher
from .rocket_service import process_rocket_scan


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")


def _buy_email_message(notification: RocketNotification) -> str:
    quantity = notification.quantity or 0
    lots = quantity / 1_000
    quantity_text = f"{quantity:,} 股（{lots:g} 張）" if quantity else "未提供"
    amount_text = f"NT${float(notification.amount):,.0f}" if notification.amount is not None else "未提供"
    return (
        "【飆股雷達｜正式買進】\n\n"
        f"股票：{notification.stock_code or '-'} {notification.stock_name or ''}\n"
        f"買進明細：{notification.message}\n"
        f"數量：{quantity_text}\n"
        f"預估金額：{amount_text}\n"
        f"進場理由：{notification.reason}\n\n"
        "此為系統模擬交易訊號，不代表投資建議。"
    )


async def _dispatch_buy_emails(notifications: list[RocketNotification]) -> int:
    sent = 0
    for notification in notifications:
        try:
            sent += await gmail_notification_dispatcher.dispatch(
                event_type="rocket_buy",
                action="正式買進",
                message=_buy_email_message(notification),
                dedupe_key=f"rocket:{notification.dedupe_key}",
                signal_id=str(notification.id),
                symbol=notification.stock_code,
                channel_name="飆股雷達",
            )
        except Exception:
            logger.exception("Rocket radar Gmail notification failed for %s", notification.dedupe_key)
    return sent


def _scanner_url() -> str:
    settings = get_settings()
    configured = settings.rocket_radar_scanner_url.strip()
    if configured:
        return configured
    adaptive = settings.adaptive_electronic_scanner_url.strip()
    return adaptive.replace("/api/adaptive-electronic/scan", "/api/rocket-radar/scan")


async def fetch_rocket_scan_payload() -> AdaptiveScanPayload:
    settings = get_settings()
    url = _scanner_url()
    if not url or urlparse(url).scheme not in {"http", "https"}:
        raise RuntimeError("ROCKET_RADAR_SCANNER_URL 尚未正確設定")
    headers = {"Accept": "application/json", "User-Agent": "TWSE-Rocket-Radar/1.0"}
    if settings.adaptive_electronic_scanner_token:
        headers["X-Adaptive-Scanner-Token"] = settings.adaptive_electronic_scanner_token
    async with httpx.AsyncClient(
        timeout=settings.rocket_radar_scanner_timeout_seconds, follow_redirects=True,
    ) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return AdaptiveScanPayload.model_validate(response.json())


class RocketRadarAutomation:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._state: dict[str, object] = {
            "status": "stopped", "lastRunAt": None, "lastSuccessAt": None,
            "lastResult": None, "lastError": None, "lineNotifications": False,
        }

    @property
    def state(self) -> dict[str, object]:
        return dict(self._state)

    async def start(self) -> None:
        if self._task and not self._task.done(): return
        self._state["status"] = "running"
        self._task = asyncio.create_task(self._run(), name="rocket-radar-automation")

    async def stop(self) -> None:
        if not self._task: return
        self._task.cancel()
        with suppress(asyncio.CancelledError): await self._task
        self._task = None
        self._state["status"] = "stopped"

    async def run_once(self, now: datetime | None = None, *, force: bool = False) -> dict[str, object]:
        current = now or datetime.now(UTC)
        local = current.astimezone(TAIPEI)
        settings = get_settings()
        self._state["lastRunAt"] = current.isoformat()
        if not settings.rocket_radar_enabled:
            result: dict[str, object] = {"status": "disabled"}
        elif not force and not is_twse_trading_day(local.date()):
            result = {"status": "skipped_non_trading_day"}
        elif not force and not (time(8, 50) <= local.time() <= time(14, 10)):
            result = {"status": "waiting_market_session"}
        else:
            payload = await fetch_rocket_scan_payload()
            if not force and payload.market.trade_date != local.date():
                result = {"status": "waiting_current_quotes", "payloadTradeDate": payload.market.trade_date.isoformat()}
            else:
                with SessionLocal() as db:
                    last_notification_id = db.scalar(select(func.max(RocketNotification.id))) or 0
                    result = process_rocket_scan(db, payload)
                    buy_notifications = list(db.scalars(select(RocketNotification).where(
                        RocketNotification.id > last_notification_id,
                        RocketNotification.notification_type == "BUY",
                    ).order_by(RocketNotification.id)).all())
                result["gmailNotifications"] = await _dispatch_buy_emails(buy_notifications)
                self._state["lastSuccessAt"] = datetime.now(UTC).isoformat()
        self._state.update({"status": "running", "lastResult": result, "lastError": None})
        return result

    async def _run(self) -> None:
        interval = max(10, get_settings().rocket_radar_scan_interval_seconds)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Rocket radar scan failed")
                self._state.update({"status": "error", "lastError": str(error)[:500]})
            await asyncio.sleep(interval)


rocket_radar_automation = RocketRadarAutomation()
