from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .theme_stock_universe import is_target_theme_symbol


TAIPEI = ZoneInfo("Asia/Taipei")
MAX_LONG_CHASE_CHANGE_PERCENT = 5.0
MIN_OFFICIAL_CONFIDENCE_SCORE = 70
MIN_OFFICIAL_CONFIRMATION_SCORE = 35
MIN_OFFICIAL_HEALTH_SCORE = 65
MIN_DAY_TRADING_VOLUME_SHARES = 1_000_000
MIN_DAY_TRADING_TURNOVER = 100_000_000
MIN_LIQUIDITY_PROGRESS = 0.10
DAY_TRADING_SIGNAL_START = "09:05"
# All new intraday entries stop at noon. Existing positions keep running
# stop-loss, take-profit, reduction, cover and forced-exit workflows afterward.
DAY_TRADING_ENTRY_CUTOFF = "12:00"
DAY_TRADING_LONG_ENTRY_CUTOFF = "12:00"
DAY_TRADING_CLOSE_REMINDER = "13:25"
DAY_TRADING_FORCED_EXIT = "13:30"


@dataclass(frozen=True)
class TradingScheduleConfig:
    timezone: str = "Asia/Taipei"
    preheat_time: str = "08:30"
    stock_pool_time: str = "08:45"
    health_check_time: str = "08:55"
    market_open_time: str = "09:00"
    signal_start_time: str = DAY_TRADING_SIGNAL_START
    latest_entry_time: str = DAY_TRADING_ENTRY_CUTOFF
    close_reminder_time: str = DAY_TRADING_CLOSE_REMINDER
    market_close_time: str = DAY_TRADING_FORCED_EXIT
    warmup_minutes: int = 3
    recommendation_refresh_seconds: int = 10
    replacement_score_gap: int = 5
    minimum_retention_minutes: int = 3
    minimum_live_samples: int = 3
    minimum_risk_reward: float = 1.5
    maximum_spread: float = 0.5
    minimum_volume: float = MIN_DAY_TRADING_VOLUME_SHARES
    minimum_turnover: float = MIN_DAY_TRADING_TURNOVER
    maximum_stop_distance: float = 3.0
    maximum_recommendations: int = 10
    holidays: frozenset[date] = field(default_factory=frozenset)


def _clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _at(day: date, value: str, timezone: ZoneInfo) -> datetime:
    return datetime.combine(day, _clock(value), timezone)


def is_twse_trading_day(day: date, holidays: Iterable[date] = ()) -> bool:
    return day.weekday() < 5 and day not in set(holidays)


def trading_session_state(
    config: TradingScheduleConfig,
    now: datetime | None = None,
    *,
    data_status: str = "normal",
    data_quality_mode: str = "live",
    quote_samples: int = 0,
    infrastructure_ok: bool = True,
    recovering: bool = False,
) -> dict[str, Any]:
    timezone = ZoneInfo(config.timezone)
    local_now = (now or datetime.now(UTC)).astimezone(timezone)
    today = local_now.date()
    trading_day = is_twse_trading_day(today, config.holidays)
    preheat = _at(today, config.preheat_time, timezone)
    stock_pool = _at(today, config.stock_pool_time, timezone)
    health_check = _at(today, config.health_check_time, timezone)
    market_open = _at(today, config.market_open_time, timezone)
    signal_start = _at(today, config.signal_start_time, timezone)
    warmup_end = max(
        market_open + timedelta(minutes=config.warmup_minutes),
        signal_start,
    )
    latest_entry = _at(today, config.latest_entry_time, timezone)
    long_entry_cutoff = _at(today, DAY_TRADING_LONG_ENTRY_CUTOFF, timezone)
    close_reminder = _at(today, config.close_reminder_time, timezone)
    market_close = _at(today, config.market_close_time, timezone)

    phase = "non_trading"
    robot_status = "等待下一交易日"
    message = "目前為非交易時段，機器人將於下一個交易日自動啟動。"
    next_transition: datetime | None = None

    if trading_day:
        if local_now < preheat:
            phase, robot_status, next_transition = "before_preheat", "等待預熱", preheat
        elif local_now < stock_pool:
            phase, robot_status, message, next_transition = (
                "preheating", "系統準備中", "系統準備中：正在預熱並檢查資料連線。", stock_pool,
            )
        elif local_now < health_check:
            phase, robot_status, message, next_transition = (
                "loading", "載入股票池", "正在載入上市、上櫃股票池、昨日資料與技術指標。", health_check,
            )
        elif local_now < market_open:
            phase, robot_status, message, next_transition = (
                "health_check", "系統準備中", "系統準備中：正在檢查行情、Redis、即時推送與資料庫。", market_open,
            )
        elif local_now < long_entry_cutoff and (
            local_now < warmup_end
            or quote_samples < config.minimum_live_samples
            or recovering
        ):
            phase, robot_status, message, next_transition = (
                "warmup", "多空動能掃描中",
                f"09:00 已開始多空動能掃描；至 {config.signal_start_time} 收集首根完整 5 分 K、量能與大單資料，暫不產生正式進場訊號。",
                warmup_end,
            )
            if local_now >= warmup_end and (quote_samples < config.minimum_live_samples or recovering):
                message = "服務已恢復，正在重新建立足夠的即時量價資料，暫不產生正式進場指令。"
                next_transition = None
        elif local_now < latest_entry:
            phase, robot_status, message, next_transition = (
                "scanning", "5 分 K 強勢股掃描中", "多空正式新進場只開放至 12:00；之後只管理既有持倉。", latest_entry,
            )
        elif local_now < long_entry_cutoff:
            phase, robot_status, message, next_transition = (
                "long_only", "停止新進場",
                "12:00 後停止所有新進場；既有持倉仍持續監控。",
                long_entry_cutoff,
            )
        elif local_now < close_reminder:
            phase, robot_status, message, next_transition = (
                "entry_closed", "停止新進場", "已停止產生新的進場訊號，現有持倉仍持續監控。", close_reminder,
            )
        elif local_now < market_close:
            phase, robot_status, message, next_transition = (
                "closing", "收盤前部位處理", "請處理未平倉部位；出場、回補與停損監控持續運作。", market_close,
            )
        else:
            phase, robot_status, message = (
                "summary", "今日掃描完成", "今日新訊號已停止，系統已產生交易摘要。",
            )

    healthy = (
        data_status == "normal"
        and data_quality_mode in {"live", "index_delay", "demo"}
        and infrastructure_ok
    )
    formal_long_allowed = phase in {"scanning", "long_only"} and healthy
    formal_short_allowed = phase == "scanning" and healthy
    formal_allowed = formal_long_allowed or formal_short_allowed
    if not healthy and phase in {"health_check", "warmup", "scanning", "long_only", "entry_closed", "closing"}:
        robot_status = "行情異常，暫停新訊號"
        message = "行情資料異常，暫停產生新交易訊號。"
        formal_allowed = False
        formal_long_allowed = False
        formal_short_allowed = False

    return {
        "timezone": config.timezone,
        "localTime": local_now.isoformat(),
        "tradingDate": today.isoformat(),
        "isTradingDay": trading_day,
        "phase": phase,
        "robotStatus": robot_status,
        "statusMessage": message,
        "formalSignalsAllowed": formal_allowed,
        "formalLongSignalsAllowed": formal_long_allowed,
        "formalShortSignalsAllowed": formal_short_allowed,
        "warmupMinutes": config.warmup_minutes,
        "warmupUntil": warmup_end.isoformat(),
        "quoteSamples": quote_samples,
        "dataQualityMode": data_quality_mode,
        "minimumLiveSamples": config.minimum_live_samples,
        "nextTransitionAt": next_transition.isoformat() if next_transition else None,
        "schedule": {
            "preheatTime": config.preheat_time,
            "stockPoolTime": config.stock_pool_time,
            "healthCheckTime": config.health_check_time,
            "marketOpenTime": config.market_open_time,
            "signalStartTime": config.signal_start_time,
            "latestEntryTime": config.latest_entry_time,
            "shortEntryCutoffTime": config.latest_entry_time,
            "longEntryCutoffTime": DAY_TRADING_LONG_ENTRY_CUTOFF,
            "closeReminderTime": config.close_reminder_time,
            "marketCloseTime": config.market_close_time,
        },
    }


def _expired(candidate: dict[str, Any], now: datetime) -> bool:
    expires_at = datetime.fromisoformat(str(candidate["expiresAt"]))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now.astimezone(UTC)


def intraday_liquidity_minimums(
    config: TradingScheduleConfig,
    now: datetime | None = None,
) -> tuple[float, float]:
    """Scale full-session liquidity targets by elapsed Taipei market time."""
    timezone = ZoneInfo(config.timezone)
    current = (now or datetime.now(UTC)).astimezone(timezone)
    market_open = _at(current.date(), config.market_open_time, timezone)
    market_close = _at(current.date(), config.market_close_time, timezone)
    session_seconds = max(1.0, (market_close - market_open).total_seconds())
    elapsed_seconds = max(0.0, min(session_seconds, (current - market_open).total_seconds()))
    progress = max(MIN_LIQUIDITY_PROGRESS, elapsed_seconds / session_seconds)
    return config.minimum_volume * progress, config.minimum_turnover * progress


def recommendation_qualification(
    candidate: dict[str, Any],
    config: TradingScheduleConfig,
    session: dict[str, Any],
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    current = now or datetime.now(UTC)
    failures: list[str] = []
    in_momentum_universe = bool(candidate.get(
        "momentumUniverseMember",
        is_target_theme_symbol(str(candidate.get("symbol", ""))),
    ))
    if not in_momentum_universe:
        failures.append("不屬於大單動能雷達股票池")
    direction = str(candidate.get("direction", ""))
    direction_allowed = (
        bool(session.get("formalLongSignalsAllowed", session["formalSignalsAllowed"]))
        if direction == "long"
        else bool(session.get("formalShortSignalsAllowed", session["formalSignalsAllowed"]))
        if direction == "short"
        else False
    )
    if not direction_allowed:
        failures.append(
            "已超過 12:00 新進場截止時間"
            if direction == "short" and session.get("phase") == "long_only"
            else str(session["statusMessage"])
        )
    if candidate.get("dataStatus", "normal") != "normal":
        failures.append("行情資料異常")
    if candidate.get("dataMode") != "official":
        failures.append(
            "實際行情樣本仍在暖機，暫停正式訊號"
            if candidate.get("dataMode") == "warming_up"
            else "策略或歷史行情仍為展示資料，禁止正式訊號"
        )
    if candidate.get("quoteIsRealtime") is not True:
        failures.append("缺少可驗證的盤中行情")
    if candidate.get("status") != "confirmed" or _expired(candidate, current):
        failures.append("訊號已失效或尚未確認")
    if str(candidate.get("action", "")).startswith(("等待", "觀望", "禁止", "行情異常")):
        failures.append("尚未形成正式進場指令")
    confidence_score = float(candidate.get("confidenceScore", 0))
    confirmation_score = float(candidate.get("confirmationScore", 0))
    if confidence_score < MIN_OFFICIAL_CONFIDENCE_SCORE:
        failures.append(f"信心分數未達 {MIN_OFFICIAL_CONFIDENCE_SCORE}")
    if confirmation_score < MIN_OFFICIAL_CONFIRMATION_SCORE:
        failures.append(f"盤中確認分數未達 {MIN_OFFICIAL_CONFIRMATION_SCORE}")
    five_minute_structure = str(candidate.get("fiveMinuteStructure", ""))
    five_minute_setup = str(candidate.get("fiveMinuteSetup", ""))
    if (
        candidate.get("direction") == "long"
        and "突破" in five_minute_setup
        and ("未確認" in five_minute_structure or "尚未" in five_minute_structure)
    ):
        failures.append("5 分 K 突破結構尚未確認")
    if float(candidate.get("healthScore", 0)) < MIN_OFFICIAL_HEALTH_SCORE:
        failures.append(f"健康度未達 {MIN_OFFICIAL_HEALTH_SCORE}")
    if float(candidate.get("riskRewardRatio", 0)) < config.minimum_risk_reward:
        failures.append(f"風險報酬比未達 1：{config.minimum_risk_reward:g}")
    required_volume, required_turnover = intraday_liquidity_minimums(config, current)
    if float(candidate.get("volume", 0)) < required_volume:
        failures.append(
            f"量能進度不足（預估全日至少 {config.minimum_volume / 1000:,.0f} 張）"
        )
    if float(candidate.get("turnover", 0)) < required_turnover:
        failures.append(
            f"成交金額進度不足（預估全日至少 {config.minimum_turnover / 100_000_000:g} 億元）"
        )
    if float(candidate.get("spreadPercentage", 999)) > config.maximum_spread:
        failures.append("買賣價差超過允許範圍")
    if (
        candidate.get("direction") == "long"
        and float(candidate.get("changePercent", 0)) >= MAX_LONG_CHASE_CHANGE_PERCENT
        and float(candidate.get("rangePositionPercent", 50)) >= 90
    ):
        failures.append(
            f"今日漲幅已達 {MAX_LONG_CHASE_CHANGE_PERCENT:g}%，禁止追價"
        )
    if candidate.get("chaseBlocked"):
        failures.append("已觸發禁止追多／追空")
    if candidate.get("isDisposed") or candidate.get("tradeRestricted"):
        failures.append("處置股或交易受限股票禁止列入當沖")
    if not candidate.get("largeOrderDataAvailable", False):
        failures.append("等待逐筆成交大單資料完成暖機")
    elif candidate.get("direction") == "short":
        if not candidate.get("largeOrderContinuousSell", False):
            failures.append("大戶尚未持續加空")
    elif not candidate.get("largeOrderContinuousBuy", False):
        failures.append("大戶尚未持續加多")
    if not candidate.get("tradingEligible", False):
        failures.append("不符合當沖交易資格")
    if float(candidate.get("marketAlignment", 0)) < 30:
        failures.append("方向與大盤環境嚴重衝突")
    if float(candidate.get("stopDistancePercent", 999)) > config.maximum_stop_distance:
        failures.append("停損距離超過風控上限")
    if candidate.get("direction") == "short":
        if not candidate.get("shortAvailabilityKnown", False):
            failures.append("放空資格待確認")
        elif not candidate.get("shortEligible", False):
            failures.append("無放空資格或有交易限制")
        if candidate.get("nearLimitDown") or candidate.get("excessiveNegativeDeviation"):
            failures.append("接近跌停或負乖離過大")
    return not failures, failures


def _ranking_key(item: dict[str, Any]) -> tuple[float, ...]:
    generated_at = datetime.fromisoformat(str(item["generatedAt"]))
    freshness = generated_at.timestamp()
    distance = abs(float(item.get("price", 0)) - (
        float(item.get("entryMin", 0)) + float(item.get("entryMax", 0))
    ) / 2)
    confirmation_mode_adjustment = 0
    return (
        float(item.get("marketAlignment", 0)) + confirmation_mode_adjustment,
        float(item.get("confidenceScore", 0)),
        float(item.get("confirmationScore", 0)),
        float(item.get("healthScore", 0)),
        float(item.get("volumeScore", 0)),
        float(item.get("activeForce", 0)),
        abs(float(item.get("largeOrderForce", 0))),
        float(item.get("riskRewardRatio", 0)),
        float(item.get("liquidityScore", 0)),
        float(item.get("industryScore", 0)),
        freshness,
        -distance,
    )


def _high_confidence_override(item: dict[str, Any]) -> bool:
    return (
        float(item.get("confidenceScore", 0)) >= 85
        and float(item.get("healthScore", 0)) >= 80
        and float(item.get("riskRewardRatio", 0)) >= 2
        and float(item.get("marketAlignment", 0)) >= 45
    )


class StableRecommendationSelector:
    """Selects up to the configured recommendation limit while preventing small-score churn."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, datetime]] = {}
        self._last_ranked_at: dict[str, datetime] = {}
        self._hour_buckets: dict[str, str] = {}
        self._hourly_admitted: dict[str, set[str]] = {}

    def select(
        self,
        user_id: str,
        candidates: list[dict[str, Any]],
        config: TradingScheduleConfig,
        session: dict[str, Any],
        *,
        open_signal_ids: set[str] | None = None,
        now: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        current_time = now or datetime.now(UTC)
        open_ids = open_signal_ids or set()
        prepared: list[dict[str, Any]] = []
        eligible: list[dict[str, Any]] = []
        for candidate in candidates:
            passed, failures = recommendation_qualification(candidate, config, session, current_time)
            if candidate["id"] in open_ids:
                passed = False
                failures = [*failures, "已轉入持倉監控"]
            row = {
                **candidate,
                "isOfficialRecommendation": False,
                "recommendationLabel": "市場掃描候選",
                "qualificationFailures": failures,
            }
            if candidate.get("direction") == "short" and "放空資格待確認" in failures:
                row["action"] = "放空資格待確認"
            prepared.append(row)
            if passed:
                eligible.append(row)
        eligible.sort(key=_ranking_key, reverse=True)

        with self._lock:
            hour_bucket = current_time.astimezone(ZoneInfo(config.timezone)).strftime("%Y-%m-%dT%H")
            if self._hour_buckets.get(user_id) != hour_bucket:
                self._hour_buckets[user_id] = hour_bucket
                self._active[user_id] = {}
                self._hourly_admitted[user_id] = set()
                self._last_ranked_at.pop(user_id, None)
            active = self._active.setdefault(user_id, {})
            admitted = self._hourly_admitted.setdefault(user_id, set())
            last_ranked = self._last_ranked_at.get(user_id)
            refresh_due = (
                last_ranked is None
                or current_time - last_ranked >= timedelta(seconds=config.recommendation_refresh_seconds)
            )
            eligible_by_id = {row["id"]: row for row in eligible}
            for signal_id in list(active):
                if signal_id not in eligible_by_id:
                    del active[signal_id]

            selected = [row for row in eligible if row["id"] in active]
            selected.sort(key=_ranking_key, reverse=True)
            for row in eligible:
                if len(selected) >= config.maximum_recommendations:
                    break
                if row not in selected:
                    high_confidence = _high_confidence_override(row)
                    if (
                        row["id"] not in admitted
                        and len(admitted) >= config.maximum_recommendations
                        and not high_confidence
                    ):
                        continue
                    selected.append(row)
                    active[row["id"]] = current_time
                    admitted.add(row["id"])

            if refresh_due:
                challengers = [row for row in eligible if row not in selected]
                for challenger in challengers:
                    if not selected:
                        break
                    if (
                        challenger["id"] not in admitted
                        and len(admitted) >= config.maximum_recommendations
                        and not _high_confidence_override(challenger)
                    ):
                        continue
                    weakest = min(selected, key=lambda row: float(row["confidenceScore"]))
                    retained_for = current_time - active.get(weakest["id"], current_time)
                    can_replace = retained_for >= timedelta(minutes=config.minimum_retention_minutes)
                    score_gap = float(challenger["confidenceScore"]) - float(weakest["confidenceScore"])
                    if can_replace and score_gap >= config.replacement_score_gap:
                        selected.remove(weakest)
                        active.pop(weakest["id"], None)
                        selected.append(challenger)
                        active[challenger["id"]] = current_time
                        admitted.add(challenger["id"])
                self._last_ranked_at[user_id] = current_time

            selected = sorted(selected, key=_ranking_key, reverse=True)[: config.maximum_recommendations]
            selected_ids = {row["id"] for row in selected}
            for signal_id in list(active):
                if signal_id not in selected_ids:
                    del active[signal_id]

        official = [
            {
                **row,
                "rank": index + 1,
                "isOfficialRecommendation": True,
                "recommendationLabel": "AI 正式推薦",
                "recommendedAt": active[row["id"]].isoformat(),
            }
            for index, row in enumerate(selected)
        ]
        official_by_id = {row["id"]: row for row in official}
        candidate_rows = [
            official_by_id.get(row["id"], {**row, "rank": index + 1})
            for index, row in enumerate(sorted(prepared, key=_ranking_key, reverse=True))
        ]
        return official, candidate_rows


stable_recommendation_selector = StableRecommendationSelector()
