"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Bell, Bot, CircleDollarSign, Clock3, History, OctagonX, Pause, Play, RefreshCw, Settings, ShieldAlert, SlidersHorizontal, Square, WalletCards } from "lucide-react";
import type { Dashboard, Performance, TradingMode } from "@/lib/day-trading-v2-types";
import { dayTradingV2Client } from "@/services/day-trading-v2-client";

type Section = "overview" | "controller" | "optimization" | "robots" | "positions" | "trades" | "backtest" | "performance" | "notifications" | "risk" | "settings";

const sections: Array<[Section, string]> = [
  ["overview", "即時交易總覽"], ["controller", "策略總控"], ["optimization", "策略優化中心"], ["robots", "五台機器人"], ["positions", "即時持倉"],
  ["trades", "交易紀錄"], ["backtest", "回測中心"], ["performance", "績效分析"],
  ["notifications", "通知中心"], ["risk", "風控中心"], ["settings", "系統設定"],
];

function number(value: string | number | null | undefined, digits = 0) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? new Intl.NumberFormat("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(parsed) : "—";
}

function signedMoney(value: string | number) {
  const parsed = Number(value);
  return `${parsed > 0 ? "+" : ""}${number(parsed)}元`;
}

function tone(value: string | number) {
  const parsed = Number(value);
  return parsed > 0 ? "gain" : parsed < 0 ? "loss" : "flat";
}

function dateTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }) : "—";
}

function ModeBadge({ mode }: { mode: TradingMode }) {
  return <span className={`dt2-mode ${mode.toLowerCase()}`}>【{mode === "PAPER" ? "模擬交易" : mode === "LIVE" ? "真實交易" : "歷史回測"}】</span>;
}

function PerfCards({ data }: { data: Performance }) {
  return <div className="dt2-metric-grid compact">
    <article><span>淨損益</span><strong className={tone(data.netPnl)}>{signedMoney(data.netPnl)}</strong></article>
    <article><span>淨報酬率</span><strong className={tone(data.netReturnPct)}>{Number(data.netReturnPct) > 0 ? "+" : ""}{number(data.netReturnPct, 2)}%</strong></article>
    <article><span>交易筆數</span><strong>{data.tradeCount}筆</strong></article>
    <article><span>勝率</span><strong>{data.sampleSufficient ? `${number(data.winRate, 1)}%` : `樣本不足（${data.tradeCount}筆）`}</strong></article>
    <article><span>平均獲利</span><strong className="gain">{signedMoney(data.averageWin)}</strong></article>
    <article><span>平均虧損</span><strong className="loss">{signedMoney(data.averageLoss)}</strong></article>
    <article><span>賺賠比</span><strong>{data.payoffRatio ?? "—"}</strong></article>
    <article><span>獲利因子</span><strong>{data.profitFactor ?? "—"}</strong></article>
  </div>;
}

export function DayTradingV2Page({ userId }: { userId: string }) {
  const [section, setSection] = useState<Section>("overview");
  const [data, setData] = useState<Dashboard | null>(null);
  const [notifications, setNotifications] = useState<{ unread: number; items: Array<{ id: number; title: string; message: string; mode: TradingMode; createdAt: string }> }>({ unread: 0, items: [] });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [backtestResult, setBacktestResult] = useState<Record<string, unknown> | null>(null);
  const [backtestMode, setBacktestMode] = useState("PORTFOLIO");
  const [backtestStrategy, setBacktestStrategy] = useState("ALL");
  const [backtestSource, setBacktestSource] = useState<"AUTO_FUGLE" | "UPLOADED_DATASET">("AUTO_FUGLE");
  const [backtestDatasetId, setBacktestDatasetId] = useState("");
  const [backtestFile, setBacktestFile] = useState<File | null>(null);
  const [backtestSymbols, setBacktestSymbols] = useState("");
  const [backtestJobId, setBacktestJobId] = useState("");
  const [optimizationFile, setOptimizationFile] = useState<File | null>(null);
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const monthStart = `${today.slice(0, 8)}01`;
  const [startDate, setStartDate] = useState(monthStart);
  const [endDate, setEndDate] = useState(today);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [dashboard, noteData] = await Promise.all([dayTradingV2Client.dashboard(userId), dayTradingV2Client.notifications(userId)]);
      setData(dashboard);
      setNotifications(noteData);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "讀取失敗");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { void load(true); }, 10_000);
    return () => window.clearInterval(timer);
  }, [load, userId]);

  useEffect(() => {
    if (!backtestJobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const job = await dayTradingV2Client.backtestJob(userId, backtestJobId);
        if (cancelled) return;
        setBacktestResult(job);
        const status = String(job.status ?? "");
        if (["COMPLETED", "DATA_INSUFFICIENT", "FAILED", "CANCELLED"].includes(status)) {
          setBacktestJobId("");
          setNotice(status === "COMPLETED" ? "回測任務已完成" : "回測任務已停止，請查看原因");
          void load(true);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "讀取回測進度失敗");
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 2_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [backtestJobId, load, userId]);

  const robotById = useMemo(() => new Map(data?.robots.map((robot) => [robot.strategyId, robot.name]) ?? []), [data]);
  const backtestDatasets = data?.optimization.datasets.filter((raw) => String((raw as Record<string, unknown>).qualityStatus ?? "") !== "FAILED") ?? [];

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); await load(true); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "操作失敗"); }
    finally { setBusy(false); }
  }

  async function startBacktest() {
    setBusy(true); setError(""); setNotice(""); setBacktestResult(null);
    try {
      let datasetId = backtestDatasetId;
      if (backtestSource === "UPLOADED_DATASET" && backtestFile) {
        const uploaded = await dayTradingV2Client.uploadOptimizationDataset(userId, backtestFile);
        datasetId = String(uploaded.id ?? "");
        setBacktestDatasetId(datasetId);
      }
      if (backtestSource === "UPLOADED_DATASET" && !datasetId) throw new Error("請選擇既有資料集或上傳CSV／Parquet分鐘資料");
      const symbols = backtestSymbols.split(/[\s,，]+/).map((value) => value.trim()).filter(Boolean);
      const job = await dayTradingV2Client.backtest(userId, {
        backtest_mode: backtestMode, strategy_id: backtestStrategy,
        start_date: startDate, end_date: endDate,
        data_source: backtestSource,
        dataset_id: backtestSource === "UPLOADED_DATASET" ? datasetId : undefined,
        universe_preset: symbols.length ? "CUSTOM" : "TOP_LIQUID_100", symbols,
      });
      setBacktestResult(job);
      const status = String(job.status ?? "");
      if (["QUEUED", "DOWNLOADING", "VALIDATING", "RUNNING"].includes(status)) {
        setBacktestJobId(String(job.id));
        setNotice("回測任務已建立，系統正在準備1分鐘行情");
      } else {
        setNotice(status === "COMPLETED" ? "回測任務已完成" : "回測未執行，請查看資料原因");
      }
      await load(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "回測啟動失敗");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) return <div className="dt2-loading"><span className="spinner" /><p>正在載入超強AI當沖系統…</p></div>;
  if (!data) return <div className="error-banner">{error || "系統資料暫時無法使用"}</div>;

  const config = data.config;
  const settingsFields: Array<[string, string, string]> = [
    ["maxRiskPerTrade", "單筆最大預計虧損", "元"], ["dailyReduceLoss", "單日減半門檻", "元"],
    ["dailyStopLoss", "單日停機門檻", "元"], ["monthlyMaxDrawdown", "單月最大回撤", "元"],
    ["minimumConfidence", "最低信心分數", "分"], ["minimumRiskReward", "最低風險報酬比", "倍"],
    ["maximumSpreadPct", "最大買賣價差", "%"], ["maximumVwapDeviationPct", "最大VWAP偏離", "%"],
    ["watchThreshold", "觀察名單門檻", "分"], ["nearEntryThreshold", "接近進場門檻", "分"],
    ["riskGateThreshold", "下單前風控門檻", "分"], ["scanIntervalSeconds", "進場掃描間隔", "秒"],
    ["heartbeatSeconds", "心跳更新間隔", "秒"], ["heartbeatTimeoutSeconds", "心跳逾時", "秒"],
    ["quoteTimeoutSeconds", "行情逾時", "秒"],
    ["resetTime", "每日初始化", ""], ["universeLoadTime", "載入股票池", ""],
    ["historyLoadTime", "載入昨日行情", ""], ["healthCheckTime", "服務健康檢查", ""],
    ["candidatePoolTime", "建立候選池", ""], ["readyNotificationTime", "盤前通知", ""],
    ["marketOpenTime", "開始掃描", ""], ["openingRangeReadyTime", "開盤區間完成", ""],
    ["summary1000Time", "10點摘要", ""], ["summary1100Time", "11點摘要", ""],
    ["summary1200Time", "12點摘要", ""],
    ["latestEntryTime", "最晚新倉時間", ""], ["forcedCloseTime", "強制平倉時間", ""],
    ["marketCloseTime", "停止盤中策略", ""], ["brokerSyncTime", "收盤後同步", ""],
    ["closeReportTime", "收盤報告", ""],
    ["controllerMinimumScore", "總控最低分數", "分"], ["controllerMinimumRiskReward", "總控最低風報比", "倍"],
    ["regimeUpdateMinutes", "盤勢更新間隔", "分"], ["regimeSwitchCycles", "盤勢切換確認次數", "次"],
    ["regimeRecoveryCycles", "急跌恢復確認次數", "次"], ["regimeMinCoveragePct", "行情最低覆蓋率", "%"],
    ["regimeCrash1mPct", "急跌1分鐘門檻", "%"], ["regimeCrash5mPct", "急跌5分鐘門檻", "%"],
    ["regimeCrashBreadthPct", "急跌市場廣度門檻", "%"], ["regimeCrashSpreadRatio", "異常價差倍數", "倍"],
    ["regimeStrongVwapDeviationPct", "強勢盤VWAP偏離", "%"], ["regimeStrongTrend5mPct", "強勢盤5分鐘趨勢", "%"],
    ["regimeStrongBreadthPct", "強勢盤市場廣度", "%"], ["regimeStrongRelativeVolume", "強勢盤相對量", "倍"],
    ["regimeStrongSectorCount", "強勢產業最低數", "個"], ["regimeWeakVwapDeviationPct", "弱勢盤VWAP偏離", "%"],
    ["regimeWeakTrend5mPct", "弱勢盤5分鐘趨勢", "%"], ["regimeWeakBreadthPct", "弱勢盤市場廣度", "%"],
    ["regimeMildVwapDeviationPct", "溫和多頭VWAP偏離", "%"], ["regimeMildTrend5mPct", "溫和多頭5分鐘趨勢", "%"],
    ["regimeMildBreadthPct", "溫和多頭市場廣度", "%"], ["regimeMildRelativeVolume", "溫和多頭相對量", "倍"],
    ["openingStrategyStart", "開盤突破開始", ""], ["openingStrategyEnd", "開盤突破結束", ""],
    ["vwapStrategyStart", "VWAP回踩開始", ""], ["vwapStrategyEnd", "VWAP回踩結束", ""],
    ["volumeStrategyStart", "爆量突破開始", ""], ["volumeStrategyEnd", "爆量突破結束", ""],
    ["reversalStrategyStart", "假跌破開始", ""], ["reversalStrategyEnd", "假跌破結束", ""],
    ["afternoonStrategyStart", "尾盤續強開始", ""], ["afternoonStrategyEnd", "尾盤續強結束", ""],
    ["healthDiagnosisTime", "每日策略診斷", ""], ["healthMinTrades", "健康判定最低樣本", "筆"],
    ["healthMinRegimeTrades", "盤勢別健康最低樣本", "筆"],
    ["health20DayDrawdownLimit", "20日回撤警戒", "元"], ["healthAlertRiskMultiplier", "警戒風險乘數", "倍"],
  ];

  return <section className="dt2-page">
    <header className="dt2-hero">
      <div><span className="section-kicker">當沖機器人2</span><h1>超強AI當沖系統</h1><p>五台純做多機器人，共用3,000,000元。回測、模擬與真實交易資料完全分開。</p></div>
      <div className="dt2-hero-actions">
        <ModeBadge mode={data.mode} />
        <span className={`dt2-status ${data.systemStatus.toLowerCase()}`}>{data.systemStatus === "NORMAL" ? "系統正常" : data.systemStatus === "REDUCED" ? "風險減半" : "系統停機"}</span>
        <button onClick={() => void load()} disabled={busy}><RefreshCw size={15} />重新整理</button>
      </div>
    </header>

    <div className="dt2-source-bar">
      <span><Activity size={15} />即時行情：{data.marketData.realtime}</span>
      <span className={data.marketData.backtestReady ? "ok" : "warn"}><History size={15} />歷史分鐘資料：{data.marketData.historicalMinute ?? "未設定"}</span>
      <span className="warn"><ShieldAlert size={15} />真實下單：{data.liveTrading.reason}</span>
    </div>

    <div className={`dt2-runtime ${data.runtime.heartbeatStale || data.runtime.quoteStale ? "alarm" : ""}`}>
      <div className="dt2-runtime-head"><Activity size={17} /><strong>背景交易系統</strong><span className={data.runtime.running ? "ok" : "warn"}>{data.runtime.status}｜{data.runtime.phase}</span></div>
      <div className="dt2-runtime-grid">
        <span>執行：<b>{data.runtime.running ? "是" : "否"}</b></span><span>初始化：<b>{data.runtime.initialized ? "完成" : "尚未"}</b></span>
        <span>行情：<b>{data.runtime.receivingQuotes ? "接收中" : "未接收"}</b></span><span>掃描：<b>{data.runtime.scanning ? "執行中" : "未執行"}</b></span>
        <span>允許下單：<b>{data.runtime.orderAllowed ? "是" : "否"}</b></span><span>模式：<b>{data.mode === "PAPER" ? "模擬交易" : data.mode}</b></span>
        <span>最近心跳：<b>{dateTime(data.runtime.heartbeatAt)}</b></span><span>最近行情：<b>{dateTime(data.runtime.lastQuoteAt)}</b></span>
        <span>最近掃描：<b>{dateTime(data.runtime.lastScanAt)}</b></span><span>下一次掃描：<b>{dateTime(data.runtime.nextScanAt)}</b></span>
        <span>下一排程：<b>{data.runtime.nextEventType || "—"} {dateTime(data.runtime.nextEventAt)}</b></span><span>最近錯誤：<b>{data.runtime.latestError || "無"}</b></span>
      </div>
      <div className="dt2-runtime-actions">
        <button disabled={busy} onClick={() => void run(() => dayTradingV2Client.startToday(userId), "今日背景交易系統已啟動")}><Play size={14} />今日啟動</button>
        <button disabled={busy} onClick={() => void run(() => dayTradingV2Client.pause(userId), "已暫停建立新部位")}><Pause size={14} />暫停新交易</button>
        <button disabled={busy} onClick={() => void run(() => dayTradingV2Client.resume(userId), "已恢復盤中掃描與新交易")}><Play size={14} />恢復交易</button>
        <button disabled={busy} onClick={() => void run(() => dayTradingV2Client.stop(userId), "已停止全部策略")}><Square size={14} />停止全部策略</button>
      </div>
    </div>

    <nav className="dt2-tabs">
      {sections.map(([key, label]) => <button key={key} className={section === key ? "active" : ""} onClick={() => setSection(key)}>{label}{key === "notifications" && notifications.unread > 0 ? <b>{notifications.unread}</b> : null}</button>)}
    </nav>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="dt2-notice">{notice}</div>}

    {section === "overview" && <>
      <div className="dt2-metric-grid">
        <article><span>今日已實現損益</span><strong className={tone(data.realizedPnl)}>{signedMoney(data.realizedPnl)}</strong><small>{data.today.winCount}勝 {data.today.lossCount}敗</small></article>
        <article><span>今日未實現損益</span><strong className={tone(data.unrealizedPnl)}>{signedMoney(data.unrealizedPnl)}</strong><small>{data.positions.length}筆持倉</small></article>
        <article><span>今日淨損益</span><strong className={tone(data.netPnl)}>{signedMoney(data.netPnl)}</strong><small>報酬率 {number(data.today.netReturnPct, 2)}%</small></article>
        <article><span>今日勝率</span><strong>{data.today.sampleSufficient ? `${number(data.today.winRate, 1)}%` : "樣本數不足"}</strong><small>{data.today.tradeCount}筆已平倉</small></article>
        <article><span>本月淨損益</span><strong className={tone(data.month.netPnl)}>{signedMoney(data.month.netPnl)}</strong><small>報酬率 {number(data.month.netReturnPct, 2)}%</small></article>
        <article><span>本月勝率</span><strong>{data.month.sampleSufficient ? `${number(data.month.winRate, 1)}%` : "樣本數不足"}</strong><small>{data.month.tradeCount}筆已平倉</small></article>
        <article><span>目前使用資金</span><strong>{number(data.usedCapital)}元</strong><small>上限3,000,000元</small></article>
        <article><span>目前可用資金</span><strong>{number(data.availableCapital)}元</strong><small>含交易成本預留</small></article>
        <article><span>當日剩餘承受虧損</span><strong className={Number(data.remainingDailyRisk) < 12000 ? "warn" : ""}>{number(data.remainingDailyRisk)}元</strong><small>停機門檻24,000元</small></article>
      </div>
      <article className="dt2-panel"><header><Clock3 size={17} /><strong>盤中掃描與最接近進場前10檔</strong><small>已掃描 {data.runtime.scannedStockCount}檔｜候選 {data.runtime.candidateCount}檔｜訊號 {data.runtime.signalCount}｜下單 {data.runtime.orderCount}｜跳過 {data.runtime.skippedCount}</small></header><div className="dt2-table candidates"><table><thead><tr><th>股票</th><th>分數</th><th>分級</th><th>對應機器人</th><th>尚未進場主要原因</th><th>行情時間</th><th>掃描時間</th></tr></thead><tbody>{data.topCandidates.map((candidate) => <tr key={candidate.symbol}><td><b>{candidate.symbol}</b><small>{candidate.stockName}</small></td><td><b>{number(candidate.confidence, 1)}</b></td><td>{candidate.signalLevel === "RISK_GATE" ? "下單前風控" : candidate.signalLevel === "NEAR_ENTRY" ? "接近進場" : candidate.signalLevel === "WATCH" ? "觀察名單" : "一般掃描"}</td><td>{(robotById.get(candidate.strategyId) ?? candidate.strategyId) || "條件比對中"}</td><td>{candidate.primaryReason}</td><td>{dateTime(candidate.quoteAt)}</td><td>{dateTime(candidate.scannedAt)}</td></tr>)}</tbody></table></div>{!data.topCandidates.length && <p className="dt2-empty">系統仍會持續心跳；目前尚無候選股票資料。</p>}</article>
      <div className="dt2-grid two">
        <article className="dt2-panel"><header><Bot size={17} /><strong>五台機器人狀態</strong></header>{data.robots.map((robot) => <div className="dt2-robot-row" key={robot.strategyId}><span className={robot.enabled ? "dot on" : "dot"} /><div><b>{robot.name}</b><small>額度 {number(robot.allocation)}元｜只做多</small></div><em className={tone(robot.today.netPnl)}>{signedMoney(robot.today.netPnl)}</em></div>)}</article>
        <article className="dt2-panel"><header><Bell size={17} /><strong>最新買賣與風控訊息</strong></header>{notifications.items.slice(0, 6).map((item) => <div className="dt2-event" key={item.id}><ModeBadge mode={item.mode} /><div><b>{item.title}</b><small>{item.message}</small></div><time>{dateTime(item.createdAt)}</time></div>)}{!notifications.items.length && <p className="dt2-empty">目前沒有通知</p>}</article>
      </div>
    </>}

    {section === "controller" && <div className="dt2-stack">
      <div className="dt2-grid two">
        <article className={`dt2-panel ${data.controller.dataBlocked ? "danger" : ""}`}><header><Activity size={17} /><strong>目前市場狀態</strong><small>每5分鐘判斷，使用遲滯避免頻繁切換。</small></header><div className="dt2-regime"><strong>{data.controller.marketRegimeLabel}</strong><span>信心度 {number(data.controller.confidence, 1)}%</span><small>{data.controller.reasons.join("｜")}</small><small>下次更新：{dateTime(data.controller.nextUpdateAt)}</small></div></article>
        <article className="dt2-panel"><header><ShieldAlert size={17} /><strong>總控執行狀態</strong></header><dl className="dt2-rules"><div><dt>決策週期</dt><dd>{data.controller.cycleId?.slice(0, 8) ?? "等待掃描"}</dd></div><div><dt>狀態</dt><dd>{data.controller.cycleStatus}</dd></div><div><dt>候選數</dt><dd>{data.controller.candidates.length}</dd></div><div><dt>允許下單</dt><dd>{data.controller.dataBlocked ? "否" : data.runtime.orderAllowed ? "是" : "否"}</dd></div></dl></article>
      </div>
      <article className="dt2-panel"><header><Bot size={17} /><strong>策略啟用、降權與暫停</strong></header><div className="dt2-strategy-state-grid">{data.controller.strategyStates.map((strategy) => <div key={strategy.strategyId} className={`dt2-strategy-state ${strategy.status.toLowerCase()}`}><b>{strategy.name}</b><span>{strategy.status}</span><small>候選 {strategy.candidateCount}｜風險乘數 {strategy.riskMultiplier}</small></div>)}</div></article>
      <article className="dt2-panel"><header><SlidersHorizontal size={17} /><strong>統一候選池與排名</strong><small>每輪最多一筆，最高分仍須通過全部風控。</small></header><div className="dt2-table"><table><thead><tr><th>排名</th><th>股票</th><th>來源策略</th><th>原始分</th><th>最終分</th><th>分數明細</th><th>進場／停損／停利</th><th>風報比</th><th>預計資金</th><th>結果</th></tr></thead><tbody>{data.controller.candidates.map((candidate) => <tr key={candidate.id}><td>{candidate.rank ?? "—"}</td><td><b>{candidate.symbol}</b><small>{candidate.stockName}｜{candidate.sector || "產業未知"}</small></td><td>{robotById.get(candidate.strategyId) ?? candidate.strategyId}<small>v{candidate.strategyVersion}</small></td><td>{number(candidate.rawScore, 1)}</td><td><b>{number(candidate.finalScore, 1)}</b></td><td><small>{Object.entries(candidate.scoreDetails).map(([key, value]) => `${key}:${value}`).join("｜")}</small></td><td>{number(candidate.entryPrice, 2)}／{number(candidate.stopPrice, 2)}／{number(candidate.targetPrice, 2)}</td><td>{number(candidate.riskReward, 2)}</td><td>{number(candidate.plannedCapital)}元</td><td className={candidate.allowed ? "gain" : "loss"}>{candidate.status}<small>{candidate.blockedReasons.join("、") || "通過"}</small></td></tr>)}</tbody></table></div>{!data.controller.candidates.length && <p className="dt2-empty">目前沒有策略候選；系統不會為了產生交易而降低門檻。</p>}</article>
    </div>}

    {section === "optimization" && <div className="dt2-stack">
      <article className="dt2-panel"><header><History size={17} /><strong>合格分鐘資料</strong><small>資料需含時區、OHLCV、產業及同時間市場脈絡；原始檔保存在伺服器持久化磁碟。</small></header><div className="dt2-form"><label>CSV／Parquet<input type="file" accept=".csv,.parquet" onChange={(event) => setOptimizationFile(event.target.files?.[0] ?? null)} /></label><button disabled={busy || !optimizationFile} onClick={() => optimizationFile && void run(() => dayTradingV2Client.uploadOptimizationDataset(userId, optimizationFile), "分鐘資料已匯入並完成品質檢查")}>匯入資料</button></div><div className="dt2-table"><table><thead><tr><th>資料集</th><th>期間</th><th>交易日</th><th>股票</th><th>列數</th><th>品質</th><th>雜湊</th></tr></thead><tbody>{data.optimization.datasets.map((raw, index) => { const dataset = raw as Record<string, unknown>; return <tr key={String(dataset.id ?? index)}><td>{String(dataset.name ?? "—")}</td><td>{String(dataset.startDate ?? "—")}～{String(dataset.endDate ?? "—")}</td><td>{String(dataset.tradingDayCount ?? 0)}</td><td>{String(dataset.symbolCount ?? 0)}</td><td>{number(String(dataset.rowCount ?? 0))}</td><td>{String(dataset.qualityStatus ?? "—")}</td><td><small>{String(dataset.checksum ?? "").slice(0, 12)}</small></td></tr>; })}</tbody></table></div>{!data.optimization.datasets.length && <p className="dt2-data-warning">尚未設定 DTV2_OPTIMIZATION_DATA_DIR 或匯入合格資料時，優化任務會安全停在 DATA_INSUFFICIENT。</p>}</article>
      <article className="dt2-panel"><header><Bot size={17} /><strong>五策略健康狀態</strong><button disabled={busy} onClick={() => void run(() => dayTradingV2Client.diagnoseStrategies(userId), "策略健康診斷已完成")}>立即診斷</button></header><div className="dt2-table"><table><thead><tr><th>策略</th><th>版本</th><th>健康狀態</th><th>最近20筆</th><th>最近50筆</th><th>原因</th><th>資金／風險乘數</th><th>操作</th></tr></thead><tbody>{data.optimization.health.map((health) => { const last20 = (health.metrics.last20 ?? {}) as Record<string, unknown>; const last50 = (health.metrics.last50 ?? {}) as Record<string, unknown>; return <tr key={health.strategyId}><td><b>{health.name}</b><small>{health.strategyId}</small></td><td>v{health.version}</td><td className={health.status === "ALERT" || health.status === "PAUSED" ? "loss" : health.status === "NORMAL" ? "gain" : "warn"}>{health.status}</td><td>{String(last20.netPnl ?? "—")}元<small>{String(last20.tradeCount ?? 0)}筆｜PF {String(last20.profitFactor ?? "—")}</small></td><td>{String(last50.netPnl ?? "—")}元<small>{String(last50.tradeCount ?? 0)}筆｜回撤 {String(last50.maxDrawdown ?? "—")}</small></td><td>{health.reasons.join("、") || "正常"}</td><td>{health.capitalMultiplier}／{health.riskMultiplier}</td><td><button disabled={busy} onClick={() => void run(() => dayTradingV2Client.createOptimizationJob(userId, health.strategyId), "優化任務已建立；缺少合格資料時會保持資料不足")}>啟動離線優化</button></td></tr>; })}</tbody></table></div></article>
      <article className="dt2-panel"><header><History size={17} /><strong>離線 Walk-Forward 優化進度</strong><small>按時間順序切割訓練、驗證與樣本外資料。</small></header><div className="dt2-table"><table><thead><tr><th>策略</th><th>候選版本</th><th>狀態</th><th>進度</th><th>結果／錯誤</th><th>完整比較</th></tr></thead><tbody>{data.optimization.jobs.map((raw, index) => { const job = raw as Record<string, unknown>; const result = (job.result ?? {}) as Record<string, unknown>; return <tr key={String(job.id ?? index)}><td>{String(job.strategyId ?? "—")}</td><td>{String(job.candidateVersion ?? "—")}</td><td>{String(job.status ?? "—")}</td><td>{String(job.progressPct ?? 0)}%</td><td><small>{String(job.error || result.reason || "等待執行")}</small></td><td><details><summary>查看報告</summary><pre className="dt2-result">{JSON.stringify(result, null, 2)}</pre></details></td></tr>; })}</tbody></table></div>{!data.optimization.jobs.length && <p className="dt2-empty">尚無優化任務</p>}</article>
      <article className="dt2-panel"><header><Bot size={17} /><strong>Champion／Challenger 平行模擬</strong><small>兩者使用同一批即時行情；Challenger 沒有券商下單程式路徑。</small></header><div className="dt2-table"><table><thead><tr><th>策略</th><th>正式版</th><th>挑戰版</th><th>狀態</th><th>完整交易日</th><th>模擬筆數</th><th>錯誤</th><th>正式版績效</th><th>挑戰版績效</th></tr></thead><tbody>{data.optimization.challengers.map((raw, index) => { const run = raw as Record<string, unknown>; const champion = (run.championMetrics ?? {}) as Record<string, unknown>; const challenger = (run.challengerMetrics ?? {}) as Record<string, unknown>; return <tr key={String(run.id ?? index)}><td>{robotById.get(String(run.strategyId ?? "")) ?? String(run.strategyId ?? "—")}</td><td>v{String(run.championVersion ?? "—")}</td><td>v{String(run.challengerVersion ?? "—")}</td><td>{String(run.status ?? "—")}</td><td>{String(run.fullTradingDays ?? 0)}／10</td><td>{String(run.tradeCount ?? 0)}／30</td><td>{String(run.errorCount ?? 0)}</td><td>淨損益 {String(champion.netPnl ?? "—")}<small>回撤 {String(champion.maxDrawdown ?? "—")}</small></td><td>淨損益 {String(challenger.netPnl ?? "—")}<small>回撤 {String(challenger.maxDrawdown ?? "—")}</small></td></tr>; })}</tbody></table></div>{!data.optimization.challengers.length && <p className="dt2-empty">目前沒有通過樣本外驗證的 Challenger</p>}</article>
      <article className="dt2-panel"><header><ShieldAlert size={17} /><strong>版本批准與回復</strong><small>新版本只能在下一交易日08:30生效。</small></header><div className="dt2-table"><table><thead><tr><th>策略</th><th>版本</th><th>角色</th><th>狀態</th><th>生效日</th><th>操作</th></tr></thead><tbody>{data.optimization.deployments.map((raw, index) => { const deployment = raw as Record<string, unknown>; const strategyId = String(deployment.strategyId ?? ""); const version = String(deployment.version ?? ""); const status = String(deployment.status ?? ""); return <tr key={String(deployment.id ?? index)}><td>{robotById.get(strategyId) ?? strategyId}</td><td>v{version}</td><td>{String(deployment.role ?? "—")}</td><td>{status}</td><td>{String(deployment.effectiveDate ?? "—")}</td><td><div className="dt2-inline-actions">{status === "WAITING_APPROVAL" && <><button disabled={busy} onClick={() => { const code = window.prompt(`輸入伺服器策略批准碼，確認升級至 ${version}`); if (code) void run(() => dayTradingV2Client.approveVersion(userId, strategyId, version, code), `${version}已排程於下一交易日生效`); }}>批准</button><button className="danger-soft" disabled={busy} onClick={() => { const reason = window.prompt("拒絕原因") ?? "使用者拒絕"; void run(() => dayTradingV2Client.rejectVersion(userId, strategyId, version, reason), `${version}已拒絕`); }}>拒絕</button></>}{status === "ACTIVE" && version !== "2.0.0" && <button className="danger-soft" disabled={busy} onClick={() => { const reason = window.prompt("回復上一版的原因"); if (reason) void run(() => dayTradingV2Client.rollbackVersion(userId, strategyId, version, reason), "已停止新版本，舊版將於下一交易日恢復"); }}>回復上一版</button>}</div></td></tr>; })}</tbody></table></div></article>
    </div>}

    {section === "robots" && <article className="dt2-panel"><header><Bot size={17} /><strong>五台機器人績效比較</strong><small>高勝率不代表正報酬，請同時比較獲利因子與回撤。</small></header><div className="dt2-table"><table><thead><tr><th>機器人</th><th>狀態</th><th>額度</th><th>今日損益</th><th>本月損益</th><th>累積損益</th><th>累積勝率</th><th>筆數</th><th>平均獲利</th><th>平均虧損</th><th>獲利因子</th><th>使用資金</th><th>操作</th></tr></thead><tbody>{data.robots.map((robot) => <tr key={robot.strategyId}><td><b>{robot.name}</b><small>{robot.strategyId}</small></td><td>{robot.status}</td><td>{number(robot.allocation)}</td><td className={tone(robot.today.netPnl)}>{signedMoney(robot.today.netPnl)}</td><td className={tone(robot.month.netPnl)}>{signedMoney(robot.month.netPnl)}</td><td className={tone(robot.all.netPnl)}>{signedMoney(robot.all.netPnl)}</td><td>{robot.all.sampleSufficient ? `${number(robot.all.winRate, 1)}%` : `樣本不足（${robot.all.tradeCount}）`}</td><td>{robot.all.tradeCount}</td><td>{number(robot.all.averageWin)}</td><td>{number(robot.all.averageLoss)}</td><td>{robot.all.profitFactor ?? "—"}</td><td>{number(robot.usedCapital)}</td><td><button className={robot.enabled ? "danger-soft" : "primary-soft"} disabled={busy} onClick={() => void run(() => dayTradingV2Client.updateRobot(userId, robot.strategyId, !robot.enabled), robot.enabled ? `已停用${robot.name}` : `已啟用${robot.name}`)}>{robot.enabled ? "停用" : "啟用"}</button></td></tr>)}</tbody></table></div></article>}

    {section === "positions" && <article className="dt2-panel"><header><WalletCards size={17} /><strong>即時持倉</strong><small>未平倉部位不納入勝率。</small></header><div className="dt2-table"><table><thead><tr><th>模式</th><th>股票</th><th>機器人</th><th>買進成交時間</th><th>成交均價</th><th>現價</th><th>股數</th><th>使用資金</th><th>停損</th><th>第一停利</th><th>移動停利</th><th>未實現損益</th><th>信心分數</th><th>進場原因</th><th>操作</th></tr></thead><tbody>{data.positions.map((position) => <tr key={position.id}><td><ModeBadge mode={position.mode} /></td><td><b>{position.symbol}</b><small>{position.stockName}</small></td><td>{robotById.get(position.strategyId) ?? position.strategyId}</td><td>{dateTime(position.entryTime)}</td><td>{number(position.entryPrice, 2)}</td><td>{number(position.currentPrice, 2)}</td><td>{number(position.quantity)}股</td><td>{number(position.usedCapital)}元</td><td>{number(position.stopPrice, 2)}</td><td>{number(position.firstTargetPrice, 2)}</td><td>{number(position.trailingStopPrice, 2)}</td><td className={tone(position.unrealizedGrossPnl)}>{signedMoney(position.unrealizedGrossPnl)}</td><td>{number(position.confidence, 1)}</td><td>{position.entryReasons.join("、") || "—"}</td><td><button className="danger-soft" disabled={busy} onClick={() => { if (window.confirm(`確認以目前顯示價格平倉 ${position.symbol}？`)) void run(() => dayTradingV2Client.closePosition(userId, position.id, position.currentPrice), `${position.symbol}已送出模擬平倉`); }}>平倉</button></td></tr>)}</tbody></table></div>{!data.positions.length && <p className="dt2-empty">目前沒有未平倉部位</p>}</article>}

    {section === "trades" && <article className="dt2-panel"><header><History size={17} /><strong>完成交易紀錄</strong></header><div className="dt2-table"><table><thead><tr><th>交易編號</th><th>模式</th><th>股票</th><th>機器人/版本</th><th>買進成交</th><th>買價/股數</th><th>賣出成交</th><th>賣價</th><th>毛損益</th><th>完整成本</th><th>淨損益</th><th>報酬率</th><th>進場原因</th><th>出場原因</th></tr></thead><tbody>{data.recentTrades.map((trade) => { const costs = Number(trade.buyFee) + Number(trade.sellFee) + Number(trade.transactionTax) + Number(trade.slippage) + Number(trade.otherCost); return <tr key={trade.id}><td><small>{trade.id.slice(0, 8)}</small></td><td><ModeBadge mode={trade.mode} /></td><td><b>{trade.symbol}</b><small>{trade.stockName}</small></td><td>{robotById.get(trade.strategyId) ?? trade.strategyId}<small>v{trade.strategyVersion}</small></td><td>{dateTime(trade.entryFillTime)}</td><td>{number(trade.entryPrice, 2)} / {number(trade.quantity)}股</td><td>{dateTime(trade.exitFillTime)}</td><td>{number(trade.exitPrice, 2)}</td><td className={tone(trade.grossPnl)}>{signedMoney(trade.grossPnl)}</td><td>{number(costs)}元</td><td className={tone(trade.netPnl)}>{signedMoney(trade.netPnl)}</td><td className={tone(trade.netReturnPct)}>{number(trade.netReturnPct, 2)}%</td><td>{trade.entryReason}</td><td>{trade.exitReason}</td></tr>; })}</tbody></table></div>{!data.recentTrades.length && <p className="dt2-empty">目前沒有已平倉交易</p>}</article>}

    {section === "backtest" && <div className="dt2-grid two">
      <article className="dt2-panel">
        <header><SlidersHorizontal size={17} /><strong>策略回測中心</strong><small>所有選項與分鐘資料都在這裡完成。</small></header>
        <div className="dt2-form">
          <label>資料來源<select value={backtestSource} onChange={(event) => setBacktestSource(event.target.value as "AUTO_FUGLE" | "UPLOADED_DATASET")}><option value="AUTO_FUGLE">系統自動取得（Fugle 1分鐘）</option><option value="UPLOADED_DATASET">上傳或選擇CSV／Parquet</option></select></label>
          {backtestSource === "UPLOADED_DATASET" && <>
            <label>既有分鐘資料集<select value={backtestDatasetId} onChange={(event) => setBacktestDatasetId(event.target.value)}><option value="">改為上傳新檔案</option>{backtestDatasets.map((raw, index) => { const dataset = raw as Record<string, unknown>; return <option key={String(dataset.id ?? index)} value={String(dataset.id ?? "")}>{String(dataset.name ?? "分鐘資料")}｜{String(dataset.startDate ?? "—")}～{String(dataset.endDate ?? "—")}</option>; })}</select></label>
            <label>上傳新資料<input type="file" accept=".csv,.parquet" onChange={(event) => setBacktestFile(event.target.files?.[0] ?? null)} /></label>
          </>}
          {backtestSource === "AUTO_FUGLE" && <label>股票池（選填）<textarea rows={3} value={backtestSymbols} onChange={(event) => setBacktestSymbols(event.target.value)} placeholder="留白使用目前高流動性100檔；或輸入代碼，例如 2330, 2317" /><small>建立任務時會凍結名單，最多200檔。</small></label>}
          <label>回測模式<select value={backtestMode} onChange={(event) => setBacktestMode(event.target.value)}><option value="PORTFOLIO">模式B：五台共用300萬元</option><option value="INDIVIDUAL">模式A：個別機器人各300萬元</option></select></label>
          <label>策略<select value={backtestStrategy} onChange={(event) => setBacktestStrategy(event.target.value)}><option value="ALL">五台全部</option>{data.robots.map((robot) => <option key={robot.strategyId} value={robot.strategyId}>{robot.name}</option>)}</select></label>
          <label>開始日期<input type="date" min={backtestSource === "AUTO_FUGLE" ? "2023-05-23" : undefined} value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
          <label>結束日期<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
          <button disabled={busy || Boolean(backtestJobId) || (backtestSource === "UPLOADED_DATASET" && !backtestDatasetId && !backtestFile)} onClick={() => void startBacktest()}>{backtestJobId ? "回測執行中…" : "執行回測"}</button>
          <p className="dt2-data-warning">系統會自動準備1分鐘行情，也可在此上傳CSV／Parquet。日K不會用於當沖回測。自動模式為固定股票池研究回測，結果會標示名單快照與資料品質。</p>
        </div>
      </article>
      <article className="dt2-panel">
        <header><History size={17} /><strong>回測進度與結果</strong></header>
        {backtestResult ? <>
          <dl className="dt2-rules"><div><dt>狀態</dt><dd>{String(backtestResult.status ?? "—")}</dd></div><div><dt>進度</dt><dd>{number(String(backtestResult.progressPct ?? 0), 1)}%</dd></div><div><dt>資料來源</dt><dd>{String(backtestResult.dataSource ?? "—")}</dd></div><div><dt>資料精度</dt><dd>{String(backtestResult.dataPrecision ?? "—")}</dd></div></dl>
          <p className="dt2-data-warning">{String(((backtestResult.progress ?? {}) as Record<string, unknown>).message ?? backtestResult.error ?? "任務資料已更新")}</p>
          <pre className="dt2-result">{JSON.stringify(backtestResult.result ?? backtestResult, null, 2)}</pre>
        </> : <div className="dt2-data-warning"><ShieldAlert size={22} /><b>請設定日期與模式後執行</b><p>完成後會顯示實際分鐘資料期間、股票池、品質、交易明細與扣除成本後績效。</p></div>}
      </article>
    </div>}

    {section === "performance" && <div className="dt2-stack"><article className="dt2-panel"><header><CircleDollarSign size={17} /><strong>今日績效</strong></header><PerfCards data={data.today} /></article><article className="dt2-panel"><header><ShieldAlert size={17} /><strong>今日未進場原因統計</strong></header><div className="dt2-skip-list">{data.skipReasons.map((item) => <div key={item.reason}><span>{item.reason}</span><b>{item.count}次</b></div>)}</div>{!data.skipReasons.length && <p className="dt2-empty">目前沒有跳過訊號</p>}</article><article className="dt2-panel"><header><CircleDollarSign size={17} /><strong>本月績效</strong></header><PerfCards data={data.month} /></article><article className="dt2-panel"><header><CircleDollarSign size={17} /><strong>全期間績效</strong></header><PerfCards data={data.all} /></article></div>}

    {section === "notifications" && <article className="dt2-panel"><header><Bell size={17} /><strong>系統通知中心</strong><small>唯一事件ID防止相同通知重複送出。</small></header>{notifications.items.map((item) => <div className="dt2-event full" key={item.id}><ModeBadge mode={item.mode} /><div><b>{item.title}</b><small>{item.message}</small></div><time>{dateTime(item.createdAt)}</time></div>)}{!notifications.items.length && <p className="dt2-empty">目前沒有通知</p>}</article>}

    {section === "risk" && <div className="dt2-grid two"><article className="dt2-panel"><header><ShieldAlert size={17} /><strong>共用資金與風控</strong></header><dl className="dt2-rules"><div><dt>總資金</dt><dd>3,000,000元</dd></div><div><dt>單筆最大風險</dt><dd>{number(config.maxRiskPerTrade as string)}元</dd></div><div><dt>單日減半 / 停機</dt><dd>{number(config.dailyReduceLoss as string)} / {number(config.dailyStopLoss as string)}元</dd></div><div><dt>同時持倉</dt><dd>最多{config.maxOpenPositions as number}筆</dd></div><div><dt>同產業持倉</dt><dd>最多{config.maxSectorPositions as number}檔</dd></div><div><dt>新倉截止</dt><dd>{String(config.latestEntryTime)}</dd></div><div><dt>強制平倉</dt><dd>{String(config.forcedCloseTime)}</dd></div><div><dt>方向</dt><dd>只允許做多</dd></div></dl></article><article className="dt2-panel danger"><header><OctagonX size={17} /><strong>緊急控制</strong></header><p>停機會立即阻止所有後續新委託。取消未成交委託與持倉平倉是不同操作。</p><div className="dt2-danger-actions"><button disabled={busy} onClick={() => void run(() => dayTradingV2Client.emergencyStop(userId), "全系統已停止新委託")}>全系統停機</button><button disabled={busy} onClick={() => void run(() => dayTradingV2Client.cancelAll(userId), "已取消所有未成交委託")}>取消所有未成交委託</button><button disabled={busy || !data.positions.length} onClick={() => { if (window.confirm(`確認以畫面現價平倉全部${data.positions.length}筆模擬部位？`)) void run(() => Promise.all(data.positions.map((position) => dayTradingV2Client.closePosition(userId, position.id, position.currentPrice, "緊急全部平倉"))), "全部模擬部位已平倉"); }}>一鍵全部平倉</button></div></article></div>}

    {section === "settings" && <div className="dt2-grid two"><article className="dt2-panel"><header><Settings size={17} /><strong>交易模式</strong></header><div className="dt2-mode-select"><button className={data.mode === "PAPER" ? "active" : ""} onClick={() => void run(() => dayTradingV2Client.saveSettings(userId, "PAPER", config), "已切換為模擬交易")}>【模擬交易】</button><button className={data.mode === "BACKTEST" ? "active" : ""} onClick={() => void run(() => dayTradingV2Client.saveSettings(userId, "BACKTEST", config), "已切換為歷史回測")}>【歷史回測】</button><button disabled title={data.liveTrading.reason}>【真實交易】鎖定</button></div><label className="dt2-check"><input type="checkbox" checked={Boolean(config.autoStart)} onChange={(event) => setData((current) => current ? { ...current, config: { ...current.config, autoStart: event.target.checked } } : current)} />交易日自動啟動模擬交易</label><div className="dt2-check-group">{([["emailReady", "08:55 Email"], ["emailOpeningRange", "09:15 Email"], ["emailHourlySummary", "整點摘要 Email"], ["emailCloseReport", "13:40 Email"]] as const).map(([key, label]) => <label className="dt2-check" key={key}><input type="checkbox" checked={Boolean(config[key])} onChange={(event) => setData((current) => current ? { ...current, config: { ...current.config, [key]: event.target.checked } } : current)} />{label}</label>)}</div><p className="dt2-data-warning">預設只啟動模擬交易。真實交易必須先接上券商API、同步持倉與未成交委託，並完成二次確認。</p></article><article className="dt2-panel"><header><SlidersHorizontal size={17} /><strong>可調整參數</strong></header><div className="dt2-settings-grid">{settingsFields.map(([key, label, unit]) => <label key={key}>{label}<span><input value={String(config[key])} onChange={(event) => setData((current) => current ? { ...current, config: { ...current.config, [key]: event.target.value } } : current)} />{unit}</span></label>)}</div><button className="dt2-save" disabled={busy} onClick={() => void run(() => dayTradingV2Client.saveSettings(userId, data.mode, config), "設定已儲存並寫入稽核紀錄")}>儲存設定</button></article></div>}
  </section>;
}
