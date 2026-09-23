"use client";

import { useEffect, useState } from "react";
import { Flame, RefreshCw } from "lucide-react";
import { topIndustryHotspots, type IndustryHotspot, type IndustryHotspotResponse } from "@/services/content-service";

function signed(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}
function price(value: number) {
  return value.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

export function StrongSectorBar({
  onSelectStock,
  onOpenIndustries,
}: {
  onSelectStock: (symbol: string) => void;
  onOpenIndustries: () => void;
}) {
  const [items, setItems] = useState<IndustryHotspot[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [meta, setMeta] = useState({ rankingStatus: "", updatedAt: "", tradeDate: "", rankingMethod: "", coverageCount: 0, targetCount: 0, validQuoteTime: "" });

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const response = await fetch("/api/industries", { cache: "no-store" });
        const payload = await response.json() as Partial<IndustryHotspotResponse> & { error?: string };
        if (!response.ok) throw new Error(payload.error ?? "強勢族群讀取失敗");
        if (!active) return;
        const ranked = topIndustryHotspots(payload.items ?? []);
        setItems(ranked);
        setMeta({
          rankingStatus: payload.rankingStatus ?? "collecting",
          updatedAt: payload.updatedAt ?? "",
          tradeDate: payload.tradeDate ?? "",
          rankingMethod: payload.rankingMethod ?? "",
          coverageCount: payload.coverageCount ?? 0,
          targetCount: payload.targetCount ?? 0,
          validQuoteTime: payload.validQuoteTime ?? "",
        });
        setStatus("ready");
      } catch {
        if (active) setStatus("error");
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const live = meta.rankingStatus === "live";
  const collecting = meta.rankingStatus === "collecting";
  return <aside className={`strong-sector-bar${collecting ? " stale-intraday" : ""}`} aria-label="今日當前強勢族群">
    <button className="strong-sector-heading" onClick={onOpenIndustries} title="開啟完整產業熱點">
      <Flame size={16} />
      <span><strong>今日當前強勢族群</strong><small>{collecting ? `目前涵蓋 ${meta.coverageCount}/${meta.targetCount}` : live ? `有效行情 ${meta.validQuoteTime}・涵蓋 ${meta.coverageCount}/${meta.targetCount}` : `${meta.tradeDate} 收盤`}</small></span>
    </button>
    <div className="strong-sector-list">
      {items.map((sector, index) => <section key={sector.industry}>
        <button className="strong-sector-name" onClick={onOpenIndustries}>
          <i>{index + 1}</i><strong>{sector.industry}</strong><b className={sector.changePercent >= 0 ? "up" : "down"}>強度 {sector.strengthScore.toFixed(1)}</b>
        </button>
        <div className="strong-sector-stocks">
          {sector.leaders.map((stock) => <button key={stock.symbol} onClick={() => onSelectStock(stock.symbol)} title={`${stock.tradeDate}｜${stock.source}｜收盤 ${price(stock.close)}｜漲跌 ${stock.change > 0 ? "+" : ""}${price(stock.change)}｜昨收 ${price(stock.previousClose)}｜${signed(stock.changePercent)}`}>
            <b>{stock.symbol}</b><span>{stock.name}</span><em className={stock.changePercent >= 0 ? "up" : "down"}>{signed(stock.changePercent)}</em>
          </button>)}
        </div>
        <small className="strong-sector-evidence" title={meta.rankingMethod}>平均 {signed(sector.changePercent)}・上漲 {sector.advanceRatio.toFixed(0)}%・{sector.stockCount} 檔</small>
      </section>)}
      {status === "loading" && <p><RefreshCw className="spin-icon" size={14} />正在計算前三名族群…</p>}
      {status === "error" && <p>強勢族群資料暫時無法取得</p>}
      {status === "ready" && !items.length && <p>{collecting ? "官方當日行情尚未達 80%，排名暫停" : "目前沒有足夠的族群行情"}</p>}
    </div>
    {meta.updatedAt && <time title={`TWSE／TPEx 官方資料｜${meta.rankingMethod}`}>{collecting ? "蒐集中" : "官方"}<br />{live || collecting ? `${meta.coverageCount}/${meta.targetCount}` : new Date(meta.updatedAt).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", hour12: false })}</time>}
  </aside>;
}
