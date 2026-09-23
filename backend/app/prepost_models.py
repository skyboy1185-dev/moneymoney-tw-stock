from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class PrePostAnalysisDay(Base):
    __tablename__ = "prepost_analysis_days"
    __table_args__ = (Index("ix_prepost_day_status", "trade_date", "status"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="NOT_STARTED", nullable=False)
    current_phase: Mapped[str] = mapped_column(String(32), default="WAITING", nullable=False)
    market_bias: Mapped[str] = mapped_column(String(40), default="資料尚未取得", nullable=False)
    market_score: Mapped[float | None] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(20), default="資料尚未取得", nullable=False)
    opening_pattern: Mapped[str] = mapped_column(String(80), default="資料尚未取得", nullable=False)
    strong_industries_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    weak_industries_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    day_trade_advice: Mapped[str] = mapped_column(Text, default="等待資料", nullable=False)
    stock_advice: Mapped[str] = mapped_column(Text, default="等待資料", nullable=False)
    recommended_position: Mapped[str] = mapped_column(String(40), default="0%～20%", nullable=False)
    support_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    resistance_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prediction_result: Mapped[str | None] = mapped_column(String(20))
    prediction_details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    market_snapshot_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    score_breakdown_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    pre_market_prediction_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    analysis_error: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrePostAnalysisMessage(Base):
    __tablename__ = "prepost_analysis_messages"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_prepost_message_dedupe"),
        Index("ix_prepost_message_day_period", "trade_date", "period", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    importance: Mapped[str] = mapped_column(String(16), default="NORMAL", nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    market_impact: Mapped[str] = mapped_column(Text, default="資料尚未取得", nullable=False)
    industry_impact_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    stock_impact_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    operation_advice: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    data_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_update: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    parent_message_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("prepost_analysis_messages.message_id"))
    notification_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mail_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_marked_important: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_followed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrePostNotificationSetting(Base):
    __tablename__ = "prepost_notification_settings"
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    pre_market: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    post_market: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    important_events: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    emergency_events: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mail_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    minimum_importance: Mapped[str] = mapped_column(String(16), default="IMPORTANT", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrePostPendingEvent(Base):
    __tablename__ = "prepost_pending_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    target_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    data_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
