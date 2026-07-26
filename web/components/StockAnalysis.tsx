"use client";

import { useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Clock3, RefreshCw } from "lucide-react";
import { AnalysisSidebar } from "./AnalysisSidebar";
import { LeaderPowerPanel } from "./LeaderPowerPanel";
import { StockChart } from "./StockChart";
import { formatPercent, formatVolume, safeNumber, valueClass } from "@/lib/format";
import { calculatePowerScore } from "@/lib/power-score";
import type { StockPayload } from "@/lib/types";

export function StockAnalysis({
  data,
  loading,
  marketOpen = false,
  onSelectStock,
}: {
  data: StockPayload;
  loading: boolean;
  marketOpen?: boolean;
  onSelectStock?: (symbol: string) => void;
}) {
  const [range, setRange] = useState("120d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const latest = data.prices.at(-1)!;
  const previous = data.prices.at(-2)!;
  const change = latest.close - previous.close;
  const changePercent = (change / previous.close) * 100;
  const powerScore = useMemo(
    () => calculatePowerScore(data.prices, data.indicators),
    [data.indicators, data.prices],
  );

  const visibleData = useMemo(() => {
    if (range === "custom") {
      const indexes = data.prices
        .map((price, index) => ({ date: price.date, index }))
        .filter((item) => (!customStart || item.date >= customStart) && (!customEnd || item.date <= customEnd));
      if (indexes.length) {
        const start = indexes[0].index;
        const end = indexes.at(-1)!.index + 1;
        return { prices: data.prices.slice(start, end), indicators: data.indicators.slice(start, end) };
      }
    }
    const count: Record<string, number> = { "30d": 30, "60d": 60, "120d": 120, "240d": 240 };
    const start = Math.max(0, data.prices.length - (count[range] ?? 120));
    return {
      prices: data.prices.slice(start),
      indicators: data.indicators.slice(start),
    };
  }, [customEnd, customStart, data, range]);

  const quoteItems = [
    ["成交量", formatVolume(latest.volume)],
    ["開盤", safeNumber(latest.open)],
    ["最高", safeNumber(latest.high)],
    ["最低", safeNumber(latest.low)],
    ["昨收", safeNumber(previous.close)],
  ];

  return (
    <div className={loading ? "content-updating" : ""}>
      {loading && <div className="updating-pill"><RefreshCw size={13} className="spin-icon" /> 資料更新中</div>}
      <section className="quote-card">
        <div className="quote-identity">
          <div className="stock-avatar">{data.meta.name.slice(0, 1)}</div>
          <div>
            <div className="stock-title-row">
              <h1>{data.meta.name}</h1>
              <span className="market-badge">{data.meta.market}</span>
              <span className={data.dataMode === "official_history" || data.quote ? "official-badge" : "demo-mini-badge"}>
                {data.dataMode === "official_history"
                  ? `${data.quote?.isRealtime ? "官方盤中" : "官方收盤"} · ${data.dataQuality?.quoteSource ?? data.quote?.source ?? "TWSE／TPEx"}`
                  : data.quote ? `${data.quote.isRealtime ? "官方準即時" : "官方收盤"} · ${data.quote.source}` : "展示資料"}
              </span>
              <span className="power-mini-badge">馬力 {powerScore.powerValue}/17 · {powerScore.starLabel}</span>
            </div>
            <p>{data.meta.symbol} · {data.meta.industry}</p>
          </div>
        </div>
        <div className="price-block">
          <strong>{safeNumber(latest.close)}</strong>
          <span className={valueClass(change)}>
            {change >= 0 ? <ArrowUpRight size={17} /> : <ArrowDownRight size={17} />}
            {change >= 0 ? "+" : ""}{safeNumber(change)}　{formatPercent(changePercent)}
          </span>
        </div>
        <div className="quote-stats">
          {quoteItems.map(([label, value]) => (
            <div key={label}><span>{label}</span><strong>{value}</strong></div>
          ))}
        </div>
        <div className="update-time">
          <Clock3 size={13} />
          {data.dataMode === "official_history" ? "官方行情與歷史資料" : data.quote ? "官方報價" : "展示資料"}更新於{" "}
          {new Date(data.updatedAt).toLocaleString("zh-TW", { hour12: false })}
        </div>
      </section>
      {data.dataNotice && <div className={data.dataMode === "official_history" || data.quote ? "quote-data-notice official" : "quote-data-notice"}>{data.dataNotice}</div>}

      <LeaderPowerPanel
        currentSymbol={data.meta.symbol}
        currentName={data.meta.name}
        score={powerScore}
        onSelectStock={onSelectStock}
      />

      <div className="workspace-grid">
        <section className="chart-card">
          <div className="chart-card-header">
            <div>
              <p className="section-kicker">PRICE ACTION</p>
              <h2>日 K 線・成交量・MACD</h2>
            </div>
            <div className="range-tabs" aria-label="時間區間">
              {[["30d", "30 日"], ["60d", "60 日"], ["120d", "120 日"], ["240d", "240 日"], ["custom", "自訂日期"]].map(([value, label]) => (
                <button key={value} className={range === value ? "active" : ""} onClick={() => setRange(value)}>{label}</button>
              ))}
            </div>
          </div>
          {range === "custom" && (
            <div className="custom-date-range">
              <label>開始日期<input type="date" value={customStart} min={data.prices[0]?.date} max={customEnd || data.prices.at(-1)?.date} onChange={(event) => setCustomStart(event.target.value)} /></label>
              <span>至</span>
              <label>結束日期<input type="date" value={customEnd} min={customStart || data.prices[0]?.date} max={data.prices.at(-1)?.date} onChange={(event) => setCustomEnd(event.target.value)} /></label>
              <small>目前顯示 {visibleData.prices.length} 個交易日</small>
            </div>
          )}
          <StockChart
            prices={visibleData.prices}
            indicators={visibleData.indicators}
            analysisPrices={data.prices}
            analysisIndicators={data.indicators}
            marketOpen={marketOpen}
          />
        </section>
        <AnalysisSidebar data={data} marketOpen={marketOpen} />
      </div>
    </div>
  );
}
