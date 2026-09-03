from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import json
import logging
import math
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError
from sqlalchemy import select

from ..adaptive_schemas import AdaptiveScanPayload
from ..config import get_settings
from ..database import BackgroundSessionLocal as SessionLocal
from ..models import (
    AdaptivePaperTrade,
    AdaptiveSignal,
    AdaptiveStockCandidate,
    ElectronicIndustryMapping,
    MarketRegime,
    SuperAIDaytradeNotification,
)
from .adaptive_electronic_service import STRATEGY_NAMES, process_adaptive_scan
from .adaptive_entry_window import adaptive_entry_window_open
from .adaptive_parameters import load_parameters
from .adaptive_performance_service import update_open_trade_from_market_price
from .ai_stock_line import push_ai_stock_message
from .day_trading_schedule import is_twse_trading_day
from .line_messaging import (
    PERSONAL_STRATEGY_SIMULATION_NOTE,
    format_personal_strategy_simulation,
)
from .gmail_messaging import gmail_notification_dispatcher
from .official_market_data import StockQuoteRequest, official_market_data_provider
from .super_ai_daytrade_service import (
    SYSTEM_NAME,
    TRADE_EMAIL_CATEGORIES,
    record_notification,
)


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
POSITION_MONITOR_ERROR_DEDUPE_SECONDS = 300


def _normalize_scan_payload(raw: object) -> object:
    """Keep one missing industry code from invalidating the entire market scan."""
    if not isinstance(raw, dict) or not isinstance(raw.get("stocks"), list):
        return raw
    normalized = 0
    for stock in raw["stocks"]:
        if isinstance(stock, dict) and not str(stock.get("industry_code") or "").strip():
            stock["industry_code"] = "00"
            normalized += 1
    if normalized:
        logger.warning("Normalized %s stocks with a missing industry code", normalized)
    return raw


def _scanner_payload_error(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    if "market" in raw:
        return None
    message = raw.get("detail") or raw.get("error") or raw.get("message")
    if message:
        return f"scanner returned error payload: {message}"
    return None


def _session(now: datetime) -> str:
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


def _signal_has_super_ai_trade_record(db, signal: AdaptiveSignal) -> bool:
    if signal.signal_type == "entry_confirmed":
        return db.scalar(select(AdaptivePaperTrade.id).where(
            AdaptivePaperTrade.entry_signal_key == signal.signal_key,
        ).limit(1)) is not None
    if signal.signal_type == "exit_triggered":
        return db.scalar(select(AdaptivePaperTrade.id).where(
            AdaptivePaperTrade.exit_signal_key == signal.signal_key,
        ).limit(1)) is not None
    return False


def _seconds_until_open(now: datetime) -> int | None:
    local = now.astimezone(TAIPEI)
    holidays: set[date] = set()
    for raw in get_settings().twse_holidays.split(","):
        try:
            holidays.add(date.fromisoformat(raw.strip()))
        except ValueError:
            continue
    if not is_twse_trading_day(local.date(), holidays):
        return None
    market_open = datetime.combine(local.date(), time(9, 0), TAIPEI)
    seconds = math.ceil((market_open - local).total_seconds())
    return seconds if seconds > 0 else None


def _list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except ValueError:
        return []


async def fetch_adaptive_scan_payload() -> AdaptiveScanPayload:
    settings = get_settings()
    url = settings.adaptive_electronic_scanner_url.strip()
    if not url or urlparse(url).scheme not in {"http", "https"}:
        raise RuntimeError("ADAPTIVE_ELECTRONIC_SCANNER_URL 尚未正確設定")
    headers = {
        "Accept": "application/json",
        "User-Agent": "TWSE-Adaptive-Electronic-Automation/1.0",
    }
    if settings.adaptive_electronic_scanner_token:
        headers["X-Adaptive-Scanner-Token"] = settings.adaptive_electronic_scanner_token
    last_error: Exception | None = None
    async with httpx.AsyncClient(
        timeout=min(settings.adaptive_electronic_scanner_timeout_seconds, 25.0),
        follow_redirects=True,
    ) as client:
        timeout_seconds = min(settings.adaptive_electronic_scanner_timeout_seconds, 25.0)
        for attempt in range(3):
            try:
                response = await asyncio.wait_for(
                    client.get(url, headers=headers),
                    timeout=timeout_seconds,
                )
                try:
                    raw = response.json()
                except ValueError as error:
                    raise RuntimeError(
                        f"scanner returned non-json HTTP {response.status_code}: {response.text[:300]}"
                    ) from error
                payload_error = _scanner_payload_error(raw)
                if response.status_code >= 400:
                    raise RuntimeError(
                        payload_error
                        or f"scanner HTTP {response.status_code}: {response.text[:300]}"
                    )
                if payload_error:
                    raise RuntimeError(payload_error)
                try:
                    return AdaptiveScanPayload.model_validate(_normalize_scan_payload(raw))
                except ValidationError as error:
                    first = error.errors()[0] if error.errors() else {}
                    location = ".".join(str(part) for part in first.get("loc", ())) or "payload"
                    message = first.get("msg") or str(error)
                    raise RuntimeError(f"scanner payload invalid at {location}: {message}") from error
            except TimeoutError as error:
                last_error = RuntimeError(f"scanner total timeout after {timeout_seconds:g}s")
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise last_error from error
            except (RuntimeError, httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.TimeoutException) as error:
                last_error = error
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise
    raise last_error or RuntimeError("adaptive scanner failed")


class AdaptiveElectronicAutomation:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._position_task: asyncio.Task[None] | None = None
        self._last_quote_error_at: dict[int, datetime] = {}
        self._state = {
            "status": "stopped", "lastRunAt": None, "lastSuccessAt": None,
            "lastResult": None, "lastError": None, "nextScanSeconds": 180,
            "positionMonitor": {
                "checkedAt": None,
                "openTrades": 0,
                "updated": 0,
                "closed": 0,
                "quoteErrors": 0,
            },
        }
        self._last_close_scan_date: date | None = None

    @property
    def state(self) -> dict:
        return dict(self._state)

    def _has_recent_success(self, now: datetime, max_age: timedelta = timedelta(minutes=20)) -> bool:
        value = self._state.get("lastSuccessAt")
        if not isinstance(value, str):
            return False
        try:
            last_success = datetime.fromisoformat(value)
        except ValueError:
            return False
        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=UTC)
        return now - last_success.astimezone(UTC) <= max_age

    async def start(self) -> None:
        if self._task and not self._task.done():
            if self._position_task is None or self._position_task.done():
                self._position_task = asyncio.create_task(
                    self._run_position_monitor(),
                    name="super-ai-daytrade-position-monitor",
                )
            return
        self._state["status"] = "running"
        self._task = asyncio.create_task(self._run(), name="adaptive-electronic-market-scanner")
        self._position_task = asyncio.create_task(
            self._run_position_monitor(),
            name="super-ai-daytrade-position-monitor",
        )

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        if self._position_task:
            self._position_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        if self._position_task:
            with suppress(asyncio.CancelledError):
                await self._position_task
        self._task = None
        self._position_task = None
        self._state["status"] = "stopped"

    async def _fetch_payload(self) -> AdaptiveScanPayload:
        return await fetch_adaptive_scan_payload()

    async def run_once(
        self,
        now: datetime | None = None,
        *,
        force: bool = False,
        send_notifications: bool = True,
    ) -> dict:
        current = now or datetime.now(UTC)
        self._state["status"] = "running"
        self._state["lastRunAt"] = current.isoformat()
        if not get_settings().adaptive_electronic_enabled:
            result = {"status": "disabled"}
            self._state["lastResult"] = result
            return result
        session = _session(current)
        local_date = current.astimezone(TAIPEI).date()
        should_close_scan = session == "after_close" and self._last_close_scan_date != local_date
        if not force and session != "open" and not should_close_scan:
            result = {"status": f"skipped_{session}"}
            self._state.update({"lastResult": result, "lastError": None})
            return result
        try:
            payload = await self._fetch_payload()
        except Exception as error:
            result = {"status": "scanner_error", "message": str(error)[:300]}
            self._state.update({"lastResult": result, "lastError": str(error)[:500]})
            raise
        with SessionLocal() as db:
            result = process_adaptive_scan(db, payload)
        if session == "after_close":
            self._last_close_scan_date = local_date
        if send_notifications:
            await self._send_pending_signals(result.get("signalIds", []))
            await self._send_super_ai_emails()
        self._state.update({
            "status": "running",
            "lastSuccessAt": datetime.now(UTC).isoformat(),
            "lastResult": {key: value for key, value in result.items() if key != "signalIds"},
            "lastError": None,
        })
        return result

    async def _send_super_ai_emails(self) -> None:
        if not gmail_notification_dispatcher.configured:
            return
        with SessionLocal() as db:
            rows = list(db.scalars(select(SuperAIDaytradeNotification).where(
                SuperAIDaytradeNotification.source == "SUPER_AI_DAYTRADE",
                SuperAIDaytradeNotification.email_sent.is_(False),
                SuperAIDaytradeNotification.category.in_(TRADE_EMAIL_CATEGORIES),
            ).order_by(SuperAIDaytradeNotification.created_at).limit(20)).all())
        for row in rows:
            sent = await gmail_notification_dispatcher.dispatch(
                event_type=f"super_ai_daytrade_{row.category.lower()}",
                action=row.category,
                message=row.message,
                dedupe_key=f"email:{row.dedupe_key}",
                signal_id=row.dedupe_key,
                symbol=row.symbol,
                channel_name=SYSTEM_NAME,
            )
            if sent:
                with SessionLocal() as db:
                    stored = db.get(SuperAIDaytradeNotification, row.id)
                    if stored:
                        stored.email_sent = True
                        db.commit()

    async def _send_pending_signals(self, signal_keys: list[str]) -> None:
        for signal_key in signal_keys:
            with SessionLocal() as db:
                signal = db.scalar(select(AdaptiveSignal).where(AdaptiveSignal.signal_key == signal_key))
                if signal is None or signal.line_push_status != "pending":
                    continue
                if not _signal_has_super_ai_trade_record(db, signal):
                    signal.line_push_status = "skipped_no_trade_record"
                    db.commit()
                    continue
                candidate = None
                if signal.stock_code:
                    candidate = db.scalar(select(AdaptiveStockCandidate).where(
                        AdaptiveStockCandidate.stock_code == signal.stock_code,
                    ).order_by(AdaptiveStockCandidate.trade_date.desc()).limit(1))
                regime = db.scalar(select(MarketRegime).where(MarketRegime.is_current.is_(True)).limit(1))
                reasons = _list(signal.reasons_json)
                if signal.signal_type == "entry_confirmed" and not adaptive_entry_window_open(
                    datetime.now(UTC), True,
                ):
                    signal.line_push_status = "expired_after_entry_cutoff"
                    db.commit()
                    continue
                if signal.signal_type == "exit_triggered" and signal.stock_code:
                    message = (
                        "【超強AI當沖系統｜模擬賣出】\n\n"
                        f"股票：{signal.stock_code} {signal.stock_name or ''}\n"
                        f"模擬賣出價：{float(signal.price or 0):,.2f} 元\n"
                        f"動作：{signal.action}\n\n"
                        "原因：\n- " + "\n- ".join(reasons[:5])
                        + f"\n\n{PERSONAL_STRATEGY_SIMULATION_NOTE}"
                    )
                    symbol = signal.stock_code
                elif candidate and signal.signal_type == "entry_confirmed":
                    message = format_personal_strategy_simulation(
                        stock_name=candidate.stock_name,
                        symbol=candidate.stock_code,
                        entry_min=candidate.entry_price_low,
                        entry_max=candidate.entry_price_high,
                        stop_loss=candidate.stop_loss_price,
                        target_1=candidate.target_price_1,
                        target_2=candidate.target_price_2,
                    )
                    symbol = candidate.stock_code
                elif candidate:
                    if signal.signal_type == "next_day_watch":
                        message = (
                            "【超強AI當沖系統｜隔日觀察】\n\n"
                            f"股票：{candidate.stock_code} {candidate.stock_name}\n"
                            f"狀態：{signal.action}\n"
                            f"健康度：{float(candidate.health_score):.1f} 分\n\n"
                            "12:00 後禁止建立新部位，本訊息不是買進訊號。"
                            "下一交易日開盤後會依最新價格、量價與風控條件重新確認。"
                        )
                    else:
                        message = (
                            "【超強AI當沖系統｜候選監控】\n\n"
                            f"股票：{candidate.stock_code} {candidate.stock_name}\n"
                            f"狀態：{signal.action}\n"
                            f"健康度：{float(candidate.health_score):.1f} 分\n\n"
                            "目前尚未形成正式進場訊號，本訊息不是買進建議。"
                        )
                    symbol = candidate.stock_code
                else:
                    message = (
                        "【超強AI當沖系統｜市場狀態切換】\n\n"
                        f"目前狀態：{STRATEGY_NAMES.get(signal.strategy_type or '', signal.strategy_type or 'UNCERTAIN')}\n"
                        "觸發原因：\n- " + "\n- ".join(reasons[:8])
                        + "\n\n若為崩盤防守模式，系統不會發出直接買進訊號。"
                    )
                    symbol = "MARKET"
            sent = await push_ai_stock_message(
                event_type="adaptive_market" if signal.stock_code is None else "adaptive_stock",
                action=signal.action, message=message, signal_id=signal.signal_key,
                symbol=symbol, priority=0 if signal.strategy_type == "CRASH" else 6,
            )
            with SessionLocal() as db:
                stored = db.scalar(select(AdaptiveSignal).where(AdaptiveSignal.signal_key == signal_key))
                if stored:
                    stored.line_push_status = "sent" if sent else "deduplicated_or_disabled"
                    stored.sent_at = datetime.now(UTC) if sent else None
                    db.commit()

    def _position_monitor_requests(self) -> list[tuple[int, StockQuoteRequest]]:
        with SessionLocal() as db:
            rows = list(db.scalars(select(AdaptivePaperTrade).where(
                AdaptivePaperTrade.status == "open",
            )).all())
            requests: list[tuple[int, StockQuoteRequest]] = []
            for trade in rows:
                candidate = db.scalar(select(AdaptiveStockCandidate).where(
                    AdaptiveStockCandidate.stock_code == trade.stock_code,
                ).order_by(AdaptiveStockCandidate.trade_date.desc()).limit(1))
                mapping = None if candidate is not None else db.scalar(select(ElectronicIndustryMapping).where(
                    ElectronicIndustryMapping.stock_code == trade.stock_code,
                ).limit(1))
                market = (
                    candidate.market_type if candidate is not None
                    else mapping.market_type if mapping is not None
                    else "上市"
                )
                requests.append((
                    trade.id,
                    StockQuoteRequest(trade.stock_code, trade.stock_name, market),
                ))
            return requests

    def _record_quote_error(self, trade_id: int, request: StockQuoteRequest, at: datetime) -> None:
        last = self._last_quote_error_at.get(trade_id)
        if last is not None and at - last < timedelta(seconds=POSITION_MONITOR_ERROR_DEDUPE_SECONDS):
            return
        self._last_quote_error_at[trade_id] = at
        with SessionLocal() as db:
            record_notification(
                db,
                category="ERROR",
                level="warning",
                title=f"【{SYSTEM_NAME}｜行情異常】{request.symbol} {request.name}",
                message=(
                    f"股票：{request.symbol} {request.name}\n"
                    "原因：即時持倉監控未取得有效 TWSE MIS 報價，未使用舊價假成交。\n"
                    "處理：保留持倉並等待下一次即時報價。"
                ),
                dedupe_key=f"super-ai-quote-missing:{at.astimezone(TAIPEI).date()}:{trade_id}",
                symbol=request.symbol,
                symbol_name=request.name,
                at=at,
            )
            db.commit()

    async def _monitor_open_positions_once(self) -> dict[str, int]:
        current = datetime.now(UTC)
        session = _session(current)
        if session not in {"open", "after_close"}:
            return {"openTrades": 0, "updated": 0, "closed": 0, "quoteErrors": 0}
        requests = self._position_monitor_requests()
        if not requests:
            return {"openTrades": 0, "updated": 0, "closed": 0, "quoteErrors": 0}
        quote_requests = [request for _, request in requests]
        quotes = await official_market_data_provider.get_quotes(
            quote_requests,
            force_refresh=True,
        )
        signal_keys: list[str] = []
        updated = 0
        closed = 0
        quote_errors = 0
        with SessionLocal() as db:
            regime = db.scalar(select(MarketRegime).where(MarketRegime.is_current.is_(True)).limit(1))
            regime_key = regime.regime if regime is not None else "UNCERTAIN"
            for trade_id, request in requests:
                quote = quotes.get(request.symbol)
                if quote is None or not quote.is_realtime or quote.price <= 0:
                    quote_errors += 1
                    self._record_quote_error(trade_id, request, current)
                    continue
                candidate = db.scalar(select(AdaptiveStockCandidate).where(
                    AdaptiveStockCandidate.stock_code == request.symbol,
                ).order_by(AdaptiveStockCandidate.trade_date.desc()).limit(1))
                signal_key = update_open_trade_from_market_price(
                    db,
                    trade_id=trade_id,
                    price=Decimal(str(quote.price)),
                    at=current,
                    regime=regime_key,
                    candidate=candidate,
                )
                updated += 1
                if signal_key is not None:
                    signal_keys.append(signal_key)
                    closed += 1
            db.commit()
        if signal_keys:
            await self._send_pending_signals(signal_keys)
            await self._send_super_ai_emails()
        return {
            "openTrades": len(requests),
            "updated": updated,
            "closed": closed,
            "quoteErrors": quote_errors,
        }

    async def _run_position_monitor(self) -> None:
        while True:
            interval = max(1.0, min(get_settings().quote_refresh_seconds, 5.0))
            try:
                result = await self._monitor_open_positions_once()
                self._state["positionMonitor"] = {
                    "checkedAt": datetime.now(UTC).isoformat(),
                    **result,
                }
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Super AI position monitor cycle failed")
                self._state["positionMonitor"] = {
                    "checkedAt": datetime.now(UTC).isoformat(),
                    "openTrades": 0,
                    "updated": 0,
                    "closed": 0,
                    "quoteErrors": 0,
                    "error": str(error)[:300],
                }
            await asyncio.sleep(interval)

    async def _run(self) -> None:
        while True:
            interval = 180
            try:
                with SessionLocal() as db:
                    configured_interval = int(load_parameters(db)["automation.scan_interval_seconds"])
                    open_trade_count = db.scalar(select(AdaptivePaperTrade.id).where(
                        AdaptivePaperTrade.status == "open",
                    ).limit(1))
                    interval = 15 if open_trade_count is not None else min(30, max(10, configured_interval))
                current = datetime.now(UTC)
                seconds_until_open = _seconds_until_open(current)
                if seconds_until_open is not None:
                    interval = max(1, min(interval, seconds_until_open))
                self._state["nextScanSeconds"] = interval
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Adaptive electronic automation cycle failed")
                current = datetime.now(UTC)
                status = "running" if self._has_recent_success(current) else "error"
                self._state.update({
                    "status": status,
                    "lastError": f"scanner transient error: {str(error)[:450]}",
                })
            await asyncio.sleep(interval)


adaptive_electronic_automation = AdaptiveElectronicAutomation()
