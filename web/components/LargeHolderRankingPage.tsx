"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BellRing, BrainCircuit, CandlestickChart, Database, Filter, RefreshCw,
  Search, ShieldAlert, Star, TrendingUp, TriangleAlert, UsersRound, X,
} from "lucide-react";
import { formatPercent, formatVolume, safeNumber, valueClass } from "@/lib/format";
import type {
  LargeHolderHistoryPoint,
  LargeHolderHistoryResponse,
  LargeHolderRankingItem,
  LargeHolderRankingResponse,
  LargeHolderRankingType,
} from "@/lib/large-holder-types";
import {
  addLargeHolderAction,
  getLargeHolderHistory,
  getLargeHolderRankings,
  type LargeHolderFilters,
} from "@/services/large-holder-client";

const initialFilters: LargeHolderFilters = {
  market: "all",
  industry: "",
  keyword: "",
  minAverageTurnover: 30_000_000,
};

function scoreLabel(score: number) {
  if (score >= 80) return "強勢集中";
  if (score >= 65) return "偏多觀察";
  if (score >= 50) return "中性";
  if (score >= 35) return "籌碼普通";
  return "籌碼轉弱";
}

function compactNumber(value: number | null) {
  if (value == null || !Number.isFinite(value)) return "暫無資料";
  const absolute = Math.abs(value);
  const prefix = value > 0 ? "+" : "";
  if (absolute >= 100_000_000) return `${prefix}${safeNumber(value / 100_000_000)} 億`;
  if (absolute >= 10_000) return `${prefix}${safeNumber(value / 10_000)} 萬`;
  return `${prefix}${Math.round(value).toLocaleString("zh-TW")}`;
}

function pp(value: number) {
  return `${value > 0 ? "+" : ""}${safeNumber(value)} 個百分點`;
}

function lots(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "暫無資料";
  return `${value.toLocaleString("zh-TW", { maximumFractionDigits: 3 })} 張`;
}

function lotChange(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "暫無資料";
  return `${value > 0 ? "+" : ""}${lots(value)}`;
}

function movement(value: number) {
  return value > 0 ? "增加" : value < 0 ? "減少" : "持平";
}

function healthClass(score: number) {
  return score >= 80 ? "strong" : score >= 65 ? "bull" : score >= 50 ? "neutral" : score >= 35 ? "weak" : "bear";
}

function LargeHolderHealthBadge({ score }: { score: number }) {
  return <span className={`lh-health ${healthClass(score)}`}><strong>{score}</strong><small>{scoreLabel(score)}</small></span>;
}

function LargeHolderAiSignalBadge({ signal }: { signal: string }) {
  return <span className="lh-ai-signal"><BrainCircuit size={11} />{signal}</span>;
}

function ActionButtons({
  item,
  type,
  userId,
  onSelectStock,
  onDetail,
  onMessage,
}: {
  item: LargeHolderRankingItem;
  type: LargeHolderRankingType;
  userId: string;
  onSelectStock: (symbol: string) => void;
  onDetail: (item: LargeHolderRankingItem) => void;
  onMessage: (message: string, error?: boolean) => void;
}) {
  const [working, setWorking] = useState("");
  const run = async (action: "watchlist" | "ai" | "line", label: string) => {
    if (!userId) {
      onMessage("使用者識別尚未建立，請稍後再試。", true);
      return;
    }
    setWorking(action);
    try {
      const result = await addLargeHolderAction(userId, item, type, action);
      onMessage(result.message || label);
    } catch (reason) {
      onMessage(reason instanceof Error ? reason.message : `${label}失敗`, true);
    } finally {
      setWorking("");
    }
  };
  return (
    <div className="lh-actions" onClick={(event) => event.stopPropagation()}>
      <button onClick={() => void run("watchlist", "已加入自選")} disabled={Boolean(working)}><Star />加入自選</button>
      <button onClick={() => void run("ai", "已加入 AI 觀察")} disabled={Boolean(working)}><BrainCircuit />AI觀察</button>
      <button onClick={() => onSelectStock(item.stockCode)}><CandlestickChart />K線</button>
      <button onClick={() => onDetail(item)}><TrendingUp />籌碼</button>
      <button onClick={() => void run("line", "LINE 通知已設定")} disabled={Boolean(working)}><BellRing />LINE</button>
    </div>
  );
}

function RankingTable({
  type,
  response,
  userId,
  onSelectStock,
  onDetail,
  onMessage,
}: {
  type: LargeHolderRankingType;
  response: LargeHolderRankingResponse;
  userId: string;
  onSelectStock: (symbol: string) => void;
  onDetail: (item: LargeHolderRankingItem) => void;
  onMessage: (message: string, error?: boolean) => void;
}) {
  const label = type === "over400" ? "400～600張級距" : "1,000張以上";
  if (!response.items.length) {
    return <div className="lh-empty"><UsersRound size={28} /><h3>目前沒有符合條件的股票</h3><p>請調整產業、市場或最低成交金額條件。</p></div>;
  }
  return (
    <>
      <div className="lh-table-wrap">
        <table className="lh-table">
          <thead><tr>
            <th>排名</th><th>股票</th><th>市場／產業</th><th>最新股價</th><th>本週漲跌</th>
            <th>本週{label}比例</th><th>上週比例</th><th>比率方向／增減</th><th>變動幅度</th>
            <th>持股張數（本週／上週）</th><th>張數增減</th><th>大戶人數／週增減</th>
            <th>外資5日</th><th>投信5日</th><th>主力5日</th>
            <th>5日量變化</th><th>技術面</th><th>健康度</th><th>AI判讀</th><th>操作</th>
          </tr></thead>
          <tbody>{response.items.map((item) => (
            <tr key={item.stockCode} onClick={() => onDetail(item)}>
              <td><b className="lh-rank">{item.rank}</b></td>
              <td><strong>{item.stockCode}</strong><small>{item.stockName}</small></td>
              <td><span>{item.market}</span><small>{item.industry}</small></td>
              <td><strong>{safeNumber(item.latestPrice)}</strong><small>{item.quoteSource}</small></td>
              <td className={valueClass(item.weeklyChangePct ?? 0)}>{formatPercent(item.weeklyChangePct)}</td>
              <td><b>{safeNumber(item.currentLargeHolderRatio)}%</b></td>
              <td>{safeNumber(item.previousLargeHolderRatio)}%</td>
              <td className={valueClass(item.changePercentagePoint)}><b>{movement(item.changePercentagePoint)}</b><small>{pp(item.changePercentagePoint)}</small></td>
              <td className={valueClass(item.changePercentage ?? 0)}>{formatPercent(item.changePercentage)}</td>
              <td><b>{lots(item.currentLotCount)}</b><small>上週 {lots(item.previousLotCount)}</small></td>
              <td className={valueClass(item.lotCountChange ?? 0)}><b>{lotChange(item.lotCountChange)}</b></td>
              <td><b>{item.currentHolderCount.toLocaleString("zh-TW")}</b><small className={valueClass(item.holderCountChange)}>{item.holderCountChange > 0 ? "+" : ""}{item.holderCountChange}</small></td>
              <td className={valueClass(item.foreignNetBuy5d ?? 0)}>{compactNumber(item.foreignNetBuy5d)}</td>
              <td className={valueClass(item.investmentTrustNetBuy5d ?? 0)}>{compactNumber(item.investmentTrustNetBuy5d)}</td>
              <td className={valueClass(item.mainForceNetBuy5d ?? 0)}>{compactNumber(item.mainForceNetBuy5d)}</td>
              <td className={valueClass(item.volumeChange5d ?? 0)}>{formatPercent(item.volumeChange5d)}</td>
              <td><span className="lh-technical">{item.technicalStatus}</span></td>
              <td><LargeHolderHealthBadge score={item.healthScore} /></td>
              <td>
                <LargeHolderAiSignalBadge signal={item.aiSignal} />
                {item.anomalyFlag && <span className="lh-warning"><TriangleAlert size={10} />資料異常</span>}
              </td>
              <td><ActionButtons {...{ item, type, userId, onSelectStock, onDetail, onMessage }} /></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="lh-mobile-list">{response.items.map((item) => (
        <article className="lh-mobile-card" key={item.stockCode} onClick={() => onDetail(item)}>
          <header>
            <b className="lh-rank">{item.rank}</b>
            <div><strong>{item.stockCode} {item.stockName}</strong><small>{item.market} · {item.industry}</small></div>
            <LargeHolderHealthBadge score={item.healthScore} />
          </header>
          <div className="lh-mobile-metrics">
            <span>現價<b>{safeNumber(item.latestPrice)}</b></span>
            <span>{label}比例<b>{safeNumber(item.currentLargeHolderRatio)}%</b></span>
            <span>比率方向<b className={valueClass(item.changePercentagePoint)}>{movement(item.changePercentagePoint)} · {pp(item.changePercentagePoint)}</b></span>
            <span>本週持股張數<b>{lots(item.currentLotCount)}</b></span>
            <span>張數增減<b className={valueClass(item.lotCountChange ?? 0)}>{lotChange(item.lotCountChange)}</b></span>
          </div>
          <LargeHolderAiSignalBadge signal={item.aiSignal} />
          {item.warnings.map((warning) => <p className="lh-card-warning" key={warning}><TriangleAlert size={11} />{warning}</p>)}
          <ActionButtons {...{ item, type, userId, onSelectStock, onDetail, onMessage }} />
        </article>
      ))}</div>
    </>
  );
}

function linePoints(values: number[], width: number, height: number) {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return "";
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const spread = Math.max(max - min, 0.0001);
  return values.map((value, index) => {
    const x = 12 + index / Math.max(1, values.length - 1) * (width - 24);
    const y = 12 + (max - value) / spread * (height - 24);
    return `${x},${y}`;
  }).join(" ");
}

function MiniLineChart({
  items,
  series,
}: {
  items: LargeHolderHistoryPoint[];
  series: { key: keyof LargeHolderHistoryPoint; label: string; color: string; suffix?: string }[];
}) {
  const width = 520;
  const height = 170;
  return (
    <svg className="lh-line-chart" viewBox={`0 0 ${width} ${height}`} role="img">
      {[.25, .5, .75].map((ratio) => <line key={ratio} x1="8" x2={width - 8} y1={height * ratio} y2={height * ratio} />)}
      {series.map((definition) => {
        const values = items.map((item) => Number(item[definition.key])).filter(Number.isFinite);
        const points = linePoints(values, width, height);
        return <g key={String(definition.key)}>
          <polyline points={points} style={{ stroke: definition.color }} />
          {points.split(" ").map((point, index) => {
            const [cx, cy] = point.split(",");
            const item = items[index];
            if (!item) return null;
            return <circle key={`${definition.key}-${item.reportDate}`} cx={cx} cy={cy} r="3" style={{ fill: definition.color }}>
              <title>{item.reportDate} · {definition.label}：{safeNumber(Number(item[definition.key]))}{definition.suffix ?? ""}</title>
            </circle>;
          })}
        </g>;
      })}
    </svg>
  );
}

function MiniVolumeChart({ items }: { items: LargeHolderHistoryPoint[] }) {
  const values = items.map((item) => item.volume ?? 0);
  const max = Math.max(...values, 1);
  return <svg className="lh-bar-chart" viewBox="0 0 520 150" role="img">
    {items.map((item, index) => {
      const height = (item.volume ?? 0) / max * 120;
      return <rect key={item.reportDate} x={10 + index * 42} y={137 - height} width="24" height={height}>
        <title>{item.reportDate} · 成交量：{formatVolume(item.volume)}</title>
      </rect>;
    })}
  </svg>;
}

function LargeHolderDetailModal({
  item,
  history,
  loading,
  onClose,
}: {
  item: LargeHolderRankingItem;
  history: LargeHolderHistoryResponse | null;
  loading: boolean;
  onClose: () => void;
}) {
  return <div className="lh-modal-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="lh-detail-modal" role="dialog" aria-modal="true" aria-label={`${item.stockName} 大戶持股趨勢`}>
      <header>
        <div><span>12 WEEK OWNERSHIP TREND</span><h2>{item.stockCode} {item.stockName}・大戶籌碼詳情</h2><p>{history?.dataNotice ?? "正在讀取資料…"}</p></div>
        <button onClick={onClose} aria-label="關閉"><X /></button>
      </header>
      {loading || !history ? <div className="lh-detail-loading"><span className="spinner" />正在整理最近 12 週資料…</div> : (
        <div className="lh-chart-grid">
          <article><h3>400～600張與千張以上持股比例趨勢</h3><div className="lh-chart-legend"><span className="purple">400～600張</span><span className="red">千張以上</span></div>
            <MiniLineChart items={history.items} series={[
              { key: "ratioOver400", label: "400～600張比例", color: "#8d80ff", suffix: "%" },
              { key: "ratioOver1000", label: "千張以上比例", color: "#ff6f75", suffix: "%" },
            ]} />
          </article>
          <article><h3>大戶人數變化</h3><div className="lh-chart-legend"><span className="blue">400～600張人數</span><span className="green">千張以上人數</span></div>
            <MiniLineChart items={history.items} series={[
              { key: "holdersOver400", label: "400～600張人數", color: "#58a6ff" },
              { key: "holdersOver1000", label: "千張以上人數", color: "#34d399" },
            ]} />
          </article>
          <article><h3>持股張數變化</h3><div className="lh-chart-legend"><span className="purple">400～600張級距</span><span className="red">千張以上</span></div>
            <MiniLineChart items={history.items} series={[
              { key: "lotsOver400", label: "400～600張級距持股", color: "#8d80ff", suffix: " 張" },
              { key: "lotsOver1000", label: "千張以上持股", color: "#ff6f75", suffix: " 張" },
            ]} />
          </article>
          <article><h3>最近12週股價走勢</h3>
            {history.items.some((point) => point.price != null)
              ? <MiniLineChart items={history.items} series={[{ key: "price", label: "股價", color: "#f3c969" }]} />
              : <div className="lh-chart-empty">股價資料暫無資料</div>}
          </article>
          <article><h3>最近12週成交量</h3>
            {history.items.some((point) => point.volume != null)
              ? <MiniVolumeChart items={history.items} />
              : <div className="lh-chart-empty">成交量資料暫無資料</div>}
          </article>
          <article className="wide"><h3>法人、主力與融資變化</h3>
            {history.items.some((point) => point.foreignNetBuy != null) ? (
              <div className="lh-flow-grid">{history.items.slice(-4).map((point) => <div key={point.reportDate}>
                <time>{point.reportDate}</time>
                <span>外資<b className={valueClass(point.foreignNetBuy ?? 0)}>{compactNumber(point.foreignNetBuy)}</b></span>
                <span>投信<b className={valueClass(point.investmentTrustNetBuy ?? 0)}>{compactNumber(point.investmentTrustNetBuy)}</b></span>
                <span>自營商<b className={valueClass(point.dealerNetBuy ?? 0)}>{compactNumber(point.dealerNetBuy)}</b></span>
                <span>主力<b className={valueClass(point.mainForceNetBuy ?? 0)}>{compactNumber(point.mainForceNetBuy)}</b></span>
                <span>融資<b className={valueClass(point.marginBalanceChange ?? 0)}>{compactNumber(point.marginBalanceChange)}</b></span>
              </div>)}</div>
            ) : <div className="lh-chart-empty">法人、主力與融資資料暫無資料</div>}
          </article>
        </div>
      )}
      <footer><ShieldAlert size={13} />集保資料為週資料；本頁資訊僅供研究參考，不構成投資建議。</footer>
    </section>
  </div>;
}

export function LargeHolderRankingPage({
  userId,
  onSelectStock,
}: {
  userId: string;
  onSelectStock: (symbol: string) => void;
}) {
  const [draft, setDraft] = useState(initialFilters);
  const [filters, setFilters] = useState(initialFilters);
  const [over400, setOver400] = useState<LargeHolderRankingResponse | null>(null);
  const [over1000, setOver1000] = useState<LargeHolderRankingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [selected, setSelected] = useState<LargeHolderRankingItem | null>(null);
  const [history, setHistory] = useState<LargeHolderHistoryResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [activeMobileTab, setActiveMobileTab] = useState<LargeHolderRankingType>("over400");

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError("");
    try {
      let first: LargeHolderRankingResponse;
      let second: LargeHolderRankingResponse;
      if (refresh) {
        first = await getLargeHolderRankings("over400", filters, true);
        second = await getLargeHolderRankings("over1000", filters, false);
      } else {
        [first, second] = await Promise.all([
          getLargeHolderRankings("over400", filters),
          getLargeHolderRankings("over1000", filters),
        ]);
      }
      setOver400(first);
      setOver1000(second);
      if (refresh) {
        const sync = first.syncResult;
        setMessage({
          text: sync?.status === "failed"
            ? `官方同步失敗，繼續顯示上次成功資料：${sync.message ?? "未知錯誤"}`
            : "集保資料同步完成；若尚未累積兩期，排行榜仍會標示展示模式。",
          error: sync?.status === "failed",
        });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "大戶持股排行榜讀取失敗");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => { void load(); }, [load]);

  const industries = useMemo(
    () => [...new Set([...(over400?.industries ?? []), ...(over1000?.industries ?? [])])].sort(),
    [over1000, over400],
  );

  const openDetail = async (item: LargeHolderRankingItem) => {
    setSelected(item);
    setHistory(null);
    setHistoryLoading(true);
    try {
      setHistory(await getLargeHolderHistory(item.stockCode));
    } catch (reason) {
      setMessage({ text: reason instanceof Error ? reason.message : "歷史資料讀取失敗", error: true });
      setSelected(null);
    } finally {
      setHistoryLoading(false);
    }
  };

  const data = over400 ?? over1000;
  return (
    <div className="large-holder-page">
      <section className="lh-hero">
        <div>
          <span className="section-kicker">TDCC SHAREHOLDER DISTRIBUTION</span>
          <h1><UsersRound />大戶持股變化榜</h1>
          <p>追蹤本週400～600張級距與千張以上持股的比率方向及實際張數增減</p>
        </div>
        <dl>
          <div><dt>本期資料</dt><dd>{data?.currentReportDate ?? "—"}</dd></div>
          <div><dt>上期資料</dt><dd>{data?.previousReportDate ?? "—"}</dd></div>
          <div><dt>更新時間</dt><dd>{data ? new Date(data.updatedAt).toLocaleString("zh-TW", { hour12: false }) : "—"}</dd></div>
          <div><dt>資料狀態</dt><dd className={data?.dataMode === "demo" ? "demo" : "official"}>{data?.dataMode === "demo" ? "展示 Adapter" : "TDCC 官方"}</dd></div>
        </dl>
      </section>

      {data && <div className={`lh-data-notice ${data.dataMode}`}><Database size={14} /><div><strong>{data.dataSource}</strong><span>{data.dataNotice}</span></div></div>}
      {message && <div className={`lh-toast ${message.error ? "error" : ""}`}><span>{message.text}</span><button onClick={() => setMessage(null)}><X /></button></div>}
      {error && <div className="error-banner"><TriangleAlert size={15} />{error}<button onClick={() => void load()}>重試</button></div>}

      <form className="lh-filters" onSubmit={(event) => { event.preventDefault(); setFilters(draft); }}>
        <label className="lh-search"><Search /><input value={draft.keyword} onChange={(event) => setDraft({ ...draft, keyword: event.target.value })} placeholder="搜尋股票代號或名稱" /></label>
        <label><Filter /><select value={draft.industry} onChange={(event) => setDraft({ ...draft, industry: event.target.value })}><option value="">全部產業</option>{industries.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><select value={draft.market} onChange={(event) => setDraft({ ...draft, market: event.target.value as LargeHolderFilters["market"] })}><option value="all">上市＋上櫃</option><option value="listed">只看上市</option><option value="otc">只看上櫃</option></select></label>
        <label className="lh-turnover">最低20日均成交金額<input type="number" min="0" step="10" value={draft.minAverageTurnover / 1_000_000} onChange={(event) => setDraft({ ...draft, minAverageTurnover: Math.max(0, Number(event.target.value)) * 1_000_000 })} /><span>百萬元</span></label>
        <label className="lh-check"><input type="checkbox" checked readOnly />排除ETF、ETN、權證、特別股、存託憑證、興櫃與非普通股</label>
        <button className="primary" type="submit">套用篩選</button>
        <button type="button" onClick={() => void load(true)} disabled={loading}><RefreshCw className={loading ? "spin-icon" : ""} />重新整理</button>
      </form>

      <div className="lh-mobile-tabs">
        <button className={activeMobileTab === "over400" ? "active" : ""} onClick={() => setActiveMobileTab("over400")}>400～600張榜</button>
        <button className={activeMobileTab === "over1000" ? "active" : ""} onClick={() => setActiveMobileTab("over1000")}>千張以上榜</button>
      </div>

      {loading && !over400 ? <div className="page-loading"><span className="spinner" /><p>正在計算大戶持股週增排名…</p></div> : (
        <div className="lh-ranking-grid">
          {over400 && <section className={`lh-ranking-panel ${activeMobileTab !== "over400" ? "mobile-hidden" : ""}`}>
            <header><div><span>TDCC LEVEL 12</span><h2>400～600張級距變化前20名</h2><p>官方第12級（400,001～600,000股），依比率增減百分點排序</p></div><strong>{over400.items.length}<small>／20</small></strong></header>
            <RankingTable type="over400" response={over400} userId={userId} onSelectStock={onSelectStock} onDetail={(item) => void openDetail(item)} onMessage={(text, isError = false) => setMessage({ text, error: isError })} />
          </section>}
          {over1000 && <section className={`lh-ranking-panel ${activeMobileTab !== "over1000" ? "mobile-hidden" : ""}`}>
            <header><div><span>OVER 1,000 LOTS</span><h2>千張以上大戶變化前20名</h2><p>官方第15級（1,000,001股以上），列出比率與持股張數增減</p></div><strong>{over1000.items.length}<small>／20</small></strong></header>
            <RankingTable type="over1000" response={over1000} userId={userId} onSelectStock={onSelectStock} onDetail={(item) => void openDetail(item)} onMessage={(text, isError = false) => setMessage({ text, error: isError })} />
          </section>}
        </div>
      )}
      <p className="lh-disclaimer"><ShieldAlert size={13} />比率增減百分點與持股張數增減是不同概念；集保資料為週資料。本頁僅供研究參考，不構成投資建議。</p>
      {selected && <LargeHolderDetailModal item={selected} history={history} loading={historyLoading} onClose={() => setSelected(null)} />}
    </div>
  );
}
