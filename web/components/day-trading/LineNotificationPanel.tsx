"use client";

import { useCallback, useEffect, useState } from "react";
import {
  BellRing, CheckCircle2, Copy, Link2, LoaderCircle, MessageCircle,
  RefreshCw, Save, Send, ShieldCheck, Trash2, TriangleAlert,
} from "lucide-react";
import type {
  LineIntegrationStatus,
  LineNotificationSettings,
} from "@/lib/day-trading-types";
import { lineNotificationClient } from "@/services/line-notification-client";

const time = (value: string | null) => value
  ? new Date(value).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" })
  : "尚無紀錄";

const statusLabel = {
  connected: "已連線",
  awaiting_group: "等待群組綁定",
  missing_credentials: "尚未設定憑證",
  disabled: "通知已停用",
} as const;

export function LineNotificationPanel() {
  const [status, setStatus] = useState<LineIntegrationStatus | null>(null);
  const [draft, setDraft] = useState<LineNotificationSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const next = await lineNotificationClient.status();
    setStatus(next);
    setDraft(next.settings);
  }, []);

  useEffect(() => {
    void refresh()
      .catch((reason) => setError(reason instanceof Error ? reason.message : "無法載入 LINE 通知設定"))
      .finally(() => setLoading(false));
  }, [refresh]);

  const run = async (name: string, action: () => Promise<unknown>, success: string) => {
    setWorking(name);
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(success);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "LINE 通知操作失敗");
    } finally {
      setWorking("");
    }
  };

  if (loading || !status || !draft) return <section className="dt-card line-panel line-loading">
    <LoaderCircle className="spin" /><span>正在載入 LINE 群組通知設定…</span>
  </section>;

  const toggle = (key: keyof LineNotificationSettings) => {
    setDraft((current) => current ? { ...current, [key]: !current[key] } : current);
  };

  const notificationOptions: Array<[keyof LineNotificationSettings, string]> = [
    ["openingEnabled", "開盤啟動通知"],
    ["longEntryEnabled", "做多進場通知"],
    ["shortEntryEnabled", "放空進場通知"],
    ["longExitEnabled", "多單出場通知"],
    ["shortCoverEnabled", "空單回補通知"],
    ["stopLossEnabled", "停損通知"],
    ["dataAlertEnabled", "資料異常通知"],
    ["closingSummaryEnabled", "收盤摘要"],
  ];

  return <section className="dt-card line-panel">
    <div className="dt-section-heading">
      <div><span className="eyebrow">LINE MESSAGING API</span><h2>LINE 通知設定</h2><p>正式推薦最多通知三檔；候選清單不會推送，出場與停損優先</p></div>
      <span className={`line-status ${status.connectionStatus}`}>
        {status.connectionStatus === "connected" ? <CheckCircle2 /> : <TriangleAlert />}
        {statusLabel[status.connectionStatus]}
      </span>
    </div>

    <div className="line-overview">
      <div><MessageCircle /><span>官方帳號<strong>{status.officialAccountName}</strong></span></div>
      <div><Link2 /><span>已綁定群組<strong>{status.groups.length} 個</strong></span></div>
      <div><BellRing /><span>今日推送<strong>{status.todayPushCount} 則</strong></span></div>
      <div><RefreshCw /><span>最後推送<strong>{time(status.lastPushAt)}</strong></span></div>
    </div>

    <div className="line-webhook-row">
      <ShieldCheck />
      <div><span>LINE Webhook URL</span><code>{status.publicWebhookUrl}</code></div>
      <button onClick={() => void navigator.clipboard.writeText(status.publicWebhookUrl).then(() => setMessage("Webhook URL 已複製"))}><Copy />複製</button>
    </div>

    {!status.credentialsConfigured && <div className="line-setup-warning">
      <TriangleAlert /><div><strong>尚未完成後端憑證設定</strong><span>請在 Railway 後端服務設定 LINE_CHANNEL_ACCESS_TOKEN 與 LINE_CHANNEL_SECRET。憑證不會傳到瀏覽器。</span></div>
    </div>}

    <div className="line-groups">
      <h3>已綁定群組</h3>
      {status.groups.length ? status.groups.map((group) => <div className="line-group-item" key={group.id}>
        <div><MessageCircle /><span><strong>{group.displayName}</strong><code>{group.maskedGroupId}</code></span></div>
        <span>綁定：{time(group.boundAt)}</span>
        <span>最後推送：{time(group.lastPushAt)}</span>
        <button disabled={group.id === 0 || working === `unbind-${group.id}`} onClick={() => {
          if (!window.confirm(`確定解除 ${group.displayName} 的 LINE 通知？`)) return;
          void run(`unbind-${group.id}`, () => lineNotificationClient.unbind(group.id), "LINE 群組已解除綁定");
        }}><Trash2 />解除綁定</button>
      </div>) : <div className="line-empty">
        <MessageCircle /><strong>尚未綁定群組</strong>
        <span>邀請「AI當沖機器人」進入 LINE 群組後，在群組輸入「綁定當沖機器人」。</span>
      </div>}
    </div>

    <div className="line-notification-options">
      <h3>通知開關</h3>
      <div>{notificationOptions.map(([key, label]) => <label key={key}>
        <input type="checkbox" checked={Boolean(draft[key])} onChange={() => toggle(key)} />
        <span>{label}</span>
      </label>)}</div>
    </div>

    <div className="line-commands">
      <span><code>綁定當沖機器人</code> 綁定目前群組</span>
      <span><code>測試當沖通知</code> 測試 Reply API</span>
      <span><code>解除當沖通知</code> 解除目前群組</span>
    </div>

    {message && <div className="line-feedback success"><CheckCircle2 />{message}</div>}
    {error && <div className="line-feedback error"><TriangleAlert />{error}</div>}

    <div className="line-actions">
      <button disabled={working === "save"} onClick={() => void run("save", () => lineNotificationClient.saveSettings(draft), "LINE 通知開關已儲存")}><Save />儲存通知設定</button>
      <button className="test" disabled={!status.credentialsConfigured || !status.groups.length || working === "test"} onClick={() => void run("test", lineNotificationClient.test, "LINE 測試通知已送出")}><Send />測試通知</button>
    </div>
    <p className="line-disclaimer">第一版只提供 LINE 訊號通知，不串接券商下單。所有訊息僅供研究參考，不構成投資建議。</p>
  </section>;
}
