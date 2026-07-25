from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
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
    minimum_volume: Mapped[float] = mapped_column(Float, default=500_000)
    minimum_turnover: Mapped[float] = mapped_column(Float, default=50_000_000)
    latest_entry_time: Mapped[str] = mapped_column(String(5), default="13:20")
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


class LineWebhookEvent(Base):
    __tablename__ = "line_webhook_events"
    __table_args__ = (UniqueConstraint("webhook_event_id", name="uq_line_webhook_event_id"),)

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
