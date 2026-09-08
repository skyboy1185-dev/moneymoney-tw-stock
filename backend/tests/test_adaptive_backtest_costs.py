from datetime import date, timedelta

import pytest

from app.adaptive_schemas import AdaptiveBacktestPrice, AdaptiveBacktestRequest
from app.services import adaptive_backtest_service as service


@pytest.mark.parametrize("slippage, commission, discount, tax", [
    (0.001, 0, 1, 0),
    (0.001, 0.001425, 0.2, 0.003),
    (0, 0.001425, 0.2, 0.003),
    (0, 0, 1, 0),
])
def test_flat_price_round_trip_charges_slippage_once_per_side(monkeypatch, slippage, commission, discount, tax):
    prices = [AdaptiveBacktestPrice(
        date=date(2026, 1, 1) + timedelta(days=index), open=100, high=101,
        low=99, close=100, volume=1000,
    ) for index in range(80)]
    monkeypatch.setattr(service, "_entry_signal", lambda _strategy, _prices, index: index == 61)
    request = AdaptiveBacktestRequest(
        stock_code="2330", stock_name="test", strategy_type="RECOVERY", years=1,
        prices=prices, commission_rate=commission, commission_discount=discount,
        tax_rate=tax, slippage_rate=slippage,
    )
    result = service.run_backtest(request)
    assert result["signalCount"] == 1
    trade = result["trades"][0]
    assert trade["entry_date"] == prices[62].date.isoformat()
    assert trade["entry_price"] == pytest.approx(100 * (1 + slippage))
    assert trade["exit_price"] == pytest.approx(100 * (1 - slippage))
    expected = ((1 - slippage) / (1 + slippage) - 1 - 2 * commission * discount - tax) * 100
    assert trade["return_percent"] == round(expected, 4)
    assert result["totalReturn"] == round(expected, 2)
    assert result["costs"]["slippageRate"] == slippage
