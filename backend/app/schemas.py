from datetime import date, datetime

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
