from datetime import UTC, datetime

from app.models import LimitUpAiSettings
from app.services.limit_up_ai import score_limit_up_candidate


NOW = datetime(2026, 8, 27, 2, 15, tzinfo=UTC)  # 10:15 Taipei


def _settings() -> LimitUpAiSettings:
    return LimitUpAiSettings(
        user_id="test-user",
        capital=3_000_000,
        min_price=20,
        max_price=500,
        min_average_turnover_20d=100_000_000,
        min_volume_ratio_20d=1.8,
        first_position_pct=.10,
        max_position_pct=.20,
        max_positions=3,
        max_loss_per_trade_pct=.005,
        max_daily_loss_pct=.01,
        max_consecutive_stops=3,
        overnight_total_pct=.30,
        overnight_single_pct=.15,
        exclude_locked_limit_up=True,
        sound_enabled=False,
        updated_at=NOW,
    )


def _signal(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "4939-long-test",
        "symbol": "4939",
        "stockName": "亞電",
        "market": "上市",
        "price": 107.0,
        "previousClose": 100.0,
        "open": 102.0,
        "changePercent": 7.0,
        "volume": 2_500_000,
        "turnover": 267_500_000,
        "volumeScore": 95,
        "confirmationScore": 88,
        "industryScore": 85,
        "marketAlignment": 80,
        "rangePositionPercent": 92,
        "vwapStatus": "站上且向上",
        "vwapDeviationPercent": 1.2,
        "fiveMinuteStructure": "低點墊高",
        "fiveMinuteBreakout": True,
        "fiveMinuteLongRetest": True,
        "entryRetestConfirmed": True,
        "threeGateCrossed": True,
        "largeOrderForce": 260,
        "largeOrderContinuousBuy": True,
        "largeOrderDataAvailable": True,
        "spreadPercentage": 0.2,
        "quoteIsRealtime": True,
        "bidVolumes": [300_000, 220_000, 180_000],
        "askVolumes": [120_000, 100_000, 80_000],
    }
    value.update(overrides)
    return value


def test_pre_limit_attack_becomes_actionable_candidate() -> None:
    item = score_limit_up_candidate(_signal(), _settings(), now=NOW)

    assert item["category"] == "attack"
    assert item["setupType"] == "pre_limit_attack"
    assert item["actionable"] is True
    assert item["score"] >= 85


def test_locked_limit_up_is_not_chased_by_default() -> None:
    item = score_limit_up_candidate(
        _signal(price=109.9, changePercent=9.9),
        _settings(),
        now=NOW,
    )

    assert item["actionable"] is False
    assert any("鎖住漲停" in reason for reason in item["failures"])


def test_price_filter_blocks_out_of_range_stock() -> None:
    item = score_limit_up_candidate(
        _signal(price=12.0, previousClose=11.5, changePercent=4.35),
        _settings(),
        now=NOW,
    )

    assert item["actionable"] is False
    assert any("股價不在" in reason for reason in item["failures"])
