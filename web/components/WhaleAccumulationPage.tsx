"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CalendarRange, Database, Filter, Fish, Search, SlidersHorizontal,
  Sparkles, TriangleAlert, TrendingUp, X,
} from "lucide-react";
import type {
  WhaleAccumulationFilters, WhaleAccumulationItem, WhaleAccumulationResponse,
  WhaleHistoryPoint, WhaleRankingType, WhaleTrendResponse,
} from "@/lib/whale-accumulation-types";
import { getWhaleAccumulation, getWhaleTrend } from "@/services/whale-accumulation-client";

const rankingOptions: Array<{ key: WhaleRankingType; label: string }> = [
  { key: "composite", label: "綜合偷掃貨" },
  { key: "big400", label: "400張增加最多" },
  { key: "big1000", label: "千張增加最多" },
  { key: "lots", label: "增加張數最多" },
  { key: "value", label: "增加市值最多" },
  { key: "retail", label: "散戶減少最多" },
  { key: "shareholders", label: "股東人數減少最多" },
];

const today = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());
const initialFilters: WhaleAccumulationFilters = {
  startDate: isoDaysBefore(today, 31), endDate: today, rankingType: "composite", limit: 30,
  keyword: "", industry: "", minBig400: -100, minBig1000: -100,
  minLots: 0, minValue: 0, maxPriceChange: 1000, minScore: 0,
};

function isoDaysBefore(end: string, days: number): string {
  const value = new Date(`${end}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - days);
  return value.toISOString().slice(0, 10);
}

function quickRange(key: string): Pick<WhaleAccumulationFilters, "startDate" | "endDate"> {
  if (key === "ytd") return { startDate: `${today.slice(0, 4)}-01-01`, endDate: today };
  const days = key === "1w" ? 7 : key === "2w" ? 14 : key === "1m" ? 31 : 92;
  return { startDate: isoDaysBefore(today, days), endDate: today };
}

function pp(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)} 個百分點`;
}

function pct(value: number | null): string {
  if (value === null) return "暫無";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function compactMoney(value: number | null): string {
  if (value === null) return "暫無";
  if (Math.abs(value) >= 100_000_000) return `${value < 0 ? "-" : ""}${(Math.abs(value) / 100_000_000).toFixed(2)} 億`;
  if (Math.abs(value) >= 10_000) return `${value < 0 ? "-" : ""}${(Math.abs(value) / 10_000).toFixed(1)} 萬`;
  return Math.round(value).toLocaleString("zh-TW");
}

function valueClass(value: number | null): string {
  return value === null ? "" : value > 0 ? "positive" : value < 0 ? "negative" : "";
}

function medal(rank: number): string {
  return rank === 1 ? "🥇" : rank === 2 ? "🥈" : rank === 3 ? "🥉" : String(rank);
}

function heat(score: number): string {
  return score >= 85 ? "🔥🔥" : score >= 75 ? "🔥" : score >= 65 ? "🟢" : score >= 50 ? "🟡" : "⚪";
}

function cardValue(value: number | null, type: string): string {
  if (value === null) return "—";
  if (type === "currency") return `約 ${compactMoney(value)}`;
  if (type === "score") return `${value.toFixed(0)} 分`;
  if (type === "negativePercentagePoint") return `-${value.toFixed(2)} 個百分點`;
  return pp(value);
}

function chartPoints(values: number[], width = 520, height = 150): string {
  if (!values.length) return "";
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = Math.max(.0001, maximum - minimum);
  return values.map((value, index) => {
    const x = 14 + index / Math.max(1, values.length - 1) * (width - 28);
    const y = 12 + (maximum - value) / spread * (height - 30);
    return `${x},${y}`;
  }).join(" ");
}

function TrendLine({ points, keys }: {
  points: WhaleHistoryPoint[];
  keys: Array<{ key: keyof WhaleHistoryPoint; color: string; label: string }>;
}) {
  return <div className="whale-chart"><svg viewBox="0 0 520 150" role="img">
    {[.25, .5, .75].map((ratio) => <line key={ratio} x1="8" x2="512" y1={150 * ratio} y2={150 * ratio} className="grid" />)}
    {keys.map((series) => {
      const values = points.map((point) => Number(point[series.key] ?? 0));
      const line = chartPoints(values);
      return <g key={String(series.key)}><polyline points={line} style={{ stroke: series.color }} />
        {line.split(" ").map((coordinate, index) => {
          const [cx, cy] = coordinate.split(",");
          return <circle key={`${series.key}-${points[index]?.reportDate}`} cx={cx} cy={cy} r="3" style={{ fill: series.color }}><title>{points[index]?.reportDate}・{series.label} {values[index]?.toFixed(2)}</title></circle>;
        })}
      </g>;
    })}
  </svg><div className="whale-chart-legend">{keys.map((item) => <span key={String(item.key)}><i style={{ background: item.color }} />{item.label}</span>)}</div>
    <footer><span>{points[0]?.reportDate}</span><span>{points.at(-1)?.reportDate}</span></footer>
  </div>;
}

function TrendModal({ data, loading, onClose }: { data: WhaleTrendResponse | null; loading: boolean; onClose: () => void }) {
  return <div className="whale-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="whale-modal" role="dialog" aria-modal="true">
      <header><div><span>OWNERSHIP ACCUMULATION TREND</span><h2>{data ? `${data.item.stockCode} ${data.item.stockName}` : "大戶籌碼趨勢"}</h2><p>{data?.dataNotice ?? "正在讀取區間內每一期集保資料…"}</p></div><button onClick={onClose}><X /></button></header>
      {loading || !data ? <div className="whale-modal-loading"><span className="spinner" />整理中間各期籌碼變化…</div> : <>
        <div className="whale-modal-summary">
          <article><span>400張以上</span><strong>{data.item.big400Start.toFixed(2)}% → {data.item.big400End.toFixed(2)}%</strong><small className={valueClass(data.item.big400Change)}>{pp(data.item.big400Change)}</small></article>
          <article><span>千張以上</span><strong>{data.item.big1000Start.toFixed(2)}% → {data.item.big1000End.toFixed(2)}%</strong><small className={valueClass(data.item.big1000Change)}>{pp(data.item.big1000Change)}</small></article>
          <article><span>散戶</span><strong>{data.item.retailStart.toFixed(2)}% → {data.item.retailEnd.toFixed(2)}%</strong><small className={valueClass(data.item.retailChange)}>{pp(data.item.retailChange)}</small></article>
          <article><span>連續加碼</span><strong>{data.item.continuationLabel}</strong><small>趨勢一致性 {data.item.trendConsistency.toFixed(0)}%</small></article>
        </div>
        <div className="whale-chart-grid">
          <article><h3>大戶與散戶持股比例</h3><TrendLine points={data.item.history} keys={[
            { key: "big400Ratio", color: "#8f7dff", label: "400張以上" },
            { key: "big1000Ratio", color: "#ff747d", label: "千張以上" },
            { key: "retailRatio", color: "#45d99b", label: "10張以下散戶" },
          ]} /></article>
          <article><h3>股東人數</h3><TrendLine points={data.item.history} keys={[{ key: "totalShareholders", color: "#5ca7ff", label: "股東人數" }]} /></article>
          <article><h3>期間股價</h3>{data.item.history.some((point) => point.price !== null) ? <TrendLine points={data.item.history.filter((point) => point.price !== null)} keys={[{ key: "price", color: "#efc35c", label: "收盤價" }]} /> : <div className="whale-chart-empty">期間歷史股價暫無，市值改用結束日可用價格估算</div>}</article>
          <article><h3>成交量</h3>{data.item.history.some((point) => Number(point.volume) > 0) ? <TrendLine points={data.item.history} keys={[{ key: "volume", color: "#b16cff", label: "成交量" }]} /> : <div className="whale-chart-empty">歷史成交量暫無資料</div>}</article>
        </div>
        <footer>使用者選擇 {data.requestedRange.start}～{data.requestedRange.end}・實際比較 {data.actualRange.start}～{data.actualRange.end}</footer>
      </>}
    </section>
  </div>;
}

export function WhaleAccumulationPage({ onSelectStock }: { onSelectStock: (symbol: string) => void }) {
  const [draft, setDraft] = useState(initialFilters);
  const [filters, setFilters] = useState(initialFilters);
  const [data, setData] = useState<WhaleAccumulationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<WhaleTrendResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await getWhaleAccumulation(filters));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "大戶偷掃貨資料讀取失敗");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => { void load(); }, [load]);

  const selectRanking = (rankingType: WhaleRankingType) => {
    setDraft((current) => ({ ...current, rankingType }));
    setFilters((current) => ({ ...current, rankingType }));
  };

  const selectQuickRange = (key: string) => {
    const range = quickRange(key);
    setDraft((current) => ({ ...current, ...range }));
    setFilters((current) => ({ ...current, ...range }));
  };

  const openDetail = async (item: WhaleAccumulationItem) => {
    setDetailOpen(true);
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(await getWhaleTrend(item.stockCode, filters.startDate, filters.endDate));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "個股籌碼趨勢讀取失敗");
      setDetailOpen(false);
    } finally {
      setDetailLoading(false);
    }
  };

  const activeRanking = useMemo(() => rankingOptions.find((item) => item.key === filters.rankingType)?.label, [filters.rankingType]);

  return <div className="whale-page">
    <section className="whale-hero"><div><span className="section-kicker">WHALE ACCUMULATION RADAR</span><h1><Fish />大戶偷掃貨</h1><p>自由比較任意集保期間，找出大戶已經卡位、股價尚未大漲的股票。</p></div><div><span>目前排名</span><strong>{activeRanking}</strong><small>符合 {data?.totalMatched ?? 0} 檔・顯示 TOP {filters.limit}</small></div></section>

    <form className="whale-date-panel" onSubmit={(event) => { event.preventDefault(); setFilters(draft); }}>
      <div className="whale-date-inputs"><label><span>起始日期</span><input type="date" value={draft.startDate} onChange={(event) => setDraft({ ...draft, startDate: event.target.value })} /></label><span>～</span><label><span>結束日期</span><input type="date" value={draft.endDate} onChange={(event) => setDraft({ ...draft, endDate: event.target.value })} /></label><button type="submit"><Search />查詢</button></div>
      <div className="whale-quick-ranges"><CalendarRange />{[["1w","近1週"],["2w","近2週"],["1m","近1個月"],["3m","近3個月"],["ytd","今年以來"]].map(([key,label]) => <button key={key} type="button" onClick={() => selectQuickRange(key)}>{label}</button>)}<button type="button" className="custom" onClick={() => document.querySelector<HTMLInputElement>(".whale-date-inputs input")?.showPicker?.()}>自訂日期</button></div>
    </form>

    {error && <div className="error-banner"><TriangleAlert />{error}<button onClick={() => void load()}>重試</button></div>}
    {data && <div className={`whale-data-notice ${data.dataMode}`}><Database /><div><strong>{data.dataSource}</strong><span>{data.dataNotice}</span></div></div>}

    {data && <section className="whale-range-result"><div><span>使用者選擇區間</span><strong>{data.requestedRange.start} ～ {data.requestedRange.end}</strong></div><TrendingUp /><div><span>實際比較資料</span><strong>{data.actualRange.start} ～ {data.actualRange.end}</strong></div><small>有效資料範圍 {data.availableRange.start}～{data.availableRange.end}</small></section>}

    {data && <section className="whale-summary-cards">{data.summaryCards.map((card) => <article key={card.key}><span>{card.label}</span><strong>{card.stockCode ? `${card.stockCode} ${card.stockName}` : "暫無"}</strong><b>{cardValue(card.value, card.valueType)}</b></article>)}</section>}

    <section className="whale-ranking-tabs">{rankingOptions.map((option) => <button key={option.key} className={filters.rankingType === option.key ? "active" : ""} onClick={() => selectRanking(option.key)}>{option.label}</button>)}</section>

    <form className="whale-filters" onSubmit={(event) => { event.preventDefault(); setFilters(draft); }}>
      <label className="search"><Search /><input placeholder="股票代號或名稱" value={draft.keyword} onChange={(event) => setDraft({ ...draft, keyword: event.target.value })} /></label>
      <label><Filter /><select value={draft.industry} onChange={(event) => setDraft({ ...draft, industry: event.target.value })}><option value="">全部產業</option>{data?.industries.map((industry) => <option key={industry}>{industry}</option>)}</select></label>
      <label>400張增加≥<input type="number" step="0.1" value={draft.minBig400} onChange={(event) => setDraft({ ...draft, minBig400: Number(event.target.value) })} />%</label>
      <label>千張增加≥<input type="number" step="0.1" value={draft.minBig1000} onChange={(event) => setDraft({ ...draft, minBig1000: Number(event.target.value) })} />%</label>
      <label>增加張數≥<input type="number" step="100" value={draft.minLots} onChange={(event) => setDraft({ ...draft, minLots: Number(event.target.value) })} /></label>
      <label>增加市值≥<input type="number" step="10000000" value={draft.minValue} onChange={(event) => setDraft({ ...draft, minValue: Number(event.target.value) })} /></label>
      <label>最大期間漲幅<input type="number" step="1" value={draft.maxPriceChange} onChange={(event) => setDraft({ ...draft, maxPriceChange: Number(event.target.value) })} />%</label>
      <label>最低分數<input type="number" min="0" max="100" value={draft.minScore} onChange={(event) => setDraft({ ...draft, minScore: Number(event.target.value) })} /></label>
      <label>顯示<select value={draft.limit} onChange={(event) => setDraft({ ...draft, limit: Number(event.target.value) as WhaleAccumulationFilters["limit"] })}>{[20,30,50,100].map((value) => <option key={value} value={value}>TOP {value}</option>)}</select></label>
      <button type="submit"><SlidersHorizontal />套用篩選</button>
    </form>

    <section className="whale-ranking-table-card">
      <header><div><Sparkles /><span><strong>TOP {filters.limit} 大戶偷掃貨排行榜</strong><small>{activeRanking}・點擊股票展開中間各期趨勢</small></span></div><b>{data?.items.length ?? 0}<small> 檔</small></b></header>
      {loading && !data ? <div className="page-loading"><span className="spinner" /><p>正在比較所有股票的集保籌碼變化…</p></div> : !data?.items.length ? <div className="whale-empty">目前沒有符合篩選條件的股票。</div> : <div className="whale-table-wrap"><table><thead><tr><th>排名</th><th>股票</th><th>產業</th><th>最新股價</th><th>期間漲跌</th><th>400張以上</th><th>400張變化</th><th>千張以上</th><th>千張變化</th><th>估增張數</th><th>估增市值</th><th>散戶變化</th><th>股東人數</th><th>偷掃貨分數</th><th>籌碼狀態</th></tr></thead><tbody>
        {data.items.map((item) => <tr key={item.stockCode} onClick={() => void openDetail(item)}><td><span className="whale-rank">{medal(item.rank)}</span></td><td><button onClick={(event) => { event.stopPropagation(); onSelectStock(item.stockCode); }}><strong>{item.stockCode}</strong><small>{item.stockName}・{item.market}</small></button></td><td>{item.industry}</td><td><strong>{item.latestPrice?.toFixed(2) ?? "暫無"}</strong><small>{item.priceSource}</small></td><td className={valueClass(item.periodPriceChangePct)}>{pct(item.periodPriceChangePct)}</td><td>{item.big400Start.toFixed(2)}% → <strong>{item.big400End.toFixed(2)}%</strong></td><td className={valueClass(item.big400Change)}><strong>{item.big400Change >= 0 ? "↑ " : "↓ "}{pp(item.big400Change)}</strong></td><td>{item.big1000Start.toFixed(2)}% → <strong>{item.big1000End.toFixed(2)}%</strong></td><td className={valueClass(item.big1000Change)}><strong>{item.big1000Change >= 0 ? "↑ " : "↓ "}{pp(item.big1000Change)}</strong></td><td className={valueClass(item.estimatedIncreaseLots)}><strong>{item.estimatedIncreaseLots >= 0 ? "+" : ""}{Math.round(item.estimatedIncreaseLots).toLocaleString("zh-TW")} 張</strong></td><td><strong>約 {compactMoney(item.estimatedAccumulationValue)}</strong></td><td className={valueClass(item.retailChange)}>{pp(item.retailChange)}</td><td className={valueClass(item.shareholderChangePct)}>{pct(item.shareholderChangePct)}<small>{item.shareholderChange.toLocaleString("zh-TW")} 人</small></td><td><span className={`whale-score s${Math.floor(item.whaleAccumulationScore / 10)}`}>{heat(item.whaleAccumulationScore)} {item.whaleAccumulationScore}</span></td><td><strong className="whale-status">{item.chipStatus}</strong><small>{item.continuationLabel}</small>{item.anomalyFlag && <em><TriangleAlert />籌碼資料可能受股本事件影響</em>}{item.singlePeriodReversal && <em>單期異常，分數已降低</em>}</td></tr>)}
      </tbody></table></div>}
    </section>
    <p className="whale-disclaimer"><TriangleAlert />估算增加張數＝400張以上持股比例變化×期末集保總股數；估算市值優先採期間平均收盤價，缺少時使用可取得的結束日／最新官方收盤價。本頁僅供研究，不構成投資建議。</p>
    {detailOpen && <TrendModal data={detail} loading={detailLoading} onClose={() => setDetailOpen(false)} />}
  </div>;
}
