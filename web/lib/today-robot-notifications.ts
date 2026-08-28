export type TodayRobotSource = "day-trading" | "limit-up-ai" | "adaptive-electronic" | "pattern-robot" | "rocket-radar";
export type TodayRobotAction = "buy" | "short" | "add" | "reduce" | "take_profit" | "sell" | "cover" | "stop_loss" | "exit" | "scan" | "warning" | "system";
export type TodayRobotLevel = "info" | "success" | "warning" | "danger";

export interface TodayRobotNotification {
  id: string;
  source: TodayRobotSource;
  sourceLabel: string;
  action: TodayRobotAction;
  actionLabel: string;
  symbol: string | null;
  stockName: string | null;
  title: string;
  message: string;
  reason: string;
  timestamp: string;
  isRead: boolean | null;
  level: TodayRobotLevel;
  rawType: string;
}

export interface TodayRobotNotificationPayloads {
  dayTradingSignals?: unknown;
  dayTradingAlerts?: unknown;
  limitUp?: unknown;
  superAi?: unknown;
  rocket?: unknown;
  pattern?: unknown;
}

export interface TodayRobotSourceSummary {
  source: TodayRobotSource;
  sourceLabel: string;
  count: number;
  unreadCount: number;
  lastTimestamp: string | null;
}

export const TODAY_ROBOT_SOURCE_LABELS: Record<TodayRobotSource, string> = {
  "day-trading": "當沖機器人",
  "limit-up-ai": "漲停機器人",
  "adaptive-electronic": "超強 AI 當沖",
  "pattern-robot": "型態選股機器人",
  "rocket-radar": "飆股雷達",
};

const SOURCE_ORDER: TodayRobotSource[] = ["day-trading", "limit-up-ai", "adaptive-electronic", "pattern-robot", "rocket-radar"];

type LooseRecord = Record<string, unknown>;

function record(value: unknown): LooseRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as LooseRecord : {};
}

function list(value: unknown): LooseRecord[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function textList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => text(item)).filter(Boolean) : [];
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function numberText(value: unknown, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "";
}

function taipeiDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleDateString("sv-SE", { timeZone: "Asia/Taipei" });
}

export function todayTaipeiDate(now: Date = new Date()): string {
  return now.toLocaleDateString("sv-SE", { timeZone: "Asia/Taipei" });
}

function isSameTaipeiDate(value: string, today: string): boolean {
  return Boolean(value) && taipeiDate(value) === today;
}

function actionFromText(value: string): TodayRobotAction {
  const upper = value.toUpperCase();
  if (upper.includes("STOP") || value.includes("停損")) return "stop_loss";
  if (upper.includes("TAKE_PROFIT") || value.includes("停利")) return "take_profit";
  if (upper.includes("COVER") || value.includes("回補")) return "cover";
  if (upper.includes("SHORT") || value.includes("放空") || value.includes("空單")) return "short";
  if (upper.includes("ADD") || value.includes("加碼")) return "add";
  if (upper.includes("REDUCE") || value.includes("減碼")) return "reduce";
  if (upper.includes("SELL") || value.includes("賣出") || value.includes("出場")) return "sell";
  if (upper.includes("BUY") || value.includes("買進") || value.includes("做多")) return "buy";
  if (upper.includes("SCAN") || value.includes("掃描")) return "scan";
  if (upper.includes("WARNING") || value.includes("警告") || value.includes("異常")) return "warning";
  if (upper.includes("EXIT")) return "exit";
  return "system";
}

export function actionLabel(action: TodayRobotAction): string {
  return {
    buy: "買進",
    short: "放空",
    add: "加碼",
    reduce: "減碼",
    take_profit: "停利",
    sell: "賣出",
    cover: "回補",
    stop_loss: "停損",
    exit: "出場",
    scan: "掃描完成",
    warning: "警示",
    system: "系統提醒",
  }[action];
}

function levelForAction(action: TodayRobotAction): TodayRobotLevel {
  if (action === "stop_loss" || action === "warning") return "danger";
  if (action === "reduce" || action === "sell" || action === "cover" || action === "exit") return "warning";
  if (action === "buy" || action === "short" || action === "add" || action === "take_profit") return "success";
  return "info";
}

function notification(
  source: TodayRobotSource,
  rawId: unknown,
  rawType: string,
  timestamp: string,
  fields: Omit<TodayRobotNotification, "id" | "source" | "sourceLabel" | "timestamp" | "level" | "rawType" | "actionLabel"> & { actionLabel?: string },
): TodayRobotNotification {
  return {
    id: `${source}:${String(rawId || `${rawType}:${timestamp}:${fields.symbol ?? ""}`)}`,
    source,
    sourceLabel: TODAY_ROBOT_SOURCE_LABELS[source],
    actionLabel: fields.actionLabel ?? actionLabel(fields.action),
    timestamp,
    level: levelForAction(fields.action),
    rawType,
    ...fields,
  };
}

function normalizeDayTradingSignals(payload: unknown, today: string): TodayRobotNotification[] {
  const body = record(payload);
  const tradingDate = text(body.tradingDate);
  return list(body.items)
    .filter((item) => item.isOfficialRecommendation === true)
    .filter((item) => tradingDate === today || isSameTaipeiDate(text(item.recommendedAt) || text(item.generatedAt), today))
    .map((item) => {
      const direction = text(item.direction);
      const action: TodayRobotAction = direction === "short" ? "short" : "buy";
      const timestamp = text(item.recommendedAt) || text(item.generatedAt) || new Date().toISOString();
      const entryMin = numberText(item.entryMin);
      const entryMax = numberText(item.entryMax);
      const stopLoss = numberText(item.stopLoss);
      return notification("day-trading", item.id, `official-${direction || "long"}`, timestamp, {
        action,
        symbol: text(item.symbol) || null,
        stockName: text(item.stockName) || null,
        title: `當沖機器人｜正式${actionLabel(action)}`,
        message: `${text(item.action, actionLabel(action))}${entryMin && entryMax ? `・進場 ${entryMin}～${entryMax}` : ""}${stopLoss ? `・停損 ${stopLoss}` : ""}`,
        reason: textList(item.reasons).slice(0, 3).join("、")
          || `信心 ${numberText(item.confidenceScore, 0)}・RR ${numberText(item.riskRewardRatio, 2)}`,
        isRead: null,
      });
    });
}

function normalizeDayTradingAlerts(payload: unknown, today: string): TodayRobotNotification[] {
  return list(record(payload).items)
    .filter((item) => isSameTaipeiDate(text(item.createdAt), today))
    .map((item) => {
      const rawType = text(item.type) || text(item.action) || text(item.title, "ALERT");
      const action = actionFromText(`${rawType} ${text(item.action)} ${text(item.title)} ${text(item.reason)}`);
      return notification("day-trading", item.id, rawType, text(item.createdAt), {
        action,
        symbol: null,
        stockName: null,
        title: text(item.title, `當沖機器人｜${actionLabel(action)}`),
        message: text(item.message) || text(item.action),
        reason: text(item.reason),
        isRead: text(item.readAt) ? true : false,
      });
    });
}

function normalizeLimitUp(payload: unknown, today: string): TodayRobotNotification[] {
  return list(record(payload).items)
    .filter((item) => isSameTaipeiDate(text(item.createdAt), today))
    .map((item) => {
      const rawType = text(item.type, "LIMIT_UP_NOTIFICATION");
      const action = actionFromText(`${rawType} ${text(item.title)} ${text(item.message)} ${text(item.reason)}`);
      return notification("limit-up-ai", item.id, rawType, text(item.createdAt), {
        action,
        symbol: text(item.symbol) || null,
        stockName: text(item.stockName) || null,
        title: text(item.title, `漲停機器人｜${actionLabel(action)}`),
        message: text(item.message),
        reason: text(item.reason),
        isRead: boolOrNull(item.isRead),
      });
    });
}

function normalizeSuperAi(payload: unknown, today: string): TodayRobotNotification[] {
  return list(record(payload).items)
    .filter((item) => isSameTaipeiDate(text(item.timestamp) || text(item.createdAt), today))
    .map((item) => {
      const rawType = text(item.category, "SUPER_AI_NOTIFICATION");
      const action = actionFromText(`${rawType} ${text(item.title)} ${text(item.message)}`);
      const reasons = textList(item.reasons).slice(0, 3).join("、");
      return notification("adaptive-electronic", item.id, rawType, text(item.timestamp) || text(item.createdAt), {
        action,
        symbol: text(item.symbol) || text(item.stockCode) || null,
        stockName: text(item.symbolName) || text(item.stockName) || null,
        title: text(item.title, `超強 AI 當沖｜${actionLabel(action)}`),
        message: text(item.message),
        reason: reasons || text(item.strategy) || (numberText(item.aiScore, 0) ? `AI ${numberText(item.aiScore, 0)}` : ""),
        isRead: boolOrNull(item.isRead) ?? boolOrNull(item.read),
      });
    });
}

function normalizeRocket(payload: unknown, today: string): TodayRobotNotification[] {
  return list(record(payload).items)
    .filter((item) => isSameTaipeiDate(text(item.timestamp), today))
    .map((item) => {
      const rawType = text(item.notificationType, "ROCKET_NOTIFICATION");
      const action = actionFromText(`${rawType} ${text(item.title)} ${text(item.message)} ${text(item.reason)}`);
      return notification("rocket-radar", item.notificationId, rawType, text(item.timestamp), {
        action,
        symbol: text(item.stockCode) || null,
        stockName: text(item.stockName) || null,
        title: text(item.title, `飆股雷達｜${actionLabel(action)}`),
        message: text(item.message),
        reason: text(item.reason),
        isRead: boolOrNull(item.isRead),
      });
    });
}

function normalizePattern(payload: unknown, today: string): TodayRobotNotification[] {
  return list(record(payload).items)
    .filter((item) => isSameTaipeiDate(text(item.createdAt), today))
    .map((item) => {
      const rawType = text(item.messageType, "PATTERN_MESSAGE");
      const action = actionFromText(`${rawType} ${text(item.action)} ${text(item.title)} ${text(item.message)}`);
      return notification("pattern-robot", item.id, rawType, text(item.createdAt), {
        action,
        symbol: text(item.stockCode) || null,
        stockName: text(item.stockName) || null,
        title: text(item.title, `型態選股機器人｜${actionLabel(action)}`),
        message: text(item.message),
        reason: text(item.action) || rawType,
        isRead: boolOrNull(item.isRead),
      });
    });
}

export function normalizeTodayRobotNotifications(
  payloads: TodayRobotNotificationPayloads,
  today: string = todayTaipeiDate(),
): TodayRobotNotification[] {
  const items = [
    ...normalizeDayTradingSignals(payloads.dayTradingSignals, today),
    ...normalizeDayTradingAlerts(payloads.dayTradingAlerts, today),
    ...normalizeLimitUp(payloads.limitUp, today),
    ...normalizeSuperAi(payloads.superAi, today),
    ...normalizePattern(payloads.pattern, today),
    ...normalizeRocket(payloads.rocket, today),
  ];
  return items.sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
}

export function summarizeTodayRobotNotifications(items: TodayRobotNotification[]): TodayRobotSourceSummary[] {
  return SOURCE_ORDER.map((source) => {
    const sourceItems = items.filter((item) => item.source === source);
    return {
      source,
      sourceLabel: TODAY_ROBOT_SOURCE_LABELS[source],
      count: sourceItems.length,
      unreadCount: sourceItems.filter((item) => item.isRead === false).length,
      lastTimestamp: sourceItems[0]?.timestamp ?? null,
    };
  });
}
