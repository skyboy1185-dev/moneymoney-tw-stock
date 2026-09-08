"use client";

import { useEffect, useState } from "react";
import { strongStockClient } from "@/services/strong-stock-client";

type Result = {
  message?: string; actualStartDate: string; actualEndDate: string; totalPnl: number;
  totalReturnPct: number; maxDrawdownPct: number; tradeCount: number; winRate: number | null;
  benchmarkReturnPct: number | null; openPositionCount: number; rules: string;
  limitations: string[]; excluded: string[]; symbols: string[];
  equityCurve: Array<{date: string; equity: number}>;
  trades: Array<{symbol: string; entryDate: string; exitDate: string; quantity: number;
    entryPrice: number; exitPrice: number; buyFee: number; sellFee: number; tax: number;
    dividends: number; netPnl: number; exitReason: string}>;
};
type Job = { id: string; status: string; result: Partial<Result> };
const format = (value: number | null | undefined, suffix = "") => value == null ? "—" : `${value.toLocaleString("zh-TW", {maximumFractionDigits: 2})}${suffix}`;

export function StrongStockBacktest({userId}: {userId: string}) {
  const [start, setStart] = useState(() => new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(() => new Date(Date.now() - 86400000).toISOString().slice(0, 10));
  const [symbols, setSymbols] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    let cancelled = false;
    strongStockClient.backtests(userId).then(response => {
      if (cancelled) return;
      const items = response.items as unknown as Job[];
      setHistory(items);
      setJob(items[0] ?? null);
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
        else setHistory(items => [next, ...items.filter(item => item.id !== next.id)]);
      } catch {
        if (!cancelled) { setError("進度連線中斷，正在重試…"); timer = setTimeout(poll, 5000); }
      }
    };
    timer = setTimeout(poll, 1000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job?.id, job?.status, userId]);
  async function submit() {
    setSubmitting(true); setError("");
    try {
      const next = await strongStockClient.freeBacktest(userId, start, end, symbols.split(/[\s,，]+/).filter(Boolean)) as unknown as Job;
      setJob(next);
      setHistory(items => [next, ...items]);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "建立回測失敗"); }
    finally { setSubmitting(false); }
  }
  const r = job?.status === "COMPLETED" && job.result.equityCurve ? job.result : null;
  const curve = r?.equityCurve ?? [];
  const min = Math.min(...curve.map(p => p.equity), 3000000);
  const max = Math.max(...curve.map(p => p.equity), 3000000);
  return <div className="strong-panel">
    <h3>免費簡化價量回測</h3>
    <p>使用免費歷史日 K；不含營收、財報與產業條件，結果不代表完整強勢股策略。今日尚未完成的日 K 不納入。</p>
    <div className="strong-form">
      <label>開始日期<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
      <label>結束日期<input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
      <label>股票池（最多 30 檔）<input value={symbols} onChange={e => setSymbols(e.target.value)} placeholder="2330.TW,3693.TWO；留空使用預設 20 檔" /></label>
      <button disabled={submitting || job?.status === "RUNNING"} onClick={() => void submit()}>{submitting || job?.status === "RUNNING" ? "回測執行中…" : "執行免費回測"}</button>
      <label>歷次回測<select value={job?.id ?? ""} onChange={e => setJob(history.find(item => item.id === e.target.value) ?? null)}><option value="">選擇紀錄</option>{history.map(item => <option key={item.id} value={item.id}>{item.result.actualStartDate ?? item.id.slice(0, 8)} · {item.status === "COMPLETED" ? "完成" : item.status === "RUNNING" ? "執行中" : "未完成"}</option>)}</select></label>
    </div>
    <p>預設池：2330、2303、2454、2308、2317、2382、3231、3037、3034、2379、2408、2344、3008、3443、3532、3661、3711、3693、3105、6488。固定名單有選樣與存活者偏誤。</p>
    <p>初始資金 300 萬元；手續費 0.0285%（每筆最低 20 元）、賣出交易稅 0.3%、買賣各 5 bps 滑價。</p>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {job?.status === "RUNNING" && <p role="status">下載日 K 與計算中，通常需數分鐘。重新整理後可繼續查看進度。</p>}
    {job && job.status !== "RUNNING" && !r && <p role="alert">{job.result.message ?? "這筆紀錄沒有可用的歷史回測績效。"}</p>}
    {r && <>
      <p>實際區間：{r.actualStartDate} ～ {r.actualEndDate}；納入 {r.symbols?.length} 檔；期末未平倉 {r.openPositionCount} 檔。</p>
      <div className="strong-metrics compact">{([
        ["總損益（含息與成本）", format(r.totalPnl, " 元")], ["總報酬", format(r.totalReturnPct, "%")],
        ["勝率", format(r.winRate, "%")], ["最大回撤", format(r.maxDrawdownPct, "%")],
        ["完成交易", format(r.tradeCount)], ["0050 含息報酬（未扣成本）", format(r.benchmarkReturnPct, "%")],
      ]).map(([label, value]) => <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</div>
      {r.tradeCount === 0 && <p>此區間沒有完成交易，勝率無樣本；這不表示程式沒有計算。</p>}
      <h4>資金曲線</h4>
      <p>{format(min, " 元")} ～ {format(max, " 元")}</p>
      <svg viewBox="0 0 800 180" role="img" aria-label="回測每日權益曲線" style={{width: "100%", maxHeight: 220}}><polyline fill="none" stroke="currentColor" strokeWidth="2" points={curve.map((p, i) => `${i / Math.max(curve.length - 1, 1) * 800},${170 - (p.equity - min) / Math.max(max - min, 1) * 160}`).join(" ")} /></svg>
      <p>{r.rules}</p>
      <details><summary>資料限制與未納入股票</summary><ul>{[...(r.limitations ?? []), ...(r.excluded ?? [])].map(item => <li key={item}>{item}</li>)}</ul><p>實際股票池：{r.symbols?.join("、")}</p></details>
      <h4>交易明細</h4>
      <div style={{overflowX: "auto"}}><table><thead><tr>{["股票", "買進日", "賣出日", "股數", "買價", "賣價", "手續費＋稅", "股息", "淨損益", "出場原因"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{r.trades?.map((t, i) => <tr key={i}><td>{t.symbol}</td><td>{t.entryDate}</td><td>{t.exitDate}</td><td>{t.quantity}</td><td>{format(t.entryPrice)}</td><td>{format(t.exitPrice)}</td><td>{format(t.buyFee + t.sellFee + t.tax)}</td><td>{format(t.dividends)}</td><td>{format(t.netPnl)}</td><td>{t.exitReason}</td></tr>)}</tbody></table></div>
    </>}
  </div>;
}
