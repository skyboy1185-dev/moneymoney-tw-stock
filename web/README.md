# Moneymoney 台股分析

完整可執行的台股技術分析與準即時選股 MVP。採 Next.js 16、React 19、TypeScript、Tailwind CSS、TradingView Lightweight Charts、SSE 與 SQLite，所有介面使用繁體中文及台股紅漲綠跌配色。

## 已完成

- 個股分析、AI 選股、大盤多空方向與獨立 `/day-trading-bot` 當沖機器人
- 代號／名稱搜尋、Loading、錯誤與空資料狀態
- 日 K、成交量、MA5～MA240、MACD、十字游標、縮放拖曳
- MACD 翻紅進場／翻綠出場同步標記
- KD (9,3,3)、RSI、ADX、日／週／月 K 重採樣
- 六個固定手動策略與後端 `screenStocksByStrategy()`
- 七項加權的 `calculateMarketForce()` 與多指標 `MarketRegimeDetector`
- 七個策略 Robot、`StrategySelector` 與 `AutoScreeningEngine`
- AI 戰情室、九項力道卡、五種市場圖表與自動選股排行榜
- 加權指數使用 TWSE MIS；臺股期貨近月會依臺灣時間自動切換 TAIFEX 官方日盤／夜盤行情，顯示契約月份、交易時段與官方取樣時間
- AI 排行榜可查看分析、加入自選或輸入成本後加入持股
- SQLite 自選觀察、持股與每分鐘 AI 分數歷史；同股票不可重複加入
- 我的自選頁面顯示最新官方價格、加入後漲跌、分數變化、五種狀態與策略失效原因
- 自選可轉為持股，完整保留原始 AI 分數、Robot、三個原因與入選時間
- SSE 準即時推送；盤中 10 秒、選股與盤勢 60 秒
- 臺股收盤後若台指期夜盤仍交易，後端會依官方 30 秒取樣頻率持續推送；所有市場皆休市時才降低更新頻率，且不把舊資料標為最新
- SQLite 事件紀錄、記憶體快取與 API 限流
- 桌面、平板與手機響應式排版
- 個股最新價優先使用 TWSE MIS，失敗時依序回退 TWSE／TPEx OpenAPI 與 Mock
- Mock Provider 介面，可替換完整正式歷史行情與市場方向服務
- Zustand 當沖狀態層、SSE 自動重連、重複事件去重與緊急出場 Modal
- 當沖排行榜、模擬持倉、風控、通知、CSV 與績效紀錄
- 當沖機器人依 Asia/Taipei 時段自動預熱、暖機、啟動、停止新倉與產生摘要
- 每小時正式推薦最多五檔，通過硬性風控後才會從市場候選升級為「AI 正式推薦」
- LINE Messaging API 群組通知管理卡，可查看遮罩群組 ID、推送狀態、測試與解除綁定

個股卡片會標示官方報價來源；較早歷史 K 線、AI 行情及大單／小單仍明確標示「展示模式／模擬資料」。

## 安裝與啟動

需要 Node.js 20.9 以上版本。

```powershell
cd C:\Users\Administrator\IdeaProjects\TWSE\web
Copy-Item .env.example .env.local
npm.cmd install
npm.cmd run dev
```

瀏覽 [http://localhost:3000](http://localhost:3000)。

正式模式：

```powershell
npm.cmd run build
npm.cmd start
```

測試：

```powershell
npm.cmd test
```

## 主要結構

```text
web/
├─ app/api/
│  ├─ stocks/                 # 個股搜尋
│  ├─ manual-screener/        # 六策略選股
│  ├─ ai/                     # AI 市場快照
│  └─ stream/                 # Server-Sent Events
├─ components/
│  ├─ StockAnalysis.tsx
│  ├─ StockChart.tsx
│  ├─ Screener.tsx
│  └─ AiCenter.tsx
├─ services/
│  ├─ market-data/            # MarketDataProvider
│  ├─ market-direction/       # MarketDirectionProvider
│  ├─ manual-strategy-service.ts
│  └─ market-snapshot-service.ts
├─ market/
│  ├─ MarketForceCalculator.ts
│  ├─ MarketRegimeDetector.ts
│  └─ StrategySelector.ts
├─ robots/
│  ├─ BaseRobot.ts
│  └─ index.ts                # 七個 Robot
├─ engines/
│  └─ AutoScreeningEngine.ts
├─ database/
│  ├─ schema.sql
│  └─ event-store.ts
├─ lib/
│  ├─ indicators.ts
│  ├─ technical.ts
│  └─ market-types.ts
└─ data/                      # 執行時建立 SQLite
```

## 資料架構

```text
正式外部行情 API
  → MarketDataProvider / MarketDirectionProvider
  → 後端記憶體快取
  → 技術指標與市場力道
  → 盤勢判斷與策略 Robot
  → AutoScreeningEngine
  → SSE
  → React 前端
```

目前個股最新 OHLC、昨收與成交量由後端優先取得 TWSE MIS，並以 TWSE／TPEx OpenAPI 作收盤備援；歷史技術圖表和 AI 市場資料仍使用固定種子的 Mock Provider。正式串接時只需替換 Provider，不需要讓瀏覽器直接接觸行情 API 或金鑰。SQLite 用於事件與掃描紀錄，資料模型可遷移 PostgreSQL。

## 訊號規則

- MACD 進場：前一根 Histogram `< 0` 且當前 `>= 0`
- MACD 出場：前一根 Histogram `>= 0` 且當前 `< 0`
- KD 低檔金叉：前 K `<= D`、當前 K `> D`，且當前 K 或 D 至少一個 `< 30`

所有指標依時間正序計算，每根 K 棒只使用當時與之前資料，避免前視偏誤。

> 本網站資訊與選股結果僅供研究參考，不構成任何投資建議。
