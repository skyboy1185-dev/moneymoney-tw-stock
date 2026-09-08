"""Validate causal input availability before starting the shared-rule replay."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..strong_stock_models import StrongStockRanking, StrongStockScanArchive


def configured_holidays() -> set[date]:
    holidays = set()
    for value in get_settings().twse_holidays.split(','):
        try:
            holidays.add(date.fromisoformat(value.strip()))
        except ValueError:
            pass
    return holidays


def history_coverage(db: Session, start: date, end: date) -> dict:
    from .strong_stock_replay import select_archives, signal_dates
    rows = db.execute(select(StrongStockRanking.trade_date, func.count())
                      .group_by(StrongStockRanking.trade_date).order_by(StrongStockRanking.trade_date)).all()
    saved = {day: count for day, count in rows}
    archive_days = set(db.scalars(select(StrongStockScanArchive.trade_date).distinct()).all())
    holidays = configured_holidays()
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    expected = [d for d in days if d.weekday() < 5 and d not in holidays]
    missing = [d.isoformat() for d in expected if d not in saved]
    pairs = signal_dates(start, end, holidays)
    _, raw_missing = select_archives(db, pairs)
    reasons = []
    if missing and raw_missing:
        reasons.append('缺少選股快照日期：' + '、'.join(missing))
    if raw_missing:
        reasons.append('缺少進場前一交易日的完整盤後選股輸入：' + '、'.join(raw_missing))
    today = datetime.now(ZoneInfo('Asia/Taipei')).date()
    minute_range_ok = today - timedelta(days=7) <= start <= end < today
    if not minute_range_ok:
        reasons.append('免費分鐘重播目前限最近七個日曆日，且不包含今日。')
    if not pairs:
        reasons.append('所選區間沒有交易日。')
    ready = bool(pairs) and not raw_missing and minute_range_ok
    return {
        'mode': 'FULL', 'ready': ready, 'startDate': start.isoformat(), 'endDate': end.isoformat(),
        'firstSnapshotDate': min(saved).isoformat() if saved else None,
        'lastSnapshotDate': max(saved).isoformat() if saved else None,
        'snapshotDates': [{'date': d.isoformat(), 'stockCount': count} for d, count in saved.items()],
        'missingSnapshotDates': missing, 'missingArchivedInputDates': raw_missing,
        'archivedInputDates': sorted(d.isoformat() for d in archive_days),
        'calendarNote': '日期檢查以週一至週五扣除已設定休市日；快照存在不代表完整或已通過時點驗證。',
        'missing': reasons,
        'message': '選股輸入已備齊，可執行正式策略分鐘重播；行情完整性會在下載後檢查。' if ready else '缺少正式策略重播資料，尚未計算績效。',
    }
