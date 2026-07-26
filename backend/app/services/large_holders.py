from __future__ import annotations

import asyncio
import random
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


TDCC_DISTRIBUTION_URL = "https://openapi.tdcc.com.tw/v1/opendata/1-5"
TWSE_STOCK_DIRECTORY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_STOCK_DIRECTORY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
OVER_400_LEVELS = frozenset({12, 13, 14, 15})
OVER_1000_LEVELS = frozenset({15})
VALID_DISTRIBUTION_LEVELS = frozenset(range(1, 16))


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


class TdccOpenDataProvider:
    """Official latest-week adapter. Historical weeks are accumulated in PostgreSQL."""

    async def fetch_latest(self) -> list[DistributionRow]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                TDCC_DISTRIBUTION_URL,
                headers={"Accept": "application/json", "User-Agent": "Moneymoney-TWSE-Dashboard"},
            )
            response.raise_for_status()
        payload = response.json()
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

    async def fetch_stock_directory(self) -> dict[str, dict[str, str]]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            listed_response, otc_response = await asyncio.gather(
                client.get(TWSE_STOCK_DIRECTORY_URL, headers={"Accept": "application/json"}),
                client.get(TPEX_STOCK_DIRECTORY_URL, headers={"Accept": "application/json"}),
            )
        directory: dict[str, dict[str, str]] = {}
        for response, market in ((listed_response, "上市"), (otc_response, "上櫃")):
            response.raise_for_status()
            rows = response.json()
            for item in rows if isinstance(rows, list) else []:
                if market == "上市":
                    symbol = str(item.get("Code") or item.get("證券代號") or "").strip()
                    name = str(item.get("Name") or item.get("證券名稱") or "").strip()
                else:
                    symbol = str(item.get("SecuritiesCompanyCode") or item.get("Code") or "").strip()
                    name = str(item.get("CompanyName") or item.get("Name") or "").strip()
                if not symbol.isdigit() or len(symbol) != 4 or symbol.startswith("00"):
                    continue
                if any(marker in name.upper() for marker in ("-DR", "特別", "特")):
                    continue
                directory[symbol] = {"name": name or symbol, "market": market, "industry": "未分類"}
        return directory


def aggregate_distribution(rows: list[DistributionRow]) -> list[DistributionSummary]:
    """Aggregate level 12-15 for 400 lots and level 15 for 1,000 lots."""
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
    directory: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    summaries = aggregate_distribution(rows)
    if not summaries:
        raise ValueError("集保資料無法產生任何週摘要")
    report_date = max(item.report_date for item in summaries)
    if db.scalar(select(LargeHolderWeeklySummary.id).where(
        LargeHolderWeeklySummary.report_date == report_date,
    ).limit(1)):
        return {"status": "already_synced", "reportDate": report_date.isoformat(), "summaryCount": len(summaries)}

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
    metadata = directory or {}
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
    current = date(2026, 7, 24)
    return [current - timedelta(days=7 * offset) for offset in reversed(range(weeks))]


def _demo_history(stock: tuple[str, str, str, str, float]) -> list[dict[str, Any]]:
    symbol, name, market, industry, base_price = stock
    rng = random.Random(int(symbol) * 411)
    ratio400 = Decimal(str(12 + rng.random() * 35))
    ratio1000 = ratio400 * Decimal(str(0.35 + rng.random() * 0.35))
    price = Decimal(str(base_price * (0.88 + rng.random() * 0.08)))
    history: list[dict[str, Any]] = []
    trend = Decimal(str(0.10 + (int(symbol[-2:]) % 11) * 0.045))
    for index, report_date in enumerate(_demo_fridays()):
        shock = Decimal(str((rng.random() - 0.35) * 0.55))
        ratio400 = max(Decimal("2"), min(Decimal("85"), ratio400 + trend + shock))
        ratio1000 = max(Decimal("0.5"), min(ratio400, ratio1000 + trend * Decimal("0.58") + shock * Decimal("0.45")))
        weekly_price_change = Decimal(str((rng.random() - 0.42) * 6))
        price = max(Decimal("5"), price * (Decimal("1") + weekly_price_change / Decimal("100")))
        volume = int(2_000_000 + rng.random() * 42_000_000)
        holder400 = int(180 + rng.random() * 620 + index * (int(symbol[-1]) % 4))
        holder1000 = int(25 + rng.random() * 150 + index * (int(symbol[-1]) % 3))
        history.append({
            "reportDate": report_date.isoformat(), "stockCode": symbol, "stockName": name,
            "market": market, "industry": industry,
            "ratioOver400": round(float(ratio400), 4), "ratioOver1000": round(float(ratio1000), 4),
            "holdersOver400": holder400, "holdersOver1000": holder1000,
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
        "dataNotice": "展示模式：尚未累積兩期官方集保資料；比例、法人與行情均為可重現模擬資料，不代表本週真實排名。",
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
        select(distinct(LargeHolderWeeklySummary.report_date))
        .order_by(LargeHolderWeeklySummary.report_date.desc())
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
    changes = db.scalars(select(LargeHolderWeeklyChange).where(
        LargeHolderWeeklyChange.current_report_date == current_date,
        LargeHolderWeeklyChange.previous_report_date == previous_date,
    )).all()
    items: list[dict[str, Any]] = []
    industries: set[str] = set()
    for change in changes:
        current = summaries.get(change.stock_code)
        if current is None or current.market not in {"上市", "上櫃"}:
            continue
        if market == "listed" and current.market != "上市":
            continue
        if market == "otc" and current.market != "上櫃":
            continue
        if industry and current.industry != industry:
            continue
        if keyword and keyword not in current.stock_code and keyword not in current.stock_name:
            continue
        industries.add(current.industry)
        ratio = float(change.current_ratio_over_400 if kind == "over400" else change.current_ratio_over_1000)
        previous_ratio = float(change.previous_ratio_over_400 if kind == "over400" else change.previous_ratio_over_1000)
        change_pp = float(change.change_pp_over_400 if kind == "over400" else change.change_pp_over_1000)
        change_pct_value = change.change_pct_over_400 if kind == "over400" else change.change_pct_over_1000
        holder_count = current.holders_over_400_count if kind == "over400" else current.holders_over_1000_count
        holder_change = change.holder_count_change_over_400 if kind == "over400" else change.holder_count_change_over_1000
        history = db.scalars(select(LargeHolderWeeklySummary).where(
            LargeHolderWeeklySummary.stock_code == current.stock_code,
        ).order_by(LargeHolderWeeklySummary.report_date.desc()).limit(5)).all()
        history_ratio = [
            float(point.ratio_over_400 if kind == "over400" else point.ratio_over_1000)
            for point in history
        ]
        four_week_change = history_ratio[0] - history_ratio[-1] if len(history_ratio) >= 4 else 0
        score = min(30, max(0, round(change_pp / 2.5 * 30)))
        score += min(20, max(0, round(four_week_change / 4 * 20)))
        score += min(10, max(0, 5 + round(holder_change / 20)))
        if change.anomaly_flag:
            score = max(0, score - 15)
        signal = (
            "大戶明顯加碼" if change_pp >= 1.5
            else "大戶持續加碼" if four_week_change >= 1.5
            else "大戶首次轉增" if change_pp > 0
            else "需持續觀察"
        )
        warnings = [change.anomaly_reason] if change.anomaly_flag else []
        items.append({
            "rank": 0, "stockCode": current.stock_code, "stockName": current.stock_name,
            "market": current.market, "industry": current.industry,
            "latestPrice": None, "weeklyChangePct": None,
            "currentLargeHolderRatio": ratio, "previousLargeHolderRatio": previous_ratio,
            "changePercentagePoint": round(change_pp, 4),
            "changePercentage": float(change_pct_value) if change_pct_value is not None else None,
            "currentHolderCount": holder_count, "holderCountChange": holder_change,
            "foreignNetBuy5d": None, "investmentTrustNetBuy5d": None, "dealerNetBuy5d": None,
            "mainForceNetBuy5d": None, "volumeChange5d": None, "averageTurnover20d": None,
            "technicalStatus": "行情因子待串接", "healthScore": score, "aiSignal": signal,
            "anomalyFlag": change.anomaly_flag, "anomalyReason": change.anomaly_reason,
            "warnings": warnings, "quoteSource": "行情待串接", "quoteTimestamp": "",
        })
    items.sort(key=lambda item: (
        item["changePercentagePoint"], item["currentLargeHolderRatio"], 0,
    ), reverse=True)
    items = items[:limit]
    for rank, item in enumerate(items, 1):
        item["rank"] = rank
    return {
        "type": kind, "currentReportDate": current_date.isoformat(),
        "previousReportDate": previous_date.isoformat(), "updatedAt": datetime.now(UTC).isoformat(),
        "dataMode": "official_tdcc", "dataSource": "臺灣集中保管結算所 OpenAPI",
        "dataNotice": (
            "大戶比例與週增減為官方集保資料；最新行情優先由TWSE MIS補充。"
            "尚未串接的20日均成交金額、法人與主力欄位不計分並顯示暫無資料。"
        ),
        "industries": sorted(industries), "items": items,
    }


def get_large_holder_history(db: Session, stock_code: str, weeks: int = 12) -> dict[str, Any]:
    summaries = db.scalars(
        select(LargeHolderWeeklySummary)
        .where(LargeHolderWeeklySummary.stock_code == stock_code)
        .order_by(LargeHolderWeeklySummary.report_date.desc())
        .limit(weeks)
    ).all()
    if len(summaries) >= 2:
        points = [{
            "reportDate": item.report_date.isoformat(), "stockCode": item.stock_code,
            "ratioOver400": float(item.ratio_over_400), "ratioOver1000": float(item.ratio_over_1000),
            "holdersOver400": item.holders_over_400_count, "holdersOver1000": item.holders_over_1000_count,
            "price": None, "volume": None, "foreignNetBuy": None, "investmentTrustNetBuy": None,
            "dealerNetBuy": None, "mainForceNetBuy": None, "marginBalanceChange": None,
        } for item in reversed(summaries)]
        return {
            "stockCode": stock_code, "stockName": stock_code, "dataMode": "official_tdcc",
            "dataSource": "臺灣集中保管結算所 OpenAPI", "items": points,
            "dataNotice": "大戶比例為官方集保週資料；尚未串接的股價、法人與融資資料顯示暫無資料。",
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
    dict[str, dict[str, str]],
]:
    """Fetch required TDCC rows and best-effort stock metadata enrichment."""
    rows = await tdcc_large_holder_provider.fetch_latest()
    try:
        directory = await tdcc_large_holder_provider.fetch_stock_directory()
    except httpx.HTTPError:
        directory = {}
    return rows, directory
