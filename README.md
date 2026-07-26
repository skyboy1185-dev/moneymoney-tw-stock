# Moneymoney 台股分析

可部署的台股技術分析 MVP，採前後端分離架構：

- `web/`：Next.js 16、React 19、TypeScript、Tailwind CSS、Lightweight Charts
- `backend/`：FastAPI、SQLAlchemy 2、Pydantic、Mock MarketDataProvider
- `postgres`：PostgreSQL 16
- 根目錄 `docker-compose.yml`：一次啟動資料庫、API 與網站

個股分析的日 K、成交量、均線與 MACD 已改用 TWSE／TPEx 官方個股日成交資料，盤中當日 K 棒才以 TWSE MIS 更新；收盤後一律回到交易所正式日成交量。若官方歷史資料無法取得，個股頁會停止並顯示錯誤，不會改用模擬 K 線。尚未完成正式資料串接的產業排行、新聞與選股策略會清楚標示「展示模式」，也不得產生正式交易訊號。

## MVP 功能

- 個股代號或名稱搜尋
- 日 K、成交量、MA5～MA240 與 MACD 單一整合圖表
- MACD 翻紅進場、翻綠出場策略
- 六組手動選股策略與 AI 選股排行
- 自選觀察、持股及轉換流程
- 產業熱點排行、排序與個股跳轉
- 新聞分類、關鍵字搜尋及個股跳轉
- FastAPI 健康檢查、個股、選股、自選、產業與新聞 REST API
- PostgreSQL 自選、持股與 AI 分數歷史 Schema
- SSE 市場快照推送
- 手機、平板與桌面響應式版面
- `/day-trading-bot` AI 當沖多空機器人（Mock Streaming Data）
- 做多／放空／續抱／減碼／賣出／回補／停損訊號
- SSE 每 2 秒推送、斷線重連、事件去重與緊急出場優先
- 模擬持倉、交易風控、瀏覽器通知、聲音、訊號與績效紀錄
- Redis 最新行情快取與 Pub/Sub；未設定 Redis 時安全退回記憶體
- Asia/Taipei 開盤排程、0～10 分鐘暖機、盤中重啟恢復與非交易時段保護
- AI 正式推薦每小時合計最多 5 檔；硬性風控、3 分鐘保留與 5 分替換門檻
- 正式推薦與市場掃描候選分流，候選股票不會誤標為正式買進／放空建議
- 大戶持股增加榜：TDCC 400張以上與千張以上週增排行榜、12週趨勢、AI觀察與LINE設定

## 一鍵啟動

需要 Docker Desktop 與 Docker Compose。

```bash
cp .env.example .env
docker compose up --build
```

啟動後：

- 前端：http://localhost:3000
- FastAPI Swagger：http://localhost:8000/docs
- 健康檢查：http://localhost:8000/api/v1/health
- PostgreSQL：localhost:5432

正式環境請務必更換 `.env` 的 `POSTGRES_PASSWORD`，並限制資料庫連線來源。

## 本機分開啟動

### 1. PostgreSQL

建立資料庫 `moneymoney`，或只啟動 Compose 中的資料庫：

```bash
docker compose up postgres -d
```

### 2. FastAPI

需要 Python 3.12+。

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

FastAPI 啟動時會使用 SQLAlchemy 建立 MVP 所需資料表。正式上線建議再加入 Alembic 管理 migration。

### 3. Next.js

需要 Node.js 20+。

```bash
cd web
npm ci
copy .env.example .env.local
npm run dev
```

`FASTAPI_URL=http://localhost:8000` 由 Next.js 伺服器端讀取，不會把資料庫連線字串或 API Key 暴露至瀏覽器。FastAPI 暫時離線時，個股、產業與新聞 API 會安全降級至 Next.js Mock Provider。

## 建置與驗證

```bash
cd web
npm test
npm run lint
npx tsc --noEmit
npm run build
npm audit
```

後端啟動測試：

```bash
cd backend
pip install -r requirements-dev.txt
pytest
pyright
uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/v1/health
```

預期健康檢查回傳 `status: ok` 與 `database: connected`。

## FastAPI 主要端點

| Method | Endpoint | 用途 |
|---|---|---|
| GET | `/api/v1/health` | API 與資料庫狀態 |
| GET | `/api/v1/stocks/search?q=台積電` | 搜尋股票 |
| GET | `/api/v1/stocks/2330` | 個股、K 線與技術指標 |
| GET | `/api/v1/screener?strategy=macd_entry` | 技術策略選股 |
| GET | `/api/v1/industries/hotspots` | 產業熱點 |
| GET | `/api/v1/news` | 新聞分類與搜尋 |
| GET/POST | `/api/v1/watchlist` | 自選清單 |
| DELETE | `/api/v1/watchlist/{symbol}` | 移除自選 |
| GET | `/api/v1/day-trading/market-regime` | 當沖盤勢與交易環境 |
| GET | `/api/v1/day-trading/signals` | 即時多空訊號 |
| GET | `/api/v1/day-trading/rankings` | 當沖掃描排行榜 |
| GET/POST | `/api/v1/day-trading/positions` | 模擬持倉 |
| PATCH | `/api/v1/day-trading/positions/{id}` | 停損、移動停利與提醒設定 |
| POST | `/api/v1/day-trading/positions/{id}/close` | 模擬減碼、賣出或回補 |
| GET | `/api/v1/day-trading/alerts` | 出場與風險通知 |
| GET | `/api/v1/day-trading/trades` | 模擬交易紀錄 |
| GET/PUT | `/api/v1/day-trading/settings` | 風控與通知設定 |
| GET | `/api/v1/day-trading/stream` | SSE 即時事件 |
| POST | `/api/integrations/line/webhook` | LINE Messaging API Webhook |
| GET | `/api/v1/integrations/line/status` | LINE 連線、群組與推送摘要 |
| GET/PUT | `/api/v1/integrations/line/settings` | LINE 事件通知開關 |
| POST | `/api/v1/integrations/line/test` | 推送 LINE 測試訊息 |
| DELETE | `/api/v1/integrations/line/groups/{id}` | 解除群組綁定 |
| GET | `/api/v1/large-holders/rankings?type=over400` | 400張／千張以上大戶週增排行榜 |
| GET | `/api/v1/large-holders/stocks/{code}/history` | 最近12週大戶持股歷史 |
| POST | `/api/v1/large-holders/sync` | 同步最新一期TDCC官方資料 |
| GET/POST | `/api/v1/large-holders/monitors` | 大戶AI觀察與LINE通知設定 |

自選 API 需帶 `X-User-Id` header。

## 當沖機器人

瀏覽 `http://localhost:3000/day-trading-bot`。第一版只提供訊號與模擬交易，不含券商串接，也不會自動下單。Mock 模式固定顯示「展示模式，非即時行情」。

資料處理順序：

1. 驗證行情時間與完整性
2. 更新 Redis 最新狀態
3. 優先檢查現有模擬持倉的停損與出場
4. 發送緊急出場或回補事件
5. 更新大盤狀態與新進場訊號
6. 透過 SSE 推送並保存重要紀錄到 PostgreSQL

測試情境可從頁面上的 Mock 控制台觸發：做多、放空、多空停損、第一停利、緊急出場、資料延遲與行情中斷。

新增資料表的可重複執行 SQL 位於 `backend/migrations/001_day_trading.sql`、`backend/migrations/002_day_trading_schedule.sql` 與 `backend/migrations/003_line_group_notifications.sql`；FastAPI 啟動時也會由 SQLAlchemy `create_all()` 建立缺少的資料表。

開盤自動流程預設為 08:30 預熱、08:45 載入股票池、08:55 健康檢查、09:00 開盤與暖機、13:20 停止新倉、13:25 部位提醒、13:30 摘要。時間、暖機分鐘、推薦重算頻率、名單替換門檻與最低行情樣本都可在當沖頁的「交易風控與通知設定」保存到 PostgreSQL。非交易日、盤外、暖機、Redis／資料庫／行情異常時不會產生正式進場推薦，但既有持倉仍持續接受出場與停損檢查。

當沖相關環境變數：

```env
REDIS_URL=redis://localhost:6379/0
DAY_TRADING_STREAM_SECONDS=2
MOCK_DATA_ENABLED=true
TWSE_TIMEZONE=Asia/Taipei
TWSE_HOLIDAYS=
```

正式行情尚未串接前，請保持 `MOCK_DATA_ENABLED=true`。

## LINE 群組通知

LINE 整合使用 Messaging API，不使用已停止服務的 LINE Notify。Channel Access Token 與 Channel Secret 只由 FastAPI 後端讀取，不會傳到 Next.js 或瀏覽器。

Railway 的 `moneymoney-tw-stock` 後端服務需設定：

```env
LINE_CHANNEL_ACCESS_TOKEN=
LINE_CHANNEL_SECRET=
LINE_TARGET_GROUP_ID=
LINE_NOTIFICATIONS_ENABLED=true
PUBLIC_WEB_URL=https://moneymoney-tw-stock-production.up.railway.app
```

請將真實 Token 與 Secret 直接貼到 Railway Variables，不要寫入 `.env.example`、程式碼、GitHub Commit、Build Log 或對話訊息。`LINE_TARGET_GROUP_ID` 是選用的單一預設群組；一般建議將官方帳號加入群組後輸入「綁定當沖機器人」，由已驗證的 Webhook 自動保存 `groupId`。

LINE Developers Console 設定：

1. 在 LINE Official Account Manager 為「AI當沖機器人」啟用 Messaging API。
2. 進入對應 Provider 與 Messaging API Channel。
3. 從 Basic settings 複製 Channel Secret 到 Railway 的 `LINE_CHANNEL_SECRET`。
4. 從 Messaging API 分頁發行 Channel Access Token，保存到 `LINE_CHANNEL_ACCESS_TOKEN`。
5. Webhook URL 輸入 `https://moneymoney-tw-stock-production.up.railway.app/api/integrations/line/webhook`。
6. 開啟 `Use webhook`，按 `Verify` 確認回傳成功；建議同時開啟 Webhook redelivery。
7. 開啟 `Allow bot to join group chats`，才能邀請官方帳號進入群組。
8. 若不希望 Messaging API 回覆與官方帳號自動回覆重疊，可在 LINE Official Account Manager 調整 Greeting／Auto-reply。

群組文字指令：

- `綁定當沖機器人`
- `測試當沖通知`
- `解除當沖通知`

正式推薦只推送通過風控的股票，做多與放空每小時合計最多五檔；市場候選不推送。同一 `signalId + action` 每群只通知一次，一般進場同股票三分鐘冷卻。Push API 最多嘗試三次，三次使用相同 `X-Line-Retry-Key` 防止 LINE 接受第一次請求後因網路錯誤造成重複訊息。緊急出場、停損與回補的佇列優先級高於新進場。

本機測試：

```bash
cd backend
pytest tests/test_line_messaging.py
uvicorn app.main:app --reload --port 8000
curl http://127.0.0.1:8000/api/v1/integrations/line/status
```

LINE Platform 無法直接呼叫 `localhost`。需要測試真實 Webhook 時，請使用有合法 HTTPS 憑證的測試網址或安全 Tunnel，將該網址暫時填入 LINE Developers Console；正式測試則在 Railway 部署完成並設定憑證後使用 Console 的 `Verify`，再從群組執行三個文字指令。

官方參考：

- https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/
- https://developers.line.biz/en/docs/messaging-api/group-chats/
- https://developers.line.biz/en/docs/messaging-api/retrying-api-request/

## 資料來源替換

Mock 資料集中於：

- 後端：`backend/app/services/mock_market.py`
- 前端降級：`web/services/stock-service.ts`

未來可實作相同 service 邊界替換為 FinMind、TWSE、TPEx 或其他合法行情來源。API Key 僅能放在後端環境變數，請勿使用 `NEXT_PUBLIC_` 前綴。

## 大戶持股增加榜

資料 Provider 介面位於 `backend/app/services/large_holders.py`。正式 Adapter 使用臺灣集中保管結算所 OpenAPI：

```text
https://openapi.tdcc.com.tw/v1/opendata/1-5
```

同步後會保存原始持股級距、週摘要與週增減到 PostgreSQL。400張以上嚴格加總 TDCC 持股分級 12、13、14、15；千張以上加總所有千張以上級距（目前格式為分級15），不會誤取單一400～600張級距。系統每6小時檢查一次最新官方週資料，資料日期已存在時不重複寫入：

```env
LARGE_HOLDER_AUTO_SYNC_ENABLED=true
LARGE_HOLDER_SYNC_INTERVAL_SECONDS=21600
```

官方大量下載只提供最新一期，歷史週資料由本站 PostgreSQL 每週累積。首次部署尚未累積兩期時，頁面使用可重現的展示 Adapter，並固定標示「展示模式」，不會把模擬排行宣稱為本週官方排行。新一期同步失敗時，資料庫仍保留最後成功資料。

Migration：

```bash
psql "$DATABASE_URL" -f backend/migrations/006_large_holder_rankings.sql
```

FastAPI 啟動時的 SQLAlchemy `create_all()` 也會建立缺少的資料表。正式環境建議先執行 migration，再重新部署後端。

## 免責聲明

本網站資訊與選股結果僅供研究參考，不構成任何投資建議。

## AI選股機器人監控流程

AI選股機器人仍採固定 TypeScript 規則，不呼叫 OpenAI。流程為：

1. 計算大盤多空力道並執行七種策略 Robot。
2. 候選清單保留條件符合分數至少 55 分的前 12 檔。
3. 通過 75 分、策略適配度、官方盤中報價、流動性、價差與硬性風控後，最多選出 5 檔正式精選。
4. 正式精選同步至 FastAPI `ai_stock_monitor`，先進入等待進場，不假設已成交。
5. 後端再次驗證即時報價、買賣價差、成交量、進場區與風險後，形成買進確認才透過「AI選股機器人」專用 LINE Messaging API 發送通知。
6. 使用者在網站輸入實際價格、股數與時間後，才建立 PostgreSQL 持倉。
7. 後端每 60 秒恢復並監控未結束持倉，會先完成全部持倉的停損、全部賣出與減碼檢查，再處理加碼及新買進。
8. 使用者確認全部賣出後才將持倉移到已結束區。
9. 收盤後保存隔夜狀態與每日摘要；下一交易日取得新鮮盤中報價後自動恢復持倉監控。

金融金額、部位與風險計算使用 Python `Decimal` 及 PostgreSQL `NUMERIC`。

### AI 選股專用 API

- `GET/PUT /api/v1/portfolio/settings`
- `GET /api/v1/portfolio/allocation`
- `GET /api/v1/ai-stock-dashboard`
- `GET/POST /api/v1/ai-stock-monitor`
- `POST /api/v1/ai-stock-monitor/{id}/confirm-entry`
- `POST /api/v1/ai-stock-monitor/{id}/continue-monitoring`
- `GET/PATCH /api/v1/ai-stock-positions/{id}`
- `POST /api/v1/ai-stock-positions/{id}/confirm-add-on`
- `POST /api/v1/ai-stock-positions/{id}/decline-add-on`
- `POST /api/v1/ai-stock-positions/{id}/disable-add-on`
- `POST /api/v1/ai-stock-positions/{id}/partial-exit`
- `POST /api/v1/ai-stock-positions/{id}/close`
- `POST /api/v1/ai-stock-positions/{id}/continue-monitoring`
- `GET/PATCH /api/v1/ai-stock-alerts`

資料庫 migration：`backend/migrations/004_ai_stock_monitor.sql` 與 `backend/migrations/005_ai_stock_line_channel.sql`。另可設定 `AI_STOCK_MONITOR_SECONDS=60`。

### AI選股機器人獨立 LINE 官方帳號

AI 選股與當沖使用不同的 LINE Channel。原本 `LINE_*` 環境變數繼續提供「AI當沖機器人」使用；新官方帳號「AI選股機器人」使用：

```env
AI_STOCK_LINE_CHANNEL_ACCESS_TOKEN=
AI_STOCK_LINE_CHANNEL_SECRET=
AI_STOCK_LINE_TARGET_GROUP_ID=
AI_STOCK_LINE_NOTIFICATIONS_ENABLED=true
PUBLIC_WEB_URL=https://moneymoney-tw-stock-production.up.railway.app
```

請將新官方帳號的 Token 與 Secret 直接填入 Railway 後端 Variables，不可填入前端、GitHub 或對話。`AI_STOCK_LINE_TARGET_GROUP_ID` 可以留空，改由群組 Webhook 安全綁定。

新 Channel 的 LINE Developers Console 設定：

1. Webhook URL：`https://moneymoney-tw-stock-production.up.railway.app/api/integrations/ai-stock-line/webhook`
2. 開啟 `Use webhook` 與 `Allow bot to join group chats`。
3. 將「AI選股機器人」官方帳號邀請進 AI 選股群組。
4. 在群組輸入 `綁定AI選股機器人`。
5. 輸入 `測試AI選股通知` 驗證推送。
6. 需要解除時輸入 `解除AI選股通知`。

AI 選股群組、推送紀錄及 Webhook 去重分別保存於 `ai_stock_line_groups`、`ai_stock_line_delivery_logs` 與 `ai_stock_line_webhook_events`，不會與當沖群組或通知紀錄混用。
