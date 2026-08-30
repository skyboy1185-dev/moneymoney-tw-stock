from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from ..models import LargeHolderWeeklySummary, ShareholderDistributionWeekly
from .large_holders import DEMO_STOCKS, _demo_history


WhaleRankingType = Literal[
    "composite", "big400", "big1000", "lots", "value", "retail", "shareholders",
]
BIG_400_LEVELS = frozenset({12, 13, 14, 15})
BIG_1000_LEVELS = frozenset({15})
RETAIL_LEVELS = frozenset({1, 2, 3})  # TDCC 10 張以下
VALID_LEVELS = frozenset(range(1, 16))
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


def _nearest_start_date(dates: list[date], requested: date) -> date:
    not_after = [value for value in dates if value <= requested]
    return max(not_after) if not_after else min(dates)


def resolve_comparison_dates(
    available_dates: list[date],
    requested_start: date,
    requested_end: date,
) -> tuple[date, date]:
    dates = sorted(set(available_dates))
    if len(dates) < 2:
        raise ValueError("至少需要兩期集保資料才能比較")
    eligible_end = [value for value in dates if value <= requested_end]
    actual_end = max(eligible_end) if eligible_end else dates[0]
    start_candidates = [value for value in dates if value < actual_end]
    if not start_candidates:
        actual_end = dates[1]
        start_candidates = [dates[0]]
    actual_start = _nearest_start_date(start_candidates, requested_start)
    return actual_start, actual_end


def _change_points_score(change: float) -> int:
    if change >= 5:
        return 25
    if change >= 4:
        return 23
    if change >= 3:
        return 20
    if change >= 2:
        return 16
    if change >= 1:
        return 12
    if change >= .5:
        return 7
    if change >= 0:
        return 3
    return 0


def _retail_score(change: float) -> int:
    decrease = -change
    if decrease >= 3:
        return 15
    if decrease >= 2:
        return 12
    if decrease >= 1:
        return 9
    if decrease >= .5:
        return 6
    if decrease > 0:
        return 3
    return 0


def _shareholder_score(change_pct: float) -> int:
    decrease = -change_pct
    if decrease >= 10:
        return 10
    if decrease >= 5:
        return 8
    if decrease >= 2:
        return 6
    if decrease > 0:
        return 3
    return 0


def _price_score(change_pct: float | None) -> int:
    if change_pct is None:
        return 8
    if -10 <= change_pct <= 5:
        return 15
    if change_pct <= 10:
        return 12
    if change_pct <= 15:
        return 8
    if change_pct <= 20:
        return 4
    return 0


def _consecutive_increases(values: list[float]) -> int:
    count = 0
    for index in range(len(values) - 1, 0, -1):
        if values[index] > values[index - 1]:
            count += 1
        else:
            break
    return count


def _trend_consistency(values: list[float]) -> float:
    if len(values) < 2:
        return 0
    positive = sum(values[index] > values[index - 1] for index in range(1, len(values)))
    return positive / (len(values) - 1)


def _aggregate_rows(rows: list[ShareholderDistributionWeekly]) -> dict[str, float | int]:
    valid = [row for row in rows if row.holding_level in VALID_LEVELS]
    return {
        "big400Ratio": sum(float(row.holding_ratio) for row in valid if row.holding_level in BIG_400_LEVELS),
        "big1000Ratio": sum(float(row.holding_ratio) for row in valid if row.holding_level in BIG_1000_LEVELS),
        "retailRatio": sum(float(row.holding_ratio) for row in valid if row.holding_level in RETAIL_LEVELS),
        "totalShareholders": sum(row.holder_count for row in valid),
        "totalShares": sum(row.share_count for row in valid),
        "volume": 0,
    }


def _capital_event_warning(points: list[dict[str, Any]]) -> str:
    for previous, current in zip(points, points[1:], strict=False):
        before = float(previous["totalShares"])
        after = float(current["totalShares"])
        if before > 0 and abs(after / before - 1) >= .10:
            return "集保總股數期間變動超過 10%，可能受減資、增資、分割、合併或面額變更影響"
    return ""


def _build_item(
    stock_code: str,
    stock_name: str,
    market: str,
    industry: str,
    points: list[dict[str, Any]],
    price_source: str,
) -> dict[str, Any]:
    start, end = points[0], points[-1]
    big400_change = float(end["big400Ratio"]) - float(start["big400Ratio"])
    big1000_change = float(end["big1000Ratio"]) - float(start["big1000Ratio"])
    retail_change = float(end["retailRatio"]) - float(start["retailRatio"])
    shareholder_change = int(end["totalShareholders"]) - int(start["totalShareholders"])
    shareholder_change_pct = (
        shareholder_change / int(start["totalShareholders"]) * 100
        if int(start["totalShareholders"]) > 0 else 0.0
    )
    prices = [float(point["price"]) for point in points if point.get("price") and float(point["price"]) > 0]
    average_price = sum(prices) / len(prices) if prices else None
    start_price = float(start["price"]) if start.get("price") else None
    end_price = float(end["price"]) if end.get("price") else average_price
    price_change = (
        (end_price / start_price - 1) * 100
        if start_price and end_price and start_price > 0 else None
    )
    estimated_increase_shares = round(max(0.0, big400_change) / 100 * int(end["totalShares"]))
    estimated_increase_lots = estimated_increase_shares / 1_000
    estimated_value = estimated_increase_shares * average_price if average_price else None
    values400 = [float(point["big400Ratio"]) for point in points]
    values1000 = [float(point["big1000Ratio"]) for point in points]
    streak400 = _consecutive_increases(values400)
    streak1000 = _consecutive_increases(values1000)
    continuation = max(streak400, streak1000)
    consistency = min(_trend_consistency(values400), _trend_consistency(values1000))
    anomaly_reason = _capital_event_warning(points)
    single_period_reversal = (big400_change > 0 or big1000_change > 0) and consistency < .5 and len(points) >= 3
    signals: list[str] = []
    if big400_change >= 2 and big1000_change >= 1 and retail_change <= -1 and shareholder_change < 0 and (price_change is None or price_change <= 15):
        signals.append("🔥 大戶強力卡位")
    elif big400_change >= 1 and big1000_change >= .5 and retail_change < 0 and (price_change is None or price_change <= 10):
        signals.append("🚨 大戶偷掃貨")
    if continuation >= 2 and (price_change is None or price_change <= 10):
        signals.append("🚀 大戶持續吃貨")
    if continuation >= 4:
        continuation_label = "🚀 大戶持續掃貨"
    elif continuation >= 3:
        continuation_label = "🔥 大戶連3期加碼"
    elif continuation >= 2:
        continuation_label = "🟢 大戶連2期加碼"
    else:
        continuation_label = "單期變化"
    missing_fields = []
    if price_change is None:
        missing_fields.append("期間起始收盤價")
    if average_price is None:
        missing_fields.append("期間平均收盤價")
    return {
        "rank": 0,
        "stockCode": stock_code,
        "stockName": stock_name,
        "market": market,
        "industry": industry,
        "latestPrice": end_price,
        "periodPriceChangePct": round(price_change, 4) if price_change is not None else None,
        "averagePrice": round(average_price, 4) if average_price is not None else None,
        "priceSource": price_source,
        "big400Start": round(float(start["big400Ratio"]), 4),
        "big400End": round(float(end["big400Ratio"]), 4),
        "big400Change": round(big400_change, 4),
        "big1000Start": round(float(start["big1000Ratio"]), 4),
        "big1000End": round(float(end["big1000Ratio"]), 4),
        "big1000Change": round(big1000_change, 4),
        "retailStart": round(float(start["retailRatio"]), 4),
        "retailEnd": round(float(end["retailRatio"]), 4),
        "retailChange": round(retail_change, 4),
        "shareholderStart": int(start["totalShareholders"]),
        "shareholderEnd": int(end["totalShareholders"]),
        "shareholderChange": shareholder_change,
        "shareholderChangePct": round(shareholder_change_pct, 4),
        "totalShares": int(end["totalShares"]),
        "estimatedIncreaseShares": estimated_increase_shares,
        "estimatedIncreaseLots": round(estimated_increase_lots, 3),
        "estimatedAccumulationValue": round(estimated_value, 2) if estimated_value is not None else None,
        "continuousIncreasePeriods": continuation,
        "continuationLabel": continuation_label,
        "trendConsistency": round(consistency * 100, 2),
        "signals": signals,
        "chipStatus": signals[0] if signals else continuation_label,
        "anomalyFlag": bool(anomaly_reason),
        "anomalyReason": anomaly_reason,
        "singlePeriodReversal": single_period_reversal,
        "missingFields": missing_fields,
        "scoreBreakdown": {
            "big400": _change_points_score(big400_change),
            "big1000": _change_points_score(big1000_change),
            "retail": _retail_score(retail_change),
            "shareholders": _shareholder_score(shareholder_change_pct),
            "value": 0,
            "priceNotSurged": _price_score(price_change),
        },
        "whaleAccumulationScore": 0,
        "_history": points,
    }


def _finalize_scores(items: list[dict[str, Any]]) -> None:
    valued = sorted(
        (item for item in items if item["estimatedAccumulationValue"] is not None),
        key=lambda item: item["estimatedAccumulationValue"],
        reverse=True,
    )
    value_points: dict[str, int] = {}
    for index, item in enumerate(valued):
        percentile = index / max(1, len(valued))
        points = 10 if percentile <= .01 else 8 if percentile <= .05 else 6 if percentile <= .10 else 4 if percentile <= .20 else 0
        value_points[item["stockCode"]] = points
    for item in items:
        item["scoreBreakdown"]["value"] = value_points.get(item["stockCode"], 0)
        score = sum(item["scoreBreakdown"].values())
        if item["singlePeriodReversal"]:
            score -= 8
        if item["anomalyFlag"]:
            score -= 20
        item["whaleAccumulationScore"] = max(0, min(100, score))


def _sort_value(item: dict[str, Any], ranking_type: WhaleRankingType) -> float:
    if ranking_type == "big400":
        return float(item["big400Change"])
    if ranking_type == "big1000":
        return float(item["big1000Change"])
    if ranking_type == "lots":
        return float(item["estimatedIncreaseLots"])
    if ranking_type == "value":
        return float(item["estimatedAccumulationValue"] or 0)
    if ranking_type == "retail":
        return -float(item["retailChange"])
    if ranking_type == "shareholders":
        return -float(item["shareholderChangePct"])
    return float(item["whaleAccumulationScore"])


def _summary_cards(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = (
        ("big400", "🐳 400張大戶增加王", lambda item: item["big400Change"], "percentagePoint"),
        ("big1000", "🐋 千張大戶增加王", lambda item: item["big1000Change"], "percentagePoint"),
        ("value", "💰 大戶吸金王", lambda item: item["estimatedAccumulationValue"] or 0, "currency"),
        ("score", "🔥 偷掃貨第一名", lambda item: item["whaleAccumulationScore"], "score"),
        ("retail", "👤 散戶撤退最多", lambda item: -item["retailChange"], "negativePercentagePoint"),
    )
    cards = []
    for key, label, getter, value_type in definitions:
        winner = max(items, key=getter) if items else None
        cards.append({
            "key": key, "label": label,
            "stockCode": winner["stockCode"] if winner else None,
            "stockName": winner["stockName"] if winner else None,
            "value": getter(winner) if winner else None,
            "valueType": value_type,
        })
    return cards


def _official_items(
    db: Session,
    actual_start: date,
    actual_end: date,
    prices: dict[str, float],
    price_history: dict[str, dict[date, tuple[float, int]]] | None = None,
) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(ShareholderDistributionWeekly).where(
        ShareholderDistributionWeekly.report_date >= actual_start,
        ShareholderDistributionWeekly.report_date <= actual_end,
        ShareholderDistributionWeekly.holding_level.in_(VALID_LEVELS),
    ).order_by(ShareholderDistributionWeekly.report_date)).all())
    grouped: dict[tuple[str, date], list[ShareholderDistributionWeekly]] = defaultdict(list)
    for row in rows:
        grouped[(row.stock_code, row.report_date)].append(row)
    metadata = {
        item.stock_code: item for item in db.scalars(select(LargeHolderWeeklySummary).where(
            LargeHolderWeeklySummary.report_date == actual_end,
        )).all()
    }
    start_stocks = {stock_code for stock_code, report_date in grouped if report_date == actual_start}
    end_stocks = {stock_code for stock_code, report_date in grouped if report_date == actual_end}
    stocks = sorted(stock_code for stock_code in start_stocks & end_stocks if _is_common_stock_code(stock_code))
    items = []
    for stock_code in stocks:
        summary = metadata.get(stock_code)
        points = []
        report_dates = sorted({report_date for code, report_date in grouped if code == stock_code})
        for report_date in report_dates:
            point = _aggregate_rows(grouped[(stock_code, report_date)])
            historical = (price_history or {}).get(stock_code, {}).get(report_date)
            point.update({
                "reportDate": report_date.isoformat(),
                "price": historical[0] if historical else prices.get(stock_code) if report_date == actual_end else None,
                "volume": historical[1] if historical else None,
            })
            points.append(point)
        if len(points) < 2:
            continue
        items.append(_build_item(
            stock_code,
            _summary_stock_name(summary, stock_code),
            _summary_market(summary),
            _summary_industry(summary),
            points,
            "TWSE／TPEx 實際比較日起迄收盤價；區間均價採可取得比較期價格平均",
        ))
    return items


def _demo_items(actual_start: date, actual_end: date) -> list[dict[str, Any]]:
    items = []
    for stock in DEMO_STOCKS:
        raw = [point for point in _demo_history(stock) if actual_start.isoformat() <= point["reportDate"] <= actual_end.isoformat()]
        points = [{
            "reportDate": point["reportDate"],
            "big400Ratio": point.get("ratioOver400All", point["ratioOver400"] + point["ratioOver1000"]),
            "big1000Ratio": point["ratioOver1000"],
            "retailRatio": point.get("retailRatio", 0),
            "totalShareholders": point.get("totalShareholders", 1),
            "totalShares": point.get("totalShares", 0),
            "price": point["price"],
            "volume": point["volume"],
        } for point in raw]
        if len(points) >= 2:
            items.append(_build_item(stock[0], stock[1], stock[2], stock[3], points, "展示週行情平均收盤價"))
    return items


def resolve_whale_comparison_context(
    db: Session,
    requested_start: date,
    requested_end: date,
) -> tuple[str, date, date, list[date]]:
    official_dates = list(db.scalars(select(distinct(ShareholderDistributionWeekly.report_date)).order_by(
        ShareholderDistributionWeekly.report_date,
    )).all())
    if len(official_dates) >= 2:
        actual_start, actual_end = resolve_comparison_dates(official_dates, requested_start, requested_end)
        return "official_tdcc", actual_start, actual_end, official_dates
    demo_dates = [date.fromisoformat(point["reportDate"]) for point in _demo_history(DEMO_STOCKS[0])]
    actual_start, actual_end = resolve_comparison_dates(demo_dates, requested_start, requested_end)
    return "demo", actual_start, actual_end, demo_dates


def get_whale_accumulation(
    db: Session,
    requested_start: date,
    requested_end: date,
    ranking_type: WhaleRankingType = "composite",
    limit: int = 30,
    keyword: str = "",
    industry: str = "",
    min_big400: float = -100,
    min_big1000: float = -100,
    min_lots: float = 0,
    min_value: float = 0,
    max_price_change: float = 1_000,
    min_score: float = 0,
    prices: dict[str, float] | None = None,
    price_history: dict[str, dict[date, tuple[float, int]]] | None = None,
    include_history: bool = False,
) -> dict[str, Any]:
    if requested_start > requested_end:
        raise ValueError("起始日期不可晚於結束日期")
    context_mode, actual_start, actual_end, available_dates = resolve_whale_comparison_context(
        db, requested_start, requested_end,
    )
    if context_mode == "official_tdcc":
        items = _official_items(db, actual_start, actual_end, prices or {}, price_history)
        data_mode = "official_tdcc"
        data_source = "臺灣集中保管結算所官方股權分散資料"
        notice = "400張以上加總TDCC第12～15級；千張以上採第15級；散戶採10張以下。股價與成交量採TWSE／TPEx實際比較日行情；區間均價以可取得比較期收盤價平均估算。"
    else:
        items = _demo_items(actual_start, actual_end)
        data_mode = "demo"
        data_source = "TDCC 區間分析展示 Adapter"
        notice = "尚未累積兩期官方資料，目前為可重現展示數據，不代表真實持股排名；日期、評分、篩選及趨勢分析流程均與正式模式相同。"
    if data_mode == "official_tdcc" and _has_partial_metadata(items):
        notice = f"{notice} {PARTIAL_METADATA_NOTICE}"
    _finalize_scores(items)
    industries = sorted({str(item["industry"]) for item in items})
    normalized_keyword = keyword.strip().lower()
    filtered = [item for item in items if (
        (not normalized_keyword or normalized_keyword in item["stockCode"].lower() or normalized_keyword in item["stockName"].lower())
        and (not industry or item["industry"] == industry)
        and item["big400Change"] >= min_big400
        and item["big1000Change"] >= min_big1000
        and item["estimatedIncreaseLots"] >= min_lots
        and (item["estimatedAccumulationValue"] or 0) >= min_value
        and (item["periodPriceChangePct"] is None or item["periodPriceChangePct"] <= max_price_change)
        and item["whaleAccumulationScore"] >= min_score
    )]
    filtered.sort(key=lambda item: (
        _sort_value(item, ranking_type), item["whaleAccumulationScore"], item["big400Change"],
    ), reverse=True)
    total_matched = len(filtered)
    result_items = filtered[:limit]
    for rank, item in enumerate(result_items, 1):
        item["rank"] = rank
        if not include_history:
            item.pop("_history", None)
        else:
            item["history"] = item.pop("_history")
    return {
        "rankingType": ranking_type,
        "requestedRange": {"start": requested_start.isoformat(), "end": requested_end.isoformat()},
        "actualRange": {"start": actual_start.isoformat(), "end": actual_end.isoformat()},
        "availableRange": {"start": min(available_dates).isoformat(), "end": max(available_dates).isoformat()},
        "dataMode": data_mode,
        "dataSource": data_source,
        "dataNotice": notice,
        "industries": industries,
        "summaryCards": _summary_cards(filtered),
        "totalMatched": total_matched,
        "items": result_items,
        "updatedAt": datetime.now(UTC).isoformat(),
    }
