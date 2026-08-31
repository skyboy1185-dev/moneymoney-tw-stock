import asyncio
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import LargeHolderWeeklyChange, LargeHolderWeeklySummary, ShareholderDistributionWeekly
from app.routers import large_holders
from app.services.large_holders import (
    DistributionRow,
    DistributionSummary,
    TdccOpenDataProvider,
    aggregate_distribution,
    calculate_weekly_change,
    get_large_holder_rankings,
    persist_latest_distribution,
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


def test_400_to_600_lot_bucket_uses_only_official_level_12() -> None:
    result = aggregate_distribution([
        row(11, "9"), row(12, "1.1"), row(13, "2.2"), row(14, "3.3"), row(15, "4.4"),
        row(16, "99"), row(17, "100"),
    ])[0]
    assert result.ratio_over_400 == Decimal("1.1")
    assert result.holders_over_400_count == 10
    assert result.shares_over_400 == 1000


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
            session.add_all([
                ShareholderDistributionWeekly(
                    stock_code="2330", report_date=report_date, holding_level=12,
                    holder_count=100 if report_date == previous else 105,
                    share_count=60_000 if report_date == previous else 66_000,
                    holding_ratio=ratio, updated_at=datetime(2026, 7, 24),
                ),
                ShareholderDistributionWeekly(
                    stock_code="2330", report_date=report_date, holding_level=15,
                    holder_count=20 if report_date == previous else 22,
                    share_count=25_000 if report_date == previous else 30_000,
                    holding_ratio=ratio / 2, updated_at=datetime(2026, 7, 24),
                ),
            ])
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
    assert response["items"][0]["currentLotCount"] == 66
    assert response["items"][0]["previousLotCount"] == 60
    assert response["items"][0]["lotCountChange"] == 6


def test_official_rankings_keep_raw_rows_when_summary_metadata_missing_for_all_market() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = date(2026, 7, 24)
    previous = date(2026, 7, 17)
    with Session(engine) as session:
        session.add_all([
            ShareholderDistributionWeekly(
                stock_code="2330", report_date=previous, holding_level=12,
                holder_count=100, share_count=60_000, holding_ratio=Decimal("20"),
                updated_at=datetime(2026, 7, 24),
            ),
            ShareholderDistributionWeekly(
                stock_code="2330", report_date=now, holding_level=12,
                holder_count=105, share_count=66_000, holding_ratio=Decimal("23"),
                updated_at=datetime(2026, 7, 24),
            ),
        ])
        session.commit()

        all_market = get_large_holder_rankings(session, "over400", limit=20, market="all", min_average_turnover=0)
        listed_only = get_large_holder_rankings(session, "over400", limit=20, market="listed", min_average_turnover=0)

    assert all_market["dataMode"] == "official_tdcc"
    assert len(all_market["items"]) == 1
    assert all_market["items"][0]["stockCode"] == "2330"
    assert all_market["items"][0]["stockName"] == "2330"
    assert all_market["items"][0]["market"] == "未知"
    assert all_market["items"][0]["industry"] == "未分類"
    assert "部分股票名稱／市場別待補" in all_market["dataNotice"]
    assert listed_only["items"] == []


def test_already_synced_distribution_repairs_missing_metadata_without_overwriting_valid_names() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    report_date = date(2026, 7, 24)
    with Session(engine) as session:
        session.add_all([
            LargeHolderWeeklySummary(
                stock_code="2330", stock_name="2330", market="未知", industry="未分類",
                report_date=report_date, holders_over_400_count=100,
                shares_over_400=60_000, ratio_over_400=Decimal("20"),
                holders_over_1000_count=20, shares_over_1000=25_000,
                ratio_over_1000=Decimal("10"), total_shareholders=1_000,
                total_shares=100_000, updated_at=datetime(2026, 7, 24),
            ),
            LargeHolderWeeklySummary(
                stock_code="2317", stock_name="鴻海", market="上市", industry="其他電子",
                report_date=report_date, holders_over_400_count=100,
                shares_over_400=60_000, ratio_over_400=Decimal("20"),
                holders_over_1000_count=20, shares_over_1000=25_000,
                ratio_over_1000=Decimal("10"), total_shareholders=1_000,
                total_shares=100_000, updated_at=datetime(2026, 7, 24),
            ),
            LargeHolderWeeklySummary(
                stock_code="4150", stock_name="4150", market="未知", industry="未分類",
                report_date=date(2026, 7, 17), holders_over_400_count=100,
                shares_over_400=60_000, ratio_over_400=Decimal("20"),
                holders_over_1000_count=20, shares_over_1000=25_000,
                ratio_over_1000=Decimal("10"), total_shareholders=1_000,
                total_shares=100_000, updated_at=datetime(2026, 7, 17),
            ),
        ])
        session.commit()

        result = persist_latest_distribution(
            session,
            [
                DistributionRow("2330", report_date, 12, 100, 60_000, Decimal("20")),
                DistributionRow("2330", report_date, 15, 20, 25_000, Decimal("10")),
            ],
            {
                "2330": {"name": "台積電", "market": "上市", "industry": "半導體"},
                "2317": {"name": "錯誤名稱", "market": "上櫃", "industry": "錯誤產業"},
                "4150": {"name": "\u512a\u4f60\u5eb7", "market": "\u672a\u77e5", "industry": "\u672a\u5206\u985e"},
            },
        )

        repaired = session.scalar(select(LargeHolderWeeklySummary).where(
            LargeHolderWeeklySummary.stock_code == "2330",
        ))
        preserved = session.scalar(select(LargeHolderWeeklySummary).where(
            LargeHolderWeeklySummary.stock_code == "2317",
        ))
        previous_period = session.scalar(select(LargeHolderWeeklySummary).where(
            LargeHolderWeeklySummary.stock_code == "4150",
        ))

    assert result["status"] == "already_synced"
    assert result["metadataRepairCount"] == 1
    assert repaired is not None
    assert repaired.stock_name == "台積電"
    assert repaired.market == "上市"
    assert repaired.industry == "半導體"
    assert preserved is not None
    assert preserved.stock_name == "鴻海"
    assert preserved.market == "上市"
    assert preserved.industry == "其他電子"
    assert previous_period is not None
    assert previous_period.stock_name == "4150"


def test_stock_directory_includes_tpex_emerging_and_company_profile_fallbacks(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, rows: list[dict[str, str]], status_code: int = 200) -> None:
            self._rows = rows
            self.status_code = status_code

        def json(self) -> list[dict[str, str]]:
            return self._rows

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise AssertionError(f"unexpected failed response {self.status_code}")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, **kwargs) -> FakeResponse:
            if "STOCK_DAY_ALL" in url:
                return FakeResponse([{"Code": "2330", "Name": "台積電", "ClosingPrice": "1200"}])
            if "tpex_mainboard_daily_close_quotes" in url:
                return FakeResponse([])
            if "tpex_esb_latest_statistics" in url:
                return FakeResponse([{"SecuritiesCompanyCode": "4150", "CompanyName": "優你康", "LatestPrice": "1.29"}])
            if "mopsfin_t187ap03_O" in url:
                return FakeResponse([{
                    "SecuritiesCompanyCode": "4747",
                    "CompanyName": "強生化學製藥廠股份有限公司",
                    "CompanyAbbreviation": "強生製藥",
                }])
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("app.services.large_holders._directory_cache", None)
    monkeypatch.setattr("app.services.large_holders.httpx.AsyncClient", FakeAsyncClient)

    directory = asyncio.run(TdccOpenDataProvider().fetch_stock_directory())

    assert directory["4150"]["name"] == "優你康"
    assert directory["4150"]["market"] == "未知"
    assert directory["4747"]["name"] == "強生製藥"
    assert directory["4747"]["market"] == "上櫃"
