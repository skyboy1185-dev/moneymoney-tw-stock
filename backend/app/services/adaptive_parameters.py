from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import StrategyParameter


DEFAULT_PARAMETERS: dict[tuple[str, str], tuple[float, str]] = {
    ("universe", "minimum_price"): (10, "最低股價（元）"),
    ("universe", "minimum_listing_days"): (60, "最低上市櫃交易日數"),
    ("universe", "minimum_average_volume_lots"): (500, "近 20 日最低平均成交量（張）"),
    ("universe", "minimum_average_turnover"): (50_000_000, "近 20 日最低平均成交金額"),
    ("universe", "maximum_illiquid_days_5d"): (1, "近五日允許流動性不足天數"),
    ("universe", "exclude_disposed"): (1, "是否排除處置股"),
    ("universe", "minimum_data_completeness"): (0.75, "最低資料完整度"),
    ("regime", "crash_minimum_conditions"): (4, "CRASH 最少成立條件"),
    ("regime", "recovery_minimum_conditions"): (4, "RECOVERY 最少成立條件"),
    ("regime", "range_minimum_conditions"): (5, "RANGE 最少成立條件"),
    ("regime", "breakout_minimum_conditions"): (5, "BREAKOUT 最少成立條件"),
    ("regime", "confirmation_days"): (2, "非 CRASH 狀態切換確認日數"),
    ("regime", "crash_return_5d"): (-7, "CRASH 五日報酬門檻"),
    ("regime", "crash_return_20d"): (-12, "CRASH 二十日報酬門檻"),
    ("regime", "crash_atr_ratio"): (2.5, "CRASH ATR20 比例門檻"),
    ("regime", "crash_advance_ratio"): (25, "CRASH 上漲家數比例上限"),
    ("regime", "immediate_crash_daily_return"): (-4, "單日立即 CRASH 跌幅"),
    ("regime", "breakout_advance_ratio"): (60, "BREAKOUT 上漲家數比例"),
    ("regime", "breakout_volume_ratio"): (1.2, "BREAKOUT 成交量倍數"),
    ("regime", "range_adx_max"): (22, "RANGE ADX 上限"),
    ("recovery", "minimum_score"): (70, "RECOVERY 入選最低分"),
    ("recovery", "observation_score"): (60, "RECOVERY 一般觀察最低分"),
    ("recovery", "relative_strength_minimum"): (3, "相對抗跌最低百分點"),
    ("recovery", "entry_health_minimum"): (75, "進場最低健康度"),
    ("range", "minimum_score"): (70, "RANGE 入選最低分"),
    ("range", "observation_score"): (60, "RANGE 一般觀察最低分"),
    ("range", "minimum_amplitude"): (6, "箱型最低振幅"),
    ("range", "maximum_amplitude"): (15, "箱型最高振幅"),
    ("range", "support_distance"): (2, "箱型下緣距離"),
    ("breakout", "minimum_score"): (75, "BREAKOUT 入選最低分"),
    ("breakout", "observation_score"): (65, "BREAKOUT 回測觀察最低分"),
    ("breakout", "direct_entry_score"): (85, "突破直接小部位門檻"),
    ("breakout", "minimum_breakout_percent"): (1, "有效突破幅度"),
    ("breakout", "minimum_volume_ratio"): (1.5, "有效突破成交量倍數"),
    ("breakout", "maximum_distance_ma20"): (15, "距月線最大百分比"),
    ("risk", "max_risk_per_trade"): (0.5, "單筆最大資金風險百分比"),
    ("risk", "maximum_industry_exposure"): (30, "單一次產業最大持股比例"),
    ("risk", "crash_exposure_max"): (20, "CRASH 總持股上限"),
    ("risk", "recovery_exposure_min"): (20, "RECOVERY 總持股下限"),
    ("risk", "recovery_exposure_max"): (40, "RECOVERY 總持股上限"),
    ("risk", "range_exposure_min"): (40, "RANGE 總持股下限"),
    ("risk", "range_exposure_max"): (60, "RANGE 總持股上限"),
    ("risk", "breakout_exposure_min"): (60, "BREAKOUT 總持股下限"),
    ("risk", "breakout_exposure_max"): (80, "BREAKOUT 總持股上限"),
    ("risk", "uncertain_exposure_min"): (20, "UNCERTAIN 總持股下限"),
    ("risk", "uncertain_exposure_max"): (40, "UNCERTAIN 總持股上限"),
    ("monitor", "maximum_candidates"): (20, "候選股最大檔數"),
    ("monitor", "priority_candidates"): (5, "重點監控檔數"),
    ("monitor", "expiry_trading_days"): (5, "未觸發監控有效交易日"),
    ("monitor", "removal_score"): (65, "監控移除分數"),
    ("notification", "cooldown_minutes"): (30, "相同股票訊號冷卻分鐘"),
    ("automation", "scan_interval_seconds"): (180, "盤中掃描秒數"),
}


def ensure_default_parameters(db: Session) -> None:
    existing = set(db.execute(select(
        StrategyParameter.parameter_group,
        StrategyParameter.parameter_name,
    )).all())
    now = datetime.now(UTC)
    for (group, name), (value, description) in DEFAULT_PARAMETERS.items():
        if (group, name) not in existing:
            db.add(StrategyParameter(
                parameter_group=group,
                parameter_name=name,
                parameter_value=Decimal(str(value)),
                description=description,
                is_enabled=True,
                updated_at=now,
            ))
    db.commit()


def load_parameters(db: Session) -> dict[str, float]:
    ensure_default_parameters(db)
    values = {
        f"{group}.{name}": float(value)
        for (group, name), (value, _) in DEFAULT_PARAMETERS.items()
    }
    for item in db.scalars(select(StrategyParameter).where(StrategyParameter.is_enabled.is_(True))):
        values[f"{item.parameter_group}.{item.parameter_name}"] = float(item.parameter_value)
    return values
