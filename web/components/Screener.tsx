"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Database, Search, Sparkles } from "lucide-react";
import { formatPercent, formatVolume, safeNumber, valueClass } from "@/lib/format";
import type { ManualScreenRow, ManualStrategy } from "@/lib/market-types";

const STRATEGIES: ManualStrategy[] = [
  { id: "day-macd", name: "日 K MACD 翻紅，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: false },
  { id: "week-macd", name: "週 K MACD 翻紅，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: false },
  { id: "month-macd", name: "月 K MACD 翻紅，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: false },
  { id: "day-macd-kd", name: "日 K MACD 翻紅且 KD 低檔金叉，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: true },
  { id: "week-macd-kd", name: "週 K MACD 翻紅且 KD 低檔金叉，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: true },
  { id: "month-macd-kd", name: "月 K MACD 翻紅且 KD 低檔金叉，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: true },
];

type SortKey = keyof ManualScreenRow;
const timeframeLabel = { day: "日 K", week: "週 K", month: "月 K" };

export function Screener({ onSelectStock }: { onSelectStock: (symbol: string) => void }) {
  const [selected, setSelected] = useState("day-macd");
  const [rows, setRows] = useState<ManualScreenRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; direction: "asc" | "desc" }>({ key: "rank", direction: "asc" });
  const [page, setPage] = useState(1);

  const run = useCallback(async (strategyId: string) => {
    setSelected(strategyId);
    setLoading(true);
    setError("");
    setPage(1);
    try {
      const response = await fetch(`/api/manual-screener?strategy=${strategyId}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "策略執行失敗");
      setRows(payload.rows);
    } catch (reason) {
      setRows([]);
      setError(reason instanceof Error ? reason.message : "選股服務暫時無法使用。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void run("day-macd"); }, [run]);

  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const left = a[sort.key];
    const right = b[sort.key];
    const compared = typeof left === "number" && typeof right === "number"
      ? left - right : String(left ?? "").localeCompare(String(right ?? ""), "zh-Hant");
    return sort.direction === "asc" ? compared : -compared;
  }), [rows, sort]);
  const pageCount = Math.max(1, Math.ceil(sorted.length / 20));
  const visible = sorted.slice((page - 1) * 20, page * 20);
  const toggleSort = (key: SortKey) => setSort((current) => ({
    key, direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
  }));
  const columns: [SortKey, string][] = [
    ["rank", "排名"], ["symbol", "股票代號"], ["name", "股票名稱"], ["market", "市場別"],
    ["price", "最新價格"], ["changePercent", "漲跌幅"], ["volume", "成交量"], ["timeframe", "K 線週期"],
    ["dif", "DIF"], ["signal", "Signal"], ["histogram", "Histogram"], ["k", "K 值"], ["d", "D 值"], ["signalDate", "訊號日期"],
  ];

  return (
    <div className="screener-page">
      <div className="page-heading">
        <div><p className="section-kicker">MANUAL STRATEGY SCREENER</p><h1>策略選股器</h1><p>一次選擇一個固定策略，所有條件由後端以相同指標規則計算。</p></div>
        <div className="demo-badge"><Database size={14} />官方歷史行情／盤中報價</div>
      </div>

      <section className="strategy-selector-panel">
        <div className="panel-title-line"><Sparkles size={17} /><div><h2>選擇選股策略</h2><p>點擊策略後立即執行篩選</p></div></div>
        <div className="fixed-strategy-grid">
          {STRATEGIES.map((strategy, index) => (
            <button key={strategy.id} className={selected === strategy.id ? "active" : ""} onClick={() => void run(strategy.id)}>
              <span>{index + 1}</span>
              <div><strong>{strategy.name}</strong><small>{timeframeLabel[strategy.timeframe]} · MACD 翻紅{strategy.requiresKD ? " · KD 低檔金叉" : ""}</small></div>
            </button>
          ))}
        </div>
        <div className="strategy-definition">
          <strong>策略判斷：</strong>MACD 前一根 Histogram &lt; 0、當前 Histogram &gt; 0；KD 金叉為前 K &lt; D、當前 K &gt; D，且當前 K &lt; 50。所有成交量門檻均採當日成交量。
        </div>
      </section>

      <section className="results-panel">
        <div className="results-header">
          <div><h2>選股結果</h2><span>{loading ? "策略計算中…" : `符合 ${rows.length} 檔股票`}</span></div>
          <button className="button primary" onClick={() => void run(selected)} disabled={loading}>{loading ? <span className="spinner small" /> : <Search size={15} />}開始篩選</button>
        </div>
        {loading ? <div className="table-loading"><span className="spinner" /><span>正在重採樣 K 線並計算 MACD、KD…</span></div>
          : error ? <div className="table-empty"><Search size={27} /><h3>策略執行失敗</h3><p>{error}</p></div>
          : !rows.length ? <div className="table-empty"><Sparkles size={27} /><h3>目前沒有符合條件的股票</h3><p>這是正常的策略結果，可切換其他週期或 KD 策略。</p></div>
          : <>
            <div className="table-scroll">
              <table className="screener-table manual-table">
                <thead><tr>{columns.map(([key, label]) => <th key={key}><button onClick={() => toggleSort(key)}>{label}<span className={sort.key === key ? "sorted" : ""}>{sort.key === key && sort.direction === "desc" ? "↓" : "↑"}</span></button></th>)}</tr></thead>
                <tbody>{visible.map((row) => <tr key={row.symbol}>
                  <td>{row.rank}</td>
                  <td><button className="symbol-link" onClick={() => onSelectStock(row.symbol)}>{row.symbol}</button></td>
                  <td><strong>{row.name}</strong></td><td>{row.market}</td><td>{safeNumber(row.price)}</td>
                  <td className={valueClass(row.changePercent)}>{formatPercent(row.changePercent)}</td>
                  <td>{formatVolume(row.volume)}</td><td>{timeframeLabel[row.timeframe]}</td>
                  <td>{safeNumber(row.dif, 3)}</td><td>{safeNumber(row.signal, 3)}</td>
                  <td className="text-up">{safeNumber(row.histogram, 3)}</td>
                  <td>{safeNumber(row.k, 2)}</td><td>{safeNumber(row.d, 2)}</td><td>{row.signalDate}</td>
                </tr>)}</tbody>
              </table>
            </div>
            <div className="pagination"><span>每頁 20 筆 · 第 {page} / {pageCount} 頁</span><div><button disabled={page === 1} onClick={() => setPage(page - 1)}><ChevronLeft size={17} /></button><button disabled={page === pageCount} onClick={() => setPage(page + 1)}><ChevronRight size={17} /></button></div></div>
          </>}
      </section>
    </div>
  );
}
