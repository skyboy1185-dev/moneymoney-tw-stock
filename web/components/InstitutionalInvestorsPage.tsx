"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowDownRight,
  ArrowUpRight,
  Database,
  Landmark,
  RefreshCw,
  Scale,
  Users,
} from "lucide-react";
import type { InstitutionalInvestorResponse } from "@/lib/institutional-investor-types";

function money(value: number, compact = true) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const absolute = Math.abs(value);
  if (compact && absolute >= 100_000_000) {
    return `${sign}${(absolute / 100_000_000).toLocaleString("zh-TW", { maximumFractionDigits: 2 })} 億`;
  }
  if (compact && absolute >= 10_000) {
    return `${sign}${(absolute / 10_000).toLocaleString("zh-TW", { maximumFractionDigits: 0 })} 萬`;
  }
  return `${sign}${absolute.toLocaleString("zh-TW")} 元`;
}

function flowClass(value: number) {
  return value > 0 ? "text-up" : value < 0 ? "text-down" : "";
}

function FlowValue({ value }: { value: number }) {
  return (
    <span className={`institution-flow-value ${flowClass(value)}`}>
      {value > 0 ? <ArrowUpRight /> : value < 0 ? <ArrowDownRight /> : null}
      {money(value)}
    </span>
  );
}

function DailyAmount({ label, value }: { label: string; value: number }) {
  return <span className="institution-daily-amount"><small>{label}</small><b>{money(value)}</b></span>;
}

function contracts(value: number) {
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toLocaleString("zh-TW")} 口`;
}

function totalContracts(value: number) {
  return `${value.toLocaleString("zh-TW")} 口`;
}

function ratioLabel(value: number) {
  return `${value > 0 ? "+" : ""}${value.toLocaleString("zh-TW", { maximumFractionDigits: 1 })}%`;
}

export function InstitutionalInvestorsPage() {
  const [data, setData] = useState<InstitutionalInvestorResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const historyScrollRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/institutional-investors?t=${Date.now()}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "三大法人資料載入失敗。");
      setData(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "三大法人資料載入失敗。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!data?.retailFutures.history.length) return;
    const frame = requestAnimationFrame(() => {
      const element = historyScrollRef.current;
      if (element) element.scrollLeft = element.scrollWidth;
    });
    return () => cancelAnimationFrame(frame);
  }, [data]);

  if (loading && !data) {
    return <div className="page-loading"><span className="spinner" /><p>正在彙整上市與上櫃三大法人資料…</p></div>;
  }

  if (!data) {
    return (
      <div className="institution-error">
        <AlertCircle />
        <h2>目前無法取得官方資料</h2>
        <p>{error}</p>
        <button onClick={() => void load()}><RefreshCw />重新載入</button>
      </div>
    );
  }

  return (
    <section className="institution-page">
      <header className="institution-hero">
        <div>
          <span className="institution-eyebrow">INSTITUTIONAL FLOW</span>
          <h1><Landmark />三大法人買賣超</h1>
          <p>外資、投信、自營商｜上市＋上櫃市場買賣金額彙整</p>
        </div>
        <div className="institution-hero-actions">
          <span><Database />官方資料日：<b>{data.asOfDate}</b></span>
          <button onClick={() => void load()} disabled={loading}>
            <RefreshCw className={loading ? "spinning" : ""} />更新
          </button>
        </div>
      </header>

      {error && <div className="institution-inline-error"><AlertCircle />{error}</div>}

      <section className="retail-futures-panel">
        <header>
          <div>
            <span>RETAIL FUTURES POSITION</span>
            <h2><Users />散戶期貨多空比</h2>
          </div>
          <p><Scale />期交所盤後未平倉資料日：<b>{data.retailFutures.asOfDate}</b></p>
        </header>
        <article className="foreign-short-card">
          <div className="foreign-short-title">
            <span><Scale /></span>
            <div>
              <small>FOREIGN TX NET POSITION</small>
              <h3>外資多空淨額</h3>
              <p>臺股期貨 TX・多方未平倉 − 空方未平倉</p>
            </div>
          </div>
          <div className="foreign-short-total">
            <span>{data.retailFutures.foreignNet.asOfDate} 多空淨額</span>
            <strong className={flowClass(data.retailFutures.foreignNet.net)}>
              {contracts(data.retailFutures.foreignNet.net)}
              {data.retailFutures.foreignNet.net > 0 ? "（淨多）" : data.retailFutures.foreignNet.net < 0 ? "（淨空）" : "（持平）"}
            </strong>
            <small>
              多單 {totalContracts(data.retailFutures.foreignNet.long)}・空單 {totalContracts(data.retailFutures.foreignNet.short)}
            </small>
          </div>
          <div className="foreign-short-change">
            <span>淨額較前一交易日</span>
            {data.retailFutures.foreignNet.change == null ? (
              <strong>暫無比較資料</strong>
            ) : (
              <strong className={flowClass(data.retailFutures.foreignNet.change)}>
                {contracts(data.retailFutures.foreignNet.change)}
              </strong>
            )}
            <small>
              {data.retailFutures.foreignNet.previousDate && data.retailFutures.foreignNet.previousNet != null
                ? `${data.retailFutures.foreignNet.previousDate}：${contracts(data.retailFutures.foreignNet.previousNet)}`
                : "等待前一交易日資料"}
            </small>
          </div>
          <p className="foreign-short-note">正值代表淨多、負值代表淨空；淨額增加以紅色表示、減少以綠色表示。這是外資留倉部位，不是當日成交量。</p>
        </article>
        <div className="retail-futures-grid">
          {data.retailFutures.items.map((item) => {
            const marker = Math.max(0, Math.min(100, (item.ratioPct + 100) / 2));
            return (
              <article className="retail-futures-card" key={item.id}>
                <header>
                  <div>
                    <span>{item.contract}</span>
                    <h3>{item.label}</h3>
                  </div>
                  <em className={flowClass(item.ratioPct)}>{item.bias}</em>
                </header>
                <div className={`retail-ratio ${flowClass(item.ratioPct)}`}>
                  <strong>{item.ratioPct > 0 ? "+" : ""}{item.ratioPct.toLocaleString("zh-TW", { maximumFractionDigits: 2 })}%</strong>
                  <small>散戶淨部位／全市場未平倉量</small>
                </div>
                <div className="retail-sentiment-track" aria-label={`空方至多方，目前 ${item.ratioPct.toFixed(2)}%`}>
                  <span className="bear">散戶偏空</span>
                  <span className="bull">散戶偏多</span>
                  <i style={{ left: `${marker}%` }} />
                </div>
                <dl>
                  <div><dt>推算散戶多單</dt><dd className="text-up">{contracts(item.retailLong)}</dd></div>
                  <div><dt>推算散戶空單</dt><dd className="text-down">{contracts(item.retailShort)}</dd></div>
                  <div><dt>散戶淨部位</dt><dd className={flowClass(item.retailNet)}>{contracts(item.retailNet)}</dd></div>
                  <div><dt>全市場未平倉</dt><dd>{contracts(item.marketOpenInterest)}</dd></div>
                </dl>
              </article>
            );
          })}
        </div>
        <div className="retail-history">
          <header>
            <div>
              <span>5-MONTH TREND</span>
              <h3>近 5 個月每日散戶多空比變化</h3>
            </div>
            <div className="retail-history-legend">
              <span><i className="mini" />小台 MTX</span>
              <span><i className="micro" />微台 TMF</span>
              <b>共 {data.retailFutures.history.length} 個交易日</b>
            </div>
          </header>
          <div className="retail-history-chart">
            <div className="retail-history-axis">
              <span>+100%</span><span>0%</span><span>−100%</span>
            </div>
            <div className="retail-history-scroll" ref={historyScrollRef}>
              <div
                className="retail-history-plot"
                style={{
                  gridTemplateColumns: `repeat(${data.retailFutures.history.length}, 28px)`,
                  width: `${data.retailFutures.history.length * 28}px`,
                }}
              >
                <div className="retail-zero-line" />
                {data.retailFutures.history.map((point, index) => {
                  const monthChanged = index === 0
                    || data.retailFutures.history[index - 1].date.slice(0, 7) !== point.date.slice(0, 7);
                  return (
                    <div className={`retail-history-group ${monthChanged ? "month-start" : ""}`} key={point.date}>
                      <div className="retail-history-bars">
                        {([
                          ["mini", point.miniRatioPct, point.miniNet],
                          ["micro", point.microRatioPct, point.microNet],
                        ] as const).map(([series, value, net]) => {
                          const height = `${Math.max(value === 0 ? 0 : 2, Math.min(100, Math.abs(value)) / 2)}%`;
                          return (
                            <i
                              className={`${series} ${value >= 0 ? "positive" : "negative"}`}
                              key={series}
                              style={value >= 0 ? { height, bottom: "50%" } : { height, top: "50%" }}
                              title={`${point.date}｜${series === "mini" ? "小台" : "微台"} ${ratioLabel(value)}｜淨部位 ${contracts(net)}`}
                            >
                              <span>{ratioLabel(value)}</span>
                            </i>
                          );
                        })}
                      </div>
                      <time dateTime={point.date}>
                        {monthChanged ? `${Number(point.date.slice(5, 7))}月` : Number(point.date.slice(8, 10))}
                      </time>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
        <footer>
          <strong>{data.retailFutures.formula}</strong>
          <span>{data.retailFutures.notice} <a href={data.retailFutures.sourceUrl} target="_blank" rel="noreferrer">查看期交所原始資料</a></span>
        </footer>
      </section>

      <article className="institution-table-panel">
        <header>
          <div>
            <span>MARKET DETAIL</span>
            <h2>各法人買賣金額明細</h2>
          </div>
          <p>紅色為買超，綠色為賣超；所有金額均為新臺幣</p>
        </header>
        <div className="institution-table-wrap">
          <table className="institution-table">
            <thead>
              <tr>
                <th>法人</th>
                <th>當日買進</th>
                <th>當日賣出</th>
                <th>當日買賣超</th>
                <th>上市／上櫃（當日）</th>
                <th>本月買賣超</th>
                <th>本年買賣超</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.id} className={item.id === "total" ? "total" : ""}>
                  <td><strong>{item.label}</strong>{item.id === "total" && <small>上市＋上櫃</small>}</td>
                  <td><DailyAmount label="買進" value={item.day.total.buy} /></td>
                  <td><DailyAmount label="賣出" value={item.day.total.sell} /></td>
                  <td><FlowValue value={item.day.total.net} /></td>
                  <td>
                    <div className="institution-market-split">
                      <span>上市 <b className={flowClass(item.day.listed.net)}>{money(item.day.listed.net)}</b></span>
                      <span>上櫃 <b className={flowClass(item.day.otc.net)}>{money(item.day.otc.net)}</b></span>
                    </div>
                  </td>
                  <td><FlowValue value={item.month.total.net} /></td>
                  <td><FlowValue value={item.year.total.net} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      <footer className="institution-note">
        <Database />
        <div>
          <strong>{data.dataNotice}</strong>
          <span>
            資料來源：
            {data.sources.map((source, index) => (
              <span key={source.market}>{index > 0 && "、"}<a href={source.url} target="_blank" rel="noreferrer">{source.provider}</a></span>
            ))}
            。更新時間 {new Date(data.updatedAt).toLocaleString("zh-TW", { hour12: false })}
          </span>
        </div>
      </footer>
    </section>
  );
}
