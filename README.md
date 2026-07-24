# Moneymoney 台股分析

可部署的台股技術分析 MVP，採前後端分離架構：

- `web/`：Next.js 16、React 19、TypeScript、Tailwind CSS、Lightweight Charts
- `backend/`：FastAPI、SQLAlchemy 2、Pydantic、Mock MarketDataProvider
- `postgres`：PostgreSQL 16
- 根目錄 `docker-compose.yml`：一次啟動資料庫、API 與網站

目前 Mock 歷史資料、產業排行與新聞皆會清楚標示「展示模式」。個股最新價格會優先嘗試 TWSE／TPEx 官方公開市場資訊；任何假資料都不會標示為即時行情。

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

自選 API 需帶 `X-User-Id` header。

## 資料來源替換

Mock 資料集中於：

- 後端：`backend/app/services/mock_market.py`
- 前端降級：`web/services/stock-service.ts`

未來可實作相同 service 邊界替換為 FinMind、TWSE、TPEx 或其他合法行情來源。API Key 僅能放在後端環境變數，請勿使用 `NEXT_PUBLIC_` 前綴。

## 免責聲明

本網站資訊與選股結果僅供研究參考，不構成任何投資建議。
