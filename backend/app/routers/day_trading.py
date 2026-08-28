import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal, get_db
from ..models import (
    DayTradingAlert,
    DayTradingPosition,
    DayTradingRecommendationHistory,
    DayTradingScheduleSettings,
    DayTradingSettings,
    DayTradingSignal,
    DayTradingTrade,
)
from ..schemas import DayPositionClose, DayPositionCreate, DayPositionUpdate, DayTradingSettingsUpdate
from ..services.day_trading import (
    DATA_NOTICE,
    DISCLAIMER,
    day_trading_engine,
    entry_allowed,
    evaluate_position,
    prioritize_events,
)
from ..services.chip_flow_alerts import (
    electronic_chip_flow_alert_monitor,
    enrich_day_trading_large_order_confirmation,
)
from ..services.chip_flow_repository import ChipFlowRepository
from ..services.day_trading_cache import day_trading_cache
from ..services.day_trading_candidate_snapshots import replay_candidate_snapshots
from ..services.day_trading_restrictions import day_trading_restrictions
from ..services.day_trading_strategies import (
    route_signals_to_active_robot,
    strategy_context,
    strategy_eligible_signals,
)
from ..services.day_trading_automation import (
    AUTOMATION_RANKED_CANDIDATES_CACHE_KEY,
    AUTOMATION_SELECTION_CACHE_KEY,
    day_trading_automation,
)
from ..services.automated_position_tracker import (
    AUTOMATION_PERFORMANCE_START,
    AUTOMATION_QUANTITY_LOTS,
    AUTOMATION_USER_ID,
    DYNAMIC_AUTOMATION_PERFORMANCE_START,
    DYNAMIC_AUTOMATION_USER_ID,
    DYNAMIC_STRATEGY_KEY,
    FIXED_STRATEGY_KEY,
    automation_capital_state,
    automation_strategy,
)
from ..services.day_trading_schedule import (
    DAY_TRADING_CLOSE_REMINDER,
    DAY_TRADING_ENTRY_CUTOFF,
    DAY_TRADING_LONG_ENTRY_CUTOFF,
    DAY_TRADING_SIGNAL_START,
    DAY_TRADING_FORCED_EXIT,
    MIN_DAY_TRADING_TURNOVER,
    MIN_DAY_TRADING_VOLUME_SHARES,
    TradingScheduleConfig,
    stable_recommendation_selector,
    trading_session_state,
)
from ..services.line_messaging import line_notification_dispatcher

router = APIRouter(prefix="/day-trading", tags=["day-trading"])
logger = logging.getLogger(__name__)
settings = get_settings()
# Keep this module in the backend image when Railway deploys day-trading route-only changes.
TAIPEI = ZoneInfo("Asia/Taipei")
DAY_TRADING_COMMISSION_RATE = 0.001425
DAY_TRADING_COMMISSION_DISCOUNT = 0.2
DAY_TRADING_COMMISSION_DISCOUNT_LABEL = "2折"


def _user_id(x_user_id: str | None = Header(default=None, min_length=8, max_length=80)) -> str:
    return x_user_id or "demo-user"


def _monthly_period(month: str = "") -> tuple[str, datetime, datetime]:
    local_now = datetime.now(TAIPEI)
    year, month_number = (
        (int(month[:4]), int(month[5:7]))
        if month
        else (local_now.year, local_now.month)
    )
    start_local = datetime(year, month_number, 1, tzinfo=TAIPEI)
    if month_number == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=TAIPEI)
    else:
        end_local = datetime(year, month_number + 1, 1, tzinfo=TAIPEI)
    return f"{year:04d}-{month_number:02d}", start_local.astimezone(UTC), end_local.astimezone(UTC)


def _daily_period() -> tuple[str, datetime, datetime]:
    local_today = datetime.now(TAIPEI).date()
    start_local = datetime.combine(local_today, datetime.min.time(), tzinfo=TAIPEI)
    end_local = start_local + timedelta(days=1)
    return local_today.isoformat(), start_local.astimezone(UTC), end_local.astimezone(UTC)


def _performance_start(user_id: str, period_start: datetime) -> datetime:
    if user_id == AUTOMATION_USER_ID:
        return max(period_start, AUTOMATION_PERFORMANCE_START)
    if user_id == DYNAMIC_AUTOMATION_USER_ID:
        return max(period_start, DYNAMIC_AUTOMATION_PERFORMANCE_START)
    return period_start


def _performance_summary(
    items: list[DayTradingTrade],
    open_positions: list[DayTradingPosition],
) -> dict[str, Any]:
    wins = [item for item in items if item.profit > 0]
    losses = [item for item in items if item.profit < 0]
    gross_wins = sum(item.profit for item in wins)
    gross_losses = abs(sum(item.profit for item in losses))
    realized_profit = round(sum(item.profit for item in items), 2)
    unrealized_profit = 0.0
    long_unrealized_profit = 0.0
    short_unrealized_profit = 0.0
    for position in open_positions:
        direction_factor = 1 if position.direction == "long" else -1
        position_unrealized = (
            position.current_price - position.entry_price
        ) * position.quantity * 1000 * direction_factor
        unrealized_profit += position_unrealized
        if position.direction == "long":
            long_unrealized_profit += position_unrealized
        else:
            short_unrealized_profit += position_unrealized
    long_realized_profit = round(sum(
        item.profit for item in items if item.direction == "long"
    ), 2)
    short_realized_profit = round(sum(
        item.profit for item in items if item.direction == "short"
    ), 2)
    unrealized_profit = round(unrealized_profit, 2)
    fee = round(sum(item.fee for item in items), 2)
    tax = round(sum(item.tax for item in items), 2)
    slippage = round(sum(item.slippage for item in items), 2)
    gross_commission = round(fee / DAY_TRADING_COMMISSION_DISCOUNT, 2) if fee else 0
    commission_rebate = round(max(0, gross_commission - fee), 2)
    consecutive_losses = 0
    maximum_consecutive_losses = 0
    for item in sorted(items, key=lambda trade: trade.exit_time):
        consecutive_losses = consecutive_losses + 1 if item.profit < 0 else 0
        maximum_consecutive_losses = max(maximum_consecutive_losses, consecutive_losses)
    return {
        "tradeCount": len(items), "winRate": round(len(wins) / len(items) * 100, 2) if items else 0,
        "wins": len(wins), "losses": len(losses), "breakeven": len(items) - len(wins) - len(losses),
        "totalProfit": realized_profit,
        "realizedProfit": realized_profit, "unrealizedProfit": unrealized_profit,
        "totalPnl": round(realized_profit + unrealized_profit, 2),
        "grossProfit": round(realized_profit + fee + tax + slippage, 2),
        "fee": fee, "tax": tax, "slippage": slippage,
        "tradingCost": round(fee + tax + slippage, 2),
        "commissionDiscount": DAY_TRADING_COMMISSION_DISCOUNT,
        "commissionDiscountLabel": DAY_TRADING_COMMISSION_DISCOUNT_LABEL,
        "grossCommission": gross_commission,
        "commissionRebate": commission_rebate,
        "rebateAccumulated": commission_rebate,
        "openPositionCount": len(open_positions),
        "averageProfit": round(realized_profit / len(items), 2) if items else 0,
        "maxLoss": min([item.profit for item in items], default=0),
        "maxConsecutiveLosses": maximum_consecutive_losses,
        # Keep the original realized-only fields for older clients.
        "longProfit": long_realized_profit,
        "shortProfit": short_realized_profit,
        "longRealizedProfit": long_realized_profit,
        "longUnrealizedProfit": round(long_unrealized_profit, 2),
        "longTotalPnl": round(long_realized_profit + long_unrealized_profit, 2),
        "longTradeCount": sum(1 for item in items if item.direction == "long"),
        "longOpenPositionCount": sum(1 for item in open_positions if item.direction == "long"),
        "shortRealizedProfit": short_realized_profit,
        "shortUnrealizedProfit": round(short_unrealized_profit, 2),
        "shortTotalPnl": round(short_realized_profit + short_unrealized_profit, 2),
        "shortTradeCount": sum(1 for item in items if item.direction == "short"),
        "shortOpenPositionCount": sum(1 for item in open_positions if item.direction == "short"),
        "profitFactor": round(gross_wins / gross_losses, 2) if gross_losses else (gross_wins if gross_wins else 0),
    }


def _sync_signals(db: Session, signals: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    signals = day_trading_engine.signals() if signals is None else signals
    signals = day_trading_restrictions.filter_candidates(signals)
    for payload in signals:
        item = db.get(DayTradingSignal, payload["id"])
        if item is None:
            try:
                # A browser can open the regime, signal and ranking endpoints at
                # the same time. Keep a concurrent insert inside a savepoint so
                # one request cannot poison the whole transaction when another
                # request creates the same signal first.
                with db.begin_nested():
                    candidate = DayTradingSignal(id=payload["id"])
                    db.add(candidate)
                    db.flush()
                item = candidate
            except IntegrityError:
                item = db.get(DayTradingSignal, payload["id"])
        if item is None:
            continue
        item.symbol = payload["symbol"]
        item.stock_name = payload["stockName"]
        item.market = payload["market"]
        item.direction = payload["direction"]
        item.action = payload["action"]
        item.current_price = payload["price"]
        item.entry_min = payload["entryMin"]
        item.entry_max = payload["entryMax"]
        item.stop_loss = payload["stopLoss"]
        item.target_1 = payload["target1"]
        item.target_2 = payload["target2"]
        item.confidence_score = payload["confidenceScore"]
        item.health_score = payload["healthScore"]
        item.risk_reward_ratio = payload["riskRewardRatio"]
        item.reasons_json = json.dumps(payload["reasons"], ensure_ascii=False)
        item.warnings_json = json.dumps(payload["warnings"], ensure_ascii=False)
        item.generated_at = datetime.fromisoformat(payload["generatedAt"])
        item.expires_at = datetime.fromisoformat(payload["expiresAt"])
        item.status = payload["status"]
        item.data_source = payload["dataSource"]
        item.quote_timestamp = datetime.fromisoformat(payload["quoteTimestamp"])
    db.commit()
    return signals


def _settings(db: Session, user_id: str) -> DayTradingSettings:
    item = db.scalar(select(DayTradingSettings).where(DayTradingSettings.user_id == user_id))
    if item is None:
        item = DayTradingSettings(user_id=user_id)
        db.add(item)
        db.commit()
        db.refresh(item)
    elif (
        item.minimum_volume < MIN_DAY_TRADING_VOLUME_SHARES
        or item.minimum_turnover < MIN_DAY_TRADING_TURNOVER
        or item.latest_entry_time != DAY_TRADING_ENTRY_CUTOFF
        or item.close_reminder_time != DAY_TRADING_CLOSE_REMINDER
    ):
        item.minimum_volume = max(item.minimum_volume, MIN_DAY_TRADING_VOLUME_SHARES)
        item.minimum_turnover = max(item.minimum_turnover, MIN_DAY_TRADING_TURNOVER)
        item.latest_entry_time = DAY_TRADING_ENTRY_CUTOFF
        item.close_reminder_time = DAY_TRADING_CLOSE_REMINDER
        db.commit()
        db.refresh(item)
    return item


def _schedule_settings(db: Session, user_id: str) -> DayTradingScheduleSettings:
    item = db.scalar(select(DayTradingScheduleSettings).where(DayTradingScheduleSettings.user_id == user_id))
    if item is None:
        item = DayTradingScheduleSettings(user_id=user_id)
        db.add(item)
        db.commit()
        db.refresh(item)
    return item


def _holiday_dates() -> frozenset[date]:
    values: set[date] = set()
    for value in settings.twse_holidays.split(","):
        try:
            values.add(date.fromisoformat(value.strip()))
        except ValueError:
            continue
    return frozenset(values)


def _schedule_config(risk: DayTradingSettings, schedule: DayTradingScheduleSettings) -> TradingScheduleConfig:
    return TradingScheduleConfig(
        timezone=schedule.timezone,
        preheat_time=schedule.preheat_time,
        stock_pool_time=schedule.stock_pool_time,
        health_check_time=schedule.health_check_time,
        market_open_time=schedule.market_open_time,
        signal_start_time=DAY_TRADING_SIGNAL_START,
        latest_entry_time=DAY_TRADING_ENTRY_CUTOFF,
        close_reminder_time=DAY_TRADING_CLOSE_REMINDER,
        market_close_time=DAY_TRADING_FORCED_EXIT,
        warmup_minutes=schedule.warmup_minutes,
        recommendation_refresh_seconds=schedule.recommendation_refresh_seconds,
        replacement_score_gap=schedule.replacement_score_gap,
        minimum_retention_minutes=schedule.minimum_retention_minutes,
        minimum_live_samples=schedule.minimum_live_samples,
        minimum_risk_reward=risk.minimum_risk_reward,
        maximum_spread=risk.maximum_spread,
        minimum_volume=max(risk.minimum_volume, MIN_DAY_TRADING_VOLUME_SHARES),
        minimum_turnover=max(risk.minimum_turnover, MIN_DAY_TRADING_TURNOVER),
        maximum_stop_distance=schedule.maximum_stop_distance,
        holidays=_holiday_dates(),
    )


def _settings_payload(item: DayTradingSettings, schedule: DayTradingScheduleSettings) -> dict[str, Any]:
    return {
        "capital": item.capital, "maxRiskPerTrade": item.max_risk_per_trade,
        "maxDailyLoss": item.max_daily_loss, "maxDailyTrades": item.max_daily_trades,
        "maxPositionPercentage": item.max_position_percentage,
        "maxConsecutiveLosses": item.max_consecutive_losses,
        "minimumRiskReward": item.minimum_risk_reward, "maximumSpread": item.maximum_spread,
        "minimumVolume": max(item.minimum_volume, MIN_DAY_TRADING_VOLUME_SHARES),
        "minimumTurnover": max(item.minimum_turnover, MIN_DAY_TRADING_TURNOVER),
        "latestEntryTime": DAY_TRADING_ENTRY_CUTOFF,
        "shortEntryCutoffTime": DAY_TRADING_ENTRY_CUTOFF,
        "longEntryCutoffTime": DAY_TRADING_LONG_ENTRY_CUTOFF,
        "closeReminderTime": DAY_TRADING_CLOSE_REMINDER,
        "notificationEnabled": item.notification_enabled, "soundEnabled": item.sound_enabled,
        "entryNotification": item.entry_notification, "exitNotification": item.exit_notification,
        "stopNotification": item.stop_notification, "targetNotification": item.target_notification,
        "dataAlertNotification": item.data_alert_notification,
        "highConfidenceOnly": item.high_confidence_only,
        "minimumConfidence": item.minimum_confidence,
        "notificationCooldown": item.notification_cooldown, "repeatCount": item.repeat_count,
        "timezone": schedule.timezone, "preheatTime": schedule.preheat_time,
        "stockPoolTime": schedule.stock_pool_time, "healthCheckTime": schedule.health_check_time,
        "marketOpenTime": schedule.market_open_time, "signalStartTime": DAY_TRADING_SIGNAL_START,
        "marketCloseTime": DAY_TRADING_FORCED_EXIT,
        "warmupMinutes": schedule.warmup_minutes,
        "recommendationRefreshSeconds": schedule.recommendation_refresh_seconds,
        "replacementScoreGap": schedule.replacement_score_gap,
        "minimumRetentionMinutes": schedule.minimum_retention_minutes,
        "minimumLiveSamples": schedule.minimum_live_samples,
        "maximumStopDistance": schedule.maximum_stop_distance,
    }


def _selection(
    db: Session,
    user_id: str,
    *,
    raw_signals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    force_candidate_ranking: bool = False,
) -> dict[str, Any]:
    risk = _settings(db, user_id)
    schedule_settings = _schedule_settings(db, user_id)
    config = _schedule_config(risk, schedule_settings)
    regime = day_trading_engine.market_regime()
    infrastructure = {
        "quoteSource": (
            "healthy" if regime["dataStatus"] == "normal"
            else "closed" if regime["dataStatus"] == "closed"
            else "error"
        ),
        "redis": day_trading_cache.status,
        "database": "healthy",
        "stream": "healthy",
    }
    infrastructure_ok = all(
        value in {"healthy", "closed", "memory_fallback"} for value in infrastructure.values()
    )
    session = trading_session_state(
        config,
        now,
        data_status=regime["dataStatus"],
        quote_samples=day_trading_engine.sample_count,
        infrastructure_ok=infrastructure_ok,
    )
    strategy = strategy_context(regime, session)
    regime = {**regime, **strategy}
    # When risk controls already block formal signals (for example a stale or
    # disconnected quote feed), candidate generation cannot affect the public
    # result. Avoid the expensive 276-symbol signal and chip-history scan so
    # status pages stay responsive while the upstream feed is unavailable.
    ranked_candidates: list[dict[str, Any]] = []
    if session["formalSignalsAllowed"] or force_candidate_ranking:
        # Selection endpoints are read paths. Persisting every temporary candidate
        # here caused concurrent page requests to race on the signal primary key.
        filtered_signals = day_trading_restrictions.enrich_short_eligibility(
            day_trading_restrictions.filter_candidates(
                day_trading_engine.signals() if raw_signals is None else raw_signals,
            ),
        )
        candidates = enrich_day_trading_large_order_confirmation(
            filtered_signals,
            ChipFlowRepository(db),
            electronic_chip_flow_alert_monitor.rules,
            as_of=now or datetime.now(UTC),
        )
        candidates = strategy_eligible_signals(route_signals_to_active_robot(
            candidates,
            strategy["activeRobot"],
        ))
        open_ids = set(db.scalars(select(DayTradingPosition.signal_id).where(
            DayTradingPosition.user_id == user_id,
            DayTradingPosition.status == "open",
            DayTradingPosition.signal_id.is_not(None),
        )).all())
        official, ranked_candidates = stable_recommendation_selector.select(
            user_id,
            candidates,
            config,
            session,
            open_signal_ids={str(value) for value in open_ids if value},
            now=now,
        )
    else:
        official = []
    summary = (
        f"已選出 {len(official)} 檔當沖機會"
        if official
        else "目前沒有符合風控條件的股票，持續掃描中"
        if session["phase"] == "scanning"
        else session["statusMessage"]
    )
    return {
        "recommended": official,
        "candidates": ranked_candidates,
        "totalRecommended": len(official),
        "maximumRecommendations": config.maximum_recommendations,
        "session": session,
        "infrastructure": infrastructure,
        "summary": summary,
        "regime": regime,
    }


def _selection_fallback(
    reason: Exception,
    *,
    now: datetime | None = None,
    stream_healthy: bool = True,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    reason_text = str(reason) or reason.__class__.__name__
    reason_text = reason_text[:240]
    try:
        config = TradingScheduleConfig(
            timezone=settings.twse_timezone,
            holidays=_holiday_dates(),
        )
    except Exception:
        config = TradingScheduleConfig()
    try:
        quote_samples = day_trading_engine.sample_count
    except Exception:
        quote_samples = 0
    session = trading_session_state(
        config,
        current,
        data_status="source_error",
        quote_samples=quote_samples,
        infrastructure_ok=False,
        recovering=True,
    )
    regime: dict[str, Any] = {
        "direction": "data_anomaly",
        "directionLabel": "資料降級",
        "score": 0,
        "environmentScore": 0,
        "environmentLabel": "核心資料降級",
        "preferredDirection": "暫停新進場",
        "shortRestriction": "核心資料降級，暫停放空與新進場",
        "risk": "高",
        "longPermission": 0,
        "shortPermission": 0,
        "suitableStrategies": ["等待資料恢復"],
        "forbiddenStrategies": ["新增做多", "新增放空", "追價進場"],
        "reasons": [
            "當沖核心選股流程暫時失敗，系統已切換降級模式",
            "正式進場訊號已暫停，避免依賴不完整資料",
            f"錯誤摘要：{reason_text}",
        ],
        "dataStatus": "source_error",
        "dataDelaySeconds": 999,
        "dataSource": "day-trading safe fallback",
        "marketOpen": session["phase"] in {"warmup", "scanning", "long_only", "entry_closed", "closing"},
        "session": "09:00～13:30",
        "updatedAt": current.isoformat(),
        "metrics": {
            "weightedIndex": "—",
            "otcIndex": "—",
            "indexFutures": "—",
            "vwap": "—",
            "oneMinuteTrend": "—",
            "fiveMinuteTrend": "—",
            "fifteenMinuteTrend": "—",
            "advancers": 0,
            "decliners": 0,
            "limitUp": 0,
            "limitDown": 0,
            "largeOrderForce": "—",
            "smallOrderForce": "—",
            "relativeVolume": 0,
            "strongIndustries": [],
            "weakIndustries": [],
            "breadth": 0,
            "volatility": "資料不足",
        },
        "mode": "demo",
        "dataNotice": "當沖核心資料暫時降級；正式進場已暫停。",
        "degraded": True,
        "fallbackReason": reason_text,
        "fallbackAt": current.isoformat(),
    }
    try:
        strategy = strategy_context(regime, session)
    except Exception:
        strategy = {
            "activeRobot": {
                "id": "safe-fallback",
                "name": "安全降級模式",
                "direction": "both",
                "directionLabel": "多空暫停",
                "useWhen": "核心資料異常時",
                "description": "保留頁面與監控狀態，但不產生正式進場訊號。",
                "entryRule": "等待行情、選股與資料庫恢復正常。",
                "avoidRule": "資料未恢復前不追價、不加碼、不放空。",
                "confidence": 0,
                "confidenceLabel": "暫停",
                "status": "paused",
                "statusLabel": "核心資料降級",
                "reasons": regime["reasons"],
            },
            "strategyRobots": [],
        }
    infrastructure = {
        "quoteSource": "error",
        "redis": day_trading_cache.status,
        "database": "error",
        "stream": "healthy" if stream_healthy else "degraded",
    }
    summary = "當沖核心資料暫時降級，正式進場已暫停。"
    return {
        "recommended": [],
        "candidates": [],
        "totalRecommended": 0,
        "maximumRecommendations": config.maximum_recommendations,
        "session": session,
        "infrastructure": infrastructure,
        "summary": summary,
        "regime": {**regime, **strategy},
        "degraded": True,
        "fallbackReason": reason_text,
        "fallbackAt": current.isoformat(),
    }


def _safe_selection(
    db: Session,
    user_id: str,
    *,
    raw_signals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    force_candidate_ranking: bool = False,
    stream_healthy: bool = True,
) -> dict[str, Any]:
    try:
        return _selection(
            db,
            user_id,
            raw_signals=raw_signals,
            now=now,
            force_candidate_ranking=force_candidate_ranking,
        )
    except Exception as reason:
        try:
            db.rollback()
        except Exception:
            logger.exception("Failed to roll back after day-trading selection failure")
        logger.exception("Day-trading selection failed; returning degraded fallback")
        return _selection_fallback(reason, now=now, stream_healthy=stream_healthy)


def _same_trading_date(payload: dict[str, Any]) -> bool:
    trading_date, _, _ = _daily_period()
    return str(payload.get("tradingDate") or "") == trading_date


def _payload_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _open_signal_ids(db: Session, user_id: str) -> set[str]:
    return {
        str(value)
        for value in db.scalars(select(DayTradingPosition.signal_id).where(
            DayTradingPosition.user_id == user_id,
            DayTradingPosition.status == "open",
            DayTradingPosition.signal_id.is_not(None),
        )).all()
        if value
    }


def _automation_cached_selection(db: Session, user_id: str) -> dict[str, Any] | None:
    payload = day_trading_cache.get(AUTOMATION_SELECTION_CACHE_KEY)
    if not isinstance(payload, dict) or not _same_trading_date(payload):
        return None
    session = payload.get("session")
    regime = payload.get("regime")
    if not isinstance(session, dict) or not isinstance(regime, dict):
        return None
    open_ids = _open_signal_ids(db, user_id)
    recommended = [
        item
        for item in _payload_list(payload.get("recommended"))
        if str(item.get("id") or "") not in open_ids
    ]
    candidates = _payload_list(payload.get("candidates"))
    return {
        "recommended": recommended,
        "candidates": candidates,
        "totalRecommended": len(recommended),
        "maximumRecommendations": int(payload.get("maximumRecommendations") or 10),
        "session": session,
        "infrastructure": {
            "quoteSource": (
                "healthy" if regime.get("dataStatus") == "normal"
                else "closed" if regime.get("dataStatus") == "closed"
                else "error"
            ),
            "redis": day_trading_cache.status,
            "database": "healthy",
            "stream": "healthy",
        },
        "summary": str(payload.get("summary") or "目前沒有符合風控條件的股票，持續掃描中"),
        "regime": regime,
        "selectionSource": str(payload.get("source") or "automation_cache"),
        "updatedAt": payload.get("updatedAt"),
    }


def _automation_cached_rankings(direction: str) -> dict[str, Any] | None:
    payload = day_trading_cache.get(AUTOMATION_RANKED_CANDIDATES_CACHE_KEY)
    if not isinstance(payload, dict) or not _same_trading_date(payload):
        return None
    rows = _payload_list(payload.get("items"))
    if direction != "all":
        rows = [item for item in rows if item.get("direction") == direction]
    return {
        "items": [{**item, "rank": index + 1} for index, item in enumerate(rows)],
        "total": len(rows),
        "recommendedTotal": int(payload.get("recommendedTotal") or 0),
        "maximumRecommendations": int(payload.get("maximumRecommendations") or 10),
        "summary": str(payload.get("summary") or "目前沒有符合風控條件的股票，持續掃描中"),
        "degraded": False,
        "fallbackReason": None,
        "fallbackAt": None,
        "rankingSource": str(payload.get("source") or "automation_cache"),
        "updatedAt": payload.get("updatedAt"),
    }


def _market_regime_payload(selection: dict[str, Any]) -> dict[str, Any]:
    degraded = bool(selection.get("degraded") or selection["regime"].get("degraded"))
    return {
        **selection["regime"],
        "marketOpen": selection["session"]["phase"] in {"warmup", "scanning", "long_only", "entry_closed", "closing"},
        "automation": selection["session"],
        "infrastructure": selection["infrastructure"],
        "recommendationSummary": selection["summary"],
        "recommendedCount": selection["totalRecommended"],
        "maximumRecommendations": selection["maximumRecommendations"],
        "supervisor": day_trading_automation.state,
        "mode": selection["regime"].get("mode", "demo"),
        "dataNotice": selection["regime"].get("dataNotice", DATA_NOTICE),
        "disclaimer": DISCLAIMER,
        "cacheMode": day_trading_cache.mode,
        "degraded": degraded,
        "fallbackReason": selection.get("fallbackReason") or selection["regime"].get("fallbackReason"),
        "fallbackAt": selection.get("fallbackAt") or selection["regime"].get("fallbackAt"),
    }


def _signal_selection_payload(selection: dict[str, Any]) -> dict[str, Any]:
    degraded = bool(selection.get("degraded") or selection["regime"].get("degraded"))
    return {
        "recommended": selection["recommended"],
        "candidates": selection["candidates"],
        "totalRecommended": selection["totalRecommended"],
        "maximumRecommendations": selection["maximumRecommendations"],
        "supervisor": day_trading_automation.state,
        "summary": selection["summary"],
        "degraded": degraded,
        "fallbackReason": selection.get("fallbackReason") or selection["regime"].get("fallbackReason"),
        "fallbackAt": selection.get("fallbackAt") or selection["regime"].get("fallbackAt"),
    }


def _position_payload(item: DayTradingPosition) -> dict[str, Any]:
    direction_factor = 1 if item.direction == "long" else -1
    gross = (item.current_price - item.entry_price) * item.quantity * 1000 * direction_factor
    opened_at = item.opened_at if item.opened_at.tzinfo else item.opened_at.replace(tzinfo=UTC)
    strategy = automation_strategy(item.user_id) if item.user_id in {AUTOMATION_USER_ID, DYNAMIC_AUTOMATION_USER_ID} else None
    return {
        "id": item.id, "signalId": item.signal_id, "symbol": item.symbol,
        "stockName": item.stock_name, "direction": item.direction,
        "directionLabel": "多單" if item.direction == "long" else "空單",
        "entryPrice": item.entry_price, "quantity": item.quantity,
        "openedAt": item.opened_at.isoformat(), "currentPrice": item.current_price,
        "unrealizedProfit": round(gross, 2),
        "returnPercentage": round((item.current_price - item.entry_price) / item.entry_price * 100 * direction_factor, 2),
        "stopLoss": item.stop_loss, "target1": item.target_1, "target2": item.target_2,
        "trailingStop": item.trailing_stop, "healthScore": item.health_score,
        "latestAction": item.latest_action, "status": item.status,
        "soundEnabled": item.sound_enabled,
        "closedAt": item.closed_at.isoformat() if item.closed_at else None,
        "exitPrice": item.exit_price, "realizedProfit": item.realized_profit,
        "holdingSeconds": max(0, round((datetime.now(UTC) - opened_at).total_seconds())),
        "updatedAt": datetime.now(UTC).isoformat(),
        "automationStrategy": strategy["key"] if strategy else None,
        "automationStrategyLabel": strategy["label"] if strategy else None,
        "holdingPeriod": item.holding_period,
        "holdingPeriodLabel": "隔日多單" if item.holding_period == "overnight_long" else "當沖",
        "entryConfidence": item.entry_confidence,
        "strategyConfidence": item.strategy_confidence,
        "overnightEligible": (
            item.direction == "long"
            and item.entry_confidence >= 85
            and item.strategy_confidence >= 85
        ),
    }


def _alert_payload(item: DayTradingAlert) -> dict[str, Any]:
    return {
        "id": item.id, "positionId": item.position_id, "signalId": item.signal_id,
        "level": item.alert_level, "type": item.alert_type, "title": item.title,
        "message": item.message, "action": item.action, "reason": item.reason,
        "price": item.price, "createdAt": item.created_at.isoformat(),
        "readAt": item.read_at.isoformat() if item.read_at else None,
    }


def _trade_payload(item: DayTradingTrade) -> dict[str, Any]:
    return {
        "id": item.id, "symbol": item.symbol, "stockName": item.stock_name,
        "direction": item.direction, "entryTime": item.entry_time.isoformat(),
        "entryPrice": item.entry_price, "exitTime": item.exit_time.isoformat(),
        "exitPrice": item.exit_price, "quantity": item.quantity, "fee": item.fee,
        "tax": item.tax, "slippage": item.slippage, "profit": item.profit,
        "returnPercentage": item.return_percentage, "maxProfit": item.max_profit,
        "maxLoss": item.max_loss, "entryReason": item.entry_reason,
        "exitReason": item.exit_reason, "strategyName": item.strategy_name,
        "followedSignal": item.followed_signal,
    }


@router.get("/market-regime")
def get_market_regime(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    selection = _automation_cached_selection(db, user_id) or _safe_selection(db, user_id)
    payload = _market_regime_payload(selection)
    day_trading_cache.put("market-regime", payload)
    return payload


@router.get("/signals")
def get_signals(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    selection = _automation_cached_selection(db, user_id) or _safe_selection(db, user_id)
    signals = selection["recommended"]
    day_trading_cache.put(f"signals:{user_id}", selection)
    return {
        "items": signals, "candidates": signals, "total": len(signals),
        "maximum": selection["maximumRecommendations"], "summary": selection["summary"],
        "automation": selection["session"],
        "mode": selection["regime"].get("mode", "demo"),
        "dataNotice": selection["regime"].get("dataNotice", DATA_NOTICE),
        "disclaimer": DISCLAIMER,
        "degraded": bool(selection.get("degraded") or selection["regime"].get("degraded")),
        "fallbackReason": selection.get("fallbackReason") or selection["regime"].get("fallbackReason"),
        "fallbackAt": selection.get("fallbackAt") or selection["regime"].get("fallbackAt"),
        "selectionSource": selection.get("selectionSource", "live_fallback"),
        "updatedAt": selection.get("updatedAt") or datetime.now(UTC).isoformat(),
    }


@router.get("/signals/today")
def get_today_signals(db: Session = Depends(get_db)) -> dict[str, Any]:
    trading_date, _, _ = _daily_period()
    rows = db.scalars(
        select(DayTradingRecommendationHistory)
        .where(DayTradingRecommendationHistory.trading_date == date.fromisoformat(trading_date))
        .order_by(DayTradingRecommendationHistory.recommended_at.desc())
    ).all()
    signal_ids = [row.signal_id for row in rows]
    strategy_positions = list(db.scalars(
        select(DayTradingPosition).where(
            DayTradingPosition.user_id.in_((AUTOMATION_USER_ID, DYNAMIC_AUTOMATION_USER_ID)),
            DayTradingPosition.signal_id.in_(signal_ids),
        )
    ).all()) if signal_ids else []
    positions_by_strategy_signal = {
        (position.user_id, str(position.signal_id)): position
        for position in strategy_positions
        if position.signal_id
    }
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        stored_allocations = payload.get("strategyAllocations")
        if not isinstance(stored_allocations, dict):
            stored_allocations = {}
        fixed_position = positions_by_strategy_signal.get((AUTOMATION_USER_ID, row.signal_id))
        dynamic_position = positions_by_strategy_signal.get((DYNAMIC_AUTOMATION_USER_ID, row.signal_id))
        fixed_stored = stored_allocations.get(FIXED_STRATEGY_KEY, {})
        dynamic_stored = stored_allocations.get(DYNAMIC_STRATEGY_KEY, {})
        fixed_quantity = (
            float(fixed_stored.get("quantityLots", 0))
            if isinstance(fixed_stored, dict) and "quantityLots" in fixed_stored
            else float(fixed_position.quantity) if fixed_position is not None
            else float(payload.get("recommendedQuantityLots", AUTOMATION_QUANTITY_LOTS))
        )
        dynamic_quantity = (
            float(dynamic_stored.get("quantityLots", 0))
            if isinstance(dynamic_stored, dict) and "quantityLots" in dynamic_stored
            else float(dynamic_position.quantity) if dynamic_position is not None
            else 0.0
        )
        fixed_status = (
            "已建立模擬持倉" if fixed_position is not None
            else str(fixed_stored.get("status", payload.get("trackingStatus", "重複確認，未加碼")))
            if isinstance(fixed_stored, dict) else "重複確認，未加碼"
        )
        dynamic_status = (
            "已建立模擬持倉" if dynamic_position is not None
            else str(dynamic_stored.get("status", "當時新版策略尚未啟用"))
            if isinstance(dynamic_stored, dict) else "當時新版策略尚未啟用"
        )
        strategy_allocations = {
            FIXED_STRATEGY_KEY: {
                "key": FIXED_STRATEGY_KEY,
                "label": "原版固定 2 張",
                "quantityLots": fixed_quantity if fixed_position is not None or fixed_quantity > 0 else 0,
                "allocatedCapital": (
                    float(fixed_stored.get("allocatedCapital", 0))
                    if isinstance(fixed_stored, dict) and "allocatedCapital" in fixed_stored
                    else round(float(payload.get("price", 0)) * fixed_quantity * 1000, 2)
                ) if fixed_position is not None or fixed_quantity > 0 else 0,
                "status": fixed_status,
            },
            DYNAMIC_STRATEGY_KEY: {
                "key": DYNAMIC_STRATEGY_KEY,
                "label": "新版 500 萬動態配置",
                "quantityLots": dynamic_quantity if dynamic_position is not None or dynamic_quantity > 0 else 0,
                "allocatedCapital": (
                    float(dynamic_stored.get("allocatedCapital", 0))
                    if isinstance(dynamic_stored, dict) and "allocatedCapital" in dynamic_stored
                    else round(float(payload.get("price", 0)) * dynamic_quantity * 1000, 2)
                ) if dynamic_position is not None or dynamic_quantity > 0 else 0,
                "status": dynamic_status,
            },
        }
        items.append({
            **payload,
            "id": row.signal_id,
            "symbol": row.symbol,
            "stockName": row.stock_name,
            "market": row.market,
            "direction": row.direction,
            "action": row.action,
            "recommendedQuantityLots": fixed_quantity,
            "trackedQuantityLots": strategy_allocations[FIXED_STRATEGY_KEY]["quantityLots"],
            "trackingStatus": fixed_status,
            "strategyAllocations": strategy_allocations,
            "recommendedAt": row.recommended_at.isoformat(),
            "rank": index + 1,
        })
    return {"tradingDate": trading_date, "items": items, "total": len(items)}


@router.get("/candidate-replay/today")
def get_today_candidate_replay(
    limit: int = Query(default=200, ge=1, le=500),
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    trading_date, _, _ = _daily_period()
    risk = _settings(db, user_id)
    schedule_settings = _schedule_settings(db, user_id)
    config = _schedule_config(risk, schedule_settings)
    items = replay_candidate_snapshots(
        db,
        config,
        trading_date=date.fromisoformat(trading_date),
        limit=limit,
    )
    formal_items = [item for item in items if item["wouldBeOfficialRecommendation"]]
    return {
        "tradingDate": trading_date,
        "items": items,
        "total": len(items),
        "formalItems": formal_items,
        "formalTotal": len(formal_items),
        "updatedAt": datetime.now(UTC).isoformat(),
    }


@router.get("/signals/{signal_id}")
def get_signal(signal_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    signals = _sync_signals(db)
    payload = next((item for item in signals if item["id"] == signal_id), None)
    if payload is None:
        raise HTTPException(status_code=404, detail="訊號不存在")
    return payload


@router.get("/rankings")
def get_rankings(
    direction: str = Query(default="all", pattern=r"^(all|long|short)$"),
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cached = _automation_cached_rankings(direction)
    if cached is not None:
        return cached
    selection = _safe_selection(db, user_id, force_candidate_ranking=True)
    rows = selection["candidates"]
    if direction != "all":
        rows = [item for item in rows if item["direction"] == direction]
    return {
        "items": [{**item, "rank": index + 1} for index, item in enumerate(rows)],
        "total": len(rows), "recommendedTotal": selection["totalRecommended"],
        "maximumRecommendations": selection["maximumRecommendations"],
        "summary": selection["summary"],
        "degraded": bool(selection.get("degraded") or selection["regime"].get("degraded")),
        "fallbackReason": selection.get("fallbackReason") or selection["regime"].get("fallbackReason"),
        "fallbackAt": selection.get("fallbackAt") or selection["regime"].get("fallbackAt"),
        "rankingSource": "live_fallback",
        "updatedAt": datetime.now(UTC).isoformat(),
    }


@router.get("/positions")
def get_positions(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    items = db.scalars(
        select(DayTradingPosition)
        .where(DayTradingPosition.user_id == user_id, DayTradingPosition.status == "open")
        .order_by(DayTradingPosition.opened_at.desc())
    ).all()
    for item in items:
        quote = day_trading_engine.quote_for(item.symbol)
        if quote is not None:
            item.current_price = quote
        result = evaluate_position(
            item.direction, item.current_price, item.stop_loss, item.target_1, item.target_2, item.trailing_stop,
        )
        item.latest_action = result["action"]
    db.commit()
    return {"items": [_position_payload(item) for item in items], "updatedAt": datetime.now(UTC).isoformat()}


@router.post("/positions", status_code=201)
def create_position(
    body: DayPositionCreate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    signals = _sync_signals(db)
    signal = next((item for item in signals if item["id"] == body.signal_id), None)
    if signal is None:
        raise HTTPException(status_code=404, detail="訊號不存在")
    settings = _settings(db, user_id)
    today_trades = db.scalars(select(DayTradingTrade).where(
        DayTradingTrade.user_id == user_id, DayTradingTrade.exit_time >= datetime.combine(date.today(), datetime.min.time(), UTC),
    )).all()
    consecutive_losses = 0
    for trade in sorted(today_trades, key=lambda item: item.exit_time, reverse=True):
        if trade.profit >= 0:
            break
        consecutive_losses += 1
    daily_loss = -sum(min(0, trade.profit) for trade in today_trades)
    daily_loss_reached = daily_loss >= settings.capital * settings.max_daily_loss / 100
    if not entry_allowed(
        day_trading_engine.market_regime()["dataDelaySeconds"],
        daily_loss_reached,
        consecutive_losses,
        settings.max_consecutive_losses,
    ) or len(today_trades) >= settings.max_daily_trades:
        raise HTTPException(status_code=409, detail="風控已停止新進場，但現有持倉仍會持續監控")
    if signal["direction"] != body.direction:
        raise HTTPException(status_code=400, detail="方向與訊號不一致")
    item = DayTradingPosition(
        user_id=user_id, signal_id=signal["id"], symbol=signal["symbol"],
        stock_name=signal["stockName"], direction=body.direction,
        entry_price=body.entry_price, quantity=body.quantity, opened_at=datetime.now(UTC),
        stop_loss=signal["stopLoss"], target_1=signal["target1"], target_2=signal["target2"],
        current_price=signal["price"], health_score=signal["healthScore"],
        latest_action="續抱多單" if body.direction == "long" else "續抱空單",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _position_payload(item)


@router.patch("/positions/{position_id}")
def update_position(
    position_id: int,
    body: DayPositionUpdate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.scalar(select(DayTradingPosition).where(
        DayTradingPosition.id == position_id, DayTradingPosition.user_id == user_id,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="模擬持倉不存在")
    for field, value in {
        "stop_loss": body.stop_loss, "trailing_stop": body.trailing_stop,
        "quantity": body.quantity, "sound_enabled": body.sound_enabled,
        "latest_action": body.action,
    }.items():
        if value is not None:
            setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return _position_payload(item)


@router.post("/positions/{position_id}/close")
def close_position(
    position_id: int,
    body: DayPositionClose,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.scalar(select(DayTradingPosition).where(
        DayTradingPosition.id == position_id, DayTradingPosition.user_id == user_id,
        DayTradingPosition.status == "open",
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="可結束的模擬持倉不存在")
    exit_price = body.exit_price or item.current_price
    close_quantity = item.quantity * body.percentage / 100
    factor = 1 if item.direction == "long" else -1
    gross = (exit_price - item.entry_price) * close_quantity * 1000 * factor
    turnover = (exit_price + item.entry_price) * close_quantity * 1000
    fee = round(turnover * DAY_TRADING_COMMISSION_RATE * DAY_TRADING_COMMISSION_DISCOUNT, 2)
    sell_price = exit_price if item.direction == "long" else item.entry_price
    tax = round(sell_price * close_quantity * 1000 * 0.0015, 2)
    slippage = round(exit_price * close_quantity * 1000 * 0.0002, 2)
    profit = round(gross - fee - tax - slippage, 2)
    trade = DayTradingTrade(
        user_id=user_id, symbol=item.symbol, stock_name=item.stock_name, direction=item.direction,
        entry_time=item.opened_at, entry_price=item.entry_price, exit_time=datetime.now(UTC),
        exit_price=exit_price, quantity=close_quantity, fee=fee, tax=tax, slippage=slippage,
        profit=profit, return_percentage=round(profit / (item.entry_price * close_quantity * 1000) * 100, 2),
        max_profit=max(0, item.unrealized_profit), max_loss=min(0, item.unrealized_profit),
        entry_reason="依 AI 訊號建立模擬持倉", exit_reason=body.reason,
    )
    db.add(trade)
    if body.percentage >= 100:
        item.status = "closed"
        item.closed_at = trade.exit_time
        item.exit_price = exit_price
        item.realized_profit = profit
        item.latest_action = "全部賣出" if item.direction == "long" else "全部回補"
    else:
        item.quantity -= close_quantity
        item.latest_action = f"已人工確認減碼／回補 {body.percentage}%"
    db.commit()
    db.refresh(trade)
    return {"position": _position_payload(item), "trade": _trade_payload(trade)}


@router.get("/alerts")
def get_alerts(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    items = db.scalars(
        select(DayTradingAlert).where(DayTradingAlert.user_id == user_id)
        .order_by(DayTradingAlert.created_at.desc()).limit(100)
    ).all()
    return {"items": [_alert_payload(item) for item in items], "unread": sum(item.read_at is None for item in items)}


@router.patch("/alerts/{alert_id}/read")
def read_alert(alert_id: int, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = db.scalar(select(DayTradingAlert).where(
        DayTradingAlert.id == alert_id, DayTradingAlert.user_id == user_id,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    item.read_at = datetime.now(UTC)
    db.commit()
    return _alert_payload(item)


@router.get("/trades")
def get_trades(
    month: str = Query(default="", pattern=r"^(|\d{4}-(0[1-9]|1[0-2]))$"),
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    period, start, end = _monthly_period(month)
    start = _performance_start(user_id, start)
    items = db.scalars(
        select(DayTradingTrade).where(
            DayTradingTrade.user_id == user_id,
            DayTradingTrade.exit_time >= start,
            DayTradingTrade.exit_time < end,
        ).order_by(DayTradingTrade.exit_time.desc()).limit(500)
    ).all()
    return {"period": period, "items": [_trade_payload(item) for item in items]}


@router.get("/performance")
def get_performance(
    month: str = Query(default="", pattern=r"^(|\d{4}-(0[1-9]|1[0-2]))$"),
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    period, start, end = _monthly_period(month)
    start = _performance_start(user_id, start)
    items = db.scalars(select(DayTradingTrade).where(
        DayTradingTrade.user_id == user_id,
        DayTradingTrade.exit_time >= start,
        DayTradingTrade.exit_time < end,
    )).all()
    open_positions = db.scalars(select(DayTradingPosition).where(
        DayTradingPosition.user_id == user_id,
        DayTradingPosition.status == "open",
        DayTradingPosition.opened_at >= start,
        DayTradingPosition.opened_at < end,
    )).all()
    daily_date, daily_start, daily_end = _daily_period()
    today_items = db.scalars(select(DayTradingTrade).where(
        DayTradingTrade.user_id == user_id,
        DayTradingTrade.exit_time >= daily_start,
        DayTradingTrade.exit_time < daily_end,
    )).all()
    today_positions = db.scalars(select(DayTradingPosition).where(
        DayTradingPosition.user_id == user_id,
        DayTradingPosition.status == "open",
        DayTradingPosition.opened_at >= daily_start,
        DayTradingPosition.opened_at < daily_end,
    )).all()
    return {
        "period": period,
        "performanceStartDate": (
            AUTOMATION_PERFORMANCE_START.astimezone(TAIPEI).date().isoformat()
            if user_id == AUTOMATION_USER_ID
            else DYNAMIC_AUTOMATION_PERFORMANCE_START.astimezone(TAIPEI).date().isoformat()
            if user_id == DYNAMIC_AUTOMATION_USER_ID else None
        ),
        **_performance_summary(list(items), list(open_positions)),
        "strategy": (
            {"key": FIXED_STRATEGY_KEY, "label": "原版固定 2 張", "description": "每次正式訊號固定建立 2 張，不套用 500 萬資金上限。"}
            if user_id == AUTOMATION_USER_ID
            else {"key": DYNAMIC_STRATEGY_KEY, "label": "新版 500 萬動態配置", "description": "每日 500 萬，依停損距離與剩餘資金自動計算張數。"}
            if user_id == DYNAMIC_AUTOMATION_USER_ID else None
        ),
        "capitalPlan": (
            automation_capital_state(db, user_id=user_id)
            if user_id == DYNAMIC_AUTOMATION_USER_ID else None
        ),
        "today": {
            "tradeDate": daily_date,
            **_performance_summary(list(today_items), list(today_positions)),
        },
    }


@router.get("/settings")
def get_day_trading_settings(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _settings_payload(_settings(db, user_id), _schedule_settings(db, user_id))


@router.put("/settings")
def update_settings(
    body: DayTradingSettingsUpdate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ordered_times = [
        body.preheat_time, body.stock_pool_time, body.health_check_time,
        body.market_open_time, DAY_TRADING_ENTRY_CUTOFF, DAY_TRADING_CLOSE_REMINDER,
        DAY_TRADING_FORCED_EXIT,
    ]
    parsed_times = [datetime.strptime(value, "%H:%M").time() for value in ordered_times]
    if parsed_times != sorted(parsed_times) or len(set(parsed_times)) != len(parsed_times):
        raise HTTPException(status_code=422, detail="交易排程時間必須依序遞增且不可重複")
    item = _settings(db, user_id)
    schedule = _schedule_settings(db, user_id)
    for api_key, model_key in {
        "capital": "capital", "max_risk_per_trade": "max_risk_per_trade",
        "max_daily_loss": "max_daily_loss", "max_daily_trades": "max_daily_trades",
        "max_position_percentage": "max_position_percentage",
        "max_consecutive_losses": "max_consecutive_losses",
        "minimum_risk_reward": "minimum_risk_reward", "maximum_spread": "maximum_spread",
        "minimum_volume": "minimum_volume", "minimum_turnover": "minimum_turnover",
        "latest_entry_time": "latest_entry_time", "close_reminder_time": "close_reminder_time",
        "notification_enabled": "notification_enabled", "sound_enabled": "sound_enabled",
        "entry_notification": "entry_notification", "exit_notification": "exit_notification",
        "stop_notification": "stop_notification", "target_notification": "target_notification",
        "data_alert_notification": "data_alert_notification",
        "high_confidence_only": "high_confidence_only",
        "minimum_confidence": "minimum_confidence",
        "notification_cooldown": "notification_cooldown", "repeat_count": "repeat_count",
    }.items():
        setattr(item, model_key, getattr(body, api_key))
    item.minimum_volume = max(item.minimum_volume, MIN_DAY_TRADING_VOLUME_SHARES)
    item.minimum_turnover = max(item.minimum_turnover, MIN_DAY_TRADING_TURNOVER)
    item.latest_entry_time = DAY_TRADING_ENTRY_CUTOFF
    item.close_reminder_time = DAY_TRADING_CLOSE_REMINDER
    for api_key, model_key in {
        "timezone": "timezone", "preheat_time": "preheat_time",
        "stock_pool_time": "stock_pool_time", "health_check_time": "health_check_time",
        "market_open_time": "market_open_time", "market_close_time": "market_close_time",
        "warmup_minutes": "warmup_minutes",
        "recommendation_refresh_seconds": "recommendation_refresh_seconds",
        "replacement_score_gap": "replacement_score_gap",
        "minimum_retention_minutes": "minimum_retention_minutes",
        "minimum_live_samples": "minimum_live_samples",
        "maximum_stop_distance": "maximum_stop_distance",
    }.items():
        setattr(schedule, model_key, getattr(body, api_key))
    schedule.market_close_time = DAY_TRADING_FORCED_EXIT
    db.commit()
    db.refresh(item)
    db.refresh(schedule)
    return _settings_payload(item, schedule)


@router.post("/scenarios/{scenario}")
async def trigger_scenario(
    scenario: str,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    allowed = {
        "long_signal", "short_signal", "long_stop", "short_stop",
        "target_1", "emergency_exit", "data_delay", "disconnect", "market_open",
    }
    if scenario not in allowed:
        raise HTTPException(status_code=400, detail="未知測試情境")
    if scenario == "market_open":
        local_now = datetime.now(UTC).astimezone(TAIPEI)
        friday = (local_now + timedelta(days=4 - local_now.weekday())).date()
        risk = _settings(db, user_id)
        schedule = _schedule_settings(db, user_id)
        config = _schedule_config(risk, schedule)
        open_hour, open_minute = (int(value) for value in config.market_open_time.split(":", 1))
        market_open_at = datetime(
            friday.year,
            friday.month,
            friday.day,
            open_hour,
            open_minute,
            tzinfo=TAIPEI,
        )
        simulated_at = market_open_at + timedelta(minutes=config.warmup_minutes)
        session = trading_session_state(
            config,
            simulated_at,
            data_status="normal",
            quote_samples=max(config.minimum_live_samples, 10),
            infrastructure_ok=True,
        )
        nonce = uuid4().hex[:10]
        candidates = day_trading_restrictions.filter_candidates(day_trading_engine.signals())
        for index, item in enumerate(candidates):
            item["id"] = f"simulation-friday-{nonce}-{item['symbol']}"
            item["generatedAt"] = (simulated_at - timedelta(seconds=20 + index * 8)).isoformat()
            item["expiresAt"] = (simulated_at + timedelta(minutes=5 + index)).isoformat()
            if item.get("dataSource") == "TWSE MIS":
                item["dataMode"] = "official_quote_demo_strategy"
                item["warnings"] = [
                    "行情為最近有效官方報價；開盤情境與策略條件為模擬",
                    *item.get("warnings", []),
                ]
            else:
                item["quoteTimestamp"] = simulated_at.isoformat()
                item["dataMode"] = "demo"
                item["dataSource"] = "mock_opening_simulation"
                item["warnings"] = ["展示模式，非即時行情", *item.get("warnings", [])]
        candidates[0]["action"] = "突破買進"
        candidates[0]["confidenceScore"] = 92
        candidates[1]["action"] = "反彈放空"
        candidates[1]["confidenceScore"] = 88
        official, _ranked = stable_recommendation_selector.select(
            f"{user_id}:friday-open:{nonce}",
            candidates,
            config,
            session,
            now=simulated_at,
        )
        recommendation_sent = await line_notification_dispatcher.send_recommendations(
            official[:config.maximum_recommendations],
        )
        return {
            "accepted": True,
            "scenario": scenario,
            "mode": "demo",
            "simulatedDate": friday.isoformat(),
            "phase": session["phase"],
            "robotStatus": session["robotStatus"],
            "formalSignalsAllowed": session["formalSignalsAllowed"],
            "recommended": official,
            "candidates": _ranked,
            "maximumRecommendations": config.maximum_recommendations,
            "lineMessagesSent": recommendation_sent,
        }
    day_trading_engine.trigger(scenario)
    return {"accepted": True, "scenario": scenario, "mode": "demo"}


def _create_alert(
    db: Session,
    user_id: str,
    position: DayTradingPosition | None,
    event: dict[str, Any],
) -> DayTradingAlert:
    item = DayTradingAlert(
        user_id=user_id, position_id=position.id if position else None,
        signal_id=position.signal_id if position else None,
        alert_level=event["level"], alert_type=event["type"], title=event["title"],
        message=event["message"], action=event["action"], reason=event["reason"],
        price=event["price"], created_at=datetime.now(UTC),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


async def _stream_events(request: Request, user_id: str):
    yield "retry: 2000\n\n"
    while not await request.is_disconnected():
        with SessionLocal() as db:
            try:
                regime = day_trading_engine.market_regime()
            except Exception as reason:
                logger.exception("Day-trading market regime failed during stream loop")
                regime = _selection_fallback(reason, stream_healthy=False)["regime"]
            scenario = day_trading_engine.consume_scenario()
            events: list[dict[str, Any]] = []
            positions = db.scalars(select(DayTradingPosition).where(
                DayTradingPosition.user_id == user_id, DayTradingPosition.status == "open",
            )).all()
            for position in positions:
                quote = day_trading_engine.quote_for(position.symbol)
                if quote is not None:
                    position.current_price = quote
                if scenario == "long_stop" and position.direction == "long":
                    position.current_price = position.stop_loss - 0.1
                if scenario == "short_stop" and position.direction == "short":
                    position.current_price = position.stop_loss + 0.1
                if scenario == "target_1":
                    position.current_price = position.target_1
                result = evaluate_position(
                    position.direction, position.current_price, position.stop_loss,
                    position.target_1, position.target_2, position.trailing_stop,
                    regime["dataStatus"],
                )
                position.latest_action = result["action"]
                factor = 1 if position.direction == "long" else -1
                position.unrealized_profit = (
                    position.current_price - position.entry_price
                ) * position.quantity * 1000 * factor
                events.append({
                    "type": "position_update", "id": f"position-{position.id}-{int(datetime.now(UTC).timestamp())}",
                    "data": _position_payload(position),
                })
                if result["level"] in {"important", "emergency"}:
                    event_type = "emergency_exit" if result["level"] == "emergency" else "exit_warning"
                    key = f"{event_type}:{position.id}:{result['action']}:{round(position.current_price, 2)}"
                    event = {
                        "type": event_type, "level": result["level"],
                        "title": "緊急出場" if position.direction == "long" else "緊急回補",
                        "message": f"{position.symbol} {position.stock_name}：{result['action']}",
                        "action": result["action"], "reason": result["reason"],
                        "price": position.current_price, "position": _position_payload(position),
                        "id": key,
                    }
                    if day_trading_engine.event_key_once(key):
                        alert = _create_alert(db, user_id, position, event)
                        event["alert"] = _alert_payload(alert)
                        events.append(event)
            try:
                raw_signals = day_trading_engine.signals()
            except Exception:
                logger.exception("Day-trading raw signals failed during stream loop")
                raw_signals = None
            selection = _safe_selection(db, user_id, raw_signals=raw_signals)
            signal_payload = _signal_selection_payload(selection)
            regime = _market_regime_payload(selection)
            if scenario == "emergency_exit" and not positions:
                events.append({
                    "type": "emergency_exit", "level": "emergency", "id": f"demo-emergency-{int(datetime.now(UTC).timestamp())}",
                    "title": "緊急出場測試", "message": "6669 緯穎：立即全部賣出",
                    "action": "立即全部賣出", "reason": "模擬跌破停損價", "price": 5635,
                    "position": {"symbol": "6669", "stockName": "緯穎", "direction": "long", "stopLoss": 5640},
                })
            if regime["dataStatus"] != "normal":
                events.append({
                    "type": "data_disconnected" if regime["dataStatus"] == "disconnected" else "data_delay",
                    "id": f"data-{regime['dataStatus']}-{int(datetime.now(UTC).timestamp())}",
                    "data": regime,
                })
            events.extend([
                {"type": "market_update", "id": f"market-{int(datetime.now(UTC).timestamp())}", "data": regime},
                {
                    "type": "new_signal" if scenario in {"long_signal", "short_signal"} else "signal_update",
                    "id": f"signal-{int(datetime.now(UTC).timestamp())}", "data": signal_payload,
                },
            ])
            db.commit()
            outbound_events = prioritize_events(events)

        # Never suspend an SSE generator while a database connection is checked
        # out. Slow clients and network backpressure may leave the generator at
        # a yield for an arbitrary amount of time, exhausting the entire pool.
        for event in outbound_events:
            event_type = event["type"]
            payload = event.get("data", event)
            if event_type in {"emergency_exit", "exit_warning"}:
                await line_notification_dispatcher.send_position_event({
                    **payload,
                    "createdAt": datetime.now(UTC).isoformat(),
                })
            elif event_type == "position_update" and str(payload.get("latestAction", "")).startswith("續抱"):
                await line_notification_dispatcher.send_position_event({
                    "type": "position_status",
                    "level": "normal",
                    "action": payload["latestAction"],
                    "reason": "原始條件仍有效",
                    "price": payload["currentPrice"],
                    "position": payload,
                    "createdAt": datetime.now(UTC).isoformat(),
                })
            day_trading_cache.put(f"latest-event:{event_type}", payload)
            day_trading_cache.publish(event_type, payload)
            yield f"id: {event['id']}\nevent: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        await asyncio.sleep(max(1.0, min(settings.day_trading_stream_seconds, 3.0)))


@router.get("/stream")
async def stream(
    request: Request,
    user_id: str = Query(default="demo-user", min_length=8, max_length=80),
) -> StreamingResponse:
    return StreamingResponse(
        _stream_events(request, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
