from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.adaptive_schemas import AdaptiveIndustryInput, AdaptiveMarketMetrics, AdaptiveScanPayload, AdaptiveStockInput
from app.database import Base
from app.services import strong_stock as strategy
from app.services.strong_stock_replay import FreeMinuteHistory, TAIPEI, replay, select_archives
from app.strong_stock_models import StrongStockScanArchive, StrongStockTrade


def payload_fixture():
    at = datetime(2026, 9, 7, 7, tzinfo=UTC)
    stocks = [AdaptiveStockInput(
        stock_code=str(2300+i), stock_name=f'Stock {i}', market_type='上市', industry_code='24',
        main_industry='電子', sub_industry='半導體', is_electronic=True,
        quote_source='test', quote_timestamp=at, price=100, open=99, high=101, low=98,
        volume_shares=2_000_000, average_volume_20d_shares=1_000_000, average_turnover_20d=200_000_000,
        return_1d=1, return_5d=5+i, return_20d=12+i, ma20=95, ma60=90,
        ma20_slope=1, ma60_slope=.5, atr14=3, atr20_ratio=3,
        breakout_20d=True, range_high=100, volume_ratio_20d=1.8, higher_low=True,
        upper_shadow_ratio=.1, foreign_net_5d=100, trust_net_5d=50, revenue_yoy=15,
        revenue_3m_yoy=12, trailing_eps=5, gross_margin_change=1, operating_margin_change=1,
        distance_to_high_percent=5,
    ) for i in range(5)]
    return AdaptiveScanPayload(
        market=AdaptiveMarketMetrics(trade_date=at.date(), updated_at=at, official_data=True,
            taiex_above_ma20=True, taiex_above_ma60=True, ma20_slope=1, ma60_slope=.5,
            advance_ratio=62, new_high_20d_ratio=15), stocks=stocks,
        industries=[AdaptiveIndustryInput(sub_industry='半導體', return_5d=15, return_20d=30,
            relative_taiex=25, advance_ratio=90, new_high_ratio=20, volume_growth=2, continuation_days=5)],
    ), at


def ticks(day):
    prices = [100, 105, 125, 94]
    return {datetime.combine(day, time(9, i+1), TAIPEI): Decimal(p) for i, p in enumerate(prices)}


def test_replay_matches_live_functions_including_add_reduce_stop_and_cash():
    payload, at = payload_fixture()
    day = date(2026, 9, 8)
    result = replay({day: {'id': 'fixture', 'payload': payload, 'observedAt': at}}, {}, lambda s, m, d: ticks(d))
    assert result['actions'] == {'queued': 1, 'filled': 1, 'added': 1, 'reduced': 1, 'closed': 1}
    assert result['tradeCount'] == 2
    assert result['openPositionCount'] == 0
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        strategy.ensure_defaults(db, 'live-reference')
        strategy.scan_and_persist(db, payload, at)
        strategy.queue_paper_orders(db, 'live-reference', payload.market.trade_date)
        for timestamp, price in ticks(day).items():
            strategy.fill_pending_orders(db, 'live-reference', {'2304': price}, timestamp)
            strategy.monitor_positions(db, 'live-reference', {'2304': price}, timestamp)
        p = strategy.performance(db, 'live-reference')
        trades = list(db.scalars(select(StrongStockTrade).order_by(StrongStockTrade.exit_at)))
        assert result['totalReturnPct'] == p['totalReturnPct']
        assert Decimal(result['totalPnl']) == Decimal(p['totalEquity']) - Decimal(p['initialCapital'])
        assert [t['netPnl'] for t in result['trades']] == [str(t.net_pnl) for t in trades]


def test_current_parameters_are_used_and_no_trade_is_valid():
    payload, at = payload_fixture()
    result = replay({date(2026, 9, 8): {'id': 'fixture', 'payload': payload, 'observedAt': at}},
                    {'minimumEntryScore': '100'}, lambda *args: pytest.fail('No orders should request quotes'))
    assert result['tradeCount'] == 0
    assert result['actions']['queued'] == 0
    assert result['winRate'] is None
    assert Decimal(result['totalPnl']) == 0


def test_incomplete_fundamentals_are_not_replaced_with_price_volume_strategy():
    payload, at = payload_fixture()
    for stock in payload.stocks:
        stock.trailing_eps = None
    result = replay({date(2026, 9, 8): {'id': 'fixture', 'payload': payload, 'observedAt': at}}, {},
                    lambda *args: pytest.fail('Incomplete fundamentals must prevent entry'))
    assert result['actions']['filled'] == 0


def test_future_and_intraday_archives_cannot_supply_previous_close_signals():
    payload, at = payload_fixture()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(StrongStockScanArchive(id='late', trade_date=payload.market.trade_date,
            observed_at=datetime(2026, 9, 8, 2, tzinfo=UTC), strategy_version='1.0.0',
            config_json='{}', payload_json=payload.model_dump_json()))
        db.commit()
        selected, missing = select_archives(db, [(date(2026, 9, 8), date(2026, 9, 7))])
        assert selected == {} and missing == ['2026-09-07']
        db.add(StrongStockScanArchive(id='valid', trade_date=payload.market.trade_date,
            observed_at=at, strategy_version='1.0.0', config_json='{}', payload_json=payload.model_dump_json()))
        db.commit()
        selected, missing = select_archives(db, [(date(2026, 9, 8), date(2026, 9, 7))])
        assert not missing and selected[date(2026, 9, 8)]['id'] == 'valid'


def test_minute_closes_are_not_used_before_available_and_require_full_session():
    day = date(2026, 9, 8)
    start = datetime.combine(day, time(9), TAIPEI)
    timestamps = [int((start + timedelta(minutes=i)).timestamp()) for i in range(265)]
    timestamps.append(int(datetime.combine(day, time(13, 30), TAIPEI).timestamp()))
    data = {'chart': {'result': [{'timestamp': timestamps, 'indicators': {'quote': [{'close': [100] * 266}]}}]}}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))) as client:
        loader = FreeMinuteHistory(client, day, day)
        values = loader('2330', '上市', day)
        assert min(values).time() == time(9, 1)
        assert max(values).time() == time(13, 30)
        assert len(loader.frozen['2330.TW:2026-09-08']) == 266
        with pytest.raises(ValueError, match='不完整'):
            loader('2330', '上市', day + timedelta(days=1))


def test_null_price_minutes_are_not_filled_forward():
    day = date(2026, 9, 8)
    start = datetime.combine(day, time(9), TAIPEI)
    timestamps = [int((start + timedelta(minutes=i)).timestamp()) for i in range(265)]
    closes = [100] * 265
    closes[3] = None
    data = {'chart': {'result': [{'timestamp': timestamps, 'indicators': {'quote': [{'close': closes}]}}]}}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))) as client:
        loader = FreeMinuteHistory(client, day, day)
        values = loader('2330', '上市', day)
        assert len(values) == 264
        assert start + timedelta(minutes=4) not in values


def test_full_worker_persists_performance_and_keeps_live_ledger_empty(monkeypatch):
    from app.services import strong_stock_replay as service
    from app.strong_stock_models import StrongStockBacktestJob, StrongStockAccount
    from sqlalchemy import func
    payload, at = payload_fixture()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service, 'SessionLocal', lambda: Session(engine))

    class Loader:
        frozen = {'fixture': 'test-only'}

        def __call__(self, symbol, market, day):
            return ticks(day)

    monkeypatch.setattr(service, 'FreeMinuteHistory', lambda *args: Loader())
    with Session(engine) as db:
        db.add(StrongStockScanArchive(id='fixture', trade_date=at.date(), observed_at=at,
            strategy_version='1.0.0', config_json='{}', payload_json=payload.model_dump_json()))
        db.add(StrongStockBacktestJob(id='full-job', user_id='full-user', start_date=date(2026, 9, 8),
            end_date=date(2026, 9, 8), status='RUNNING', request_json=json.dumps({'parameters': {}})))
        db.commit()
    service.run_job('full-job')
    with Session(engine) as db:
        job = db.get(StrongStockBacktestJob, 'full-job')
        assert job.status == 'COMPLETED'
        assert json.loads(job.result_json)['actions']['added'] == 1
        assert json.loads(job.data_status_json)['scanArchiveIds'] == ['fixture']
        assert db.scalar(select(func.count()).select_from(StrongStockAccount)) == 0


def test_full_api_freezes_current_parameters_and_schedules_shared_worker(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from app.database import get_db
    from app.routers.strong_stock import router
    from app.services import strong_stock_history, strong_stock_replay
    from app.strong_stock_models import StrongStockBacktestJob, StrongStockSetting
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(StrongStockSetting(user_id='parameter-user', config_json='{"minimumEntryScore":"99"}'))
        db.commit()
    calls = []
    monkeypatch.setattr(strong_stock_history, 'history_coverage', lambda *args: {'ready': True})
    monkeypatch.setattr(strong_stock_replay, 'run_job', lambda job_id: calls.append(job_id))
    app = FastAPI()
    app.include_router(router)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    response = TestClient(app).post('/strong-stocks/backtests', headers={'x-user-id': 'parameter-user'},
        json={'start_date': '2026-09-01', 'end_date': '2026-09-07', 'mode': 'FULL'})
    assert response.status_code == 200 and response.json()['status'] == 'RUNNING'
    with Session(engine) as db:
        job = db.get(StrongStockBacktestJob, calls[0])
        assert json.loads(job.request_json)['parameters']['minimumEntryScore'] == '99'
