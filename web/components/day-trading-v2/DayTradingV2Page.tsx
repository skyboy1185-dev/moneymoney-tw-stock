"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Bell, Bot, CircleDollarSign, History, OctagonX, RefreshCw, Settings, ShieldAlert, SlidersHorizontal, WalletCards } from "lucide-react";
import type { Dashboard, Performance, TradingMode } from "@/lib/day-trading-v2-types";
import { dayTradingV2Client } from "@/services/day-trading-v2-client";

type Section = "overview" | "robots" | "positions" | "trades" | "backtest" | "performance" | "notifications" | "risk" | "settings";

const sections: Array<[Section, string]> = [
  ["overview", "即時交易總覽"], ["robots", "五台機器人"], ["positions", "即時持倉"],
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
  const today = new Date().toISOString().slice(0, 10);
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
    const timer = window.setInterval(() => {
      void dayTradingV2Client.scanNow(userId).then(() => load(true)).catch(() => load(true));
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [load, userId]);

  const robotById = useMemo(() => new Map(data?.robots.map((robot) => [robot.strategyId, robot.name]) ?? []), [data]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); await load(true); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "操作失敗"); }
    finally { setBusy(false); }
  }

  if (loading && !data) return <div className="dt2-loading"><span className="spinner" /><p>正在載入超強AI當沖系統…</p></div>;
  if (!data) return <div className="error-banner">{error || "系統資料暫時無法使用"}</div>;

  const config = data.config;
  const settingsFields: Array<[string, string, string]> = [
    ["maxRiskPerTrade", "單筆最大預計虧損", "元"], ["dailyReduceLoss", "單日減半門檻", "元"],
    ["dailyStopLoss", "單日停機門檻", "元"], ["monthlyMaxDrawdown", "單月最大回撤", "元"],
    ["minimumConfidence", "最低信心分數", "分"], ["minimumRiskReward", "最低風險報酬比", "倍"],
    ["maximumSpreadPct", "最大買賣價差", "%"], ["maximumVwapDeviationPct", "最大VWAP偏離", "%"],
    ["latestEntryTime", "最晚新倉時間", ""], ["forcedCloseTime", "強制平倉時間", ""],
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
      <div className="dt2-grid two">
        <article className="dt2-panel"><header><Bot size={17} /><strong>五台機器人狀態</strong></header>{data.robots.map((robot) => <div className="dt2-robot-row" key={robot.strategyId}><span className={robot.enabled ? "dot on" : "dot"} /><div><b>{robot.name}</b><small>額度 {number(robot.allocation)}元｜只做多</small></div><em className={tone(robot.today.netPnl)}>{signedMoney(robot.today.netPnl)}</em></div>)}</article>
        <article className="dt2-panel"><header><Bell size={17} /><strong>最新買賣與風控訊息</strong></header>{notifications.items.slice(0, 6).map((item) => <div className="dt2-event" key={item.id}><ModeBadge mode={item.mode} /><div><b>{item.title}</b><small>{item.message}</small></div><time>{dateTime(item.createdAt)}</time></div>)}{!notifications.items.length && <p className="dt2-empty">目前沒有通知</p>}</article>
      </div>
    </>}

    {section === "robots" && <article className="dt2-panel"><header><Bot size={17} /><strong>五台機器人績效比較</strong><small>高勝率不代表正報酬，請同時比較獲利因子與回撤。</small></header><div className="dt2-table"><table><thead><tr><th>機器人</th><th>狀態</th><th>額度</th><th>今日損益</th><th>本月損益</th><th>累積損益</th><th>累積勝率</th><th>筆數</th><th>平均獲利</th><th>平均虧損</th><th>獲利因子</th><th>使用資金</th><th>操作</th></tr></thead><tbody>{data.robots.map((robot) => <tr key={robot.strategyId}><td><b>{robot.name}</b><small>{robot.strategyId}</small></td><td>{robot.status}</td><td>{number(robot.allocation)}</td><td className={tone(robot.today.netPnl)}>{signedMoney(robot.today.netPnl)}</td><td className={tone(robot.month.netPnl)}>{signedMoney(robot.month.netPnl)}</td><td className={tone(robot.all.netPnl)}>{signedMoney(robot.all.netPnl)}</td><td>{robot.all.sampleSufficient ? `${number(robot.all.winRate, 1)}%` : `樣本不足（${robot.all.tradeCount}）`}</td><td>{robot.all.tradeCount}</td><td>{number(robot.all.averageWin)}</td><td>{number(robot.all.averageLoss)}</td><td>{robot.all.profitFactor ?? "—"}</td><td>{number(robot.usedCapital)}</td><td><button className={robot.enabled ? "danger-soft" : "primary-soft"} disabled={busy} onClick={() => void run(() => dayTradingV2Client.updateRobot(userId, robot.strategyId, !robot.enabled), robot.enabled ? `已停用${robot.name}` : `已啟用${robot.name}`)}>{robot.enabled ? "停用" : "啟用"}</button></td></tr>)}</tbody></table></div></article>}

    {section === "positions" && <article className="dt2-panel"><header><WalletCards size={17} /><strong>即時持倉</strong><small>未平倉部位不納入勝率。</small></header><div className="dt2-table"><table><thead><tr><th>模式</th><th>股票</th><th>機器人</th><th>買進成交時間</th><th>成交均價</th><th>現價</th><th>股數</th><th>使用資金</th><th>停損</th><th>第一停利</th><th>移動停利</th><th>未實現損益</th><th>信心分數</th><th>進場原因</th><th>操作</th></tr></thead><tbody>{data.positions.map((position) => <tr key={position.id}><td><ModeBadge mode={position.mode} /></td><td><b>{position.symbol}</b><small>{position.stockName}</small></td><td>{robotById.get(position.strategyId) ?? position.strategyId}</td><td>{dateTime(position.entryTime)}</td><td>{number(position.entryPrice, 2)}</td><td>{number(position.currentPrice, 2)}</td><td>{number(position.quantity)}股</td><td>{number(position.usedCapital)}元</td><td>{number(position.stopPrice, 2)}</td><td>{number(position.firstTargetPrice, 2)}</td><td>{number(position.trailingStopPrice, 2)}</td><td className={tone(position.unrealizedGrossPnl)}>{signedMoney(position.unrealizedGrossPnl)}</td><td>{number(position.confidence, 1)}</td><td>{position.entryReasons.join("、") || "—"}</td><td><button className="danger-soft" disabled={busy} onClick={() => { if (window.confirm(`確認以目前顯示價格平倉 ${position.symbol}？`)) void run(() => dayTradingV2Client.closePosition(userId, position.id, position.currentPrice), `${position.symbol}已送出模擬平倉`); }}>平倉</button></td></tr>)}</tbody></table></div>{!data.positions.length && <p className="dt2-empty">目前沒有未平倉部位</p>}</article>}

    {section === "trades" && <article className="dt2-panel"><header><History size={17} /><strong>完成交易紀錄</strong></header><div className="dt2-table"><table><thead><tr><th>交易編號</th><th>模式</th><th>股票</th><th>機器人/版本</th><th>買進成交</th><th>買價/股數</th><th>賣出成交</th><th>賣價</th><th>毛損益</th><th>完整成本</th><th>淨損益</th><th>報酬率</th><th>進場原因</th><th>出場原因</th></tr></thead><tbody>{data.recentTrades.map((trade) => { const costs = Number(trade.buyFee) + Number(trade.sellFee) + Number(trade.transactionTax) + Number(trade.slippage) + Number(trade.otherCost); return <tr key={trade.id}><td><small>{trade.id.slice(0, 8)}</small></td><td><ModeBadge mode={trade.mode} /></td><td><b>{trade.symbol}</b><small>{trade.stockName}</small></td><td>{robotById.get(trade.strategyId) ?? trade.strategyId}<small>v{trade.strategyVersion}</small></td><td>{dateTime(trade.entryFillTime)}</td><td>{number(trade.entryPrice, 2)} / {number(trade.quantity)}股</td><td>{dateTime(trade.exitFillTime)}</td><td>{number(trade.exitPrice, 2)}</td><td className={tone(trade.grossPnl)}>{signedMoney(trade.grossPnl)}</td><td>{number(costs)}元</td><td className={tone(trade.netPnl)}>{signedMoney(trade.netPnl)}</td><td className={tone(trade.netReturnPct)}>{number(trade.netReturnPct, 2)}%</td><td>{trade.entryReason}</td><td>{trade.exitReason}</td></tr>; })}</tbody></table></div>{!data.recentTrades.length && <p className="dt2-empty">目前沒有已平倉交易</p>}</article>}

    {section === "backtest" && <div className="dt2-grid two"><article className="dt2-panel"><header><SlidersHorizontal size={17} /><strong>策略回測中心</strong></header><div className="dt2-form"><label>回測模式<select value={backtestMode} onChange={(event) => setBacktestMode(event.target.value)}><option value="PORTFOLIO">模式B：五台共用300萬元</option><option value="INDIVIDUAL">模式A：個別機器人各300萬元</option></select></label><label>策略<select value={backtestStrategy} onChange={(event) => setBacktestStrategy(event.target.value)}><option value="ALL">五台全部</option>{data.robots.map((robot) => <option key={robot.strategyId} value={robot.strategyId}>{robot.name}</option>)}</select></label><label>開始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>結束日期<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label><button disabled={busy} onClick={() => void run(async () => { const result = await dayTradingV2Client.backtest(userId, { backtest_mode: backtestMode, strategy_id: backtestStrategy, start_date: startDate, end_date: endDate }); setBacktestResult(result); }, "回測任務已完成檢查")}>執行回測</button></div></article><article className="dt2-panel"><header><History size={17} /><strong>回測結果</strong></header>{backtestResult ? <pre className="dt2-result">{String(backtestResult.message ?? JSON.stringify(backtestResult.summary, null, 2))}</pre> : <div className="dt2-data-warning"><ShieldAlert size={22} /><b>歷史分鐘行情尚未設定</b><p>系統不會拿日K或稀疏快照產生虛假當沖績效。接上合格分鐘資料後，會套用延遲、滑價、成本、部分成交與共用資金限制。</p></div>}</article></div>}

    {section === "performance" && <div className="dt2-stack"><article className="dt2-panel"><header><CircleDollarSign size={17} /><strong>今日績效</strong></header><PerfCards data={data.today} /></article><article className="dt2-panel"><header><CircleDollarSign size={17} /><strong>本月績效</strong></header><PerfCards data={data.month} /></article><article className="dt2-panel"><header><CircleDollarSign size={17} /><strong>全期間績效</strong></header><PerfCards data={data.all} /></article></div>}

    {section === "notifications" && <article className="dt2-panel"><header><Bell size={17} /><strong>系統通知中心</strong><small>唯一事件ID防止相同通知重複送出。</small></header>{notifications.items.map((item) => <div className="dt2-event full" key={item.id}><ModeBadge mode={item.mode} /><div><b>{item.title}</b><small>{item.message}</small></div><time>{dateTime(item.createdAt)}</time></div>)}{!notifications.items.length && <p className="dt2-empty">目前沒有通知</p>}</article>}

    {section === "risk" && <div className="dt2-grid two"><article className="dt2-panel"><header><ShieldAlert size={17} /><strong>共用資金與風控</strong></header><dl className="dt2-rules"><div><dt>總資金</dt><dd>3,000,000元</dd></div><div><dt>單筆最大風險</dt><dd>{number(config.maxRiskPerTrade as string)}元</dd></div><div><dt>單日減半 / 停機</dt><dd>{number(config.dailyReduceLoss as string)} / {number(config.dailyStopLoss as string)}元</dd></div><div><dt>同時持倉</dt><dd>最多{config.maxOpenPositions as number}筆</dd></div><div><dt>同產業持倉</dt><dd>最多{config.maxSectorPositions as number}檔</dd></div><div><dt>新倉截止</dt><dd>{String(config.latestEntryTime)}</dd></div><div><dt>強制平倉</dt><dd>{String(config.forcedCloseTime)}</dd></div><div><dt>方向</dt><dd>只允許做多</dd></div></dl></article><article className="dt2-panel danger"><header><OctagonX size={17} /><strong>緊急控制</strong></header><p>停機會立即阻止所有後續新委託。取消未成交委託與持倉平倉是不同操作。</p><div className="dt2-danger-actions"><button disabled={busy} onClick={() => void run(() => dayTradingV2Client.emergencyStop(userId), "全系統已停止新委託")}>全系統停機</button><button disabled={busy} onClick={() => void run(() => dayTradingV2Client.cancelAll(userId), "已取消所有未成交委託")}>取消所有未成交委託</button><button disabled={busy || !data.positions.length} onClick={() => { if (window.confirm(`確認以畫面現價平倉全部${data.positions.length}筆模擬部位？`)) void run(() => Promise.all(data.positions.map((position) => dayTradingV2Client.closePosition(userId, position.id, position.currentPrice, "緊急全部平倉"))), "全部模擬部位已平倉"); }}>一鍵全部平倉</button></div></article></div>}

    {section === "settings" && <div className="dt2-grid two"><article className="dt2-panel"><header><Settings size={17} /><strong>交易模式</strong></header><div className="dt2-mode-select"><button className={data.mode === "PAPER" ? "active" : ""} onClick={() => void run(() => dayTradingV2Client.saveSettings(userId, "PAPER", config), "已切換為模擬交易")}>【模擬交易】</button><button className={data.mode === "BACKTEST" ? "active" : ""} onClick={() => void run(() => dayTradingV2Client.saveSettings(userId, "BACKTEST", config), "已切換為歷史回測")}>【歷史回測】</button><button disabled title={data.liveTrading.reason}>【真實交易】鎖定</button></div><p className="dt2-data-warning">預設只啟動模擬交易。真實交易必須先接上券商API、同步持倉與未成交委託，並完成二次確認。</p></article><article className="dt2-panel"><header><SlidersHorizontal size={17} /><strong>可調整參數</strong></header><div className="dt2-settings-grid">{settingsFields.map(([key, label, unit]) => <label key={key}>{label}<span><input value={String(config[key])} onChange={(event) => setData((current) => current ? { ...current, config: { ...current.config, [key]: event.target.value } } : current)} />{unit}</span></label>)}</div><button className="dt2-save" disabled={busy} onClick={() => void run(() => dayTradingV2Client.saveSettings(userId, data.mode, config), "設定已儲存並寫入稽核紀錄")}>儲存設定</button></article></div>}
  </section>;
}
