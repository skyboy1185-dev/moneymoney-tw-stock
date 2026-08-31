from __future__ import annotations

import asyncio
import csv
import io
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from ..models import (
    LargeHolderWeeklyChange,
    LargeHolderWeeklySummary,
    ShareholderDistributionWeekly,
)


TDCC_DISTRIBUTION_URL = "https://smart.tdcc.com.tw/opendata/getOD.ashx"
TWSE_STOCK_DIRECTORY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_STOCK_DIRECTORY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_EMERGING_STOCK_DIRECTORY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics"
TPEX_OTC_COMPANY_PROFILE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
# TDCC does not publish a 400-499 lot band. Level 12 is the closest official
# bucket: 400,001-600,000 shares (roughly 400-600 lots).
OVER_400_LEVELS = frozenset({12})
OVER_1000_LEVELS = frozenset({15})
VALID_DISTRIBUTION_LEVELS = frozenset(range(1, 16))
LISTED_MARKET = "\u4e0a\u5e02"
OTC_MARKET = "\u4e0a\u6ac3"
KNOWN_MARKETS = frozenset({LISTED_MARKET, OTC_MARKET})
UNKNOWN_MARKET = "\u672a\u77e5"
UNKNOWN_INDUSTRY = "\u672a\u5206\u985e"
PARTIAL_METADATA_NOTICE = "\u90e8\u5206\u80a1\u7968\u540d\u7a31\uff0f\u5e02\u5834\u5225\u5f85\u88dc\uff0c\u4f46\u6301\u80a1\u6bd4\u4f8b\u63a1\u5b98\u65b9 TDCC\u3002"


def _summary_stock_name(summary: LargeHolderWeeklySummary | None, stock_code: str) -> str:
    name = (summary.stock_name if summary else "").strip()
    return name or stock_code


def _summary_market(summary: LargeHolderWeeklySummary | None) -> str:
    market = (summary.market if summary else "").strip()
    return market if market in KNOWN_MARKETS else UNKNOWN_MARKET


def _summary_industry(summary: LargeHolderWeeklySummary | None) -> str:
    industry = (summary.industry if summary else "").strip()
    return industry or UNKNOWN_INDUSTRY


def _has_partial_metadata(items: list[dict[str, Any]]) -> bool:
    return any(
        item["market"] == UNKNOWN_MARKET
        or item["industry"] == UNKNOWN_INDUSTRY
        or item["stockName"] == item["stockCode"]
        for item in items
    )


def _is_common_stock_code(stock_code: str) -> bool:
    return stock_code.isdigit() and len(stock_code) == 4 and not stock_code.startswith("00")


def _looks_like_common_stock_name(name: str) -> bool:
    upper_name = name.upper()
    return bool(name) and not any(marker in upper_name for marker in ("-DR", "特別", "特"))


def _merge_directory_stock(
    directory: dict[str, dict[str, Any]],
    symbol: str,
    name: str,
    market: str,
    industry: str = UNKNOWN_INDUSTRY,
    price: Decimal | None = None,
) -> None:
    if not _is_common_stock_code(symbol) or not _looks_like_common_stock_name(name):
        return
    existing = directory.setdefault(symbol, {
        "name": symbol,
        "market": UNKNOWN_MARKET,
        "industry": UNKNOWN_INDUSTRY,
        "price": None,
    })
    if name and existing.get("name") in ("", symbol):
        existing["name"] = name
    if market in KNOWN_MARKETS and existing.get("market") not in KNOWN_MARKETS:
        existing["market"] = market
    if industry and industry != UNKNOWN_INDUSTRY and existing.get("industry") in ("", UNKNOWN_INDUSTRY):
        existing["industry"] = industry
    if price is not None and price > 0 and existing.get("price") is None:
        existing["price"] = float(price)


@dataclass(frozen=True)
class DistributionRow:
    stock_code: str
    report_date: date
    holding_level: int
    holder_count: int
    share_count: int
    holding_ratio: Decimal


@dataclass(frozen=True)
class DistributionSummary:
    stock_code: str
    report_date: date
    holders_over_400_count: int
    shares_over_400: int
    ratio_over_400: Decimal
    holders_over_1000_count: int
    shares_over_1000: int
    ratio_over_1000: Decimal
    total_shareholders: int
    total_shares: int


class LargeHolderDataProvider(Protocol):
    async def fetch_latest(self) -> list[DistributionRow]: ...


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", "").strip())
    except InvalidOperation:
        return Decimal("0")


def _integer(value: Any) -> int:
    try:
        return int(str(value or "0").replace(",", "").strip())
    except ValueError:
        return 0


def _date(value: Any) -> date:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError("TDCC 資料日期格式錯誤")
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


_directory_cache: tuple[float, dict[str, dict[str, Any]]] | None = None


class TdccOpenDataProvider:
    """Official latest-week adapter. Historical weeks are accumulated in PostgreSQL."""

    async def fetch_latest(self) -> list[DistributionRow]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                TDCC_DISTRIBUTION_URL,
                params={"id": "1-5", "_": str(int(time.time()))},
                headers={
                    "Accept": "text/csv,application/json",
                    "Cache-Control": "no-cache",
                    "User-Agent": "Moneymoney-TWSE-Dashboard",
                },
            )
            response.raise_for_status()
        payload = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        if not isinstance(payload, list):
            raise ValueError("TDCC 回傳格式不是陣列")
        rows: list[DistributionRow] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            date_value = next((value for key, value in item.items() if str(key).lstrip("\ufeff") == "資料日期"), None)
            stock_code = str(item.get("證券代號") or "").strip()
            level = _integer(item.get("持股分級"))
            if not stock_code or date_value is None or level not in range(1, 18):
                continue
            rows.append(DistributionRow(
                stock_code=stock_code,
                report_date=_date(date_value),
                holding_level=level,
                holder_count=_integer(item.get("人數")),
                share_count=_integer(item.get("股數")),
                holding_ratio=_decimal(item.get("占集保庫存數比例%")),
            ))
        if not rows:
            raise ValueError("TDCC 未回傳可用的股權分散資料")
        return rows

    async def fetch_stock_directory(self) -> dict[str, dict[str, Any]]:
        global _directory_cache
        if _directory_cache is not None and _directory_cache[0] > time.monotonic():
            return _directory_cache[1]
        async with httpx.AsyncClient(timeout=20.0) as client:
            listed_response, otc_response, emerging_response, otc_profile_response = await asyncio.gather(
                client.get(TWSE_STOCK_DIRECTORY_URL, headers={"Accept": "application/json"}),
                client.get(TPEX_STOCK_DIRECTORY_URL, headers={"Accept": "application/json"}),
                client.get(TPEX_EMERGING_STOCK_DIRECTORY_URL, headers={"Accept": "application/json"}),
                client.get(TPEX_OTC_COMPANY_PROFILE_URL, headers={"Accept": "application/json"}),
                return_exceptions=True,
            )
        directory: dict[str, dict[str, Any]] = {}
        for response, market, symbol_fields, name_fields, price_fields in (
            (
                listed_response,
                LISTED_MARKET,
                ("Code", "證券代號"),
                ("Name", "證券名稱"),
                ("ClosingPrice", "收盤價"),
            ),
            (
                otc_response,
                OTC_MARKET,
                ("SecuritiesCompanyCode", "Code"),
                ("CompanyName", "Name"),
                ("Close", "收盤價"),
            ),
            (
                emerging_response,
                UNKNOWN_MARKET,
                ("SecuritiesCompanyCode", "Code"),
                ("CompanyName", "Name"),
                ("LatestPrice", "Average"),
            ),
            (
                otc_profile_response,
                OTC_MARKET,
                ("SecuritiesCompanyCode", "Code"),
                ("CompanyAbbreviation", "CompanyName", "Name"),
                (),
            ),
        ):
            if isinstance(response, Exception) or response.status_code >= 400:
                continue
            response.raise_for_status()
            rows = response.json()
            for item in rows if isinstance(rows, list) else []:
                symbol = next((str(item.get(field) or "").strip() for field in symbol_fields if item.get(field)), "")
                name = next((str(item.get(field) or "").strip() for field in name_fields if item.get(field)), "")
                close_price = next((_decimal(item.get(field)) for field in price_fields if item.get(field)), None)
                _merge_directory_stock(directory, symbol, name, market, price=close_price)
        _directory_cache = (time.monotonic() + 60, directory)
        return directory


def repair_large_holder_metadata(
    db: Session,
    directory: dict[str, dict[str, Any]],
    report_date: date | None = None,
) -> int:
    """Backfill missing stock names/markets without overwriting valid metadata."""
    if not directory:
        return 0
    query = select(LargeHolderWeeklySummary)
    if report_date is not None:
        query = query.where(LargeHolderWeeklySummary.report_date == report_date)
    repaired = 0
    for item in db.scalars(query).all():
        stock = directory.get(item.stock_code)
        if not stock:
            continue
        changed = False
        stock_name = str(stock.get("name") or "").strip()
        stock_market = str(stock.get("market") or "").strip()
        stock_industry = str(stock.get("industry") or "").strip()
        if stock_name and (not item.stock_name.strip() or item.stock_name == item.stock_code):
            item.stock_name = stock_name
            changed = True
        if stock_market in KNOWN_MARKETS and item.market not in KNOWN_MARKETS:
            item.market = stock_market
            changed = True
        if stock_industry and stock_industry != UNKNOWN_INDUSTRY and (
            not item.industry.strip() or item.industry == UNKNOWN_INDUSTRY
        ):
            item.industry = stock_industry
            changed = True
        if changed:
            item.updated_at = datetime.now(UTC)
            repaired += 1
    return repaired


def aggregate_distribution(rows: list[DistributionRow]) -> list[DistributionSummary]:
    """Use official level 12 for 400-600 lots and level 15 for 1,000+ lots."""
    grouped: dict[tuple[str, date], list[DistributionRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.stock_code, row.report_date)].append(row)
    summaries: list[DistributionSummary] = []
    for (stock_code, report_date), stock_rows in grouped.items():
        valid = [row for row in stock_rows if row.holding_level in VALID_DISTRIBUTION_LEVELS]
        over400 = [row for row in valid if row.holding_level in OVER_400_LEVELS]
        over1000 = [row for row in valid if row.holding_level in OVER_1000_LEVELS]
        if not valid or not over400:
            continue
        summaries.append(DistributionSummary(
            stock_code=stock_code,
            report_date=report_date,
            holders_over_400_count=sum(row.holder_count for row in over400),
            shares_over_400=sum(row.share_count for row in over400),
            ratio_over_400=sum((row.holding_ratio for row in over400), Decimal("0")),
            holders_over_1000_count=sum(row.holder_count for row in over1000),
            shares_over_1000=sum(row.share_count for row in over1000),
            ratio_over_1000=sum((row.holding_ratio for row in over1000), Decimal("0")),
            total_shareholders=sum(row.holder_count for row in valid),
            total_shares=sum(row.share_count for row in valid),
        ))
    return summaries


def percentage_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return ((current - previous) / previous * Decimal("100")).quantize(Decimal("0.000001"))


def _lots(share_count: int) -> float:
    return round(share_count / 1_000, 3)


def _anomaly_reason(current: DistributionSummary, previous: DistributionSummary) -> str:
    if previous.total_shares <= 0:
        return "上期總股數不足，無法檢查股本變化"
    share_change = abs(Decimal(current.total_shares - previous.total_shares) / Decimal(previous.total_shares))
    if share_change >= Decimal("0.10"):
        return f"集保總股數單週變動 {share_change * 100:.2f}%，可能有減資、增資或分割等結構性事件"
    if current.ratio_over_400 >= Decimal("99.5") and current.holders_over_400_count <= 2:
        return "持股集中於極少數帳戶，可能為減資或停止過戶期間的結構性資料"
    return ""


def calculate_weekly_change(
    current: DistributionSummary,
    previous: DistributionSummary,
) -> dict[str, Any]:
    if current.stock_code != previous.stock_code:
        raise ValueError("不可比較不同股票")
    anomaly_reason = _anomaly_reason(current, previous)
    return {
        "stock_code": current.stock_code,
        "current_report_date": current.report_date,
        "previous_report_date": previous.report_date,
        "current_ratio_over_400": current.ratio_over_400,
        "previous_ratio_over_400": previous.ratio_over_400,
        "change_pp_over_400": current.ratio_over_400 - previous.ratio_over_400,
        "change_pct_over_400": percentage_change(current.ratio_over_400, previous.ratio_over_400),
        "current_ratio_over_1000": current.ratio_over_1000,
        "previous_ratio_over_1000": previous.ratio_over_1000,
        "change_pp_over_1000": current.ratio_over_1000 - previous.ratio_over_1000,
        "change_pct_over_1000": percentage_change(current.ratio_over_1000, previous.ratio_over_1000),
        "holder_count_change_over_400": current.holders_over_400_count - previous.holders_over_400_count,
        "holder_count_change_over_1000": current.holders_over_1000_count - previous.holders_over_1000_count,
        "anomaly_flag": bool(anomaly_reason),
        "anomaly_reason": anomaly_reason,
    }


def persist_latest_distribution(
    db: Session,
    rows: list[DistributionRow],
    directory: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summaries = aggregate_distribution(rows)
    if not summaries:
        raise ValueError("集保資料無法產生任何週摘要")
    report_date = max(item.report_date for item in summaries)
    metadata = directory or {}
    if db.scalar(select(LargeHolderWeeklySummary.id).where(
        LargeHolderWeeklySummary.report_date == report_date,
    ).limit(1)):
        repair_count = repair_large_holder_metadata(db, metadata)
        if repair_count:
            db.commit()
        return {
            "status": "already_synced",
            "reportDate": report_date.isoformat(),
            "summaryCount": len(summaries),
            "metadataRepairCount": repair_count,
        }

    now = datetime.now(UTC)
    numeric_codes = {item.stock_code for item in summaries if item.stock_code.isdigit() and len(item.stock_code) == 4}
    raw_objects = [
        ShareholderDistributionWeekly(
            stock_code=row.stock_code,
            report_date=row.report_date,
            holding_level=row.holding_level,
            holder_count=row.holder_count,
            share_count=row.share_count,
            holding_ratio=row.holding_ratio,
            updated_at=now,
        )
        for row in rows
        if row.stock_code in numeric_codes and row.holding_level in range(1, 18)
    ]
    summary_objects = []
    for item in summaries:
        if item.stock_code not in numeric_codes:
            continue
        stock = metadata.get(item.stock_code, {})
        summary_objects.append(LargeHolderWeeklySummary(
            **item.__dict__,
            stock_name=stock.get("name", item.stock_code),
            market=stock.get("market", "未知"),
            industry=stock.get("industry", "未分類"),
            updated_at=now,
        ))
    db.bulk_save_objects(raw_objects)
    db.bulk_save_objects(summary_objects)
    db.flush()

    report_dates = db.scalars(
        select(distinct(LargeHolderWeeklySummary.report_date))
        .order_by(LargeHolderWeeklySummary.report_date.desc())
        .limit(2)
    ).all()
    changes = 0
    if len(report_dates) >= 2:
        current_date, previous_date = report_dates[0], report_dates[1]
        current_items = db.scalars(select(LargeHolderWeeklySummary).where(
            LargeHolderWeeklySummary.report_date == current_date,
        )).all()
        previous_items = {
            item.stock_code: item for item in db.scalars(select(LargeHolderWeeklySummary).where(
                LargeHolderWeeklySummary.report_date == previous_date,
            )).all()
        }
        for item in current_items:
            previous_item = previous_items.get(item.stock_code)
            if previous_item is None:
                continue
            current_summary = _model_summary(item)
            previous_summary = _model_summary(previous_item)
            db.add(LargeHolderWeeklyChange(**calculate_weekly_change(current_summary, previous_summary), updated_at=now))
            changes += 1
    db.commit()
    return {
        "status": "synced",
        "reportDate": report_date.isoformat(),
        "rawCount": len(raw_objects),
        "summaryCount": len(summary_objects),
        "changeCount": changes,
        "metadataRepairCount": 0,
    }


def _model_summary(item: LargeHolderWeeklySummary) -> DistributionSummary:
    return DistributionSummary(
        stock_code=item.stock_code,
        report_date=item.report_date,
        holders_over_400_count=item.holders_over_400_count,
        shares_over_400=item.shares_over_400,
        ratio_over_400=Decimal(item.ratio_over_400),
        holders_over_1000_count=item.holders_over_1000_count,
        shares_over_1000=item.shares_over_1000,
        ratio_over_1000=Decimal(item.ratio_over_1000),
        total_shareholders=item.total_shareholders,
        total_shares=item.total_shares,
    )


DEMO_STOCKS = [
    ("2330", "台積電", "上市", "半導體", 1125), ("2317", "鴻海", "上市", "其他電子", 181),
    ("2454", "聯發科", "上市", "半導體", 1430), ("2308", "台達電", "上市", "電子零組件", 468),
    ("2382", "廣達", "上市", "電腦及週邊", 292), ("6669", "緯穎", "上市", "電腦及週邊", 2830),
    ("3711", "日月光投控", "上市", "半導體", 165), ("2303", "聯電", "上市", "半導體", 52),
    ("2383", "台光電", "上市", "電子零組件", 1210), ("3037", "欣興", "上市", "電子零組件", 198),
    ("2345", "智邦", "上市", "通信網路", 925), ("2327", "國巨*", "上市", "電子零組件", 176),
    ("1303", "南亞", "上市", "塑膠工業", 47), ("2881", "富邦金", "上市", "金融保險", 92),
    ("2882", "國泰金", "上市", "金融保險", 69), ("2891", "中信金", "上市", "金融保險", 43),
    ("2603", "長榮", "上市", "航運業", 196), ("1301", "台塑", "上市", "塑膠工業", 49),
    ("3008", "大立光", "上市", "光電", 2510), ("5274", "信驊", "上櫃", "半導體", 5220),
    ("6488", "環球晶", "上櫃", "半導體", 437), ("8069", "元太", "上櫃", "光電", 246),
    ("8299", "群聯", "上櫃", "半導體", 690), ("3529", "力旺", "上櫃", "半導體", 3280),
    ("5347", "世界", "上櫃", "半導體", 118), ("6188", "廣明", "上櫃", "電腦及週邊", 128),
    ("4979", "華星光", "上櫃", "通信網路", 232), ("3260", "威剛", "上櫃", "電子通路", 116),
]


def _demo_fridays(weeks: int = 12) -> list[date]:
    current = date(2026, 8, 7)
    return [current - timedelta(days=7 * offset) for offset in reversed(range(weeks))]


def _demo_history(stock: tuple[str, str, str, str, float]) -> list[dict[str, Any]]:
    symbol, name, market, industry, base_price = stock
    rng = random.Random(int(symbol) * 411)
    ratio400 = Decimal(str(.5 + rng.random() * 3.5))
    ratio1000 = Decimal(str(15 + rng.random() * 55))
    total_shares = int(300_000_000 + rng.random() * 2_000_000_000)
    price = Decimal(str(base_price * (0.88 + rng.random() * 0.08)))
    middle_large_ratio = Decimal(str(4 + rng.random() * 10))
    retail_ratio = Decimal(str(18 + rng.random() * 25))
    total_shareholders = int(20_000 + rng.random() * 180_000)
    history: list[dict[str, Any]] = []
    trend = Decimal(str(0.10 + (int(symbol[-2:]) % 11) * 0.045))
    for index, report_date in enumerate(_demo_fridays()):
        shock = Decimal(str((rng.random() - 0.35) * 0.55))
        ratio400 = max(Decimal(".1"), min(Decimal("10"), ratio400 + trend * Decimal(".2") + shock * Decimal(".12")))
        ratio1000 = max(Decimal("1"), min(Decimal("90"), ratio1000 + trend * Decimal("0.58") + shock * Decimal("0.45")))
        middle_large_ratio = max(Decimal("1"), middle_large_ratio + trend * Decimal(".12") + shock * Decimal(".08"))
        retail_ratio = max(Decimal("2"), retail_ratio - trend * Decimal(".32") - shock * Decimal(".12"))
        total_shareholders = max(500, total_shareholders + round((rng.random() - .58) * 1_400))
        weekly_price_change = Decimal(str((rng.random() - 0.42) * 6))
        price = max(Decimal("5"), price * (Decimal("1") + weekly_price_change / Decimal("100")))
        volume = int(2_000_000 + rng.random() * 42_000_000)
        shares400 = round(total_shares * float(ratio400) / 100)
        shares1000 = round(total_shares * float(ratio1000) / 100)
        holder400 = max(1, round(shares400 / (450_000 + rng.random() * 150_000)))
        holder1000 = max(1, round(shares1000 / (2_000_000 + rng.random() * 10_000_000)))
        history.append({
            "reportDate": report_date.isoformat(), "stockCode": symbol, "stockName": name,
            "market": market, "industry": industry,
            "ratioOver400": round(float(ratio400), 4), "ratioOver1000": round(float(ratio1000), 4),
            "ratioOver400All": round(float(min(Decimal("99.9"), ratio400 + ratio1000 + middle_large_ratio)), 4),
            "retailRatio": round(float(retail_ratio), 4),
            "totalShareholders": total_shareholders, "totalShares": total_shares,
            "holdersOver400": holder400, "holdersOver1000": holder1000,
            "lotsOver400": _lots(shares400), "lotsOver1000": _lots(shares1000),
            "price": round(float(price), 2), "volume": volume,
            "foreignNetBuy": round((rng.random() - .42) * 14_000_000),
            "investmentTrustNetBuy": round((rng.random() - .46) * 5_000_000),
            "dealerNetBuy": round((rng.random() - .5) * 2_000_000),
            "mainForceNetBuy": round((rng.random() - .4) * 16_000_000),
            "marginBalanceChange": round((rng.random() - .5) * 2_500_000),
        })
    return history


def _score_and_signal(history: list[dict[str, Any]], kind: str) -> tuple[int, str, list[str]]:
    current, previous = history[-1], history[-2]
    key = "ratioOver400" if kind == "over400" else "ratioOver1000"
    holder_key = "holdersOver400" if kind == "over400" else "holdersOver1000"
    change_pp = current[key] - previous[key]
    four_week = current[key] - history[-5][key]
    holder_change = current[holder_key] - previous[holder_key]
    weekly_price = (current["price"] - previous["price"]) / previous["price"] * 100
    score = 0
    score += min(30, max(0, round(change_pp / 2.5 * 30)))
    score += min(20, max(0, round(four_week / 4 * 20)))
    score += min(10, max(0, 5 + round(holder_change / 20)))
    score += 10 if current["foreignNetBuy"] > 0 else 0
    score += 10 if current["investmentTrustNetBuy"] > 0 else 0
    score += 10 if current["mainForceNetBuy"] > 0 else 0
    score += 5 if current["volume"] >= 5_000_000 else 2
    score += 5 if weekly_price <= 10 else 2 if weekly_price <= 15 else 0
    warnings: list[str] = []
    if weekly_price > 15:
        score = max(0, score - 12)
        warnings.append("本週漲幅超過 15%，追高風險偏高")
    if current["foreignNetBuy"] < 0 and current["investmentTrustNetBuy"] < 0:
        warnings.append("大戶加碼但外資與投信同步賣超")
    if current["volume"] < 3_000_000:
        warnings.append("成交量不足，籌碼訊號需再確認")
    if change_pp >= 1.5 and weekly_price < 4:
        signal = "大戶加碼且股價尚未發動"
    elif change_pp >= 1.5:
        signal = "大戶明顯加碼"
    elif four_week > 1.5:
        signal = "大戶持續加碼"
    elif change_pp > 0 and history[-3][key] <= history[-4][key]:
        signal = "大戶首次轉增"
    elif current["foreignNetBuy"] < 0:
        signal = "大戶加碼但法人賣超"
    else:
        signal = "籌碼集中度提升"
    return min(100, score), signal, warnings


def _demo_rankings(
    kind: str,
    limit: int,
    market: str,
    industry: str,
    keyword: str,
    min_average_turnover: float,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for stock in DEMO_STOCKS:
        history = _demo_history(stock)
        current, previous = history[-1], history[-2]
        if market == "listed" and current["market"] != "上市":
            continue
        if market == "otc" and current["market"] != "上櫃":
            continue
        if industry and current["industry"] != industry:
            continue
        if keyword and keyword not in current["stockCode"] and keyword not in current["stockName"]:
            continue
        average_turnover = sum(point["price"] * point["volume"] for point in history[-4:]) / 4
        if min_average_turnover > 0 and average_turnover < min_average_turnover:
            continue
        ratio_key = "ratioOver400" if kind == "over400" else "ratioOver1000"
        holder_key = "holdersOver400" if kind == "over400" else "holdersOver1000"
        lot_key = "lotsOver400" if kind == "over400" else "lotsOver1000"
        change_pp = current[ratio_key] - previous[ratio_key]
        change_pct = change_pp / previous[ratio_key] * 100 if previous[ratio_key] else None
        score, signal, warnings = _score_and_signal(history, kind)
        weekly_change = (current["price"] - previous["price"]) / previous["price"] * 100
        volume_change = (current["volume"] - previous["volume"]) / previous["volume"] * 100 if previous["volume"] else 0
        technical_status = "突破20日高點" if weekly_change > 4 and volume_change > 10 else "多頭整理" if weekly_change >= 0 else "拉回觀察"
        items.append({
            "rank": 0, "stockCode": current["stockCode"], "stockName": current["stockName"],
            "market": current["market"], "industry": current["industry"], "latestPrice": current["price"],
            "weeklyChangePct": round(weekly_change, 2),
            "currentLargeHolderRatio": current[ratio_key], "previousLargeHolderRatio": previous[ratio_key],
            "changePercentagePoint": round(change_pp, 4),
            "changePercentage": round(change_pct, 2) if change_pct is not None else None,
            "currentHolderCount": current[holder_key],
            "holderCountChange": current[holder_key] - previous[holder_key],
            "currentLotCount": current[lot_key], "previousLotCount": previous[lot_key],
            "lotCountChange": round(current[lot_key] - previous[lot_key], 3),
            "foreignNetBuy5d": current["foreignNetBuy"],
            "investmentTrustNetBuy5d": current["investmentTrustNetBuy"],
            "dealerNetBuy5d": current["dealerNetBuy"],
            "mainForceNetBuy5d": current["mainForceNetBuy"],
            "volumeChange5d": round(volume_change, 2),
            "averageTurnover20d": round(average_turnover),
            "technicalStatus": technical_status, "healthScore": score, "aiSignal": signal,
            "anomalyFlag": False, "anomalyReason": "", "warnings": warnings,
            "quoteSource": "展示行情", "quoteTimestamp": f"{current['reportDate']}T13:30:00+08:00",
        })
    items.sort(key=lambda item: (
        item["changePercentagePoint"], item["currentLargeHolderRatio"], item["averageTurnover20d"],
    ), reverse=True)
    for rank, item in enumerate(items[:limit], 1):
        item["rank"] = rank
    dates = _demo_fridays()
    return {
        "type": kind, "currentReportDate": dates[-1].isoformat(), "previousReportDate": dates[-2].isoformat(),
        "updatedAt": datetime.now(UTC).isoformat(), "dataMode": "demo",
        "dataSource": "TDCC Provider 展示 Adapter",
        "dataNotice": (
            "展示模式：尚未累積兩期官方集保資料；400張榜模擬TDCC第12級"
            "（400,001～600,000股），千張榜模擬第15級（1,000,001股以上），不代表本週真實排名。"
        ),
        "industries": sorted({item[3] for item in DEMO_STOCKS}),
        "items": items[:limit],
    }


def get_large_holder_rankings(
    db: Session,
    kind: str,
    limit: int = 20,
    market: str = "all",
    industry: str = "",
    keyword: str = "",
    min_average_turnover: float = 30_000_000,
) -> dict[str, Any]:
    report_dates = db.scalars(
        select(distinct(ShareholderDistributionWeekly.report_date))
        .order_by(ShareholderDistributionWeekly.report_date.desc())
        .limit(2)
    ).all()
    if len(report_dates) < 2:
        return _demo_rankings(kind, limit, market, industry, keyword, min_average_turnover)
    current_date, previous_date = report_dates[0], report_dates[1]
    summaries = {
        item.stock_code: item for item in db.scalars(select(LargeHolderWeeklySummary).where(
            LargeHolderWeeklySummary.report_date == current_date,
        )).all()
    }
    changes = {
        item.stock_code: item for item in db.scalars(select(LargeHolderWeeklyChange).where(
        LargeHolderWeeklyChange.current_report_date == current_date,
        LargeHolderWeeklyChange.previous_report_date == previous_date,
        )).all()
    }
    target_level = 12 if kind == "over400" else 15
    current_rows = {
        item.stock_code: item for item in db.scalars(select(ShareholderDistributionWeekly).where(
            ShareholderDistributionWeekly.report_date == current_date,
            ShareholderDistributionWeekly.holding_level == target_level,
        )).all()
    }
    previous_rows = {
        item.stock_code: item for item in db.scalars(select(ShareholderDistributionWeekly).where(
            ShareholderDistributionWeekly.report_date == previous_date,
            ShareholderDistributionWeekly.holding_level == target_level,
        )).all()
    }
    items: list[dict[str, Any]] = []
    industries: set[str] = set()
    for stock_code, current_row in current_rows.items():
        if not _is_common_stock_code(stock_code):
            continue
        previous_row = previous_rows.get(stock_code)
        current = summaries.get(stock_code)
        if previous_row is None:
            continue
        current_market = _summary_market(current)
        stock_name = _summary_stock_name(current, stock_code)
        current_industry = _summary_industry(current)
        if market == "listed" and current_market != LISTED_MARKET:
            continue
        if market == "otc" and current_market != OTC_MARKET:
            continue
        if industry and current_industry != industry:
            continue
        if keyword and keyword not in stock_code and keyword not in stock_name:
            continue
        industries.add(current_industry)
        ratio = float(current_row.holding_ratio)
        previous_ratio = float(previous_row.holding_ratio)
        change_pp = ratio - previous_ratio
        change_pct_value = percentage_change(
            Decimal(current_row.holding_ratio), Decimal(previous_row.holding_ratio),
        )
        holder_count = current_row.holder_count
        holder_change = current_row.holder_count - previous_row.holder_count
        history = db.scalars(select(ShareholderDistributionWeekly).where(
            ShareholderDistributionWeekly.stock_code == stock_code,
            ShareholderDistributionWeekly.holding_level == target_level,
        ).order_by(ShareholderDistributionWeekly.report_date.desc()).limit(5)).all()
        history_ratio = [float(point.holding_ratio) for point in history]
        four_week_change = history_ratio[0] - history_ratio[-1] if len(history_ratio) >= 4 else 0
        score = min(30, max(0, round(change_pp / 2.5 * 30)))
        score += min(20, max(0, round(four_week_change / 4 * 20)))
        score += min(10, max(0, 5 + round(holder_change / 20)))
        stored_change = changes.get(stock_code)
        anomaly_flag = bool(stored_change and stored_change.anomaly_flag)
        anomaly_reason = stored_change.anomaly_reason if stored_change else ""
        if anomaly_flag:
            score = max(0, score - 15)
        signal = (
            "大戶明顯加碼" if change_pp >= 1.5
            else "大戶持續加碼" if four_week_change >= 1.5
            else "大戶首次轉增" if change_pp > 0
            else "需持續觀察"
        )
        warnings = [anomaly_reason] if anomaly_flag else []
        items.append({
            "rank": 0, "stockCode": stock_code, "stockName": stock_name,
            "market": current_market, "industry": current_industry,
            "latestPrice": None, "weeklyChangePct": None,
            "currentLargeHolderRatio": ratio, "previousLargeHolderRatio": previous_ratio,
            "changePercentagePoint": round(change_pp, 4),
            "changePercentage": float(change_pct_value) if change_pct_value is not None else None,
            "currentHolderCount": holder_count, "holderCountChange": holder_change,
            "currentLotCount": _lots(current_row.share_count),
            "previousLotCount": _lots(previous_row.share_count),
            "lotCountChange": _lots(current_row.share_count - previous_row.share_count),
            "foreignNetBuy5d": None, "investmentTrustNetBuy5d": None, "dealerNetBuy5d": None,
            "mainForceNetBuy5d": None, "volumeChange5d": None, "averageTurnover20d": None,
            "technicalStatus": "行情因子待串接", "healthScore": score, "aiSignal": signal,
            "anomalyFlag": anomaly_flag, "anomalyReason": anomaly_reason,
            "warnings": warnings, "quoteSource": "行情待串接", "quoteTimestamp": "",
        })
    items.sort(key=lambda item: (
        item["changePercentagePoint"], item["currentLargeHolderRatio"], 0,
    ), reverse=True)
    items = items[:limit]
    for rank, item in enumerate(items, 1):
        item["rank"] = rank
    notice = (
        "400張榜採TDCC第12級（400,001～600,000股），千張榜採第15級"
        "（1,000,001股以上）；比例、戶數與持股張數週增減均為官方集保資料。"
        "尚未串接的20日均成交金額、法人與主力欄位不計分並顯示暫無資料。"
    )
    if _has_partial_metadata(items):
        notice = f"{notice} {PARTIAL_METADATA_NOTICE}"
    return {
        "type": kind, "currentReportDate": current_date.isoformat(),
        "previousReportDate": previous_date.isoformat(), "updatedAt": datetime.now(UTC).isoformat(),
        "dataMode": "official_tdcc", "dataSource": "臺灣集中保管結算所官方 CSV",
        "dataNotice": notice,
        "industries": sorted(industries), "items": items,
    }


def get_large_holder_history(db: Session, stock_code: str, weeks: int = 12) -> dict[str, Any]:
    raw_rows = db.scalars(
        select(ShareholderDistributionWeekly)
        .where(
            ShareholderDistributionWeekly.stock_code == stock_code,
            ShareholderDistributionWeekly.holding_level.in_((12, 15)),
        )
        .order_by(ShareholderDistributionWeekly.report_date.desc())
    ).all()
    rows_by_date: dict[date, dict[int, ShareholderDistributionWeekly]] = defaultdict(dict)
    for item in raw_rows:
        rows_by_date[item.report_date][item.holding_level] = item
    report_dates = sorted(rows_by_date, reverse=True)[:weeks]
    if len(report_dates) >= 2:
        summary = db.scalar(
            select(LargeHolderWeeklySummary)
            .where(LargeHolderWeeklySummary.stock_code == stock_code)
            .order_by(LargeHolderWeeklySummary.report_date.desc())
            .limit(1)
        )
        points = []
        for report_date in reversed(report_dates):
            level400 = rows_by_date[report_date].get(12)
            level1000 = rows_by_date[report_date].get(15)
            if level400 is None or level1000 is None:
                continue
            points.append({
                "reportDate": report_date.isoformat(), "stockCode": stock_code,
                "ratioOver400": float(level400.holding_ratio),
                "ratioOver1000": float(level1000.holding_ratio),
                "holdersOver400": level400.holder_count,
                "holdersOver1000": level1000.holder_count,
                "lotsOver400": _lots(level400.share_count),
                "lotsOver1000": _lots(level1000.share_count),
                "price": None, "volume": None, "foreignNetBuy": None, "investmentTrustNetBuy": None,
                "dealerNetBuy": None, "mainForceNetBuy": None, "marginBalanceChange": None,
            })
        return {
            "stockCode": stock_code, "stockName": summary.stock_name if summary else stock_code,
            "dataMode": "official_tdcc",
            "dataSource": "臺灣集中保管結算所官方 CSV", "items": points,
            "dataNotice": (
                "400張資料為TDCC第12級（400,001～600,000股），千張資料為第15級"
                "（1,000,001股以上）；比例、戶數與持股張數均為官方週資料。"
            ),
        }
    stock = next((item for item in DEMO_STOCKS if item[0] == stock_code), None)
    if stock is None:
        raise KeyError(stock_code)
    return {
        "stockCode": stock_code, "stockName": stock[1], "dataMode": "demo",
        "dataSource": "TDCC Provider 展示 Adapter", "items": _demo_history(stock),
        "dataNotice": "展示模式：12週數值為可重現模擬資料，不代表真實集保持股。",
    }


tdcc_large_holder_provider = TdccOpenDataProvider()


async def fetch_latest_distribution_bundle() -> tuple[
    list[DistributionRow],
    dict[str, dict[str, Any]],
]:
    """Fetch required TDCC rows and best-effort stock metadata enrichment."""
    rows = await tdcc_large_holder_provider.fetch_latest()
    try:
        directory = await tdcc_large_holder_provider.fetch_stock_directory()
    except (httpx.HTTPError, ValueError):
        directory = {}
    return rows, directory
