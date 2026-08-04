from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import (
    LineDeliveryLog,
    LineNotificationGroup,
    LineNotificationSettings,
)


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
OFFICIAL_ACCOUNT_NAME = "AI當沖機器人"
LINE_API_BASE = "https://api.line.me/v2/bot/message"
LINE_GROUP_DISCLAIMER = (
    "⚠️ 免責聲明：\n"
    "本訊息為演算法內部測試之【自動化數據產出】，僅供技術研究與程式調校之用。"
    "本站及發訊系統非屬投顧事業，本訊息「絕不構成」任何個股之買賣推介、操作勸誘或專業投資建議。"
    "金融市場具極高風險，群內成員請勿依此進行真實市場跟單。"
    "任何依此資訊所為之投資行為，均須【自行判斷並自負盈虧】，開發者不承擔任何直接或間接之法律責任。"
)
PERSONAL_STRATEGY_SIMULATION_NOTE = (
    "此為個人看盤策略的模擬練習紀錄，非真實交易，不構成投資建議。"
    "請勿跟單，盈虧自負。"
)


def verify_line_signature(raw_body: bytes, signature: str, channel_secret: str) -> bool:
    if not signature or not channel_secret:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def mask_group_id(group_id: str) -> str:
    if len(group_id) <= 10:
        return f"{group_id[:2]}***{group_id[-2:]}"
    return f"{group_id[:6]}••••••{group_id[-4:]}"


def _number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _time(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return "—"


def format_personal_strategy_simulation(
    *,
    stock_name: Any,
    symbol: Any,
    entry_min: Any,
    entry_max: Any | None,
    stop_loss: Any,
    target_1: Any,
    target_2: Any | None = None,
) -> str:
    entry = _number(entry_min)
    if entry_max is not None and _number(entry_max) != entry:
        entry = f"{entry}～{_number(entry_max)}"
    targets = _number(target_1)
    if target_2 is not None and _number(target_2) != targets:
        targets = f"{targets}、{_number(target_2)}"
    return (
        "【個人策略模擬測試】\n"
        f"標的：{str(stock_name).strip()} {str(symbol).strip()}\n"
        f"模擬進場點：{entry}\n"
        f"模擬停損/停利：{_number(stop_loss)} / {targets}\n"
        f"說明：{PERSONAL_STRATEGY_SIMULATION_NOTE}"
    )


def format_signal_message(signal: dict[str, Any]) -> str:
    return format_personal_strategy_simulation(
        stock_name=signal.get("stockName", "—"),
        symbol=signal.get("symbol", "—"),
        entry_min=signal.get("entryMin"),
        entry_max=signal.get("entryMax"),
        stop_loss=signal.get("stopLoss"),
        target_1=signal.get("target1"),
        target_2=signal.get("target2"),
    )


def format_position_message(event: dict[str, Any]) -> str:
    position = event.get("position") or {}
    short = position.get("direction") == "short"
    emergency = event.get("level") == "emergency"
    if emergency:
        title = "緊急回補" if short else "緊急出場"
    else:
        title = "空單通知" if short else "多單通知"
    return (
        f"【AI當沖機器人｜{title}】\n\n"
        f"股票：{position.get('symbol', '—')} {position.get('stockName', '')}\n"
        f"方向：{'空單' if short else '多單'}\n"
        f"指令：{event.get('action', '—')}\n"
        f"目前價格：{_number(event.get('price'))}\n"
        f"停損價：{_number(position.get('stopLoss'))}\n"
        f"原因：{event.get('reason', '—')}\n"
        f"通知時間：{_time(event.get('createdAt') or datetime.now(UTC).isoformat())}\n\n"
        f"{PERSONAL_STRATEGY_SIMULATION_NOTE}"
    )


@dataclass(frozen=True)
class LineNotificationEvent:
    event_type: str
    action: str
    message: str
    dedupe_key: str
    priority: int
    signal_id: str | None = None
    symbol: str | None = None
    cooldown_entry: bool = False


@dataclass(frozen=True)
class LineApiResult:
    success: bool
    attempts: int
    response_status: int | None
    error: str | None


class LineMessagingClient:
    def __init__(
        self,
        *,
        access_token_setting: str = "line_channel_access_token",
        channel_secret_setting: str = "line_channel_secret",
        enabled_setting: str = "line_notifications_enabled",
    ) -> None:
        self._settings = get_settings()
        self._access_token_setting = access_token_setting
        self._channel_secret_setting = channel_secret_setting
        self._enabled_setting = enabled_setting

    @property
    def configured(self) -> bool:
        return bool(
            getattr(self._settings, self._enabled_setting)
            and getattr(self._settings, self._access_token_setting)
            and getattr(self._settings, self._channel_secret_setting)
        )

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        max_attempts: int,
        *,
        retry_key: str | None = None,
    ) -> LineApiResult:
        token = str(getattr(self._settings, self._access_token_setting))
        if not token:
            return LineApiResult(False, 0, None, "LINE Channel Access Token 尚未設定")
        last_status: int | None = None
        last_error: str | None = None
        attempts = 0
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    if retry_key:
                        headers["X-Line-Retry-Key"] = retry_key
                    response = await client.post(
                        f"{LINE_API_BASE}/{path}",
                        headers=headers,
                        json=payload,
                    )
                last_status = response.status_code
                if 200 <= response.status_code < 300 or (retry_key and response.status_code == 409):
                    return LineApiResult(True, attempt, response.status_code, None)
                last_error = f"LINE API HTTP {response.status_code}"
                if response.status_code < 500:
                    break
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: LINE API 連線失敗"
            if attempt < max_attempts:
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
        logger.warning("LINE Messaging API request failed after %s attempts: %s", attempts, last_error)
        return LineApiResult(False, attempts, last_status, last_error)

    async def push_text(self, group_id: str, text: str) -> LineApiResult:
        return await self._post(
            "push",
            {"to": group_id, "messages": [{"type": "text", "text": text[:5000]}]},
            max_attempts=3,
            retry_key=str(uuid4()),
        )

    async def reply_text(self, reply_token: str, text: str) -> LineApiResult:
        return await self._post(
            "reply",
            {"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]},
            max_attempts=1,
        )


def get_line_notification_settings(db: Session) -> LineNotificationSettings:
    item = db.get(LineNotificationSettings, 1)
    if item is None:
        item = LineNotificationSettings(id=1, updated_at=datetime.now(UTC))
        db.add(item)
        db.commit()
        db.refresh(item)
    return item


def line_setting_allows(settings: LineNotificationSettings, event_type: str) -> bool:
    mapping = {
        "opening": settings.opening_enabled,
        "long_entry": settings.long_entry_enabled,
        "short_entry": settings.short_entry_enabled,
        "long_exit": settings.long_exit_enabled,
        "short_cover": settings.short_cover_enabled,
        "stop_loss": settings.stop_loss_enabled,
        "data_alert": settings.data_alert_enabled,
        "closing_summary": settings.closing_summary_enabled,
        "robot_stopped": settings.closing_summary_enabled,
        "test": True,
    }
    return mapping.get(event_type, True)


class LineNotificationDispatcher:
    def __init__(self) -> None:
        self.client = LineMessagingClient()
        self._lock = asyncio.Lock()
        self._queue: asyncio.PriorityQueue[
            tuple[int, int, LineNotificationEvent, asyncio.Future[int]]
        ] = asyncio.PriorityQueue()
        self._sequence = 0
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker(), name="line-notification-worker")

    async def stop(self) -> None:
        if not self._worker_task:
            return
        self._worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker_task
        self._worker_task = None

    async def _worker(self) -> None:
        while True:
            first = await self._queue.get()
            await asyncio.sleep(0.05)
            batch = [first]
            while not self._queue.empty():
                batch.append(self._queue.get_nowait())
            batch.sort(key=lambda item: (item[0], item[1]))
            for _, _, event, future in batch:
                try:
                    result = await self._dispatch(event)
                    if not future.done():
                        future.set_result(result)
                except Exception as exc:
                    logger.exception("LINE notification worker failed for event type %s", event.event_type)
                    if not future.done():
                        future.set_exception(exc)
                finally:
                    self._queue.task_done()

    def _active_group_ids(self, db: Session) -> list[str]:
        group_ids = list(db.scalars(
            select(LineNotificationGroup.group_id)
            .where(LineNotificationGroup.active.is_(True))
            .order_by(LineNotificationGroup.bound_at),
        ).all())
        fallback = get_settings().line_target_group_id
        if fallback and fallback not in group_ids:
            group_ids.append(fallback)
        return group_ids

    async def dispatch_many(self, events: list[LineNotificationEvent]) -> int:
        if not events:
            return 0
        if not self._worker_task or self._worker_task.done():
            sent = 0
            async with self._lock:
                for event in sorted(events, key=lambda value: value.priority):
                    sent += await self._dispatch(event)
            return sent
        loop = asyncio.get_running_loop()
        futures: list[asyncio.Future[int]] = []
        for event in events:
            self._sequence += 1
            future: asyncio.Future[int] = loop.create_future()
            futures.append(future)
            await self._queue.put((event.priority, self._sequence, event, future))
        return sum(await asyncio.gather(*futures))

    async def _dispatch(self, event: LineNotificationEvent) -> int:
        if not get_settings().line_notifications_enabled:
            return 0
        with SessionLocal() as db:
            notification_settings = get_line_notification_settings(db)
            if not line_setting_allows(notification_settings, event.event_type):
                return 0
            group_ids = self._active_group_ids(db)
        successful = 0
        for group_id in group_ids:
            with SessionLocal() as db:
                if event.cooldown_entry and event.symbol:
                    local_now = datetime.now(UTC).astimezone(TAIPEI)
                    cutoff = datetime.combine(local_now.date(), time.min, TAIPEI).astimezone(UTC)
                    recent = db.scalar(select(LineDeliveryLog.id).where(
                        LineDeliveryLog.group_id == group_id,
                        LineDeliveryLog.symbol == event.symbol,
                        LineDeliveryLog.event_type == event.event_type,
                        LineDeliveryLog.status.in_(["pending", "sent"]),
                        LineDeliveryLog.created_at >= cutoff,
                    ).limit(1))
                    if recent is not None:
                        continue
                log = LineDeliveryLog(
                    group_id=group_id,
                    event_type=event.event_type,
                    signal_id=event.signal_id,
                    symbol=event.symbol,
                    action=event.action,
                    priority=event.priority,
                    dedupe_key=event.dedupe_key,
                    status="pending",
                    attempts=0,
                    message_preview=event.message[:5000],
                    created_at=datetime.now(UTC),
                )
                db.add(log)
                try:
                    db.commit()
                    db.refresh(log)
                except IntegrityError:
                    db.rollback()
                    continue
            result = await self.client.push_text(group_id, event.message)
            with SessionLocal() as db:
                stored = db.get(LineDeliveryLog, log.id)
                if stored is None:
                    continue
                stored.attempts = result.attempts
                stored.response_status = result.response_status
                stored.error_message = result.error
                stored.status = "sent" if result.success else "failed"
                if result.success:
                    stored.sent_at = datetime.now(UTC)
                    group = db.scalar(select(LineNotificationGroup).where(
                        LineNotificationGroup.group_id == group_id,
                    ))
                    if group:
                        group.last_push_at = stored.sent_at
                    successful += 1
                db.commit()
        return successful

    async def send_recommendations(self, recommendations: list[dict[str, Any]]) -> int:
        events: list[LineNotificationEvent] = []
        seen_entries: set[tuple[str, str, str]] = set()
        for signal in recommendations[:5]:
            if not signal.get("isOfficialRecommendation"):
                continue
            action = str(signal.get("action", ""))
            if action.startswith("等待"):
                continue
            direction = str(signal.get("direction"))
            if direction == "long" and not any(value in action for value in ["突破買進", "回踩買進"]):
                continue
            if direction == "short" and not any(value in action for value in ["跌破", "反彈放空"]):
                continue
            event_type = "long_entry" if direction == "long" else "short_entry"
            signal_id = str(signal["id"])
            timestamp = str(signal.get("quoteTimestamp") or signal.get("generatedAt") or "")
            try:
                parsed_at = datetime.fromisoformat(timestamp)
                if parsed_at.tzinfo is None:
                    parsed_at = parsed_at.replace(tzinfo=UTC)
                trading_date = parsed_at.astimezone(TAIPEI).date().isoformat()
            except ValueError:
                trading_date = datetime.now(UTC).astimezone(TAIPEI).date().isoformat()
            symbol = str(signal["symbol"])
            entry_key = (symbol, direction, trading_date)
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)
            events.append(LineNotificationEvent(
                event_type=event_type,
                action=action,
                message=format_signal_message(signal),
                dedupe_key=f"formal-entry:{trading_date}:{symbol}:{direction}",
                priority=6,
                signal_id=signal_id,
                symbol=symbol,
                cooldown_entry=True,
            ))
        return await self.dispatch_many(events)

    async def send_confidence_candidates(
        self,
        candidates: list[dict[str, Any]],
        minimum_confidence: float = 75,
    ) -> int:
        # Candidate notifications are intentionally disabled. Only formal entry
        # recommendations and position risk/exit events may be pushed to LINE.
        del candidates, minimum_confidence
        return 0

    async def send_position_event(self, event: dict[str, Any]) -> int:
        position = event.get("position") or {}
        short = position.get("direction") == "short"
        action = str(event.get("action", ""))
        emergency = event.get("level") == "emergency"
        stop = "停損" in action or emergency
        event_type = "stop_loss" if stop else "short_cover" if short else "long_exit"
        position_id = position.get("id") or position.get("signalId") or position.get("symbol")
        notification = LineNotificationEvent(
            event_type=event_type,
            action=action,
            message=format_position_message(event),
            dedupe_key=f"position:{position_id}:{action}",
            priority=0 if emergency else 1,
            signal_id=str(position.get("signalId")) if position.get("signalId") else None,
            symbol=str(position.get("symbol")) if position.get("symbol") else None,
        )
        return await self.dispatch_many([notification])

    async def send_system_event(
        self,
        event_type: str,
        title: str,
        details: str,
        dedupe_key: str,
        priority: int = 3,
    ) -> int:
        message = (
            f"【AI當沖機器人｜{title}】\n\n"
            f"{details}\n"
            f"通知時間：{_time(datetime.now(UTC).isoformat())}\n\n"
            f"{PERSONAL_STRATEGY_SIMULATION_NOTE}"
        )
        return await self.dispatch_many([LineNotificationEvent(
            event_type=event_type,
            action=title,
            message=message,
            dedupe_key=dedupe_key,
            priority=priority,
        )])


line_notification_dispatcher = LineNotificationDispatcher()
