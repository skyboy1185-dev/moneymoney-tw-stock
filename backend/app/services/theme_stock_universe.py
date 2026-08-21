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
CPO_THEME = "CPO／矽光子"
PACKAGING_TEST_THEME = "半導體封測"
POWER_THEME = "電源／電力"
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

# CPO／矽光子完整供應鏈固定納入盤中大單動能輪巡。這個清單刻意與
# 日常成交量熱門榜分開，避免相關股票成交量未進前十時完全漏掃。
CPO_ALERT_STOCKS = (
    # 上游：III-V 磊晶、雷射光源與晶圓材料
    ThemeStock("3081", "聯亞", "上櫃", "通信網路", (CPO_THEME,)),
    ThemeStock("2455", "全新", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("4971", "IET-KY", "上櫃", "半導體", (CPO_THEME,)),
    ThemeStock("3714", "富采", "上市", "光電", (CPO_THEME,)),
    ThemeStock("3105", "穩懋", "上櫃", "半導體", (CPO_THEME,)),
    ThemeStock("4908", "前鼎", "上櫃", "通信網路", (CPO_THEME,)),
    ThemeStock("6488", "環球晶", "上櫃", "半導體", (CPO_THEME,)),
    ThemeStock("4991", "環宇-KY", "上櫃", "半導體", (CPO_THEME,)),
    # 中游：FAU、光元件、光引擎與高速光收發模組
    ThemeStock("3363", "上詮", "上櫃", "通信網路", (CPO_THEME,)),
    ThemeStock("3163", "波若威", "上櫃", "通信網路", (CPO_THEME,)),
    ThemeStock("4977", "眾達-KY", "上櫃", "通信網路", (CPO_THEME,)),
    ThemeStock("4979", "華星光", "上櫃", "通信網路", (CPO_THEME,)),
    ThemeStock("6442", "光聖", "上市", "通信網路", (CPO_THEME,)),
    ThemeStock("3234", "光環", "上櫃", "通信網路", (CPO_THEME,)),
    ThemeStock("6789", "采鈺", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("3008", "大立光", "上市", "光電", (CPO_THEME,)),
    # 下游：先進封裝、測試與系統
    ThemeStock("3711", "日月光投控", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("3265", "台星科", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("6257", "矽格", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("3450", "聯鈞", "上市", "光電", (CPO_THEME,)),
    ThemeStock("6451", "訊芯-KY", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("2345", "智邦", "上市", "通信網路", (CPO_THEME,)),
    ThemeStock("3380", "明泰", "上市", "通信網路", (CPO_THEME,)),
    ThemeStock("2317", "鴻海", "上市", "其他電子", (CPO_THEME,)),
    # 平台、晶片設計與晶圓製造
    ThemeStock("2330", "台積電", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("2303", "聯電", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("5347", "世界", "上櫃", "半導體", (CPO_THEME,)),
    ThemeStock("2454", "聯發科", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("2379", "瑞昱", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("3443", "創意", "上市", "半導體", (CPO_THEME,)),
    # 檢測分析、量測介面與設備
    ThemeStock("6830", "汎銓", "上市", "其他電子", (CPO_THEME,)),
    ThemeStock("3587", "閎康", "上櫃", "其他電子", (CPO_THEME,)),
    ThemeStock("3289", "宜特", "上櫃", "其他電子", (CPO_THEME,)),
    ThemeStock("6706", "惠特", "上市", "光電", (CPO_THEME,)),
    ThemeStock("6223", "旺矽", "上櫃", "半導體", (CPO_THEME,)),
    ThemeStock("6515", "穎崴", "上市", "半導體", (CPO_THEME,)),
    ThemeStock("2360", "致茂", "上市", "其他電子", (CPO_THEME,)),
)

# 半導體封裝、測試、測試介面與先進封裝設備。
PACKAGING_TEST_ALERT_STOCKS = (
    ThemeStock("3711", "日月光投控", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6239", "力成", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("2449", "京元電子", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("8150", "南茂", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6257", "矽格", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("3265", "台星科", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6147", "頎邦", "上櫃", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("2329", "華泰", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("8110", "華東", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6451", "訊芯-KY", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("3374", "精材", "上櫃", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6515", "穎崴", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6223", "旺矽", "上櫃", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6510", "精測", "上櫃", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6683", "雍智科技", "上櫃", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("3289", "宜特", "上櫃", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("6830", "汎銓", "上市", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("3587", "閎康", "上櫃", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("3131", "弘塑", "上櫃", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("3583", "辛耘", "上市", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("6187", "萬潤", "上櫃", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("6640", "均華", "上櫃", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("3413", "京鼎", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("6196", "帆宣", "上市", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("2360", "致茂", "上市", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("5443", "均豪", "上櫃", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("2467", "志聖", "上市", "電子零組件", (PACKAGING_TEST_THEME,)),
    ThemeStock("8027", "鈦昇", "上櫃", "其他電子", (PACKAGING_TEST_THEME,)),
    ThemeStock("3372", "典範", "上櫃", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("1560", "中砂", "上市", "電機機械", (PACKAGING_TEST_THEME,)),
    ThemeStock("3037", "欣興", "上市", "電子零組件", (PACKAGING_TEST_THEME,)),
    ThemeStock("3189", "景碩", "上市", "半導體", (PACKAGING_TEST_THEME,)),
    ThemeStock("8046", "南電", "上市", "電子零組件", (PACKAGING_TEST_THEME,)),
)

# AI 資料中心供電、UPS／BBU、PMIC、功率半導體、被動元件與電網重電。
POWER_ALERT_STOCKS = (
    ThemeStock("2308", "台達電", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("2301", "光寶科", "上市", "電腦及週邊", (POWER_THEME,)),
    ThemeStock("6412", "群電", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("6282", "康舒", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("3015", "全漢", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("2420", "新巨", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("6409", "旭隼", "上市", "其他電子", (POWER_THEME,)),
    ThemeStock("2457", "飛宏", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("3078", "僑威", "上櫃", "電腦及週邊", (POWER_THEME,)),
    ThemeStock("6203", "海韻電", "上櫃", "電子零組件", (POWER_THEME,)),
    ThemeStock("5309", "系統電", "上櫃", "電子零組件", (POWER_THEME,)),
    ThemeStock("6121", "新普", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("3211", "順達", "上櫃", "電子零組件", (POWER_THEME,)),
    ThemeStock("4931", "新盛力", "上櫃", "電子零組件", (POWER_THEME,)),
    ThemeStock("6781", "AES-KY", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("3323", "加百裕", "上櫃", "電子零組件", (POWER_THEME,)),
    ThemeStock("6558", "興能高", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("5227", "立凱-KY", "上櫃", "電子零組件", (POWER_THEME,)),
    ThemeStock("4721", "美琪瑪", "上櫃", "化學工業", (POWER_THEME,)),
    ThemeStock("6415", "矽力*-KY", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("6138", "茂達", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("8081", "致新", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("6719", "力智", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("3257", "虹冠電", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("3288", "點晶", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("6291", "沛亨", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("6651", "全宇昕", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("6693", "廣閎科", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("6799", "來頡", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("6129", "普誠", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("3588", "通嘉", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("2436", "偉詮電", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("3317", "尼克森", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("6435", "大中", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("8261", "富鼎", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("5299", "杰力", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("3675", "德微", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("2481", "強茂", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("5425", "台半", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("2342", "茂矽", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("3707", "漢磊", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("3016", "嘉晶", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("6182", "合晶", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("2455", "全新", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("6488", "環球晶", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("6525", "捷敏-KY", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("2351", "順德", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("5285", "界霖", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("8255", "朋程", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("2303", "聯電", "上市", "半導體", (POWER_THEME,)),
    ThemeStock("5347", "世界", "上櫃", "半導體", (POWER_THEME,)),
    ThemeStock("2327", "國巨", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("2492", "華新科", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("6173", "信昌電", "上櫃", "電子零組件", (POWER_THEME,)),
    ThemeStock("2375", "凱美", "上市", "電子零組件", (POWER_THEME,)),
    ThemeStock("1519", "華城", "上市", "電機機械", (POWER_THEME,)),
    ThemeStock("1503", "士電", "上市", "電機機械", (POWER_THEME,)),
    ThemeStock("1513", "中興電", "上市", "電機機械", (POWER_THEME,)),
    ThemeStock("1514", "亞力", "上市", "電機機械", (POWER_THEME,)),
    ThemeStock("2371", "大同", "上市", "電機機械", (POWER_THEME,)),
    ThemeStock("1504", "東元", "上市", "電機機械", (POWER_THEME,)),
    ThemeStock("1609", "大亞", "上市", "電器電纜", (POWER_THEME,)),
    ThemeStock("1612", "宏泰", "上市", "電器電纜", (POWER_THEME,)),
    ThemeStock("1608", "華榮", "上市", "電器電纜", (POWER_THEME,)),
    ThemeStock("1618", "合機", "上市", "電器電纜", (POWER_THEME,)),
    ThemeStock("1617", "榮星", "上市", "電器電纜", (POWER_THEME,)),
    ThemeStock("6806", "森崴能源", "上市", "綠能環保", (POWER_THEME,)),
    ThemeStock("6869", "雲豹能源", "上市", "綠能環保", (POWER_THEME,)),
    ThemeStock("1529", "樂事綠能", "上市", "電機機械", (POWER_THEME,)),
    ThemeStock("4588", "玖鼎電力", "上市", "其他電子", (POWER_THEME,)),
)


def _merge_alert_stocks(*groups: tuple[ThemeStock, ...]) -> tuple[ThemeStock, ...]:
    merged: dict[str, ThemeStock] = {}
    for group in groups:
        for stock in group:
            current = merged.get(stock.symbol)
            if current is None:
                merged[stock.symbol] = stock
                continue
            themes = tuple(dict.fromkeys((*current.themes, *stock.themes)))
            merged[stock.symbol] = ThemeStock(
                current.symbol, current.name, current.market, current.industry, themes,
            )
    return tuple(merged.values())


ELECTRONIC_ALERT_STOCKS = tuple(
    stock
    for stock in _merge_alert_stocks(
        THEME_STOCKS,
        ELECTRONIC_ALERT_EXTRAS,
        CPO_ALERT_STOCKS,
        PACKAGING_TEST_ALERT_STOCKS,
        POWER_ALERT_STOCKS,
    )
    if stock.industry in ELECTRONIC_INDUSTRIES
    or any(theme in {CPO_THEME, PACKAGING_TEST_THEME, POWER_THEME} for theme in stock.themes)
)
AI_RELATED_THEME_STOCKS = ELECTRONIC_ALERT_STOCKS
AI_RELATED_THEME_STOCKS_BY_SYMBOL = {
    stock.symbol: stock for stock in AI_RELATED_THEME_STOCKS
}


def themes_for_symbol(symbol: str) -> tuple[str, ...]:
    stock = THEME_STOCKS_BY_SYMBOL.get(symbol)
    return stock.themes if stock else ()


def is_target_theme_symbol(symbol: str) -> bool:
    return symbol in THEME_STOCKS_BY_SYMBOL


def is_ai_related_theme_symbol(symbol: str) -> bool:
    return symbol in AI_RELATED_THEME_STOCKS_BY_SYMBOL


def is_expanded_theme_symbol(symbol: str) -> bool:
    return any(
        theme in {LEO_THEME, FIBERGLASS_THEME, FACILITY_THEME}
        for theme in themes_for_symbol(symbol)
    )
