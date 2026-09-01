"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, BarChart3, Bot, Fish, Flame, Landmark, Newspaper, Rocket, ScanSearch, Search, SlidersHorizontal, Telescope, TrendingUp, Waves, Wifi, WifiOff, Zap } from "lucide-react";
import { StockAnalysis } from "@/components/StockAnalysis";
import { Screener } from "@/components/Screener";
import { PortfolioPage } from "@/components/PortfolioPage";
import { IndustryHotspots } from "@/components/IndustryHotspots";
import { NewsPage } from "@/components/NewsPage";
import { InstitutionalInvestorsPage } from "@/components/InstitutionalInvestorsPage";
import { ChipFlowPage } from "@/components/ChipFlowPage";
import { DayTradingDashboard } from "@/components/day-trading/DayTradingDashboard";
import { ElectronicChipFlowTicker } from "@/components/ElectronicChipFlowTicker";
import { RobotHealthPanel } from "@/components/RobotHealthPanel";
import { TodayRobotNotificationsPanel } from "@/components/TodayRobotNotificationsPanel";
import { AdaptiveElectronicPage } from "@/components/AdaptiveElectronicPage";
import { LongTermSelectionPage } from "@/components/LongTermSelectionPage";
import { RocketRadarPage } from "@/components/RocketRadarPage";
import { LimitUpAiPage } from "@/components/LimitUpAiPage";
import { WhaleAccumulationPage } from "@/components/WhaleAccumulationPage";
import { PatternRobotPage } from "@/components/PatternRobotPage";
import { LegalTermsButton } from "@/components/LegalTermsGate";
import { PrivateSiteLogoutButton } from "@/components/PrivateSiteLogoutButton";
import type { MarketSnapshot } from "@/lib/market-types";
import {
  futuresFlashDirection,
  isFuturesQuoteDelayed,
  marketSnapshotFallbackPollMs,
  shouldFallbackRefreshMarketSnapshot,
  type FuturesFlashDirection,
} from "@/lib/market-snapshot-refresh";
import type { StockPayload } from "@/lib/types";

type Tab = "analysis" | "screener" | "day-trading" | "limit-up-ai" | "pattern-robot" | "adaptive-electronic" | "rocket-radar" | "long-term" | "whale-accumulation" | "institutional-investors" | "chip-flow" | "portfolio" | "industries" | "news";
type Connection = "connecting" | "connected" | "disconnected";
const VIEW_TABS: Tab[] = ["analysis", "screener", "day-trading", "limit-up-ai", "pattern-robot", "adaptive-electronic", "rocket-radar", "long-term", "whale-accumulation", "institutional-investors", "chip-flow", "portfolio", "industries", "news"];

function viewUrl(symbol: string, view: Tab): string {
  const params = new URLSearchParams({ symbol });
  if (view !== "analysis") params.set("view", view);
  return `?${params.toString()}`;
}

function resolveViewTab(view: string | null): Tab {
  if (view === "ai") return "adaptive-electronic";
  if (view === "limit-up-robot") return "limit-up-ai";
  return view && VIEW_TABS.includes(view as Tab) ? view as Tab : "analysis";
}

function formatFuturesNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 }).format(value);
}

function formatFuturesSigned(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${formatFuturesNumber(value)}`;
}

function quoteTimeLabel(quoteAt: string | null | undefined, updatedAt: string | null | undefined): string {
  if (quoteAt) return quoteAt.includes(" ") ? quoteAt.split(" ").pop() ?? quoteAt : quoteAt;
  if (!updatedAt) return "等待";
  return new Date(updatedAt).toLocaleTimeString("zh-TW", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Taipei",
  });
}

function FuturesLiveChip({ snapshot }: { snapshot: MarketSnapshot | null }) {
  const futures = snapshot?.context;
  const previousPrice = useRef<number | null>(null);
  const [flash, setFlash] = useState<FuturesFlashDirection>("");
  const [now, setNow] = useState(() => Date.now());
  const price = futures?.futuresPrice;
  const hasPrice = price != null && Number.isFinite(price) && price > 0;
  const delayed = hasPrice
    ? isFuturesQuoteDelayed({ now, quoteAt: futures?.futuresQuoteAt, updatedAt: snapshot?.updatedAt })
    : true;
  const status = !hasPrice || !snapshot?.futuresMarketOpen
    ? "waiting"
    : delayed
      ? "delayed"
      : "live";
  const statusLabel = status === "live" ? "LIVE" : status === "delayed" ? "延遲" : "等待夜盤";

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const direction = futuresFlashDirection(previousPrice.current, price);
    if (price != null && Number.isFinite(price)) previousPrice.current = price;
    if (!direction) return;
    setFlash(direction);
    const timer = window.setTimeout(() => setFlash(""), 1400);
    return () => window.clearTimeout(timer);
  }, [price, futures?.futuresQuoteAt]);

  return (
    <div
      className={`futures-live-chip status-${status}`}
      title={`台指期官方行情；報價時間 ${futures?.futuresQuoteAt ?? snapshot?.updatedAt ?? "等待資料"}`}
    >
      <span className="futures-live-label">
        <Activity size={12} />
        台指夜盤
        <b>{statusLabel}</b>
      </span>
      <strong className={flash ? `futures-price-flash-${flash}` : ""}>{hasPrice ? formatFuturesNumber(price) : "—"}</strong>
      <span className={(futures?.futuresChangePercent ?? 0) > 0 ? "up" : (futures?.futuresChangePercent ?? 0) < 0 ? "down" : ""}>
        {hasPrice ? `${formatFuturesSigned(futures?.futuresChange)}（${formatFuturesSigned(futures?.futuresChangePercent)}%）` : "行情待補"}
      </span>
      <time>{quoteTimeLabel(futures?.futuresQuoteAt, snapshot?.updatedAt)}</time>
    </div>
  );
}

async function fetchStock(query: string): Promise<StockPayload> {
  const response = await fetch(`/api/stocks?q=${encodeURIComponent(query)}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? "查詢失敗，請稍後再試。");
  return payload;
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("analysis");
  const tabRef = useRef<Tab>("analysis");
  const [query, setQuery] = useState("2330");
  const [stock, setStock] = useState<StockPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [autoMode] = useState(true);
  const [userId, setUserId] = useState("");
  const [rocketUnread, setRocketUnread] = useState(0);
  const [limitUpUnread, setLimitUpUnread] = useState(0);
  const snapshotRef = useRef<MarketSnapshot | null>(null);
  const lastMarketEventAtRef = useRef(0);
  const fallbackRefreshInFlightRef = useRef(false);

  useEffect(() => {
    tabRef.current = tab;
  }, [tab]);

  const loadStock = useCallback(async (keyword: string, preferredTab?: Tab) => {
    const normalized = keyword.trim();
    if (!normalized) { setError("請輸入股票代號或名稱。"); return; }
    setLoading(true);
    setError("");
    try {
      const result = await fetchStock(normalized);
      const nextTab = preferredTab ?? (tabRef.current === "limit-up-ai" ? "limit-up-ai" : "analysis");
      setStock(result);
      setQuery(result.meta.symbol);
      setTab(nextTab);
      tabRef.current = nextTab;
      window.history.replaceState(null, "", viewUrl(result.meta.symbol, nextTab));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "查詢失敗，請稍後再試。");
    } finally {
      setLoading(false);
    }
  }, []);

  const applyMarketSnapshot = useCallback((nextSnapshot: MarketSnapshot) => {
    snapshotRef.current = nextSnapshot;
    lastMarketEventAtRef.current = Date.now();
    setSnapshot(nextSnapshot);
    setConnection("connected");
  }, []);

  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("symbol") ?? "2330";
    const requestedView = new URLSearchParams(window.location.search).get("view");
    const initialTab = resolveViewTab(requestedView);
    setQuery(initial);
    tabRef.current = initialTab;
    void loadStock(initial, initialTab);
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
    lastMarketEventAtRef.current = Date.now();
    const source = new EventSource(`/api/stream?auto=${autoMode ? "1" : "0"}`);
    source.addEventListener("market", (event) => {
      applyMarketSnapshot(JSON.parse((event as MessageEvent).data) as MarketSnapshot);
    });
    source.onopen = () => setConnection("connected");
    source.onerror = () => setConnection("disconnected");
    return () => source.close();
  }, [applyMarketSnapshot, autoMode]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    const schedule = () => {
      if (stopped) return;
      const current = snapshotRef.current;
      const delay = current
        ? marketSnapshotFallbackPollMs({
            marketOpen: current.marketOpen,
            futuresMarketOpen: current.futuresMarketOpen,
          })
        : 30_000;
      timer = setTimeout(() => void tick(), delay);
    };
    const tick = async () => {
      const current = snapshotRef.current;
      const forceNightFuturesRefresh = Boolean(current?.futuresMarketOpen && !current.marketOpen);
      if (
        !fallbackRefreshInFlightRef.current
        && (
          forceNightFuturesRefresh
          || shouldFallbackRefreshMarketSnapshot({
            now: Date.now(),
            lastEventAt: lastMarketEventAtRef.current,
            hasSnapshot: Boolean(current),
            marketOpen: current?.marketOpen ?? false,
            futuresMarketOpen: current?.futuresMarketOpen ?? false,
          })
        )
      ) {
        fallbackRefreshInFlightRef.current = true;
        try {
          const response = await fetch(`/api/ai?auto=${autoMode ? "1" : "0"}&refresh=1`, {
            cache: "no-store",
            headers: userId ? { "x-user-id": userId } : undefined,
          });
          if (response.ok) {
            applyMarketSnapshot(await response.json() as MarketSnapshot);
          } else {
            setConnection("disconnected");
          }
        } catch {
          setConnection("disconnected");
        } finally {
          fallbackRefreshInFlightRef.current = false;
        }
      }
      schedule();
    };
    schedule();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [applyMarketSnapshot, autoMode, userId]);

  useEffect(() => {
    const loadUnread = async () => {
      try {
        const response = await fetch("/api/rocket-radar/notifications/unread", { cache: "no-store" });
        if (response.ok) setRocketUnread((await response.json()).count ?? 0);
      } catch { /* badge retries on the next interval */ }
    };
    void loadUnread();
    const timer = window.setInterval(() => void loadUnread(), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!userId) return;
    const loadLimitUpUnread = async () => {
      try {
        const response = await fetch("/api/limit-up-ai/notifications/unread", {
          cache: "no-store",
          headers: { "x-user-id": userId },
        });
        if (response.ok) setLimitUpUnread((await response.json()).count ?? 0);
      } catch { /* badge retries on the next interval */ }
    };
    void loadLimitUpUnread();
    const timer = window.setInterval(() => void loadLimitUpUnread(), 30_000);
    return () => window.clearInterval(timer);
  }, [userId]);

  const switchTab = useCallback((next: Tab) => {
    setTab(next);
    tabRef.current = next;
    window.history.replaceState(null, "", viewUrl(stock?.meta.symbol ?? query, next));
  }, [query, stock?.meta.symbol]);

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
        marketSnapshot={snapshot}
        onSelectStock={(symbol) => {
          setQuery(symbol);
          void loadStock(symbol);
        }}
      />
      <header className="topbar enhanced">
        <button className="brand" onClick={() => switchTab("analysis")} aria-label="回到個股分析">
          <span className="brand-icon"><BarChart3 size={20} /></span>
          <span><strong>Moneymoney</strong><small>台股分析</small></span>
        </button>
        <form className="search-form" onSubmit={(event) => { event.preventDefault(); void loadStock(query); }}>
          <Search size={17} aria-hidden="true" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="股票代號或名稱，例如 2330、台積電" aria-label="股票代號或名稱" />
          <button type="submit" disabled={loading}>{loading ? <span className="spinner small" /> : "查詢"}</button>
        </form>
        <div className="connection-panel">
          <FuturesLiveChip snapshot={snapshot} />
          <div className={`connection-state ${connection}`}>
            {connection === "connected" ? <Wifi size={14} /> : <WifiOff size={14} />}
            <span>{connectionLabel}</span>
          </div>
          <div className="global-update"><span>最後更新</span><strong>{snapshot ? new Date(snapshot.updatedAt).toLocaleTimeString("zh-TW", { hour12: false }) : "—"}</strong></div>
        </div>
      </header>

      <nav className="main-nav" aria-label="主要功能">
        <button className={tab === "analysis" ? "active" : ""} onClick={() => switchTab("analysis")}><Activity size={17} />個股分析</button>
        <button className={tab === "screener" ? "active" : ""} onClick={() => switchTab("screener")}><SlidersHorizontal size={17} />AI 選股</button>
        <button className={tab === "day-trading" ? "active ai-nav" : "ai-nav"} onClick={() => switchTab("day-trading")}><Bot size={17} />當沖機器人<span>LIVE</span></button>
        <button className={tab === "limit-up-ai" ? "active rocket-nav limit-up-nav" : "rocket-nav limit-up-nav"} onClick={() => switchTab("limit-up-ai")} aria-label="開啟專抓漲停飆股AI"><Zap size={17} />漲停機器人{limitUpUnread > 0 && <span className="rocket-unread-badge">{limitUpUnread > 99 ? "99+" : limitUpUnread}</span>}</button>
        <button className={tab === "pattern-robot" ? "active pattern-nav" : "pattern-nav"} onClick={() => switchTab("pattern-robot")}><ScanSearch size={17} />型態選股機器人</button>
        <button className={tab === "adaptive-electronic" ? "active" : ""} onClick={() => switchTab("adaptive-electronic")}><TrendingUp size={17} />超強AI當沖系統</button>
        <button className={tab === "rocket-radar" ? "active rocket-nav" : "rocket-nav"} onClick={() => switchTab("rocket-radar")}><Rocket size={17} />飆股雷達{rocketUnread > 0 && <span className="rocket-unread-badge">{rocketUnread > 99 ? "99+" : rocketUnread}</span>}</button>
        <button className={tab === "long-term" ? "active" : ""} onClick={() => switchTab("long-term")}><Telescope size={17} />長線選股</button>
        <button className={tab === "whale-accumulation" ? "active whale-nav" : "whale-nav"} onClick={() => switchTab("whale-accumulation")}><Fish size={17} />大戶偷掃貨</button>
        <button className={tab === "institutional-investors" ? "active" : ""} onClick={() => switchTab("institutional-investors")}><Landmark size={17} />三大法人</button>
        <button className={tab === "chip-flow" ? "active" : ""} onClick={() => switchTab("chip-flow")}><Waves size={17} />盤中籌碼</button>
        <button className={tab === "industries" ? "active" : ""} onClick={() => switchTab("industries")}><Flame size={17} />產業熱點</button>
        <button className={tab === "news" ? "active" : ""} onClick={() => switchTab("news")}><Newspaper size={17} />新聞</button>
      </nav>

      <RobotHealthPanel userId={userId} onOpen={switchTab} />
      <TodayRobotNotificationsPanel userId={userId} onOpen={switchTab} />

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
        ) : tab === "limit-up-ai" ? (
          <LimitUpAiPage userId={userId} />
        ) : tab === "pattern-robot" ? (
          <PatternRobotPage userId={userId} onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : tab === "adaptive-electronic" ? (
          <AdaptiveElectronicPage userId={userId} onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : tab === "rocket-radar" ? (
          <RocketRadarPage onUnreadChange={setRocketUnread} onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : tab === "long-term" ? (
          <LongTermSelectionPage onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
        ) : tab === "whale-accumulation" ? (
          <WhaleAccumulationPage onSelectStock={(symbol) => { setQuery(symbol); void loadStock(symbol); }} />
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
