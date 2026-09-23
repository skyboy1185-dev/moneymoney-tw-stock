from datetime import date
from app.services.strong_stock_fundamentals import financial_features


def test_revenue_uses_three_consecutive_months_and_matching_prior_year():
    rows = [{"date": "2026-09-10", "revenue_year": year, "revenue_month": month, "revenue": amount}
            for year, amount in [(2025, 100), (2026, 150)] for month in [6, 7, 8]]
    result = financial_features(rows, [], date(2026, 9, 14))
    assert result["revenue_yoy"] == 50
    assert result["revenue_3m_yoy"] == 50
    assert "revenue_3m_yoy" not in financial_features(rows[1:], [], date(2026, 9, 14))
    assert financial_features(rows, [], date(2026, 9, 1)) == {}


def test_margin_changes_are_percentage_points_and_do_not_fabricate_trailing_eps():
    rows = [{"date": day, "type": kind, "value": value}
            for day, gross, operating in [("2025-06-30", 20, 10), ("2026-06-30", 30, 15)]
            for kind, value in [("Revenue", 100), ("GrossProfit", gross), ("OperatingIncome", operating), ("EPS", 1)]]
    result = financial_features([], rows, date(2026, 9, 14))
    assert round(result["gross_margin_change"], 6) == 10
    assert round(result["operating_margin_change"], 6) == 5
    assert "trailing_eps" not in result
    assert financial_features([], rows, date(2027, 9, 14)) == {}
