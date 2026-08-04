"use client";

import { useEffect, useState } from "react";
import { Search, Waves } from "lucide-react";
import { ChipFlowPanel } from "./ChipFlowPanel";

export function ChipFlowPage({ initialSymbol = "2330" }: { initialSymbol?: string }) {
  const [draft, setDraft] = useState(initialSymbol);
  const [symbol, setSymbol] = useState(initialSymbol);
  const [stockName, setStockName] = useState("");
  const [validation, setValidation] = useState("");
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (/^\d{4,6}$/.test(initialSymbol)) {
      setDraft(initialSymbol);
      setSymbol(initialSymbol);
    }
  }, [initialSymbol]);

  useEffect(() => {
    if (!/^\d{4,6}$/.test(symbol)) return;
    const controller = new AbortController();
    setStockName("");
    void fetch(`/api/stocks/lookup?q=${encodeURIComponent(symbol)}`, {
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) return;
      const payload = await response.json() as { symbol?: string; name?: string };
      if (payload.symbol === symbol && payload.name) setStockName(payload.name);
    }).catch((error: unknown) => {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setStockName("");
      }
    });
    return () => controller.abort();
  }, [symbol]);

  return (
    <div className="chip-flow-page">
      <section className="chip-flow-page-hero">
        <div>
          <span>INTRADAY CHIP FLOW</span>
          <h1><Waves />盤中大戶／散戶累積買賣超</h1>
          <p>輸入股票代號或名稱，查看「即時大單買賣超」與「即時小單買賣超」推估資料。</p>
        </div>
        <form onSubmit={async (event) => {
          event.preventDefault();
          const normalized = draft.trim();
          if (!normalized) {
            setValidation("請輸入股票代號或名稱。");
            return;
          }
          setSearching(true);
          setValidation("");
          try {
            const response = await fetch(
              `/api/stocks/lookup?q=${encodeURIComponent(normalized)}`,
            );
            const payload = await response.json() as {
              symbol?: string;
              name?: string;
              error?: string;
            };
            if (!response.ok || !payload.symbol) {
              setValidation(payload.error ?? `找不到「${normalized}」。`);
              return;
            }
            if (!/^\d{4,6}$/.test(payload.symbol)) {
              setValidation(`「${payload.name ?? normalized}」目前不支援盤中籌碼查詢。`);
              return;
            }
            setDraft(payload.symbol);
            setSymbol(payload.symbol);
            setStockName(payload.name ?? "");
          } catch {
            setValidation("股票名稱查詢暫時無法連線，請稍後再試。");
          } finally {
            setSearching(false);
          }
        }}>
          <Search />
          <input
            value={draft}
            inputMode="search"
            aria-label="股票代號或名稱"
            placeholder="例如 2308 或台達電"
            onChange={(event) => {
              setDraft(event.target.value);
              if (validation) setValidation("");
            }}
          />
          <button type="submit" disabled={searching}>
            {searching ? "搜尋中…" : "查詢"}
          </button>
          {validation && <small>{validation}</small>}
        </form>
      </section>
      <ChipFlowPanel stockId={symbol} stockName={stockName} />
    </div>
  );
}
