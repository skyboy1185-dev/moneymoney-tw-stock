from __future__ import annotations

import json
from datetime import UTC, datetime, time
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import (
    LineDeliveryLog,
    LineNotificationGroup,
    LineNotificationSettings,
    LineWebhookEvent,
)
from ..schemas import LineNotificationSettingsUpdate
from ..services.line_messaging import (
    LINE_GROUP_DISCLAIMER,
    OFFICIAL_ACCOUNT_NAME,
    LineNotificationEvent,
    get_line_notification_settings,
    line_notification_dispatcher,
    mask_group_id,
    verify_line_signature,
)


webhook_router = APIRouter(prefix="/api/integrations/line", tags=["LINE integration"])
router = APIRouter(prefix="/integrations/line", tags=["LINE integration"])
settings = get_settings()
TAIPEI = ZoneInfo("Asia/Taipei")


def _settings_payload(item: LineNotificationSettings) -> dict[str, Any]:
    return {
        "openingEnabled": item.opening_enabled,
        "longEntryEnabled": item.long_entry_enabled,
        "shortEntryEnabled": item.short_entry_enabled,
        "longExitEnabled": item.long_exit_enabled,
        "shortCoverEnabled": item.short_cover_enabled,
        "stopLossEnabled": item.stop_loss_enabled,
        "dataAlertEnabled": item.data_alert_enabled,
        "closingSummaryEnabled": item.closing_summary_enabled,
        "updatedAt": item.updated_at.isoformat(),
    }


def _group_payload(item: LineNotificationGroup) -> dict[str, Any]:
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
    item = LineWebhookEvent(
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


def _bind_group(db: Session, group_id: str) -> LineNotificationGroup:
    now = datetime.now(UTC)
    item = db.scalar(select(LineNotificationGroup).where(LineNotificationGroup.group_id == group_id))
    if item is None:
        item = LineNotificationGroup(
            group_id=group_id,
            display_name="LINE 當沖通知群組",
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
    item = db.scalar(select(LineNotificationGroup).where(LineNotificationGroup.group_id == group_id))
    if item:
        item.active = False
        item.unbound_at = datetime.now(UTC)
        item.last_webhook_at = datetime.now(UTC)
        db.commit()


@webhook_router.post("/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: str | None = Header(default=None, alias="X-Line-Signature"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw_body = await request.body()
    if not settings.line_channel_secret:
        raise HTTPException(status_code=503, detail="LINE_CHANNEL_SECRET 尚未設定")
    if not verify_line_signature(raw_body, x_line_signature or "", settings.line_channel_secret):
        raise HTTPException(status_code=401, detail="LINE Webhook 簽章驗證失敗")
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
        if (
            event.get("type") != "message"
            or message.get("type") != "text"
            or not group_id
        ):
            continue
        text = str(message.get("text", "")).strip()
        reply_token = str(event.get("replyToken", ""))
        reply = ""
        if text == "綁定當沖機器人":
            _bind_group(db, str(group_id))
            reply = "AI當沖機器人已成功綁定此群組"
        elif text == "解除當沖通知":
            _unbind_group(db, str(group_id))
            reply = "AI當沖機器人已解除此群組的當沖通知"
        elif text == "測試當沖通知":
            reply = (
                "【測試訊息】\n"
                "AI當沖機器人 LINE 群組通知已連線成功。\n"
                "此訊息為測試通知，非交易訊號。"
            )
        elif text == "發送免責聲明":
            reply = LINE_GROUP_DISCLAIMER
        if reply and reply_token:
            await line_notification_dispatcher.client.reply_text(reply_token, reply)
            handled += 1
    return {"ok": True, "handledEvents": handled}


@router.get("/status")
def line_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    groups = db.scalars(
        select(LineNotificationGroup)
        .where(LineNotificationGroup.active.is_(True))
        .order_by(LineNotificationGroup.bound_at.desc())
    ).all()
    local_today = datetime.now(UTC).astimezone(TAIPEI).date()
    start = datetime.combine(local_today, time.min, TAIPEI).astimezone(UTC)
    today_count = int(db.scalar(select(func.count()).select_from(LineDeliveryLog).where(
        LineDeliveryLog.status == "sent",
        LineDeliveryLog.sent_at >= start,
    )) or 0)
    last_push = db.scalar(select(func.max(LineDeliveryLog.sent_at)).where(
        LineDeliveryLog.status == "sent",
    ))
    target_configured = bool(settings.line_target_group_id)
    has_group = bool(groups or target_configured)
    credentials_ready = bool(settings.line_channel_access_token and settings.line_channel_secret)
    if not settings.line_notifications_enabled:
        connection_status = "disabled"
    elif not credentials_ready:
        connection_status = "missing_credentials"
    elif not has_group:
        connection_status = "awaiting_group"
    else:
        connection_status = "connected"
    payload_groups = [_group_payload(item) for item in groups]
    if target_configured and not groups:
        payload_groups.append({
            "id": 0,
            "displayName": "環境變數預設群組",
            "maskedGroupId": mask_group_id(settings.line_target_group_id),
            "active": True,
            "boundAt": None,
            "lastPushAt": last_push.isoformat() if last_push else None,
        })
    return {
        "officialAccountName": OFFICIAL_ACCOUNT_NAME,
        "enabled": settings.line_notifications_enabled,
        "credentialsConfigured": credentials_ready,
        "connectionStatus": connection_status,
        "groups": payload_groups,
        "lastPushAt": last_push.isoformat() if last_push else None,
        "todayPushCount": today_count,
        "publicWebhookUrl": (
            f"{settings.public_web_url.rstrip('/')}/api/integrations/line/webhook"
            if settings.public_web_url else "/api/integrations/line/webhook"
        ),
        "settings": _settings_payload(get_line_notification_settings(db)),
    }


@router.put("/settings")
def update_line_settings(
    body: LineNotificationSettingsUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = get_line_notification_settings(db)
    for api_name, model_name in {
        "opening_enabled": "opening_enabled",
        "long_entry_enabled": "long_entry_enabled",
        "short_entry_enabled": "short_entry_enabled",
        "long_exit_enabled": "long_exit_enabled",
        "short_cover_enabled": "short_cover_enabled",
        "stop_loss_enabled": "stop_loss_enabled",
        "data_alert_enabled": "data_alert_enabled",
        "closing_summary_enabled": "closing_summary_enabled",
    }.items():
        setattr(item, model_name, getattr(body, api_name))
    item.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(item)
    return _settings_payload(item)


@router.post("/test")
async def test_line_notification(db: Session = Depends(get_db)) -> dict[str, Any]:
    groups = db.scalar(select(func.count()).select_from(LineNotificationGroup).where(
        LineNotificationGroup.active.is_(True),
    )) or 0
    if not settings.line_target_group_id and groups == 0:
        raise HTTPException(status_code=409, detail="尚未綁定任何 LINE 群組")
    if not line_notification_dispatcher.client.configured:
        raise HTTPException(status_code=409, detail="LINE Channel Access Token 或 Secret 尚未設定")
    event = LineNotificationEvent(
        event_type="test",
        action="測試當沖通知",
        message=(
            "【測試訊息】\n"
            "AI當沖機器人 LINE 群組通知已連線成功。\n"
            "此訊息為測試通知，非交易訊號。"
        ),
        dedupe_key=f"manual-test:{uuid4()}",
        priority=2,
    )
    sent = await line_notification_dispatcher.dispatch_many([event])
    if sent == 0:
        raise HTTPException(status_code=502, detail="LINE 測試通知推送失敗，請檢查推送紀錄與 Channel 設定")
    return {"ok": True, "sentGroups": sent}


@router.delete("/groups/{group_record_id}")
def unbind_line_group(group_record_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    if group_record_id == 0:
        raise HTTPException(status_code=409, detail="環境變數群組請移除 LINE_TARGET_GROUP_ID 後重新部署")
    item = db.get(LineNotificationGroup, group_record_id)
    if item is None or not item.active:
        raise HTTPException(status_code=404, detail="LINE 群組綁定不存在")
    item.active = False
    item.unbound_at = datetime.now(UTC)
    db.commit()
    return {"ok": True, "groupId": mask_group_id(item.group_id)}
