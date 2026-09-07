from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


MONEY = Numeric(20, 2)
PRICE = Numeric(20, 4)
RATE = Numeric(12, 4)


class StrongStockSetting(Base):
    __tablename__ = "strong_stock_settings"

    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    paper_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class StrongStockStrategyVersion(Base):
    __tablename__ = "strong_stock_strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strong_stock_strategy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False, default="STRONG_STOCK")
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    data_requirements_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StrongStockAccount(Base):
    __tablename__ = "strong_stock_accounts"

    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="PAPER")
    initial_capital: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("3000000"))
    cash: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("3000000"))
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    trading_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class StrongStockDataRun(Base):
    __tablename__ = "strong_stock_data_runs"
    __table_args__ = (Index("ix_strong_stock_data_run_date", "trade_date", "status"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    source_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    missing_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StrongStockMarketRegime(Base):
    __tablename__ = "strong_stock_market_regimes"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_strong_stock_market_regime_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    regime: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    suggested_exposure_pct: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StrongStockIndustryRanking(Base):
    __tablename__ = "strong_stock_industry_rankings"
    __table_args__ = (UniqueConstraint("trade_date", "industry", name="uq_strong_stock_industry_day"), Index("ix_strong_stock_industry_rank", "trade_date", "rank"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    percentile: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StrongStockRanking(Base):
    __tablename__ = "strong_stock_rankings"
    __table_args__ = (UniqueConstraint("trade_date", "symbol", name="uq_strong_stock_ranking_day"), Index("ix_strong_stock_rank", "trade_date", "rank"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    relative_strength_score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    trend_score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    industry_score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    volume_chip_score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    fundamental_score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    valuation_risk_score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    data_completeness: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    close_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high_52w_distance_pct: Mapped[Decimal | None] = mapped_column(RATE)
    entry_low: Mapped[Decimal | None] = mapped_column(PRICE)
    entry_high: Mapped[Decimal | None] = mapped_column(PRICE)
    breakout_price: Mapped[Decimal | None] = mapped_column(PRICE)
    pullback_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE)
    add_price: Mapped[Decimal | None] = mapped_column(PRICE)
    risk_reward: Mapped[Decimal | None] = mapped_column(RATE)
    suggested_capital: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False, default="WATCH")
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    blocked_reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    score_details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    strategy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StrongStockOrder(Base):
    __tablename__ = "strong_stock_orders"
    __table_args__ = (UniqueConstraint("signal_key", name="uq_strong_stock_order_signal"), Index("ix_strong_stock_order_user_status", "user_id", "status"))

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    signal_key: Mapped[str] = mapped_column(String(160), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False, default="BUY")
    order_type: Mapped[str] = mapped_column(String(20), nullable=False, default="LIMIT")
    limit_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    tranche_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    strategy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    valid_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StrongStockPosition(Base):
    __tablename__ = "strong_stock_positions"
    __table_args__ = (Index("ix_strong_stock_position_user", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    current_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    initial_stop: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    trailing_stop: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    next_add_price: Mapped[Decimal | None] = mapped_column(PRICE)
    invested_capital: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    initial_risk: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    current_score: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    tranches_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class StrongStockTrade(Base):
    __tablename__ = "strong_stock_trades"
    __table_args__ = (Index("ix_strong_stock_trade_user_exit", "user_id", "exit_at"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    position_id: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    buy_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    sell_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    slippage: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    return_pct: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    entry_reason: Mapped[str] = mapped_column(Text, nullable=False)
    exit_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StrongStockEquitySnapshot(Base):
    __tablename__ = "strong_stock_equity_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "trade_date", name="uq_strong_stock_equity_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    market_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    daily_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StrongStockNotification(Base):
    __tablename__ = "strong_stock_notifications"
    __table_args__ = (UniqueConstraint("user_id", "event_key", name="uq_strong_stock_notification_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="NORMAL")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StrongStockBacktestJob(Base):
    __tablename__ = "strong_stock_backtest_jobs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    data_status_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    request_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StrongStockAuditEvent(Base):
    __tablename__ = "strong_stock_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
