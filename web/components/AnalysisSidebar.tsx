import { Activity, CircleDollarSign, Gauge, Layers3 } from "lucide-react";
import { formatMarketCap, formatPercent, safeNumber, valueClass } from "@/lib/format";
import type { StockPayload } from "@/lib/types";

function Row({ label, value, className = "" }: { label: string; value: React.ReactNode; className?: string }) {
  return <div className="analysis-row"><span>{label}</span><strong className={className}>{value}</strong></div>;
}

export function AnalysisSidebar({ data, marketOpen = false }: { data: StockPayload; marketOpen?: boolean }) {
  const price = data.prices.at(-1)!;
  const indicator = data.indicators.at(-1)!;
  const latestSignal = [...data.indicators].reverse().find((item) => item.macdSignal);
  const signalPrice = latestSignal
    ? data.prices.find((item) => item.date === latestSignal.date)?.close ?? null
    : null;
  const sinceSignal = signalPrice ? ((price.close - signalPrice) / signalPrice) * 100 : null;

  const above = (ma: number | null) => ma == null ? "暫無資料" : price.close >= ma ? "之上" : "之下";
  const alignment = indicator.ma5 != null && indicator.ma20 != null && indicator.ma60 != null
    ? indicator.ma5 > indicator.ma20 && indicator.ma20 > indicator.ma60
      ? "多頭排列"
      : indicator.ma5 < indicator.ma20 && indicator.ma20 < indicator.ma60 ? "空頭排列" : "均線糾結"
    : "暫無資料";
  const trend = indicator.histogram == null || indicator.dif == null || indicator.signal == null
    ? "暫無資料"
    : indicator.histogram > 0 && indicator.dif > indicator.signal ? "偏多" : indicator.histogram < 0 && indicator.dif < indicator.signal ? "偏空" : "盤整";

  const recent = data.prices.slice(-60);
  const highs = [...recent].sort((a, b) => b.high - a.high);
  const lows = [...recent].sort((a, b) => a.low - b.low);
  const resistance1 = highs.find((item) => item.high >= price.close)?.high ?? indicator.ma20;
  const resistance2 = highs.find((item) => resistance1 != null && item.high > resistance1 * 1.015)?.high ?? highs[0]?.high;
  const support1 = lows.find((item) => item.low <= price.close)?.low ?? indicator.ma20;
  const support2 = lows.find((item) => support1 != null && item.low < support1 * 0.985)?.low ?? lows[0]?.low;

  const isTemporary = marketOpen && latestSignal?.date === price.date;
  const signalLabel = isTemporary
    ? `盤中暫時${latestSignal?.macdSignal === "entry" ? "進場" : "出場"}`
    : latestSignal?.macdSignal === "entry" ? "進場" : latestSignal?.macdSignal === "exit" ? "出場" : "觀望";
  const signalClass = isTemporary ? "signal-temporary" : latestSignal?.macdSignal === "entry" ? "signal-entry" : latestSignal?.macdSignal === "exit" ? "signal-exit" : "signal-neutral";

  return (
    <aside className="analysis-sidebar">
      <article className="analysis-card">
        <div className="analysis-card-title"><span className="icon-wrap purple"><Gauge size={17} /></span><h3>目前訊號</h3><span className={`signal-badge ${signalClass}`}>{signalLabel}</span></div>
        <Row label="最新訊號日期" value={latestSignal?.date ?? "暫無資料"} />
        <Row label="訊號價格" value={safeNumber(signalPrice)} />
        <Row label="目前股價" value={safeNumber(price.close)} />
        <Row label="訊號後漲跌" value={sinceSignal == null ? "暫無資料" : formatPercent(sinceSignal)} className={sinceSignal == null ? "" : valueClass(sinceSignal)} />
      </article>

      <article className="analysis-card">
        <div className="analysis-card-title"><span className="icon-wrap blue"><Activity size={17} /></span><h3>技術趨勢</h3><span className={`trend-chip ${trend === "偏多" ? "up-bg" : trend === "偏空" ? "down-bg" : ""}`}>{trend}</span></div>
        <div className="ma-position-grid">
          {[["MA5", above(indicator.ma5)], ["MA20", above(indicator.ma20)], ["MA60", above(indicator.ma60)]].map(([label, value]) => (
            <div key={label}><span>{label}</span><strong className={value === "之上" ? "text-up" : value === "之下" ? "text-down" : ""}>{value}</strong></div>
          ))}
        </div>
        <Row label="均線排列" value={alignment} />
        <Row label="MACD 柱狀圖" value={indicator.histogram == null ? "暫無資料" : indicator.histogram >= 0 ? "紅柱" : "綠柱"} className={indicator.histogram == null ? "" : indicator.histogram >= 0 ? "text-up" : "text-down"} />
        <Row label="DIF / Signal" value={indicator.dif == null || indicator.signal == null ? "暫無資料" : indicator.dif >= indicator.signal ? "DIF 高於 Signal" : "DIF 低於 Signal"} />
      </article>

      <article className="analysis-card">
        <div className="analysis-card-title"><span className="icon-wrap orange"><Layers3 size={17} /></span><h3>支撐與壓力</h3></div>
        <div className="levels">
          <div><span>第二壓力</span><strong className="text-up">{safeNumber(resistance2)}</strong></div>
          <div><span>第一壓力</span><strong className="text-up">{safeNumber(resistance1)}</strong></div>
          <div className="current-level"><span>目前股價</span><strong>{safeNumber(price.close)}</strong></div>
          <div><span>第一支撐</span><strong className="text-down">{safeNumber(support1)}</strong></div>
          <div><span>第二支撐</span><strong className="text-down">{safeNumber(support2)}</strong></div>
        </div>
      </article>

      <article className="analysis-card">
        <div className="analysis-card-title"><span className="icon-wrap green"><CircleDollarSign size={17} /></span><h3>基本資料</h3></div>
        <Row label="本益比" value={safeNumber(data.meta.peRatio)} />
        <Row label="殖利率" value={data.meta.dividendYield == null ? "暫無資料" : `${safeNumber(data.meta.dividendYield)}%`} />
        <Row label="股價淨值比" value={safeNumber(data.meta.priceToBook)} />
        <Row label="EPS" value={safeNumber(data.meta.eps)} />
        <Row label="市值" value={formatMarketCap(data.meta.marketCap)} />
        <Row label="產業類別" value={data.meta.industry || "暫無資料"} />
      </article>
    </aside>
  );
}
