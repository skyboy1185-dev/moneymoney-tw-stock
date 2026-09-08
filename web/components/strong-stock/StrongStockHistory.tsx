"use client";

import { useState } from "react";
import { strongStockClient } from "@/services/strong-stock-client";
import { StrongStockBacktest } from "./StrongStockBacktest";

type Coverage = {
  message: string; firstSnapshotDate: string | null; lastSnapshotDate: string | null;
  missingSnapshotDates: string[]; missing: string[]; calendarNote: string;
  snapshotDates: Array<{date: string; stockCount: number}>;
};

export function StrongStockHistory({userId}: {userId: string}) {
  const [start, setStart] = useState(() => new Date().toLocaleDateString("sv-SE", {timeZone: "Asia/Taipei"}).slice(0, 8) + "01");
  const [end, setEnd] = useState(() => new Date(Date.now() - 86400000).toISOString().slice(0, 10));
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showResearch, setShowResearch] = useState(false);
  async function check() {
    setBusy(true); setError(""); setCoverage(null);
    try { setCoverage(await strongStockClient.backtestCoverage(userId, start, end) as unknown as Coverage); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "資料範圍讀取失敗"); }
    finally { setBusy(false); }
  }
  return <>
    <article className="strong-panel">
      <h3>正式強勢股策略回測</h3>
      <p>目標是重播正式策略的選股、委託、加碼、減碼與停損規則。目前歷史資料與重播流程尚未齊全，無法提供正式策略的歷史績效。</p>
      <p><strong>尚未計算，不能解讀為「這段期間不會買股票」或「損益為零」。</strong></p>
      <div className="strong-form">
        <label>開始日期<input type="date" value={start} onChange={e => { setStart(e.target.value); setCoverage(null); }} /></label>
        <label>結束日期<input type="date" value={end} onChange={e => { setEnd(e.target.value); setCoverage(null); }} /></label>
        <button disabled={busy} onClick={() => void check()}>{busy ? "檢查中…" : "檢查正式策略歷史資料"}</button>
      </div>
      {error && <p role="alert">{error}</p>}
      {coverage && <div role="status">
        <p>已存選股快照：{coverage.firstSnapshotDate ?? "尚無"} ～ {coverage.lastSnapshotDate ?? "尚無"}</p>
        <p>{coverage.message}</p>
        <ul>{coverage.missing.map(item => <li key={item}>{item}</li>)}</ul>
        <details><summary>已保存的日期與股票數</summary><ul>{coverage.snapshotDates.map(row => <li key={row.date}>{row.date}：{row.stockCount} 檔</li>)}</ul><p>{coverage.calendarNote}</p></details>
      </div>}
    </article>
    <article className="strong-panel">
      <h3>另一套策略：免費價量研究</h3>
      <p>先前的免費價量版使用不同買賣規則。它的零交易與報酬率，都不能用來判斷正式強勢股策略。</p>
      <button onClick={() => setShowResearch(value => !value)}>{showResearch ? "收起價量研究" : "查看價量研究與舊紀錄"}</button>
      {showResearch && <StrongStockBacktest userId={userId} />}
    </article>
  </>;
}
