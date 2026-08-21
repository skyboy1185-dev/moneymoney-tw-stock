from app.services.popular_stock_universe import (
    MOMENTUM_UNIVERSE_SIZE,
    POPULAR_ALERT_FALLBACK_STOCKS,
    merge_momentum_stocks,
    parse_tpex_volume_rank,
    parse_twse_volume_rank,
)
from app.services.theme_stock_universe import (
    CPO_ALERT_STOCKS,
    CPO_THEME,
    ELECTRONIC_ALERT_STOCKS,
    PACKAGING_TEST_ALERT_STOCKS,
    PACKAGING_TEST_THEME,
    POWER_ALERT_STOCKS,
    POWER_THEME,
    ThemeStock,
)


def test_twse_volume_rank_keeps_common_stocks_and_skips_etfs() -> None:
    stocks = parse_twse_volume_rank({"data": [
        [1, "00631L", "元大台灣50正2"],
        [2, "3481", "群創"],
        [3, "6770", "力積電"],
    ]})

    assert [stock.symbol for stock in stocks] == ["3481", "6770"]
    assert all(stock.market == "上市" for stock in stocks)


def test_tpex_volume_rank_keeps_top_common_stocks() -> None:
    stocks = parse_tpex_volume_rank([
        {"Rank": "1", "SecuritiesCompanyCode": "5351", "CompanyName": "鈺創"},
        {"Rank": "2", "SecuritiesCompanyCode": "8349A", "CompanyName": "特別股"},
        {"Rank": "3", "SecuritiesCompanyCode": "8358", "CompanyName": "金居"},
    ])

    assert [stock.symbol for stock in stocks] == ["5351", "8358"]
    assert all(stock.market == "上櫃" for stock in stocks)


def test_large_order_momentum_radar_excludes_financial_stocks() -> None:
    twse = parse_twse_volume_rank([
        {"Code": "2891", "Name": "中信金", "TradeValue": "3000"},
        {"Code": "5876", "Name": "上海商銀", "TradeValue": "2500"},
        {"Code": "2330", "Name": "台積電", "TradeValue": "2000"},
    ])
    tpex = parse_tpex_volume_rank([
        {"SecuritiesCompanyCode": "6026", "CompanyName": "福邦證", "TransactionAmount": "2000"},
        {"SecuritiesCompanyCode": "8358", "CompanyName": "金居", "TransactionAmount": "1500"},
    ])
    merged, _ = merge_momentum_stocks((
        ThemeStock("2881", "富邦金", "上市", "金融保險", ("熱門股",)),
        *twse,
        *tpex,
    ))

    assert [stock.symbol for stock in twse] == ["2330"]
    assert [stock.symbol for stock in tpex] == ["8358"]
    assert "2881" not in {stock.symbol for stock in merged}


def test_daily_market_rows_are_ranked_by_turnover_and_tpex_uses_latest_date() -> None:
    twse = parse_twse_volume_rank([
        {"Code": "2330", "Name": "台積電", "TradeValue": "900"},
        {"Code": "2317", "Name": "鴻海", "TradeValue": "1200"},
    ])
    tpex = parse_tpex_volume_rank([
        {"Date": "1150806", "SecuritiesCompanyCode": "5483", "CompanyName": "中美晶", "TransactionAmount": "9999"},
        {"Date": "1150807", "SecuritiesCompanyCode": "5351", "CompanyName": "鈺創", "TransactionAmount": "800"},
        {"Date": "1150807", "SecuritiesCompanyCode": "8358", "CompanyName": "金居", "TransactionAmount": "1000"},
    ])

    assert [stock.symbol for stock in twse] == ["2317", "2330"]
    assert [stock.symbol for stock in tpex] == ["8358", "5351"]


def test_momentum_universe_is_capped_at_exactly_three_hundred() -> None:
    dynamic = tuple(
        ThemeStock(str(4000 + index), f"熱門{index}", "上市", "市場熱門", ("熱門股",))
        for index in range(400)
        if 4000 + index <= 9999
    )
    stocks, _ = merge_momentum_stocks(dynamic)

    assert len(stocks) == MOMENTUM_UNIVERSE_SIZE == 300
    assert len({stock.symbol for stock in stocks}) == 300


def test_momentum_universe_adds_popular_stocks_without_duplicates() -> None:
    dynamic = parse_twse_volume_rank({"data": [
        [1, "3481", "群創"],
        [2, "2330", "台積電"],
    ]})
    stocks, popular_symbols = merge_momentum_stocks(dynamic)
    symbols = [stock.symbol for stock in stocks]

    assert len(symbols) == len(set(symbols))
    assert "3481" in symbols
    assert "2330" in symbols
    assert "3481" in popular_symbols
    assert "2330" in popular_symbols
    assert len(stocks) > 63
    assert len(popular_symbols) >= len(POPULAR_ALERT_FALLBACK_STOCKS)


def test_cpo_supply_chain_is_always_in_large_order_momentum_universe() -> None:
    expected = {
        "3081", "2455", "4971", "3714", "3105", "4908", "6488", "4991",
        "3363", "3163", "4977", "4979", "6442", "3234", "6789", "3008",
        "3711", "3265", "6257", "3450", "6451", "2345", "3380", "2317",
        "2330", "2303", "5347", "2454", "2379", "3443", "6830", "3587",
        "3289", "6706", "6223", "6515", "2360",
    }
    cpo_by_symbol = {stock.symbol: stock for stock in CPO_ALERT_STOCKS}
    momentum_by_symbol = {stock.symbol: stock for stock in ELECTRONIC_ALERT_STOCKS}

    assert expected == set(cpo_by_symbol)
    assert expected <= set(momentum_by_symbol)
    assert all(CPO_THEME in momentum_by_symbol[symbol].themes for symbol in expected)
    assert len(ELECTRONIC_ALERT_STOCKS) == len(momentum_by_symbol)


def test_packaging_and_test_supply_chain_is_always_monitored() -> None:
    expected = {
        "3711", "6239", "2449", "8150", "6257", "3265", "6147", "2329",
        "8110", "6451", "3374", "6515", "6223", "6510", "6683", "3289",
        "6830", "3587", "3131", "3583", "6187", "6640", "3413", "6196",
        "2360", "5443", "2467", "8027", "3372", "1560", "3037", "3189",
        "8046",
    }
    packaging = {stock.symbol: stock for stock in PACKAGING_TEST_ALERT_STOCKS}
    momentum = {stock.symbol: stock for stock in ELECTRONIC_ALERT_STOCKS}

    assert expected == set(packaging)
    assert expected <= set(momentum)
    assert all(PACKAGING_TEST_THEME in momentum[symbol].themes for symbol in expected)


def test_power_supply_chain_is_always_monitored() -> None:
    expected = {
        "2308", "2301", "6412", "6282", "3015", "2420", "6409", "2457",
        "3078", "6203", "5309", "6121", "3211", "4931", "6781", "3323",
        "6558", "5227", "4721", "6415", "6138", "8081", "6719", "3257",
        "3288", "6291", "6651", "6693", "6799", "6129", "3588", "2436",
        "3317", "6435", "8261", "5299", "3675", "2481", "5425", "2342",
        "3707", "3016", "6182", "2455", "6488", "6525", "2351", "5285",
        "8255", "2303", "5347", "2327", "2492", "6173", "2375", "1519",
        "1503", "1513", "1514", "2371", "1504", "1609", "1612", "1608",
        "1618", "1617", "6806", "6869", "1529", "4588",
    }
    power = {stock.symbol: stock for stock in POWER_ALERT_STOCKS}
    momentum = {stock.symbol: stock for stock in ELECTRONIC_ALERT_STOCKS}

    assert expected == set(power)
    assert expected <= set(momentum)
    assert all(POWER_THEME in momentum[symbol].themes for symbol in expected)
    assert len(ELECTRONIC_ALERT_STOCKS) == len(momentum)
