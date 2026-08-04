"use client";

import { useMemo, useState } from "react";
import { BarChartHorizontal, Database, MapPin, TrendingUp } from "lucide-react";
import { buildVolumePriceTrend, buildVolumeProfile } from "@/lib/volume-profile";
import type { DailyPrice } from "@/lib/types";

function price(value: number) {
  return value.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function lots(value: number) {
  return `${(value / 1_000).toLocaleString("zh-TW", { maximumFractionDigits: 0 })} 張`;
}

export function VolumeProfilePanel({
  prices,
  source,
}: {
  prices: DailyPrice[];
  source: string;
}) {
  const [days, setDays] = useState(60);
  const profile = useMemo(
    () => buildVolumeProfile(prices, days, days >= 750 ? 48 : days >= 250 ? 36 : 24),
    [days, prices],
  );

  if (!profile) return null;
  const maxVolume = Math.max(...profile.bins.map((bin) => bin.volume));
  const trend = buildVolumePriceTrend(
    prices,
    days,
    profile.bins[0].low,
    profile.bins.at(-1)!.high,
  );
  const firstTrend = trend[0];
  const latestTrend = trend.at(-1);
  const trendDirection = latestTrend && firstTrend && latestTrend.close >= firstTrend.close
    ? "up"
    : "down";
  const trendPoints = trend.map((point) => `${point.xPct},${point.yPct}`).join(" ");
  const areaPoints = trend.length
    ? `0,100 ${trendPoints} 100,100`
    : "";
  const currentY = latestTrend?.yPct ?? 50;
  const currentEdge = currentY < 4 ? "edge-top" : currentY > 96 ? "edge-bottom" : "";

  return (
    <section className="volume-profile-panel">
      <header>
        <div>
          <span className="section-kicker">VOLUME BY PRICE</span>
          <h2><BarChartHorizontal />成交量密集價位</h2>
          <p>以日 K 高低價區間分配成交量，估算市場主要持有成本區</p>
        </div>
        <div className="volume-profile-tabs" aria-label="大量區計算期間">
          {[
            { value: 20, label: "20 日" },
            { value: 60, label: "60 日" },
            { value: 120, label: "120 日" },
            { value: 250, label: "1 年" },
            { value: 750, label: "3 年" },
          ].map(({ value, label }) => (
            <button key={value} className={days === value ? "active" : ""} onClick={() => setDays(value)}>
              {label}
            </button>
          ))}
        </div>
      </header>

      <div className="volume-profile-layout">
        <div className="volume-profile-chart">
          <div className="volume-profile-chart-head">
            <span>價位區間</span>
            <span>估算成交量分布＋收盤價走勢</span>
            <strong>現價 {price(profile.currentPrice)}</strong>
          </div>
          <div className="volume-profile-plot">
            {[...profile.bins].reverse().map((bin) => (
              <div
                className={`volume-profile-row ${bin.isPoc ? "poc" : ""} ${bin.hasCurrentPrice ? "current" : ""}`}
                key={`${bin.low}-${bin.high}`}
                title={`${price(bin.low)}～${price(bin.high)}・${lots(bin.volume)}・占 ${bin.volumePct}%`}
              >
                <span>{price(bin.low)}～{price(bin.high)}</span>
                <div>
                  <i style={{ width: `${Math.max(2, bin.volume / maxVolume * 100)}%` }} />
                  {bin.isPoc && <b>最大量區</b>}
                </div>
                <small>{bin.volumePct}%</small>
              </div>
            ))}
            {!!trend.length && (
              <>
                <svg
                  className={`volume-price-overlay ${trendDirection}`}
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                  role="img"
                  aria-label={`${profile.startDate} 至 ${profile.endDate} 收盤價走勢`}
                >
                  <polygon points={areaPoints} />
                  <polyline points={trendPoints} />
                  {latestTrend && <circle cx={latestTrend.xPct} cy={latestTrend.yPct} r="1.15" />}
                </svg>
                <div className={`volume-current-price-line ${currentEdge}`} style={{ top: `${currentY}%` }}>
                  <span><MapPin />現價 <b>{price(profile.currentPrice)}</b></span>
                </div>
              </>
            )}
          </div>
          <div className="volume-price-time-axis">
            <span>{profile.startDate}</span>
            <b>每日收盤價走勢</b>
            <span>{profile.endDate}</span>
          </div>
        </div>

        <div className="volume-profile-insights">
          <article className={`volume-current-price-card ${trendDirection}`}>
            <span><TrendingUp />CURRENT PRICE・當前股價</span>
            <strong>{price(profile.currentPrice)}</strong>
            <small>{profile.endDate} 最新日K收盤／盤中報價</small>
          </article>
          <article className="volume-profile-poc">
            <span>POC・最大成交量價位</span>
            <strong>{price(profile.poc.low)}～{price(profile.poc.high)}</strong>
            <small>{lots(profile.poc.volume)}・占估算成交量 {profile.poc.volumePct}%</small>
          </article>
          <div className="volume-zone-list">
            {profile.zones.map((zone, index) => (
              <article key={`${zone.low}-${zone.high}`}>
                <span>大量區 #{index + 1}{zone.includesPoc ? "・POC" : ""}</span>
                <strong>{price(zone.low)}～{price(zone.high)}</strong>
                <small>{lots(zone.volume)}・占 {zone.volumePct}%</small>
              </article>
            ))}
          </div>
          <div className={`volume-position-note ${profile.position}`}>
            <MapPin />
            <div><strong>現價 {price(profile.currentPrice)}</strong><span>{profile.positionLabel}</span></div>
          </div>
        </div>
      </div>

      <footer>
        <span><Database />{source}・{profile.startDate}～{profile.endDate}・{profile.days} 個交易日</span>
        <span>此為日 K 區間分配估算，非逐筆成交量價分布；大量區僅供觀察籌碼成本，不代表必然支撐或壓力。</span>
      </footer>
    </section>
  );
}
