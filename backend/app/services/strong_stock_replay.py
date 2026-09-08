"""Replay the production strong-stock functions in an isolated, disposable database."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import json
import logging
import time as clock
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..adaptive_schemas import AdaptiveScanPayload
from ..database import Base, SessionLocal
from ..strong_stock_models import (
    StrongStockBacktestJob, StrongStockOrder, StrongStockPosition,
    StrongStockScanArchive, StrongStockTrade,
)
from . import strong_stock as strategy

TAIPEI = ZoneInfo('Asia/Taipei')
logger = logging.getLogger(__name__)


def aware(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at


def signal_dates(start: date, end: date, holidays: set[date]) -> list[tuple[date, date]]:
    pairs = []
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in holidays:
            prior = day - timedelta(days=1)
            while prior.weekday() >= 5 or prior in holidays:
                prior -= timedelta(days=1)
            pairs.append((day, prior))
        day += timedelta(days=1)
    return pairs


def select_archives(db: Session, pairs: list[tuple[date, date]]) -> tuple[dict[date, dict], list[str]]:
    selected, missing = {}, []
    if not pairs:
        return selected, missing
    grouped = defaultdict(list)
    rows = db.scalars(select(StrongStockScanArchive).where(
        StrongStockScanArchive.trade_date >= min(prior for _, prior in pairs),
        StrongStockScanArchive.trade_date <= max(prior for _, prior in pairs),
    ).order_by(StrongStockScanArchive.observed_at.desc())).all()
    for row in rows:
        grouped[row.trade_date].append(row)
    for day, signal_day in pairs:
        # Only inputs observed before this session could influence its orders.
        cutoff = datetime.combine(day, time(9), TAIPEI)
        valid = None
        for row in grouped[signal_day]:
            try:
                payload = AdaptiveScanPayload.model_validate_json(row.payload_json)
                observed = aware(row.observed_at)
                if (observed >= cutoff or not payload.stocks or payload.market.market_open or not payload.market.official_data
                        or payload.market.trade_date != signal_day
                        or observed < datetime.combine(signal_day, time(13, 30), TAIPEI)
                        or aware(payload.market.updated_at) > observed
                        or any(aware(stock.quote_timestamp) > observed for stock in payload.stocks)):
                    continue
                valid = {'id': row.id, 'payload': payload, 'observedAt': observed}
                break
            except (ValueError, TypeError):
                continue
        if valid is None:
            missing.append(signal_day.isoformat())
        else:
            selected[day] = valid
    return selected, sorted(set(missing))


class FreeMinuteHistory:
    """Yahoo minute-close sampling, explicitly distinct from live tick execution."""
    def __init__(self, client: httpx.Client, start: date, end: date):
        self.client, self.start, self.end = client, start, end
        self.cache: dict[str, dict] = {}
        self.frozen: dict[str, object] = {}
        self.daily: dict[str, dict[date, Decimal]] = {}
        self.grids: dict[str, dict[date, set]] = {}
        self.deadline = clock.monotonic() + 160

    def __call__(self, symbol: str, market: str, day: date) -> dict[datetime, Decimal]:
        if clock.monotonic() > self.deadline:
            raise ValueError('免費分鐘行情準備逾時，請縮短回測區間後重試。')
        ticker = symbol + ('.TWO' if market == '上櫃' else '.TW')
        if ticker not in self.cache:
            params = {'interval': '1m', 'period1': int(datetime.combine(self.start, time.min, TAIPEI).timestamp()),
                      'period2': int(datetime.combine(self.end + timedelta(days=1), time.min, TAIPEI).timestamp())}
            parsed = None
            for host in ('query1', 'query2'):
                try:
                    response = self.client.get(f'https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}', params=params)
                    response.raise_for_status()
                    result = response.json()['chart']['result'][0]
                    closes = result['indicators']['quote'][0]['close']
                    parsed = defaultdict(dict)
                    grid = defaultdict(set)
                    for stamp, close in zip(result['timestamp'], closes, strict=True):
                        bar_start = datetime.fromtimestamp(stamp, TAIPEI)
                        if time(9) <= bar_start.time() < time(13, 25):
                            grid[bar_start.date()].add(bar_start.time())
                        if close is None:
                            continue
                        price = Decimal(str(close))
                        if not price.is_finite() or price <= 0:
                            continue
                        # A bar's closing price becomes known after its minute ends.
                        # TWSE closing auction prints at 13:30, after the five-minute
                        # no-match interval. It is a terminal price, not a new trading minute.
                        # https://www.twse.com.tw/zh/products/system/trading.html
                        at = bar_start if bar_start.time() == time(13, 30) else bar_start + timedelta(minutes=1)
                        if time(9, 1) <= at.time() <= time(13, 30):
                                parsed[at.date()][at] = price
                    self.grids[ticker] = grid
                    break
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                    continue
            if parsed is None:
                raise ValueError(f'{ticker} 免費分鐘行情取得失敗，未以日 K 或即時現價替代。')
            self.cache[ticker] = parsed
        values = self.cache[ticker].get(day, {})
        # Null prices in a returned minute slot are not fabricated into fills.
        # Verify the provider returned the full regular-session time grid instead
        # of requiring an actively traded price in every minute (invalid for OTC).
        if not values or len(self.grids.get(ticker, {}).get(day, set())) != 265:
            raise ValueError(f'{day} {ticker} 分鐘行情不完整，無法驗證成交及期末估值。')
        self.frozen[f'{ticker}:{day}'] = [[at.isoformat(), str(price)] for at, price in sorted(values.items())]
        return values

    def closing_price(self, symbol: str, market: str, day: date) -> Decimal:
        """Daily closing price is only a terminal valuation, never a synthetic minute fill."""
        if clock.monotonic() > self.deadline:
            raise ValueError('收盤估值行情準備逾時，請稍後重試。')
        ticker = symbol + ('.TWO' if market == '上櫃' else '.TW')
        if ticker not in self.daily:
            params = {'interval': '1d', 'period1': int(datetime.combine(self.start, time.min, TAIPEI).timestamp()),
                      'period2': int(datetime.combine(self.end + timedelta(days=1), time.min, TAIPEI).timestamp())}
            parsed = {}
            for host in ('query1', 'query2'):
                try:
                    response = self.client.get(f'https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}', params=params)
                    response.raise_for_status()
                    data = response.json()['chart']['result'][0]
                    for stamp, raw in zip(data['timestamp'], data['indicators']['quote'][0]['close'], strict=True):
                        if raw is not None:
                            price = Decimal(str(raw))
                            if price.is_finite() and price > 0:
                                parsed[datetime.fromtimestamp(stamp, TAIPEI).date()] = price
                    if day in parsed:
                        break
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                    continue
            self.daily[ticker] = parsed
        price = self.daily[ticker].get(day)
        if price is None:
            raise ValueError(f'{day} {ticker} 缺少收盤估值，不能以舊價格替代。')
        self.frozen[f'{ticker}:{day}:closingValuation'] = str(price)
        return price


def replay(selected: dict[date, dict], config: dict, quote_loader) -> dict:
    """All decisions and cash mutations run through the very same live strategy functions."""
    if not selected:
        raise ValueError('區間沒有可重播的交易日')
    engine = create_engine('sqlite://')
    uid = 'isolated-strong-stock-replay'
    curve, counts = [], {'queued': 0, 'filled': 0, 'added': 0, 'reduced': 0, 'closed': 0}
    config = strategy.merged_config(config)
    deadline = clock.monotonic() + 180
    initial = strategy.dec(config['initialCapital'])
    if initial <= 0:
        raise ValueError('初始資金必須大於零')
    try:
        # Only strategy tables exist here. No live account or notification dispatcher is reachable.
        tables = [table for table in Base.metadata.sorted_tables if table.name.startswith('strong_stock_')]
        Base.metadata.create_all(engine, tables=tables)
        with Session(engine, expire_on_commit=False) as db:
            setting, account, _ = strategy.ensure_defaults(db, uid)
            setting.config_json = json.dumps(config)
            account.initial_capital = account.cash = initial
            db.commit()
            peak = initial
            max_drawdown = Decimal(0)
            for day, snapshot in sorted(selected.items()):
                payload = snapshot['payload']
                # Rescore the historical inputs under the frozen CURRENT parameters.
                strategy.scan_and_persist(db, payload, snapshot['observedAt'], config)
                counts['queued'] += strategy.queue_paper_orders(db, uid, payload.market.trade_date)
                positions = list(db.scalars(select(StrongStockPosition).where(StrongStockPosition.status == 'OPEN')))
                orders = list(db.scalars(select(StrongStockOrder).where(
                    StrongStockOrder.status == 'PENDING', StrongStockOrder.valid_date == day)))
                symbols = {row.symbol for row in [*positions, *orders]}
                markets = {stock.stock_code: stock.market_type for stock in payload.stocks}
                events = defaultdict(dict)
                for symbol in sorted(symbols):
                    if symbol not in markets:
                        raise ValueError(f'{day} 缺少持股 {symbol} 的選股輸入，不能以未來名單補值。')
                    for at, price in quote_loader(symbol, markets[symbol], day).items():
                        if at.tzinfo is None or at.astimezone(TAIPEI).date() != day:
                            raise ValueError('分鐘行情日期或時區錯誤')
                        events[at][symbol] = price
                # Expiration happens even when there is no actionable symbol this session.
                strategy.fill_pending_orders(db, uid, {}, datetime.combine(day, time(9), TAIPEI))
                for at, prices in sorted(events.items()):
                    if clock.monotonic() > deadline:
                        raise ValueError('回測超過時間上限，請縮短區間後重試。')
                    fills = strategy.fill_pending_orders(db, uid, prices, at)
                    actions = strategy.monitor_positions(db, uid, prices, at)
                    counts['filled'] += fills['filled']
                    for key in ('added', 'reduced', 'closed'):
                        counts[key] += actions[key]
                    equity = strategy.dec(strategy.performance(db, uid)['totalEquity'])
                    peak = max(peak, equity)
                    max_drawdown = min(max_drawdown, (equity / peak - 1) * 100)
                if hasattr(quote_loader, 'closing_price'):
                    for position in db.scalars(select(StrongStockPosition).where(StrongStockPosition.status == 'OPEN')):
                        position.current_price = quote_loader.closing_price(position.symbol, markets[position.symbol], day)
                    db.commit()
                closing_equity = strategy.dec(strategy.performance(db, uid)['totalEquity'])
                peak = max(peak, closing_equity)
                max_drawdown = min(max_drawdown, (closing_equity / peak - 1) * 100)
                strategy.snapshot_equity(db, uid, day)
                strategy.strategy_health(db, uid, apply_guard=True)
                p = strategy.performance(db, uid)
                curve.append({'date': day.isoformat(), 'equity': p['totalEquity']})
            p = strategy.performance(db, uid)
            trades = list(db.scalars(select(StrongStockTrade).order_by(StrongStockTrade.exit_at)))
            positions = list(db.scalars(select(StrongStockPosition).where(StrongStockPosition.status == 'OPEN')))
            return {
                'mode': 'FULL', 'strategy': strategy.STRATEGY_ID, 'strategyVersion': strategy.STRATEGY_VERSION,
                'executionModel': 'MINUTE_CLOSE_REPLAY', 'parameters': config,
                'actualStartDate': min(selected).isoformat(), 'actualEndDate': max(selected).isoformat(),
                'initialCapital': str(initial), 'totalPnl': str(strategy.dec(p['totalEquity']) - initial),
                'totalReturnPct': p['totalReturnPct'], 'tradeCount': p['tradeCount'], 'winRate': p['winRate'],
                'maxDrawdownPct': str(strategy.money(max_drawdown)), 'openPositionCount': len(positions),
                'equityCurve': curve, 'actions': counts,
                'trades': [{'symbol': t.symbol, 'name': t.name, 'entryAt': t.entry_at.isoformat(),
                            'exitAt': t.exit_at.isoformat(), 'quantity': t.quantity, 'netPnl': str(t.net_pnl),
                            'entryPrice': str(t.entry_price), 'exitPrice': str(t.exit_price),
                            'exitReason': t.exit_reason} for t in trades],
                'notice': '使用目前正式策略與參數，從空倉開始；免費分鐘取樣可能漏掉盤中碰價。缺少收盤撮合分鐘價時，日收盤價只用於估值，不模擬收盤撮合成交。期末不強制平倉；股息處理沿用正式策略。',
            }
    finally:
        engine.dispose()


def run_job(job_id: str) -> None:
    from .strong_stock_history import configured_holidays
    frozen = {}
    try:
        with SessionLocal() as db:
            job = db.get(StrongStockBacktestJob, job_id)
            if job is None:
                return
            request = json.loads(job.request_json)
            start, end = job.start_date, job.end_date
            pairs = signal_dates(start, end, configured_holidays())
            selected, missing = select_archives(db, pairs)
            if missing:
                raise ValueError('缺少開盤前已保存的完整選股輸入：' + '、'.join(missing))
            frozen['scanArchiveIds'] = [s['id'] for s in selected.values()]
        with httpx.Client(timeout=8, headers={'User-Agent': 'Mozilla/5.0'}) as client:
            loader = FreeMinuteHistory(client, start, end)
            result = replay(selected, request['parameters'], loader)
            frozen['minutePrices'] = loader.frozen
        status = 'COMPLETED'
    except ValueError as exc:
        result, status = {'mode': 'FULL', 'message': str(exc)}, 'DATA_INSUFFICIENT'
    except Exception:
        logger.exception('Official strong-stock replay failed: %s', job_id)
        result, status = {'mode': 'FULL', 'message': '正式策略回測失敗，請查看服務紀錄。'}, 'FAILED'
    with SessionLocal() as db:
        job = db.get(StrongStockBacktestJob, job_id)
        if job and job.status == 'RUNNING':
            job.status, job.result_json = status, json.dumps(result, ensure_ascii=False)
            job.data_status_json = json.dumps(frozen, ensure_ascii=False)
            job.error_message, job.completed_at = result.get('message', ''), datetime.now(UTC)
            db.commit()
