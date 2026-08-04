"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle, Bell, Bot, CheckCircle2, CircleDollarSign, Eye, LineChart,
  RefreshCw, Save, ShieldAlert, Star, TrendingUp, WalletCards,
} from "lucide-react";
import type {
  AIPortfolioSettings, AIStockDashboard, AIStockMonitor, AIStockPosition,
} from "@/lib/ai-stock-types";
import type { MarketSnapshot, RankingRow } from "@/lib/market-types";
import {
  aiStockLineNotificationClient,
  type AIStockLineIntegrationStatus,
} from "@/services/line-notification-client";
import { formatPercent, formatVolume, safeNumber, valueClass } from "@/lib/format";

const STATUS: Record<string, string> = {
  monitoring: "監控中", waiting_breakout: "等待突破", waiting_pullback: "等待回踩",
  near_entry: "接近進場區", buy_confirmed: "買進確認", chase_blocked: "禁止追價",
  signal_weakened: "訊號轉弱", expired: "訊號失效", data_abnormal: "資料異常",
  holding: "持有中", overnight: "隔夜持有", continue_holding: "續抱",
  add_on_confirmed: "加碼確認", reduce: "建議減碼", sell_all: "建議全部賣出",
  stop_loss: "立即停損", closed: "已全部賣出",
};
const money = (value: number) => Number.isFinite(value) ? value.toLocaleString("zh-TW", { maximumFractionDigits: 0 }) : "—";
const dateTime = (value: string | null | undefined) => value
  ? new Date(value).toLocaleString("zh-TW", { hour12: false }) : "尚未取得";

async function api<T>(path = "", init?: RequestInit, userId?: string): Promise<T> {
  const response = await fetch(`/api/ai-stock${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(userId ? { "x-user-id": userId } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error ?? "AI選股監控操作失敗");
  return payload as T;
}

export function AIStockWorkflow({
  snapshot, userId, watchSymbols, onAddWatch, onAnalyze,
}: {
  snapshot: MarketSnapshot;
  userId: string;
  watchSymbols: Set<string>;
  onAddWatch: (symbol: string) => Promise<void>;
  onAnalyze: (symbol: string) => void;
}) {
  const [dashboard, setDashboard] = useState<AIStockDashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [tab, setTab] = useState<"waiting" | "positions" | "ended">("waiting");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<AIPortfolioSettings | null>(null);
  const [lineState, setLineState] = useState("檢查中");
  const [lineDetails, setLineDetails] = useState<AIStockLineIntegrationStatus | null>(null);

  const load = async (silent = false) => {
    if (!userId) return;
    if (!silent) setLoading(true);
    try {
      const data = await api<AIStockDashboard>("", undefined, userId);
      setDashboard(data);
      setSettings(data.settings);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI監控後端無法連線");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!userId) return;
    void load();
    let stopped = false;
    let timer: number | undefined;
    const refresh = async () => {
      await load(true);
      if (!stopped) timer = window.setTimeout(refresh, 10_000);
    };
    timer = window.setTimeout(refresh, 10_000);
    void aiStockLineNotificationClient.status()
      .then((status) => {
        setLineDetails(status);
        setLineState(status.connectionStatus === "connected" ? `已連線・${status.groups.length} 個群組` : "尚未綁定");
      })
      .catch(() => setLineState("LINE 狀態無法取得"));
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const featured = dashboard?.featured ?? snapshot.featured ?? [];
  const candidates = dashboard?.candidates ?? snapshot.rankings;
  const recommendedExposure = ({
    strong_bull: "70%～85%", bull: "55%～70%", sideways: "35%～50%",
    transition: "20%～40%", bear: "15%～30%", strong_bear: "0%～15%",
  } as const)[snapshot.force.direction];

  const perform = async (path: string, body?: object, method = "POST") => {
    try {
      await api(path, { method, body: body ? JSON.stringify(body) : undefined }, userId);
      setMessage("操作已保存，系統將繼續依最新狀態監控。");
      await load(true);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "操作失敗");
    }
  };

  const confirmEntry = async (item: AIStockMonitor) => {
    const actualPrice = Number(window.prompt("請輸入實際買進價格", String(item.currentPrice)));
    const quantity = Number(window.prompt("請輸入實際買進股數", String(item.suggestedInitialQuantity || 1000)));
    const customStopLoss = Number(window.prompt("請輸入停損價格", String(item.stopLoss)));
    if (!(actualPrice > 0) || !Number.isInteger(quantity) || quantity <= 0) return;
    await perform(`/ai-stock-monitor/${item.id}/confirm-entry`, {
      actual_entry_price: actualPrice, quantity, entry_time: new Date().toISOString(),
      custom_stop_loss: customStopLoss > 0 ? customStopLoss : item.stopLoss,
      line_exit_notifications: window.confirm("是否開啟此持倉的 LINE 出場通知？"),
      add_on_enabled: window.confirm("是否開啟此持倉的順勢加碼建議？"),
    });
  };

  const confirmAddOn = async (item: AIStockPosition) => {
    const actualPrice = Number(window.prompt("請輸入實際加碼價格", String(item.currentPrice)));
    const actualQuantity = Number(window.prompt("請輸入實際加碼股數", "1000"));
    const fee = Number(window.prompt("手續費（可填 0）", "0"));
    if (!(actualPrice > 0) || !Number.isInteger(actualQuantity) || actualQuantity <= 0) return;
    await perform(`/ai-stock-positions/${item.id}/confirm-add-on`, {
      actual_price: actualPrice, actual_quantity: actualQuantity,
      add_on_time: new Date().toISOString(), fee: Number.isFinite(fee) ? fee : 0,
      accept_new_stop_loss: window.confirm("是否接受系統提高後的新停損價？"),
    });
  };

  const partialExit = async (item: AIStockPosition) => {
    const quantity = Number(window.prompt("請輸入實際部分賣出股數", String(Math.max(1, Math.floor(item.remainingQuantity / 2)))));
    const exitPrice = Number(window.prompt("請輸入實際賣出價格", String(item.currentPrice)));
    const fee = Number(window.prompt("手續費（可填 0）", "0"));
    const tax = Number(window.prompt("交易稅（可填 0）", "0"));
    if (!(exitPrice > 0) || !Number.isInteger(quantity) || quantity <= 0 || quantity >= item.remainingQuantity) return;
    await perform(`/ai-stock-positions/${item.id}/partial-exit`, {
      quantity, exit_price: exitPrice, exit_time: new Date().toISOString(),
      fee: Number.isFinite(fee) ? fee : 0, tax: Number.isFinite(tax) ? tax : 0,
    });
  };

  const closePosition = async (item: AIStockPosition) => {
    const exitPrice = Number(window.prompt("請輸入實際全部賣出價格", String(item.currentPrice)));
    const fee = Number(window.prompt("手續費（可填 0）", "0"));
    const tax = Number(window.prompt("交易稅（可填 0）", "0"));
    if (!(exitPrice > 0)) return;
    await perform(`/ai-stock-positions/${item.id}/close`, {
      quantity: item.remainingQuantity, exit_price: exitPrice,
      exit_time: new Date().toISOString(),
      fee: Number.isFinite(fee) ? fee : 0, tax: Number.isFinite(tax) ? tax : 0,
      reason: "使用者確認已全部賣出",
    });
  };

  const modifyStop = async (item: AIStockPosition) => {
    const stopLoss = Number(window.prompt("新停損只能提高，不可向下放寬", String(item.stopLoss)));
    if (!(stopLoss > 0)) return;
    await perform(`/ai-stock-positions/${item.id}`, { stop_loss: stopLoss }, "PATCH");
  };

  const saveSettings = async () => {
    if (!settings) return;
    await perform("/portfolio/settings", {
      total_capital: settings.totalCapital,
      minimum_cash_percentage: settings.minimumCashPercentage,
      max_total_exposure: settings.maxTotalExposure,
      max_position_percentage: settings.maxPositionPercentage,
      max_industry_percentage: settings.maxIndustryPercentage,
      max_risk_per_trade: settings.maxRiskPerTrade,
      max_portfolio_risk: settings.maxPortfolioRisk,
      maximum_add_on_count: settings.maximumAddOnCount,
      initial_entry_ratio: settings.initialEntryRatio,
      first_add_on_ratio: settings.firstAddOnRatio,
      second_add_on_ratio: settings.secondAddOnRatio,
      allow_add_on: settings.allowAddOn,
      prohibit_averaging_down: settings.prohibitAveragingDown,
      daily_summary_enabled: settings.dailySummaryEnabled,
    }, "PUT");
    setSettingsOpen(false);
  };

  const currentItems = tab === "waiting" ? dashboard?.waiting ?? []
    : tab === "positions" ? dashboard?.positions ?? [] : dashboard?.ended ?? [];
  const statusClass = (status: string) => `ai-monitor-status status-${status.replaceAll("_", "-")}`;

  return <div className="ai-stock-workflow">
    {message && <button className="portfolio-toast" onClick={() => setMessage("")}>{message}</button>}
    {error && <div className="ai-workflow-error"><AlertTriangle size={16} />{error}<button onClick={() => void load()}><RefreshCw size={13} />重試</button></div>}

    <section className="ai-section ai-featured-panel">
      <div className="ai-section-title">
        <div><SparkIcon /><div><h2>今日 AI 精選：{featured.length}／5 檔</h2><p>通過硬性風控後自動加入 AI監控區；精選不等於立即買進</p></div></div>
        <button className="ai-settings-button" onClick={() => setSettingsOpen(!settingsOpen)}><WalletCards size={15} />資金配置</button>
      </div>
      {!snapshot.marketOpen
        ? <div className="ai-featured-empty"><Bot size={25} /><strong>目前為非交易時段</strong><span>不產生正式買進或加碼訊號；既有持倉仍保存在 PostgreSQL。</span></div>
        : !featured.length
          ? <div className="ai-featured-empty"><ShieldAlert size={25} /><strong>今日 AI 精選：0／5 檔</strong><span>目前沒有符合風控標準的股票，持續掃描中。</span></div>
          : <div className="ai-featured-grid">{featured.map((row) => <FeaturedCard key={row.signalId} row={row} onAnalyze={onAnalyze} />)}</div>}
    </section>

    {settingsOpen && settings && <section className="ai-section ai-capital-panel">
      <div className="ai-section-title"><div><CircleDollarSign size={17} /><div><h2>資金配置與部位管理</h2><p>金融金額與股數由後端 Decimal 計算</p></div></div></div>
      <div className="ai-settings-grid">
        <NumberSetting label="可投入總資金" value={settings.totalCapital} onChange={(value) => setSettings({ ...settings, totalCapital: value })} />
        <NumberSetting label="最低現金比例 %" value={settings.minimumCashPercentage} onChange={(value) => setSettings({ ...settings, minimumCashPercentage: value })} />
        <NumberSetting label="整體持倉上限 %" value={settings.maxTotalExposure} onChange={(value) => setSettings({ ...settings, maxTotalExposure: value })} />
        <NumberSetting label="單檔最大占比 %" value={settings.maxPositionPercentage} onChange={(value) => setSettings({ ...settings, maxPositionPercentage: value })} />
        <NumberSetting label="單一產業最大占比 %" value={settings.maxIndustryPercentage} onChange={(value) => setSettings({ ...settings, maxIndustryPercentage: value })} />
        <NumberSetting label="單筆最大風險 %" value={settings.maxRiskPerTrade} onChange={(value) => setSettings({ ...settings, maxRiskPerTrade: value })} />
        <NumberSetting label="整體最大風險 %" value={settings.maxPortfolioRisk} onChange={(value) => setSettings({ ...settings, maxPortfolioRisk: value })} />
        <NumberSetting label="最大加碼次數" value={settings.maximumAddOnCount} onChange={(value) => setSettings({ ...settings, maximumAddOnCount: Math.min(2, Math.max(0, Math.round(value))) })} />
        <NumberSetting label="初始建倉比例 %" value={settings.initialEntryRatio} onChange={(value) => setSettings({ ...settings, initialEntryRatio: value })} />
        <NumberSetting label="第一次加碼比例 %" value={settings.firstAddOnRatio} onChange={(value) => setSettings({ ...settings, firstAddOnRatio: value })} />
        <NumberSetting label="第二次加碼比例 %" value={settings.secondAddOnRatio} onChange={(value) => setSettings({ ...settings, secondAddOnRatio: value })} />
        <label className="ai-check-setting"><input type="checkbox" checked={settings.allowAddOn} onChange={(event) => setSettings({ ...settings, allowAddOn: event.target.checked })} />允許順勢加碼</label>
        <label className="ai-check-setting"><input type="checkbox" checked={settings.prohibitAveragingDown} onChange={(event) => setSettings({ ...settings, prohibitAveragingDown: event.target.checked })} />禁止虧損攤平</label>
        <label className="ai-check-setting"><input type="checkbox" checked={settings.dailySummaryEnabled} onChange={(event) => setSettings({ ...settings, dailySummaryEnabled: event.target.checked })} />每日 LINE 持倉摘要</label>
      </div>
      <button className="button primary" onClick={() => void saveSettings()}><Save size={14} />儲存資金設定</button>
    </section>}

    {dashboard?.allocation && <section className="ai-allocation-strip">
      <div><span>可投入資金</span><strong>${money(dashboard.allocation.totalCapital)}</strong></div>
      <div><span>已投入</span><strong>${money(dashboard.allocation.investedAmount)}</strong></div>
      <div><span>剩餘資金</span><strong>${money(dashboard.allocation.availableCapital)}</strong></div>
      <div><span>實際總持股</span><strong>{safeNumber(dashboard.allocation.actualExposurePercentage)}%</strong></div>
      <div><span>整體風險</span><strong>{safeNumber(dashboard.allocation.portfolioRiskPercentage)}%</strong></div>
      <div><span>大盤建議總持股</span><strong>{recommendedExposure}</strong></div>
      <div><span>Redis</span><strong>{dashboard.allocation.cacheHealthy ? "正常" : "記憶體降級"}</strong></div>
    </section>}

    <section className="ai-section ai-monitor-panel">
      <div className="ai-section-title">
        <div><LineChart size={17} /><div><h2>AI監控區</h2><p>持倉不受五檔限制，直到使用者確認全部賣出才結束</p></div></div>
        <span className="line-status"><Bell size={13} />AI選股 LINE {lineState}</span>
      </div>
      <div className="ai-monitor-tabs">
        <button className={tab === "waiting" ? "active" : ""} onClick={() => setTab("waiting")}>等待進場 {dashboard?.waiting.length ?? 0}</button>
        <button className={tab === "positions" ? "active" : ""} onClick={() => setTab("positions")}>持倉監控 {dashboard?.positions.length ?? 0}</button>
        <button className={tab === "ended" ? "active" : ""} onClick={() => setTab("ended")}>已結束 {dashboard?.ended.length ?? 0}</button>
      </div>
      {!currentItems.length ? <div className="ai-monitor-empty">目前沒有此類監控資料。</div>
        : tab === "waiting"
          ? <div className="ai-monitor-grid">{(currentItems as AIStockMonitor[]).map((item) =>
            <MonitorCard key={item.id} item={item} statusClass={statusClass} onAnalyze={onAnalyze}
              onConfirm={() => void confirmEntry(item)}
              onContinue={() => void perform(`/ai-stock-monitor/${item.id}/continue-monitoring`)}
              onIgnore={() => void perform(`/ai-stock-monitor/${item.id}/ignore`)}
              onRemove={() => void perform(`/ai-stock-monitor/${item.id}`, undefined, "DELETE")} />)}</div>
          : <div className="ai-monitor-grid">{(currentItems as AIStockPosition[]).map((item) =>
            <PositionCard key={item.id} item={item} statusClass={statusClass} onAnalyze={onAnalyze}
              onAddOn={() => void confirmAddOn(item)} onPartial={() => void partialExit(item)}
              onClose={() => void closePosition(item)}
              onModifyStop={() => void modifyStop(item)}
              onDeclineAddOn={() => void perform(`/ai-stock-positions/${item.id}/decline-add-on`)}
              onDisableAddOn={() => void perform(`/ai-stock-positions/${item.id}/disable-add-on`)}
              onContinue={() => void perform(`/ai-stock-positions/${item.id}/continue-monitoring`)} />)}</div>}
    </section>

    <section className="ai-section ranking-panel">
      <div className="ai-section-title"><div><TrendingUp size={17} /><div><h2>市場掃描候選清單</h2><p>至少 55 分、最多 12 檔；候選清單不發送 LINE 買進通知</p></div></div></div>
      {!candidates.length ? <div className="ai-monitor-empty">目前沒有達到 55 分的候選股票。</div>
        : <div className="table-scroll"><table className="ai-ranking-table"><thead><tr>
          <th>股票</th><th>現價／漲跌</th><th>主要策略</th><th>次要訊號</th><th>條件符合分數</th>
          <th>策略／大盤適配</th><th>健康度</th><th>入選原因</th><th>風險</th><th>行情</th><th>操作</th>
        </tr></thead><tbody>{candidates.map((row) => <tr key={row.symbol}>
          <td><button className="symbol-link" onClick={() => onAnalyze(row.symbol)}>{row.symbol}</button><small>{row.name}</small></td>
          <td>{safeNumber(row.price)}<small className={valueClass(row.changePercent)}>{formatPercent(row.changePercent)}</small><small>{formatVolume(row.volume)}</small></td>
          <td><span className="strategy-tag">{row.strategyName}</span></td>
          <td>{row.secondaryStrategies.slice(0, 2).map((value) => <small key={value}>{value}</small>)}</td>
          <td><div className={`score-pill score-${Math.floor(row.score / 10)}`}>{row.score}</div><small title={`趨勢 ${row.scoreBreakdown.trend}、動能 ${row.scoreBreakdown.momentum}、成交量 ${row.scoreBreakdown.volume}、關鍵價 ${row.scoreBreakdown.keyPrice}、策略 ${row.scoreBreakdown.strategy}、大盤 ${row.scoreBreakdown.market}、風險 ${row.scoreBreakdown.risk}`}>可追蹤評分明細</small><small className={row.aboveKeyPrice ? "positive" : "muted"}>{row.keyPrice == null ? "關鍵價資料不足" : row.aboveKeyPrice ? `站上關鍵價 ${safeNumber(row.keyPrice)}` : `關鍵價 ${safeNumber(row.keyPrice)}（未站上）`}</small></td>
          <td>{row.strategyFit}%<small>大盤 {row.marketFit}%</small></td><td>{row.healthScore}</td>
          <td><ul>{row.reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}</ul></td>
          <td>{[...row.riskTags, ...row.hardRiskFailures].slice(0, 2).map((risk) => <span className="risk-tag" key={risk}>{risk}</span>)}</td>
          <td><span className={row.quoteFresh ? "ranking-price-source official" : "ranking-price-source demo"}>{row.priceSource}</span><small>{row.priceDate} {row.priceTime}</small></td>
          <td><div className="ranking-actions"><button onClick={() => onAnalyze(row.symbol)}><Eye size={11} />查看分析</button><button disabled={watchSymbols.has(row.symbol)} onClick={() => void onAddWatch(row.symbol)}><Star size={11} />{watchSymbols.has(row.symbol) ? "已加入自選" : "加入自選"}</button></div></td>
        </tr>)}</tbody></table></div>}
    </section>

    <section className="ai-section ai-alert-log">
      <div className="ai-section-title"><div><Bell size={17} /><div><h2>AI 與 LINE 通知紀錄</h2><p>只記錄買進、加碼、減碼、賣出、停損與資料異常事件</p></div></div>
        <button onClick={() => void aiStockLineNotificationClient.test().then(() => setMessage("AI選股 LINE 測試通知已送出")).catch((reason) => setMessage(reason.message))}>測試 AI選股 LINE</button>
      </div>
      {lineDetails && <div className="ai-line-overview">
        <div><span>官方帳號</span><strong>{lineDetails.officialAccountName}</strong></div>
        <div><span>連線狀態</span><strong>{lineDetails.connectionStatus === "connected" ? "已連線" : "尚未完成設定"}</strong></div>
        <div><span>已綁定群組</span><strong>{lineDetails.groups.length}</strong></div>
        <div><span>今日推送</span><strong>{lineDetails.todayPushCount}</strong></div>
        <div><span>最後推送</span><strong>{dateTime(lineDetails.lastPushAt)}</strong></div>
      </div>}
      {!dashboard?.alerts.length ? <div className="ai-monitor-empty">目前沒有通知紀錄。</div>
        : <div className="ai-alert-list">{dashboard.alerts.slice(0, 20).map((alert) => <article key={alert.id} className={`alert-${alert.alertLevel}`}>
          <span>{dateTime(alert.createdAt)}</span><strong>{alert.action}</strong><p>{alert.reason}</p><em>LINE：{alert.linePushStatus}</em>
        </article>)}</div>}
    </section>
    <p className="ai-fixed-disclaimer">僅供研究參考，不構成投資建議。</p>
    {loading && <div className="ai-workflow-loading"><span className="spinner small" />同步 AI監控資料…</div>}
  </div>;
}

function SparkIcon() {
  return <span className="ai-section-symbol"><CheckCircle2 size={17} /></span>;
}

function NumberSetting({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return <label><span>{label}</span><input type="number" min="0" step="0.1" value={value} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

function FeaturedCard({ row, onAnalyze }: { row: RankingRow; onAnalyze: (symbol: string) => void }) {
  return <article className="ai-featured-card">
    <div><span>AI 正式精選</span><strong>{row.symbol} {row.name}</strong><em>{row.strategyName}</em></div>
    <dl><div><dt>條件符合分數</dt><dd>{row.score}</dd></div><div><dt>關鍵價</dt><dd>{row.keyPrice == null ? "—" : safeNumber(row.keyPrice)}</dd></div><div><dt>是否站上</dt><dd>{row.aboveKeyPrice ? "是" : "否"}</dd></div><div><dt>策略適配</dt><dd>{row.strategyFit}%</dd></div><div><dt>健康度</dt><dd>{row.healthScore}</dd></div><div><dt>風險報酬</dt><dd>1：{row.riskRewardRatio}</dd></div></dl>
    <p>進場區 {safeNumber(row.entryMin)}～{safeNumber(row.entryMax)} · 停損 {safeNumber(row.stopLoss)}</p>
    <small>{row.priceSource} · {row.priceDate} {row.priceTime}</small>
    <button onClick={() => onAnalyze(row.symbol)}><Eye size={13} />查看分析</button>
  </article>;
}

function MonitorCard({ item, statusClass, onAnalyze, onConfirm, onContinue, onIgnore, onRemove }: {
  item: AIStockMonitor; statusClass: (status: string) => string; onAnalyze: (symbol: string) => void;
  onConfirm: () => void; onContinue: () => void; onIgnore: () => void; onRemove: () => void;
}) {
  return <article className="ai-monitor-card">
    <header><div><strong>{item.symbol} {item.stockName}</strong><small>{item.strategyName}</small></div><span className={statusClass(item.monitorStatus)}>{STATUS[item.monitorStatus] ?? item.monitorStatus}</span></header>
    <div className="ai-monitor-numbers"><span>現價 <b>{safeNumber(item.currentPrice)}</b></span><span>進場 <b>{safeNumber(item.entryMin)}～{safeNumber(item.entryMax)}</b></span><span>停損 <b>{safeNumber(item.stopLoss)}</b></span><span>目標 <b>{safeNumber(item.target1)}／{safeNumber(item.target2)}</b></span></div>
    <div className="ai-monitor-scores"><span>條件 {item.totalScore}</span><span>適配 {item.strategyFit}%</span><span>健康 {item.healthScore}</span><span>風報 1：{item.riskRewardRatio}</span></div>
    <p>建議最終 {item.targetAllocationPercentage}% · 初始 {item.initialAllocationPercentage}% · {money(item.suggestedInitialQuantity)} 股</p>
    <ul>{item.reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}</ul>
    {!!item.warnings.length && <div>{item.warnings.slice(0, 3).map((warning) => <span className="risk-tag" key={warning}>{warning}</span>)}</div>}
    <small>{item.quoteSource} · {dateTime(item.quoteTimestamp)}</small>
    <footer><button onClick={() => onAnalyze(item.symbol)}>查看分析</button><button className="primary" disabled={item.monitorStatus !== "buy_confirmed"} onClick={onConfirm}>我已買進</button><button onClick={onContinue}>繼續觀察</button><button onClick={onIgnore}>忽略訊號</button><button onClick={onRemove}>移除監控</button></footer>
  </article>;
}

function PositionCard({
  item, statusClass, onAnalyze, onAddOn, onPartial, onClose, onContinue,
  onModifyStop, onDeclineAddOn, onDisableAddOn,
}: {
  item: AIStockPosition; statusClass: (status: string) => string; onAnalyze: (symbol: string) => void;
  onAddOn: () => void; onPartial: () => void; onClose: () => void; onContinue: () => void;
  onModifyStop: () => void; onDeclineAddOn: () => void; onDisableAddOn: () => void;
}) {
  return <article className="ai-monitor-card position-card">
    <header><div><strong>{item.symbol} {item.stockName}</strong><small>{item.strategyName}</small></div><span className={statusClass(item.positionStatus)}>{STATUS[item.positionStatus] ?? item.latestAction}</span></header>
    <div className="ai-monitor-numbers"><span>平均成本 <b>{safeNumber(item.averageCost)}</b></span><span>現價 <b>{safeNumber(item.currentPrice)}</b></span><span>剩餘股數 <b>{money(item.remainingQuantity)}</b></span><span>報酬 <b className={valueClass(item.returnPercentage)}>{formatPercent(item.returnPercentage)}</b></span></div>
    <div className="ai-monitor-scores"><span>未實現 ${money(item.unrealizedProfit)}</span><span>已實現 ${money(item.realizedProfit)}</span><span>健康 {item.healthScore}</span><span>加碼 {item.addOnCount}/2</span></div>
    <p>原始 {money(item.originalQuantity)} 股 · 已投入 ${money(item.investedAmount)} · 目前占比 {item.currentAllocationPercentage}% · 剩餘可加碼 ${money(item.availableAddOnAmount)}</p>
    <p>最高 {safeNumber(item.highestPrice)} · 最低 {safeNumber(item.lowestPrice)} · 最大浮盈 ${money(item.maxUnrealizedProfit)} · 最大浮虧 ${money(item.maxUnrealizedLoss)}</p>
    <p>停損 {safeNumber(item.stopLoss)} · 目標 {safeNumber(item.target1)}／{safeNumber(item.target2)} · 移動停利 {item.trailingStop ? safeNumber(item.trailingStop) : "未啟用"}</p>
    <small>{item.quoteSource} · {dateTime(item.quoteTimestamp)}</small>
    <footer><button onClick={() => onAnalyze(item.symbol)}>查看分析</button><button onClick={onModifyStop}>修改停損</button>{item.latestAction.includes("加碼確認") && <><button className="primary" onClick={onAddOn}>我已加碼</button><button onClick={onDeclineAddOn}>暫不加碼</button></>}<button onClick={onDisableAddOn}>關閉加碼建議</button><button onClick={onPartial}>我已部分賣出</button><button className="danger" onClick={onClose}>我已全部賣出</button>{["sell_all","stop_loss","reduce"].includes(item.positionStatus) && <button onClick={onContinue}>尚未成交，繼續監控</button>}</footer>
  </article>;
}
