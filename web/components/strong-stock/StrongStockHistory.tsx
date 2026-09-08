"use client";

import { useState } from "react";
import { strongStockClient } from "@/services/strong-stock-client";

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
  async function check() {
    setBusy(true); setError(""); setCoverage(null);
    try { setCoverage(await strongStockClient.backtestCoverage(userId, start, end) as unknown as Coverage); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "資料範圍讀取失敗"); }
    finally { setBusy(false); }
  }
  return <article className="strong-panel">
    <h3>強勢股回測</h3>
    <p>僅針對目前強勢股策略的選股、買賣、加減碼與停損規則。</p>
    <div className="strong-form">
      <label>開始日期<input type="date" value={start} disabled={busy} onChange={e => { setStart(e.target.value); setCoverage(null); }} /></label>
      <label>結束日期<input type="date" value={end} disabled={busy} onChange={e => { setEnd(e.target.value); setCoverage(null); }} /></label>
      <button disabled={busy || !start || !end || end < start} onClick={() => void check()}>{busy ? "檢查中…" : "檢查可回測資料"}</button>
    </div>
    {error && <p role="alert">{error}</p>}
    <div role="status">
      <p><strong>目前尚無可用的正式策略回測績效。</strong></p>
      <p>{coverage?.missingSnapshotDates.length
        ? `所選區間缺少 ${coverage.missingSnapshotDates.length} 天的選股紀錄。`
        : "完整交易重播尚未完成，暫時無法計算損益。"}</p>
      {coverage && <details>
        <summary>查看資料缺漏</summary>
        <p>已保存選股紀錄：{coverage.firstSnapshotDate ?? "尚無"} ～ {coverage.lastSnapshotDate ?? "尚無"}</p>
        <ul>{coverage.missing.map(item => <li key={item}>{item}</li>)}</ul>
        <p>{coverage.calendarNote}</p>
      </details>}
    </div>
  </article>;
}
