from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
import logging
import smtplib
import ssl

import httpx
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..database import SessionLocal
from ..models import GmailDeliveryLog


logger = logging.getLogger(__name__)


class GmailNotificationDispatcher:
    def __init__(self) -> None:
        self._smtp_unavailable_until: datetime | None = None

    @property
    def enabled(self) -> bool:
        return get_settings().gmail_notifications_enabled

    @property
    def configured(self) -> bool:
        settings = get_settings()
        transport_configured = bool(
            (settings.gmail_apps_script_url and settings.gmail_apps_script_secret)
            or (settings.gmail_sender_email and settings.gmail_app_password)
        )
        return bool(
            self.enabled
            and settings.gmail_recipient_list
            and transport_configured
        )

    @property
    def transport(self) -> str:
        settings = get_settings()
        if settings.gmail_apps_script_url and settings.gmail_apps_script_secret:
            return "apps_script"
        if settings.gmail_sender_email and settings.gmail_app_password:
            return "smtp"
        return "unconfigured"

    @property
    def masked_recipients(self) -> list[str]:
        values: list[str] = []
        for recipient in get_settings().gmail_recipient_list:
            local, separator, domain = recipient.partition("@")
            if not separator:
                values.append("***")
                continue
            visible = local[:2] if len(local) > 2 else local[:1]
            values.append(f"{visible}***@{domain}")
        return values

    @staticmethod
    def _send_sync(recipient: str, subject: str, body: str) -> None:
        settings = get_settings()
        message = EmailMessage()
        message["From"] = formataddr((settings.gmail_sender_name, settings.gmail_sender_email))
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        context = ssl.create_default_context()
        password = settings.gmail_app_password.replace(" ", "")
        errors: list[str] = []
        ports = [settings.gmail_smtp_port]
        alternate = 587 if settings.gmail_smtp_port == 465 else 465
        if alternate not in ports:
            ports.append(alternate)
        for port in ports:
            try:
                if port == 465:
                    with smtplib.SMTP_SSL(
                        settings.gmail_smtp_host,
                        port,
                        timeout=10,
                        context=context,
                    ) as smtp:
                        smtp.login(settings.gmail_sender_email, password)
                        smtp.send_message(message)
                else:
                    with smtplib.SMTP(settings.gmail_smtp_host, port, timeout=10) as smtp:
                        smtp.ehlo()
                        smtp.starttls(context=context)
                        smtp.ehlo()
                        smtp.login(settings.gmail_sender_email, password)
                        smtp.send_message(message)
                return
            except smtplib.SMTPAuthenticationError:
                raise
            except (OSError, smtplib.SMTPException) as exc:
                detail = str(exc).replace(settings.gmail_sender_email, "***@gmail.com")
                errors.append(f"{port}/{type(exc).__name__}: {detail[:180]}")
        raise OSError("；".join(errors))

    @staticmethod
    async def _send_apps_script(recipient: str, subject: str, body: str) -> None:
        settings = get_settings()
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.post(
                settings.gmail_apps_script_url,
                json={
                    "secret": settings.gmail_apps_script_secret,
                    "to": recipient,
                    "subject": subject,
                    "body": body,
                    "senderName": settings.gmail_sender_name,
                },
            )
        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                f"Apps Script HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OSError("Apps Script 回傳格式錯誤") from exc
        if payload.get("ok") is not True:
            raise OSError(f"Apps Script 拒絕寄送：{str(payload.get('error', 'unknown'))[:180]}")

    async def dispatch(
        self,
        *,
        event_type: str,
        action: str,
        message: str,
        dedupe_key: str,
        signal_id: str | None = None,
        symbol: str | None = None,
        channel_name: str = "AI當沖機器人",
    ) -> int:
        if not self.configured:
            return 0
        now_utc = datetime.now(UTC)
        if self._smtp_unavailable_until and now_utc < self._smtp_unavailable_until:
            return 0
        subject = f"【{channel_name}｜{action}】"
        if symbol:
            subject = f"{subject} {symbol}"
        sent = 0
        for recipient in get_settings().gmail_recipient_list:
            now = datetime.now(UTC)
            with SessionLocal() as db:
                log = GmailDeliveryLog(
                    recipient=recipient,
                    event_type=event_type,
                    signal_id=signal_id,
                    symbol=symbol,
                    action=action,
                    dedupe_key=dedupe_key,
                    subject=subject,
                    status="pending",
                    attempts=0,
                    message_preview=message[:5000],
                    created_at=now,
                )
                db.add(log)
                try:
                    db.commit()
                    db.refresh(log)
                except IntegrityError:
                    db.rollback()
                    continue

            attempts = 0
            error_message: str | None = None
            for attempt in range(1, 4):
                attempts = attempt
                try:
                    if self.transport == "apps_script":
                        await self._send_apps_script(recipient, subject, message)
                    else:
                        await asyncio.to_thread(self._send_sync, recipient, subject, message)
                    error_message = None
                    break
                except (OSError, smtplib.SMTPException, httpx.HTTPError) as exc:
                    detail = str(exc).replace(get_settings().gmail_sender_email, "***@gmail.com")
                    error_message = f"{type(exc).__name__}: {detail[:420]}"
                    if self.transport == "smtp" and "Network is unreachable" in detail:
                        self._smtp_unavailable_until = datetime.now(UTC) + timedelta(minutes=15)
                        break
                    if isinstance(exc, smtplib.SMTPAuthenticationError):
                        break
                    if attempt < 3:
                        await asyncio.sleep(0.5 * attempt)

            with SessionLocal() as db:
                stored = db.get(GmailDeliveryLog, log.id)
                if stored is None:
                    continue
                stored.attempts = attempts
                stored.error_message = error_message
                stored.status = "sent" if error_message is None else "failed"
                if error_message is None:
                    stored.sent_at = datetime.now(UTC)
                    sent += 1
                db.commit()
            if error_message:
                logger.warning("Gmail notification failed for %s: %s", dedupe_key, error_message)
        return sent


gmail_notification_dispatcher = GmailNotificationDispatcher()
