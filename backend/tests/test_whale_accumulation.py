from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import LargeHolderWeeklySummary, ShareholderDistributionWeekly
from app.services.whale_accumulation import get_whale_accumulation, resolve_comparison_dates
from app.services.whale_market_data import _parse_tables


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _add_period(session: Session, report_date: date, values: dict[int, tuple[str, int, int]]) -> None:
    total_shares = sum(value[2] for value in values.values())
    total_shareholders = sum(value[1] for value in values.values())
    for level, (ratio, holders, shares) in values.items():
        session.add(ShareholderDistributionWeekly(
            stock_code="2330", report_date=report_date, holding_level=level,
            holder_count=holders, share_count=shares, holding_ratio=Decimal(ratio),
            updated_at=datetime(2026, 8, 7),
        ))
    session.add(LargeHolderWeeklySummary(
        stock_code="2330", stock_name="台積電", market="上市", industry="半導體",
        report_date=report_date, holders_over_400_count=20, shares_over_400=400_000,
        ratio_over_400=Decimal("20"), holders_over_1000_count=5,
        shares_over_1000=200_000, ratio_over_1000=Decimal("10"),
        total_shareholders=total_shareholders, total_shares=total_shares,
        updated_at=datetime(2026, 8, 7),
    ))


def _add_raw_period(session: Session, report_date: date, values: dict[int, tuple[str, int, int]]) -> None:
    for level, (ratio, holders, shares) in values.items():
        session.add(ShareholderDistributionWeekly(
            stock_code="2330", report_date=report_date, holding_level=level,
            holder_count=holders, share_count=shares, holding_ratio=Decimal(ratio),
            updated_at=datetime(2026, 8, 7),
        ))


def test_resolve_dates_uses_nearest_start_and_latest_end_not_after_request() -> None:
    dates = [date(2026, 7, 24), date(2026, 7, 31), date(2026, 8, 7)]
    assert resolve_comparison_dates(dates, date(2026, 7, 28), date(2026, 8, 9)) == (
        date(2026, 7, 24), date(2026, 8, 7),
    )


def test_official_accumulation_sums_all_400_plus_levels_and_scores_middle_periods() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _add_period(session, date(2026, 7, 24), {
            1: ("10", 500, 100_000), 2: ("10", 300, 100_000), 3: ("10", 200, 100_000),
            12: ("3", 20, 100_000), 13: ("3", 15, 100_000),
            14: ("4", 10, 100_000), 15: ("10", 5, 100_000),
        })
        _add_period(session, date(2026, 7, 31), {
            1: ("9.5", 480, 100_000), 2: ("9.5", 290, 100_000), 3: ("9.5", 190, 100_000),
            12: ("3.5", 21, 100_000), 13: ("3.5", 16, 100_000),
            14: ("4.5", 11, 100_000), 15: ("11", 6, 100_000),
        })
        _add_period(session, date(2026, 8, 7), {
            1: ("9", 450, 100_000), 2: ("9", 270, 100_000), 3: ("10", 180, 100_000),
            12: ("4", 22, 100_000), 13: ("4", 17, 100_000),
            14: ("4", 12, 100_000), 15: ("12", 7, 100_000),
        })
        session.commit()
        payload = get_whale_accumulation(
            session, date(2026, 7, 28), date(2026, 8, 9), prices={"2330": 50},
        )

    assert payload["actualRange"] == {"start": "2026-07-24", "end": "2026-08-07"}
    item = payload["items"][0]
    assert item["big400Start"] == 20
    assert item["big400End"] == 24
    assert item["big400Change"] == 4
    assert item["big1000Change"] == 2
    assert item["retailChange"] == -2
    assert item["continuousIncreasePeriods"] == 2
    assert item["estimatedIncreaseLots"] == 28
    assert item["estimatedAccumulationValue"] == 1_400_000
    assert item["whaleAccumulationScore"] >= 75
    assert "🔥 大戶強力卡位" in item["signals"]


def test_official_accumulation_keeps_raw_rows_when_summary_metadata_missing() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _add_raw_period(session, date(2026, 7, 24), {
            1: ("10", 500, 100_000), 2: ("10", 300, 100_000), 3: ("10", 200, 100_000),
            12: ("3", 20, 100_000), 13: ("3", 15, 100_000),
            14: ("4", 10, 100_000), 15: ("10", 5, 100_000),
        })
        _add_raw_period(session, date(2026, 8, 7), {
            1: ("9", 450, 100_000), 2: ("9", 270, 100_000), 3: ("10", 180, 100_000),
            12: ("4", 22, 100_000), 13: ("4", 17, 100_000),
            14: ("4", 12, 100_000), 15: ("12", 7, 100_000),
        })
        session.commit()
        payload = get_whale_accumulation(
            session, date(2026, 7, 24), date(2026, 8, 7), prices={"2330": 50},
        )

    assert payload["dataMode"] == "official_tdcc"
    assert payload["totalMatched"] == 1
    item = payload["items"][0]
    assert item["stockCode"] == "2330"
    assert item["stockName"] == "2330"
    assert item["market"] == "未知"
    assert item["industry"] == "未分類"
    assert "部分股票名稱／市場別待補" in payload["dataNotice"]


def test_demo_accumulation_supports_filters_and_top_limit() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        payload = get_whale_accumulation(
            session, date(2026, 7, 1), date(2026, 8, 9),
            ranking_type="big400", limit=20, min_big400=0,
        )
    assert payload["dataMode"] == "demo"
    assert len(payload["items"]) <= 20
    assert all(item["big400Change"] >= 0 for item in payload["items"])
    values = [item["big400Change"] for item in payload["items"]]
    assert values == sorted(values, reverse=True)


def test_market_snapshot_parser_extracts_close_and_volume_by_named_fields() -> None:
    payload = {"tables": [{
        "fields": ["證券代號", "證券名稱", "成交股數", "收盤價"],
        "data": [["2330", "台積電", "12,345,000", "1,125.00"]],
    }]}
    assert _parse_tables(payload, "證券代號", "收盤價", "成交股數") == {
        "2330": (1125.0, 12_345_000),
    }
