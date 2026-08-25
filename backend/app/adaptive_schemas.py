from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


MarketRegimeName = Literal["CRASH", "RECOVERY", "RANGE", "BREAKOUT", "UNCERTAIN"]


class AdaptiveMarketMetrics(BaseModel):
    trade_date: date
    updated_at: datetime
    market_open: bool = False
    official_data: bool = True
    taiex_close: float | None = None
    otc_close: float | None = None
    electronic_close: float | None = None
    semiconductor_close: float | None = None
    taiex_return_1d: float | None = None
    otc_return_1d: float | None = None
    electronic_return_1d: float | None = None
    taiex_return_5d: float | None = None
    taiex_return_10d: float | None = None
    taiex_return_20d: float | None = None
    taiex_return_60d: float | None = None
    electronic_return_20d: float | None = None
    taiex_above_ma5: bool | None = None
    taiex_above_ma20: bool | None = None
    taiex_above_ma60: bool | None = None
    electronic_above_ma20: bool | None = None
    electronic_above_ma60: bool | None = None
    ma5_slope: float | None = None
    ma20_slope: float | None = None
    ma60_slope: float | None = None
    atr20_ratio: float | None = None
    adx14: float | None = None
    volume_ratio_20d: float | None = None
    advance_ratio: float | None = None
    advance_ratio_2d: float | None = None
    limit_up_count: int | None = None
    limit_down_count: int | None = None
    new_high_20d_ratio: float | None = None
    new_low_20d_ratio: float | None = None
    new_low_ratio_change: float | None = None
    electronic_turnover_share: float | None = None
    foreign_net_5d: float | None = None
    trust_net_5d: float | None = None
    futures_bias: float | None = None
    taiex_new_low: bool | None = None
    electronic_new_low: bool | None = None
    taiex_breakout_20d: bool | None = None
    taiex_breakout_60d: bool | None = None
    higher_low: bool | None = None
    panic_volume_contracted: bool | None = None
    up_volume_expanding: bool | None = None
    foreign_selling_shrinking: bool | None = None
    otc_relative_strength: bool | None = None
    electronic_long_black_days: int | None = None
    sector_continuation_days: int | None = None
    bollinger_width_percentile: float | None = None
    source_status: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


class AdaptiveIndustryInput(BaseModel):
    sub_industry: str = Field(min_length=1, max_length=80)
    return_1d: float | None = None
    return_3d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    advance_ratio: float | None = None
    new_high_ratio: float | None = None
    volume_growth: float | None = None
    foreign_net_buy: float | None = None
    investment_trust_net_buy: float | None = None
    large_holder_change: float | None = None
    relative_taiex: float | None = None
    relative_electronic: float | None = None
    continuation_days: int = 0


class AdaptiveStockInput(BaseModel):
    stock_code: str = Field(pattern=r"^\d{4,6}$")
    stock_name: str = Field(min_length=1, max_length=80)
    market_type: Literal["上市", "上櫃"]
    industry_code: str = Field(min_length=1, max_length=10)
    main_industry: str = Field(min_length=1, max_length=80)
    sub_industry: str = Field(min_length=1, max_length=80)
    listing_date: date | None = None
    is_electronic: bool
    is_full_delivery: bool = False
    is_alternate_trading: bool = False
    is_disposed: bool = False
    is_suspended: bool = False
    is_delisted: bool = False
    has_recent_trade: bool = True
    abnormal_trading: bool = False
    data_completeness: float = Field(default=1, ge=0, le=1)
    quote_source: str
    quote_timestamp: datetime
    price: float = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    volume_shares: float = Field(ge=0)
    average_volume_20d_shares: float = Field(ge=0)
    average_turnover_20d: float = Field(ge=0)
    illiquid_days_5d: int = Field(default=0, ge=0, le=5)
    return_1d: float = 0
    return_3d: float = 0
    return_5d: float = 0
    return_20d: float = 0
    gap_percent: float = 0
    consecutive_strong_up_days: int = 0
    consecutive_long_bullish_days: int = 0
    is_highest_volume_20d: bool = False
    market_return_20d: float = 0
    electronic_return_20d: float = 0
    relative_strength_market: float = 0
    relative_strength_electronic: float = 0
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma5_slope: float | None = None
    ma20_slope: float | None = None
    ma60_slope: float | None = None
    atr14: float | None = None
    atr20_ratio: float | None = None
    adx14: float | None = None
    rsi14: float | None = None
    macd_histogram: float | None = None
    macd_histogram_rising: bool | None = None
    bollinger_width_percentile: float | None = None
    range_low: float | None = None
    range_high: float | None = None
    range_amplitude: float | None = None
    range_position: float | None = None
    breakout_20d: bool = False
    breakout_60d: bool = False
    breakout_percent: float = 0
    distance_to_high_percent: float | None = None
    volume_ratio_5d: float | None = None
    volume_ratio_20d: float | None = None
    close_location: float | None = None
    upper_shadow_ratio: float | None = None
    higher_low: bool = False
    bottom_reversal_candle: bool = False
    volume_contracting: bool = False
    down_volume_less_than_up: bool = False
    foreign_net_5d: float | None = None
    trust_net_5d: float | None = None
    holder_400_change: float | None = None
    holder_1000_change: float | None = None
    retail_holder_change: float | None = None
    margin_change: float | None = None
    short_sale_change: float | None = None
    revenue_yoy: float | None = None
    revenue_3m_yoy: float | None = None
    latest_eps: float | None = None
    trailing_eps: float | None = None
    gross_margin_change: float | None = None
    operating_margin_change: float | None = None
    fundamental_risk: bool = False
    industry_strength_score: float = Field(default=0, ge=0, le=100)
    industry_rank_percentile: float = Field(default=1, ge=0, le=1)
    industry_continuation_days: int = 0
    same_industry_strong_count: int = 0


class AdaptiveScanPayload(BaseModel):
    market: AdaptiveMarketMetrics
    industries: list[AdaptiveIndustryInput] = Field(default_factory=list, max_length=80)
    stocks: list[AdaptiveStockInput] = Field(default_factory=list, max_length=1200)
    data_sources: list[str] = Field(default_factory=list, max_length=30)


class AdaptiveMonitorCreate(BaseModel):
    stock_code: str = Field(pattern=r"^\d{4,6}$")


class AdaptiveParameterUpdate(BaseModel):
    parameter_group: str = Field(min_length=1, max_length=80)
    parameter_name: str = Field(min_length=1, max_length=120)
    parameter_value: float
    description: str = Field(default="", max_length=500)
    is_enabled: bool = True


class AdaptiveParameterBatchUpdate(BaseModel):
    items: list[AdaptiveParameterUpdate] = Field(min_length=1, max_length=100)


class AdaptiveBacktestPrice(BaseModel):
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)


class AdaptiveBacktestRequest(BaseModel):
    stock_code: str = Field(pattern=r"^\d{4,6}$")
    stock_name: str = Field(min_length=1, max_length=80)
    strategy_type: Literal["RECOVERY", "RANGE", "BREAKOUT"]
    years: Literal[1, 3, 5]
    prices: list[AdaptiveBacktestPrice] = Field(min_length=80, max_length=1500)
    benchmark_prices: list[AdaptiveBacktestPrice] = Field(default_factory=list, max_length=1500)
    commission_rate: float = Field(default=0.001425, ge=0, le=0.02)
    commission_discount: float = Field(default=0.2, ge=0, le=1)
    tax_rate: float = Field(default=0.003, ge=0, le=0.02)
    slippage_rate: float = Field(default=0.001, ge=0, le=0.02)
