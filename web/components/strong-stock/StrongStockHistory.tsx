"use client";

import { useEffect, useState } from "react";
import { strongStockClient } from "@/services/strong-stock-client";

type Result = {
  mode?: string; message?: string; missing?: string[]; actualStartDate?: string; actualEndDate?: string;
  totalPnl?: string; totalReturnPct?: string; winRate?: string | null; maxDrawdownPct?: string;
  tradeCount?: number; openPositionCount?: number; notice?: string;
  actions?: {queued: number; filled: number; added: number; reduced: number; closed: number};
  equityCurve?: Array<{date: string; equity: string}>;
  trades?: Array<{symbol: string; name: string; entryAt: string; exitAt: string; quantity: number;
    entryPrice: string; exitPrice: string; netPnl: string; exitReason: string}>;
};
type Job = {id: string; status: string; result: Result};
const n = (value: string | number | null | undefined, suffix = "") => value == null ? "—" : `${Number(value).toLocaleString("zh-TW", {maximumFractionDigits: 2})}${suffix}`;

export function StrongStockHistory({userId}: {userId: string}) {
  const [start, setStart] = useState(() => new Date(Date.now() - 7 * 86400000).toLocaleDateString("sv-SE", {timeZone: "Asia/Taipei"}));
  const [end, setEnd] = useState(() => new Date(Date.now() - 86400000).toLocaleDateString("sv-SE", {timeZone: "Asia/Taipei"}));
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    strongStockClient.backtests(userId).then(response => {
      if (!cancelled) setJob((response.items.find(item => item.mode === "FULL") as unknown as Job) ?? null);
    }).catch(() => { if (!cancelled) setError("回測紀錄讀取失敗，請重新整理。"); });
    return () => { cancelled = true; };
  }, [userId]);
  useEffect(() => {
    if (job?.status !== "RUNNING") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await strongStockClient.backtestDetail(userId, job.id) as unknown as Job;
        if (cancelled) return;
        setJob(next); setError("");
        if (next.status === "RUNNING") timer = setTimeout(poll, 2500);
      } catch {
        if (!cancelled) { setError("進度連線中斷，正在重試…"); timer = setTimeout(poll, 5000); }
      }
    };
    timer = setTimeout(poll, 1000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job?.id, job?.status, userId]);
  async function run() {
    setBusy(true); setError(""); setJob(null);
    try { setJob(await strongStockClient.backtest(userId, start, end) as unknown as Job); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "回測建立失敗"); }
    finally { setBusy(false); }
  }
  const running = busy || job?.status === "RUNNING";
  const result = job?.status === "COMPLETED" && job.result.mode === "FULL" ? job.result : null;
  const curve = result?.equityCurve ?? [];
  const min = Math.min(...curve.map(p => Number(p.equity)));
  const max = Math.max(...curve.map(p => Number(p.equity)));
  return <article className="strong-panel">
    <h3>強勢股回測</h3>
    <p>使用目前策略與參數，從空倉重播。免費分鐘行情限最近 7 天，且須有前一交易日的完整選股輸入。</p>
    <div className="strong-form">
      <label>開始日期<input type="date" value={start} disabled={running} onChange={e => { setStart(e.target.value); setJob(null); }} /></label>
      <label>結束日期<input type="date" value={end} disabled={running} onChange={e => { setEnd(e.target.value); setJob(null); }} /></label>
      <button disabled={running || !start || !end || end < start} onClick={() => void run()}>{running ? "回測中…" : "執行回測"}</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {running && <p role="status">正在準備歷史資料與重播交易，重新整理後可繼續查看進度。</p>}
    {job && !running && !result && <div role="status">
      <p><strong>此區間尚無可用績效。</strong> {job.result.message ?? "歷史資料不足，尚未計算。"}</p>
      {!!job.result.missing?.length && <details><summary>查看缺少的資料</summary><ul>{job.result.missing.map(item => <li key={item}>{item}</li>)}</ul></details>}
    </div>}
    {result && <>
      <p>{result.actualStartDate} ～ {result.actualEndDate} · 完成交易 {result.tradeCount} 筆 · 未平倉 {result.openPositionCount} 檔</p>
      <div className="strong-metrics compact">{[
        ["總損益", n(result.totalPnl, " 元")], ["報酬率", n(result.totalReturnPct, "%")],
        ["勝率", n(result.winRate, "%")], ["最大回撤", n(result.maxDrawdownPct, "%")],
      ].map(([label, value]) => <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</div>
      {result.tradeCount === 0 && <p>完成交易為 0 筆；買進 {result.actions?.filled ?? 0} 筆。尚未平倉的持股已按行情估值。</p>}
      {curve.length > 0 && <svg viewBox="0 0 800 150" role="img" aria-label="每日資金曲線" style={{width: "100%", maxHeight: 180}}><polyline fill="none" stroke="currentColor" strokeWidth="2" points={curve.map((p, i) => `${i / Math.max(1, curve.length - 1) * 800},${140 - (Number(p.equity) - min) / Math.max(1, max - min) * 130}`).join(" ")} /></svg>}
      <details><summary>交易明細與計算方式</summary><p>{result.notice}</p><div style={{overflowX: "auto"}}><table><thead><tr>{["股票", "買進", "賣出", "股數", "買價", "賣價", "淨損益", "原因"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{result.trades?.map((t, i) => <tr key={i}><td>{t.symbol} {t.name}</td><td>{t.entryAt}</td><td>{t.exitAt}</td><td>{t.quantity}</td><td>{n(t.entryPrice)}</td><td>{n(t.exitPrice)}</td><td>{n(t.netPnl)}</td><td>{t.exitReason}</td></tr>)}</tbody></table></div></details>
    </>}
  </article>;
}
