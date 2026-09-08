# 正式強勢股策略重播

`POST /api/v1/strong-stocks/backtests` 的 `mode: FULL` 會檢查資料、建立背景任務並回傳 RUNNING。
缺資料時回傳 DATA_INSUFFICIENT 與實際缺漏日期，不回傳零績效。
前端輪詢原本的單筆任務 API，顯示總損益、報酬率、勝率、最大回撤與交易明細。

## 共用正式規則

`strong_stock_replay.replay` 在獨立記憶體 SQLite 執行正式策略函式：

- `scan_and_persist`：用歷史原始輸入與本次固定的目前參數重新評分。
- `queue_paper_orders`、`fill_pending_orders`：正式部位配置、委託限制、成本與成交。
- `monitor_positions`：同一套加碼、2R 減碼與停損邏輯。
- `snapshot_equity`、`strategy_health`：每日估值與交易風險控制。

回測从空倉與設定初始資金開始；不複製目前實際持倉。通知僅留在隨任務銷毀的記憶體資料庫，不傳送郵件。
當下參數存入 request_json；原始選股快照 ID 與下載價格存入 data_status_json，API 不回傳大型原始價格陣列。
不採用另一套價量策略替代基本面或產業條件。

## 資料與模型限制

目前免費分鐘重播限最近七個日曆日，不含當天。每天須有前一交易日完整盤後原始輸入，且在當天開盤前已觀察到。
不以後來的快照倒填；保存時 market_open 必須為 false，更新與報價時間不可晚於觀察時間。
當日來源缺失不會由現在的資料取代。每天收盤掃描會保留不可覆寫的原始輸入與觀察時間。

Yahoo `1m` 分鐘收盤在該分鐘結束後才用於決策。需有 09:00–13:24 的完整時間欄位，null 價格不補值、不模擬成交。
實測 Yahoo 的歷史分鐘資料可能缺少收盤撮合價，不能將回應附加的今日收盤價當成歷史價格。
依 [證交所交易制度](https://www.twse.com.tw/zh/products/system/trading.html)，收盤前五分鐘為集合競價委託期間。
若取得同日 13:30 撮合價，可供取樣重播；否則不模擬該次撮合成交，日 K 收盤價僅作收盤估值。
持倉缺少收盤價時整個任務資料不足，避免舊價格被當成期末估值。

這是相同買賣規則的分鐘取樣回測，不保證與即時報價取樣成交完全相同。可能漏掉分鐘內碰價，不模擬排隊、逐筆容量及所有公司行動。
期末不強制平倉，股息與持有成本處理沿用正式策略現有程式。勝率以已完成的賣出交易計算。

## 驗證

`python -m pytest -q tests/test_strong_stock_replay.py tests/test_strong_stock_history.py tests/test_strong_stock.py tests/test_strong_stock_backtest.py`

比對直接呼叫正式函式與重播的買進、加碼、減碼、停損、交易淨損益與現金結果；另測目前參數生效、基本面缺失阻擋、未來快照拒絕、null 分鐘不補價及正式帳戶隔離。
