from __future__ import annotations

from dataclasses import dataclass


AI_THEME = "AI"
LEO_THEME = "低軌衛星"
PCB_THEME = "PCB"
ABF_THEME = "ABF載板"
PASSIVE_THEME = "被動元件"
MEMORY_THEME = "記憶體"
FIBERGLASS_THEME = "玻纖布"
FACILITY_THEME = "廠務工程"
IC_DESIGN_THEME = "IC設計"
TARGET_THEMES = frozenset({AI_THEME, LEO_THEME, FIBERGLASS_THEME, FACILITY_THEME})


@dataclass(frozen=True)
class ThemeStock:
    symbol: str
    name: str
    market: str
    industry: str
    themes: tuple[str, ...]


THEME_STOCKS = (
    ThemeStock("2330", "台積電", "上市", "半導體", (AI_THEME,)),
    ThemeStock("2317", "鴻海", "上市", "電子零組件", (AI_THEME,)),
    ThemeStock("2454", "聯發科", "上市", "半導體", (AI_THEME, IC_DESIGN_THEME)),
    ThemeStock("2308", "台達電", "上市", "電子零組件", (AI_THEME,)),
    ThemeStock("2382", "廣達", "上市", "電腦及週邊", (AI_THEME,)),
    ThemeStock("6669", "緯穎", "上市", "電腦及週邊", (AI_THEME,)),
    ThemeStock("2313", "華通", "上市", "電子零組件", (AI_THEME, PCB_THEME, LEO_THEME)),
    ThemeStock("2314", "台揚", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("2345", "智邦", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("2367", "燿華", "上市", "電子零組件", (PCB_THEME, LEO_THEME)),
    ThemeStock("2383", "台光電", "上市", "電子零組件", (PCB_THEME, LEO_THEME)),
    ThemeStock("2419", "仲琦", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("3025", "星通", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("3062", "建漢", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("3138", "耀登", "上櫃", "通信網路", (LEO_THEME,)),
    ThemeStock("3163", "波若威", "上櫃", "通信網路", (LEO_THEME,)),
    ThemeStock("3363", "上詮", "上櫃", "通信網路", (LEO_THEME,)),
    ThemeStock("3491", "昇達科", "上櫃", "通信網路", (LEO_THEME,)),
    ThemeStock("3596", "智易", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("3704", "合勤控", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("4906", "正文", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("4977", "眾達-KY", "上櫃", "通信網路", (LEO_THEME,)),
    ThemeStock("5388", "中磊", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("6271", "同欣電", "上市", "半導體", (LEO_THEME,)),
    ThemeStock("6285", "啓碁", "上市", "通信網路", (AI_THEME, LEO_THEME)),
    ThemeStock("6442", "光聖", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("6451", "訊芯-KY", "上市", "半導體", (LEO_THEME,)),
    ThemeStock("6546", "正基", "上櫃", "通信網路", (LEO_THEME,)),
    ThemeStock("8011", "台通", "上市", "通信網路", (LEO_THEME,)),
    ThemeStock("8086", "宏捷科", "上櫃", "半導體", (LEO_THEME,)),
    ThemeStock("2368", "金像電", "上市", "電子零組件", (AI_THEME, PCB_THEME)),
    ThemeStock("3037", "欣興", "上市", "電子零組件", (AI_THEME, PCB_THEME, ABF_THEME)),
    ThemeStock("3189", "景碩", "上市", "半導體", (AI_THEME, ABF_THEME)),
    ThemeStock("8046", "南電", "上市", "電子零組件", (AI_THEME, PCB_THEME, ABF_THEME)),
    ThemeStock("2327", "國巨", "上市", "電子零組件", (AI_THEME, PASSIVE_THEME)),
    ThemeStock("2492", "華新科", "上市", "電子零組件", (AI_THEME, PASSIVE_THEME)),
    ThemeStock("3026", "禾伸堂", "上市", "電子零組件", (AI_THEME, PASSIVE_THEME)),
    ThemeStock("2337", "旺宏", "上市", "半導體", (AI_THEME, MEMORY_THEME)),
    ThemeStock("2344", "華邦電", "上市", "半導體", (AI_THEME, MEMORY_THEME)),
    ThemeStock("2408", "南亞科", "上市", "半導體", (AI_THEME, MEMORY_THEME)),
    ThemeStock("8299", "群聯", "上櫃", "半導體", (AI_THEME, MEMORY_THEME)),
    ThemeStock("1802", "台玻", "上市", "玻璃陶瓷", (AI_THEME, FIBERGLASS_THEME)),
    ThemeStock("1815", "富喬", "上櫃", "電子零組件", (AI_THEME, FIBERGLASS_THEME)),
    ThemeStock("5340", "建榮", "上櫃", "電子零組件", (AI_THEME, FIBERGLASS_THEME)),
    ThemeStock("1303", "南亞", "上市", "塑膠", (FIBERGLASS_THEME,)),
    ThemeStock("5475", "德宏", "上櫃", "電子零組件", (FIBERGLASS_THEME,)),
    ThemeStock("2404", "漢唐", "上市", "其他電子", (FACILITY_THEME,)),
    ThemeStock("3402", "漢科", "上櫃", "其他電子", (FACILITY_THEME,)),
    ThemeStock("5536", "聖暉*", "上櫃", "其他電子", (FACILITY_THEME,)),
    ThemeStock("6139", "亞翔", "上市", "其他電子", (FACILITY_THEME,)),
    ThemeStock("6196", "帆宣", "上市", "其他電子", (FACILITY_THEME,)),
    ThemeStock("6613", "朋億*", "上櫃", "其他電子", (FACILITY_THEME,)),
    ThemeStock("6667", "信紘科", "上櫃", "其他電子", (FACILITY_THEME,)),
    ThemeStock("6691", "洋基工程", "上市", "其他電子", (FACILITY_THEME,)),
    ThemeStock("6903", "巨漢", "上櫃", "其他電子", (FACILITY_THEME,)),
    ThemeStock("7703", "銳澤", "上櫃", "其他電子", (FACILITY_THEME,)),
    ThemeStock("2379", "瑞昱", "上市", "半導體", (AI_THEME, IC_DESIGN_THEME)),
    ThemeStock("3034", "聯詠", "上市", "半導體", (AI_THEME, IC_DESIGN_THEME)),
    ThemeStock("3443", "創意", "上市", "半導體", (AI_THEME, IC_DESIGN_THEME)),
    ThemeStock("3661", "世芯-KY", "上市", "半導體", (AI_THEME, IC_DESIGN_THEME)),
    ThemeStock("5269", "祥碩", "上市", "半導體", (AI_THEME, IC_DESIGN_THEME)),
)
THEME_STOCKS_BY_SYMBOL = {stock.symbol: stock for stock in THEME_STOCKS}

ELECTRONIC_INDUSTRIES = frozenset({
    "半導體",
    "電子零組件",
    "電腦及週邊",
    "電腦及週邊設備",
    "光電",
    "通信網路",
    "電子通路",
    "資訊服務",
    "其他電子",
})
ELECTRONIC_ALERT_EXTRAS = (
    ThemeStock("3008", "大立光", "上市", "光電", (AI_THEME,)),
    ThemeStock("5274", "信驊", "上櫃", "半導體", (AI_THEME, IC_DESIGN_THEME)),
    ThemeStock("6488", "環球晶", "上櫃", "半導體", (AI_THEME,)),
    ThemeStock("8069", "元太", "上櫃", "光電", (AI_THEME,)),
)
ELECTRONIC_ALERT_STOCKS = tuple(
    stock
    for stock in (*THEME_STOCKS, *ELECTRONIC_ALERT_EXTRAS)
    if stock.industry in ELECTRONIC_INDUSTRIES
)


def themes_for_symbol(symbol: str) -> tuple[str, ...]:
    stock = THEME_STOCKS_BY_SYMBOL.get(symbol)
    return stock.themes if stock else ()


def is_target_theme_symbol(symbol: str) -> bool:
    return symbol in THEME_STOCKS_BY_SYMBOL


def is_expanded_theme_symbol(symbol: str) -> bool:
    return any(
        theme in {LEO_THEME, FIBERGLASS_THEME, FACILITY_THEME}
        for theme in themes_for_symbol(symbol)
    )
