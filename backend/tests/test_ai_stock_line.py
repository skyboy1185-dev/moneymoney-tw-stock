from app.services.ai_stock_line import (
    daily_position_summary_message,
    initial_entry_message,
    position_action_message,
)


def test_initial_entry_line_message_states_confirmation_not_execution() -> None:
    message = initial_entry_message({
        "symbol": "2330", "stockName": "台積電", "strategyName": "波段起漲 Bot",
        "currentPrice": 1000, "entryMin": 990, "entryMax": 1000,
        "targetAllocationPercentage": 20, "initialAllocationPercentage": 8,
        "suggestedInitialAmount": 80000, "suggestedInitialQuantity": 80,
        "estimatedRiskAmount": 4000, "stopLoss": 950, "target1": 1075, "target2": 1125,
        "totalScore": 90, "strategyFit": 88, "healthScore": 84, "riskRewardRatio": 2.5,
        "updatedAt": "2026-07-27T09:10:00+08:00", "expiredAt": "2026-07-27T09:20:00+08:00",
        "firstAddOnPercentage": 6, "secondAddOnPercentage": 6,
        "reasons": ["MACD 翻紅", "站上 MA20", "成交量增加"], "warnings": ["不可追價"],
    })
    assert "初始買進確認" in message
    assert "建議數量：80 股" in message
    assert "請自行確認即時行情、資金與交易條件" in message
    assert "不構成投資建議" in message


def test_stop_message_is_explicit_but_does_not_claim_execution() -> None:
    message = position_action_message({
        "symbol": "2317", "stockName": "鴻海", "currentPrice": 245,
        "averageCost": 252.5, "returnPercentage": -2.97, "stopLoss": 246,
    }, "立即停損", ["現價跌破硬性停損"])
    assert "緊急停損" in message
    assert "請自行確認即時行情與實際成交價格" in message


def test_daily_position_summary_keeps_overnight_position_as_monitoring() -> None:
    message = daily_position_summary_message({
        "symbol": "2330",
        "stockName": "台積電",
        "averageCost": 1000,
        "currentPrice": 1020,
        "returnPercentage": 2,
        "targetAllocationPercentage": 20,
        "currentAllocationPercentage": 8,
        "healthScore": 82,
        "latestAction": "隔夜持有",
        "stopLoss": 970,
        "trailingStop": 995,
    })
    assert "每日持倉摘要" in message
    assert "隔夜持有" in message
    assert "剩餘可加碼：12%" in message
    assert "不構成投資建議" in message
