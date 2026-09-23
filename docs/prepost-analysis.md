# 盤前盤後分析

系統以 `Asia/Taipei` 為唯一排程時區。盤前於交易日 08:00 先產生隔夜國際走勢摘要，並於 08:15、08:25、08:30、08:35、08:40 分階段更新，08:45 產生今日盤勢統整與最終方向；盤後於 13:30、14:10、15:30、18:00、21:30 持續更新，隔日 05:00 後補充美股收盤階段，06:00 產生最終摘要、驗證預測並歸檔。背景工作每 30 秒檢查一次，訊息用唯一去重鍵避免重複執行，服務重啟後會補跑已到期而未完成的階段。

## 資料表

- `prepost_analysis_days`：交易日、分析狀態、最新摘要、預測與策略績效。
- `prepost_analysis_messages`：永久訊息、來源時間、影響、信心、版本、父訊息及通知狀態。
- `prepost_notification_settings`：每位使用者的盤前、盤後、重大事件及 Mail 偏好。
- `prepost_pending_events`：06:00 至 08:30 或休市日先收集、下個交易日盤前使用的事件。

建表檔為 `backend/migrations/029_prepost_analysis.sql`；應用啟動也會透過 SQLAlchemy 建立缺少的表。

## API

- `GET /api/prepost-analysis/dashboard`
- `GET /api/prepost-analysis/messages`
- `GET /api/prepost-analysis/history`
- `GET /api/prepost-analysis/calendar`
- `GET /api/prepost-analysis/accuracy`
- `GET /api/prepost-analysis/stream`
- `GET /api/prepost-analysis/export`
- `POST /api/prepost-analysis/reanalyze`
- `POST /api/prepost-analysis/events`
- `PATCH /api/prepost-analysis/messages/{message_id}`
- `POST /api/prepost-analysis/messages/{message_id}/mail`
- `GET/PATCH /api/prepost-analysis/settings`

## 資料與 AI 規則

分析器只使用資料庫已有且附有時間的市場、強勢股及產業快照。尚未接入的國際指數、期貨、ADR、匯率、利率、原物料、法人與新聞資料會明確列為「資料尚未取得」，不會推算或產生模擬數值。外部收集器可把已驗證事件送入 `POST /events`；分析器保存來源、判斷影響、建立後續更新，並在判斷改變時留下前後值及原因。

一般與注意訊息只進入訊息流。重要與緊急訊息提供系統彈窗；啟用 Mail 且現有 Gmail 通知環境設定完整時才寄信。事件去重鍵同時防止訊息與 Mail 重複發送。

## 啟動與環境

後端使用專案既有啟動方式；本機可執行 `uvicorn app.main:app`。前端執行 `npm run dev`。無新增必填環境變數。休市日延用 `TWSE_HOLIDAYS`；Mail 延用現有 Gmail OAuth/寄件人與收件人環境設定。若未設定 Mail，系統訊息與彈窗仍正常，寄信 API 會回傳 `configured: false`。

## 驗證

後端測試涵蓋跨日歸屬、週末休市、台灣時間排程邊界及事件去重。前端由 TypeScript、ESLint 與 Vitest 驗證。歷史資料自功能上線後開始累積；系統不會為過去日期捏造或回填市場資料。
