"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Database, Search, Sparkles } from "lucide-react";
import { formatPercent, formatVolume, safeNumber, valueClass } from "@/lib/format";
import {
  CONFIRMED_MANUAL_STRATEGIES,
  DEDUCTION_MANUAL_STRATEGIES,
  DIVERGENCE_MANUAL_STRATEGIES,
  FORECAST_MANUAL_STRATEGIES,
  KD_MANUAL_STRATEGIES,
  MANUAL_STRATEGIES,
} from "@/lib/manual-strategies";
import type { ManualScreenRow, ManualStrategy } from "@/lib/market-types";

type SortKey = keyof ManualScreenRow;
const timeframeLabel = { day: "日 K", week: "週 K", month: "月 K" };

export function Screener({ onSelectStock }: { onSelectStock: (symbol: string) => void }) {
  const [selected, setSelected] = useState("day-macd");
  const [rows, setRows] = useState<ManualScreenRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; direction: "asc" | "desc" }>({ key: "rank", direction: "asc" });
  const [page, setPage] = useState(1);
  const selectedStrategy = MANUAL_STRATEGIES.find((strategy) => strategy.id === selected) ?? MANUAL_STRATEGIES[0];
  const isDeductionStrategy = selectedStrategy.deductionDirection != null;
  const isKdThresholdStrategy = selectedStrategy.signalMode === "kd-below";
  const isDivergenceStrategy = selectedStrategy.signalMode === "kd-bullish-divergence"
    || selectedStrategy.signalMode === "kd-double-bullish-divergence";
  const isDoubleDivergenceStrategy = selectedStrategy.signalMode === "kd-double-bullish-divergence";

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
  const commonColumns: [SortKey, string][] = [
    ["rank", "排名"], ["symbol", "股票代號"], ["name", "股票名稱"], ["market", "市場別"],
    ["price", "最新價格"], ["changePercent", "漲跌幅"], ["volume", "成交量"], ["timeframe", "K 線週期"],
  ];
  const columns: [SortKey, string][] = isDeductionStrategy
    ? [...commonColumns,
      ["signalMode", "扣抵型態"], ["maPeriod", "均線週期"], ["deductionValues", "未來 3 期扣抵值"],
      ["deductionAverage", "扣抵均值"], ["deductionGapPercent", "現價與均值差"],
      ["projectedMaValues", "推估 MA 走勢"], ["signalDate", "訊號日期"]]
    : isKdThresholdStrategy
      ? [...commonColumns, ["signalMode", "訊號型態"], ["k", "K 值"], ["d", "D 值"], ["signalDate", "訊號日期"]]
      : isDivergenceStrategy
        ? [...commonColumns, ["signalMode", "訊號型態"], ["divergencePreviousDate", "前低日期"],
          ...(isDoubleDivergenceStrategy ? [
            ["divergenceMiddleDate", "第二低日期"], ["divergenceMiddleLow", "第二低點"],
          ] as [SortKey, string][] : []),
          ["divergencePreviousLow", "第一次低點"], ["divergenceCurrentLow", "本次低點"],
          ["k", "本次 K"], ["d", "本次 D"], ["divergenceStrength", "背離強度"], ["signalDate", "訊號日期"]]
      : [...commonColumns,
      ["signalMode", "MACD 狀態"], ["estimatedBarsToCross", "預估翻紅"], ["dif", "DIF"], ["signal", "Signal"],
      ["histogram", "Histogram"], ["k", "K 值"], ["d", "D 值"], ["signalDate", "訊號日期"]];

  const renderStrategyGroup = (title: string, description: string, strategies: ManualStrategy[]) => (
    <div className={`strategy-group ${strategies[0]?.deductionDirection ? "deduction" : strategies[0]?.signalMode ?? ""}`}>
      <div className="strategy-group-heading"><strong>{title}</strong><span>{description}</span></div>
      <div className="fixed-strategy-grid">
        {strategies.map((strategy, index) => (
          <button key={strategy.id} className={selected === strategy.id ? "active" : ""} onClick={() => void run(strategy.id)}>
            <span>{index + 1}</span>
            <div><strong>{strategy.name}</strong><small>{timeframeLabel[strategy.timeframe]} · {strategy.deductionDirection
              ? `${strategy.maPeriod ?? 20} 期均線 · 未來 3 根扣${strategy.deductionDirection === "low" ? "低" : "高"}`
              : strategy.signalMode === "kd-below"
                ? `K、D 嚴格低於 ${strategy.kdThreshold ?? 8}`
                : strategy.signalMode === "kd-bullish-divergence"
                  ? `股價創低、KD 未創低 · 回看 ${strategy.divergenceLookback ?? 30} 根`
                : strategy.signalMode === "kd-double-bullish-divergence"
                  ? `三個價格低點下移、KD 三個低點墊高 · 回看 ${strategy.divergenceLookback ?? 45} 根`
                : `${strategy.signalMode === "forecast" ? "預測即將翻紅" : "MACD 已翻紅"}${strategy.requiresKD ? " · KD 低檔金叉" : ""}`}</small></div>
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="screener-page">
      <div className="page-heading">
        <div><p className="section-kicker">MANUAL STRATEGY SCREENER</p><h1>策略選股器</h1><p>一次選擇一個固定策略，所有條件由後端以相同指標規則計算。</p></div>
        <div className="demo-badge"><Database size={14} />官方歷史行情／盤中報價</div>
      </div>

      <section className="strategy-selector-panel">
        <div className="panel-title-line"><Sparkles size={17} /><div><h2>選擇選股策略</h2><p>點擊策略後立即執行篩選</p></div></div>
        {renderStrategyGroup("已確認翻紅", "原有 6 種嚴格翻紅策略", CONFIRMED_MANUAL_STRATEGIES)}
        {renderStrategyGroup("預測即將翻紅", "新增 6 種提前觀察策略", FORECAST_MANUAL_STRATEGIES)}
        {renderStrategyGroup("KD 極低檔", "日／週／月 K、D 同時嚴格低於 8，共 3 種策略", KD_MANUAL_STRATEGIES)}
        {renderStrategyGroup("低檔背離", "日 KD 一次背離與二度背離，共 2 種策略", DIVERGENCE_MANUAL_STRATEGIES)}
        {renderStrategyGroup("均線扣抵轉折", "日／週／月各有扣三低與扣三高，共 6 種模型", DEDUCTION_MANUAL_STRATEGIES)}
        <div className="strategy-definition">
          <strong>目前策略判斷：</strong>{selectedStrategy.deductionDirection === "low"
            ? "未來三根將從 20 期均線扣除的收盤價，全都嚴格低於目前收盤價；若後續價格維持現價附近，均線具上彎條件。"
            : selectedStrategy.deductionDirection === "high"
              ? "未來三根將從 20 期均線扣除的收盤價，全都嚴格高於目前收盤價；若後續價格維持現價附近，均線具下彎條件。"
              : selectedStrategy.signalMode === "kd-below"
                ? `${timeframeLabel[selectedStrategy.timeframe]}最新 K 值與 D 值必須同時嚴格低於 ${selectedStrategy.kdThreshold ?? 8}；任一數值等於或高於門檻都不入選。此策略不要求 MACD 翻紅。`
                : selectedStrategy.signalMode === "kd-bullish-divergence"
                  ? "近 3 根日 K 出現比前一次低點更低的價格，但 K、D 都比前低時墊高；兩次 KD 均須位於 30 以下，且兩個低點至少相隔 4 根 K。"
                : selectedStrategy.signalMode === "kd-double-bullish-divergence"
                  ? "價格依序形成三個更低低點，K、D 則依序形成三個更高低點；三次 KD 均須位於 30 以下，每個低點至少間隔 4 根 K。"
                : selectedStrategy.signalMode === "forecast"
                  ? "Histogram 仍在零軸下，但最近三根連續收斂，且依最近兩段改善速度推估 2 根 K 內翻紅。這是預測訊號，不代表已正式翻紅。"
                  : "MACD 前一根 Histogram < 0、當前 Histogram > 0，屬於已確認翻紅訊號。"}
          {selectedStrategy.requiresKD ? " 同時要求 KD 前 K < D、當前 K > D，且當前 K < 50。" : ""}
          {isDeductionStrategy
            ? " 結果會列出三個扣抵值與推估均線，這是均線結構篩選，不保證價格一定上漲或下跌。"
            : isKdThresholdStrategy
              ? " KD 極低檔代表動能超賣，不代表價格已止跌或一定反彈。"
              : isDivergenceStrategy
                ? isDoubleDivergenceStrategy
                  ? " 二度背離的結構較完整，但仍需等待價格或成交量確認。"
                  : " 一次背離是初步止跌線索，仍需等待價格或成交量確認。"
              : " 所有成交量門檻均採當日成交量。"}
        </div>
      </section>

      <section className="results-panel">
        <div className="results-header">
          <div><h2>選股結果</h2><span>{loading ? "策略計算中…" : `符合 ${rows.length} 檔股票`}</span></div>
          <button className="button primary" onClick={() => void run(selected)} disabled={loading}>{loading ? <span className="spinner small" /> : <Search size={15} />}開始篩選</button>
        </div>
        {loading ? <div className="table-loading"><span className="spinner" /><span>{isDeductionStrategy ? "正在重採樣 K 線並計算未來三期扣抵值…" : isDivergenceStrategy ? "正在比對近期價格低點與日 KD 低點…" : isKdThresholdStrategy ? `正在計算${timeframeLabel[selectedStrategy.timeframe]} KD…` : "正在重採樣 K 線並計算 MACD、KD…"}</span></div>
          : error ? <div className="table-empty"><Search size={27} /><h3>策略執行失敗</h3><p>{error}</p></div>
          : !rows.length ? <div className="table-empty"><Sparkles size={27} /><h3>目前沒有符合條件的股票</h3><p>{isDeductionStrategy
            ? `目前沒有未來三期扣抵值全數${selectedStrategy.deductionDirection === "low" ? "低於" : "高於"}現價的標的。`
            : selectedStrategy.signalMode === "kd-below" ? `目前沒有${timeframeLabel[selectedStrategy.timeframe]} K、D 同時低於 ${selectedStrategy.kdThreshold ?? 8} 的標的。`
            : selectedStrategy.signalMode === "kd-bullish-divergence" ? "目前沒有最近 3 根出現一次日 KD 低檔背離的標的。"
            : selectedStrategy.signalMode === "kd-double-bullish-divergence" ? "目前沒有最近 3 根完成日 KD 二度低檔背離的標的。"
            : selectedStrategy.signalMode === "forecast" ? "目前沒有同時符合連續收斂與兩根 K 內預估翻紅的標的。" : "這是正常的策略結果，可切換其他週期或 KD 策略。"}</p></div>
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
                  {isDeductionStrategy ? <>
                    <td><span className={`manual-signal-badge ${row.signalMode}`}>{row.signalMode === "deduction-low" ? "扣三低" : "扣三高"}</span></td>
                    <td>MA {row.maPeriod ?? 20}</td>
                    <td>{row.deductionValues?.map((value) => safeNumber(value, 2)).join("／") ?? "—"}</td>
                    <td>{safeNumber(row.deductionAverage, 2)}</td>
                    <td className={valueClass(row.deductionGapPercent ?? 0)}>{formatPercent(row.deductionGapPercent)}</td>
                    <td>{row.projectedMaValues?.map((value) => safeNumber(value, 2)).join(" → ") ?? "—"}</td>
                    <td>{row.signalDate}</td>
                  </> : isKdThresholdStrategy ? <>
                    <td><span className="manual-signal-badge kd-below">KD &lt; {selectedStrategy.kdThreshold ?? 8}</span></td>
                    <td>{safeNumber(row.k, 2)}</td><td>{safeNumber(row.d, 2)}</td><td>{row.signalDate}</td>
                  </> : isDivergenceStrategy ? <>
                    <td><span className={`manual-signal-badge ${selectedStrategy.signalMode}`}>{isDoubleDivergenceStrategy ? "二度底背離" : "一次底背離"}</span></td>
                    <td>{row.divergencePreviousDate ?? "—"}</td>
                    {isDoubleDivergenceStrategy && <><td>{row.divergenceMiddleDate ?? "—"}</td><td>{safeNumber(row.divergenceMiddleLow, 2)}</td></>}
                    <td>{safeNumber(row.divergencePreviousLow, 2)}</td>
                    <td>{safeNumber(row.divergenceCurrentLow, 2)}</td>
                    <td>{safeNumber(row.k, 2)}</td><td>{safeNumber(row.d, 2)}</td>
                    <td>{safeNumber(row.divergenceStrength, 2)}</td><td>{row.signalDate}</td>
                  </> : <>
                    <td><span className={`manual-signal-badge ${row.signalMode}`}>{row.signalMode === "forecast" ? "預測" : "已翻紅"}</span></td>
                    <td>{row.estimatedBarsToCross == null ? "—" : `約 ${safeNumber(row.estimatedBarsToCross, 1)} 根`}</td>
                    <td>{safeNumber(row.dif, 3)}</td><td>{safeNumber(row.signal, 3)}</td>
                    <td className={row.histogram != null && row.histogram < 0 ? "text-down" : "text-up"}>{safeNumber(row.histogram, 3)}</td>
                    <td>{safeNumber(row.k, 2)}</td><td>{safeNumber(row.d, 2)}</td><td>{row.signalDate}</td>
                  </>}
                </tr>)}</tbody>
              </table>
            </div>
            <div className="pagination"><span>每頁 20 筆 · 第 {page} / {pageCount} 頁</span><div><button disabled={page === 1} onClick={() => setPage(page - 1)}><ChevronLeft size={17} /></button><button disabled={page === pageCount} onClick={() => setPage(page + 1)}><ChevronRight size={17} /></button></div></div>
          </>}
      </section>
    </div>
  );
}
