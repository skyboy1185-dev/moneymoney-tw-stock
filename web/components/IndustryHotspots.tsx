"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Building2, Flame, RefreshCw } from "lucide-react";
import { formatPercent, safeNumber, valueClass } from "@/lib/format";
import type { IndustryHotspot } from "@/services/content-service";

export function IndustryHotspots({ onSelectStock }: { onSelectStock: (symbol: string) => void }) {
  const [items, setItems] = useState<IndustryHotspot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sort, setSort] = useState<"change" | "momentum" | "count">("change");
  const [updatedAt, setUpdatedAt] = useState("");
  const [dataSource, setDataSource] = useState("");
  const [quoteStatus, setQuoteStatus] = useState<"intraday" | "official_close">("official_close");
  const [coverageRatio, setCoverageRatio] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/industries", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "產業資料載入失敗");
      setItems(payload.items ?? []);
      setUpdatedAt(payload.updatedAt ?? new Date().toISOString());
      setDataSource(payload.dataSource ?? "");
      setQuoteStatus(payload.quoteStatus ?? "official_close");
      setCoverageRatio(typeof payload.coverageRatio === "number" ? payload.coverageRatio : null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "產業資料載入失敗");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const sorted = useMemo(() => [...items].sort((a, b) =>
    sort === "momentum" ? b.momentum - a.momentum
      : sort === "count" ? b.stockCount - a.stockCount
        : b.changePercent - a.changePercent,
  ), [items, sort]);

  return (
    <div className="content-page">
      <div className="content-page-heading">
        <div><p className="section-kicker">SECTOR MOMENTUM</p><h1><Flame size={24} />產業熱點</h1><p>盤中依各產業高流動性代表股即時估算；收盤後使用 TWSE／TPEx 官方完整產業資料。</p></div>
        <button className="secondary-action" onClick={() => void load()} disabled={loading}><RefreshCw size={14} className={loading ? "spin-icon" : ""} />重新整理</button>
      </div>
      <div className="content-toolbar">
        <div className="content-tabs">
          <button className={sort === "change" ? "active" : ""} onClick={() => setSort("change")}>漲跌幅</button>
          <button className={sort === "momentum" ? "active" : ""} onClick={() => setSort("momentum")}>動能分數</button>
          <button className={sort === "count" ? "active" : ""} onClick={() => setSort("count")}>成分數量</button>
        </div>
        <span>{updatedAt ? `${quoteStatus === "intraday" ? "盤中即時" : "最近收盤"} ${new Date(updatedAt).toLocaleString("zh-TW", { hour12: false })}${quoteStatus === "intraday" && coverageRatio != null ? `・覆蓋 ${safeNumber(coverageRatio, 1)}%` : ""}` : "等待官方行情"}</span>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {loading && !items.length ? <div className="page-loading"><span className="spinner" /><p>正在計算產業動能…</p></div>
        : !sorted.length ? <div className="empty-state"><Building2 size={32} /><h2>暫無產業資料</h2><p>請稍後重新整理。</p></div>
          : <div className="industry-grid">{sorted.map((item, index) => (
            <article className="industry-card" key={item.industry}>
              <div className="industry-rank">#{index + 1}</div>
              <div className="industry-card-top"><div><span>{item.status}</span><h2>{item.industry}</h2></div>{item.changePercent >= 0 ? <ArrowUpRight className="text-up" /> : <ArrowDownRight className="text-down" />}</div>
              <strong className={valueClass(item.changePercent)}>{formatPercent(item.changePercent)}</strong>
              <div className="momentum-row"><span>動能 {safeNumber(item.momentum, 0)}</span><i><b style={{ width: `${item.momentum}%` }} /></i><span>{item.stockCount} 檔</span></div>
              <div className="industry-leaders">
                <span>領漲個股</span>
                {item.leaders.map((stock) => <button key={stock.symbol} onClick={() => onSelectStock(stock.symbol)}><b>{stock.symbol}</b>{stock.name}<small className={valueClass(stock.changePercent)}>{formatPercent(stock.changePercent)}</small></button>)}
              </div>
            </article>
          ))}</div>}
      <p className="content-disclaimer">資料來源：{dataSource || "TWSE／TPEx 官方資料"}。產業排行僅供研究參考，不構成投資建議。</p>
    </div>
  );
}
