"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Database, Landmark, RefreshCw } from "lucide-react";
import type { StockInstitutionalFlowResponse } from "@/lib/types";

function lots(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toLocaleString("zh-TW", { maximumFractionDigits: 2 })}`;
}

function valueClass(value: number) {
  return value > 0 ? "text-up" : value < 0 ? "text-down" : "";
}

export function StockInstitutionalFlow({ symbol }: { symbol: string }) {
  const [data, setData] = useState<StockInstitutionalFlowResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(
        `/api/stocks/${encodeURIComponent(symbol)}/institutional?t=${Date.now()}`,
        { cache: "no-store", signal },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "個股三大法人資料讀取失敗。");
      setData(payload);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "個股三大法人資料讀取失敗。");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
    <section className="stock-institutional-panel">
      <header>
        <div>
          <span className="section-kicker">DAILY INSTITUTIONAL FLOW</span>
          <h2><Landmark />近一個月每日三大法人買賣超</h2>
          <p>{symbol}・依交易日列出外資、投信與自營商淨買賣張數</p>
        </div>
        <button onClick={() => void load()} disabled={loading}>
          <RefreshCw className={loading ? "spinning" : ""} />更新
        </button>
      </header>

      {loading && !data ? (
        <div className="stock-institutional-state"><span className="spinner" />正在讀取官方個股法人資料…</div>
      ) : error && !data ? (
        <div className="stock-institutional-state error">
          <AlertCircle />
          <span>{error}</span>
          <button onClick={() => void load()}>重試</button>
        </div>
      ) : data ? (
        <>
          {error && <div className="stock-institutional-warning"><AlertCircle />{error}</div>}
          <div className="stock-institutional-summary">
            {([
              ["外資累計", data.totals.foreign],
              ["投信累計", data.totals.trust],
              ["自營商累計", data.totals.dealer],
              ["三大法人合計", data.totals.total],
            ] as const).map(([label, value]) => (
              <div key={label}>
                <span>{label}</span>
                <strong className={valueClass(value)}>{lots(value)} 張</strong>
              </div>
            ))}
          </div>
          <div className="stock-institutional-table-wrap">
            <table className="stock-institutional-table">
              <thead>
                <tr>
                  <th>交易日期</th>
                  <th>外資</th>
                  <th>投信</th>
                  <th>自營商</th>
                  <th>三大法人合計</th>
                </tr>
              </thead>
              <tbody>
                {[...data.items].reverse().map((item) => (
                  <tr key={item.date}>
                    <td>{item.date}</td>
                    <td className={valueClass(item.foreign)}>{lots(item.foreign)}</td>
                    <td className={valueClass(item.trust)}>{lots(item.trust)}</td>
                    <td className={valueClass(item.dealer)}>{lots(item.dealer)}</td>
                    <td className={valueClass(item.total)}><strong>{lots(item.total)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <footer>
            <span><Database />資料區間：{data.startDate}～{data.items.at(-1)?.date}・共 {data.items.length} 個交易日</span>
            <span>{data.notice}</span>
            <a href={data.sourceUrl} target="_blank" rel="noreferrer">來源：{data.source}</a>
          </footer>
        </>
      ) : null}
    </section>
  );
}
