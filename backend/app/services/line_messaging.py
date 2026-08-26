from __future__ import annotations

import asyncio
import base64
import math
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
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import (
    LineDeliveryLog,
    LineNotificationGroup,
    LineNotificationSettings,
)
from .day_trading_schedule import (
    DAY_TRADING_ENTRY_CUTOFF,
    DAY_TRADING_FORCED_EXIT,
    DAY_TRADING_SIGNAL_START,
)
from .gmail_messaging import gmail_notification_dispatcher


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
OFFICIAL_ACCOUNT_NAME = "AI當沖機器人"
LINE_API_BASE = "https://api.line.me/v2/bot/message"
TRADE_NOTIFICATION_EVENT_TYPES = frozenset({
    "long_entry",
    "short_entry",
    "long_exit",
    "short_cover",
    "stop_loss",
})
LINE_RECOMMENDATION_BATCH_LIMIT = 10
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


def format_signal_message(
    signal: dict[str, Any],
    *,
    include_session_status: bool = False,
) -> str:
    signal_message = format_personal_strategy_simulation(
        stock_name=signal.get("stockName", "—"),
        symbol=signal.get("symbol", "—"),
        entry_min=signal.get("entryMin"),
        entry_max=signal.get("entryMax"),
        stop_loss=signal.get("stopLoss"),
        target_1=signal.get("target1"),
        target_2=signal.get("target2"),
    )
    if not include_session_status:
        return signal_message
    return (
        "【AI當沖機器人｜今日首次進場】\n"
        f"啟動：{DAY_TRADING_SIGNAL_START} 正式訊號掃描已啟動\n"
        f"結束：{DAY_TRADING_ENTRY_CUTOFF} 停止新進場，"
        f"{DAY_TRADING_FORCED_EXIT} 完成當沖部位處理\n\n"
        f"{signal_message}"
    )


def format_position_message(event: dict[str, Any]) -> str:
    position = event.get("position") or {}
    strategy_label = str(position.get("automationStrategyLabel") or "模擬策略")
    short = position.get("direction") == "short"
    emergency = event.get("level") == "emergency"
    if emergency:
        title = "緊急回補" if short else "緊急出場"
    else:
        title = "空單通知" if short else "多單通知"
    return (
        f"【AI當沖機器人｜{title}】\n\n"
        f"策略帳本：{strategy_label}\n"
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


def effective_daily_trade_message_limit(
    *,
    monthly_limit: int | None,
    monthly_usage: int | None,
    remaining_trading_days: int,
    base_limit: int,
    minimum_daily_limit: int = 6,
) -> int:
    fallback = max(0, base_limit)
    if monthly_limit is None or monthly_usage is None:
        return fallback
    remaining = max(0, monthly_limit - monthly_usage)
    if remaining == 0:
        return 0
    days = max(1, remaining_trading_days)
    # Keep a practical daily budget for entries and exits while the official
    # monthly balance can support it. The LINE balance remains authoritative,
    # so the final days of an already exhausted month may be below the floor.
    fair_share = math.ceil(remaining / days)
    return min(remaining, max(max(0, minimum_daily_limit), fair_share))


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
            retry_after = 0.0
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
                try:
                    detail = str(response.json().get("message", "")).strip()
                except (TypeError, ValueError):
                    detail = ""
                last_error = f"LINE API HTTP {response.status_code}"
                if detail:
                    last_error = f"{last_error}: {detail}"
                if response.status_code == 429:
                    try:
                        retry_after = float(response.headers.get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        retry_after = 0.0
                # 429 can be a short-lived Messaging API rate limit. Retry it
                # with the same retry key; a hard monthly quota will still fail
                # cleanly after the bounded attempts.
                if response.status_code < 500 and response.status_code != 429:
                    break
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: LINE API 連線失敗"
            if attempt < max_attempts:
                await asyncio.sleep(min(5.0, max(retry_after, 0.25 * (2 ** (attempt - 1)))))
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

    async def message_quota(self) -> tuple[int, int] | None:
        token = str(getattr(self._settings, self._access_token_setting))
        if not token:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                quota_response, usage_response = await asyncio.gather(
                    client.get(f"{LINE_API_BASE}/quota", headers=headers),
                    client.get(f"{LINE_API_BASE}/quota/consumption", headers=headers),
                )
            if quota_response.status_code != 200 or usage_response.status_code != 200:
                return None
            quota = quota_response.json()
            usage = usage_response.json()
            if quota.get("type") != "limited" or quota.get("value") is None:
                return None
            return int(quota["value"]), int(usage.get("totalUsage", 0))
        except (httpx.HTTPError, TypeError, ValueError):
            logger.exception("Unable to read LINE monthly message quota")
            return None


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


def daily_trade_message_count(
    db: Session,
    *,
    now: datetime | None = None,
    statuses: tuple[str, ...] = ("pending", "sent"),
) -> int:
    local_now = (now or datetime.now(UTC)).astimezone(TAIPEI)
    cutoff = datetime.combine(local_now.date(), time.min, TAIPEI).astimezone(UTC)
    return int(db.scalar(
        select(func.count())
        .select_from(LineDeliveryLog)
        .where(
            LineDeliveryLog.event_type.in_(TRADE_NOTIFICATION_EVENT_TYPES),
            LineDeliveryLog.status.in_(statuses),
            LineDeliveryLog.created_at >= cutoff,
        )
    ) or 0)


class LineNotificationDispatcher:
    def __init__(self) -> None:
        self.client = LineMessagingClient()
        self._lock = asyncio.Lock()
        self._queue: asyncio.PriorityQueue[
            tuple[int, int, LineNotificationEvent, asyncio.Future[int]]
        ] = asyncio.PriorityQueue()
        self._sequence = 0
        self._worker_task: asyncio.Task[None] | None = None
        self._quota_checked_at: datetime | None = None
        self._quota_snapshot: dict[str, Any] | None = None
        self._quota_lock = asyncio.Lock()

    @staticmethod
    def _remaining_trading_days(now: datetime) -> int:
        local_day = now.astimezone(TAIPEI).date()
        if local_day.month == 12:
            next_month = local_day.replace(year=local_day.year + 1, month=1, day=1)
        else:
            next_month = local_day.replace(month=local_day.month + 1, day=1)
        holidays = {
            value.strip()
            for value in get_settings().twse_holidays.split(",")
            if value.strip()
        }
        cursor = local_day
        remaining = 0
        while cursor < next_month:
            if cursor.weekday() < 5 and cursor.isoformat() not in holidays:
                remaining += 1
            cursor += timedelta(days=1)
        return max(1, remaining)

    async def quota_status(self, *, force: bool = False) -> dict[str, Any]:
        now = datetime.now(UTC)
        if (
            not force
            and self._quota_snapshot is not None
            and self._quota_checked_at is not None
            and now - self._quota_checked_at < timedelta(minutes=5)
        ):
            return self._quota_snapshot
        async with self._quota_lock:
            # Another request may have refreshed the snapshot while this one
            # waited for the lock. Recheck to avoid serial LINE API calls from
            # a burst of dashboard tabs.
            now = datetime.now(UTC)
            if (
                not force
                and self._quota_snapshot is not None
                and self._quota_checked_at is not None
                and now - self._quota_checked_at < timedelta(minutes=5)
            ):
                return self._quota_snapshot
            quota = await self.client.message_quota()
            base_limit = max(0, get_settings().line_daily_trade_message_limit)
            remaining_days = self._remaining_trading_days(now)
            monthly_limit = quota[0] if quota else None
            monthly_usage = quota[1] if quota else None
            monthly_remaining = (
                max(0, monthly_limit - monthly_usage)
                if monthly_limit is not None and monthly_usage is not None
                else None
            )
            effective_limit = effective_daily_trade_message_limit(
                monthly_limit=monthly_limit,
                monthly_usage=monthly_usage,
                remaining_trading_days=remaining_days,
                base_limit=base_limit,
                minimum_daily_limit=get_settings().line_min_daily_trade_message_limit,
            )
            local_now = now.astimezone(TAIPEI)
            if local_now.month == 12:
                reset_local = local_now.replace(
                    year=local_now.year + 1, month=1, day=1,
                    hour=0, minute=0, second=0, microsecond=0,
                )
            else:
                reset_local = local_now.replace(
                    month=local_now.month + 1, day=1,
                    hour=0, minute=0, second=0, microsecond=0,
                )
            self._quota_checked_at = now
            self._quota_snapshot = {
                "monthlyMessageLimit": monthly_limit,
                "monthlyMessageUsage": monthly_usage,
                "monthlyMessageRemaining": monthly_remaining,
                "remainingTradingDays": remaining_days,
                "baseDailyTradeMessageLimit": base_limit,
                "minimumDailyTradeMessageLimit": max(
                    0, get_settings().line_min_daily_trade_message_limit,
                ),
                "effectiveDailyTradeMessageLimit": effective_limit,
                "dailyTradeMessageShortfall": max(
                    0,
                    get_settings().line_min_daily_trade_message_limit - effective_limit,
                ),
                "quotaResetAt": reset_local.isoformat(),
                "quotaCheckedAt": now.isoformat(),
            }
            return self._quota_snapshot

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
        with SessionLocal() as db:
            notification_settings = get_line_notification_settings(db)
            if not line_setting_allows(notification_settings, event.event_type):
                return 0
            group_ids = self._active_group_ids(db)
        try:
            await gmail_notification_dispatcher.dispatch(
                event_type=event.event_type,
                action=event.action,
                message=event.message,
                dedupe_key=event.dedupe_key,
                signal_id=event.signal_id,
                symbol=event.symbol,
            )
        except Exception:
            logger.exception("Gmail notification dispatch failed for %s", event.dedupe_key)
        if not get_settings().line_notifications_enabled:
            return 0
        daily_limit = max(0, get_settings().line_daily_trade_message_limit)
        if event.event_type in TRADE_NOTIFICATION_EVENT_TYPES:
            quota = await self.quota_status()
            daily_limit = int(quota["effectiveDailyTradeMessageLimit"])
        successful = 0
        for group_id in group_ids:
            with SessionLocal() as db:
                created_at = datetime.now(UTC)
                if event.cooldown_entry and event.symbol:
                    local_now = created_at.astimezone(TAIPEI)
                    cutoff = datetime.combine(local_now.date(), time.min, TAIPEI).astimezone(UTC)
                    recent = db.scalar(select(LineDeliveryLog.id).where(
                        LineDeliveryLog.group_id == group_id,
                        LineDeliveryLog.symbol == event.symbol,
                        LineDeliveryLog.event_type == event.event_type,
                        LineDeliveryLog.status.in_(["pending", "sent", "skipped"]),
                        LineDeliveryLog.created_at >= cutoff,
                    ).limit(1))
                    if recent is not None:
                        continue
                if (
                    event.event_type in TRADE_NOTIFICATION_EVENT_TYPES
                    and daily_trade_message_count(db, now=created_at) >= daily_limit
                ):
                    skipped = LineDeliveryLog(
                        group_id=group_id,
                        event_type=event.event_type,
                        signal_id=event.signal_id,
                        symbol=event.symbol,
                        action=event.action,
                        priority=event.priority,
                        dedupe_key=event.dedupe_key,
                        status="skipped",
                        attempts=0,
                        error_message=f"daily trade message limit reached ({daily_limit})",
                        message_preview=event.message[:5000],
                        created_at=created_at,
                    )
                    db.add(skipped)
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                    logger.info(
                        "Skipped LINE %s notification after reaching daily trade limit %s",
                        event.event_type,
                        daily_limit,
                    )
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
                    created_at=created_at,
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
        status_processed_dates: set[str] = set()
        with SessionLocal() as db:
            notification_settings = get_line_notification_settings(db)
        for signal in recommendations:
            if not signal.get("isOfficialRecommendation"):
                continue
            action = str(signal.get("action", ""))
            if action.startswith("等待"):
                continue
            direction = str(signal.get("direction"))
            if direction == "long" and "買進" not in action:
                continue
            if direction == "short" and not any(value in action for value in ["放空", "做空", "跌破"]):
                continue
            event_type = "long_entry" if direction == "long" else "short_entry"
            if not line_setting_allows(notification_settings, event_type):
                continue
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
            include_session_status = False
            if trading_date not in status_processed_dates:
                with SessionLocal() as db:
                    prior_entry = db.scalar(
                        select(LineDeliveryLog.id)
                        .where(
                            LineDeliveryLog.event_type.in_({"long_entry", "short_entry"}),
                            LineDeliveryLog.status.in_({"pending", "sent"}),
                            LineDeliveryLog.dedupe_key.like(
                                f"formal-entry:{trading_date}:%"
                            ),
                        )
                        .limit(1)
                    )
                include_session_status = (
                    notification_settings.opening_enabled
                    and prior_entry is None
                )
                status_processed_dates.add(trading_date)
            events.append(LineNotificationEvent(
                event_type=event_type,
                action=action,
                message=format_signal_message(
                    signal,
                    include_session_status=include_session_status,
                ),
                dedupe_key=f"formal-entry:{trading_date}:{symbol}:{direction}",
                priority=6,
                signal_id=signal_id,
                symbol=symbol,
                cooldown_entry=True,
            ))
            if len(events) >= LINE_RECOMMENDATION_BATCH_LIMIT:
                break
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
