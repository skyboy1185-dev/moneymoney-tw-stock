"""Durable, mode-isolated records for the unified intraday trading system."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


MONEY = Numeric(20, 2)
PRICE = Numeric(18, 4)
RATE = Numeric(12, 6)


class DayTradeV2Setting(Base):
    __tablename__ = "day_trade_v2_settings"

    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    trade_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="PAPER")
    live_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DayTradeV2Robot(Base):
    __tablename__ = "day_trade_v2_robots"
    __table_args__ = (UniqueConstraint("user_id", "strategy_id", name="uq_dtv2_robot_user_strategy"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False, default="LONG")
    allocation: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="READY")
    status_date: Mapped[date | None] = mapped_column(Date)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DayTradeV2StrategyVersion(Base):
    __tablename__ = "day_trade_v2_strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_dtv2_strategy_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DayTradeV2Signal(Base):
    __tablename__ = "day_trade_v2_signals"
    __table_args__ = (
        Index("ix_dtv2_signal_user_time", "user_id", "signal_time"),
        Index("ix_dtv2_signal_symbol_time", "symbol", "signal_time"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    sector: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    side: Mapped[str] = mapped_column(String(10), nullable=False, default="LONG")
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    risk_reward: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    stop_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    target_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    skip_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    market_context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DayTradeV2Order(Base):
    __tablename__ = "day_trade_v2_orders"
    __table_args__ = (
        UniqueConstraint("user_id", "client_order_id", name="uq_dtv2_order_client"),
        Index("ix_dtv2_order_user_sent", "user_id", "sent_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    signal_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    client_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False, default="LIMIT")
    order_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    order_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    signal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broker_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")


class DayTradeV2Fill(Base):
    __tablename__ = "day_trade_v2_fills"
    __table_args__ = (UniqueConstraint("mode", "broker_execution_id", name="uq_dtv2_fill_execution"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    broker_execution_id: Mapped[str] = mapped_column(String(120), nullable=False)
    price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    exchange_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(40), nullable=False, default="Asia/Taipei")


class DayTradeV2Position(Base):
    __tablename__ = "day_trade_v2_positions"
    __table_args__ = (
        Index("ix_dtv2_position_user_mode", "user_id", "mode", "status"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    signal_id: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    sector: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    side: Mapped[str] = mapped_column(String(10), nullable=False, default="LONG")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    current_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    stop_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    first_target_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    trailing_stop_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    used_capital: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    entry_reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=0)
    risk_reward: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DayTradeV2Trade(Base):
    __tablename__ = "day_trade_v2_trades"
    __table_args__ = (Index("ix_dtv2_trade_user_exit", "user_id", "exit_fill_time"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False, default="LONG")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_order_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_fill_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    exit_signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_order_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_fill_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    buy_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    sell_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    transaction_tax: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    slippage: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    other_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_return_pct: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    entry_reason: Mapped[str] = mapped_column(Text, nullable=False)
    exit_reason: Mapped[str] = mapped_column(Text, nullable=False)
    market_context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    strategy_parameters_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DayTradeV2RiskDaily(Base):
    __tablename__ = "day_trade_v2_risk_daily"
    __table_args__ = (UniqueConstraint("user_id", "mode", "trading_date", name="uq_dtv2_risk_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="NORMAL")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DayTradeV2Notification(Base):
    __tablename__ = "day_trade_v2_notifications"
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="uq_dtv2_notification_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DayTradeV2AuditEvent(Base):
    __tablename__ = "day_trade_v2_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, default="SYSTEM")
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DayTradeV2BacktestJob(Base):
    __tablename__ = "day_trade_v2_backtest_jobs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    backtest_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False, default="ALL")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    data_source: Mapped[str] = mapped_column(String(80), nullable=False)
    data_precision: Mapped[str] = mapped_column(String(30), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DayTradeV2PerformanceSnapshot(Base):
    __tablename__ = "day_trade_v2_performance_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "mode", "period_type", "period_key", "strategy_id", name="uq_dtv2_performance_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False, default="ALL")
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DayTradeV2SystemError(Base):
    __tablename__ = "day_trade_v2_system_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, default="system")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="PAPER")
    component: Mapped[str] = mapped_column(String(80), nullable=False)
    error_code: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
