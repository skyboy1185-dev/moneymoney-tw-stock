"use client";

import { useEffect, useState } from "react";
import { Radio, Zap } from "lucide-react";
import type {
  ElectronicChipFlowAlert,
  ElectronicChipFlowAlertsResponse,
} from "@/lib/electronic-chip-flow-alerts";

interface ElectronicChipFlowTickerProps {
  onSelectStock?: (symbol: string) => void;
}

function formatLots(value: number): string {
  return new Intl.NumberFormat("zh-TW", {
    maximumFractionDigits: 2,
  }).format(value);
}

function statusMessage(data: ElectronicChipFlowAlertsResponse | null): string {
  if (!data) return "電子股大單監測載入中…";
  if (data.status === "disconnected") return "盤中大單監測服務暫時無法連線";
  if (data.status === "unavailable") return "大單監測等待逐筆成交行情";
  if (data.status === "warming" || data.status === "scanning") {
    return `電子股大單輪巡中 ${data.scannedCount}/${data.candidateCount} 檔`;
  }
  return data.marketOpen
    ? `目前尚未偵測到符合條件的電子股（近 ${data.windowMinutes} 分鐘）`
    : "今日收盤前未偵測到符合條件的電子股";
}

function AlertItems({
  alerts,
  windowMinutes,
  onSelectStock,
}: {
  alerts: ElectronicChipFlowAlert[];
  windowMinutes: number;
  onSelectStock: (symbol: string) => void;
}) {
  return (
    <>
      {alerts.map((alert) => (
        <button
          key={alert.symbol}
          type="button"
          onClick={() => onSelectStock(alert.symbol)}
          title={`${alert.name} ${alert.symbol}｜大單資料為推估值`}
        >
          <strong>{alert.name}</strong>
          <b>{alert.symbol}</b>
          <span>近 {windowMinutes} 分 +{formatLots(alert.recentNetBuyLots)} 張</span>
          <small>累積 +{formatLots(alert.largeNetLots)} 張・{alert.time}</small>
        </button>
      ))}
    </>
  );
}

export function ElectronicChipFlowTicker({
  onSelectStock,
}: ElectronicChipFlowTickerProps) {
  const [data, setData] = useState<ElectronicChipFlowAlertsResponse | null>(null);

  useEffect(() => {
    let controller: AbortController | null = null;
    const load = () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      void fetch("/api/chip-flow/electronic-alerts", {
        cache: "no-store",
        signal: controller.signal,
      })
        .then(async (response) => {
          const payload = await response.json() as ElectronicChipFlowAlertsResponse;
          setData(payload);
        })
        .catch((error) => {
          if ((error as Error).name !== "AbortError") {
            setData((current) => current ? { ...current, status: "disconnected" } : null);
          }
        });
    };
    load();
    const interval = window.setInterval(load, 5_000);
    document.addEventListener("visibilitychange", load);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", load);
      controller?.abort();
    };
  }, []);

  const alerts = Array.from(
    new Map((data?.alerts ?? []).map((alert) => [alert.symbol, alert])).values(),
  );
  const hasAlerts = alerts.length > 0;
  const staticAlerts = alerts.length < 4;
  const label = data?.marketOpen ? "盤中大單狂進" : "今日大單狂進";
  const selectStock = (symbol: string) => {
    if (onSelectStock) {
      onSelectStock(symbol);
      return;
    }
    window.location.assign(`/?symbol=${encodeURIComponent(symbol)}&view=analysis`);
  };

  return (
    <section
      className={`chip-alert-ticker ${hasAlerts ? "has-alerts" : ""}`}
      aria-label="電子股大單狂進提醒"
      title={data?.notice}
    >
      <div className="chip-alert-label">
        {hasAlerts ? <Zap size={14} /> : <Radio size={13} />}
        <strong>{label}</strong>
        <em>推估</em>
      </div>
      <div className="chip-alert-viewport" aria-live="polite">
        {hasAlerts ? (
          <div className={`chip-alert-track ${staticAlerts ? "is-static" : ""}`}>
            <div className="chip-alert-group">
              <AlertItems
                alerts={alerts}
                windowMinutes={data?.windowMinutes ?? 5}
                onSelectStock={selectStock}
              />
            </div>
            {!staticAlerts && (
              <div className="chip-alert-group" aria-hidden="true">
                <AlertItems
                  alerts={alerts}
                  windowMinutes={data?.windowMinutes ?? 5}
                  onSelectStock={selectStock}
                />
              </div>
            )}
          </div>
        ) : (
          <span className="chip-alert-message">{statusMessage(data)}</span>
        )}
      </div>
      {data && (
        <small className="chip-alert-coverage">
          {data.scannedCount}/{data.candidateCount}
        </small>
      )}
    </section>
  );
}
