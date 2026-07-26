from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import LargeHolderWeeklyChange, LargeHolderWeeklySummary
from app.routers import large_holders
from app.services.large_holders import (
    DistributionRow,
    DistributionSummary,
    aggregate_distribution,
    calculate_weekly_change,
    get_large_holder_rankings,
)


def row(level: int, ratio: str, holders: int = 10, shares: int = 1000) -> DistributionRow:
    return DistributionRow(
        stock_code="2330",
        report_date=date(2026, 7, 24),
        holding_level=level,
        holder_count=holders,
        share_count=shares,
        holding_ratio=Decimal(ratio),
    )


def summary(report_date: date, over400: str, over1000: str, shares: int = 100_000) -> DistributionSummary:
    return DistributionSummary(
        stock_code="2330",
        report_date=report_date,
        holders_over_400_count=100,
        shares_over_400=60_000,
        ratio_over_400=Decimal(over400),
        holders_over_1000_count=20,
        shares_over_1000=25_000,
        ratio_over_1000=Decimal(over1000),
        total_shareholders=1_000,
        total_shares=shares,
    )


def test_400_lots_aggregates_all_levels_12_through_15() -> None:
    result = aggregate_distribution([
        row(11, "9"), row(12, "1.1"), row(13, "2.2"), row(14, "3.3"), row(15, "4.4"),
        row(16, "99"), row(17, "100"),
    ])[0]
    assert result.ratio_over_400 == Decimal("11.0")
    assert result.holders_over_400_count == 40
    assert result.shares_over_400 == 4000


def test_1000_lots_uses_every_1000_plus_level_without_total_or_adjustment() -> None:
    result = aggregate_distribution([
        row(12, "1.1"), row(13, "2.2"), row(14, "3.3"), row(15, "7.7"),
        row(16, "8.8"), row(17, "100"),
    ])[0]
    assert result.ratio_over_1000 == Decimal("7.7")
    assert result.holders_over_1000_count == 10


def test_percentage_point_and_percentage_change_are_not_mixed() -> None:
    change = calculate_weekly_change(
        summary(date(2026, 7, 24), "23", "12"),
        summary(date(2026, 7, 17), "20", "10"),
    )
    assert change["change_pp_over_400"] == Decimal("3")
    assert change["change_pct_over_400"] == Decimal("15.000000")
    assert change["change_pp_over_1000"] == Decimal("2")
    assert change["change_pct_over_1000"] == Decimal("20.000000")


def test_large_share_count_change_is_flagged_as_structural_anomaly() -> None:
    change = calculate_weekly_change(
        summary(date(2026, 7, 24), "23", "12", shares=125_000),
        summary(date(2026, 7, 17), "20", "10", shares=100_000),
    )
    assert change["anomaly_flag"] is True
    assert "結構性" in change["anomaly_reason"]


def test_demo_rankings_follow_change_point_desc_and_limit() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        response = get_large_holder_rankings(session, "over400", limit=20, min_average_turnover=0)
    assert response["dataMode"] == "demo"
    assert len(response["items"]) == 20
    points = [item["changePercentagePoint"] for item in response["items"]]
    assert points == sorted(points, reverse=True)


def test_rankings_api_has_two_periods_and_friendly_data_mode() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(large_holders.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    response = TestClient(app).get("/api/v1/large-holders/rankings?type=over1000&limit=20&minAverageTurnover=0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["currentReportDate"]
    assert payload["previousReportDate"]
    assert payload["type"] == "over1000"
    assert payload["dataMode"] == "demo"


def test_two_official_periods_publish_official_tdcc_ranking() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = date(2026, 7, 24)
    previous = date(2026, 7, 17)
    with Session(engine) as session:
        for report_date, ratio in ((previous, Decimal("20")), (now, Decimal("23"))):
            session.add(LargeHolderWeeklySummary(
                stock_code="2330", stock_name="台積電", market="上市", industry="半導體",
                report_date=report_date, holders_over_400_count=100,
                shares_over_400=60_000, ratio_over_400=ratio,
                holders_over_1000_count=20, shares_over_1000=25_000,
                ratio_over_1000=ratio / 2, total_shareholders=1_000,
                total_shares=100_000, updated_at=datetime(2026, 7, 24),
            ))
        session.add(LargeHolderWeeklyChange(
            stock_code="2330", current_report_date=now, previous_report_date=previous,
            current_ratio_over_400=Decimal("23"), previous_ratio_over_400=Decimal("20"),
            change_pp_over_400=Decimal("3"), change_pct_over_400=Decimal("15"),
            current_ratio_over_1000=Decimal("11.5"), previous_ratio_over_1000=Decimal("10"),
            change_pp_over_1000=Decimal("1.5"), change_pct_over_1000=Decimal("15"),
            holder_count_change_over_400=5, holder_count_change_over_1000=2,
            anomaly_flag=False, anomaly_reason="", updated_at=datetime(2026, 7, 24),
        ))
        session.commit()
        response = get_large_holder_rankings(session, "over400", limit=20, min_average_turnover=0)
    assert response["dataMode"] == "official_tdcc"
    assert response["items"][0]["stockCode"] == "2330"
    assert response["items"][0]["changePercentagePoint"] == 3
