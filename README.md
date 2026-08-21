# Moneymoney 台股分析

可部署的台股技術分析 MVP，採前後端分離架構：

- `web/`：Next.js 16、React 19、TypeScript、Tailwind CSS、Lightweight Charts
- `backend/`：FastAPI、SQLAlchemy 2、Pydantic、Mock MarketDataProvider
- `postgres`：PostgreSQL 16
- 根目錄 `docker-compose.yml`：一次啟動資料庫、API 與網站

個股分析的日 K、成交量、均線與 MACD 已改用 TWSE／TPEx 官方個股日成交資料，盤中當日 K 棒才以 TWSE MIS 更新；收盤後一律回到交易所正式日成交量。AI 選股歷史日 K 優先使用 FinMind `TaiwanStockPrice` 的市場日成交資料，失敗時回退 TWSE／TPEx 月資料，盤中當日 K 棒同樣由 MIS 更新，不再使用合成歷史價格。若正式歷史資料無法取得，相關股票會停止計算，不會改用模擬 K 線。

盤中 MIS 報價採批次輪詢並保存每檔股票「今日最後一筆有效成交」。MIS 的 `z` 欄位暫時空白時會沿用今日已驗證成交，不會退回昨日收盤價；若服務啟動後尚未取得今日成交，該股票會顯示無資料並停止正式訊號。後端輪詢頻率可用 `QUOTE_REFRESH_SECONDS` 調整，預設 1 秒。

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
- AI選股的自適應掃描池涵蓋官方上市／上櫃電子產業與低軌衛星、玻纖布、廠務工程指定題材；AI當沖另共用擴充後的供應鏈題材池，非允許股票即使分數合格也不得升級為正式推薦
- 正式推薦與市場掃描候選分流，候選股票不會誤標為正式買進／放空建議
- 大戶持股變化榜：TDCC 400～600張級距與千張以上的週比率、持股張數增減、12週趨勢、AI觀察與LINE設定

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

`FASTAPI_URL=http://127.0.0.1:8000` 由 Next.js 伺服器端讀取，不會把資料庫連線字串或 API Key 暴露至瀏覽器。

## 本機／Railway 模式切換

所有前端到 FastAPI、LINE Proxy 與掃描 worker 的連線設定集中在 `web/lib/runtime-config.ts`，不需再逐一修改 API 路由。

| 執行環境 | 設定位置 | 必要設定 |
|---|---|---|
| 本機 | `web/.env.local`、`backend/.env` | `APP_RUNTIME_MODE=local`、`FASTAPI_URL=http://127.0.0.1:8000` |
| Docker Compose | `docker-compose.yml` | 已固定為 `local`，FastAPI 使用 `http://backend:8000` |
| Railway | 各服務的 Railway Variables | `APP_RUNTIME_MODE=railway`；前端另設定 Railway 私有 `FASTAPI_URL` |

`APP_RUNTIME_MODE=auto` 會依 `RAILWAY_*` 系統變數自動辨識，但正式環境建議明確設定 `railway`。Railway 模式若漏設 `FASTAPI_URL`，或錯填成 `localhost`，程式會回報設定錯誤而不會錯連前端容器自己。Docker 建置不再寫入 `FASTAPI_URL`，因此同一份映像可由執行時環境變數切換。

啟動後可登入並呼叫 `GET /api/runtime` 驗證；本機應回傳 `mode: local`，Railway 應回傳 `mode: railway`。此端點只顯示模式與連線是否已設定，不會洩漏實際網址或密鑰。

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

新增資料表的可重複執行 SQL 位於 `backend/migrations/001_day_trading.sql`、`backend/migrations/002_day_trading_schedule.sql`、`backend/migrations/003_line_group_notifications.sql` 與 `backend/migrations/004_day_trading_recommendation_history.sql`；FastAPI 啟動時也會由 SQLAlchemy `create_all()` 建立缺少的資料表。

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
LINE_DAILY_TRADE_MESSAGE_LIMIT=200
GMAIL_NOTIFICATIONS_ENABLED=true
GMAIL_SENDER_EMAIL=
GMAIL_APP_PASSWORD=
GMAIL_RECIPIENT_EMAILS=
GMAIL_APPS_SCRIPT_URL=
GMAIL_APPS_SCRIPT_SECRET=
PUBLIC_WEB_URL=https://moneymoney-tw-stock-production.up.railway.app
```

Gmail 使用 `smtp.gmail.com:465`，與 LINE 獨立寄送及去重。設定方式：

1. 寄件 Gmail 帳號先開啟 Google 兩步驟驗證。
2. 在 Google 帳戶建立 16 碼「應用程式密碼」。
3. 將寄件地址填入 `GMAIL_SENDER_EMAIL`，應用程式密碼填入 `GMAIL_APP_PASSWORD`。
4. `GMAIL_RECIPIENT_EMAILS` 可填一個或多個收件地址，多個地址以逗號分隔。
5. 不可填 Gmail 一般登入密碼，也不可把應用程式密碼提交到 Git。

AI 當沖與 AI 選股機器人的正式進場、加減碼、賣出、停損與系統訊息會沿用相同事件內容；即使 LINE 月額度已滿，Gmail 仍會嘗試寄送。每位收件者與事件使用唯一鍵，避免重複寄信。可在當沖頁的通知設定中查看 Gmail 狀態並寄送測試信。

飆股雷達的正式買進（BUY）也會沿用同一組 Gmail 設定寄送，信件包含股票、買進價、股數／張數、預估金額與進場理由；觀察、突破與一般警告不寄信，以免產生過多郵件。

如果部署平台封鎖 SMTP，可使用免費的 Google Apps Script HTTPS 寄信橋樑：

1. 在 [Google Apps Script](https://script.google.com/) 建立新專案，貼上 `backend/scripts/google_apps_script_gmail.gs`。
2. 在「專案設定 → 指令碼屬性」新增 `MAIL_WEBHOOK_SECRET`（自行產生的長密碼）與 `MAIL_ALLOWED_RECIPIENT`（允許的收件地址）。
3. 選「部署 → 新增部署作業 → 網頁應用程式」，執行身分選「我」，存取權選「任何人」，完成 Gmail 權限授權。
4. 將部署產生的 `/exec` 網址填入 `GMAIL_APPS_SCRIPT_URL`，並把同一組長密碼填入 `GMAIL_APPS_SCRIPT_SECRET`。
5. 設定完成後會優先使用 Apps Script HTTPS；`GMAIL_APP_PASSWORD` 可移除。寄件對象仍由 `GMAIL_RECIPIENT_EMAILS` 控制。

## 盤中大小單籌碼

前端「盤中籌碼」分頁及個股走勢下方提供「即時大單買賣超」、
「即時小單買賣超」與估算散戶成交占比。後端 API：

```text
GET /api/v1/stocks/{stockId}/chip-flow/intraday
```

門檻可透過環境變數設定：

```dotenv
CHIP_FLOW_LARGE_ORDER_AMOUNT=2000000
CHIP_FLOW_SMALL_ORDER_AMOUNT=500000
CHIP_FLOW_DYNAMIC_LARGE_ORDER_ENABLED=true
CHIP_FLOW_DYNAMIC_LARGE_ORDER_PERCENTILE=0.99
CHIP_FLOW_DYNAMIC_LARGE_ORDER_MIN_SAMPLES=100
CHIP_FLOW_ALERT_WINDOW_MINUTES=5
CHIP_FLOW_ALERT_MIN_RECENT_NET_LOTS=10
CHIP_FLOW_ALERT_MIN_BUY_SELL_RATIO=1.5
CHIP_FLOW_ALERT_MIN_POSITIVE_STEPS=2
CHIP_FLOW_ALERT_LIFECYCLE_MINUTES=15
CHIP_FLOW_ALERT_SUDDEN_DROP_RATIO=0.35
CHIP_FLOW_ALERT_MIN_MOMENTUM_CHANGE_LOTS=2
CHIP_FLOW_ALERT_MIN_SUDDEN_DROP_LOTS=5
CHIP_FLOW_ELECTRONIC_SCAN_INTERVAL_SECONDS=2
FUGLE_MARKETDATA_API_KEY=
```

大單預設採當日 09:00～13:30 前連續交易整股成交金額的 P99，
並以 `CHIP_FLOW_LARGE_ORDER_AMOUNT` 作為最低門檻；整股樣本少於 100 筆時
回退固定最低門檻。13:30 集合競價會獨立統計但不納入方向，
13:30 後盤後成交也不納入盤中累積。

資料庫保存每分鐘累積快照，唯一鍵為
`trade_date + stock_id + snapshot_time`。正式環境不會使用測試 Mock。
未設定 Fugle 金鑰時，TWSE MIS 因缺少可回補且具唯一成交 ID 的完整逐筆資料，
API 會回傳 `awaiting_provider`，畫面顯示「等待串接逐筆成交行情」，不會產生
推測或隨機數字。設定 Fugle 金鑰後，後端會回補當日整股與盤中零股成交明細，
以 Fugle `serial` 去重，並從成交價、買一、賣一及 Tick Rule 推估成交方向。
頂部「大單動能」跑馬燈會保存每分鐘符合條件的出現次數與 5 分鐘動能軌跡；
啟動後 15 分鐘內持續追蹤。動能連續增加時加強標示，較前次驟減 35%（至少
5 張）或連續減弱時顯示警示。已啟動標的會插入優先掃描佇列，前端每 2 秒
刷新狀態，同時保留一半掃描機會繼續發現其他電子股。
真實金鑰只能放在 Railway Variables 等加密環境變數，且公開展示行情或衍生資訊
前，須先確認資料供應商與交易所授權範圍。

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

正式推薦只推送通過風控的股票，做多與放空每小時合計最多五檔；市場候選不推送。同一 `signalId + action` 每群只通知一次，一般進場同股票三分鐘冷卻。系統直接以 LINE API 回傳的「本月剩餘額度 ÷ 剩餘交易日」作為當日買賣通知額度，不再套用固定每日 10 則；`LINE_DAILY_TRADE_MESSAGE_LIMIT` 只在 LINE 額度 API 暫時無法讀取時作為備援上限。升級 LINE 方案後會自動使用新的月上限。開盤、行情異常與收盤摘要不計入交易通知平均額度。網頁保存今日所有正式訊號，不受 LINE 上限影響；所有正式訊號都會建立模擬持倉並計算盈虧。Push API 最多嘗試三次，三次使用相同 `X-Line-Retry-Key` 防止 LINE 接受第一次請求後因網路錯誤造成重複訊息。緊急出場、停損與回補的佇列優先級高於新進場。

每日啟動通知不再獨立推送，而是附在當天第一則已啟用的正式進場通知中，
同一訊息列出 09:05 啟動、11:00 停止新進場及 13:30 完成當沖部位處理；
13:30 的實際每日收盤摘要仍依 LINE 通知開關決定是否發送。

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

同步後會保存原始持股級距、週摘要與週增減到 PostgreSQL。TDCC 沒有獨立的 400～499 張級距，因此第一榜採官方持股分級 12（400,001～600,000 股，約 400～600 張）；第二榜採分級 15（1,000,001 股以上）。兩榜均列出本期、上期的比率與持股張數增減。系統每6小時檢查一次最新官方週資料，資料日期已存在時不重複寫入：

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
# 飆股雷達

「飆股雷達」沿用既有 TWSE／TPEx 行情、技術指標、法人、大戶與產業分類資料，建立獨立的 100 萬元模擬帳戶。後端交易事件會在同一個資料庫交易內同步更新持倉、現金、成交、每日資產與網站通知；此模組不會呼叫 LINE Messaging API。

主要服務：

- 前端全市場高流動性掃描：`GET /api/rocket-radar/scan`
- 後端儀表板：`GET /api/v1/rocket-radar/dashboard`
- 即時事件輪詢：`GET /api/v1/rocket-radar/events?afterId=0`
- 通知歷史：`GET /api/v1/rocket-radar/notifications`
- 無前視績效驗證：`GET /api/v1/rocket-radar/backtest?period=3m`

本機啟動後，背景掃描器會在台股交易日 08:50～14:10 依 `ROCKET_RADAR_SCAN_INTERVAL_SECONDS` 執行。若未設定 `ROCKET_RADAR_SCANNER_URL`，會從 `ADAPTIVE_ELECTRONIC_SCANNER_URL` 自動推導 `/api/rocket-radar/scan`。

測試：

```powershell
.\.artifacts\python-env\Scripts\python.exe -m pytest backend\tests\test_rocket_radar.py -q
cd web
npm.cmd test -- --run
npm.cmd run build
```

## 本機／遠端資料庫安全切換

切換工具會先把目前使用中的資料庫合併至目標資料庫，全部同步成功後才更新
`backend/.env` 的 `DATABASE_URL`。目標端獨有資料不會被刪除；同一主鍵若兩邊內容
不同，以目前使用中的資料庫為準。切回本機前會在 `backend/data/backups/` 自動建立
SQLite 備份。

切換前先停止 FastAPI，然後在 `backend` 目錄執行：

```powershell
# 只檢查本機與 Railway PostgreSQL 是否都能連線
python scripts/switch_database.py --to remote --dry-run

# 同步本機資料至 Railway PostgreSQL，成功後切到遠端 DB
python scripts/switch_database.py --to remote

# 同步目前的遠端資料至本機 SQLite，成功後切回本機 DB
python scripts/switch_database.py --to local
```

若專案已由 Railway CLI 連結，工具會自動讀取 `Postgres` 服務的
`DATABASE_PUBLIC_URL`，不必把密碼寫入版本庫。也可在 `backend/.env` 手動設定：

```env
LOCAL_DATABASE_URL=sqlite:///./data/moneymoney-backend.db
REMOTE_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@PUBLIC_HOST:PORT/DATABASE
```

請勿直接手動改 `DATABASE_URL`，否則會略過同步流程。切換完成後重新啟動 FastAPI。
