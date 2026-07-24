"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { DailyPrice, TechnicalIndicator } from "@/lib/types";
import { safeNumber } from "@/lib/format";

const MA_CONFIG = [
  { key: "ma5", label: "MA5", color: "#ffd166" },
  { key: "ma10", label: "MA10", color: "#fa8cff" },
  { key: "ma20", label: "MA20", color: "#62a8ff" },
  { key: "ma30", label: "MA30", color: "#8f7cff" },
  { key: "ma60", label: "MA60", color: "#38d9c5" },
  { key: "ma120", label: "MA120", color: "#ff9f43" },
  { key: "ma240", label: "MA240", color: "#b7c0d8" },
] as const;

export function StockChart({ prices, indicators, marketOpen = false }: { prices: DailyPrice[]; indicators: TechnicalIndicator[]; marketOpen?: boolean }) {
  const priceRef = useRef<HTMLDivElement>(null);
  const [activeMAs, setActiveMAs] = useState<string[]>(["ma5", "ma20", "ma60"]);
  const [hoverDate, setHoverDate] = useState<string | null>(null);
  const latestIndicator = indicators.at(-1);
  const indexByDate = useMemo(() => new Map(prices.map((price, index) => [price.date, index])), [prices]);

  useEffect(() => {
    if (!priceRef.current || !prices.length) return;
    let disposed = false;
    let cleanup = () => {};

    void import("lightweight-charts").then((charts) => {
      if (disposed || !priceRef.current) return;
      const baseOptions = {
        layout: { background: { color: "#111827" }, textColor: "#8490aa", fontFamily: "Inter, sans-serif", fontSize: 11 },
        grid: { vertLines: { color: "#1b2638" }, horzLines: { color: "#1b2638" } },
        crosshair: { mode: charts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: "#26334a", scaleMargins: { top: 0.05, bottom: 0.4 } },
        timeScale: { borderColor: "#26334a", timeVisible: false, rightOffset: 3, barSpacing: 7, minBarSpacing: 2 },
        handleScroll: true,
        handleScale: true,
      };
      const priceChart = charts.createChart(priceRef.current, {
        ...baseOptions,
        width: priceRef.current.clientWidth,
        height: 640,
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
      const volume = priceChart.addHistogramSeries({
        priceFormat: { type: "volume" }, priceScaleId: "volume", priceLineVisible: false, lastValueVisible: false,
      });
      priceChart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.55, bottom: 0.4 } });
      volume.setData(prices.map((item) => ({
        time: item.date as never, value: item.volume,
        color: item.close >= item.open ? "rgba(239,83,80,.42)" : "rgba(32,178,107,.42)",
      })));
      MA_CONFIG.filter((ma) => activeMAs.includes(ma.key)).forEach((ma) => {
        const series = priceChart.addLineSeries({ color: ma.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        series.setData(indicators.flatMap((item) => {
          const value = item[ma.key];
          return value == null ? [] : [{ time: item.date as never, value }];
        }));
      });

      const candleSignalMarkers = indicators.flatMap((item) => {
        if (!item.macdSignal) return [];
        const entry = item.macdSignal === "entry";
        return [{
          time: item.date as never,
          position: entry ? "belowBar" as const : "aboveBar" as const,
          color: entry ? "#ff6467" : "#2bce7f",
          shape: entry ? "arrowUp" as const : "arrowDown" as const,
          text: entry ? "進" : "出",
          size: .72,
        }];
      });
      candleSeries.setMarkers(candleSignalMarkers);

      const histogram = priceChart.addHistogramSeries({
        priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false,
      });
      priceChart.priceScale("macd").applyOptions({ scaleMargins: { top: 0.72, bottom: 0.04 } });
      histogram.setData(indicators.flatMap((item) => item.histogram == null ? [] : [{
        time: item.date as never, value: item.histogram,
        color: item.histogram >= 0 ? "rgba(239,83,80,.72)" : "rgba(32,178,107,.72)",
      }]));
      const difLine = priceChart.addLineSeries({
        priceScaleId: "macd", color: "#ffd166", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      const signalLine = priceChart.addLineSeries({
        priceScaleId: "macd", color: "#7599ff", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      difLine.setData(indicators.flatMap((item) => item.dif == null ? [] : [{ time: item.date as never, value: item.dif }]));
      signalLine.setData(indicators.flatMap((item) => item.signal == null ? [] : [{ time: item.date as never, value: item.signal }]));
      histogram.createPriceLine({ price: 0, color: "#556078", lineWidth: 1, lineStyle: charts.LineStyle.Solid, axisLabelVisible: false, title: "" });
      histogram.setMarkers(indicators.flatMap((item) => {
        if (!item.macdSignal || item.histogram == null) return [];
        const entry = item.macdSignal === "entry";
        return [{
          time: item.date as never,
          position: entry ? "belowBar" as const : "aboveBar" as const,
          color: entry ? "#ff6467" : "#2bce7f",
          shape: "circle" as const,
          text: "",
          size: .62,
        }];
      }));

      priceChart.timeScale().fitContent();
      const crosshair = (param: { time?: unknown }) => {
        setHoverDate(typeof param.time === "string" ? param.time : null);
      };
      priceChart.subscribeCrosshairMove(crosshair);

      const observer = new ResizeObserver(() => {
        if (!priceRef.current) return;
        priceChart.applyOptions({ width: priceRef.current.clientWidth });
      });
      observer.observe(priceRef.current);
      cleanup = () => { observer.disconnect(); priceChart.remove(); };
    });

    return () => { disposed = true; cleanup(); };
  }, [prices, indicators, activeMAs, marketOpen]);

  const hoverIndex = hoverDate ? indexByDate.get(hoverDate) : undefined;
  const hoverPrice = hoverIndex == null ? null : prices[hoverIndex];
  const hoverIndicator = hoverIndex == null ? null : indicators[hoverIndex];

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
      </div>
      <div className="integrated-chart-stack">
        <div className="chart-frame">
          <div ref={priceRef} className="price-chart" />
          <div className="macd-inline-header">
            <div><strong>MACD</strong><span>(12, 26, 9)</span></div>
            <div className="legend">
              <span><i className="dif" />DIF {safeNumber(latestIndicator?.dif, 3)}</span>
              <span><i className="signal" />Signal {safeNumber(latestIndicator?.signal, 3)}</span>
              <span>柱 {safeNumber(latestIndicator?.histogram, 3)}</span>
              <span className="entry-point"><i />進場</span>
              <span className="exit-point"><i />出場</span>
            </div>
          </div>
          {hoverPrice && hoverIndicator && (
            <div className="chart-tooltip">
              <strong>{hoverPrice.date}{hoverIndicator.macdSignal ? ` · ${hoverIndicator.macdSignal === "entry" ? "進場" : "出場"}` : ""}</strong>
              <span>開 {safeNumber(hoverPrice.open)}　高 {safeNumber(hoverPrice.high)}　低 {safeNumber(hoverPrice.low)}　收 {safeNumber(hoverPrice.close)}</span>
              <span>DIF {safeNumber(hoverIndicator.dif, 3)}　Signal {safeNumber(hoverIndicator.signal, 3)}　柱 {safeNumber(hoverIndicator.histogram, 3)}</span>
            </div>
          )}
        </div>
      </div>
      <p className="chart-help">K 線、成交量與 MACD 共用時間軸 · 滾輪縮放 · 拖曳平移 · 十字游標查看完整數值</p>
    </div>
  );
}
