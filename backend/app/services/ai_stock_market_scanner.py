from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from ..schemas import AIRecommendationSyncItem


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
OFFICIAL_QUOTE_SOURCES = {"TWSE MIS", "TWSE OpenAPI", "TPEx OpenAPI"}


@dataclass(frozen=True)
class MarketScanResult:
    status: str
    fetched_at: datetime
    market_status: str | None = None
    featured_count: int = 0
    items: tuple[AIRecommendationSyncItem, ...] = ()
    error: str | None = None


def _number(row: dict[str, Any], key: str) -> Decimal:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} is not numeric")
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{key} is not numeric") from error


def _quote_timestamp(row: dict[str, Any]) -> datetime:
    price_date = row.get("priceDate")
    price_time = row.get("priceTime")
    if not isinstance(price_date, str) or not isinstance(price_time, str):
        raise ValueError("quote date or time is missing")
    parsed = datetime.fromisoformat(f"{price_date}T{price_time}")
    return parsed.replace(tzinfo=TAIPEI) if parsed.tzinfo is None else parsed


def normalize_market_snapshot(
    payload: object,
    now: datetime | None = None,
) -> MarketScanResult:
    current = now or datetime.now(UTC)
    if not isinstance(payload, dict):
        return MarketScanResult("invalid_response", current, error="response is not an object")

    market_status = payload.get("marketStatus")
    market_status_text = market_status if isinstance(market_status, str) else None
    featured = payload.get("featured")
    if payload.get("marketOpen") is not True:
        return MarketScanResult("market_closed", current, market_status=market_status_text)
    if not isinstance(featured, list):
        return MarketScanResult(
            "invalid_response", current, market_status=market_status_text,
            error="featured is not an array",
        )

    normalized: list[AIRecommendationSyncItem] = []
    for raw in featured[:5]:
        if not isinstance(raw, dict):
            continue
        try:
            hard_failures = raw.get("hardRiskFailures")
            reasons = raw.get("reasons")
            source = raw.get("priceSource")
            if (
                raw.get("quoteFresh") is not True
                or raw.get("isOfficialPrice") is not True
                or raw.get("volumeQualified") is not True
                or raw.get("liquidityQualified") is not True
                or not isinstance(hard_failures, list)
                or hard_failures
                or source not in OFFICIAL_QUOTE_SOURCES
                or not isinstance(reasons, list)
                or len(reasons) < 3
                or _number(raw, "score") < 75
                or _number(raw, "strategyFit") < 75
                or _number(raw, "marketFit") < 55
                or _number(raw, "riskRewardRatio") < 1.5
            ):
                continue
            warnings = [
                str(item)
                for value in (raw.get("riskTags"), hard_failures)
                if isinstance(value, list)
                for item in value
            ][:10]
            normalized.append(AIRecommendationSyncItem(
                signal_id=str(raw.get("signalId", "")),
                symbol=str(raw.get("symbol", "")),
                stock_name=str(raw.get("name", "")),
                market=str(raw.get("market") or "上市"),
                industry=str(raw.get("industry") or "未分類"),
                strategy_name=str(raw.get("strategyName", "")),
                secondary_strategies=[
                    str(item) for item in raw.get("secondaryStrategies", [])
                ][:2] if isinstance(raw.get("secondaryStrategies"), list) else [],
                total_score=_number(raw, "score"),
                strategy_fit=_number(raw, "strategyFit"),
                market_fit=_number(raw, "marketFit"),
                health_score=_number(raw, "healthScore"),
                current_price=_number(raw, "price"),
                entry_min=_number(raw, "entryMin"),
                entry_max=_number(raw, "entryMax"),
                stop_loss=_number(raw, "stopLoss"),
                target_1=_number(raw, "target1"),
                target_2=_number(raw, "target2"),
                risk_reward_ratio=_number(raw, "riskRewardRatio"),
                reasons=[str(item) for item in reasons[:5]],
                warnings=warnings,
                quote_source=str(source),
                quote_timestamp=_quote_timestamp(raw),
                expired_at=current + timedelta(minutes=10),
            ))
        except (TypeError, ValueError, ValidationError):
            logger.warning("Skipped invalid AI stock scanner row: %s", raw.get("symbol", "unknown"))

    return MarketScanResult(
        "success", current, market_status=market_status_text,
        featured_count=len(featured), items=tuple(normalized),
    )


class AIStockMarketScanner:
    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 30.0,
        service_token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.strip()
        self.timeout_seconds = timeout_seconds
        self.service_token = service_token
        self.transport = transport

    async def scan(self, now: datetime | None = None) -> MarketScanResult:
        current = now or datetime.now(UTC)
        if not self.url:
            return MarketScanResult("disabled", current)
        if urlparse(self.url).scheme not in {"http", "https"}:
            return MarketScanResult("invalid_url", current, error="scanner URL must use HTTP or HTTPS")
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=True,
            ) as client:
                headers = {"Accept": "application/json", "User-Agent": "TWSE-AI-Stock-Automation/1.0"}
                if self.service_token:
                    headers["X-Adaptive-Scanner-Token"] = self.service_token
                response = await client.get(self.url, headers=headers)
                response.raise_for_status()
                return normalize_market_snapshot(response.json(), current)
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("AI stock market scan failed: %s", error)
            return MarketScanResult("error", current, error=str(error)[:300])
