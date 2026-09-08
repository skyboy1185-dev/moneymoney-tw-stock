"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, ChevronDown, RefreshCw, ShieldAlert } from "lucide-react";
import { DATA_LABELS, EXECUTION_LABELS, ROBOT_HEALTH_LABELS, robotHealthException, robotModeLabel, type RobotHealthId, type RobotHealthResponse } from "@/lib/robot-health";

function time(value: string | null | undefined) {
  if (!value) return "未取得";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" });
}

export function RobotHealthPanel({ userId, onOpen }: { userId: string; onOpen: (target: RobotHealthId) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [snapshot, setSnapshot] = useState<{ owner: string; data: RobotHealthResponse | null; error: string }>({ owner: "", data: null, error: "" });
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const cancelRequests = useCallback(() => { ++requestId.current; controller.current?.abort(); }, []);
  const load = useCallback(async () => {
    if (!userId) return;
    const current = ++requestId.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setLoading(true);
    try {
      const response = await fetch("/api/robot-health", { headers: { "x-user-id": userId }, cache: "no-store", signal: AbortSignal.any([abort.signal, AbortSignal.timeout(22_000)]) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "機器人狀態暫時無法讀取");
      if (current === requestId.current) setSnapshot({ owner: userId, data: body as RobotHealthResponse, error: "" });
    } catch (error) {
      if (current === requestId.current) setSnapshot((previous) => ({ owner: userId, data: previous.owner === userId ? previous.data : null, error: error instanceof Error ? error.message : "機器人狀態暫時無法讀取" }));
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => { window.clearInterval(timer); cancelRequests(); };
  }, [load, cancelRequests]);

  const data = snapshot.owner === userId ? snapshot.data : null;
  const error = snapshot.owner === userId ? snapshot.error : "";
  const exceptions = data?.items.flatMap((item) => {
    const reason = robotHealthException(item);
    return reason ? [{ id: item.id, reason }] : [];
  }) ?? [];
  const unknown = data?.items.filter((item) => item.execution.status === "unknown" || item.data.status === "unknown").length ?? 0;

  return <section className="robot-health-strip compact-health" aria-label="機器人狀態總覽">
    <div className="robot-compact-heading">
      <button type="button" className="robot-panel-toggle" aria-expanded={expanded} aria-controls="robot-health-details" onClick={() => setExpanded((value) => !value)}>
        <Bot size={16} /><strong>機器人狀態</strong>
        <span>{!data ? loading ? "讀取中" : "未取得" : error ? "更新失敗・保留上次狀態" : `${data.items.length} 項・${exceptions.length ? `${exceptions.length} 項異常` : "無已知異常"}${unknown ? `・${unknown} 項待確認` : ""}`}</span>
        <ChevronDown size={16} className={expanded ? "expanded" : ""} />
      </button>
      <button className="robot-panel-refresh" type="button" onClick={() => void load()} disabled={loading || !userId} aria-label="重新整理機器人狀態"><RefreshCw size={15} className={loading ? "spin-icon" : ""} /></button>
    </div>
    {(error || exceptions.length > 0) && <div className="robot-compact-exceptions" role="status">
      {error && <p><ShieldAlert size={14} />{error}{data ? `；上次取得 ${time(data.generatedAt)}` : ""}</p>}
      {exceptions.map((item) => <button type="button" key={item.id} onClick={() => onOpen(item.id)}><ShieldAlert size={14} /><strong>{ROBOT_HEALTH_LABELS[item.id]}</strong>：{item.reason}</button>)}
    </div>}
    <div id="robot-health-details" hidden={!expanded}>
      {data ? <>
        <p className="robot-health-updated">檢查時間 {time(data.generatedAt)}・{data.market.localDate} {data.market.isTradingDay ? "交易日" : "非交易日"}{error ? "・以下為上次取得的狀態" : ""}</p>
        <div className="robot-health-detail-grid">{data.items.map((item) => <article key={item.id}>
          <header><button type="button" onClick={() => onOpen(item.id)}>{ROBOT_HEALTH_LABELS[item.id]}</button><span>{item.scope === "user" ? "我的帳戶" : "共用服務"}</span></header>
          <dl>
            <div><dt>執行</dt><dd>{EXECUTION_LABELS[item.execution.status]}{item.execution.reason && <small>{item.execution.reason}</small>}{item.execution.error && <small className="health-error">{item.execution.error}</small>}</dd></div>
            <div><dt>資料</dt><dd>{DATA_LABELS[item.data.status]}{item.data.tradeDate && <small>資料日期 {item.data.tradeDate}</small>}{item.data.reason && <small>{item.data.reason}</small>}</dd></div>
            <div><dt>模式</dt><dd>{robotModeLabel(item.mode.tradeMode)}{item.mode.performanceMode && <small>績效：{robotModeLabel(item.mode.performanceMode)}</small>}</dd></div>
          </dl>
          <footer>最近執行 {time(item.execution.lastRunAt)}<br />最近成功 {time(item.execution.lastSuccessAt)}<br />資料更新 {time(item.data.updatedAt)}</footer>
        </article>)}</div>
      </> : <p className="robot-health-updated">{loading ? "正在讀取機器人狀態…" : "尚未取得機器人狀態，請重新整理。"}</p>}
    </div>
  </section>;
}
