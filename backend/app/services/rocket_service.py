from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adaptive_schemas import AdaptiveScanPayload
from ..models import (
    RocketAccount, RocketCandidate, RocketDailyPortfolio, RocketMarketRegime,
    RocketNotification, RocketPosition, RocketSectorStrength, RocketTrade,
)
from .rocket_scoring import (
    RocketPick, classify_rocket_market, rank_rocket_candidates, score_rocket_stock,
)
from .rocket_trading import (
    INITIAL_CAPITAL, RocketEvent, ensure_rocket_account, manage_open_positions,
    open_new_positions, record_daily_portfolio, record_rocket_event,
)


STATUS_LABELS = {
    "watch": "⚪ 觀察", "waiting": "🟡 等待訊號", "can_enter": "🟢 可進場",
    "strong_breakout": "🔥 強勢突破", "pullback": "🔵 回踩等待",
    "can_add": "🟣 可加碼", "reduce": "🟠 減碼", "exit": "🔴 出場",
    "overheated": "🔴 過熱，不追",
}


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _upsert_regime(db: Session, payload: AdaptiveScanPayload, regime) -> RocketMarketRegime:
    item = db.scalar(select(RocketMarketRegime).where(RocketMarketRegime.trade_date == payload.market.trade_date))
    values = {
        "regime": regime.key, "regime_label": regime.label, "score": _decimal(regime.score),
        "maximum_exposure_pct": _decimal(regime.exposure_pct), "strategy_label": regime.strategy_label,
        "reasons_json": _json(regime.reasons), "indicators_json": _json(regime.indicators),
        "missing_fields_json": _json(payload.market.missing_fields), "evaluated_at": payload.market.updated_at,
    }
    if item is None:
        item = RocketMarketRegime(trade_date=payload.market.trade_date, **values)
        db.add(item)
    else:
        for key, value in values.items(): setattr(item, key, value)
    return item


def _upsert_sector(db: Session, payload: AdaptiveScanPayload, result, source_map) -> RocketSectorStrength:
    item = db.scalar(select(RocketSectorStrength).where(
        RocketSectorStrength.trade_date == payload.market.trade_date,
        RocketSectorStrength.sector_name == result.sub_industry,
    ))
    source = source_map[result.sub_industry]
    values = {
        "strength_rank": result.rank, "strength_score": _decimal(result.score),
        "return_1d": _decimal(source.return_1d) if source.return_1d is not None else None,
        "return_3d": _decimal(source.return_3d) if source.return_3d is not None else None,
        "return_5d": _decimal(source.return_5d) if source.return_5d is not None else None,
        "return_20d": _decimal(source.return_20d) if source.return_20d is not None else None,
        "advance_ratio": _decimal(source.advance_ratio) if source.advance_ratio is not None else None,
        "new_high_ratio": _decimal(source.new_high_ratio) if source.new_high_ratio is not None else None,
        "volume_growth": _decimal(source.volume_growth) if source.volume_growth is not None else None,
        "breakdown_json": _json(result.breakdown), "updated_at": payload.market.updated_at,
    }
    if item is None:
        item = RocketSectorStrength(
            trade_date=payload.market.trade_date, sector_name=result.sub_industry, **values,
        )
        db.add(item)
    else:
        for key, value in values.items(): setattr(item, key, value)
    return item


def _candidate_values(pick: RocketPick, regime_key: str, rank: int, is_top5: bool, at: datetime) -> dict[str, object]:
    components = pick.components
    return {
        "stock_name": pick.stock.stock_name, "market_type": pick.stock.market_type,
        "sector_name": pick.stock.sub_industry, "sector_rank": pick.sector_rank,
        "rank": rank, "is_top5": is_top5, "candidate_status": pick.status,
        "pattern_type": pick.pattern_type, "market_regime": regime_key,
        "current_price": _decimal(pick.stock.price), "change_pct": _decimal(pick.stock.return_1d),
        "rocket_score": _decimal(pick.rocket_score), "chase_risk_score": _decimal(pick.chase_risk_score),
        "sector_score": _decimal(components["族群強度"]) if components["族群強度"] is not None else None,
        "momentum_score": _decimal(components["價格動能"]) if components["價格動能"] is not None else None,
        "volume_score": _decimal(components["成交量"]) if components["成交量"] is not None else None,
        "pattern_score": _decimal(components["突破型態"]) if components["突破型態"] is not None else None,
        "chip_score": _decimal(components["籌碼強度"]) if components["籌碼強度"] is not None else None,
        "institutional_score": _decimal(components["法人"]) if components["法人"] is not None else None,
        "quality_score": _decimal(components["風險品質"]) if components["風險品質"] is not None else None,
        "data_availability_pct": _decimal(pick.data_availability_pct),
        "volume_ratio": _decimal(pick.stock.volume_ratio_20d or 0),
        "breakout_price": _decimal(pick.breakout_price), "stop_loss_price": _decimal(pick.stop_loss_price),
        "target_price_1": _decimal(pick.target_price_1), "target_price_2": _decimal(pick.target_price_2),
        "risk_reward_ratio": _decimal(pick.risk_reward_ratio),
        "atr": _decimal(pick.stock.atr14) if pick.stock.atr14 is not None else None,
        "ma5": _decimal(pick.stock.ma5) if pick.stock.ma5 is not None else None,
        "ma10": _decimal(pick.stock.ma10) if pick.stock.ma10 is not None else None,
        "ma20": _decimal(pick.stock.ma20) if pick.stock.ma20 is not None else None,
        "reasons_json": _json(pick.reasons), "missing_data_json": _json(pick.missing_data),
        "score_breakdown_json": _json(pick.components), "updated_at": at,
    }


def _upsert_candidate(
    db: Session, trade_date: date, pick: RocketPick, regime_key: str,
    rank: int, is_top5: bool, at: datetime,
) -> tuple[RocketCandidate, str | None]:
    item = db.scalar(select(RocketCandidate).where(
        RocketCandidate.trade_date == trade_date,
        RocketCandidate.stock_code == pick.stock.stock_code,
    ))
    previous_status = item.candidate_status if item else None
    values = _candidate_values(pick, regime_key, rank, is_top5, at)
    if item is None:
        item = RocketCandidate(trade_date=trade_date, stock_code=pick.stock.stock_code, **values)
        db.add(item); db.flush()
    else:
        for key, value in values.items(): setattr(item, key, value)
    return item, previous_status


def _status_event(db: Session, item: RocketCandidate, previous: str | None, at: datetime) -> None:
    if previous == item.candidate_status:
        return
    event_type = (
        "WARNING" if item.candidate_status == "overheated"
        else "BREAKOUT" if item.candidate_status in {"can_enter", "strong_breakout"}
        else "WATCH"
    )
    record_rocket_event(db, RocketEvent(
        key=f"candidate:{item.trade_date}:{item.stock_code}:{previous or 'NEW'}>{item.candidate_status}",
        event_type=event_type, timestamp=at, stock_code=item.stock_code, stock_name=item.stock_name,
        title=f"飆股雷達｜{STATUS_LABELS[item.candidate_status]}",
        message=f"{item.stock_code} {item.stock_name}｜Rocket {float(item.rocket_score):.1f}｜CHASE {float(item.chase_risk_score):.1f}",
        reason="；".join(json.loads(item.reasons_json)[:3]), price=float(item.current_price),
        rocket_score=float(item.rocket_score), chase_risk=float(item.chase_risk_score),
        strategy_type=item.pattern_type, previous_status=previous, new_status=item.candidate_status,
    ))


def process_rocket_scan(db: Session, payload: AdaptiveScanPayload) -> dict[str, object]:
    at = payload.market.updated_at
    account = ensure_rocket_account(db, at)
    previous_regime = db.scalar(select(RocketMarketRegime).order_by(RocketMarketRegime.trade_date.desc()).limit(1))
    regime, sectors, all_picks = rank_rocket_candidates(payload)
    _upsert_regime(db, payload, regime)
    if previous_regime is None or previous_regime.regime != regime.key:
        record_rocket_event(db, RocketEvent(
            key=f"market:{payload.market.trade_date}:{regime.key}", event_type="MARKET", timestamp=at,
            stock_code=None, stock_name=None, title=f"市場狀態｜{regime.label}",
            message=f"今日策略：{regime.strategy_label}；最大曝險 {regime.exposure_pct:.0f}%",
            reason="；".join(regime.reasons[:4]), strategy_type=regime.strategy_label,
            previous_status=previous_regime.regime if previous_regime else None, new_status=regime.key,
        ))
    source_map = {item.sub_industry: item for item in payload.industries}
    for result in sectors:
        _upsert_sector(db, payload, result, source_map)
    if sectors:
        record_rocket_event(db, RocketEvent(
            key=f"sector:{payload.market.trade_date}:{sectors[0].sub_industry}", event_type="SECTOR", timestamp=at,
            stock_code=None, stock_name=None, title="強勢族群更新",
            message=f"今日第一強族群：{sectors[0].sub_industry}（{sectors[0].score:.1f} 分）",
            reason="族群動能、量能、寬度與可用籌碼資料綜合評分", new_status="TOP_SECTOR",
        ))

    prior_items = list(db.scalars(select(RocketCandidate).where(
        RocketCandidate.trade_date == payload.market.trade_date,
    )).all())
    for old in prior_items:
        old.rank = 999; old.is_top5 = False
    observation_picks = [pick for pick in all_picks if pick.rocket_score >= 75]
    top20 = observation_picks[:20]
    tradeable_picks = [
        pick for pick in top20
        if pick.status in {"can_enter", "strong_breakout"}
        and pick.rocket_score >= 85 and pick.chase_risk_score < 60 and pick.risk_reward_ratio >= 1.8
    ][:5]
    top5_symbols = {pick.stock.stock_code for pick in tradeable_picks}
    candidate_rows: list[RocketCandidate] = []
    for rank, pick in enumerate(top20, 1):
        item, previous = _upsert_candidate(
            db, payload.market.trade_date, pick, regime.key, rank,
            pick.stock.stock_code in top5_symbols, at,
        )
        candidate_rows.append(item)
        _status_event(db, item, previous, at)

    held_positions = list(db.scalars(select(RocketPosition).where(RocketPosition.status == "open")).all())
    held_symbols = {item.stock_code for item in held_positions}
    current_by_code = {pick.stock.stock_code: pick for pick in all_picks}
    stock_by_code = {item.stock_code: item for item in payload.stocks}
    sector_by_name = {item.sub_industry: item for item in sectors}
    for symbol in held_symbols:
        if symbol in current_by_code:
            continue
        stock = stock_by_code.get(symbol)
        if stock is None:
            continue
        pick = score_rocket_stock(stock, regime, sector_by_name.get(stock.sub_industry), enforce_initial_filter=False)
        if pick is None:
            continue
        item, _ = _upsert_candidate(db, payload.market.trade_date, pick, regime.key, 999, False, at)
        candidate_rows.append(item)

    db.flush()
    candidate_map = {item.stock_code: item for item in candidate_rows}
    managed = manage_open_positions(db, account, candidate_map, regime.key, at)
    db.flush()
    top5_rows = [item for item in candidate_rows if item.is_top5]
    opened = open_new_positions(db, account, top5_rows, regime.exposure_pct, at)
    record_daily_portfolio(db, account, payload.market.trade_date, at)
    db.commit()
    return {
        "status": "completed", "tradeDate": payload.market.trade_date.isoformat(),
        "regime": regime.key, "candidateCount": len(top20), "tradeableCount": len(top5_rows),
        "opened": opened, "positionEvents": managed, "lineNotifications": 0,
        "updatedAt": at.isoformat(),
    }


def candidate_payload(item: RocketCandidate) -> dict[str, object]:
    return {
        "id": item.id, "rank": item.rank, "isTop5": item.is_top5,
        "stockCode": item.stock_code, "stockName": item.stock_name, "marketType": item.market_type,
        "sectorName": item.sector_name, "sectorRank": item.sector_rank,
        "status": item.candidate_status, "statusLabel": STATUS_LABELS.get(item.candidate_status, item.candidate_status),
        "patternType": item.pattern_type, "marketRegime": item.market_regime,
        "currentPrice": float(item.current_price), "changePercent": float(item.change_pct),
        "rocketScore": float(item.rocket_score), "chaseRiskScore": float(item.chase_risk_score),
        "volumeRatio": float(item.volume_ratio), "breakoutPrice": float(item.breakout_price),
        "stopLossPrice": float(item.stop_loss_price), "targetPrice1": float(item.target_price_1),
        "targetPrice2": float(item.target_price_2), "riskRewardRatio": float(item.risk_reward_ratio),
        "atr": float(item.atr) if item.atr is not None else None,
        "ma5": float(item.ma5) if item.ma5 is not None else None,
        "ma10": float(item.ma10) if item.ma10 is not None else None,
        "ma20": float(item.ma20) if item.ma20 is not None else None,
        "scoreBreakdown": json.loads(item.score_breakdown_json),
        "dataAvailabilityPercent": float(item.data_availability_pct),
        "reasons": json.loads(item.reasons_json), "missingData": json.loads(item.missing_data_json),
        "updatedAt": item.updated_at.isoformat(),
    }


def notification_payload(item: RocketNotification) -> dict[str, object]:
    return {
        "notificationId": item.id, "timestamp": item.created_at.isoformat(),
        "stockCode": item.stock_code, "stockName": item.stock_name,
        "notificationType": item.notification_type, "priority": item.priority,
        "title": item.title, "message": item.message,
        "price": float(item.price) if item.price is not None else None,
        "rocketScore": float(item.rocket_score) if item.rocket_score is not None else None,
        "chaseRisk": float(item.chase_risk) if item.chase_risk is not None else None,
        "positionSize": item.quantity, "amount": float(item.amount) if item.amount is not None else None,
        "pnl": float(item.pnl) if item.pnl is not None else None,
        "pnlPercent": float(item.pnl_percent) if item.pnl_percent is not None else None,
        "reason": item.reason, "strategyType": item.strategy_type, "isRead": item.is_read,
    }


def _position_return(item: RocketPosition) -> float:
    invested = float(item.invested_cost)
    return float(item.realized_pnl) / invested * 100 if invested else 0


def _maximum_streak(values: list[bool], winning: bool) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if value is winning else 0
        best = max(best, current)
    return best


def performance_payload(db: Session) -> dict[str, object]:
    closed = list(db.scalars(select(RocketPosition).where(
        RocketPosition.status == "closed",
    ).order_by(RocketPosition.exit_time)).all())
    returns = [_position_return(item) for item in closed]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    gross_profit = sum(float(item.realized_pnl) for item in closed if float(item.realized_pnl) > 0)
    gross_loss = abs(sum(float(item.realized_pnl) for item in closed if float(item.realized_pnl) < 0))
    curve = list(db.scalars(select(RocketDailyPortfolio).order_by(RocketDailyPortfolio.trade_date)).all())
    latest_equity = float(curve[-1].total_equity) if curve else INITIAL_CAPITAL
    outcomes = [value > 0 for value in returns]
    return {
        "totalTrades": len(closed), "winningTrades": len(wins), "losingTrades": len(losses),
        "winRate": round(len(wins) / len(closed) * 100, 2) if closed else 0,
        "averageWinPercent": round(sum(wins) / len(wins), 2) if wins else 0,
        "averageLossPercent": round(sum(losses) / len(losses), 2) if losses else 0,
        "payoffRatio": round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 2) if wins and losses and sum(losses) else None,
        "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "expectancyPercent": round(sum(returns) / len(returns), 2) if returns else 0,
        "maximumWinningStreak": _maximum_streak(outcomes, True),
        "maximumLosingStreak": _maximum_streak(outcomes, False),
        "maximumDrawdownPercent": round(min((float(item.drawdown_pct) for item in curve), default=0), 2),
        "totalReturnPercent": round((latest_equity / INITIAL_CAPITAL - 1) * 100, 2),
        "realizedPnl": round(sum(float(item.realized_pnl) for item in closed), 2),
    }


def _group_stats(items: list[RocketPosition], key_fn) -> list[dict[str, object]]:
    groups: dict[str, list[RocketPosition]] = defaultdict(list)
    for item in items: groups[str(key_fn(item))].append(item)
    result = []
    for key, rows in groups.items():
        rows.sort(key=lambda item: item.exit_time or item.updated_at)
        returns = [_position_return(item) for item in rows]
        wins = [item for item in rows if float(item.realized_pnl) > 0]
        profits = sum(float(item.realized_pnl) for item in rows if float(item.realized_pnl) > 0)
        losses = abs(sum(float(item.realized_pnl) for item in rows if float(item.realized_pnl) < 0))
        equity = peak = INITIAL_CAPITAL
        maximum_drawdown = 0.0
        for item in rows:
            equity += float(item.realized_pnl)
            peak = max(peak, equity)
            maximum_drawdown = min(maximum_drawdown, (equity / peak - 1) * 100)
        result.append({
            "key": key, "tradeCount": len(rows), "winRate": round(len(wins) / len(rows) * 100, 2),
            "averageReturnPercent": round(sum(returns) / len(returns), 2),
            "averageWinPercent": round(sum(v for v in returns if v > 0) / max(1, len([v for v in returns if v > 0])), 2),
            "averageLossPercent": round(sum(v for v in returns if v <= 0) / max(1, len([v for v in returns if v <= 0])), 2),
            "profitFactor": round(profits / losses, 2) if losses else None,
            "maximumLossPercent": round(min(returns), 2),
            "maximumDrawdownPercent": round(maximum_drawdown, 2),
            "totalReturnPercent": round(sum(float(item.realized_pnl) for item in rows) / INITIAL_CAPITAL * 100, 2),
        })
    return result


def _score_bucket(item: RocketPosition) -> str:
    score = float(item.rocket_score_entry)
    if score < 80: return "75～79"
    if score < 85: return "80～84"
    if score < 90: return "85～89"
    if score < 95: return "90～94"
    return "95～100"


def _holding_bucket(item: RocketPosition) -> str:
    days = max(1, ((item.exit_time or item.updated_at).date() - item.entry_time.date()).days)
    for limit in (1, 2, 3, 5, 10, 20):
        if days <= limit: return f"{limit}天"
    return "20天以上"


def dashboard_payload(db: Session) -> dict[str, object]:
    now = datetime.now(UTC)
    account = ensure_rocket_account(db, now)
    regime = db.scalar(select(RocketMarketRegime).order_by(RocketMarketRegime.trade_date.desc()).limit(1))
    trade_date = regime.trade_date if regime else None
    candidates = list(db.scalars(select(RocketCandidate).where(
        RocketCandidate.trade_date == trade_date, RocketCandidate.rank <= 20,
    ).order_by(RocketCandidate.rank)).all()) if trade_date else []
    sectors = list(db.scalars(select(RocketSectorStrength).where(
        RocketSectorStrength.trade_date == trade_date,
    ).order_by(RocketSectorStrength.strength_rank).limit(5)).all()) if trade_date else []
    positions = list(db.scalars(select(RocketPosition).where(RocketPosition.status == "open").order_by(RocketPosition.updated_at.desc())).all())
    closed = list(db.scalars(select(RocketPosition).where(RocketPosition.status == "closed")).all())
    trades = list(db.scalars(select(RocketTrade).order_by(RocketTrade.executed_at.desc()).limit(100)).all())
    curve = list(db.scalars(select(RocketDailyPortfolio).order_by(RocketDailyPortfolio.trade_date)).all())
    notifications = list(db.scalars(select(RocketNotification).order_by(
        RocketNotification.created_at.desc(), RocketNotification.priority,
    ).limit(200)).all())
    market_value = sum(float(item.current_price) * item.remaining_quantity for item in positions)
    equity = float(account.cash) + market_value
    stats = performance_payload(db)
    return {
        "market": {
            "regime": regime.regime if regime else "unknown", "label": regime.regime_label if regime else "等待首次掃描",
            "score": float(regime.score) if regime else 0, "maximumExposurePercent": float(regime.maximum_exposure_pct) if regime else 0,
            "strategy": regime.strategy_label if regime else "尚未取得正式市場資料",
            "reasons": json.loads(regime.reasons_json) if regime else [],
            "missingFields": json.loads(regime.missing_fields_json) if regime else ["market_scan"],
            "updatedAt": regime.evaluated_at.isoformat() if regime else None,
        },
        "account": {
            "initialCapital": float(account.initial_capital), "cash": float(account.cash),
            "marketValue": round(market_value, 2), "totalEquity": round(equity, 2),
            "cumulativePnl": round(equity - INITIAL_CAPITAL, 2),
            "returnPercent": round((equity / INITIAL_CAPITAL - 1) * 100, 2),
            "todayPnl": float(curve[-1].daily_pnl) if curve else 0,
            "realizedPnl": float(account.realized_pnl),
            "unrealizedPnl": round(sum(float(item.unrealized_pnl) for item in positions), 2),
            "positionCount": len(positions),
        },
        "top5": [candidate_payload(item) for item in candidates if item.is_top5],
        "candidates": [candidate_payload(item) for item in candidates],
        "candidateMessage": None if candidates else "今日無符合風險報酬條件的飆股，維持現金。",
        "sectors": [{
            "rank": item.strength_rank, "name": item.sector_name, "score": float(item.strength_score),
            "return1d": float(item.return_1d) if item.return_1d is not None else None,
            "return3d": float(item.return_3d) if item.return_3d is not None else None,
            "return5d": float(item.return_5d) if item.return_5d is not None else None,
            "advanceRatio": float(item.advance_ratio) if item.advance_ratio is not None else None,
            "newHighRatio": float(item.new_high_ratio) if item.new_high_ratio is not None else None,
        } for item in sectors],
        "positions": [{
            "id": item.id, "stockCode": item.stock_code, "stockName": item.stock_name,
            "averageCost": float(item.average_cost), "currentPrice": float(item.current_price),
            "quantity": item.remaining_quantity, "cost": round(float(item.average_cost) * item.remaining_quantity, 2),
            "marketValue": round(float(item.current_price) * item.remaining_quantity, 2),
            "unrealizedPnl": float(item.unrealized_pnl),
            "returnPercent": round((float(item.current_price) / float(item.average_cost) - 1) * 100, 2),
            "highestProfit": float(item.max_favorable_excursion), "maximumLoss": float(item.max_adverse_excursion),
            "stopLoss": float(item.stop_loss_price),
            "trailingStop": float(item.trailing_stop_price) if item.trailing_stop_price is not None else None,
            "holdingDays": max(1, (now.date() - item.entry_time.date()).days + 1),
            "rocketScoreEntry": float(item.rocket_score_entry), "rocketScoreCurrent": float(item.rocket_score_current),
            "addStage": item.add_stage, "latestAction": item.latest_action,
        } for item in positions],
        "performance": stats,
        "equityCurve": [{
            "date": item.trade_date.isoformat(), "cash": float(item.cash), "marketValue": float(item.market_value),
            "totalEquity": float(item.total_equity), "dailyPnl": float(item.daily_pnl),
            "cumulativePnl": float(item.cumulative_pnl), "drawdownPercent": float(item.drawdown_pct),
        } for item in curve],
        "strategyStats": _group_stats(closed, lambda item: item.strategy_type),
        "scoreStats": _group_stats(closed, _score_bucket),
        "holdingStats": _group_stats(closed, _holding_bucket),
        "regimeStats": _group_stats(closed, lambda item: item.market_regime),
        "completedTrades": [{
            "id": item.id, "stockCode": item.stock_code, "stockName": item.stock_name,
            "signalDate": item.entry_time.date().isoformat(), "signalTime": item.entry_time.isoformat(),
            "entryPrice": float(item.entry_price), "averageCost": float(item.average_cost),
            "quantity": item.original_quantity, "investedAmount": float(item.invested_cost),
            "strategyType": item.strategy_type, "rocketScore": float(item.rocket_score_entry),
            "sectorName": item.sector_name, "marketRegime": item.market_regime,
            "stopLossPrice": float(item.stop_loss_price), "highestPrice": float(item.highest_price),
            "lowestPrice": float(item.lowest_price), "exitPrice": float(item.exit_price or 0),
            "exitDate": item.exit_time.date().isoformat() if item.exit_time else None,
            "holdingDays": max(1, ((item.exit_time or item.updated_at).date() - item.entry_time.date()).days + 1),
            "profit": float(item.realized_pnl), "returnPercent": _position_return(item),
            "maximumFavorableExcursion": float(item.max_favorable_excursion),
            "maximumAdverseExcursion": float(item.max_adverse_excursion),
            "isProfit": float(item.realized_pnl) > 0, "exitReason": item.exit_reason,
        } for item in sorted(closed, key=lambda row: row.exit_time or row.updated_at, reverse=True)[:100]],
        "trades": [{
            "id": item.id, "positionId": item.position_id, "timestamp": item.executed_at.isoformat(),
            "stockCode": item.stock_code, "stockName": item.stock_name, "action": item.action,
            "strategyType": item.strategy_type, "price": float(item.price), "quantity": item.quantity,
            "grossAmount": float(item.gross_amount), "fee": float(item.fee), "tax": float(item.tax),
            "netAmount": float(item.net_amount), "realizedPnl": float(item.realized_pnl), "reason": item.reason,
        } for item in trades],
        "notifications": [notification_payload(item) for item in notifications],
        "unreadCount": db.scalar(select(func.count(RocketNotification.id)).where(RocketNotification.is_read.is_(False))) or 0,
        "settings": {
            "brokerFeeDiscount": float(account.broker_fee_discount),
            "slippageRate": float(account.slippage_rate), "soundEnabled": account.sound_enabled,
            "commissionRate": 0.001425, "taxRate": 0.003,
        },
        "updatedAt": now.isoformat(),
    }


def backtest_payload(db: Session, period: str) -> dict[str, object]:
    days = {"1m": 31, "3m": 93, "6m": 186, "1y": 366, "2y": 732, "all": None}[period]
    cutoff = date.today() - timedelta(days=days) if days else None
    query = select(RocketPosition).where(RocketPosition.status == "closed")
    if cutoff is not None:
        query = query.where(RocketPosition.entry_time >= datetime.combine(cutoff, datetime.min.time(), tzinfo=UTC))
    rows = list(db.scalars(query.order_by(RocketPosition.entry_time)).all())
    dates = {item.entry_time.date() for item in rows}
    if len(dates) < 5:
        return {
            "status": "insufficient_history", "period": period, "initialCapital": INITIAL_CAPITAL,
            "tradeDays": len(dates), "tradeCount": len(rows),
            "message": "尚未累積至少 5 個實際掃描交易日；不使用未來資料或虛構歷史結果。",
            "lookAheadBias": False,
        }
    pnl = sum(float(item.realized_pnl) for item in rows)
    returns = [_position_return(item) for item in rows]
    return {
        "status": "completed", "period": period, "initialCapital": INITIAL_CAPITAL,
        "endingCapital": round(INITIAL_CAPITAL + pnl, 2), "totalReturnPercent": round(pnl / INITIAL_CAPITAL * 100, 2),
        "tradeDays": len(dates), "tradeCount": len(rows),
        "winRate": round(len([value for value in returns if value > 0]) / len(rows) * 100, 2),
        "averageReturnPercent": round(sum(returns) / len(returns), 2),
        "lookAheadBias": False,
        "methodology": "只使用各掃描當下已保存的候選、成交與風控事件進行 walk-forward 統計。",
    }
