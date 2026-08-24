from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PatternCandleInput(BaseModel):
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    turnover: float = Field(ge=0)

    @field_validator("high")
    @classmethod
    def high_is_valid(cls, value: float, info):
        data = info.data
        if data.get("open") and value < data["open"]:
            raise ValueError("high must be at least open")
        return value


class PatternStockInput(BaseModel):
    stock_code: str = Field(pattern=r"^[0-9A-Z]{4,7}$")
    stock_name: str
    market_type: str
    sector_name: str = "其他"
    listing_date: date | None = None
    is_etf: bool = False
    is_etn: bool = False
    is_warrant: bool = False
    is_disposed: bool = False
    is_full_delivery: bool = False
    current_price: float = Field(gt=0)
    current_volume: float = Field(ge=0)
    current_turnover: float = Field(ge=0)
    vwap: float | None = None
    quote_time: datetime
    quote_realtime: bool = False
    quote_source: str
    close_complete: bool = False
    adjusted_prices: list[PatternCandleInput]
    actual_prices: list[PatternCandleInput]


class PatternScanPayload(BaseModel):
    trade_date: date
    generated_at: datetime
    is_trading_day: bool
    market_regime: Literal["strong_bull", "bull", "neutral", "bear", "strong_bear"] = "neutral"
    market_score: float = 50
    stocks: list[PatternStockInput]
    sources: list[str]
    source_status: dict[str, str] = {}


class PatternSettingsUpdate(BaseModel):
    robotMode: Literal["SWING", "DAY_TRADE", "ALERT_ONLY"] | None = None
    performanceMode: Literal["BACKTEST", "PAPER_LIVE", "MANUAL_PAPER"] | None = None
    initialCapital: float | None = Field(default=None, gt=0)
    maxPositions: int | None = Field(default=None, ge=1, le=20)
    maxPositionPct: float | None = Field(default=None, gt=0, le=30)
    maxSectorPct: float | None = Field(default=None, gt=0, le=60)
    riskPerTradePct: float | None = Field(default=None, gt=0, le=1)
    minimumScore: float | None = Field(default=None, ge=60, le=100)
    minimumRiskReward: float | None = Field(default=None, ge=1, le=10)
    pivotWindow: int | None = Field(default=None, ge=3, le=7)
    minimumSwingPct: float | None = Field(default=None, ge=3, le=8)
    allowProbe: bool | None = None
    allowAdd: bool | None = None
    trailingStopEnabled: bool | None = None
    openingReminderEnabled: bool | None = None
    brokerFeeDiscount: float | None = Field(default=None, gt=0, le=1)
    slippageRate: float | None = Field(default=None, ge=0, le=.02)
    dayTradeCloseTime: str | None = Field(default=None, pattern=r"^(?:0\d|1\d|2[0-3]):[0-5]\d$")


class PatternWatchlistCreate(BaseModel):
    stockCode: str
    patternType: str
    detectionId: int | None = None
    reminderOnly: bool = False


class PatternManualTrade(BaseModel):
    quantity: int = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    reason: str = Field(min_length=2, max_length=500)


class PatternPositionUpdate(BaseModel):
    stopLossPrice: float | None = Field(default=None, gt=0)
    takeProfit1: float | None = Field(default=None, gt=0)
    takeProfit2: float | None = Field(default=None, gt=0)
    trailingStopPrice: float | None = Field(default=None, gt=0)
    autoTradePaused: bool | None = None
    note: str | None = Field(default=None, max_length=1000)
    reason: str = Field(min_length=2, max_length=500)


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
