"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bell,
  ChevronDown,
  Clipboard,
  Download,
  History,
  Mail,
  RefreshCw,
  Search,
  Star,
} from "lucide-react";
type Message = {
  messageId: string;
  period: string;
  createdAt: string;
  dataTime: string;
  category: string;
  importance: string;
  title: string;
  content: string;
  marketImpact: string;
  industryImpact: string[];
  stockImpact: Array<{ symbol: string; name: string }>;
  operationAdvice: string;
  confidenceScore: number;
  sources: Array<{ name: string; dataTime?: string }>;
  isUpdate: boolean;
  parentMessageId?: string;
  markedImportant: boolean;
  followed: boolean;
};
type Day = {
  tradeDate: string;
  isTradingDay: boolean;
  status: string;
  currentPhase: string;
  latestMarketBias: string;
  marketScore: number | null;
  scoreBreakdown?: Record<string, number | null>;
  dataCompleteness?: number | null;
  riskLevel: string;
  openingPattern: string;
  strongIndustries: string[];
  weakIndustries: string[];
  dayTradeAdvice: string;
  stockAdvice: string;
  recommendedPosition: string;
  support: string[];
  resistance: string[];
  dataAsOf?: string;
  lastUpdatedAt: string;
  analysisStatus: string;
  error?: string;
  predictionResult?: string;
  predictionDetails?: {
    status?: string;
    reason?: string;
    items?: Array<{ name: string; predicted: string; actual: string; result: string }>;
  };
  preMarketMessageCount: number;
  postMarketMessageCount: number;
  preMarketMessages: Message[];
  postMarketMessages: Message[];
  marketCharts?: {
    international: Record<string, MarketQuote>;
    domestic: Record<string, MarketQuote>;
    updatedAt?: string;
    futuresNote: string;
  };
  postMarketCharts?: {
    indexes: Record<string, MarketQuote>;
    advanceRatio?: number | null;
    limitUpCount?: number | null;
    limitDownCount?: number | null;
    volumeRatio20d?: number | null;
    otcRelativeStrength?: boolean | null;
    dataTime?: string;
  };
  industryOutlook?: {
    dataDate?: string;
    items: IndustryOutlook[];
  };
};
type MarketQuote = {
  name: string;
  price?: number;
  previousClose?: number;
  changePct?: number;
  changePoints?: number;
  contract?: string;
  volume?: number;
  isRealtime?: boolean;
  dataTime?: string;
  source?: string;
};
type IndustryOutlook = {
  industry: string;
  score: number;
  percentile: number;
  memberCount: number;
  stance: string;
  catalysts: string[];
  risks: string[];
  confirmation: string;
};
type NotificationSettings = {
  preMarket: boolean;
  postMarket: boolean;
  importantEvents: boolean;
  emergencyEvents: boolean;
  mailEnabled: boolean;
  minimumImportance: string;
};
const statusLabel: Record<string, string> = {
  NOT_STARTED: "尚未開始",
  PRE_MARKET_ANALYZING: "盤前分析中",
  PRE_MARKET_COMPLETE: "盤前已完成",
  INTRADAY: "盤中",
  POST_MARKET_ANALYZING: "盤後分析中",
  FULL_DAY_COMPLETE: "全日分析已完成",
  MARKET_CLOSED: "台股休市",
  FAILED: "執行失敗",
};
function clock(v?: string) {
  if (!v) return "資料尚未取得";
  if (/^\d{8}$/.test(v)) return `${v.slice(0, 4)}/${v.slice(4, 6)}/${v.slice(6, 8)}`;
  const parsed = new Date(v);
  return Number.isNaN(parsed.getTime()) ? v : parsed.toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    hour12: false,
  });
}
function signed(value?: number) {
  return value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}
function ChangeBars({ title, items }: { title: string; items: MarketQuote[] }) {
  const usable = items.filter((item) => item.changePct != null);
  const maximum = Math.max(1, ...usable.map((item) => Math.abs(item.changePct ?? 0)));
  return <article className="prepost-chart-card">
    <h3>{title}</h3>
    {usable.map((item) => {
      const value = item.changePct ?? 0;
      return <div className="prepost-change-row" key={item.name} title={`${item.source ?? ""}｜${clock(item.dataTime)}`}>
        <span>{item.name}{item.contract ? <small>{item.contract}</small> : null}</span>
        <div className="prepost-change-track"><em /><i className={value >= 0 ? "up" : "down"} style={{ width: `${Math.max(2, Math.abs(value) / maximum * 50)}%` }} /></div>
        <b className={value > 0 ? "up" : value < 0 ? "down" : ""}>{signed(value)}</b>
      </div>;
    })}
    {!usable.length && <p className="empty-state">目前沒有可驗證的漲跌資料。</p>}
  </article>;
}
function ConfidenceBars({ messages }: { messages: Message[] }) {
  const latest = messages.slice(-7);
  return <article className="prepost-chart-card"><h3>盤後分析信心度</h3>
    {latest.map((item) => <div className="prepost-confidence-row" key={item.messageId}>
      <span>{item.title}</span><div><i style={{ width: `${Math.max(0, Math.min(100, item.confidenceScore))}%` }} /></div><b>{item.confidenceScore.toFixed(0)}</b>
    </div>)}
    {!latest.length && <p className="empty-state">盤後分析產生後會顯示各階段信心度。</p>}
  </article>;
}
function PostMarketStats({ data }: { data?: Day["postMarketCharts"] }) {
  const advanceRatio = data?.advanceRatio == null ? null : data.advanceRatio <= 1 ? data.advanceRatio * 100 : data.advanceRatio;
  const volumeRatio = data?.volumeRatio20d ?? null;
  const limitsMax = Math.max(1, data?.limitUpCount ?? 0, data?.limitDownCount ?? 0);
  const rows = [
    { label: "上漲家數比", value: advanceRatio, width: advanceRatio, display: advanceRatio == null ? "—" : `${advanceRatio.toFixed(1)}%` },
    { label: "成交量／20日均量", value: volumeRatio, width: volumeRatio == null ? null : Math.min(100, volumeRatio * 50), display: volumeRatio == null ? "—" : `${volumeRatio.toFixed(2)}x` },
    { label: "漲停家數", value: data?.limitUpCount, width: data?.limitUpCount == null ? null : data.limitUpCount / limitsMax * 100, display: data?.limitUpCount == null ? "—" : `${data.limitUpCount}` },
    { label: "跌停家數", value: data?.limitDownCount, width: data?.limitDownCount == null ? null : data.limitDownCount / limitsMax * 100, display: data?.limitDownCount == null ? "—" : `${data.limitDownCount}` },
  ];
  return <article className="prepost-chart-card"><h3>量能與市場廣度</h3>
    {rows.map((row) => <div className="prepost-confidence-row" key={row.label}><span>{row.label}</span><div><i className={row.label === "跌停家數" ? "down" : ""} style={{ width: `${Math.max(0, Math.min(100, row.width ?? 0))}%` }} /></div><b>{row.display}</b></div>)}
    {rows.every((row) => row.value == null) && <p className="empty-state">收盤市場廣度尚未入庫，完成盤後分析後會自動顯示。</p>}
    {data?.otcRelativeStrength != null && <p className="prepost-chart-note">櫃買相對加權：{data.otcRelativeStrength ? "較強" : "較弱"}</p>}
  </article>;
}
function PredictionValidation({ details, result }: { details?: Day["predictionDetails"]; result?: string }) {
  const items = details?.items ?? [];
  return <article className="prepost-chart-card"><h3>盤前預測驗證</h3>
    {items.map((item) => <div className="prepost-prediction-row" key={item.name}><span>{item.name}</span><small>預測 {item.predicted}</small><small>實際 {item.actual}</small><b className={item.result === "HIT" ? "hit" : "miss"}>{item.result === "HIT" ? "命中" : "未命中"}</b></div>)}
    {!items.length && <p className="empty-state">{details?.reason ?? "盤後收盤資料齊備後，會核對盤前方向與實際結果。"}</p>}
    {result && <p className="prepost-chart-note">整體驗證：{result}</p>}
  </article>;
}
const scoreLabels: Record<string, string> = { localMomentum: "台股動能", internationalRisk: "國際風險偏好", technologyChain: "半導體科技鏈", futures: "國際期貨", dataCompleteness: "資料完整度" };
function ScoreBreakdown({ scores }: { scores?: Record<string, number | null> }) {
  const items = Object.entries(scores ?? {}).filter((item): item is [string, number] => item[1] != null);
  return <article className="prepost-chart-card"><h3>AI 盤勢分數拆解</h3>{items.map(([key, value]) => <div className="prepost-confidence-row" key={key}><span>{scoreLabels[key] ?? key}</span><div><i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div><b>{value.toFixed(0)}</b></div>)}{!items.length && <p className="empty-state">尚無可拆解的評分資料。</p>}</article>;
}
async function api(path: string, userId: string, init?: RequestInit) {
  const r = await fetch(`/api/prepost-analysis${path}`, {
    ...init,
    headers: {
      "x-user-id": userId,
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.detail ?? body.error ?? "分析服務錯誤");
  return body;
}
function MessageCard({
  item,
  userId,
  onChanged,
}: {
  item: Message;
  userId: string;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(
    item.importance === "IMPORTANT" || item.importance === "EMERGENCY",
  );
  const patch = async (body: object) => {
    await api(`/messages/${item.messageId}`, userId, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    onChanged();
  };
  return (
    <article
      className={`prepost-message importance-${item.importance.toLowerCase()}`}
    >
      <header onClick={() => setOpen(!open)}>
        <strong className={`prepost-sentiment sentiment-${item.marketImpact}`}>
          {item.marketImpact || "中性"}
        </strong>
        <time>{clock(item.createdAt)}</time>
        <span>{item.category}</span>
        <b>{item.importance}</b>
        <h3>{item.title}</h3>
        <ChevronDown className={open ? "open" : ""} />
      </header>
      {open && (
        <div className="prepost-message-body">
          <small>
            資料截至 {clock(item.dataTime)}・AI 信心 {item.confidenceScore}分
          </small>
          <p>{item.content}</p>
          <dl>
            <div>
              <dt>台股影響</dt>
              <dd>{item.marketImpact}</dd>
            </div>
            <div>
              <dt>產業</dt>
              <dd>{item.industryImpact.join("、") || "資料尚未取得"}</dd>
            </div>
            <div>
              <dt>股票</dt>
              <dd>
                {item.stockImpact
                  .map((x) => `${x.symbol} ${x.name}`)
                  .join("、") || "資料尚未取得"}
              </dd>
            </div>
            <div>
              <dt>操作建議</dt>
              <dd>{item.operationAdvice}</dd>
            </div>
          </dl>
          <details>
            <summary>資料來源</summary>
            {item.sources.length ? (
              item.sources.map((x, i) => (
                <p key={i}>
                  {x.name}・{x.dataTime ?? "時間未提供"}
                </p>
              ))
            ) : (
              <p>資料尚未取得</p>
            )}
          </details>
          <footer>
            <button
              onClick={() =>
                navigator.clipboard.writeText(`${item.title}\n${item.content}`)
              }
            >
              <Clipboard />
              複製
            </button>
            <button
              onClick={() =>
                void patch({ marked_important: !item.markedImportant })
              }
            >
              <Star />
              標記重要
            </button>
            <button onClick={() => void patch({ followed: !item.followed })}>
              <Bell />
              加入追蹤
            </button>
            <button
              onClick={() =>
                void api(`/messages/${item.messageId}/mail`, userId, {
                  method: "POST",
                })
              }
            >
              <Mail />
              發送 Mail
            </button>
          </footer>
        </div>
      )}
    </article>
  );
}
export function PrePostAnalysisPage({ userId }: { userId: string }) {
  const today = new Date().toLocaleDateString("en-CA", {
    timeZone: "Asia/Taipei",
  });
  const [selected, setSelected] = useState(today);
  const [data, setData] = useState<Day | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [auto, setAuto] = useState(true);
  const [importance, setImportance] = useState("");
  const [period, setPeriod] = useState("");
  const [keyword, setKeyword] = useState("");
  const [notice, setNotice] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [popup, setPopup] = useState<Message | null>(null);
  const [historyRows, setHistoryRows] = useState<
    Array<{
      tradeDate: string;
      weekday: string;
      status: string;
      marketBias: string;
      marketScore: number | null;
      riskLevel: string;
      predictionResult?: string;
      preMarketMessageCount: number;
      postMarketMessageCount: number;
    }>
  >([]);
  const selectedRef = useRef(selected);
  const latestAlertRef = useRef("");
  const load = useCallback(
    async (day = selected) => {
      if (!userId) return;
      try {
        setError("");
        setData(await api(`/dashboard?trade_date=${day}`, userId));
      } catch (e) {
        setError(e instanceof Error ? e.message : "載入失敗");
      } finally {
        setLoading(false);
      }
    },
    [selected, userId],
  );
  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!userId) return;
    void api("/settings", userId).then(setSettings);
  }, [userId]);
  useEffect(() => {
    if (historyOpen && userId)
      void api(
        `/history?page_size=100&keyword=${encodeURIComponent(keyword)}`,
        userId,
      ).then((x) => setHistoryRows(x.items));
  }, [historyOpen, keyword, userId]);
  useEffect(() => {
    if (!auto || !userId) return;
    const source = new EventSource(
      `/api/prepost-analysis/stream?trade_date=${selected}`,
    );
    source.addEventListener("analysis", (e) => {
      const next = JSON.parse((e as MessageEvent).data);
      const alerts = [
        ...(next.preMarketMessages ?? []),
        ...(next.postMarketMessages ?? []),
      ].filter((item: Message) =>
        ["IMPORTANT", "EMERGENCY"].includes(item.importance),
      );
      const latest = alerts.at(-1) as Message | undefined;
      if (!latestAlertRef.current)
        latestAlertRef.current = latest?.messageId ?? "";
      else if (latest && latest.messageId !== latestAlertRef.current) {
        latestAlertRef.current = latest.messageId;
        setPopup(latest);
      }
      if (selectedRef.current === today) setData(next);
      else setNotice(true);
    });
    return () => source.close();
  }, [auto, selected, today, userId]);
  const messages = useMemo(
    () =>
      [...(data?.preMarketMessages ?? []), ...(data?.postMarketMessages ?? [])]
        .filter(
          (x) =>
            (!importance || x.importance === importance) &&
            (!period || x.period === period) &&
            (!keyword ||
              `${x.title}${x.content}${x.category}${JSON.stringify(x.stockImpact)}${x.industryImpact}`.includes(
                keyword,
              )),
        )
        .sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
    [data, importance, period, keyword],
  );
  const taipeiNow = new Date(
    new Date().toLocaleString("en-US", { timeZone: "Asia/Taipei" }),
  );
  const reportReady =
    selected !== today ||
    taipeiNow.getHours() > 15 ||
    (taipeiNow.getHours() === 15 && taipeiNow.getMinutes() >= 10);
  const preReportReady =
    selected !== today ||
    taipeiNow.getHours() > 8 ||
    taipeiNow.getHours() === 8;
  const move = (n: number) => {
    const d = new Date(`${selected}T12:00:00+08:00`);
    do d.setDate(d.getDate() + n);
    while ([0, 6].includes(d.getDay()));
    setSelected(d.toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" }));
  };
  if (loading && !data)
    return (
      <div className="page-loading">
        <span className="spinner" />
        <p>載入盤前盤後分析…</p>
      </div>
    );
  return (
    <div className="prepost-page">
      {error && <div className="error-banner">{error}</div>}
      {popup && (
        <aside
          className={`prepost-popup importance-${popup.importance.toLowerCase()}`}
          role="alertdialog"
        >
          <b>
            {popup.importance === "EMERGENCY" ? "重大市場事件" : "重要市場更新"}
          </b>
          <span>{popup.title}</span>
          <button onClick={() => setPopup(null)} aria-label="關閉通知">
            ×
          </button>
        </aside>
      )}
      <div className="prepost-mode">
        <button
          className={!historyOpen ? "active" : ""}
          onClick={() => setHistoryOpen(false)}
        >
          今日分析
        </button>
        <button
          className={historyOpen ? "active" : ""}
          onClick={() => setHistoryOpen(true)}
        >
          <History />
          歷史查詢
        </button>
      </div>
      <section className="prepost-toolbar">
        <div>
          <button onClick={() => move(-1)}>上一交易日</button>
          <input
            type="date"
            value={selected}
            onChange={(e) => {
              setSelected(e.target.value);
              setHistoryOpen(false);
            }}
          />
          <button onClick={() => move(1)}>下一交易日</button>
          <button
            onClick={() => {
              setSelected(today);
              setHistoryOpen(false);
            }}
          >
            回到今天
          </button>
        </div>
        <div>
          <label>
            <input
              type="checkbox"
              checked={auto}
              onChange={(e) => setAuto(e.target.checked)}
            />
            自動更新
          </label>
          <button onClick={() => void load()}>
            <RefreshCw />
            重新整理
          </button>
          <button
            onClick={async () => {
              setLoading(true);
              await api("/reanalyze", userId, { method: "POST" });
              await load();
            }}
          >
            <RefreshCw />
            重新分析
          </button>
          <a
            href={`/api/prepost-analysis/export?start_date=${selected}&end_date=${selected}&format=csv`}
          >
            <Download />
            CSV
          </a>
          <a
            href={`/api/prepost-analysis/export?start_date=${selected}&end_date=${selected}&format=excel`}
          >
            <Download />
            Excel
          </a>
        </div>
      </section>
      {settings && (
        <details className="prepost-settings">
          <summary>通知設定</summary>
          <div>
            {(
              [
                ["preMarket", "盤前通知"],
                ["postMarket", "盤後通知"],
                ["importantEvents", "重要事件"],
                ["emergencyEvents", "緊急事件"],
                ["mailEnabled", "Mail 通知"],
              ] as const
            ).map(([key, label]) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={settings[key]}
                  onChange={(event) =>
                    setSettings({ ...settings, [key]: event.target.checked })
                  }
                />
                {label}
              </label>
            ))}
            <label>
              通知門檻
              <select
                value={settings.minimumImportance}
                onChange={(event) =>
                  setSettings({
                    ...settings,
                    minimumImportance: event.target.value,
                  })
                }
              >
                <option value="NOTICE">注意</option>
                <option value="IMPORTANT">重要</option>
                <option value="EMERGENCY">緊急</option>
              </select>
            </label>
            <button
              onClick={() =>
                void api("/settings", userId, {
                  method: "PATCH",
                  body: JSON.stringify(settings),
                }).then(setSettings)
              }
            >
              儲存通知設定
            </button>
          </div>
        </details>
      )}
      {notice && (
        <button
          className="prepost-new"
          onClick={() => {
            setSelected(today);
            setHistoryOpen(false);
            setNotice(false);
          }}
        >
          今天有新的分析訊息
        </button>
      )}
      {historyOpen && (
        <section className="prepost-history">
          <h2>歷史分析列表</h2>
          <div className="prepost-table">
            <table>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>星期</th>
                  <th>狀態</th>
                  <th>盤前預估</th>
                  <th>分數</th>
                  <th>風險</th>
                  <th>驗證</th>
                  <th>盤前／盤後</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {historyRows.map((row) => (
                  <tr key={row.tradeDate}>
                    <td>{row.tradeDate}</td>
                    <td>{row.weekday}</td>
                    <td>{statusLabel[row.status] ?? row.status}</td>
                    <td>{row.marketBias}</td>
                    <td>{row.marketScore ?? "—"}</td>
                    <td>{row.riskLevel}</td>
                    <td>{row.predictionResult ?? "待驗證"}</td>
                    <td>
                      {row.preMarketMessageCount}／{row.postMarketMessageCount}
                    </td>
                    <td>
                      <button
                        onClick={() => {
                          setSelected(row.tradeDate);
                          setHistoryOpen(false);
                        }}
                      >
                        查看
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {!historyOpen && data && (
        <>
          <section className="prepost-summary">
            <header>
              <div>
                <small>最新分析摘要</small>
                <h1>盤前盤後分析</h1>
                <p>
                  {data.tradeDate}・
                  {data.isTradingDay ? "台股交易日" : "今日台股休市"}・
                  {statusLabel[data.status] ?? data.status}
                </p>
              </div>
              <div
                className={`analysis-state ${data.analysisStatus.toLowerCase()}`}
              >
                {data.analysisStatus}
              </div>
            </header>
            <div className="summary-grid">
              <div>
                <span>盤勢方向</span>
                <b>{data.latestMarketBias}</b>
              </div>
              <div>
                <span>AI盤勢分數</span>
                <b>{data.marketScore ?? "資料尚未取得"}</b>
              </div>
              <div>
                <span>資料完整度</span>
                <b>{data.dataCompleteness != null ? `${data.dataCompleteness}%` : "資料尚未取得"}</b>
              </div>
              <div>
                <span>風險等級</span>
                <b>{data.riskLevel}</b>
              </div>
              <div>
                <span>預估開盤</span>
                <b>{data.openingPattern}</b>
              </div>
              <div>
                <span>建議持股</span>
                <b>{data.recommendedPosition}</b>
              </div>
              <div>
                <span>強勢產業</span>
                <b>{data.strongIndustries.join("、") || "資料尚未取得"}</b>
              </div>
              <div>
                <span>弱勢產業</span>
                <b>{data.weakIndustries.join("、") || "資料尚未取得"}</b>
              </div>
              <div>
                <span>支撐／壓力</span>
                <b>
                  {[...data.support, ...data.resistance].join("／") ||
                    "資料尚未取得"}
                </b>
              </div>
            </div>
            <div className="advice-grid">
              <article>
                <h3>當沖操作建議</h3>
                <p>{data.dayTradeAdvice}</p>
              </article>
              <article>
                <h3>現股操作建議</h3>
                <p>{data.stockAdvice}</p>
              </article>
            </div>
            <footer>
              資料截至 {clock(data.dataAsOf)}・最後更新{" "}
              {clock(data.lastUpdatedAt)}
            </footer>
          </section>
          <section className="prepost-analytics">
            <header>
              <div><small>盤前圖表分析</small><h2>跨市場方向與期貨核對</h2></div>
              <time>更新 {clock(data.marketCharts?.updatedAt)}</time>
            </header>
            <div className="prepost-chart-grid">
              <ChangeBars title="美股、半導體與 ADR" items={["dow", "sp500", "nasdaq", "sox", "tsm", "nvda", "amd"].map((key) => data.marketCharts?.international[key]).filter((item): item is MarketQuote => Boolean(item))} />
              <ChangeBars title="美國期貨與總體資產" items={["es_future", "nq_future", "ym_future", "dxy", "us10y", "oil", "gold"].map((key) => data.marketCharts?.international[key]).filter((item): item is MarketQuote => Boolean(item))} />
              <ChangeBars title="台灣夜盤期貨（最近公布的期交所日報・非即時）" items={["tx_night", "te_night", "sof_night"].map((key) => data.marketCharts?.domestic[key]).filter((item): item is MarketQuote => Boolean(item))} />
              <ScoreBreakdown scores={data.scoreBreakdown} />
            </div>
            <div className="prepost-futures-audit">
              <b>期貨圖表判讀</b>
              <p>{data.marketCharts?.futuresNote ?? "台指期與美國指數期貨必須分開判讀。"}</p>
              <div>
                {["tx_night", "nq_future", "es_future"].map((key) => {
                  const quote = data.marketCharts?.domestic[key] ?? data.marketCharts?.international[key];
                  if (!quote) return null;
                  return <span key={key}><strong>{quote.name}{quote.contract ? ` ${quote.contract}` : ""}</strong> {signed(quote.changePct)}{quote.changePoints != null ? `（${quote.changePoints > 0 ? "+" : ""}${quote.changePoints} 點）` : ""}・{clock(quote.dataTime)}{quote.isRealtime === false ? "・日報非即時" : ""}</span>;
                })}
              </div>
            </div>
          </section>
          <section className="prepost-analytics post-market-analytics">
            <header>
              <div><small>盤後圖表分析</small><h2>加權、櫃買、量能與市場廣度</h2></div>
              <time>收盤資料 {clock(data.postMarketCharts?.dataTime)}</time>
            </header>
            <div className="prepost-chart-grid">
              <ChangeBars title="加權與櫃買收盤表現" items={["taiex", "otc"].map((key) => data.postMarketCharts?.indexes?.[key]).filter((item): item is MarketQuote => Boolean(item))} />
              <PostMarketStats data={data.postMarketCharts} />
              <ConfidenceBars messages={data.postMarketMessages} />
              <PredictionValidation details={data.predictionDetails} result={data.predictionResult} />
            </div>
          </section>
          <section className="prepost-industry-outlook">
            <header><div><small>未來產業展望</small><h2>領先產業、催化劑與失效條件</h2></div><span>評分資料日 {data.industryOutlook?.dataDate ?? "尚未取得"}</span></header>
            <div className="industry-ranking-chart">
              {(data.industryOutlook?.items ?? []).map((item) => <div key={item.industry}>
                <span>{item.industry}</span><div><i style={{ width: `${Math.max(0, Math.min(100, item.percentile))}%` }} /></div><b>{item.score.toFixed(1)} 分</b>
              </div>)}
            </div>
            <div className="industry-outlook-grid">
              {(data.industryOutlook?.items ?? []).map((item) => <article key={item.industry}>
                <header><h3>{item.industry}</h3><b>{item.stance}</b></header>
                <p>族群分數 {item.score.toFixed(1)}｜市場百分位 {item.percentile.toFixed(0)}｜樣本 {item.memberCount} 檔</p>
                <h4>未來催化劑</h4><ul>{item.catalysts.map((text) => <li key={text}>{text}</li>)}</ul>
                <h4>確認訊號</h4><p>{item.confirmation}</p>
                <h4>主要風險</h4><ul>{item.risks.map((text) => <li key={text}>{text}</li>)}</ul>
              </article>)}
            </div>
            {!(data.industryOutlook?.items.length) && <p className="empty-state">尚無可驗證的產業排名，系統不會以推測值補圖。</p>}
          </section>
          <section className="prepost-filters">
            <Search />
            <input
              placeholder="搜尋關鍵字、股票或產業"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <select value={period} onChange={(e) => setPeriod(e.target.value)}>
              <option value="">盤前與盤後</option>
              <option value="PRE_MARKET">盤前</option>
              <option value="POST_MARKET">盤後</option>
            </select>
            <select
              value={importance}
              onChange={(e) => setImportance(e.target.value)}
            >
              <option value="">全部重要程度</option>
              <option value="NORMAL">一般</option>
              <option value="NOTICE">注意</option>
              <option value="IMPORTANT">重要</option>
              <option value="EMERGENCY">緊急</option>
            </select>
          </section>
          <div className="prepost-columns">
            <section>
              <header>
                <h2>盤前訊息</h2>
                <span>{data.preMarketMessageCount} 則</span>
                {preReportReady ? (
                  <a
                    className="prepost-report-button pre"
                    href={`/api/prepost-analysis/pre-report?trade_date=${data.tradeDate}`}
                  >
                    <Download />
                    下載劉宗元大神盤前稿
                  </a>
                ) : (
                  <span className="prepost-report-wait">
                    08:00 開放完整盤前報告
                  </span>
                )}
              </header>
              {messages
                .filter((x) => x.period === "PRE_MARKET")
                .map((x) => (
                  <MessageCard
                    key={x.messageId}
                    item={x}
                    userId={userId}
                    onChanged={() => void load()}
                  />
                ))}
              {!messages.some((x) => x.period === "PRE_MARKET") && (
                <p className="empty-state">目前沒有符合條件的盤前訊息。</p>
              )}
            </section>
            <section>
              <header>
                <h2>盤後訊息</h2>
                <span>{data.postMarketMessageCount} 則</span>
                {reportReady ? (
                  <a
                    className="prepost-report-button"
                    href={`/api/prepost-analysis/report?trade_date=${data.tradeDate}&format=pdf`}
                  >
                    <Download />
                    下載劉宗元大神－盤後稿
                  </a>
                ) : (
                  <span className="prepost-report-wait">15:10 後開放報告</span>
                )}
              </header>
              {messages
                .filter((x) => x.period === "POST_MARKET")
                .map((x) => (
                  <MessageCard
                    key={x.messageId}
                    item={x}
                    userId={userId}
                    onChanged={() => void load()}
                  />
                ))}
              {!messages.some((x) => x.period === "POST_MARKET") && (
                <p className="empty-state">目前沒有符合條件的盤後訊息。</p>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
