"use client";

import { Crosshair } from "lucide-react";
import type { RankingRow } from "@/lib/market-types";
import { formatPercent, safeNumber, valueClass } from "@/lib/format";

const GATE_GROUPS = [
  { signal: "middle_breakout", title: "今日站上中關價", tone: "middle", empty: "目前沒有今日剛站上中關價的 AI 候選股" },
  { signal: "upper_breakout", title: "今日站上上關價", tone: "upper", empty: "目前沒有今日剛站上上關價的 AI 候選股" },
  { signal: "lower_breakdown", title: "今日跌破下關價", tone: "lower", empty: "目前沒有今日剛跌破下關價的 AI 候選股" },
] as const;

export function AIThreeGateScanner({ rows, updatedAt, onAnalyze }: {
  rows: RankingRow[];
  updatedAt: string;
  onAnalyze: (symbol: string) => void;
}) {
  const covered = rows.filter((row) => row.threeGate !== null).length;
  return <section className="ai-section ai-three-gate-panel" id="ai-three-gate-scanner">
    <div className="ai-section-title"><div><Crosshair size={18} /><div>
      <h2>三關價選股法</h2>
      <p>今日剛穿越才列入，三組不重複；資料覆蓋 {covered}/{rows.length} 檔</p>
    </div></div><span>更新 {new Date(updatedAt).toLocaleTimeString("zh-TW", { hour12: false })}</span></div>
    <div className="ai-three-gate-grid">{GATE_GROUPS.map((group) => {
      const matches = rows.filter((row) => row.threeGateSignal === group.signal);
      return <article className={`ai-three-gate-card ${group.tone}`} key={group.signal}>
        <header><strong>{group.title}</strong><span>{matches.length} 檔</span></header>
        {!matches.length ? <p className="ai-three-gate-empty">{group.empty}</p> : <div className="ai-three-gate-list">{matches.map((row) => {
          const level = group.signal === "upper_breakout" ? row.threeGate?.upper
            : group.signal === "middle_breakout" ? row.threeGate?.middle : row.threeGate?.lower;
          return <button key={row.symbol} onClick={() => onAnalyze(row.symbol)}>
            <span><b>{row.symbol}</b><small>{row.name}</small></span>
            <span><b>{safeNumber(row.price)}</b><small className={valueClass(row.changePercent)}>{formatPercent(row.changePercent)}</small></span>
            <span><b>關價 {safeNumber(level)}</b><small>{row.threeGateDistancePct == null ? "—" : `${row.threeGateDistancePct >= 0 ? "+" : ""}${row.threeGateDistancePct.toFixed(2)}%`}・{row.threeGate?.sourceDate}</small></span>
          </button>;
        })}</div>}
      </article>;
    })}</div>
    <p className="ai-three-gate-note">關價以前一交易日高低價計算；突破名單是技術位置提示，不代表建議直接追價。</p>
  </section>;
}
