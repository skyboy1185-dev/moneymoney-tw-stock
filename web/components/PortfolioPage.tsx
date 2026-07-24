"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, BriefcaseBusiness, Clock3, Eye, RefreshCw, Star, Trash2, TrendingUp, X } from "lucide-react";
import type { HoldingItem, WatchlistItem, WatchStatus } from "@/lib/market-types";
import { formatPercent, safeNumber, valueClass } from "@/lib/format";

const statusClass: Record<WatchStatus, string> = {
  "剛加入觀察": "new", "持續強勢": "strong", "回檔轉強": "recovering", "動能轉弱": "weak", "出場警戒": "warning",
};

export function PortfolioPage({ userId, onSelectStock }: { userId: string; onSelectStock: (symbol: string) => void }) {
  const [tab, setTab] = useState<"watchlist" | "holdings">("watchlist");
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [holdings, setHoldings] = useState<HoldingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState("");
  const [convertTarget, setConvertTarget] = useState<WatchlistItem | null>(null);
  const [form, setForm] = useState({ cost: "", lots: "1", buyDate: new Date().toISOString().slice(0, 10) });

  const load = useCallback(async (silent = false) => {
    if (!userId) return;
    if (!silent) setLoading(true);
    setError("");
    try {
      const [watchResponse, holdingResponse] = await Promise.all([
        fetch("/api/watchlist", { headers: { "x-user-id": userId } }),
        fetch("/api/holdings", { headers: { "x-user-id": userId } }),
      ]);
      const watchPayload = await watchResponse.json();
      const holdingPayload = await holdingResponse.json();
      if (!watchResponse.ok) throw new Error(watchPayload.error ?? "自選清單載入失敗");
      if (!holdingResponse.ok) throw new Error(holdingPayload.error ?? "持股載入失敗");
      setWatchlist(watchPayload.items);
      setHoldings(holdingPayload.items);
      setUpdatedAt(new Date().toISOString());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "資料載入失敗");
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const remove = async (type: "watchlist" | "holdings", symbol: string) => {
    const endpoint = type === "watchlist" ? "/api/watchlist" : "/api/holdings";
    const response = await fetch(`${endpoint}?symbol=${symbol}`, { method: "DELETE", headers: { "x-user-id": userId } });
    if (!response.ok) { setError("移除失敗，請稍後再試。"); return; }
    if (type === "watchlist") setWatchlist((items) => items.filter((item) => item.symbol !== symbol));
    else setHoldings((items) => items.filter((item) => item.symbol !== symbol));
  };

  const convert = async () => {
    if (!convertTarget) return;
    const cost = Number(form.cost);
    const lots = Number(form.lots);
    if (!(cost > 0) || !(lots > 0)) { setError("請輸入有效的成本與張數。"); return; }
    const response = await fetch("/api/holdings", {
      method: "POST", headers: { "Content-Type": "application/json", "x-user-id": userId },
      body: JSON.stringify({ symbol: convertTarget.symbol, cost, lots, buyDate: form.buyDate, fromWatchlist: true }),
    });
    const payload = await response.json();
    if (!response.ok) { setError(payload.error ?? (response.status === 409 ? "此股票已在持股中。" : "轉換失敗")); return; }
    setConvertTarget(null);
    setTab("holdings");
    await load();
  };

  return (
    <div className="portfolio-page">
      <div className="page-heading portfolio-heading">
        <div><p className="section-kicker">PERSONAL TRACKER</p><h1>我的自選</h1><p>自選觀察與實際持股分開管理，每 60 秒重新評估價格、AI 分數與狀態。</p></div>
        <div className="portfolio-update"><span><Clock3 size={12} />最後更新 {updatedAt ? new Date(updatedAt).toLocaleTimeString("zh-TW", { hour12: false }) : "—"}</span><button onClick={() => void load()} disabled={loading}><RefreshCw size={14} className={loading ? "spin-icon" : ""} />立即更新</button></div>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="portfolio-tabs">
        <button className={tab === "watchlist" ? "active" : ""} onClick={() => setTab("watchlist")}><Star size={15} />自選觀察<span>{watchlist.length}</span></button>
        <button className={tab === "holdings" ? "active" : ""} onClick={() => setTab("holdings")}><BriefcaseBusiness size={15} />我的持股<span>{holdings.length}</span></button>
      </div>

      <section className="portfolio-panel">
        <div className="portfolio-panel-info">
          {tab === "watchlist" ? <><Star size={17} /><div><h2>尚未買進的觀察清單</h2><p>股票不再符合原始策略時不會自動刪除，而會保留並顯示失效原因。</p></div></>
            : <><BriefcaseBusiness size={17} /><div><h2>已買進的持股</h2><p>成本、張數與買進日期獨立保存；報酬僅依價格計算，未含手續費與稅。</p></div></>}
        </div>
        {loading ? <div className="table-loading"><span className="spinner" /><span>正在更新官方報價與 AI 狀態…</span></div>
          : tab === "watchlist" ? (
            !watchlist.length ? <div className="table-empty"><Star size={28} /><h3>尚未加入自選觀察</h3><p>可從 AI 自動選股排行榜點擊「加入自選」。</p></div>
              : <div className="table-scroll"><table className="portfolio-table watch-table"><thead><tr><th>股票</th><th>加入時間</th><th>加入時價格</th><th>最新價格</th><th>加入後漲跌</th><th>AI 分數</th><th>分數變化</th><th>原始機器人</th><th>目前狀態</th><th>策略有效性</th><th>操作</th></tr></thead>
                <tbody>{watchlist.map((item) => <tr key={item.symbol}>
                  <td><button className="symbol-link" onClick={() => onSelectStock(item.symbol)}>{item.symbol}</button><small>{item.name}</small></td>
                  <td>{new Date(item.addedAt).toLocaleDateString("zh-TW")}<small>{new Date(item.addedAt).toLocaleTimeString("zh-TW", { hour12: false })}</small></td>
                  <td>{safeNumber(item.addedPrice)}</td><td>{safeNumber(item.latestPrice)}</td>
                  <td className={valueClass(item.returnPercent)}>{formatPercent(item.returnPercent)}</td>
                  <td><span className="score-compare"><b>{item.addedScore}</b><i>→</i><strong>{item.currentScore}</strong></span><small>加入時／目前</small></td>
                  <td className={valueClass(item.scoreChange)}>{item.scoreChange >= 0 ? "+" : ""}{item.scoreChange}</td>
                  <td><span className="strategy-tag">{item.originalRobotName}</span><small>{item.originalReasons.slice(0,3).join("・")}</small></td>
                  <td><span className={`watch-status ${statusClass[item.status]}`}>{item.status}</span></td>
                  <td>{item.matchesOriginalStrategy ? <span className="strategy-valid">仍符合原始策略</span> : <><span className="strategy-invalid">已不符合原始策略</span><ul className="invalid-reasons">{item.invalidReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></>}</td>
                  <td><div className="portfolio-actions"><button onClick={() => onSelectStock(item.symbol)}><Eye size={11} />查看分析</button><button onClick={() => { setConvertTarget(item); setForm({ cost: String(item.latestPrice), lots: "1", buyDate: new Date().toISOString().slice(0,10) }); }}><BriefcaseBusiness size={11} />轉為持股</button><button className="danger" onClick={() => void remove("watchlist", item.symbol)}><Trash2 size={11} />移除自選</button></div></td>
                </tr>)}</tbody></table></div>
          ) : (
            !holdings.length ? <div className="table-empty"><BriefcaseBusiness size={28} /><h3>目前沒有持股</h3><p>可從 AI 排行榜直接加入，或將自選觀察轉為持股。</p></div>
              : <div className="table-scroll"><table className="portfolio-table holding-table"><thead><tr><th>股票</th><th>買進日期</th><th>成本</th><th>張數</th><th>最新價格</th><th>未實現損益</th><th>報酬率</th><th>AI 分數</th><th>原始機器人</th><th>目前狀態</th><th>原始選股紀錄</th><th>操作</th></tr></thead>
                <tbody>{holdings.map((item) => <tr key={item.symbol}>
                  <td><button className="symbol-link" onClick={() => onSelectStock(item.symbol)}>{item.symbol}</button><small>{item.name}</small></td><td>{item.buyDate}</td>
                  <td>{safeNumber(item.cost)}</td><td>{safeNumber(item.lots, 3)} 張<small>{item.shares.toLocaleString("zh-TW")} 股</small></td><td>{safeNumber(item.latestPrice)}</td>
                  <td className={valueClass(item.unrealizedProfit)}>{item.unrealizedProfit >= 0 ? "+" : ""}{Math.round(item.unrealizedProfit).toLocaleString("zh-TW")}<small>市值 {Math.round(item.marketValue).toLocaleString("zh-TW")}</small></td>
                  <td className={valueClass(item.returnPercent)}>{formatPercent(item.returnPercent)}</td>
                  <td><span className="score-compare"><b>{item.originalAiScore}</b><i>→</i><strong>{item.currentAiScore}</strong></span></td>
                  <td><span className="strategy-tag">{item.originalRobotName}</span></td><td><span className={`watch-status ${statusClass[item.status]}`}>{item.status}</span></td>
                  <td>{new Date(item.originalSelectedAt).toLocaleString("zh-TW", { hour12: false })}<small>入選價 {safeNumber(item.originalSelectedPrice)}</small><small>{item.originalReasons.slice(0,3).join("・")}</small></td>
                  <td><div className="portfolio-actions"><button onClick={() => onSelectStock(item.symbol)}><Eye size={11} />查看分析</button><button className="danger" onClick={() => void remove("holdings", item.symbol)}><Trash2 size={11} />移除持股</button></div></td>
                </tr>)}</tbody></table></div>
          )}
      </section>

      <div className="intraday-warning"><AlertTriangle size={15} /><span>AI 分數代表目前條件符合策略的完整程度，不代表上漲機率或投資績效。</span></div>
      {convertTarget && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setConvertTarget(null); }}>
        <div className="holding-modal" role="dialog" aria-modal="true">
          <button className="modal-close" onClick={() => setConvertTarget(null)}><X size={17} /></button>
          <div className="modal-icon"><TrendingUp size={20} /></div><h2>從自選轉為持股</h2>
          <p>{convertTarget.symbol} {convertTarget.name} · 原始 AI {convertTarget.addedScore} 分與加入紀錄將完整保留</p>
          <div className="holding-form-grid"><label><span>買進成本（每股）</span><input type="number" min=".01" step=".01" value={form.cost} onChange={(event) => setForm({ ...form, cost: event.target.value })} /></label><label><span>張數</span><input type="number" min=".001" step=".001" value={form.lots} onChange={(event) => setForm({ ...form, lots: event.target.value })} /></label><label><span>買進日期</span><input type="date" value={form.buyDate} onChange={(event) => setForm({ ...form, buyDate: event.target.value })} /></label></div>
          <div className="modal-actions"><button className="button ghost" onClick={() => setConvertTarget(null)}>取消</button><button className="button primary" onClick={() => void convert()}>確認轉為持股</button></div>
        </div>
      </div>}
    </div>
  );
}
