from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .day_trading_cache import day_trading_cache
from .ai_stock_line_messaging import ai_stock_line_dispatcher
from .line_messaging import (
    PERSONAL_STRATEGY_SIMULATION_NOTE,
    LineNotificationEvent,
    format_personal_strategy_simulation,
)
from .mock_market import stock_payload


TAIPEI = ZoneInfo("Asia/Taipei")
DISCLAIMER = PERSONAL_STRATEGY_SIMULATION_NOTE


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
    return format_personal_strategy_simulation(
        stock_name=monitor["stockName"],
        symbol=monitor["symbol"],
        entry_min=monitor["entryMin"],
        entry_max=monitor["entryMax"],
        stop_loss=monitor["stopLoss"],
        target_1=monitor["target1"],
        target_2=monitor.get("target2"),
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
    reduce_text = ""
    if action.startswith("建議減碼"):
        try:
            percentage = Decimal(action.split(" ")[1].replace("%", ""))
        except (IndexError, ValueError):
            percentage = Decimal("50")
        shares = int(
            (Decimal(position["remainingQuantity"]) * percentage / 100)
            .to_integral_value(rounding=ROUND_DOWN)
        )
        reduce_text = (
            f"建議賣出數量：{shares:,} 股／{Decimal(shares) / Decimal(1000):.3f} 張\n"
            f"剩餘部位停損：{_price(position['stopLoss'])}\n"
        )
    return (
        f"【超強AI當沖系統｜{title}】\n\n"
        f"股票：{position['symbol']} {position['stockName']}\n"
        f"目前價格：{_price(position['currentPrice'])}\n"
        f"實際平均成本：{_price(position['averageCost'])}\n"
        f"目前報酬：{position['returnPercentage']}%\n"
        f"指令：{action}\n"
        f"{reduce_text}"
        f"停損價格：{_price(position['stopLoss'])}\n"
        f"原因：\n{reason_text}\n"
        f"通知時間：{_time(datetime.now(UTC))}\n\n"
        "請自行確認即時行情與實際成交價格。\n"
        f"{DISCLAIMER}"
    )


def add_on_message(position: dict[str, Any], add_on: dict[str, Any]) -> str:
    stage = "第一次" if add_on["addOnNumber"] == 1 else "第二次"
    quantity = int(add_on["suggestedQuantity"])
    new_allocation = (
        Decimal(str(position["currentAllocationPercentage"]))
        + Decimal(str(add_on["suggestedPercentage"]))
    )
    total_quantity = int(position["remainingQuantity"]) + quantity
    estimated_average = (
        Decimal(str(position["averageCost"])) * int(position["remainingQuantity"])
        + Decimal(str(position["currentPrice"])) * quantity
    ) / max(1, total_quantity)
    return (
        f"【超強AI當沖系統｜{stage}加碼確認】\n\n"
        f"股票：{position['symbol']} {position['stockName']}\n"
        f"目前價格：{_price(position['currentPrice'])}\n"
        f"原始進場價：{_price(position['entryPrice'])}\n"
        f"目前平均成本：{_price(position['averageCost'])}\n"
        f"目前持倉：總資金 {position['currentAllocationPercentage']}%\n\n"
        "建議加碼：\n"
        f"- 建議加碼比例：總資金 {add_on['suggestedPercentage']}%\n"
        f"- 建議加碼金額：新台幣 {_money(add_on['suggestedAmount'])}\n"
        f"- 建議加碼數量：{quantity:,} 股／{Decimal(quantity) / Decimal(1000):.3f} 張\n"
        f"- 加碼後總部位：總資金 {new_allocation}%\n"
        f"加碼參考區：{_price(add_on['suggestedPriceMin'])}～{_price(add_on['suggestedPriceMax'])}\n"
        f"加碼後停損：{_price(add_on['newStopLoss'])}\n\n"
        f"加碼後平均成本參考：{_price(estimated_average)}\n\n"
        "原因：\n- 順勢突破確認\n- 健康度與部位風險合格\n\n"
        "風險：\n- LINE 通知不代表已成交，禁止虧損攤平\n\n"
        "請自行確認即時行情與可用資金。\n"
        f"{DISCLAIMER}"
    )


def daily_position_summary_message(position: dict[str, Any]) -> str:
    remaining = max(
        Decimal("0"),
        Decimal(str(position["targetAllocationPercentage"]))
        - Decimal(str(position["currentAllocationPercentage"])),
    )
    return (
        "【超強AI當沖系統｜每日持倉摘要】\n\n"
        f"股票：{position['symbol']} {position['stockName']}\n"
        f"實際平均成本：{_price(position['averageCost'])}\n"
        f"今日收盤：{_price(position['currentPrice'])}\n"
        f"目前報酬：{position['returnPercentage']}%\n"
        f"目前資金占比：{position['currentAllocationPercentage']}%\n"
        f"健康度：{position['healthScore']}\n"
        f"最新狀態：{position['latestAction']}\n"
        f"停損參考：{_price(position['stopLoss'])}\n"
        f"移動停利：{_price(position.get('trailingStop'))}\n"
        f"剩餘可加碼：{remaining}%\n\n"
        "明日監控重點：優先檢查停損、移動停利與原始策略是否仍成立。\n\n"
        f"{DISCLAIMER}"
    )


def friday_replay_messages(trade_date: date) -> list[tuple[str, str | None]]:
    """Build an explicitly labelled demo replay without treating mock prices as live quotes."""
    scenarios = [
        {
            "symbol": "2330",
            "score": 87,
            "strategy_fit": 85,
            "health": 82,
            "allocation": Decimal("18"),
            "reasons": ["站上 MA20", "MACD 維持紅柱", "成交量配合趨勢"],
            "warnings": ["展示模式價格，不是即時行情", "需等待價格進入正式進場區"],
        },
        {
            "symbol": "2382",
            "score": 82,
            "strategy_fit": 80,
            "health": 78,
            "allocation": Decimal("15"),
            "reasons": ["中期均線向上", "回檔量縮後轉強", "大盤適配度合格"],
            "warnings": ["展示模式價格，不是即時行情", "正式通知仍須通過即時價差與流動性檢查"],
        },
    ]
    messages: list[tuple[str, str | None]] = [(
        "【超強AI當沖系統｜展示模擬回放】\n\n"
        f"回放日期：{trade_date.isoformat()}（星期五）\n"
        "大盤狀態：偏多（模擬）\n"
        "今日 AI 精選：2／5 檔\n"
        "流程：正式精選 → AI監控區 → 等待進場確認\n\n"
        "本次使用 Mock 台股資料，只測試 LINE 訊息格式與傳送流程。\n"
        "不是即時行情，也不是正式交易訊號。\n"
        f"{DISCLAIMER}",
        None,
    )]
    for scenario in scenarios:
        payload = stock_payload(scenario["symbol"])
        if payload is None:
            continue
        candle = next(
            (item for item in payload["prices"] if item["date"] == trade_date.isoformat()),
            payload["prices"][-1],
        )
        current_price = Decimal(str(candle["close"]))
        entry_min = (current_price * Decimal(".995")).quantize(Decimal(".01"))
        entry_max = (current_price * Decimal("1.005")).quantize(Decimal(".01"))
        stop_loss = (current_price * Decimal(".96")).quantize(Decimal(".01"))
        target_1 = (current_price * Decimal("1.06")).quantize(Decimal(".01"))
        target_2 = (current_price * Decimal("1.10")).quantize(Decimal(".01"))
        initial_percentage = scenario["allocation"] * Decimal(".4")
        initial_amount = Decimal("1000000") * initial_percentage / 100
        quantity = int((initial_amount / current_price).to_integral_value(rounding=ROUND_DOWN))
        estimated_risk = (current_price - stop_loss) * quantity
        monitor = {
            "symbol": scenario["symbol"],
            "stockName": payload["meta"]["name"],
            "strategyName": "波段起漲 Bot",
            "currentPrice": current_price,
            "entryMin": entry_min,
            "entryMax": entry_max,
            "targetAllocationPercentage": scenario["allocation"],
            "initialAllocationPercentage": initial_percentage,
            "suggestedInitialAmount": initial_amount,
            "suggestedInitialQuantity": quantity,
            "estimatedRiskAmount": estimated_risk,
            "stopLoss": stop_loss,
            "target1": target_1,
            "target2": target_2,
            "totalScore": scenario["score"],
            "strategyFit": scenario["strategy_fit"],
            "healthScore": scenario["health"],
            "riskRewardRatio": Decimal("2"),
            "updatedAt": f"{trade_date.isoformat()}T10:15:00+08:00",
            "expiredAt": f"{trade_date.isoformat()}T10:25:00+08:00",
            "firstAddOnPercentage": scenario["allocation"] * Decimal(".3"),
            "secondAddOnPercentage": scenario["allocation"] * Decimal(".3"),
            "reasons": scenario["reasons"],
            "warnings": scenario["warnings"],
        }
        messages.append((
            f"【展示模擬｜{trade_date.isoformat()} 歷史回放】\n"
            "以下不是正式訊號，不代表當日真實選股結果。\n\n"
            f"{initial_entry_message(monitor)}",
            scenario["symbol"],
        ))
    return messages


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
    return await ai_stock_line_dispatcher.dispatch_many([event])
