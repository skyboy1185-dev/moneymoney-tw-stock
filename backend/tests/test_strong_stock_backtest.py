from datetime import date, timedelta

import pytest

from app.services.strong_stock_backtest import simulate


def fixture():
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(70)]
    bars = {day: (100 + i / 10, 100.1 + i / 10, 99.9 + i / 10, 100 + i / 10, 2_000_000)
            for i, day in enumerate(days)}
    # Signal only known at day 61 close; doubled volume with next-open gap.
    bars[days[61]] = (106, 107, 105.9, 107, 4_000_000)
    bars[days[62]] = (108, 109, 107.5, 108.5, 2_000_000)
    for day in days[63:]:
        bars[day] = (108.5, 110, 108, 109, 2_000_000)
    history = {'bars': bars, 'dividends': {}, 'splits': []}
    benchmark = {'bars': {d: (100, 101, 99, 100, 1_000_000) for d in days}, 'dividends': {}, 'splits': []}
    return days, history, benchmark


def test_next_open_costs_dividends_and_equity_reconcile():
    days, history, benchmark = fixture()
    history['dividends'][days[64]] = 1
    result = simulate({'2330.TW': history}, benchmark, days[61], days[-1])
    assert result['tradeCount'] == 1
    trade = result['trades'][0]
    assert trade['entryDate'] == days[62].isoformat()
    assert trade['entryPrice'] == pytest.approx(108 * 1.0005)
    assert trade['exitPrice'] == pytest.approx(109 * .9995)
    assert trade['dividends'] == trade['quantity']
    assert trade['tax'] > 0 and trade['buyFee'] >= 20 and trade['sellFee'] >= 20
    assert result['totalPnl'] == trade['netPnl']
    assert result['equityCurve'][-1]['equity'] == 3_000_000 + result['totalPnl']
    assert result['openPositionCount'] == 0


def test_future_bars_do_not_change_existing_signals_or_curve():
    days, history, benchmark = fixture()
    before = simulate({'2330.TW': history}, benchmark, days[61], days[-1])
    history['bars'][days[-1] + timedelta(days=1)] = (999, 1000, 900, 950, 1_000_000)
    after = simulate({'2330.TW': history}, benchmark, days[61], days[-1])
    assert before == after


def test_gap_stop_and_no_signal_day_execution():
    days, history, benchmark = fixture()
    history['bars'][days[63]] = (100, 101, 99, 100, 2_000_000)
    result = simulate({'2330.TW': history}, benchmark, days[61], days[-1])
    assert result['trades'][0]['exitPrice'] == pytest.approx(100 * .9995)
    assert result['trades'][0]['netPnl'] < 0
    assert result['maxDrawdownPct'] < 0


def test_no_trades_has_null_win_rate():
    days, history, benchmark = fixture()
    for d, bar in list(history['bars'].items()):
        history['bars'][d] = (*bar[:4], 1)
    result = simulate({'2330.TW': history}, benchmark, days[61], days[-1])
    assert result['tradeCount'] == 0
    assert result['winRate'] is None
    assert result['totalPnl'] == 0


def test_missing_day_and_splits_are_not_silently_valued():
    days, history, benchmark = fixture()
    del history['bars'][days[64]]
    with pytest.raises(ValueError, match='完整日 K'):
        simulate({'2330.TW': history}, benchmark, days[61], days[-1])
    days, history, benchmark = fixture()
    history['splits'] = [days[64]]
    with pytest.raises(ValueError, match='完整日 K'):
        simulate({'2330.TW': history}, benchmark, days[61], days[-1])


def test_api_free_mode_persists_job_and_isolates_users(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.database import Base, get_db
    from app.routers.strong_stock import router
    from app.services import strong_stock_backtest as service
    calls = []

    async def run(*args):
        calls.append(args)

    monkeypatch.setattr(service, 'run_backtest', run)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(router)

    def db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = db
    client = TestClient(app)
    headers = {'x-user-id': 'free-test-user'}
    body = {'mode': 'FREE_PRICE_VOLUME', 'start_date': '2025-01-01', 'end_date': '2025-02-01', 'symbols': ['2330.TW']}
    response = client.post('/strong-stocks/backtests', headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()['status'] == 'RUNNING'
    assert len(calls) == 1
    job_id = response.json()['id']
    assert client.get(f'/strong-stocks/backtests/{job_id}', headers=headers).status_code == 200
    assert client.get(f'/strong-stocks/backtests/{job_id}', headers={'x-user-id': 'other-test-user'}).status_code == 404
    assert client.post('/strong-stocks/backtests', headers=headers, json=body).status_code == 409
    body['symbols'] = ['2330/../../bad']
    assert client.post('/strong-stocks/backtests', headers=headers, json=body).status_code == 422


def test_worker_writes_completed_result_without_paper_ledger(monkeypatch):
    import asyncio
    from sqlalchemy import create_engine, select, func
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.strong_stock_models import StrongStockBacktestJob, StrongStockAccount
    from app.services import strong_stock_backtest as service
    days, history, benchmark = fixture()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service, 'SessionLocal', lambda: Session(engine))

    async def fetch(client, semaphore, symbol, start, end):
        return benchmark if symbol == '0050.TW' else history

    monkeypatch.setattr(service, 'fetch_history', fetch)
    with Session(engine) as db:
        db.add(StrongStockBacktestJob(id='worker-test', user_id='test-user', start_date=days[61],
                                     end_date=days[-1], status='RUNNING'))
        db.commit()
    asyncio.run(service.run_backtest('worker-test', ['2330.TW'], days[61], days[-1]))
    with Session(engine) as db:
        job = db.get(StrongStockBacktestJob, 'worker-test')
        assert job.status == 'COMPLETED'
        assert 'FREE_PRICE_VOLUME_V1' in job.result_json
        assert job.completed_at is not None
        assert db.scalar(select(func.count()).select_from(StrongStockAccount)) == 0
