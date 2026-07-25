from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    symbol: str = Field(pattern=r"^\d{4,6}$")
    added_score: float = Field(default=0, ge=0, le=100)
    robot_id: str = "manual"
    robot_name: str = "手動加入"
    reasons: list[str] = Field(default_factory=list, max_length=3)


class HoldingCreate(BaseModel):
    symbol: str = Field(pattern=r"^\d{4,6}$")
    cost: float = Field(gt=0)
    lots: float = Field(gt=0)
    buy_date: date
    ai_score: float = Field(default=0, ge=0, le=100)
    robot_name: str = "手動加入"
    reasons: list[str] = Field(default_factory=list, max_length=3)


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    database: str
    mock_data: bool
    checked_at: datetime


class DayPositionCreate(BaseModel):
    signal_id: str
    direction: str = Field(pattern=r"^(long|short)$")
    entry_price: float = Field(gt=0)
    quantity: float = Field(default=1, gt=0, le=1000)


class DayPositionUpdate(BaseModel):
    stop_loss: float | None = Field(default=None, gt=0)
    trailing_stop: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0, le=1000)
    sound_enabled: bool | None = None
    action: str | None = None


class DayPositionClose(BaseModel):
    exit_price: float | None = Field(default=None, gt=0)
    percentage: int = Field(default=100, ge=1, le=100)
    reason: str = Field(default="使用者手動確認", max_length=200)


class DayTradingSettingsUpdate(BaseModel):
    capital: float = Field(gt=0)
    max_risk_per_trade: float = Field(ge=0.1, le=10)
    max_daily_loss: float = Field(ge=0.1, le=20)
    max_daily_trades: int = Field(ge=1, le=100)
    max_position_percentage: float = Field(ge=1, le=100)
    max_consecutive_losses: int = Field(ge=1, le=20)
    minimum_risk_reward: float = Field(ge=0.5, le=10)
    maximum_spread: float = Field(ge=0, le=10)
    minimum_volume: float = Field(ge=0)
    minimum_turnover: float = Field(ge=0)
    latest_entry_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    close_reminder_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    notification_enabled: bool
    sound_enabled: bool
    entry_notification: bool
    exit_notification: bool
    stop_notification: bool
    target_notification: bool
    data_alert_notification: bool
    high_confidence_only: bool
    minimum_confidence: int = Field(ge=0, le=100)
    notification_cooldown: int = Field(ge=0, le=3600)
    repeat_count: int = Field(ge=1, le=10)
    timezone: str = Field(default="Asia/Taipei", pattern=r"^Asia/Taipei$")
    preheat_time: str = Field(default="08:30", pattern=r"^\d{2}:\d{2}$")
    stock_pool_time: str = Field(default="08:45", pattern=r"^\d{2}:\d{2}$")
    health_check_time: str = Field(default="08:55", pattern=r"^\d{2}:\d{2}$")
    market_open_time: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    market_close_time: str = Field(default="13:30", pattern=r"^\d{2}:\d{2}$")
    warmup_minutes: Literal[0, 1, 3, 5, 10] = 3
    recommendation_refresh_seconds: Literal[5, 10, 15, 30] = 10
    replacement_score_gap: int = Field(default=5, ge=0, le=30)
    minimum_retention_minutes: int = Field(default=3, ge=0, le=30)
    minimum_live_samples: int = Field(default=3, ge=2, le=100)
    maximum_stop_distance: float = Field(default=3.0, ge=0.1, le=20)
