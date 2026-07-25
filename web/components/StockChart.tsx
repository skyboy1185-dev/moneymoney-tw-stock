"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, BarChart3, Gauge, ShieldAlert, TrendingUp } from "lucide-react";
import type { DailyPrice, TechnicalIndicator } from "@/lib/types";
import { analyzeTechnicalData } from "@/lib/technical-analysis";
import { formatPercent, formatVolume, safeNumber } from "@/lib/format";

const MA_CONFIG = [
  { key: "ma5", label: "MA5", color: "#ffd166" },
  { key: "ma10", label: "MA10", color: "#fa8cff" },
  { key: "ma20", label: "MA20", color: "#62a8ff" },
  { key: "ma60", label: "MA60", color: "#38d9c5" },
  { key: "ma120", label: "MA120", color: "#ff9f43" },
] as const;

function timeKey(time: unknown): string | null {
  if (typeof time === "string") return time;
  if (time && typeof time === "object" && "year" in time && "month" in time && "day" in time) {
    const item = time as { year: number; month: number; day: number };
    return `${item.year}-${String(item.month).padStart(2, "0")}-${String(item.day).padStart(2, "0")}`;
  }
  return null;
}

function signalClass(signal: string) {
  if (signal === "entry" || signal === "add") return "positive";
  if (signal === "reduce") return "warning";
  if (signal === "exit") return "danger";
  return "neutral";
}

export function StockChart({
  prices,
  indicators,
  analysisPrices = prices,
  analysisIndicators = indicators,
  marketOpen = false,
}: {
  prices: DailyPrice[];
  indicators: TechnicalIndicator[];
  analysisPrices?: DailyPrice[];
  analysisIndicators?: TechnicalIndicator[];
  marketOpen?: boolean;
}) {
  const priceRef = useRef<HTMLDivElement>(null);
  const volumeRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);
  const [activeMAs, setActiveMAs] = useState<string[]>(["ma5", "ma10", "ma20", "ma60", "ma120"]);
  const [hoverDate, setHoverDate] = useState<string | null>(null);
  const analysis = useMemo(
    () => analyzeTechnicalData(analysisPrices, analysisIndicators, marketOpen),
    [analysisIndicators, analysisPrices, marketOpen],
  );
  const fullIndexByDate = useMemo(() => new Map(analysisPrices.map((price, index) => [price.date, index])), [analysisPrices]);
  const visibleIndexByDate = useMemo(() => new Map(prices.map((price, index) => [price.date, index])), [prices]);
  const latestIndicator = indicators.at(-1);
  const latestFullIndex = analysisPrices.length - 1;
  const latestVolume = analysis.volume[latestFullIndex];
  const visibleVolume = useMemo(
    () => prices.map((price) => analysis.volume[fullIndexByDate.get(price.date) ?? -1]).filter(Boolean),
    [analysis.volume, fullIndexByDate, prices],
  );

  useEffect(() => {
    if (!priceRef.current || !volumeRef.current || !macdRef.current || !prices.length) return;
    let disposed = false;
    let cleanup = () => {};

    void import("lightweight-charts").then((charts) => {
      if (disposed || !priceRef.current || !volumeRef.current || !macdRef.current) return;
      const baseOptions = {
        layout: { background: { color: "#111827" }, textColor: "#8490aa", fontFamily: "Inter, sans-serif", fontSize: 11 },
        grid: { vertLines: { color: "#1b2638" }, horzLines: { color: "#1b2638" } },
        crosshair: {
          mode: charts.CrosshairMode.Normal,
          vertLine: { color: "#71809a", width: 1 as const, style: charts.LineStyle.Dashed, labelBackgroundColor: "#35435a" },
          horzLine: { color: "#48566e", width: 1 as const, style: charts.LineStyle.Dotted, labelBackgroundColor: "#35435a" },
        },
        rightPriceScale: { borderColor: "#26334a", minimumWidth: 68 },
        timeScale: {
          borderColor: "#26334a", timeVisible: false, rightOffset: 3, barSpacing: 7, minBarSpacing: 1,
          fixLeftEdge: false, lockVisibleTimeRangeOnResize: true,
        },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      };
      const priceChart = charts.createChart(priceRef.current, {
        ...baseOptions, width: priceRef.current.clientWidth, height: 390,
        timeScale: { ...baseOptions.timeScale, visible: false },
      });
      const volumeChart = charts.createChart(volumeRef.current, {
        ...baseOptions, width: volumeRef.current.clientWidth, height: 165,
        rightPriceScale: { ...baseOptions.rightPriceScale, scaleMargins: { top: 0.15, bottom: 0.08 } },
        timeScale: { ...baseOptions.timeScale, visible: false },
      });
      const macdChart = charts.createChart(macdRef.current, {
        ...baseOptions, width: macdRef.current.clientWidth, height: 225,
        rightPriceScale: { ...baseOptions.rightPriceScale, scaleMargins: { top: 0.15, bottom: 0.12 } },
        timeScale: { ...baseOptions.timeScale, visible: true },
      });

      const candleSeries = priceChart.addCandlestickSeries({
        upColor: "#ef5350", downColor: "#20b26b", wickUpColor: "#ef5350", wickDownColor: "#20b26b",
        borderUpColor: "#ef5350", borderDownColor: "#20b26b", priceLineColor: "#8b7cff",
      });
      candleSeries.setData(prices.map((item) => ({
        time: item.date as never, open: item.open, high: item.high, low: item.low, close: item.close,
      })));
      candleSeries.createPriceLine({
        price: prices.at(-1)!.close, color: "#8b7cff", lineWidth: 1, lineStyle: charts.LineStyle.Dashed,
        axisLabelVisible: true, title: "最新",
      });
      if (analysis.summary.support != null) candleSeries.createPriceLine({
        price: analysis.summary.support, color: "#35bf79", lineWidth: 1, lineStyle: charts.LineStyle.Dashed,
        axisLabelVisible: true, title: "支撐",
      });
      if (analysis.summary.resistance != null) candleSeries.createPriceLine({
        price: analysis.summary.resistance, color: "#ec7d7f", lineWidth: 1, lineStyle: charts.LineStyle.Dashed,
        axisLabelVisible: true, title: "壓力",
      });
      MA_CONFIG.filter((ma) => activeMAs.includes(ma.key)).forEach((ma) => {
        const series = priceChart.addLineSeries({ color: ma.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        series.setData(indicators.flatMap((item) => {
          const value = item[ma.key];
          return value == null ? [] : [{ time: item.date as never, value }];
        }));
      });
      const visibleDates = new Set(prices.map((price) => price.date));
      candleSeries.setMarkers(analysis.markers.filter((marker) => visibleDates.has(marker.date)).map((marker) => ({
        time: marker.date as never,
        position: marker.position,
        color: marker.color,
        shape: marker.shape,
        text: marker.text,
        size: 0.72,
      })));

      const volumeSeries = volumeChart.addHistogramSeries({
        priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false,
      });
      volumeSeries.setData(prices.map((item) => ({
        time: item.date as never, value: item.volume,
        color: item.close >= item.open ? "rgba(239,83,80,.58)" : "rgba(32,178,107,.58)",
      })));
      const volumeMa5Series = volumeChart.addLineSeries({
        color: "#ffd166", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      const volumeMa20Series = volumeChart.addLineSeries({
        color: "#62a8ff", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      volumeMa5Series.setData(visibleVolume.flatMap((item) => item.ma5 == null ? [] : [{ time: item.date as never, value: item.ma5 }]));
      volumeMa20Series.setData(visibleVolume.flatMap((item) => item.ma20 == null ? [] : [{ time: item.date as never, value: item.ma20 }]));

      const histogramSeries = macdChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      histogramSeries.setData(indicators.flatMap((item) => item.histogram == null ? [] : [{
        time: item.date as never, value: item.histogram,
        color: item.histogram >= 0 ? "rgba(239,83,80,.78)" : "rgba(32,178,107,.78)",
      }]));
      const difSeries = macdChart.addLineSeries({
        color: "#ffd166", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      const deaSeries = macdChart.addLineSeries({
        color: "#7599ff", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      difSeries.setData(indicators.flatMap((item) => item.dif == null ? [] : [{ time: item.date as never, value: item.dif }]));
      deaSeries.setData(indicators.flatMap((item) => item.signal == null ? [] : [{ time: item.date as never, value: item.signal }]));
      histogramSeries.createPriceLine({
        price: 0, color: "#69758b", lineWidth: 1, lineStyle: charts.LineStyle.Solid,
        axisLabelVisible: true, title: "零軸",
      });
      const macdMarkerTexts = new Set(["止跌觀察", "初步進場", "加碼觀察", "減碼觀察", "出場觀察", "底背離", "頂背離"]);
      histogramSeries.setMarkers(analysis.markers.filter((marker) => visibleDates.has(marker.date) && macdMarkerTexts.has(marker.text)).map((marker) => ({
        time: marker.date as never,
        position: marker.position,
        color: marker.color,
        shape: "circle" as const,
        text: marker.text,
        size: 0.65,
      })));

      const chartApis = [priceChart, volumeChart, macdChart];
      const anchorSeries = [candleSeries, volumeSeries, histogramSeries];
      let syncingRange = false;
      const rangeHandlers = chartApis.map((sourceChart, sourceIndex) => {
        const handler = (range: { from: number; to: number } | null) => {
          if (!range || syncingRange) return;
          syncingRange = true;
          chartApis.forEach((targetChart, targetIndex) => {
            if (targetIndex !== sourceIndex) targetChart.timeScale().setVisibleLogicalRange(range);
          });
          syncingRange = false;
        };
        sourceChart.timeScale().subscribeVisibleLogicalRangeChange(handler);
        return handler;
      });

      let syncingCrosshair = false;
      const crosshairHandlers = chartApis.map((sourceChart, sourceIndex) => {
        const handler: Parameters<typeof sourceChart.subscribeCrosshairMove>[0] = (param) => {
          if (syncingCrosshair) return;
          const date = timeKey(param.time);
          setHoverDate(date);
          syncingCrosshair = true;
          if (!date) {
            chartApis.forEach((targetChart, targetIndex) => {
              if (targetIndex !== sourceIndex) targetChart.clearCrosshairPosition();
            });
          } else {
            const index = visibleIndexByDate.get(date);
            if (index != null) {
              const values = [
                prices[index]?.close ?? 0,
                prices[index]?.volume ?? 0,
                indicators[index]?.histogram ?? 0,
              ];
              chartApis.forEach((targetChart, targetIndex) => {
                if (targetIndex !== sourceIndex) targetChart.setCrosshairPosition(values[targetIndex], date as never, anchorSeries[targetIndex] as never);
              });
            }
          }
          syncingCrosshair = false;
        };
        sourceChart.subscribeCrosshairMove(handler);
        return handler;
      });

      macdChart.timeScale().fitContent();
      const observer = new ResizeObserver(() => {
        if (!priceRef.current || !volumeRef.current || !macdRef.current) return;
        priceChart.applyOptions({ width: priceRef.current.clientWidth });
        volumeChart.applyOptions({ width: volumeRef.current.clientWidth });
        macdChart.applyOptions({ width: macdRef.current.clientWidth });
      });
      observer.observe(priceRef.current);
      observer.observe(volumeRef.current);
      observer.observe(macdRef.current);
      cleanup = () => {
        observer.disconnect();
        chartApis.forEach((chart, index) => {
          chart.unsubscribeCrosshairMove(crosshairHandlers[index]);
          chart.timeScale().unsubscribeVisibleLogicalRangeChange(rangeHandlers[index]);
          chart.remove();
        });
      };
    });

    return () => { disposed = true; cleanup(); };
  }, [activeMAs, analysis.markers, analysis.summary.resistance, analysis.summary.support, indicators, prices, visibleIndexByDate, visibleVolume]);

  const hoverIndex = hoverDate ? fullIndexByDate.get(hoverDate) : undefined;
  const hoverPrice = hoverIndex == null ? analysisPrices.at(-1) : analysisPrices[hoverIndex];
  const hoverIndicator = hoverIndex == null ? analysisIndicators.at(-1) : analysisIndicators[hoverIndex];
  const hoverVolume = hoverIndex == null ? analysis.volume.at(-1) : analysis.volume[hoverIndex];
  const hoverMacd = hoverIndex == null ? analysis.macd.at(-1) : analysis.macd[hoverIndex];
  const previousPrice = hoverIndex == null ? analysisPrices.at(-2) : analysisPrices[hoverIndex - 1];
  const changePercent = hoverPrice && previousPrice ? (hoverPrice.close - previousPrice.close) / previousPrice.close * 100 : 0;
  const amplitude = hoverPrice && previousPrice ? (hoverPrice.high - hoverPrice.low) / previousPrice.close * 100 : 0;
  const summary = analysis.summary;

  return (
    <div className="chart-area">
      <div className="ma-toolbar">
        <span>移動平均線</span>
        {MA_CONFIG.map((ma) => (
          <button
            key={ma.key}
            className={activeMAs.includes(ma.key) ? "active" : ""}
            onClick={() => setActiveMAs((current) => current.includes(ma.key) ? current.filter((item) => item !== ma.key) : [...current, ma.key])}
          >
            <i style={{ background: ma.color }} />{ma.label}
            <small>{safeNumber(latestIndicator?.[ma.key])}</small>
          </button>
        ))}
        <span className={`close-confirmation ${marketOpen ? "pending" : "confirmed"}`}>
          {marketOpen ? "盤中訊號尚未確認" : "收盤資料確認"}
        </span>
      </div>

      <div className="technical-visual-grid">
        <div className="synchronized-chart-stack">
          <div className="shared-chart-values">
            {hoverPrice && hoverIndicator && hoverVolume && hoverMacd ? (
              <>
                <strong>{hoverPrice.date}</strong>
                <span>開 {safeNumber(hoverPrice.open)}　高 {safeNumber(hoverPrice.high)}　低 {safeNumber(hoverPrice.low)}　收 {safeNumber(hoverPrice.close)}</span>
                <span>漲跌 {formatPercent(changePercent)}　振幅 {formatPercent(amplitude)}　量 {formatVolume(hoverPrice.volume)}</span>
                <span>MA5 {safeNumber(hoverIndicator.ma5)}　MA10 {safeNumber(hoverIndicator.ma10)}　MA20 {safeNumber(hoverIndicator.ma20)}　MA60 {safeNumber(hoverIndicator.ma60)}　MA120 {safeNumber(hoverIndicator.ma120)}</span>
              </>
            ) : <span>移動滑鼠查看詳細數值</span>}
          </div>

          <section className="chart-pane price-pane">
            <div className="pane-title"><BarChart3 size={13} /><strong>日 K 線與均線</strong><span className={`trend-label ${signalClass(summary.signal)}`}>{summary.trend}</span></div>
            <div ref={priceRef} className="technical-price-chart" />
          </section>
          <section className="chart-pane volume-pane">
            <div className="pane-title">
              <Activity size={13} /><strong>成交量</strong>
              <span><i className="volume-ma5" />5 日均量</span><span><i className="volume-ma20" />20 日均量</span>
              {hoverVolume && <em>5 日比 {hoverVolume.ratio5?.toFixed(2) ?? "—"}x　20 日比 {hoverVolume.ratio20?.toFixed(2) ?? "—"}x・{hoverVolume.status}</em>}
            </div>
            <div ref={volumeRef} className="technical-volume-chart" />
          </section>
          <section className="chart-pane macd-pane">
            <div className="pane-title">
              <TrendingUp size={13} /><strong>MACD (12, 26, 9)</strong>
              <span><i className="dif" />DIF</span><span><i className="dea" />DEA</span>
              {hoverIndicator && hoverMacd && <em>DIF {safeNumber(hoverIndicator.dif, 3)}　DEA {safeNumber(hoverIndicator.signal, 3)}　柱 {safeNumber(hoverIndicator.histogram, 3)}　變化 {safeNumber(hoverMacd.histogramChange, 3)}・{hoverMacd.state}・{hoverMacd.cross}</em>}
            </div>
            <div ref={macdRef} className="technical-macd-chart" />
          </section>
        </div>

        <aside className="technical-summary-card">
          <div className="technical-summary-heading">
            <div><Gauge size={17} /><span>技術分析摘要</span></div>
            <strong className={signalClass(summary.signal)}>{summary.healthScore}<small>／100</small></strong>
          </div>
          <div className="health-meter"><i style={{ width: `${summary.healthScore}%` }} /></div>
          <dl>
            <div><dt>目前趨勢</dt><dd><span className={`trend-label ${signalClass(summary.signal)}`}>{summary.trend}</span></dd></div>
            <div><dt>K 線狀態</dt><dd>{summary.klineStatus}</dd></div>
            <div><dt>成交量狀態</dt><dd>{summary.volumeStatus}</dd></div>
            <div><dt>MACD 狀態</dt><dd>{summary.macdStatus}</dd></div>
            <div><dt>支撐價</dt><dd>{safeNumber(summary.support)}</dd></div>
            <div><dt>壓力價</dt><dd>{safeNumber(summary.resistance)}</dd></div>
          </dl>
          <div className={`operation-box ${signalClass(summary.signal)}`}>
            <strong><TrendingUp size={14} />操作建議</strong>
            <p>{summary.operation}</p>
            <ul>{summary.operationReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          </div>
          <div className="volume-explanation">{latestVolume ? summary.volumeExplanation : "成交量資料不足"}</div>
          <div className="technical-risk"><ShieldAlert size={14} /><span>{summary.risk}</span></div>
          {(summary.topDivergence || summary.bottomDivergence) && (
            <div className="divergence-alert"><AlertTriangle size={14} />{summary.topDivergence ? "偵測到 MACD 頂背離" : "偵測到 MACD 底背離"}</div>
          )}
        </aside>
      </div>
      <p className="chart-help">三區共用時間軸，可滾輪縮放、拖曳與觸控操作；訊號綜合 K 線、均線、成交量與 MACD，僅供技術分析參考，不保證未來報酬。</p>
    </div>
  );
}
