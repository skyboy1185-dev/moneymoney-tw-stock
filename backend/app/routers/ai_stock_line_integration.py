from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import AIStockLineDeliveryLog, AIStockLineGroup, AIStockLineWebhookEvent
from ..services.ai_stock_line_messaging import (
    AI_STOCK_OFFICIAL_ACCOUNT_NAME,
    ai_stock_line_dispatcher,
)
from ..services.ai_stock_line import friday_replay_messages
from ..services.gmail_messaging import gmail_notification_dispatcher
from ..services.line_messaging import LineNotificationEvent, mask_group_id, verify_line_signature


webhook_router = APIRouter(
    prefix="/api/integrations/ai-stock-line",
    tags=["AI stock LINE integration"],
)
router = APIRouter(
    prefix="/integrations/ai-stock-line",
    tags=["AI stock LINE integration"],
)
settings = get_settings()
TAIPEI = ZoneInfo("Asia/Taipei")


def _group_payload(item: AIStockLineGroup) -> dict[str, Any]:
    return {
        "id": item.id,
        "displayName": item.display_name,
        "maskedGroupId": mask_group_id(item.group_id),
        "active": item.active,
        "boundAt": item.bound_at.isoformat(),
        "lastPushAt": item.last_push_at.isoformat() if item.last_push_at else None,
    }


def _save_webhook_once(db: Session, event: dict[str, Any], group_id: str | None) -> bool:
    webhook_event_id = str(event.get("webhookEventId") or "")
    if not webhook_event_id:
        return True
    item = AIStockLineWebhookEvent(
        webhook_event_id=webhook_event_id,
        event_type=str(event.get("type", "unknown")),
        group_id_masked=mask_group_id(group_id) if group_id else None,
        received_at=datetime.now(UTC),
    )
    db.add(item)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _bind_group(db: Session, group_id: str) -> AIStockLineGroup:
    now = datetime.now(UTC)
    item = db.scalar(select(AIStockLineGroup).where(AIStockLineGroup.group_id == group_id))
    if item is None:
        item = AIStockLineGroup(
            group_id=group_id,
            display_name="AI 選股通知群組",
            active=True,
            bound_at=now,
            last_webhook_at=now,
        )
        db.add(item)
    else:
        item.active = True
        item.unbound_at = None
        item.bound_at = now
        item.last_webhook_at = now
    db.commit()
    db.refresh(item)
    return item


def _unbind_group(db: Session, group_id: str) -> None:
    item = db.scalar(select(AIStockLineGroup).where(AIStockLineGroup.group_id == group_id))
    if item:
        item.active = False
        item.unbound_at = datetime.now(UTC)
        item.last_webhook_at = datetime.now(UTC)
        db.commit()


@webhook_router.post("/webhook")
async def ai_stock_line_webhook(
    request: Request,
    x_line_signature: str | None = Header(default=None, alias="X-Line-Signature"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw_body = await request.body()
    if not settings.ai_stock_line_channel_secret:
        raise HTTPException(status_code=503, detail="AI_STOCK_LINE_CHANNEL_SECRET 尚未設定")
    if not verify_line_signature(
        raw_body,
        x_line_signature or "",
        settings.ai_stock_line_channel_secret,
    ):
        raise HTTPException(status_code=401, detail="AI 選股 LINE Webhook 簽章驗證失敗")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook JSON 格式錯誤") from exc

    handled = 0
    for event in payload.get("events", []):
        source = event.get("source") or {}
        group_id = source.get("groupId") if source.get("type") == "group" else None
        if not _save_webhook_once(db, event, group_id):
            continue
        if event.get("type") == "leave" and group_id:
            _unbind_group(db, str(group_id))
            handled += 1
            continue
        message = event.get("message") or {}
        if event.get("type") != "message" or message.get("type") != "text" or not group_id:
            continue
        text = str(message.get("text", "")).strip()
        reply_token = str(event.get("replyToken", ""))
        reply = ""
        if text in {"綁定AI選股機器人", "綁定選股機器人"}:
            _bind_group(db, str(group_id))
            reply = "AI選股機器人已成功綁定此群組"
        elif text in {"解除AI選股通知", "解除選股通知"}:
            _unbind_group(db, str(group_id))
            reply = "AI選股機器人已解除此群組的選股通知"
        elif text in {"測試AI選股通知", "測試選股通知"}:
            reply = (
                "【測試訊息】\n"
                "AI選股機器人 LINE 群組通知已連線成功。\n"
                "此訊息為測試通知，非交易訊號。"
            )
        if reply and reply_token:
            await ai_stock_line_dispatcher.client.reply_text(reply_token, reply)
            handled += 1
    return {"ok": True, "handledEvents": handled}


@router.get("/status")
def ai_stock_line_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    groups = db.scalars(
        select(AIStockLineGroup)
        .where(AIStockLineGroup.active.is_(True))
        .order_by(AIStockLineGroup.bound_at.desc())
    ).all()
    local_today = datetime.now(UTC).astimezone(TAIPEI).date()
    start = datetime.combine(local_today, time.min, TAIPEI).astimezone(UTC)
    today_count = int(db.scalar(select(func.count()).select_from(AIStockLineDeliveryLog).where(
        AIStockLineDeliveryLog.status == "sent",
        AIStockLineDeliveryLog.sent_at >= start,
    )) or 0)
    last_push = db.scalar(select(func.max(AIStockLineDeliveryLog.sent_at)).where(
        AIStockLineDeliveryLog.status == "sent",
    ))
    target_configured = bool(settings.ai_stock_line_target_group_id)
    credentials_ready = bool(
        settings.ai_stock_line_channel_access_token
        and settings.ai_stock_line_channel_secret
    )
    if not settings.ai_stock_line_notifications_enabled:
        connection_status = "disabled"
    elif not credentials_ready:
        connection_status = "missing_credentials"
    elif not groups and not target_configured:
        connection_status = "awaiting_group"
    else:
        connection_status = "connected"
    payload_groups = [_group_payload(item) for item in groups]
    if target_configured and not groups:
        payload_groups.append({
            "id": 0,
            "displayName": "環境變數預設群組",
            "maskedGroupId": mask_group_id(settings.ai_stock_line_target_group_id),
            "active": True,
            "boundAt": None,
            "lastPushAt": last_push.isoformat() if last_push else None,
        })
    return {
        "officialAccountName": AI_STOCK_OFFICIAL_ACCOUNT_NAME,
        "enabled": settings.ai_stock_line_notifications_enabled,
        "credentialsConfigured": credentials_ready,
        "connectionStatus": connection_status,
        "groups": payload_groups,
        "lastPushAt": last_push.isoformat() if last_push else None,
        "todayPushCount": today_count,
        "gmailEnabled": gmail_notification_dispatcher.enabled,
        "gmailConfigured": gmail_notification_dispatcher.configured,
        "gmailTransport": gmail_notification_dispatcher.transport,
        "gmailRecipients": gmail_notification_dispatcher.masked_recipients,
        "publicWebhookUrl": (
            f"{settings.public_web_url.rstrip('/')}/api/integrations/ai-stock-line/webhook"
            if settings.public_web_url else "/api/integrations/ai-stock-line/webhook"
        ),
    }


@router.post("/gmail/test")
async def test_ai_stock_gmail_notification() -> dict[str, Any]:
    if not gmail_notification_dispatcher.configured:
        raise HTTPException(status_code=409, detail="AI選股 Gmail 尚未完成設定")
    sent = await gmail_notification_dispatcher.dispatch(
        event_type="ai_stock_test",
        action="AI選股 Gmail 測試通知",
        message=(
            "【AI選股機器人｜Gmail 測試】\n\n"
            "這是一封 AI 選股機器人測試信，不是真實選股訊號。\n\n"
            "測試標的：2330 台積電\n"
            "測試策略：預測即將翻紅\n"
            "測試狀態：等待確認\n\n"
            "正式選股的買進、加碼、減碼、賣出、停損與資料異常事件，"
            "都會透過 Gmail 獨立發送。"
        ),
        dedupe_key=f"ai-stock-gmail-manual-test:{uuid4()}",
        symbol="2330",
        channel_name=AI_STOCK_OFFICIAL_ACCOUNT_NAME,
    )
    if sent == 0:
        raise HTTPException(status_code=502, detail="AI選股 Gmail 測試寄送失敗，請查看 Gmail 傳送紀錄")
    return {"ok": True, "sentRecipients": sent}


@router.post("/test")
async def test_ai_stock_line_notification(db: Session = Depends(get_db)) -> dict[str, Any]:
    groups = db.scalar(select(func.count()).select_from(AIStockLineGroup).where(
        AIStockLineGroup.active.is_(True),
    )) or 0
    if not settings.ai_stock_line_target_group_id and groups == 0:
        raise HTTPException(status_code=409, detail="尚未綁定任何 AI 選股 LINE 群組")
    if not ai_stock_line_dispatcher.client.configured:
        raise HTTPException(
            status_code=409,
            detail="AI 選股 LINE Channel Access Token 或 Secret 尚未設定",
        )
    event = LineNotificationEvent(
        event_type="test",
        action="測試AI選股通知",
        message=(
            "【測試訊息】\n"
            "AI選股機器人 LINE 群組通知已連線成功。\n"
            "此訊息為測試通知，非交易訊號。"
        ),
        dedupe_key=f"manual-test:{uuid4()}",
        priority=2,
    )
    sent = await ai_stock_line_dispatcher.dispatch_many([event])
    if sent == 0:
        raise HTTPException(status_code=502, detail="AI 選股 LINE 測試通知推送失敗")
    return {"ok": True, "sentGroups": sent}


@router.post("/simulations/last-friday")
async def simulate_last_friday_ai_stock_line(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    groups = db.scalar(select(func.count()).select_from(AIStockLineGroup).where(
        AIStockLineGroup.active.is_(True),
    )) or 0
    if not settings.ai_stock_line_target_group_id and groups == 0:
        raise HTTPException(status_code=409, detail="尚未綁定任何 AI 選股 LINE 群組")
    if not ai_stock_line_dispatcher.client.configured:
        raise HTTPException(
            status_code=409,
            detail="AI 選股 LINE Channel Access Token 或 Secret 尚未設定",
        )
    cutoff = datetime.now(UTC) - timedelta(minutes=2)
    recent = db.scalar(select(AIStockLineDeliveryLog.id).where(
        AIStockLineDeliveryLog.event_type == "ai_stock_simulation",
        AIStockLineDeliveryLog.created_at >= cutoff,
    ).limit(1))
    if recent is not None:
        raise HTTPException(status_code=429, detail="模擬通知兩分鐘內只能執行一次")

    replay_date = date(2026, 7, 24)
    run_id = str(uuid4())
    events = [
        LineNotificationEvent(
            event_type="ai_stock_simulation",
            action="展示模擬回放",
            message=message,
            dedupe_key=f"ai-stock-simulation:{run_id}:{index}",
            priority=index,
            signal_id=f"simulation-{replay_date.isoformat()}-{index}",
            symbol=symbol,
        )
        for index, (message, symbol) in enumerate(friday_replay_messages(replay_date))
    ]
    sent = await ai_stock_line_dispatcher.dispatch_many(events)
    if sent == 0:
        raise HTTPException(status_code=502, detail="AI 選股 LINE 模擬通知推送失敗")
    return {
        "ok": True,
        "replayDate": replay_date.isoformat(),
        "messages": len(events),
        "sentDeliveries": sent,
        "mode": "demo",
    }


@router.delete("/groups/{group_record_id}")
def unbind_ai_stock_line_group(
    group_record_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if group_record_id == 0:
        raise HTTPException(
            status_code=409,
            detail="環境變數群組請移除 AI_STOCK_LINE_TARGET_GROUP_ID 後重新部署",
        )
    item = db.get(AIStockLineGroup, group_record_id)
    if item is None or not item.active:
        raise HTTPException(status_code=404, detail="AI 選股 LINE 群組綁定不存在")
    item.active = False
    item.unbound_at = datetime.now(UTC)
    db.commit()
    return {"ok": True, "groupId": mask_group_id(item.group_id)}
