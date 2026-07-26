from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class TradingScheduleConfig:
    timezone: str = "Asia/Taipei"
    preheat_time: str = "08:30"
    stock_pool_time: str = "08:45"
    health_check_time: str = "08:55"
    market_open_time: str = "09:00"
    latest_entry_time: str = "13:20"
    close_reminder_time: str = "13:25"
    market_close_time: str = "13:30"
    warmup_minutes: int = 3
    recommendation_refresh_seconds: int = 10
    replacement_score_gap: int = 5
    minimum_retention_minutes: int = 3
    minimum_live_samples: int = 3
    minimum_risk_reward: float = 1.5
    maximum_spread: float = 0.5
    minimum_volume: float = 500_000
    minimum_turnover: float = 50_000_000
    maximum_stop_distance: float = 3.0
    maximum_recommendations: int = 5
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
    warmup_end = market_open + timedelta(minutes=config.warmup_minutes)
    latest_entry = _at(today, config.latest_entry_time, timezone)
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
        elif local_now < warmup_end or quote_samples < config.minimum_live_samples or recovering:
            phase, robot_status, message, next_transition = (
                "warmup", "開盤暖機中",
                "市場剛開盤，正在收集量價資料與建立 VWAP，暫不產生正式進場指令。",
                warmup_end,
            )
            if local_now >= warmup_end and (quote_samples < config.minimum_live_samples or recovering):
                message = "服務已恢復，正在重新建立足夠的即時量價資料，暫不產生正式進場指令。"
                next_transition = None
        elif local_now < latest_entry:
            phase, robot_status, message, next_transition = (
                "scanning", "即時掃描中", "AI 當沖機器人已啟動，持續掃描符合風控條件的股票。", latest_entry,
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

    healthy = data_status == "normal" and infrastructure_ok
    formal_allowed = phase == "scanning" and healthy
    if not healthy:
        robot_status = "行情異常，暫停新訊號"
        message = "行情資料異常，暫停產生新交易訊號。"
        formal_allowed = False

    return {
        "timezone": config.timezone,
        "localTime": local_now.isoformat(),
        "tradingDate": today.isoformat(),
        "isTradingDay": trading_day,
        "phase": phase,
        "robotStatus": robot_status,
        "statusMessage": message,
        "formalSignalsAllowed": formal_allowed,
        "warmupMinutes": config.warmup_minutes,
        "warmupUntil": warmup_end.isoformat(),
        "quoteSamples": quote_samples,
        "minimumLiveSamples": config.minimum_live_samples,
        "nextTransitionAt": next_transition.isoformat() if next_transition else None,
        "schedule": {
            "preheatTime": config.preheat_time,
            "stockPoolTime": config.stock_pool_time,
            "healthCheckTime": config.health_check_time,
            "marketOpenTime": config.market_open_time,
            "latestEntryTime": config.latest_entry_time,
            "closeReminderTime": config.close_reminder_time,
            "marketCloseTime": config.market_close_time,
        },
    }


def _expired(candidate: dict[str, Any], now: datetime) -> bool:
    expires_at = datetime.fromisoformat(str(candidate["expiresAt"]))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now.astimezone(UTC)


def recommendation_qualification(
    candidate: dict[str, Any],
    config: TradingScheduleConfig,
    session: dict[str, Any],
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    current = now or datetime.now(UTC)
    failures: list[str] = []
    if not session["formalSignalsAllowed"]:
        failures.append(str(session["statusMessage"]))
    if candidate.get("dataStatus", "normal") != "normal":
        failures.append("行情資料異常")
    if candidate.get("dataMode") != "official":
        failures.append("策略或歷史行情仍為展示資料，禁止正式訊號")
    if candidate.get("quoteIsRealtime") is not True:
        failures.append("缺少可驗證的盤中行情")
    if candidate.get("status") != "confirmed" or _expired(candidate, current):
        failures.append("訊號已失效或尚未確認")
    if str(candidate.get("action", "")).startswith(("等待", "觀望", "禁止", "行情異常")):
        failures.append("尚未形成正式進場指令")
    if float(candidate.get("confidenceScore", 0)) < 75:
        failures.append("信心分數未達 75")
    if float(candidate.get("healthScore", 0)) < 70:
        failures.append("健康度未達 70")
    if float(candidate.get("riskRewardRatio", 0)) < config.minimum_risk_reward:
        failures.append(f"風險報酬比未達 1：{config.minimum_risk_reward:g}")
    if float(candidate.get("volume", 0)) < config.minimum_volume:
        failures.append("成交量未達最低標準")
    if float(candidate.get("turnover", 0)) < config.minimum_turnover:
        failures.append("成交金額未達最低標準")
    if float(candidate.get("spreadPercentage", 999)) > config.maximum_spread:
        failures.append("買賣價差超過允許範圍")
    if candidate.get("chaseBlocked"):
        failures.append("已觸發禁止追多／追空")
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
    return (
        float(item.get("marketAlignment", 0)),
        float(item.get("confidenceScore", 0)),
        float(item.get("healthScore", 0)),
        float(item.get("riskRewardRatio", 0)),
        float(item.get("confirmationScore", 0)),
        float(item.get("volumeScore", 0)),
        float(item.get("activeForce", 0)),
        abs(float(item.get("largeOrderForce", 0))),
        float(item.get("industryScore", 0)),
        float(item.get("liquidityScore", 0)),
        freshness,
        -distance,
    )


class StableRecommendationSelector:
    """Selects at most three recommendations while preventing small-score churn."""

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
                    if row["id"] not in admitted and len(admitted) >= config.maximum_recommendations:
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
