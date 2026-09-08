"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Bell, CandlestickChart, Database, History, Pause, Play, RefreshCw, Search, Settings, ShieldAlert, Target, TrendingUp, WalletCards } from "lucide-react";
import type { StrongDashboard, StrongRanking } from "@/lib/strong-stock-types";
import { strongStockClient } from "@/services/strong-stock-client";

type Section = "overview" | "ranking" | "positions" | "trades" | "backtest" | "notifications" | "data" | "settings";

const sectionLabels: Array<[Section, string]> = [
  ["overview", "總覽"], ["ranking", "強勢股排行榜"], ["positions", "模擬持倉"],
  ["trades", "交易與績效"], ["backtest", "獨立回測"], ["notifications", "通知"],
  ["data", "資料狀態"], ["settings", "設定"],
];

function n(value: unknown, digits = 0) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? new Intl.NumberFormat("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(parsed) : "—";
}
function signed(value: unknown) { const parsed = Number(value ?? 0); return `${parsed > 0 ? "+" : ""}${n(parsed)}`; }
function tone(value: unknown) { const parsed = Number(value ?? 0); return parsed > 0 ? "profit" : parsed < 0 ? "loss" : ""; }
function dt(value: string | null | undefined) { return value ? new Date(value).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }) : "—"; }
function statusLabel(value: string) {
  return ({ ENTRY_READY: "符合進場", WATCH: "進場觀察", TRACKING: "持續追蹤", DATA_INSUFFICIENT: "資料不足", BREAKOUT: "突破", PULLBACK: "回踩", PENDING: "等待隔日成交" } as Record<string, string>)[value] ?? value;
}

export function StrongStockPage({ userId, onSelectStock }: { userId: string; onSelectStock: (symbol: string) => void }) {
  const [section, setSection] = useState<Section>("overview");
  const [data, setData] = useState<StrongDashboard | null>(null);
  const [selected, setSelected] = useState<StrongRanking | null>(null);
  const [query, setQuery] = useState("");
  const [industry, setIndustry] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [backtestResult, setBacktestResult] = useState<Record<string, unknown> | null>(null);
  const [startDate, setStartDate] = useState(`${new Date().getFullYear() - 1}-${String(new Date().getMonth() + 1).padStart(2, "0")}-${String(new Date().getDate()).padStart(2, "0")}`);
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10));

  const load = useCallback(async (quiet = false) => {
    if (!userId) return;
    if (!quiet) setLoading(true);
    try { setData(await strongStockClient.dashboard(userId)); setError(""); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "強勢股資料讀取失敗"); }
    finally { if (!quiet) setLoading(false); }
  }, [userId]);

  useEffect(() => { void load(); const timer = window.setInterval(() => void load(true), 30_000); return () => window.clearInterval(timer); }, [load]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); await load(true); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "操作失敗"); }
    finally { setBusy(false); }
  }

  const industries = useMemo(() => [...new Set(data?.rankings.map((row) => row.industry) ?? [])].sort(), [data]);
  const rankings = useMemo(() => (data?.rankings ?? []).filter((row) => {
    const matchText = !query || row.symbol.includes(query.trim()) || row.name.includes(query.trim());
    return matchText && (!industry || row.industry === industry) && (!status || row.status === status);
  }), [data, query, industry, status]);

  if (loading && !data) return <div className="strong-loading"><span className="spinner" /><p>載入強勢股策略資料…</p></div>;
  if (!data) return <div className="error-banner">{error || "強勢股策略暫時無法使用"}</div>;
  const p = data.performance;

  return <section className="strong-page">
    <header className="strong-hero">
      <div><p className="section-kicker">PURE LONG · 5–60 TRADING DAYS · PAPER</p><h1><TrendingUp size={25} />強勢股策略</h1><p>日K、週K、產業、基本面與相對強度選股；使用獨立300萬元模擬資金。</p></div>
      <div className="strong-hero-actions"><span className="strong-paper">【模擬交易】</span><button disabled={busy} onClick={() => void load()}><RefreshCw size={15} />更新</button>{data.paperEnabled ? <button disabled={busy} onClick={() => void run(() => strongStockClient.pause(userId), "已暫停新增模擬交易")}><Pause size={15} />暫停</button> : <button disabled={busy} onClick={() => void run(() => strongStockClient.start(userId), "已啟動模擬交易")}><Play size={15} />啟動</button>}</div>
    </header>
    {(error || notice) && <div className={error ? "error-banner" : "strong-notice"}>{error || notice}</div>}
    <nav className="strong-tabs">{sectionLabels.map(([key, label]) => <button key={key} className={section === key ? "active" : ""} onClick={() => setSection(key)}>{label}</button>)}</nav>

    {section === "overview" && <>
      <div className="strong-metrics">
        <article><span>大盤狀態</span><strong>{data.marketRegime.label}</strong><small>信心 {n(data.marketRegime.confidence, 0)}%</small></article>
        <article><span>建議持股</span><strong>{n(data.marketRegime.suggestedExposurePct)}%</strong><small>{data.marketRegime.reasons[0] ?? "等待資料"}</small></article>
        <article><span>模擬總資產</span><strong>{n(p.totalEquity)}元</strong><small>初始 3,000,000元</small></article>
        <article><span>可用現金</span><strong>{n(p.cash)}元</strong><small>目前持倉 {n(p.openCount)} 檔</small></article>
        <article><span>已實現損益</span><strong className={tone(p.realizedPnl)}>{signed(p.realizedPnl)}元</strong><small>完整成本後</small></article>
        <article><span>未實現損益</span><strong className={tone(p.unrealizedPnl)}>{signed(p.unrealizedPnl)}元</strong><small>不納入勝率</small></article>
        <article><span>累積報酬率</span><strong className={tone(p.totalReturnPct)}>{signed(p.totalReturnPct)}%</strong><small>模擬帳本</small></article>
        <article><span>勝率</span><strong>{p.winRate == null ? "無已完成交易" : `${n(p.winRate, 1)}%`}</strong><small>{n(p.tradeCount)} 筆已平倉</small></article>
        <article><span>最大回撤</span><strong className="warn">{n(p.maximumDrawdownPct, 2)}%</strong><small>{n(p.maximumDrawdown)}元</small></article>
      </div>
      <div className="strong-grid two">
        <article className="strong-panel"><header><Activity size={17} /><div><strong>盤勢判斷</strong><small>{data.marketRegime.tradeDate ?? "尚未完成"}</small></div></header><ul className="strong-reasons">{data.marketRegime.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></article>
        <article className="strong-panel"><header><Target size={17} /><div><strong>強勢產業前5名</strong><small>優先從前20%產業挑選</small></div></header><div className="strong-industry-list">{data.industries.map((row) => <div key={row.industry}><b>#{row.rank}</b><span>{row.industry}</span><strong>{n(row.score, 1)}分</strong></div>)}{!data.industries.length && <p>等待盤後產業資料</p>}</div></article>
      </div>
      <article className="strong-panel"><header><ShieldAlert size={17} /><div><strong>策略健康檢查：{data.strategyHealth.status === "SAMPLE_INSUFFICIENT" ? "樣本不足" : data.strategyHealth.status === "ALERT" ? "績效警戒" : "正常"}</strong><small>最近20筆與50筆；警戒時只會降低風險或暫停，不會自動放寬參數</small></div></header><ul className="strong-reasons">{data.strategyHealth.reasons.map((reason) => <li key={reason}>{reason}</li>)}{!data.strategyHealth.reasons.length && <li>目前未觸發績效警戒</li>}</ul></article>
      <RankingTable rows={data.rankings.slice(0, 10)} onDetail={setSelected} onSelectStock={onSelectStock} />
      {!!data.pendingOrders.length && <article className="strong-panel"><header><CandlestickChart size={17} /><div><strong>隔日模擬委託</strong><small>不符合價格條件會自動標記未成交</small></div></header><div className="strong-table"><table><thead><tr><th>股票</th><th>型態</th><th>限價</th><th>股數</th><th>有效日</th><th>原因</th></tr></thead><tbody>{data.pendingOrders.map((row) => <tr key={row.id}><td><b>{row.symbol}</b><small>{row.name}</small></td><td>{statusLabel(row.entryType)}</td><td>{n(row.limitPrice, 2)}</td><td>{n(row.quantity)}</td><td>{row.validDate}</td><td>{row.reason || "—"}</td></tr>)}</tbody></table></div></article>}
    </>}

    {section === "ranking" && <>
      <div className="strong-filters"><label><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="股票代碼或名稱" /></label><select value={industry} onChange={(event) => setIndustry(event.target.value)}><option value="">全部產業</option>{industries.map((item) => <option key={item}>{item}</option>)}</select><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部狀態</option><option value="ENTRY_READY">符合進場</option><option value="WATCH">進場觀察</option><option value="TRACKING">持續追蹤</option><option value="DATA_INSUFFICIENT">資料不足</option></select></div>
      <RankingTable rows={rankings} onDetail={setSelected} onSelectStock={onSelectStock} full />
    </>}

    {section === "positions" && <article className="strong-panel"><header><WalletCards size={17} /><div><strong>獨立模擬持倉</strong><small>當沖系統無法讀取或賣出這些部位</small></div></header><div className="strong-table"><table><thead><tr><th>股票</th><th>買進時間</th><th>成本／現價</th><th>股數／資金</th><th>未實現損益</th><th>停損／移動停利</th><th>下次加碼</th><th>評分</th><th>理由／警告</th><th>操作</th></tr></thead><tbody>{data.positions.map((row) => <tr key={row.id}><td><b>{row.symbol}</b><small>{row.name}｜{row.industry}</small></td><td>{dt(row.entryAt)}<small>v{row.strategyVersion}</small></td><td>{n(row.averageCost, 2)}<small>{n(row.currentPrice, 2)}</small></td><td>{n(row.quantity)}股<small>{n(row.investedCapital)}元</small></td><td className={tone(row.unrealizedPnl)}>{signed(row.unrealizedPnl)}元<small>{signed(row.returnPct)}%</small></td><td>{n(row.initialStop, 2)}<small>{n(row.trailingStop, 2)}</small></td><td>{row.nextAddPrice ? n(row.nextAddPrice, 2) : "—"}</td><td>{n(row.currentScore, 1)}</td><td>{row.reasons.join("、") || "—"}<small className="warn">{row.warnings.join("、")}</small></td><td><button className="danger-soft" disabled={busy} onClick={() => { if (window.confirm(`確認以目前顯示價格模擬賣出 ${row.symbol}？`)) void run(() => strongStockClient.close(userId, row.id, row.currentPrice, "使用者手動模擬賣出"), `${row.symbol} 已完成模擬賣出`); }}>模擬賣出</button></td></tr>)}</tbody></table></div>{!data.positions.length && <p className="strong-empty">目前沒有強勢股模擬持倉</p>}</article>}

    {section === "trades" && <><div className="strong-metrics compact"><article><span>已完成交易</span><strong>{n(p.tradeCount)}</strong></article><article><span>獲利／虧損</span><strong>{n(p.winCount)}／{n(p.lossCount)}</strong></article><article><span>獲利因子</span><strong>{p.profitFactor ?? "樣本不足"}</strong></article><article><span>使用資金</span><strong>{n(p.investedCapital)}元</strong></article></div><article className="strong-panel"><header><History size={17} /><div><strong>完成交易</strong><small>損益已扣手續費、交易稅與滑價</small></div></header><div className="strong-table"><table><thead><tr><th>股票</th><th>型態／版本</th><th>買進／賣出</th><th>價格／股數</th><th>毛損益</th><th>成本</th><th>淨損益</th><th>出場原因</th></tr></thead><tbody>{data.trades.map((row) => <tr key={row.id}><td><b>{row.symbol}</b><small>{row.name}</small></td><td>{statusLabel(row.entryType)}<small>v{row.strategyVersion}</small></td><td>{dt(row.entryAt)}<small>{dt(row.exitAt)}</small></td><td>{n(row.entryPrice, 2)} → {n(row.exitPrice, 2)}<small>{n(row.quantity)}股</small></td><td className={tone(row.grossPnl)}>{signed(row.grossPnl)}</td><td>{n(Number(row.buyFee) + Number(row.sellFee) + Number(row.tax) + Number(row.slippage))}</td><td className={tone(row.netPnl)}>{signed(row.netPnl)}<small>{signed(row.returnPct)}%</small></td><td>{row.exitReason}</td></tr>)}</tbody></table></div>{!data.trades.length && <p className="strong-empty">尚無已完成交易，勝率不顯示為0%</p>}</article></>}

    {section === "backtest" && <><div className="strong-metrics compact"><article><span>模擬交易績效</span><strong>{n(p.totalReturnPct, 2)}%</strong></article><article><span>已完成交易</span><strong>{n(p.tradeCount)}</strong></article><article><span>勝率</span><strong>{p.winRate == null ? "樣本不足" : `${n(p.winRate, 1)}%`}</strong></article><article><span>總損益</span><strong className={tone(p.realizedPnl)}>{signed(p.realizedPnl)}元</strong></article></div><div className="strong-grid two"><article className="strong-panel"><header><History size={17} /><div><strong>強勢股獨立回測</strong><small>與當沖回測資料完全分開</small></div></header><div className="strong-form"><label>開始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>結束日期<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label><label>基準ETF<input value="0050" readOnly /></label><button disabled={busy} onClick={() => void run(async () => setBacktestResult(await strongStockClient.backtest(userId, startDate, endDate)), "已完成歷史資料完整性檢查")}>執行回測</button></div></article><article className="strong-panel"><header><ShieldAlert size={17} /><div><strong>回測資料狀態</strong><small>不使用未來資料或目前股票名單回填</small></div></header><div className="strong-data-warning"><b>{data.dataStatus.historicalBacktestReady ? "資料已通過驗證" : "歷史時間點資料尚未齊全"}</b><p>下方是目前已完成的模擬交易績效；它不等同於歷史回測。</p><ul>{(backtestResult?.result as { missing?: string[] } | undefined)?.missing?.map((item) => <li key={item}>{item}</li>) ?? <><li>歷史上市、上櫃及下市股票母體</li><li>基本面與月營收實際公布時間</li><li>歷史產業分類與完整企業行動</li></>}</ul><pre>{backtestResult ? JSON.stringify(backtestResult, null, 2) : "資料合格後才會解鎖1／3／5／10年績效。"}</pre></div></article></div></>}

    {section === "notifications" && <article className="strong-panel"><header><Bell size={17} /><div><strong>強勢股通知中心</strong><small>事件ID避免重複通知</small></div></header><div className="strong-notification-list">{data.notifications.map((row) => <article key={row.id} className={row.priority === "HIGH" ? "high" : ""}><div><b>{row.title}</b><time>{dt(row.createdAt)}</time></div><p>{row.message}</p></article>)}{!data.notifications.length && <p className="strong-empty">目前沒有通知</p>}</div></article>}

    {section === "data" && <div className="strong-grid two"><article className="strong-panel"><header><Database size={17} /><div><strong>行情與排程</strong><small>只有資料日期一致才會產生正式評分</small></div></header><dl className="strong-data-list"><div><dt>更新狀態</dt><dd>{data.dataStatus.status}</dd></div><div><dt>最新交易日期</dt><dd>{data.dataStatus.latestTradeDate ?? "—"}</dd></div><div><dt>成功更新時間</dt><dd>{dt(data.dataStatus.lastSuccessfulUpdate)}</dd></div><div><dt>排程錯誤</dt><dd>{data.dataStatus.error || "無"}</dd></div></dl></article><article className="strong-panel"><header><ShieldAlert size={17} /><div><strong>缺少資料</strong><small>缺項不會以推測值代替</small></div></header><ul className="strong-reasons">{data.dataStatus.missing.map((item) => <li key={item}>{item}</li>)}{!data.dataStatus.missing.length && <li>即時選股資料未回報缺項</li>}<li>3／5／10年時間點基本面資料集尚未設定</li></ul></article></div>}

    {section === "settings" && <article className="strong-panel"><header><Settings size={17} /><div><strong>可調整參數</strong><small>真實交易永久鎖定；調整只影響後續模擬訊號</small></div></header><div className="strong-settings">{[["minimumEntryScore", "最低進場分數"], ["minimumRiskReward", "最低風險報酬"], ["minimumAverageTurnover20d", "20日最低成交金額"], ["maximumDailyRisePct", "禁止追高單日漲幅%"], ["maximumDistanceMa20Pct", "最大偏離MA20%"], ["riskPerTrade", "單筆最大風險"], ["maximumPositionCapital", "單檔最高資金"], ["maximumIndustryPct", "產業上限%"], ["maximumOpenPositions", "最多持股檔數"]].map(([key, label]) => <label key={key}>{label}<input value={String(data.config[key])} onChange={(event) => setData((current) => current ? { ...current, config: { ...current.config, [key]: event.target.value } } : current)} /></label>)}</div><footer><label><input type="checkbox" checked={Boolean(data.config.paperAutoTrade)} onChange={(event) => setData((current) => current ? { ...current, config: { ...current.config, paperAutoTrade: event.target.checked } } : current)} />隔日自動模擬交易</label><button disabled={busy} onClick={() => void run(() => strongStockClient.saveSettings(userId, true, data.config), "強勢股設定已儲存")}>儲存設定</button></footer></article>}

    {selected && <div className="strong-detail-backdrop" onClick={() => setSelected(null)}><article className="strong-detail" onClick={(event) => event.stopPropagation()}><button onClick={() => setSelected(null)}>×</button><header><b>#{selected.rank} {selected.symbol} {selected.name}</b><span>{selected.industry}｜{statusLabel(selected.status)}</span></header><div className="strong-score-grid">{[["總分", selected.totalScore], ["相對強度", selected.relativeStrengthScore], ["趨勢", selected.trendScore], ["產業", selected.industryScore], ["量價籌碼", selected.volumeChipScore], ["基本面", selected.fundamentalScore], ["估值風險", selected.valuationRiskScore], ["完整度", `${n(Number(selected.dataCompleteness) * 100)}%`]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><h3>入選原因</h3><ul>{selected.reasons.map((item) => <li key={item}>{item}</li>)}</ul><h3>尚未進場原因</h3><ul>{selected.blockedReasons.map((item) => <li key={item}>{item}</li>)}</ul><button className="primary-soft" onClick={() => onSelectStock(selected.symbol)}>開啟完整個股K線</button></article></div>}
    <p className="strong-disclaimer">{data.notice}</p>
  </section>;
}

function RankingTable({ rows, onDetail, onSelectStock, full = false }: { rows: StrongRanking[]; onDetail: (row: StrongRanking) => void; onSelectStock: (symbol: string) => void; full?: boolean }) {
  return <article className="strong-panel"><header><TrendingUp size={17} /><div><strong>{full ? "完整強勢股排行榜" : "強勢股票前10名"}</strong><small>高分只代表觀察，仍須等待合理買點</small></div></header><div className="strong-table"><table><thead><tr><th>排名</th><th>股票</th><th>產業</th><th>總分</th><th>相對／趨勢</th><th>產業／量價</th><th>基本面／風險</th><th>收盤價</th><th>進場區間</th><th>停損／加碼</th><th>風報比</th><th>狀態</th><th>原因</th></tr></thead><tbody>{rows.map((row) => <tr key={`${row.tradeDate}-${row.symbol}`}><td>#{row.rank}</td><td><button className="strong-stock-link" onClick={() => onSelectStock(row.symbol)}><b>{row.symbol}</b><small>{row.name}</small></button></td><td>{row.industry}</td><td><button className="strong-score" onClick={() => onDetail(row)}>{n(row.totalScore, 1)}</button></td><td>{n(row.relativeStrengthScore, 1)}／{n(row.trendScore, 1)}</td><td>{n(row.industryScore, 1)}／{n(row.volumeChipScore, 1)}</td><td>{n(row.fundamentalScore, 1)}／{n(row.valuationRiskScore, 1)}</td><td>{n(row.closePrice, 2)}</td><td>{n(row.entryLow, 2)}～{n(row.entryHigh, 2)}<small>{statusLabel(row.entryType)}</small></td><td>{n(row.stopPrice, 2)}<small>{n(row.addPrice, 2)}</small></td><td>{row.riskReward ? `1:${n(row.riskReward, 1)}` : "—"}</td><td><span className={`strong-status ${row.status.toLowerCase()}`}>{statusLabel(row.status)}</span></td><td>{row.reasons[0] ?? row.blockedReasons[0] ?? "—"}<button className="strong-more" onClick={() => onDetail(row)}>查看明細</button></td></tr>)}</tbody></table></div>{!rows.length && <p className="strong-empty">等待完整盤後資料後產生排行榜</p>}</article>;
}
