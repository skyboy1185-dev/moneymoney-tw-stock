"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUpRight, BellRing, CalendarClock, LockKeyhole,
  Play, RefreshCw, Repeat2, ShieldCheck, Sparkles, Telescope, Trophy,
} from "lucide-react";
import type {
  LongTermMode, LongTermPortfolioResponse, LongTermTradeMessage, LongTermYtdBacktestResponse,
} from "@/lib/long-term-types";

function percent(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function price(value: number): string {
  return new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
}

function money(value: number): string {
  const amount = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(Math.abs(value));
  return `${value < 0 ? "-" : ""}NT$${amount}`;
}

function returnClass(value: number): string {
  return value > 0 ? "profit" : value < 0 ? "loss" : "";
}

function eventTime(value: string): string {
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date(value));
}

function holdingsLabel(symbols: string[]): string {
  if (symbols.length <= 12) return symbols.join("、") || "現金";
  return `${symbols.slice(0, 12).join("、")}…共${symbols.length}檔`;
}

export function LongTermSelectionPage({ onSelectStock }: { onSelectStock: (symbol: string) => void }) {
  const [mode, setMode] = useState<LongTermMode>("long_only");
  const [data, setData] = useState<LongTermPortfolioResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [replacing, setReplacing] = useState<number | null>(null);
  const [backtest, setBacktest] = useState<LongTermYtdBacktestResponse | null>(null);
  const [backtesting, setBacktesting] = useState(false);
  const [backtestError, setBacktestError] = useState("");
  const [error, setError] = useState("");
  const [eventFilter, setEventFilter] = useState<"ALL" | "BUY" | "SELL">("ALL");
  const eventCursor = useRef<Record<LongTermMode, number>>({ long_only: 0, focused_long: 0 });
  const eventInitialized = useRef<Record<LongTermMode, boolean>>({ long_only: false, focused_long: false });

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/long-term/portfolio?mode=${mode}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "長線選股資料讀取失敗");
      const typedPayload = payload as LongTermPortfolioResponse;
      if (!eventInitialized.current[mode]) {
        eventCursor.current[mode] = Math.max(0, ...typedPayload.tradeMessages.map((item) => item.id));
        eventInitialized.current[mode] = true;
      }
      setData(typedPayload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "長線選股資料讀取失敗");
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const poll = async () => {
      if (!eventInitialized.current[mode]) return;
      try {
        const response = await fetch(`/api/long-term/events?mode=${mode}&afterId=${eventCursor.current[mode]}&limit=50`, { cache: "no-store" });
        const payload = await response.json() as { items?: LongTermTradeMessage[] };
        if (!response.ok || !payload.items?.length) return;
        const fresh = payload.items;
        eventCursor.current[mode] = Math.max(eventCursor.current[mode], ...fresh.map((item) => item.id));
        setData((current) => current && current.mode === mode ? {
          ...current,
          tradeMessages: [...fresh.map((item) => ({ ...item, isRead: true })), ...current.tradeMessages]
            .filter((item, index, rows) => rows.findIndex((candidate) => candidate.id === item.id) === index)
            .sort((left, right) => right.id - left.id)
            .slice(0, 100),
          unreadTradeMessageCount: current.unreadTradeMessageCount,
        } : current);
        fresh.forEach((item) => {
          void fetch(`/api/long-term/events/${item.id}/read`, { method: "POST" });
        });
      } catch {
        // Keep portfolio polling alive when an event request temporarily fails.
      }
    };
    const timer = window.setInterval(() => void poll(), 5_000);
    return () => window.clearInterval(timer);
  }, [mode]);

  const replace = async (id: number) => {
    setReplacing(id);
    setError("");
    try {
      const response = await fetch(`/api/long-term/positions/${id}/replace`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? payload.error ?? "汰換失敗");
      await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "汰換失敗");
    } finally {
      setReplacing(null);
    }
  };

  const runYtdBacktest = async () => {
    setBacktesting(true);
    setBacktestError("");
    try {
      const response = await fetch("/api/long-term/backtest/ytd", { cache: "no-store" });
      const payload = await response.json() as LongTermYtdBacktestResponse & { detail?: string; error?: string };
      if (!response.ok) throw new Error(payload.detail ?? payload.error ?? "年初至今回測失敗");
      setBacktest(payload);
    } catch (reason) {
      setBacktestError(reason instanceof Error ? reason.message : "年初至今回測失敗");
    } finally {
      setBacktesting(false);
    }
  };

  const visibleMessages = data?.tradeMessages.filter((item) => eventFilter === "ALL" || item.eventType === eventFilter) ?? [];
  const cagrTop50 = data?.performanceComparison.rows.find(
    (row) => row.benchmarkType === "ten_year_cagr_group",
  )?.constituents ?? [];

  return <div className="long-term-page">
    <header className="long-term-heading">
      <div>
        <p className="section-kicker">LONG-TERM MODEL PORTFOLIO</p>
        <h1><Telescope size={24} />長線選股</h1>
        <p>每日 09:15 依官方行情更新模型：10 檔穩健組與 3 檔精選組，各自使用 100 萬模擬本金。</p>
      </div>
      <button type="button" onClick={() => void load()} disabled={loading}>
        <RefreshCw size={15} className={loading ? "spin-icon" : ""} />更新損益
      </button>
    </header>

    <div className="long-term-mode-tabs">
      <button className={mode === "long_only" ? "active" : ""} onClick={() => setMode("long_only")}>
        <ArrowUpRight size={16} /><span><strong>只做多</strong><small>10 檔多方組合</small></span>
      </button>
      <button className={mode === "focused_long" ? "active" : ""} onClick={() => setMode("focused_long")}>
        <Sparkles size={16} /><span><strong>精選做多</strong><small>3 檔多方組合・獨立 100 萬</small></span>
      </button>
    </div>

    {error && <div className="error-banner">{error}</div>}
    {loading && !data ? <div className="table-loading"><span className="spinner" /><span>正在載入長線模型組合…</span></div> : data && <>
      <section className="long-term-summary">
        <article><span>本組模擬本金</span><strong>{money(data.capitalAllocation.totalCapital)}</strong><small>兩個組合各自獨立計算</small></article>
        <article><span>目前持倉</span><strong>{data.summary.openCount} / {data.targetCount}</strong><small>多 {data.summary.longCount}・空 {data.summary.shortCount}</small></article>
        <article><span>累計含息損益</span><strong className={returnClass(data.capitalAllocation.totalProfit)}>{money(data.capitalAllocation.totalProfit)}</strong><small>股息 {money(data.capitalAllocation.dividendIncome)}・淨值 {money(data.capitalAllocation.estimatedEquity)}</small></article>
        <article><span>預估一個月報酬</span><strong className="forecast">{percent(data.summary.predictedMonthReturnPercent)}</strong><small>模型估計，非保證報酬</small></article>
        <article><span>已實現平均損益</span><strong className={returnClass(data.summary.realizedReturnPercent)}>{percent(data.summary.realizedReturnPercent)}</strong><small>已完成 {data.summary.completedTradeCount} 筆</small></article>
        <article><span>最近選股</span><strong>{data.lastSelectionDate ?? "明日開始"}</strong><small>每日 {data.selectionTime} 執行一次</small></article>
      </section>

      <section className="long-term-performance">
        <div className="long-term-section-title">
          <Trophy size={16} /><div><h2>模型績效挑戰</h2><p>{data.performanceComparison.goal}・含息總報酬</p></div>
          <span className={`benchmark-goal ${data.performanceComparison.beatsAllBenchmarks ? "achieved" : "tracking"}`}>
            {data.performanceComparison.beatsAllBenchmarks ? "目前全部領先" : "持續追蹤中"}
          </span>
        </div>
        <div className="long-term-table-wrap"><table className="benchmark-table">
          <thead><tr><th>比較標的</th><th>近 10 年年化</th><th>比較起算日</th><th>起始淨值／價格</th><th>目前淨值／價格</th><th>含息總報酬</th><th>模型超額績效</th><th>目前狀態</th></tr></thead>
          <tbody>{data.performanceComparison.rows.map((row) => <tr key={row.key} className={row.isModel ? "model-row" : ""}>
            <td><strong>{row.rank10Year ? `#${row.rank10Year} ${row.name}` : row.name}</strong>
              {row.symbol && <small>{row.symbol}{row.selectionDate ? `・${row.selectionDate} 入選` : ""}</small>}
              {row.constituents?.length ? <small>{row.constituents.map((item) => item.symbol).join("、")}・每檔 2%</small> : null}
            </td>
            <td>{row.annualizedReturn10Year == null ? "—" : <><strong className={returnClass(row.annualizedReturn10Year)}>{percent(row.annualizedReturn10Year)}</strong><small>{row.historyStartDate} ～ {row.historyEndDate}</small></>}</td>
            <td>{row.startDate}</td>
            <td>{row.startPrice === null ? "待建立" : price(row.startPrice)}</td>
            <td>{row.currentPrice === null ? "待行情" : price(row.currentPrice)}</td>
            <td><strong className={returnClass(row.cumulativeReturnPercent)}>{percent(row.cumulativeReturnPercent)}</strong><small>價差 {percent(row.priceReturnPercent)}・股息 {row.dividendDataAvailable ? percent(row.dividendReturnPercent) : "資料待補"}</small></td>
            <td>{row.isModel || row.leadVsBenchmarkPercent === null ? "—" : <strong className={returnClass(row.leadVsBenchmarkPercent)}>{percent(row.leadVsBenchmarkPercent)}</strong>}</td>
            <td>{row.isModel
              ? <span className="benchmark-status model">挑戰基準</span>
              : <span className={`benchmark-status ${row.status}`}>{row.status === "leading" ? "模型領先" : row.status === "trailing" ? "模型落後" : "績效持平"}</span>}</td>
          </tr>)}</tbody>
        </table></div>
        {cagrTop50.length > 0 && <div className="benchmark-constituent-detail">
          <header><div><strong>近10年年化報酬率最高50檔｜模擬買進點位</strong><small>組合採等權重，每檔配置 2%；買進價為首次加入績效比較時保存的行情。</small></div></header>
          <div className="long-term-table-wrap"><table>
            <thead><tr><th>排名</th><th>股票</th><th>配置</th><th>模擬買進日</th><th>模擬買進點位</th><th>目前價格</th><th>含息總報酬</th><th>近10年年化</th></tr></thead>
            <tbody>{cagrTop50.map((item) => <tr key={item.symbol}>
              <td>#{item.rank}</td>
              <td><button type="button" className="long-term-stock" onClick={() => onSelectStock(item.symbol)}><strong>{item.name}</strong><span>{item.symbol}</span></button></td>
              <td>{item.allocationWeightPercent.toFixed(0)}%</td>
              <td>{item.startDate ?? "待建立"}</td>
              <td><strong>{item.entryPrice == null ? "待行情" : `${price(item.entryPrice)} 元`}</strong></td>
              <td>{item.currentPrice == null ? "待行情" : `${price(item.currentPrice)} 元`}</td>
              <td><strong className={returnClass(item.returnPercent)}>{percent(item.returnPercent)}</strong><small>價差 {percent(item.priceReturnPercent)}・股息 {item.dividendDataAvailable ? percent(item.dividendReturnPercent) : "待補"}</small></td>
              <td><strong className={returnClass(item.annualizedReturn10Year)}>{percent(item.annualizedReturn10Year)}</strong></td>
            </tr>)}</tbody>
          </table></div>
        </div>}
        <div className="long-term-ytd-backtest">
          <header>
            <div><strong>策略歷史回測｜今年年初至今</strong><small>比較50檔穩健輪動、10檔與3檔模型、三個ETF及近10年年化最高50檔</small></div>
            <button type="button" onClick={() => void runYtdBacktest()} disabled={backtesting}>
              {backtesting ? <span className="spinner small" /> : <Play size={12} />}
              {backtesting ? "正在下載行情並回測…" : backtest ? "重新顯示回測" : "回測今年"}
            </button>
          </header>
          {backtestError && <div className="long-term-backtest-error">{backtestError}</div>}
          {backtest && <>
            <div className="long-term-backtest-meta">
              <span>{backtest.periodLabel}：{backtest.fromDate} ～ {backtest.toDate}</span>
              <span>有效股票池 {backtest.universeCount} / {backtest.requestedUniverseCount} 檔</span>
              <span>含配息再投入總報酬</span>
              <span>{backtest.dataSource}</span>
            </div>
            <div className="long-term-table-wrap"><table className="long-term-backtest-table">
              <thead><tr><th>排名</th><th>策略／基準</th><th>年初至今含息</th><th>相對0050</th><th>最大回撤</th><th>買進次數</th><th>汰換次數</th><th>目前持股</th></tr></thead>
              <tbody>{backtest.rows.map((row) => <tr key={row.key} className={row.strategyType === "model" ? "model-row" : ""}>
                <td>#{row.rank}</td>
                <td><strong>{row.name}</strong>{row.symbol && <small>{row.symbol}</small>}</td>
                <td><strong className={returnClass(row.returnPercent)}>{percent(row.returnPercent)}</strong></td>
                <td><strong className={returnClass(row.leadVs0050Percent)}>{percent(row.leadVs0050Percent)}</strong></td>
                <td><strong className={returnClass(row.maximumDrawdownPercent)}>{percent(row.maximumDrawdownPercent)}</strong></td>
                <td>{row.entryCount}</td><td>{row.replacementCount}</td>
                <td><span className="backtest-holdings">{holdingsLabel(row.currentHoldings)}</span></td>
              </tr>)}</tbody>
            </table></div>
            <div className="stable-rotation-variants">
              <header><div><strong>50檔穩健輪動｜持有期參數比較</strong><small>{backtest.stableRotation.selectionMethod}</small></div><b>選定 {backtest.stableRotation.selectedMinimumHoldingDays} 日</b></header>
              <div className="stable-rule-strip">
                <span>持股 {backtest.stableRotation.rules.targetCount} 檔</span>
                <span>{backtest.stableRotation.rules.reviewFrequency}檢查</span>
                <span>前 {backtest.stableRotation.rules.protectedRank} 名保護</span>
                <span>新股至少高 {backtest.stableRotation.rules.minimumScoreGap} 分</span>
                <span>每週最多換 {backtest.stableRotation.rules.maximumWeeklyReplacements} 檔</span>
              </div>
              <div className="long-term-table-wrap"><table className="stable-variant-table">
                <thead><tr><th>最低持有</th><th>年初至今含息</th><th>最大回撤</th><th>均衡分</th><th>買進次數</th><th>汰換次數</th><th>每週檢查</th><th>判定</th></tr></thead>
                <tbody>{backtest.stableRotation.variants.map((variant) => <tr key={variant.minimumHoldingDays} className={variant.selected ? "selected" : ""}>
                  <td><strong>{variant.minimumHoldingDays} 個交易日</strong></td>
                  <td><strong className={returnClass(variant.returnPercent)}>{percent(variant.returnPercent)}</strong></td>
                  <td><strong className={returnClass(variant.maximumDrawdownPercent)}>{percent(variant.maximumDrawdownPercent)}</strong></td>
                  <td>{variant.balanceScore.toFixed(2)}</td><td>{variant.entryCount}</td><td>{variant.replacementCount}</td><td>{variant.weeklyReviewCount} 次</td>
                  <td>{variant.selected ? <span className="variant-selected">均衡最佳</span> : "比較組"}</td>
                </tr>)}</tbody>
              </table></div>
            </div>
            <div className="long-term-backtest-notes"><p>{backtest.methodology}</p><ul>{backtest.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>
          </>}
        </div>
        <footer>{data.performanceComparison.methodology}</footer>
      </section>

      <section className="long-term-models">
        <div className="long-term-section-title"><Sparkles size={16} /><div><h2>本組合選股模型</h2><p>每檔採最高適配模型入選</p></div></div>
        <div>{data.models.map((model) => <article key={model.key}><strong>{model.name}</strong><p>{model.description}</p></article>)}</div>
      </section>

      <section className="long-term-positions">
        <div className="long-term-section-title">
          <ShieldCheck size={16} /><div><h2>{data.targetCount} 檔模擬追蹤</h2><p>前 5 個交易日鎖定；期滿後可手動或由模型賣出汰換</p></div>
        </div>
        {!data.items.length ? <div className="long-term-waiting">
          <CalendarClock size={30} /><h3>首批選股將於 2026/8/10 09:15 建立</h3><p>系統會自動選出 {data.targetCount} 檔多方標的，並開始記錄每日實際損益與買賣訊息。</p>
        </div> : <div className="long-term-table-wrap"><table>
          <thead><tr><th>#</th><th>股票</th><th>方向／模型</th><th>買進比重</th><th>分配資金／股數</th><th>進場資料</th><th>目前價格</th><th>含息總損益</th><th>預估一個月</th><th>模型分數</th><th>持有進度</th><th>動作</th></tr></thead>
          <tbody>{data.items.map((item, index) => <tr key={item.id}>
            <td>{index + 1}</td>
            <td><button className="long-term-stock" onClick={() => onSelectStock(item.symbol)}><strong>{item.name}</strong><span>{item.symbol}・{item.industry}</span></button></td>
            <td><span className="long-term-direction long"><ArrowUpRight size={12} />做多</span><small>{item.modelName}</small></td>
            <td><strong className="allocation-weight">{item.allocationWeightPercent.toFixed(2)}%</strong></td>
            <td><strong>{money(item.allocatedCapital)}</strong><small>{item.quantity.toLocaleString("zh-TW")} 股・成交 {money(item.investedCapital)}</small></td>
            <td><strong>{price(item.entryPrice)}</strong><small>{item.entryDate}</small></td>
            <td><strong>{price(item.currentPrice)}</strong></td>
            <td><strong className={returnClass(item.actualReturnPercent)}>{percent(item.actualReturnPercent)}</strong><small className={returnClass(item.unrealizedProfit)}>{money(item.unrealizedProfit)}</small><small>價差 {percent(item.priceReturnPercent)}・股息 {item.dividendDataAvailable ? `${percent(item.dividendReturnPercent)}／${money(item.dividendIncome)}` : "資料待補"}</small></td>
            <td><strong className="forecast">{percent(item.predictedMonthReturnPercent)}</strong></td>
            <td><strong>{item.currentScore.toFixed(1)}</strong><small>入選 {item.selectionScore.toFixed(1)}</small></td>
            <td><div className="holding-progress"><i><b style={{ width: `${Math.min(100, item.holdingTradingDays / item.minimumHoldingDays * 100)}%` }} /></i><span>{item.holdingTradingDays} / {item.minimumHoldingDays} 天</span></div></td>
            <td>{item.eligibleToReplace
              ? <button className="replace-position" onClick={() => void replace(item.id)} disabled={replacing !== null}>{replacing === item.id ? <span className="spinner small" /> : <Repeat2 size={13} />}賣出並補位</button>
              : <span className="position-locked"><LockKeyhole size={12} />鎖定至 {item.minimumExitDate}</span>}</td>
          </tr>)}</tbody>
        </table></div>}
        <footer>{data.capitalAllocation.methodology} {data.notice} 股息資料：{data.dividendData.source}（{data.dividendData.availableCount}/{data.dividendData.requestedCount} 檔可用）。</footer>
      </section>

      {data.closedItems.length > 0 && <section className="long-term-history">
        <div className="long-term-section-title"><CalendarClock size={16} /><div><h2>歷史汰換紀錄</h2><p>保留進出價格、配息與含息總報酬</p></div></div>
        <div className="long-term-table-wrap"><table><thead><tr><th>股票</th><th>方向</th><th>模型</th><th>進場日</th><th>出場日</th><th>進／出價格</th><th>含息總損益</th><th>原因</th></tr></thead><tbody>
          {data.closedItems.map((item) => <tr key={item.id}><td>{item.name}<small>{item.symbol}</small></td><td>多</td><td>{item.modelName}</td><td>{item.entryDate}</td><td>{item.exitDate}</td><td>{price(item.entryPrice)} → {price(item.exitPrice)}</td><td className={returnClass(item.actualReturnPercent)}><strong>{percent(item.actualReturnPercent)}</strong><small>價差 {percent(item.priceReturnPercent)}・股息 {item.dividendDataAvailable ? `${percent(item.dividendReturnPercent)}／${money(item.dividendIncome)}` : "資料待補"}</small></td><td>{item.exitReason}</td></tr>)}
        </tbody></table></div>
      </section>}

      <section className="long-term-events">
        <div className="long-term-section-title">
          <BellRing size={16} /><div><h2>長線選股交易訊息</h2><p>本組合所有模擬買進、賣出與汰換紀錄</p></div>
          <span className="long-term-event-count">共 {data.tradeMessages.length} 筆{data.unreadTradeMessageCount > 0 ? `・未讀 ${data.unreadTradeMessageCount}` : ""}</span>
        </div>
        <div className="long-term-event-filters">
          {([{"key":"ALL","label":"全部"},{"key":"BUY","label":"買進"},{"key":"SELL","label":"賣出"}] as const).map((filter) => <button key={filter.key} type="button" className={eventFilter === filter.key ? "active" : ""} onClick={() => setEventFilter(filter.key)}>{filter.label}</button>)}
        </div>
        {!visibleMessages.length ? <div className="long-term-event-empty">目前尚無{eventFilter === "BUY" ? "買進" : eventFilter === "SELL" ? "賣出" : "交易"}訊息，模型建立部位後會自動記錄在這裡。</div> : <div className="long-term-event-list">
          {visibleMessages.map((item) => <article key={item.id} className={item.eventType.toLowerCase()}>
            <time>{eventTime(item.timestamp)}</time>
            <span className="event-kind">{item.eventType === "BUY" ? "🟢 BUY" : item.pnl !== null && item.pnl >= 0 ? "💰 SELL" : "🔴 SELL"}</span>
            <div><strong>{item.stockCode} {item.stockName}</strong><p>{item.reason}</p></div>
            <div className="event-numbers"><strong>{price(item.price)} 元・{item.quantity.toLocaleString("zh-TW")} 股</strong><small>配置 {item.allocationWeightPercent.toFixed(2)}%・{money(item.allocatedCapital)}</small></div>
            <div>{item.pnlPercent === null ? <strong>建立部位</strong> : <><strong className={returnClass(item.pnlPercent)}>{percent(item.pnlPercent)}</strong><small className={returnClass(item.pnl ?? 0)}>{money(item.pnl ?? 0)}</small></>}</div>
          </article>)}
        </div>}
      </section>
    </>}
  </div>;
}
