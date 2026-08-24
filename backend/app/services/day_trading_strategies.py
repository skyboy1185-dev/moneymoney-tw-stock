from __future__ import annotations

from typing import Any


STRATEGY_ROBOTS: tuple[dict[str, Any], ...] = (
    {
        "id": "strong-bull-breakout",
        "name": "強多突破機器人",
        "direction": "long",
        "useWhen": "多空分數 60～100（強多盤）",
        "description": "順著強勢趨勢，只做多方突破與回測確認。",
        "entryRule": "站穩中關價或突破上關價，等待 5 分 K 回測、量能與大單買盤確認。",
        "avoidRule": "不追高、不接逆勢空單。",
    },
    {
        "id": "bull-pullback",
        "name": "多頭回撤機器人",
        "direction": "long",
        "useWhen": "多空分數 20～59（偏多盤）",
        "description": "偏多但不追價，以回撤關鍵價的承接為主要買點。",
        "entryRule": "回撤中關價不破並重新轉強，搭配 5 分 K 均線向上與 VWAP 確認。",
        "avoidRule": "跌破下關價或回測失敗時停止做多。",
    },
    {
        "id": "range-two-way",
        "name": "震盪雙向機器人",
        "direction": "both",
        "useWhen": "多空分數 -19～19（震盪盤）",
        "description": "盤勢沒有明確方向時，同時保留多空，但只在關鍵價確認後進場。",
        "entryRule": "中關價站回做多；下關價跌破後回測不過做空，必須通過 5 分 K 確認。",
        "avoidRule": "區間中央、量能不足或方向反覆時觀望。",
    },
    {
        "id": "bear-rebound-short",
        "name": "空頭反彈機器人",
        "direction": "short",
        "useWhen": "多空分數 -59～-20（偏空盤）",
        "description": "偏空盤不追殺，等待反彈到關鍵價失敗後放空。",
        "entryRule": "反彈中關價或下關價無法站回，搭配 5 分 K 均線向下與賣壓確認。",
        "avoidRule": "急跌低檔不追空，站回中關價即取消。",
    },
    {
        "id": "strong-bear-breakdown",
        "name": "強空跌破機器人",
        "direction": "short",
        "useWhen": "多空分數 -100～-60（強空盤）",
        "description": "順著強空趨勢，以跌破下關價後的回測失敗為主要空點。",
        "entryRule": "跌破下關價後等待回測不過，再由完整 5 分 K 與賣壓確認放空。",
        "avoidRule": "不在日內最低附近追空，不做逆勢多單。",
    },
)


def _robot_for_score(score: float) -> dict[str, Any]:
    if score >= 60:
        return STRATEGY_ROBOTS[0]
    if score >= 20:
        return STRATEGY_ROBOTS[1]
    if score > -20:
        return STRATEGY_ROBOTS[2]
    if score > -60:
        return STRATEGY_ROBOTS[3]
    return STRATEGY_ROBOTS[4]


def _strategy_confidence(score: float, data_status: str, mode: str) -> int:
    strength = abs(score)
    if strength >= 60:
        confidence = 65 + (strength - 60) * .875
    elif strength >= 20:
        confidence = 62 + (strength - 20) * .75
    else:
        confidence = 88 - strength * 1.5
    if data_status not in {"normal", "closed"}:
        confidence = min(confidence, 30)
    if mode not in {"official", "warming_up"}:
        confidence = min(confidence, 45)
    return round(max(0, min(100, confidence)))


def _confidence_label(confidence: int) -> str:
    if confidence >= 85:
        return "高"
    if confidence >= 70:
        return "中高"
    if confidence >= 55:
        return "中"
    return "低"


def strategy_context(
    regime: dict[str, Any],
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    score = float(regime.get("score", 0))
    selected = _robot_for_score(score)
    data_status = str(regime.get("dataStatus", "source_error"))
    mode = str(regime.get("mode", "demo"))
    confidence = _strategy_confidence(score, data_status, mode)
    phase = str((session or {}).get("phase", "scanning"))
    formal_allowed = bool((session or {}).get("formalSignalsAllowed", data_status == "normal"))
    if data_status not in {"normal", "closed"}:
        status, status_label = "paused", "行情異常・暫停進場"
    elif phase == "warmup":
        status, status_label = "warming_up", "暖機確認中"
    elif phase == "scanning" and formal_allowed:
        status, status_label = "active", "目前使用中"
    elif phase in {"entry_closed", "closing"}:
        status, status_label = "managing", "停止新進場・持倉管理中"
    else:
        status, status_label = "standby", "盤後待機"
    direction_label = {
        "long": "只做多",
        "short": "只做空",
        "both": "多空雙向",
    }[str(selected["direction"])]
    active = {
        **selected,
        "confidence": confidence,
        "confidenceLabel": _confidence_label(confidence),
        "status": status,
        "statusLabel": status_label,
        "directionLabel": direction_label,
        "reasons": [
            f"目前多空分數 {score:+.0f}，落在「{selected['useWhen']}」",
            f"策略方向：{direction_label}",
            "行情品質正常" if data_status == "normal" else "目前為盤後有效行情" if data_status == "closed" else "行情品質異常，信心度已降級",
        ],
    }
    roster = [
        {**robot, "selected": robot["id"] == selected["id"]}
        for robot in STRATEGY_ROBOTS
    ]
    return {"activeRobot": active, "strategyRobots": roster}


def route_signals_to_active_robot(
    signals: list[dict[str, Any]],
    active_robot: dict[str, Any],
) -> list[dict[str, Any]]:
    allowed_direction = str(active_robot["direction"])
    routed: list[dict[str, Any]] = []
    for signal in signals:
        aligned = allowed_direction == "both" or signal.get("direction") == allowed_direction
        routed.append({
            **signal,
            "strategyRobotId": active_robot["id"],
            "strategyRobotName": active_robot["name"],
            "strategyConfidence": active_robot["confidence"],
            "strategyAligned": aligned,
        })
    return routed


def strategy_eligible_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [signal for signal in signals if signal.get("strategyAligned", True)]
