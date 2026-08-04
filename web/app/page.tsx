"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, BarChart3, Bot, Flame, Landmark, Newspaper, Search, SlidersHorizontal, Star, TrendingUp, UsersRound, Waves, Wifi, WifiOff } from "lucide-react";
import { StockAnalysis } from "@/components/StockAnalysis";
import { Screener } from "@/components/Screener";
import { PortfolioPage } from "@/components/PortfolioPage";
import { IndustryHotspots } from "@/components/IndustryHotspots";
import { NewsPage } from "@/components/NewsPage";
import { LargeHolderRankingPage } from "@/components/LargeHolderRankingPage";
import { InstitutionalInvestorsPage } from "@/components/InstitutionalInvestorsPage";
import { ChipFlowPage } from "@/components/ChipFlowPage";
import { DayTradingDashboard } from "@/components/day-trading/DayTradingDashboard";
import { ElectronicChipFlowTicker } from "@/components/ElectronicChipFlowTicker";
import { AdaptiveElectronicPage } from "@/components/AdaptiveElectronicPage";
import { LegalTermsButton } from "@/components/LegalTermsGate";
import { PrivateSiteLogoutButton } from "@/components/PrivateSiteLogoutButton";
import type { MarketSnapshot } from "@/lib/market-types";
import type { StockPayload } from "@/lib/types";

type Tab = "analysis" | "screener" | "day-trading" | "adaptive-electronic" | "large-holders" | "institutional-investors" | "chip-flow" | "portfolio" | "industries" | "news";
type Connection = "connecting" | "connected" | "disconnected";

async function fetchStock(query: string): Promise<StockPayload> {
  const response = await fetch(`/api/stocks?q=${encodeURIComponent(query)}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? "查詢失敗，請稍後再試。");
  return payload;
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("analysis");
  const [query, setQuery] = useState("2330");
  const [stock, setStock] = useState<StockPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [autoMode] = useState(true);
  const [userId, setUserId] = useState("");

  const loadStock = useCallback(async (keyword: string) => {
    const normalized = keyword.trim();
    if (!normalized) { setError("請輸入股票代號或名稱。"); return; }
    setLoading(true);
    setError("");
    try {
      const result = await fetchStock(normalized);
      setStock(result);
      setQuery(result.meta.symbol);
      setTab("analysis");
      window.history.replaceState(null, "", `?symbol=${result.meta.symbol}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "查詢失敗，請稍後再試。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("symbol") ?? "2330";
    const requestedView = new URLSearchParams(window.location.search).get("view") as Tab | "ai" | null;
    setQuery(initial);
    void loadStock(initial).then(() => {
      if (requestedView === "ai") {
        setTab("adaptive-electronic");
      } else if (requestedView && ["analysis", "screener", "day-trading", "adaptive-electronic", "large-holders", "institutional-investors", "chip-flow", "portfolio", "industries", "news"].includes(requestedView)) {
        setTab(requestedView);
      }
    });
  }, [loadStock]);

  useEffect(() => {
    let id = localStorage.getItem("moneymoney-user-id");
    if (!id) {
      id = globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      localStorage.setItem("moneymoney-user-id", id);
    }
    setUserId(id);
  }, []);

  useEffect(() => {
    setConnection("connecting");
    const source = new EventSource(`/api/stream?auto=${autoMode ? "1" : "0"}`);
    source.addEventListener("market", (event) => {
      setSnapshot(JSON.parse((event as MessageEvent).data));
      setConnection("connected");
    });
    source.onopen = () => setConnection("connected");
    source.onerror = () => setConnection("disconnected");
    return () => source.close();
  }, [autoMode]);

  const connectionLabel = connection === "connected"
    ? snapshot?.marketOpen
      ? "行情已連線"
      : snapshot?.futuresMarketOpen
        ? "官方台指期夜盤已連線"
        : "已連線・非交易時間"
    : connection === "connecting" ? "連線中" : "行情連線中斷";

  return (
    <div className="app-shell">
      <ElectronicChipFlowTicker
        onSelectStock={(symbol) => {
          setQuery(symbol);
          void loadStock(symbol);
        }}
      />
      <header className="topbar enhanced">
        <button className="brand" onClick={() => setTab("analysis")} aria-label="回到個股分析">
          <span className="brand-icon"><BarChart3 size={20} /></span>
          <span><strong>Moneymoney</strong><small>台股分析</small></span>
        </button>
        <form className="search-form" onSubmit={(event) => { event.preventDefault(); void loadStock(query); }}>
          <Search size={17} aria-hidden="true" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="股票代號或名稱，例如 2330、台積電" aria-label="股票代號或名稱" />
          <button type="submit" disabled={loading}>{loading ? <span className="spinner small" /> : "查詢"}</button>
        </form>
        <div className="connection-panel">
          <div className={`connection-state ${connection}`}>
            {connection === "connected" ? <Wifi size={14} /> : <WifiOff size={14} />}
            <span>{connectionLabel}</span>
          </div>
          <div className="global-update"><span>最後更新</span><strong>{snapshot ? new Date(snapshot.updatedAt).toLocaleTimeString("zh-TW", { hour12: false }) : "—"}</strong></div>
        </div>
      </header>

      <nav className="main-nav" aria-label="主要功能">
        <button className={tab === "analysis" ? "active" : ""} onClick={() => setTab("analysis")}><Activity size={17} />個股分析</button>
        <button className={tab === "screener" ? "active" : ""} onClick={() => setTab("screener")}><SlidersHorizontal size={17} />AI 選股</button>
        <button className={tab === "day-trading" ? "active ai-nav" : "ai-nav"} onClick={() => setTab("day-trading")}><Bot size={17} />當沖機器人<span>LIVE</span></button>
        <button className={tab === "adaptive-electronic" ? "active" : ""} onClick={() => setTab("adaptive-electronic")}><TrendingUp size={17} />AI選股機器人</button>
        <button className={tab === "large-holders" ? "active" : ""} onClick={() => setTab("large-holders")}><UsersRound size={17} />大戶持股變化榜</button>
        <button className={tab === "institutional-investors" ? "active" : ""} onClick={() => setTab("institutional-investors")}><Landmark size={17} />三大法人</button>
        <button className={tab === "chip-flow" ? "active" : ""} onClick={() => setTab("chip-flow")}><Waves size={17} />盤中籌碼</button>
        <button className={tab === "portfolio" ? "active" : ""} onClick={() => setTab("portfolio")}><Star size={17} />觀察清單</button>
        <button className={tab === "industries" ? "active" : ""} onClick={() => setTab("industries")}><Flame size={17} />產業熱點</button>
        <button className={tab === "news" ? "active" : ""} onClick={() => setTab("news")}><Newspaper size={17} />新聞</button>
      </nav>

      <main className="main-content">
        {error && <div className="error-banner" role="alert">{error}</div>}
        {tab === "analysis" ? (
          loading && !stock ? <div className="page-loading"><span className="spinner" /><p>正在整理個股資料與技術指標…</p></div>
          : stock ? (
            <StockAnalysis
              data={stock}
              loading={loading}
              marketOpen={snapshot?.marketOpen ?? false}
              onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }}
            />
          )
          : <div className="empty-state"><Search size={30} /><h2>找不到股票資料</h2><p>請嘗試輸入其他股票代號或名稱。</p></div>
        ) : tab === "screener" ? (
          <Screener onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : tab === "day-trading" ? (
          <DayTradingDashboard />
        ) : tab === "adaptive-electronic" ? (
          <AdaptiveElectronicPage userId={userId} onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : tab === "large-holders" ? (
          <LargeHolderRankingPage userId={userId} onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : tab === "institutional-investors" ? (
          <InstitutionalInvestorsPage />
        ) : tab === "chip-flow" ? (
          <ChipFlowPage initialSymbol={stock?.meta.symbol ?? query} />
        ) : tab === "portfolio" ? (
          <PortfolioPage userId={userId} onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : tab === "industries" ? (
          <IndustryHotspots onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : (
          <NewsPage onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        )}
      </main>

      <footer>
        <span className="footer-primary">本網站資訊與選股結果僅供研究參考，不構成任何投資建議。</span>
        <span>個股最新報價優先使用官方市場資訊；歷史圖表與 AI 分析仍含展示資料</span>
        <LegalTermsButton />
        <PrivateSiteLogoutButton />
      </footer>
    </div>
  );
}
