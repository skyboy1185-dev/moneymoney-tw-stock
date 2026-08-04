from __future__ import annotations


REGIME_SINGLE_LIMITS = {"CRASH": 0, "RECOVERY": 10, "RANGE": 12, "BREAKOUT": 15, "UNCERTAIN": 5}


def allocation_percent(regime: str, score: float) -> float:
    limit = REGIME_SINGLE_LIMITS.get(regime, 5)
    if regime == "CRASH" or score < 70:
        return 0
    floor = {"RECOVERY": 5, "RANGE": 8, "BREAKOUT": 10}.get(regime, 3)
    return round(min(limit, floor + max(0, score - 70) / 30 * (limit - floor)), 2)


def position_size_shares(
    capital: float,
    entry_price: float,
    stop_loss: float,
    risk_percent: float,
) -> dict[str, float | int | str]:
    per_share_risk = max(0, entry_price - stop_loss)
    if capital <= 0 or per_share_risk <= 0:
        return {"shares": 0, "lots": 0, "oddLotShares": 0, "message": "進場價或停損價無法計算部位"}
    raw_shares = int(capital * risk_percent / 100 / per_share_risk)
    lots = raw_shares // 1000
    odd = raw_shares % 1000
    message = "可使用整張交易" if lots else f"不足一張，可評估零股 {odd} 股或放棄交易"
    return {"shares": raw_shares, "lots": lots, "oddLotShares": odd, "message": message}
