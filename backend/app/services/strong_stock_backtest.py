"""Independent, daily price/volume research strategy. Never touches paper accounts."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
import json
import math
from statistics import mean

import httpx

from ..database import SessionLocal
from ..strong_stock_models import StrongStockBacktestJob

DEFAULT_SYMBOLS = ['2330.TW', '2303.TW', '2454.TW', '2308.TW', '2317.TW', '2382.TW',
                   '3231.TW', '3037.TW', '3034.TW', '2379.TW', '2408.TW', '2344.TW',
                   '3008.TW', '3443.TW', '3532.TW', '3661.TW', '3711.TW', '3693.TWO',
                   '3105.TWO', '6488.TWO']
LIMITATIONS = [
    '簡化價量策略，不含營收、財報、產業及籌碼條件，不能代表完整強勢股策略。',
    '使用指定的固定股票池，未還原歷史上市及下市母體，存在選樣與存活者偏誤。',
    '日 K 模擬無法還原盤中順序、排隊與漲跌停成交；平盤且無振幅的日 K 不成交。',
    '使用未還原價格，現金股息計入權益；區間內有分割或減資等價格重設事件的股票不納入。',
]


def parse_history(payload: dict) -> dict:
    results = payload.get('chart', {}).get('result') or []
    if not results:
        raise ValueError('找不到日 K 資料')
    result = results[0]
    quotes = result['indicators']['quote'][0]
    bars = {}
    for i, stamp in enumerate(result.get('timestamp', [])):
        values = [quotes.get(key, [])[i] for key in ('open', 'high', 'low', 'close', 'volume')]
        if any(v is None or not math.isfinite(float(v)) for v in values):
            continue
        o, h, l, c, v = map(float, values)
        if min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
            continue
        bars[datetime.fromtimestamp(stamp, UTC).date()] = (o, h, l, c, v)
    events = result.get('events', {})
    dividends = {datetime.fromtimestamp(e['date'], UTC).date(): float(e['amount'])
                 for e in events.get('dividends', {}).values()}
    splits = [datetime.fromtimestamp(e['date'], UTC).date() for e in events.get('splits', {}).values()]
    return {'bars': bars, 'dividends': dividends, 'splits': splits}


async def fetch_history(client: httpx.AsyncClient, semaphore: asyncio.Semaphore,
                        symbol: str, start: date, end: date) -> dict:
    params = {'interval': '1d', 'period1': int(datetime.combine(start, datetime.min.time(), UTC).timestamp()),
              'period2': int(datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC).timestamp()),
              'events': 'div,splits'}
    async with semaphore:
        for host in ('query1', 'query2'):
            try:
                response = await client.get(f'https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}', params=params)
                response.raise_for_status()
                return parse_history(response.json())
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                continue
    raise ValueError(f'{symbol} 免費歷史行情暫時無法取得')


def simulate(histories: dict, benchmark: dict, start: date, end: date) -> dict:
    days = sorted(d for d in benchmark['bars'] if start <= d <= end)
    if len(days) < 2:
        raise ValueError('選定區間至少需要兩個已完成交易日；當日日 K 尚未完成時不納入。')
    usable, excluded = {}, []
    for symbol, history in histories.items():
        bars = history['bars']
        if any(d <= end for d in history['splits']):
            excluded.append(f'{symbol}：暖機或回測區間有價格重設事件')
        elif len([d for d in bars if d < days[0]]) < 61 or any(d not in bars for d in days):
            excluded.append(f'{symbol}：缺少 61 日暖機或回測日 K')
        else:
            usable[symbol] = history
    if not usable:
        raise ValueError('股票池沒有具備完整日 K 與 61 日暖機的股票，請縮短區間或改選股票。')
    cash = initial = 3_000_000.0
    positions, pending_buys, pending_sells = {}, [], {}
    trades, curve = [], []
    peak, max_drawdown = initial, 0.0
    fee = lambda value: round(max(20, value * 0.001425 * 0.2), 2)
    ordered = {s: sorted(h['bars']) for s, h in usable.items()}
    indices = {s: {d: i for i, d in enumerate(ds)} for s, ds in ordered.items()}

    def close(symbol, raw_price, day, reason):
        nonlocal cash
        pos = positions.pop(symbol)
        price = raw_price * (1 - 0.0005)
        proceeds = price * pos['quantity']
        sell_fee, tax = fee(proceeds), round(proceeds * 0.003, 2)
        cash += proceeds - sell_fee - tax
        pnl = proceeds - sell_fee - tax - pos['cost'] + pos['dividends']
        trades.append({'symbol': symbol, 'entryDate': pos['day'].isoformat(), 'exitDate': day.isoformat(),
                       'quantity': pos['quantity'], 'entryPrice': round(pos['price'], 4), 'exitPrice': round(price, 4),
                       'buyFee': pos['buyFee'], 'sellFee': sell_fee, 'tax': tax,
                       'dividends': round(pos['dividends'], 2), 'netPnl': round(pnl, 2),
                       'returnPct': round(pnl / pos['cost'] * 100, 4), 'exitReason': reason})

    for session, day in enumerate(days):
        for symbol, pos in list(positions.items()):
            dividend = usable[symbol]['dividends'].get(day, 0) * pos['quantity']
            cash += dividend
            pos['dividends'] += dividend
            if dividend:
                pos['stop'] -= dividend / pos['quantity']
            o, h, l, c, v = usable[symbol]['bars'][day]
            if symbol in pending_sells and v > 0 and h > l:
                close(symbol, o, day, pending_sells[symbol])
        pending_sells = {s: reason for s, reason in pending_sells.items() if s in positions}
        for symbol, stop, prior_volume in pending_buys:
            if symbol in positions or len(positions) >= 8:
                continue
            o, h, l, c, v = usable[symbol]['bars'][day]
            if v <= 0 or h <= l or o <= stop:
                continue
            price = o * 1.0005
            qty = int(min(300_000 / price, 15_000 / (price - stop), prior_volume * 0.01))
            cost = qty * price + fee(qty * price)
            if qty <= 0 or cost > cash:
                continue
            cash -= cost
            positions[symbol] = {'day': day, 'session': session, 'quantity': qty, 'price': price,
                                 'cost': cost, 'buyFee': fee(qty * price), 'stop': stop, 'dividends': 0.0}
        pending_buys = []
        for symbol, pos in list(positions.items()):
            o, h, l, c, v = usable[symbol]['bars'][day]
            if v > 0 and h > l and l <= pos['stop']:
                close(symbol, min(o, pos['stop']), day, '停損（日 K 保守成交）')
            elif session == len(days) - 1 and v > 0 and h > l:
                close(symbol, c, day, '回測期末平倉')
        candidates, scores = [], []
        for symbol, history in usable.items():
            ds = ordered[symbol]
            index = indices[symbol][day]
            past = [history['bars'][d] for d in ds[index - 60:index + 1]]
            c = past[-1][3]
            ma20, ma60 = mean(b[3] for b in past[-20:]), mean(b[3] for b in past[-60:])
            score = c / past[0][3] - 1
            scores.append((symbol, score))
            if symbol in positions:
                pos = positions[symbol]
                age = session - pos['session'] + 1
                if age >= 60 or (age >= 5 and c < ma20):
                    pending_sells[symbol] = '持有滿 60 日' if age >= 60 else '跌破 20 日均線'
            volume = mean(b[4] for b in past[-21:-1])
            turnover = mean(b[3] * b[4] for b in past[-21:-1])
            if (c >= 10 and c > ma20 > ma60 and c > max(b[1] for b in past[-21:-1])
                    and past[-1][4] >= volume * 1.5 and turnover >= 100_000_000
                    and c / past[-2][3] <= 1.06 and c / ma20 <= 1.12):
                atr = mean(max(past[i][1] - past[i][2], abs(past[i][1] - past[i-1][3]),
                               abs(past[i][2] - past[i-1][3])) for i in range(len(past)-14, len(past)))
                candidates.append((symbol, max(0.01, c - 2 * atr), volume))
        top = {s for s, _ in sorted(scores, key=lambda row: (-row[1], row[0]))[:max(1, math.ceil(len(scores) * .2))]}
        ranks = {s: i for i, (s, _) in enumerate(sorted(scores, key=lambda row: (-row[1], row[0])))}
        if session < len(days) - 1:
            pending_buys = sorted((row for row in candidates if row[0] in top), key=lambda row: ranks[row[0]])
        equity = cash + sum(p['quantity'] * usable[s]['bars'][day][3] for s, p in positions.items())
        peak = max(peak, equity)
        dd = (equity / peak - 1) * 100
        max_drawdown = min(max_drawdown, dd)
        curve.append({'date': day.isoformat(), 'equity': round(equity, 2), 'drawdownPct': round(dd, 4)})
    benchmark_start = benchmark['bars'][days[0]][0]
    benchmark_return = ((benchmark['bars'][days[-1]][3] + sum(benchmark['dividends'].get(d, 0) for d in days[1:])) / benchmark_start - 1) * 100
    # Splits affect ETF comparison too; omit it instead of reporting a false loss.
    if any(days[0] <= d <= days[-1] for d in benchmark['splits']):
        benchmark_return = None
    return {'strategy': 'FREE_PRICE_VOLUME_V1', 'label': '免費簡化價量回測', 'limitations': LIMITATIONS,
            'source': 'Yahoo Finance daily OHLCV and cash dividends',
            'actualStartDate': days[0].isoformat(), 'actualEndDate': days[-1].isoformat(),
            'symbols': sorted(usable), 'excluded': excluded, 'initialCapital': initial,
            'totalPnl': round(equity - initial, 2), 'totalReturnPct': round((equity / initial - 1) * 100, 4),
            'maxDrawdownPct': round(max_drawdown, 4), 'tradeCount': len(trades),
            'winRate': round(sum(t['netPnl'] > 0 for t in trades) / len(trades) * 100, 2) if trades else None,
            'benchmarkReturnPct': round(benchmark_return, 4) if benchmark_return is not None else None,
            'openPositionCount': len(positions), 'trades': trades, 'equityCurve': curve,
            'costs': {'commissionRate': .000285, 'minimumCommission': 20, 'taxRate': .003, 'slippageBps': 5},
            'rules': '60日報酬前20%、收盤突破前20日高點、量比≥1.5、收盤>MA20>MA60；隔日開盤進場、2ATR停損、滿5日跌破MA20出場、最多60日。單筆30萬、風險1.5萬、最多8檔、買進不超過前20日均量1%。'}


async def run_backtest(job_id: str, symbols: list[str], start: date, end: date) -> None:
    try:
        semaphore = asyncio.Semaphore(4)
        async with httpx.AsyncClient(timeout=12, headers={'User-Agent': 'Mozilla/5.0'}) as client:
            async with asyncio.timeout(150):
                fetched = await asyncio.gather(*(fetch_history(client, semaphore, s, start - timedelta(days=400), end)
                                                 for s in [*symbols, '0050.TW']), return_exceptions=True)
        failed = [s for s, data in zip([*symbols, '0050.TW'], fetched) if isinstance(data, BaseException)]
        if failed:
            raise ValueError('免費行情讀取失敗，請稍後重試：' + '、'.join(failed))
        result = await asyncio.to_thread(simulate, dict(zip(symbols, fetched[:-1])), fetched[-1], start, end)
        status = 'COMPLETED'
    except (ValueError, TimeoutError) as exc:
        status, result = 'DATA_INSUFFICIENT', {'message': str(exc) or '免費行情下載逾時，請稍後重試。'}
    except Exception:
        status, result = 'FAILED', {'message': '回測計算失敗，請重新執行。'}
    with SessionLocal() as db:
        job = db.get(StrongStockBacktestJob, job_id)
        if job:
            job.status = status
            job.result_json = json.dumps(result, ensure_ascii=False)
            job.error_message = result.get('message', '')
            job.completed_at = datetime.now(UTC)
            db.commit()
