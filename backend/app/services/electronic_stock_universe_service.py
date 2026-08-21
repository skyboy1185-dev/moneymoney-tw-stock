from __future__ import annotations

from datetime import date

from ..adaptive_schemas import AdaptiveStockInput
from .theme_stock_universe import is_expanded_theme_symbol


ALLOWED_ELECTRONIC_INDUSTRY_CODES = frozenset({"24", "25", "26", "27", "28", "29", "30", "31"})


def common_filter_failures(
    stock: AdaptiveStockInput,
    parameters: dict[str, float],
    trade_date: date,
) -> list[str]:
    failures: list[str] = []
    if stock.market_type not in {"上市", "上櫃"}: failures.append("非台灣上市或上櫃股票")
    is_official_electronic = stock.is_electronic and stock.industry_code in ALLOWED_ELECTRONIC_INDUSTRY_CODES
    if not is_official_electronic and not is_expanded_theme_symbol(stock.stock_code):
        failures.append("非官方電子產業分類或指定題材股")
    if stock.is_full_delivery: failures.append("全額交割股")
    if stock.is_alternate_trading: failures.append("變更交易方式股票")
    if stock.is_disposed and parameters["universe.exclude_disposed"] >= 1: failures.append("處置股")
    if stock.is_suspended: failures.append("暫停交易股票")
    if stock.is_delisted: failures.append("下市或下櫃股票")
    if not stock.has_recent_trade: failures.append("最近交易日無成交")
    if stock.abnormal_trading: failures.append("重大異常交易股票")
    if stock.price < parameters["universe.minimum_price"]: failures.append("股價低於最低門檻")
    if stock.listing_date is None:
        failures.append("缺少上市櫃日期")
    elif (trade_date - stock.listing_date).days < parameters["universe.minimum_listing_days"] * 1.45:
        failures.append("上市櫃未滿約 60 個交易日")
    if stock.average_volume_20d_shares / 1000 < parameters["universe.minimum_average_volume_lots"]: failures.append("近 20 日平均成交量不足 500 張")
    if stock.average_turnover_20d < parameters["universe.minimum_average_turnover"]: failures.append("近 20 日平均成交金額不足")
    if stock.illiquid_days_5d > parameters["universe.maximum_illiquid_days_5d"]: failures.append("最近 5 日流動性不足")
    if stock.data_completeness < parameters["universe.minimum_data_completeness"]: failures.append("資料缺漏超過允許範圍")
    if not (
        stock.quote_source.startswith("TWSE MIS")
        or stock.quote_source in {"TWSE OpenAPI", "TPEx OpenAPI"}
        or stock.quote_source.startswith("Yahoo Finance")
    ):
        failures.append("行情來源非官方市場資訊")
    return failures
