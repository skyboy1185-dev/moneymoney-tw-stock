from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
        Index("ix_watchlist_user_added", "user_id", "added_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    added_price: Mapped[float] = mapped_column(Float, nullable=False)
    added_score: Mapped[float] = mapped_column(Float, default=0)
    original_robot_id: Mapped[str] = mapped_column(String(80), default="manual")
    original_robot_name: Mapped[str] = mapped_column(String(120), default="手動加入")
    original_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HoldingItem(Base):
    __tablename__ = "holding_items"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_holding_user_symbol"),
        Index("ix_holding_user_added", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    lots: Mapped[float] = mapped_column(Float, nullable=False)
    buy_date: Mapped[date] = mapped_column(Date, nullable=False)
    original_ai_score: Mapped[float] = mapped_column(Float, default=0)
    original_robot_name: Mapped[str] = mapped_column(String(120), default="手動加入")
    original_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScoreHistory(Base):
    __tablename__ = "ai_score_history"
    __table_args__ = (Index("ix_score_history_symbol_time", "user_id", "symbol", "recorded_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DayTradingSignal(Base):
    __tablename__ = "day_trading_signals"
    __table_args__ = (Index("ix_day_signals_generated", "generated_at"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_min: Mapped[float] = mapped_column(Float, nullable=False)
    entry_max: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    target_1: Mapped[float] = mapped_column(Float, nullable=False)
    target_2: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    health_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_reward_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="confirmed")
    data_source: Mapped[str] = mapped_column(String(80), default="mock_stream")
    quote_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DayTradingRecommendationHistory(Base):
    __tablename__ = "day_trading_recommendation_history"
    __table_args__ = (
        UniqueConstraint("signal_id", name="uq_day_recommendation_history_signal"),
        Index("ix_day_recommendation_history_date", "trading_date", "recommended_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(80), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DayTradingPosition(Base):
    __tablename__ = "day_trading_positions"
    __table_args__ = (Index("ix_day_positions_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(80))
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    target_1: Mapped[float] = mapped_column(Float, nullable=False)
    target_2: Mapped[float] = mapped_column(Float, nullable=False)
    trailing_stop: Mapped[float | None] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_profit: Mapped[float] = mapped_column(Float, default=0)
    health_score: Mapped[float] = mapped_column(Float, default=80)
    latest_action: Mapped[str] = mapped_column(String(80), default="續抱")
    status: Mapped[str] = mapped_column(String(20), default="open")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[float | None] = mapped_column(Float)
    realized_profit: Mapped[float | None] = mapped_column(Float)
    sound_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DayTradingAlert(Base):
    __tablename__ = "day_trading_alerts"
    __table_args__ = (Index("ix_day_alerts_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    position_id: Mapped[int | None] = mapped_column(Integer)
    signal_id: Mapped[str | None] = mapped_column(String(80))
    alert_level: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DayTradingTrade(Base):
    __tablename__ = "day_trading_trades"
    __table_args__ = (Index("ix_day_trades_user_exit", "user_id", "exit_time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, default=0)
    tax: Mapped[float] = mapped_column(Float, default=0)
    slippage: Mapped[float] = mapped_column(Float, default=0)
    profit: Mapped[float] = mapped_column(Float, nullable=False)
    return_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    max_profit: Mapped[float] = mapped_column(Float, default=0)
    max_loss: Mapped[float] = mapped_column(Float, default=0)
    entry_reason: Mapped[str] = mapped_column(Text, default="模擬進場")
    exit_reason: Mapped[str] = mapped_column(Text, default="手動結束")
    strategy_name: Mapped[str] = mapped_column(String(120), default="AI 當沖多空機器人")
    followed_signal: Mapped[bool] = mapped_column(Boolean, default=True)


class DayTradingSettings(Base):
    __tablename__ = "day_trading_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_day_settings_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    capital: Mapped[float] = mapped_column(Float, default=1_000_000)
    max_risk_per_trade: Mapped[float] = mapped_column(Float, default=0.5)
    max_daily_loss: Mapped[float] = mapped_column(Float, default=2.0)
    max_daily_trades: Mapped[int] = mapped_column(Integer, default=5)
    max_position_percentage: Mapped[float] = mapped_column(Float, default=20)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=3)
    minimum_risk_reward: Mapped[float] = mapped_column(Float, default=1.5)
    maximum_spread: Mapped[float] = mapped_column(Float, default=0.5)
    minimum_volume: Mapped[float] = mapped_column(Float, default=1_000_000)
    minimum_turnover: Mapped[float] = mapped_column(Float, default=100_000_000)
    latest_entry_time: Mapped[str] = mapped_column(String(5), default="10:30")
    close_reminder_time: Mapped[str] = mapped_column(String(5), default="13:25")
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sound_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    entry_notification: Mapped[bool] = mapped_column(Boolean, default=True)
    exit_notification: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_notification: Mapped[bool] = mapped_column(Boolean, default=True)
    target_notification: Mapped[bool] = mapped_column(Boolean, default=True)
    data_alert_notification: Mapped[bool] = mapped_column(Boolean, default=True)
    high_confidence_only: Mapped[bool] = mapped_column(Boolean, default=False)
    minimum_confidence: Mapped[int] = mapped_column(Integer, default=75)
    notification_cooldown: Mapped[int] = mapped_column(Integer, default=60)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1)


class DayTradingScheduleSettings(Base):
    __tablename__ = "day_trading_schedule_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_day_schedule_settings_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Taipei")
    preheat_time: Mapped[str] = mapped_column(String(5), default="08:30")
    stock_pool_time: Mapped[str] = mapped_column(String(5), default="08:45")
    health_check_time: Mapped[str] = mapped_column(String(5), default="08:55")
    market_open_time: Mapped[str] = mapped_column(String(5), default="09:00")
    market_close_time: Mapped[str] = mapped_column(String(5), default="13:30")
    warmup_minutes: Mapped[int] = mapped_column(Integer, default=3)
    recommendation_refresh_seconds: Mapped[int] = mapped_column(Integer, default=10)
    replacement_score_gap: Mapped[int] = mapped_column(Integer, default=5)
    minimum_retention_minutes: Mapped[int] = mapped_column(Integer, default=3)
    minimum_live_samples: Mapped[int] = mapped_column(Integer, default=3)
    maximum_stop_distance: Mapped[float] = mapped_column(Float, default=3.0)


class LineNotificationGroup(Base):
    __tablename__ = "line_notification_groups"
    __table_args__ = (
        UniqueConstraint("group_id", name="uq_line_notification_group_id"),
        Index("ix_line_groups_active", "active", "bound_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="LINE 群組")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LineNotificationSettings(Base):
    __tablename__ = "line_notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    opening_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    long_entry_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    short_entry_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    long_exit_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    short_cover_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_loss_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    data_alert_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    closing_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LineDeliveryLog(Base):
    __tablename__ = "line_delivery_logs"
    __table_args__ = (
        UniqueConstraint("group_id", "dedupe_key", name="uq_line_delivery_group_dedupe"),
        Index("ix_line_delivery_status_priority", "status", "priority", "created_at"),
        Index("ix_line_delivery_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(80))
    symbol: Mapped[str | None] = mapped_column(String(12))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_status: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(String(500))
    message_preview: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GmailDeliveryLog(Base):
    __tablename__ = "gmail_delivery_logs"
    __table_args__ = (
        UniqueConstraint("recipient", "dedupe_key", name="uq_gmail_delivery_recipient_dedupe"),
        Index("ix_gmail_delivery_status_created", "status", "created_at"),
        Index("ix_gmail_delivery_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient: Mapped[str] = mapped_column(String(254), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(120))
    symbol: Mapped[str | None] = mapped_column(String(12))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(220), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(500))
    message_preview: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LineWebhookEvent(Base):
    __tablename__ = "line_webhook_events"
    __table_args__ = (UniqueConstraint("webhook_event_id", name="uq_line_webhook_event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    webhook_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    group_id_masked: Mapped[str | None] = mapped_column(String(40))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIStockLineGroup(Base):
    __tablename__ = "ai_stock_line_groups"
    __table_args__ = (
        UniqueConstraint("group_id", name="uq_ai_stock_line_group_id"),
        Index("ix_ai_stock_line_groups_active", "active", "bound_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="AI 選股通知群組")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIStockLineDeliveryLog(Base):
    __tablename__ = "ai_stock_line_delivery_logs"
    __table_args__ = (
        UniqueConstraint("group_id", "dedupe_key", name="uq_ai_stock_line_delivery_group_dedupe"),
        Index("ix_ai_stock_line_delivery_status", "status", "priority", "created_at"),
        Index("ix_ai_stock_line_delivery_symbol", "symbol", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(120))
    symbol: Mapped[str | None] = mapped_column(String(12))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_status: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(String(500))
    message_preview: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIStockLineWebhookEvent(Base):
    __tablename__ = "ai_stock_line_webhook_events"
    __table_args__ = (
        UniqueConstraint("webhook_event_id", name="uq_ai_stock_line_webhook_event_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    webhook_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    group_id_masked: Mapped[str | None] = mapped_column(String(40))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PortfolioSettings(Base):
    __tablename__ = "portfolio_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_portfolio_settings_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    total_capital: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("1000000"))
    minimum_cash_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("20"))
    max_total_exposure: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("85"))
    max_position_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("20"))
    max_industry_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("35"))
    max_risk_per_trade: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0.5"))
    max_portfolio_risk: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("3"))
    maximum_add_on_count: Mapped[int] = mapped_column(Integer, default=2)
    initial_entry_ratio: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("40"))
    first_add_on_ratio: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("30"))
    second_add_on_ratio: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("30"))
    allow_add_on: Mapped[bool] = mapped_column(Boolean, default=True)
    prohibit_averaging_down: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIStockMonitor(Base):
    __tablename__ = "ai_stock_monitor"
    __table_args__ = (
        UniqueConstraint("user_id", "signal_id", name="uq_ai_monitor_user_signal"),
        Index("ix_ai_monitor_user_status", "user_id", "monitor_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    market: Mapped[str] = mapped_column(String(20), default="上市")
    industry: Mapped[str] = mapped_column(String(80), default="未分類")
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False)
    secondary_strategies_json: Mapped[str] = mapped_column(Text, default="[]")
    signal_id: Mapped[str] = mapped_column(String(120), nullable=False)
    monitor_status: Mapped[str] = mapped_column(String(40), default="monitoring")
    total_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    strategy_fit: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    market_fit: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    health_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entry_min: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entry_max: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_1: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_2: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    risk_reward_ratio: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    target_allocation_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    initial_allocation_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    first_add_on_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    second_add_on_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    suggested_initial_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    suggested_initial_quantity: Mapped[int] = mapped_column(Integer, default=0)
    estimated_risk_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    quote_source: Mapped[str] = mapped_column(String(80), nullable=False)
    quote_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIStockPosition(Base):
    __tablename__ = "ai_stock_positions"
    __table_args__ = (Index("ix_ai_positions_user_status", "user_id", "position_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("ai_stock_monitor.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    industry: Mapped[str] = mapped_column(String(80), default="未分類")
    direction: Mapped[str] = mapped_column(String(12), default="long")
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    original_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_1: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_2: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    trailing_stop: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    current_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    highest_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    lowest_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    max_unrealized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    max_unrealized_loss: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    realized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    unrealized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    return_percentage: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"))
    health_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("80"))
    latest_action: Mapped[str] = mapped_column(String(80), default="持有中")
    position_status: Mapped[str] = mapped_column(String(40), default="holding")
    overnight_status: Mapped[bool] = mapped_column(Boolean, default=False)
    target_allocation_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    initial_allocation_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    current_allocation_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    invested_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    available_add_on_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    add_on_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_risk_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    industry_exposure_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    line_exit_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    add_on_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    exit_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIStockAddOn(Base):
    __tablename__ = "ai_stock_add_ons"
    __table_args__ = (UniqueConstraint("position_id", "add_on_number", name="uq_ai_add_on_stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("ai_stock_positions.id"), nullable=False)
    add_on_number: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_price_min: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    suggested_price_max: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    suggested_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    suggested_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    suggested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    actual_quantity: Mapped[int | None] = mapped_column(Integer)
    previous_average_cost: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    new_average_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    previous_stop_loss: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    new_stop_loss: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="suggested")
    signal_id: Mapped[str] = mapped_column(String(120), nullable=False)
    suggested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIStockPartialExit(Base):
    __tablename__ = "ai_stock_partial_exits"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("ai_stock_positions.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))
    realized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIStockAlert(Base):
    __tablename__ = "ai_stock_alerts"
    __table_args__ = (
        UniqueConstraint("user_id", "signal_id", "action", name="uq_ai_alert_signal_action"),
        Index("ix_ai_alerts_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    monitor_id: Mapped[int | None] = mapped_column(Integer)
    position_id: Mapped[int | None] = mapped_column(Integer)
    signal_id: Mapped[str] = mapped_column(String(120), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(40), nullable=False)
    alert_level: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    line_push_status: Mapped[str] = mapped_column(String(20), default="pending")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShareholderDistributionWeekly(Base):
    __tablename__ = "shareholder_distribution_weekly"
    __table_args__ = (
        UniqueConstraint("stock_code", "report_date", "holding_level", name="uq_holder_distribution_stock_date_level"),
        Index("ix_holder_distribution_date_stock", "report_date", "stock_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    holding_level: Mapped[int] = mapped_column(Integer, nullable=False)
    holder_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    share_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    holding_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LargeHolderWeeklySummary(Base):
    __tablename__ = "large_holder_weekly_summary"
    __table_args__ = (
        UniqueConstraint("stock_code", "report_date", name="uq_large_holder_summary_stock_date"),
        Index("ix_large_holder_summary_date", "report_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), default="")
    market: Mapped[str] = mapped_column(String(20), default="未知")
    industry: Mapped[str] = mapped_column(String(80), default="未分類")
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    holders_over_400_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shares_over_400: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ratio_over_400: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    holders_over_1000_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shares_over_1000: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ratio_over_1000: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    total_shareholders: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LargeHolderWeeklyChange(Base):
    __tablename__ = "large_holder_weekly_change"
    __table_args__ = (
        UniqueConstraint("stock_code", "current_report_date", "previous_report_date", name="uq_large_holder_change_period"),
        Index("ix_large_holder_change_current", "current_report_date", "change_pp_over_400"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    current_report_date: Mapped[date] = mapped_column(Date, nullable=False)
    previous_report_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_ratio_over_400: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    previous_ratio_over_400: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    change_pp_over_400: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    change_pct_over_400: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    current_ratio_over_1000: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    previous_ratio_over_1000: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    change_pp_over_1000: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    change_pct_over_1000: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    holder_count_change_over_400: Mapped[int] = mapped_column(BigInteger, nullable=False)
    holder_count_change_over_1000: Mapped[int] = mapped_column(BigInteger, nullable=False)
    anomaly_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    anomaly_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LargeHolderMonitor(Base):
    __tablename__ = "large_holder_monitors"
    __table_args__ = (
        UniqueConstraint("user_id", "stock_code", name="uq_large_holder_monitor_user_stock"),
        Index("ix_large_holder_monitor_user_active", "user_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    monitor_type: Mapped[str] = mapped_column(String(20), default="over400")
    line_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChipFlowSnapshot(Base):
    __tablename__ = "chip_flow_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "stock_id",
            "snapshot_time",
            name="uq_chip_flow_stock_date_minute",
        ),
        Index("ix_chip_flow_stock_date_time", "stock_id", "trade_date", "snapshot_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_id: Mapped[str] = mapped_column(String(12), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    large_buy_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    large_sell_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    large_net_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    medium_buy_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    medium_sell_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    medium_net_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    small_buy_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    small_sell_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    small_net_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    unknown_shares: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketRegime(Base):
    __tablename__ = "market_regime"
    __table_args__ = (
        UniqueConstraint("trade_date", name="uq_market_regime_trade_date"),
        Index("ix_market_regime_current", "is_current", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    regime: Mapped[str] = mapped_column(String(20), nullable=False)
    provisional_regime: Mapped[str] = mapped_column(String(20), nullable=False)
    regime_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    taiex_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    otc_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    electronic_index_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    breadth_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    volume_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    institutional_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    volatility_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    confirmation_days: Mapped[int] = mapped_column(Integer, default=1)
    recommended_exposure_min: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("20"))
    recommended_exposure_max: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("40"))
    trigger_reasons: Mapped[str] = mapped_column(Text, default="[]")
    indicators_json: Mapped[str] = mapped_column(Text, default="{}")
    source_status_json: Mapped[str] = mapped_column(Text, default="{}")
    missing_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    switched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ElectronicIndustryMapping(Base):
    __tablename__ = "electronic_industry_mapping"
    __table_args__ = (
        UniqueConstraint("stock_code", name="uq_electronic_mapping_stock"),
        Index("ix_electronic_mapping_enabled", "is_electronic", "is_enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    market_type: Mapped[str] = mapped_column(String(20), nullable=False)
    industry_code: Mapped[str] = mapped_column(String(10), nullable=False)
    main_industry: Mapped[str] = mapped_column(String(80), nullable=False)
    sub_industry: Mapped[str] = mapped_column(String(80), nullable=False)
    listing_date: Mapped[date | None] = mapped_column(Date)
    is_electronic: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ElectronicIndustryStrength(Base):
    __tablename__ = "electronic_industry_strength"
    __table_args__ = (
        UniqueConstraint("trade_date", "sub_industry", name="uq_electronic_strength_date_industry"),
        Index("ix_electronic_strength_rank", "trade_date", "strength_rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    sub_industry: Mapped[str] = mapped_column(String(80), nullable=False)
    return_1d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_3d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_5d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_20d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    advance_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    new_high_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    volume_growth: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    foreign_net_buy: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    investment_trust_net_buy: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    large_holder_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    strength_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    strength_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    continuation_days: Mapped[int] = mapped_column(Integer, default=0)
    score_breakdown_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdaptiveStockCandidate(Base):
    __tablename__ = "adaptive_stock_candidates"
    __table_args__ = (
        UniqueConstraint("trade_date", "stock_code", "strategy_type", name="uq_adaptive_candidate_date_stock_strategy"),
        Index("ix_adaptive_candidate_rank", "trade_date", "total_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    market_type: Mapped[str] = mapped_column(String(20), nullable=False)
    main_industry: Mapped[str] = mapped_column(String(80), nullable=False)
    sub_industry: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(20), nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    technical_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    chip_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    fundamental_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    industry_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    market_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    health_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    previous_health_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    current_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entry_price_low: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entry_price_high: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    breakout_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_1: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_2: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    allocation_percent: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    relative_strength: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    volume_status: Mapped[str] = mapped_column(String(80), default="資料不足")
    foreign_net_buy: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    investment_trust_net_buy: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    holder_400_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    holder_1000_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    retail_holder_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    margin_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    industry_strength: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    false_breakout_risk: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    candidate_status: Mapped[str] = mapped_column(String(40), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score_breakdown_json: Mapped[str] = mapped_column(Text, default="{}")
    selected_reasons: Mapped[str] = mapped_column(Text, default="[]")
    risk_reasons: Mapped[str] = mapped_column(Text, default="[]")
    missing_data_json: Mapped[str] = mapped_column(Text, default="[]")
    quote_source: Mapped[str] = mapped_column(String(120), nullable=False)
    quote_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdaptiveStockMonitoring(Base):
    __tablename__ = "stock_monitoring"
    __table_args__ = (
        UniqueConstraint("user_id", "stock_code", name="uq_adaptive_monitor_user_stock"),
        Index("ix_adaptive_monitor_status", "user_id", "monitor_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(20), nullable=False)
    added_date: Mapped[date] = mapped_column(Date, nullable=False)
    trigger_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_1: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_2: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    allocation_percent: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    health_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    monitor_status: Mapped[str] = mapped_column(String(40), default="monitoring")
    last_signal: Mapped[str | None] = mapped_column(String(80))
    last_notification_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StrategyParameter(Base):
    __tablename__ = "strategy_parameters"
    __table_args__ = (
        UniqueConstraint("parameter_group", "parameter_name", name="uq_strategy_parameter_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parameter_group: Mapped[str] = mapped_column(String(80), nullable=False)
    parameter_name: Mapped[str] = mapped_column(String(120), nullable=False)
    parameter_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdaptiveSignal(Base):
    __tablename__ = "adaptive_signals"
    __table_args__ = (
        UniqueConstraint("signal_key", name="uq_adaptive_signal_key"),
        Index("ix_adaptive_signals_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_key: Mapped[str] = mapped_column(String(180), nullable=False)
    stock_code: Mapped[str | None] = mapped_column(String(12))
    stock_name: Mapped[str | None] = mapped_column(String(80))
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_type: Mapped[str | None] = mapped_column(String(20))
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    health_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    line_push_status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdaptivePaperTrade(Base):
    __tablename__ = "adaptive_paper_trades"
    __table_args__ = (
        UniqueConstraint("entry_signal_key", name="uq_adaptive_paper_trade_entry_signal"),
        Index("ix_adaptive_paper_trade_status", "status", "entry_time"),
        Index("ix_adaptive_paper_trade_exit", "exit_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_signal_key: Mapped[str] = mapped_column(String(180), nullable=False)
    exit_signal_key: Mapped[str | None] = mapped_column(String(180))
    quantity_shares: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_reason: Mapped[str] = mapped_column(Text, nullable=False)
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_1: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_2: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    last_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_reason: Mapped[str | None] = mapped_column(Text)
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    trading_cost: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    net_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    return_percentage: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    unrealized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LongTermPosition(Base):
    __tablename__ = "long_term_positions"
    __table_args__ = (
        UniqueConstraint("entry_key", name="uq_long_term_position_entry_key"),
        Index("ix_long_term_position_mode_status", "portfolio_mode", "status"),
        Index("ix_long_term_position_entry_date", "entry_date", "portfolio_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_key: Mapped[str] = mapped_column(String(120), nullable=False)
    portfolio_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    market_type: Mapped[str] = mapped_column(String(20), nullable=False)
    industry: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    model_key: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    minimum_exit_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    last_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    selection_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    current_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    predicted_month_return_pct: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    allocation_weight_pct: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False, default=Decimal("10"))
    allocated_capital: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("100000"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    exit_reason: Mapped[str | None] = mapped_column(Text)
    actual_return_pct: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LongTermPositionSnapshot(Base):
    __tablename__ = "long_term_position_snapshots"
    __table_args__ = (
        UniqueConstraint("position_id", "trade_date", name="uq_long_term_snapshot_position_date"),
        Index("ix_long_term_snapshot_date", "trade_date", "position_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("long_term_positions.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    actual_return_pct: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    predicted_month_return_pct: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LongTermPortfolioRun(Base):
    __tablename__ = "long_term_portfolio_runs"
    __table_args__ = (
        UniqueConstraint("portfolio_mode", "trade_date", name="uq_long_term_run_mode_date"),
        Index("ix_long_term_run_date", "trade_date", "portfolio_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    portfolio_nav: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False, default=Decimal("100"))
    daily_return_pct: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LongTermBenchmark(Base):
    __tablename__ = "long_term_benchmarks"

    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    last_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LongTermBenchmarkSelection(Base):
    """Daily snapshot of the stocks with the highest trailing 10-year CAGR."""

    __tablename__ = "long_term_benchmark_selections"
    __table_args__ = (
        UniqueConstraint("selection_date", "rank", name="uq_long_term_benchmark_selection_rank"),
        Index("ix_long_term_benchmark_selection_date", "selection_date", "rank"),
    )

    selection_date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    annualized_return_10y: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    history_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    history_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    history_start_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    history_end_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LongTermBacktestCache(Base):
    """Persist expensive strategy backtests so restarts and multiple users share one result."""

    __tablename__ = "long_term_backtest_cache"

    backtest_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LongTermTradeEvent(Base):
    __tablename__ = "long_term_trade_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_long_term_trade_event_key"),
        Index("ix_long_term_trade_event_mode_id", "portfolio_mode", "id"),
        Index("ix_long_term_trade_event_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(140), nullable=False)
    portfolio_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    position_id: Mapped[int] = mapped_column(ForeignKey("long_term_positions.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="long")
    event_type: Mapped[str] = mapped_column(String(12), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    allocation_weight_pct: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    allocated_capital: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    pnl_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketAccount(Base):
    __tablename__ = "rocket_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("1000000"))
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("1000000"))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    broker_fee_discount: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False, default=Decimal("0.6"))
    slippage_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False, default=Decimal("0.001"))
    sound_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketMarketRegime(Base):
    __tablename__ = "rocket_market_regime"
    __table_args__ = (
        UniqueConstraint("trade_date", name="uq_rocket_regime_date"),
        Index("ix_rocket_regime_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    regime: Mapped[str] = mapped_column(String(30), nullable=False)
    regime_label: Mapped[str] = mapped_column(String(40), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    maximum_exposure_pct: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    strategy_label: Mapped[str] = mapped_column(String(120), nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    indicators_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    missing_fields_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketSectorStrength(Base):
    __tablename__ = "rocket_sector_strength"
    __table_args__ = (
        UniqueConstraint("trade_date", "sector_name", name="uq_rocket_sector_date_name"),
        Index("ix_rocket_sector_date_rank", "trade_date", "strength_rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    sector_name: Mapped[str] = mapped_column(String(80), nullable=False)
    strength_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    strength_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    return_1d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_3d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_5d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_20d: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    advance_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    new_high_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    volume_growth: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    breakdown_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketCandidate(Base):
    __tablename__ = "rocket_candidates"
    __table_args__ = (
        UniqueConstraint("trade_date", "stock_code", name="uq_rocket_candidate_date_stock"),
        Index("ix_rocket_candidate_date_rank", "trade_date", "rank"),
        Index("ix_rocket_candidate_status", "trade_date", "candidate_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    market_type: Mapped[str] = mapped_column(String(20), nullable=False)
    sector_name: Mapped[str] = mapped_column(String(80), nullable=False)
    sector_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    is_top5: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    candidate_status: Mapped[str] = mapped_column(String(30), nullable=False)
    pattern_type: Mapped[str] = mapped_column(String(40), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(30), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    change_pct: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    rocket_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    chase_risk_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    sector_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    momentum_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    volume_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    pattern_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    chip_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    institutional_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    data_availability_pct: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    volume_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    breakout_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_1: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_2: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    risk_reward_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    atr: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    ma5: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    ma10: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    ma20: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    missing_data_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    score_breakdown_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketSignal(Base):
    __tablename__ = "rocket_signals"
    __table_args__ = (
        UniqueConstraint("signal_key", name="uq_rocket_signal_key"),
        Index("ix_rocket_signal_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_key: Mapped[str] = mapped_column(String(180), nullable=False)
    stock_code: Mapped[str | None] = mapped_column(String(12))
    stock_name: Mapped[str | None] = mapped_column(String(80))
    signal_type: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    rocket_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    chase_risk: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    strategy_type: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketPosition(Base):
    __tablename__ = "rocket_positions"
    __table_args__ = (
        Index("ix_rocket_position_status", "status", "updated_at"),
        Index("ix_rocket_position_stock", "stock_code", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    sector_name: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(40), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_allocation: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    original_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    add_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    take_profit_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    trailing_stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    target_price_1: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_price_2: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    highest_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    lowest_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    rocket_score_entry: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    rocket_score_current: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    chase_risk_current: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    invested_cost: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    max_favorable_excursion: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    max_adverse_excursion: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    latest_action: Mapped[str] = mapped_column(String(80), nullable=False, default="持有")
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    exit_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketTrade(Base):
    __tablename__ = "rocket_trades"
    __table_args__ = (Index("ix_rocket_trade_time", "executed_at", "stock_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("rocket_positions.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(40), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    tax: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    slippage: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketDailyPortfolio(Base):
    __tablename__ = "rocket_daily_portfolio"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_rocket_portfolio_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    daily_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    cumulative_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RocketNotification(Base):
    __tablename__ = "rocket_notifications"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_rocket_notification_dedupe"),
        Index("ix_rocket_notification_created", "created_at"),
        Index("ix_rocket_notification_unread", "is_read", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stock_code: Mapped[str | None] = mapped_column(String(12))
    stock_name: Mapped[str | None] = mapped_column(String(80))
    notification_type: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    rocket_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    chase_risk: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    quantity: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    pnl_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    strategy_type: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
