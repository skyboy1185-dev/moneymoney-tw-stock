import { Activity, CircleDollarSign, Crosshair } from "lucide-react";
import { formatMarketCap, safeNumber } from "@/lib/format";
import { calculateThreeGatePrice } from "@/lib/three-gate-price";
import type { StockPayload } from "@/lib/types";

function Row({ label, value, className = "" }: { label: string; value: React.ReactNode; className?: string }) {
  return <div className="analysis-row"><span>{label}</span><strong className={className}>{value}</strong></div>;
}

export function AnalysisSidebar({ data, marketOpen = false }: { data: StockPayload; marketOpen?: boolean }) {
  const price = data.prices.at(-1)!;
  const indicator = data.indicators.at(-1)!;

  const above = (ma: number | null) => ma == null ? "暫無資料" : price.close >= ma ? "之上" : "之下";
  const alignment = indicator.ma5 != null && indicator.ma20 != null && indicator.ma60 != null
    ? indicator.ma5 > indicator.ma20 && indicator.ma20 > indicator.ma60
      ? "多頭排列"
      : indicator.ma5 < indicator.ma20 && indicator.ma20 < indicator.ma60 ? "空頭排列" : "均線糾結"
    : "暫無資料";
  const trend = indicator.histogram == null || indicator.dif == null || indicator.signal == null
    ? "暫無資料"
    : indicator.histogram > 0 && indicator.dif > indicator.signal ? "偏多" : indicator.histogram < 0 && indicator.dif < indicator.signal ? "偏空" : "盤整";

  const threeGate = calculateThreeGatePrice(data.prices, marketOpen);
  const threeGateState = !threeGate
    ? "資料不足"
    : price.close >= threeGate.upper
      ? "站上上關・偏多"
      : price.close >= threeGate.middle
        ? "中關之上・偏強"
        : price.close <= threeGate.lower
          ? "跌破下關・偏空"
          : "中關之下・偏弱";

  return (
    <aside className="analysis-sidebar">
      <article className="analysis-card">
        <div className="analysis-card-title">
          <span className="icon-wrap purple"><Crosshair size={17} /></span>
          <h3>三關價</h3>
          <span className={`three-gate-state ${threeGateState.includes("偏多") || threeGateState.includes("偏強") ? "bullish" : threeGateState.includes("偏空") || threeGateState.includes("偏弱") ? "bearish" : ""}`}>
            {threeGateState}
          </span>
        </div>
        <div className="levels">
          <div><span>上關價</span><strong className="text-up">{safeNumber(threeGate?.upper)}</strong></div>
          <div><span>中關價</span><strong>{safeNumber(threeGate?.middle)}</strong></div>
          <div className="current-level"><span>目前股價</span><strong>{safeNumber(price.close)}</strong></div>
          <div><span>下關價</span><strong className="text-down">{safeNumber(threeGate?.lower)}</strong></div>
        </div>
        <p className="analysis-source-note">
          {threeGate
            ? `${marketOpen ? "今日" : "下一交易日"}參考・依 ${threeGate.sourceDate} 高低價計算`
            : "需至少一個完整交易日資料"}
        </p>
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
        <div className="analysis-card-title"><span className="icon-wrap green"><CircleDollarSign size={17} /></span><h3>基本資料</h3></div>
        <Row label="本益比" value={safeNumber(data.meta.peRatio)} />
        <Row label="殖利率" value={data.meta.dividendYield == null ? "暫無資料" : `${safeNumber(data.meta.dividendYield)}%`} />
        <Row label="股價淨值比" value={safeNumber(data.meta.priceToBook)} />
        <Row label="近四季 EPS" value={safeNumber(data.meta.eps)} />
        <Row label="市值" value={formatMarketCap(data.meta.marketCap)} />
        <Row label="產業類別" value={data.meta.industry || "暫無資料"} />
        <p className="analysis-source-note">
          {data.meta.fundamentalsSource
            ? `${data.meta.fundamentalsSource}・${data.meta.fundamentalsDate ?? "最近交易日"}`
            : "交易所暫無可用基本資料"}
        </p>
      </article>
    </aside>
  );
}
