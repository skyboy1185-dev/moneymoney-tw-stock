"""Report actual saved input coverage, without presenting it as an executable backtest."""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..strong_stock_models import StrongStockRanking, StrongStockScanArchive


def history_coverage(db: Session, start: date, end: date) -> dict:
    rows = db.execute(select(StrongStockRanking.trade_date, func.count())
                      .group_by(StrongStockRanking.trade_date).order_by(StrongStockRanking.trade_date)).all()
    saved = {day: count for day, count in rows}
    archive_days = set(db.scalars(select(StrongStockScanArchive.trade_date).distinct()).all())
    holidays = set()
    for value in get_settings().twse_holidays.split(','):
        try:
            holidays.add(date.fromisoformat(value.strip()))
        except ValueError:
            pass
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    expected = [d for d in days if d.weekday() < 5 and d not in holidays]
    missing = [d.isoformat() for d in expected if d not in saved]
    raw_missing = [d.isoformat() for d in expected if d not in archive_days]
    reasons = []
    if missing:
        reasons.append('缺少選股快照日期：' + '、'.join(missing))
    if raw_missing:
        reasons.append('缺少不可覆寫的完整選股輸入與策略參數：' + '、'.join(raw_missing))
    reasons.append('尚未建立可驗證的盤中行情、初始帳戶狀態與共用交易規則重播流程，不能提供正式策略歷史損益。')
    return {
        'mode': 'FULL', 'ready': False, 'startDate': start.isoformat(), 'endDate': end.isoformat(),
        'firstSnapshotDate': min(saved).isoformat() if saved else None,
        'lastSnapshotDate': max(saved).isoformat() if saved else None,
        'snapshotDates': [{'date': d.isoformat(), 'stockCount': count} for d, count in saved.items()],
        'missingSnapshotDates': missing, 'missingArchivedInputDates': raw_missing,
        'archivedInputDates': sorted(d.isoformat() for d in archive_days),
        'calendarNote': '日期檢查以週一至週五扣除已設定休市日；快照存在不代表完整或已通過時點驗證。',
        'missing': reasons,
        'message': '正式策略回測尚未具備完整資料與重播流程；績效未計算，不能解讀為零交易或零損益。',
    }
