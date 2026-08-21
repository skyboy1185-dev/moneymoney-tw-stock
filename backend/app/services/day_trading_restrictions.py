from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

import httpx


logger = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
TWSE_DISPOSAL_URL = "https://openapi.twse.com.tw/v1/announcement/punish"
TPEX_DISPOSAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
TWSE_DAY_TRADE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U"
TPEX_DAY_TRADE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_securities"
REFRESH_INTERVAL = timedelta(minutes=10)
_STOCK_CODE = re.compile(r"^\d{4}$")
_DATE_TOKEN = re.compile(r"(?<!\d)(\d{3,4})[./-]?(\d{2})[./-]?(\d{2})(?!\d)")


def _parse_market_date(year: str, month: str, day: str) -> date | None:
    parsed_year = int(year)
    if parsed_year < 1911:
        parsed_year += 1911
    try:
        return date(parsed_year, int(month), int(day))
    except ValueError:
        return None


def parse_disposition_period(value: object) -> tuple[date, date] | None:
    """Parse both TWSE ROC slash dates and TPEx compact ROC dates."""
    matches = _DATE_TOKEN.findall(str(value or ""))
    if len(matches) < 2:
        return None
    start = _parse_market_date(*matches[0])
    end = _parse_market_date(*matches[1])
    if start is None or end is None or end < start:
        return None
    return start, end


def active_disposition_symbols(rows: object, trading_date: date) -> set[str]:
    if not isinstance(rows, list):
        return set()
    symbols: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_symbol = next(
            (
                row.get(key)
                for key in (
                    "Code",
                    "SecuritiesCompanyCode",
                    "StockCode",
                    "stock_code",
                    "股票代號",
                    "證券代號",
                )
                if row.get(key) is not None
            ),
            None,
        )
        symbol = str(raw_symbol or "").strip()
        # Warrants and convertible bonds can also appear in the announcement.
        # The day-trading stock pool contains ordinary four-digit stock codes only.
        if not _STOCK_CODE.fullmatch(symbol):
            continue
        raw_period = next(
            (
                row.get(key)
                for key in (
                    "DispositionPeriod",
                    "DisposalPeriod",
                    "Period",
                    "disposition_period",
                    "處置期間",
                )
                if row.get(key) is not None
            ),
            None,
        )
        period = parse_disposition_period(raw_period)
        if period is None:
            logger.warning("Skipped disposal row with an invalid period: symbol=%s", symbol)
            continue
        if period[0] <= trading_date <= period[1]:
            symbols.add(symbol)
    return symbols


def short_day_trade_symbols(rows: object) -> set[str]:
    """Return official day-trade symbols whose sell-first suspension flag is clear."""
    if not isinstance(rows, list):
        return set()
    symbols: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_symbol = next((
            row.get(key) for key in (
                "Code", "SecuritiesCompanyCode", "StockCode", "stock_code",
                "股票代號", "證券代號",
            ) if row.get(key) is not None
        ), None)
        symbol = str(raw_symbol or "").strip()
        if not _STOCK_CODE.fullmatch(symbol):
            continue
        raw_suspension = next((
            row.get(key) for key in (
                "Suspension",
                "SuspendDayTradeSellFirst",
                "暫停現股賣出後現款買進當沖註記",
            ) if row.get(key) is not None
        ), "")
        suspension = str(raw_suspension or "").strip().upper()
        if suspension not in {"Y", "YES", "是", "暫停"}:
            symbols.add(symbol)
    return symbols


class DayTradingRestrictionService:
    """Caches disposal restrictions and official sell-first day-trade eligibility."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_refresh_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._trading_date: date | None = None
        self._source_symbols: dict[str, set[str]] = {"twse": set(), "tpex": set()}
        self._source_status: dict[str, str] = {"twse": "pending", "tpex": "pending"}
        self._short_symbols: dict[str, set[str]] = {"twse": set(), "tpex": set()}
        self._short_status: dict[str, str] = {"twse": "pending", "tpex": "pending"}

    async def _fetch(self, client: httpx.AsyncClient, source: str, url: str) -> tuple[str, object]:
        response = await client.get(url)
        response.raise_for_status()
        return source, response.json()

    async def refresh(
        self,
        now: datetime | None = None,
        *,
        force: bool = False,
    ) -> set[str]:
        current = now or datetime.now(UTC)
        current_date = current.astimezone(TAIPEI).date()
        if (
            not force
            and self._last_refresh_at is not None
            and self._trading_date == current_date
            and current - self._last_refresh_at < REFRESH_INTERVAL
        ):
            return self.disposed_symbols

        async with self._lock:
            if (
                not force
                and self._last_refresh_at is not None
                and self._trading_date == current_date
                and current - self._last_refresh_at < REFRESH_INTERVAL
            ):
                return self.disposed_symbols

            self._last_refresh_at = current
            self._trading_date = current_date
            headers = {
                "Accept": "application/json",
                "User-Agent": "TWSE-day-trading-restriction-check/1.0",
            }
            async with httpx.AsyncClient(timeout=10.0, headers=headers, follow_redirects=True) as client:
                results = await asyncio.gather(
                    self._fetch(client, "twse", TWSE_DISPOSAL_URL),
                    self._fetch(client, "tpex", TPEX_DISPOSAL_URL),
                    self._fetch(client, "twse", TWSE_DAY_TRADE_URL),
                    self._fetch(client, "tpex", TPEX_DAY_TRADE_URL),
                    return_exceptions=True,
                )

            successful_sources = 0
            for source, result in zip(("twse", "tpex"), results[:2], strict=True):
                if isinstance(result, BaseException):
                    self._source_status[source] = "error"
                    logger.warning("%s disposal list refresh failed: %s", source.upper(), result)
                    continue
                _, rows = result
                self._source_symbols[source] = active_disposition_symbols(rows, current_date)
                self._source_status[source] = "healthy"
                successful_sources += 1
            for source, result in zip(("twse", "tpex"), results[2:], strict=True):
                if isinstance(result, BaseException):
                    self._short_status[source] = "error"
                    logger.warning("%s day-trade list refresh failed: %s", source.upper(), result)
                    continue
                _, rows = result
                self._short_symbols[source] = short_day_trade_symbols(rows)
                self._short_status[source] = "healthy"
                successful_sources += 1
            if successful_sources:
                self._last_success_at = current
            return self.disposed_symbols

    @property
    def disposed_symbols(self) -> set[str]:
        return set().union(*self._source_symbols.values())

    def is_disposed(self, symbol: object) -> bool:
        return str(symbol or "").strip() in self.disposed_symbols

    def market_restrictions_available(self, market: object) -> bool:
        market_name = str(market or "").strip()
        source = "tpex" if market_name == "上櫃" else "twse" if market_name == "上市" else None
        return source is None or self._source_status.get(source) == "healthy"

    def filter_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            item for item in candidates
            if not self.is_disposed(item.get("symbol"))
            and self.market_restrictions_available(item.get("market"))
        ]

    def enrich_short_eligibility(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Attach exchange-level sell-first eligibility; broker inventory remains external."""
        enriched: list[dict[str, Any]] = []
        for candidate in candidates:
            item = dict(candidate)
            market = str(item.get("market") or "").strip()
            source = "tpex" if market == "上櫃" else "twse" if market == "上市" else None
            known = source is not None and self._short_status.get(source) == "healthy"
            eligible_symbols = self._short_symbols.get(source, set()) if source is not None else set()
            eligible = (
                known
                and str(item.get("symbol") or "") in eligible_symbols
                and not self.is_disposed(item.get("symbol"))
            )
            item["shortAvailabilityKnown"] = known
            item["shortEligible"] = eligible
            item["shortEligibilitySource"] = "TWSE／TPEx 當日沖銷交易標的" if known else None
            item["brokerShortInventoryConfirmed"] = False
            if item.get("direction") == "short":
                warnings = list(item.get("warnings") or [])
                if eligible:
                    warnings.append("交易所允許先賣後買當沖；實際券源與帳戶資格仍須由券商確認")
                elif known:
                    warnings.append("交易所未列為可先賣後買當沖標的")
                else:
                    warnings.append("官方放空資格資料暫時無法確認")
                item["warnings"] = warnings
            enriched.append(item)
        return enriched

    @property
    def state(self) -> dict[str, Any]:
        return {
            "status": (
                "healthy"
                if all(
                    value == "healthy"
                    for value in (*self._source_status.values(), *self._short_status.values())
                )
                else "degraded"
            ),
            "sources": dict(self._source_status),
            "shortEligibilitySources": dict(self._short_status),
            "shortEligibleCount": sum(len(symbols) for symbols in self._short_symbols.values()),
            "blockedMarkets": [
                market for market, source in (("上市", "twse"), ("上櫃", "tpex"))
                if self._source_status.get(source) != "healthy"
            ],
            "disposedCount": len(self.disposed_symbols),
            "tradingDate": self._trading_date.isoformat() if self._trading_date else None,
            "lastRefreshAt": self._last_refresh_at.isoformat() if self._last_refresh_at else None,
            "lastSuccessAt": self._last_success_at.isoformat() if self._last_success_at else None,
        }


day_trading_restrictions = DayTradingRestrictionService()
