"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, Database, Radio, RefreshCw, Users } from "lucide-react";
import type { ChipFlowPoint, ChipFlowResponse } from "@/lib/chip-flow-types";
import { toTaipeiChartTimestamp } from "@/lib/chip-flow-time";

const BUY_COLOR = "#ff5964";
const SELL_COLOR = "#31c48d";

function lots(value: number) {
  return value.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function summaryText(kind: "大單" | "小單", value: number) {
  if (value > 0) return `${kind}累積買超 ${lots(Math.abs(value))} 張`;
  if (value < 0) return `${kind}累積賣超 ${lots(Math.abs(value))} 張`;
  return `${kind}累積買賣超 0 張`;
}

function thresholdText(data: ChipFlowResponse) {
  const threshold = `NT$ ${data.largeOrderThreshold.toLocaleString("zh-TW")}`;
  if (data.largeOrderThresholdMode === "dynamic_percentile") {
    return `動態門檻 P${lots(data.largeOrderThresholdPercentile)}（${data.largeOrderThresholdSampleCount.toLocaleString("zh-TW")} 筆）≥ ${threshold}`;
  }
  return `固定下限（樣本 ${data.largeOrderThresholdSampleCount.toLocaleString("zh-TW")} 筆）≥ ${threshold}`;
}

function FlowChart({
  title,
  points,
  kind,
}: {
  title: string;
  points: ChipFlowPoint[];
  kind: "large" | "small";
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<ChipFlowPoint | null>(null);

  useEffect(() => {
    let disposed = false;
    let cleanup = () => undefined;
    void import("lightweight-charts").then((charts) => {
      if (disposed || !chartRef.current) return;
      const chart = charts.createChart(chartRef.current, {
        width: chartRef.current.clientWidth,
        height: 245,
        layout: { background: { color: "#0a121d" }, textColor: "#7f8da2" },
        grid: {
          vertLines: { color: "rgba(42,55,74,.35)" },
          horzLines: { color: "rgba(42,55,74,.55)" },
        },
        rightPriceScale: { borderColor: "#2a394d", scaleMargins: { top: .14, bottom: .14 } },
        timeScale: {
          borderColor: "#2a394d",
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 1,
        },
        crosshair: {
          mode: charts.CrosshairMode.Normal,
          vertLine: { color: "#697991", labelBackgroundColor: "#33445d" },
          horzLine: { color: "#697991", labelBackgroundColor: "#33445d" },
        },
        handleScroll: true,
        handleScale: true,
      });
      const series = chart.addHistogramSeries({
        priceFormat: { type: "custom", formatter: (value: number) => `${lots(value)} 張` },
        priceLineVisible: false,
        lastValueVisible: true,
      });
      const pointByTime = new Map<number, ChipFlowPoint>();
      series.setData(points.map((point) => {
        const time = toTaipeiChartTimestamp(point.snapshotTime);
        pointByTime.set(time, point);
        const value = kind === "large" ? point.largeNetLots : point.smallNetLots;
        return { time: time as never, value, color: value >= 0 ? BUY_COLOR : SELL_COLOR };
      }));
      series.createPriceLine({
        price: 0,
        color: "#9ba7b9",
        lineWidth: 1,
        lineStyle: charts.LineStyle.Solid,
        axisLabelVisible: true,
        title: "0",
      });
      chart.subscribeCrosshairMove((param) => {
        if (typeof param.time !== "number") {
          setHover(null);
          return;
        }
        setHover(pointByTime.get(param.time) ?? null);
      });
      chart.timeScale().fitContent();
      const observer = new ResizeObserver(() => {
        if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth });
      });
      observer.observe(chartRef.current);
      cleanup = () => {
        observer.disconnect();
        chart.remove();
      };
    });
    return () => {
      disposed = true;
      cleanup();
    };
  }, [kind, points]);

  const latest = points.at(-1) ?? null;
  const shown = hover ?? latest;
  const buy = shown ? (kind === "large" ? shown.largeBuyLots : shown.smallBuyLots) : 0;
  const sell = shown ? (kind === "large" ? shown.largeSellLots : shown.smallSellLots) : 0;
  const net = shown ? (kind === "large" ? shown.largeNetLots : shown.smallNetLots) : 0;

  return (
    <article className="chip-flow-chart-card">
      <header>
        <div><span>{kind === "large" ? "LARGE ORDER" : "SMALL ORDER"}</span><h3>{title}</h3></div>
        {shown && <strong className={net >= 0 ? "text-up" : "text-down"}>{net >= 0 ? "+" : ""}{lots(net)} 張</strong>}
      </header>
      <div className="chip-flow-tooltip" aria-live="polite">
        {shown
          ? <><b>{shown.time}</b><span>累積買進 {lots(buy)} 張</span><span>累積賣出 {lots(sell)} 張</span><span>淨額 {net >= 0 ? "+" : ""}{lots(net)} 張</span></>
          : <span>移到柱狀圖查看明細</span>}
      </div>
      <div ref={chartRef} className="chip-flow-chart" />
    </article>
  );
}

const STATUS_LABELS: Record<string, string> = {
  realtime: "即時",
  delayed: "延遲",
  no_data: "尚無資料",
  awaiting_provider: "等待逐筆行情",
  invalid_symbol: "代號錯誤",
  disconnected: "行情中斷",
};

export function ChipFlowPanel({
  stockId,
  stockName = "",
}: {
  stockId: string;
  stockName?: string;
}) {
  const [data, setData] = useState<ChipFlowResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    if (!/^\d{4,6}$/.test(stockId)) return;
    let stopped = false;
    setLoading(true);
    setError("");
    setData(null);
    const source = new EventSource(
      `/api/stocks/${encodeURIComponent(stockId)}/chip-flow/stream`,
    );
    source.addEventListener("CHIP_FLOW_UPDATE", (event) => {
      if (stopped) return;
      const payload = JSON.parse((event as MessageEvent).data) as ChipFlowResponse;
      setData(payload);
      setLoading(false);
      setError(payload.status === "disconnected" ? payload.statusMessage : "");
    });
    source.addEventListener("CHIP_FLOW_END", () => {
      source.close();
      if (!stopped) setLoading(false);
    });
    source.onerror = () => {
      if (!stopped) {
        setLoading(false);
        setError("即時籌碼通道中斷，正在嘗試重新連線。");
      }
    };
    return () => {
      stopped = true;
      source.close();
    };
  }, [retryKey, stockId]);

  const latest = data?.latest ?? null;
  const status = error ? "disconnected" : data?.status ?? "no_data";
  const refreshSeconds = status === "realtime" || status === "no_data"
    ? 2
    : status === "disconnected"
      ? 5
      : 30;
  const refreshLabel = status === "realtime" || status === "no_data"
    ? `每 ${refreshSeconds} 秒更新`
    : `每 ${refreshSeconds} 秒檢查`;
  const updated = useMemo(() => {
    const value = latest?.updatedAt ?? data?.updatedAt;
    return value ? new Date(value).toLocaleString("zh-TW", {
      hour12: false,
      timeZone: "Asia/Taipei",
    }) : "—";
  }, [data?.updatedAt, latest?.updatedAt]);

  return (
    <section className="chip-flow-panel">
      <header className="chip-flow-heading">
        <div>
          <span>INTRADAY ORDER FLOW · ESTIMATE</span>
          <h2><Activity />盤中大小單籌碼</h2>
          <p>
            <strong>{stockName ? `${stockName}（${stockId}）` : stockId}</strong>
            {" · "}依單筆成交金額與主動買賣方向推估
          </p>
        </div>
        <div className="chip-flow-status">
          <span className={status}><Radio />{STATUS_LABELS[status] ?? status}</span>
          <small>最後更新 {updated} · {refreshLabel}</small>
          <button onClick={() => setRetryKey((value) => value + 1)} disabled={loading}>
            <RefreshCw className={loading ? "spinning" : ""} />重新整理
          </button>
        </div>
      </header>

      {loading && !data ? (
        <div className="chip-flow-empty"><span className="spinner" /><p>正在檢查逐筆成交行情…</p></div>
      ) : error ? (
        <div className="chip-flow-empty error">
          <AlertTriangle />
          <h3>盤中籌碼服務暫時無法連線</h3>
          <p>{error}</p>
        </div>
      ) : !data?.series.length || !latest ? (
        <div className="chip-flow-empty waiting">
          <Database />
          <h3>目前尚無足夠逐筆成交資料，暫時無法計算大小單買賣超。</h3>
          <p>{data?.statusMessage}</p>
          {!!data?.missingFields?.length && (
            <div><strong>目前缺少</strong>{data.missingFields.map((field) => <span key={field}>{field}</span>)}</div>
          )}
          <small>正式環境不會以隨機數字、累積成交量或測試 Mock 資料代替。</small>
        </div>
      ) : (
        <>
          <div className="chip-flow-summary">
            <article className={latest.largeNetLots >= 0 ? "positive" : "negative"}>
              <span>即時大單買賣超</span>
              <strong>{summaryText("大單", latest.largeNetLots)}</strong>
              <small>{thresholdText(data)} · {refreshLabel}</small>
            </article>
            <article className={latest.smallNetLots >= 0 ? "positive" : "negative"}>
              <span>即時小單買賣超</span>
              <strong>{summaryText("小單", latest.smallNetLots)}</strong>
              <small>門檻 &lt; NT$ {data.smallOrderThreshold.toLocaleString("zh-TW")} · {refreshLabel}</small>
            </article>
            <article className="retail">
              <span><Users />估算指標</span>
              <strong>散戶成交占比 {latest.retailControlRatio == null ? "—" : `${lots(latest.retailControlRatio)}%`}</strong>
              <small>小單成交股數 ÷ 已分類成交股數</small>
            </article>
          </div>
          <div className="chip-flow-exclusions">
            <span>
              <b>13:30 集合競價</b>
              {lots(data.excludedClosingAuctionLots)} 張
              <em>獨立排除</em>
            </span>
            <span>
              <b>盤後成交</b>
              {lots(data.excludedAfterHoursLots)} 張
              <em>不納入</em>
            </span>
            <small>圖表僅累積 09:00 至 13:30 前的盤中成交，避免收盤集合競價與盤後鉅量扭曲方向。</small>
          </div>
          <div className="chip-flow-chart-grid">
            <FlowChart title="即時大單買賣超" points={data.series} kind="large" />
            <FlowChart title="即時小單買賣超" points={data.series} kind="small" />
          </div>
        </>
      )}

      <footer>
        <AlertTriangle />
        <span>{data?.notice ?? "大小單係依單筆成交金額與成交方向推估，不代表真實投資人身分，亦可能受到拆單影響。"}</span>
        <b>推估值</b>
      </footer>
    </section>
  );
}
