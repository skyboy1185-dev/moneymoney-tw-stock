from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .day_trading_cache import day_trading_cache
from .line_messaging import LineNotificationEvent, line_notification_dispatcher


TAIPEI = ZoneInfo("Asia/Taipei")
DISCLAIMER = "僅供研究參考，不構成投資建議。"


def _money(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):,.0f}"
    except Exception:
        return "—"


def _price(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):,.2f}"
    except Exception:
        return "—"


def _time(value: Any) -> str:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "—"


def initial_entry_message(monitor: dict[str, Any]) -> str:
    reasons = "\n".join(f"- {reason}" for reason in monitor["reasons"][:5])
    warnings = "\n".join(f"- {warning}" for warning in monitor["warnings"][:5]) or "- 無"
    quantity = int(monitor["suggestedInitialQuantity"])
    return (
        "【AI選股機器人｜初始買進確認】\n\n"
        f"股票：{monitor['symbol']} {monitor['stockName']}\n"
        f"策略：{monitor['strategyName']}\n"
        f"目前價格：{_price(monitor['currentPrice'])}\n"
        f"建議進場區：{_price(monitor['entryMin'])}～{_price(monitor['entryMax'])}\n\n"
        "資金配置：\n"
        f"- 建議最終部位：總資金 {monitor['targetAllocationPercentage']}%\n"
        f"- 初始建倉：總資金 {monitor['initialAllocationPercentage']}%\n"
        f"- 建議投入：新台幣 {_money(monitor['suggestedInitialAmount'])}\n"
        f"- 建議數量：{quantity:,} 股／{Decimal(quantity) / Decimal(1000):.3f} 張\n"
        f"- 單筆預估最大損失：新台幣 {_money(monitor['estimatedRiskAmount'])}\n\n"
        f"停損參考：{_price(monitor['stopLoss'])}\n"
        f"第一目標：{_price(monitor['target1'])}\n"
        f"第二目標：{_price(monitor['target2'])}\n"
        f"條件符合分數：{monitor['totalScore']}\n"
        f"策略適配度：{monitor['strategyFit']}%\n"
        f"健康度：{monitor['healthScore']}\n"
        f"風險報酬比：1：{monitor['riskRewardRatio']}\n"
        f"訊號時間：{_time(monitor['updatedAt'])}\n"
        f"有效期限：{_time(monitor['expiredAt'])}\n\n"
        "後續加碼計畫：\n"
        f"- 第一次加碼：總資金 {monitor['firstAddOnPercentage']}%\n"
        f"- 第二次加碼：總資金 {monitor['secondAddOnPercentage']}%\n\n"
        f"買進理由：\n{reasons}\n\n風險提醒：\n{warnings}\n\n"
        "請自行確認即時行情、資金與交易條件。\n"
        f"{DISCLAIMER}"
    )


def position_action_message(position: dict[str, Any], action: str, reasons: list[str]) -> str:
    if action == "立即停損":
        title = "緊急停損"
    elif action == "建議全部賣出":
        title = "賣出提醒"
    elif action.startswith("建議減碼"):
        title = "建議減碼"
    else:
        title = action
    reason_text = "\n".join(f"- {reason}" for reason in reasons)
    return (
        f"【AI選股機器人｜{title}】\n\n"
        f"股票：{position['symbol']} {position['stockName']}\n"
        f"目前價格：{_price(position['currentPrice'])}\n"
        f"實際平均成本：{_price(position['averageCost'])}\n"
        f"目前報酬：{position['returnPercentage']}%\n"
        f"指令：{action}\n"
        f"停損價格：{_price(position['stopLoss'])}\n"
        f"原因：\n{reason_text}\n"
        f"通知時間：{_time(datetime.now(UTC))}\n\n"
        "請自行確認即時行情與實際成交價格。\n"
        f"{DISCLAIMER}"
    )


def add_on_message(position: dict[str, Any], add_on: dict[str, Any]) -> str:
    stage = "第一次" if add_on["addOnNumber"] == 1 else "第二次"
    return (
        f"【AI選股機器人｜{stage}加碼確認】\n\n"
        f"股票：{position['symbol']} {position['stockName']}\n"
        f"目前價格：{_price(position['currentPrice'])}\n"
        f"原始進場價：{_price(position['entryPrice'])}\n"
        f"目前平均成本：{_price(position['averageCost'])}\n"
        f"目前持倉：總資金 {position['currentAllocationPercentage']}%\n\n"
        "建議加碼：\n"
        f"- 建議加碼比例：總資金 {add_on['suggestedPercentage']}%\n"
        f"- 建議加碼金額：新台幣 {_money(add_on['suggestedAmount'])}\n"
        f"- 建議加碼數量：{add_on['suggestedQuantity']:,} 股\n"
        f"加碼參考區：{_price(add_on['suggestedPriceMin'])}～{_price(add_on['suggestedPriceMax'])}\n"
        f"加碼後停損：{_price(add_on['newStopLoss'])}\n\n"
        "請自行確認即時行情與可用資金。\n"
        f"{DISCLAIMER}"
    )


async def push_ai_stock_message(
    *,
    event_type: str,
    action: str,
    message: str,
    signal_id: str,
    symbol: str,
    priority: int,
) -> int:
    dedupe = f"ai-stock:{signal_id}:{action}"
    if not day_trading_cache.claim_once(dedupe):
        return 0
    event = LineNotificationEvent(
        event_type=event_type,
        action=action,
        message=message,
        dedupe_key=dedupe,
        priority=priority,
        signal_id=signal_id,
        symbol=symbol,
        cooldown_entry=event_type == "ai_initial_entry",
    )
    return await line_notification_dispatcher.dispatch_many([event])
