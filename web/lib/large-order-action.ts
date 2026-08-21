import type {
  ElectronicChipFlowAlert,
  ElectronicChipFlowQuote,
} from "@/lib/electronic-chip-flow-alerts";
import type { GroupResonance } from "@/lib/group-resonance";

export type LargeOrderAction = "strong_buy" | "buy" | "watch" | "short" | "strong_short";

export interface LargeOrderGuidance {
  action: LargeOrderAction;
  label: "強烈建議買進" | "建議買進" | "建議觀望" | "建議放空" | "強烈建議放空";
  score: number | null;
  reasons: string[];
  cautions: string[];
}

const MIN_QUOTE_MOVE_PERCENT = 0.2;
const MAX_CHASE_MOVE_PERCENT = 6;
const QUOTE_FRESH_SECONDS = 120;

function watch(reason: string, score: number | null = null, cautions: string[] = []): LargeOrderGuidance {
  return {
    action: "watch",
    label: "建議觀望",
    score,
    reasons: [reason],
    cautions,
  };
}

function quoteIsFresh(quote: ElectronicChipFlowQuote, now: Date): boolean {
  const timestamp = new Date(quote.quoteTimestamp).getTime();
  const ageSeconds = (now.getTime() - timestamp) / 1_000;
  return quote.isRealtime && Number.isFinite(timestamp) && ageSeconds >= -5 && ageSeconds <= QUOTE_FRESH_SECONDS;
}

export function evaluateLargeOrderGuidance({
  alert,
  quote,
  marketOpen,
  resonance,
  now = new Date(),
}: {
  alert: ElectronicChipFlowAlert;
  quote?: ElectronicChipFlowQuote;
  marketOpen: boolean;
  resonance?: GroupResonance;
  now?: Date;
}): LargeOrderGuidance {
  if (!marketOpen) return watch("目前非盤中，等待開盤後重新確認");
  if (alert.dataState === "warming" || alert.dataState === "stale") {
    return watch(alert.dataStateLabel ?? "大單掃描資料尚未更新");
  }
  if (!quote) return watch("等待即時股價與大單訊號交叉確認");
  if (!quoteIsFresh(quote, now)) return watch("行情不是即時或已超過 120 秒", null, ["不可用延遲行情判斷進場"]);

  const shortSide = alert.direction === "short";
  const force = shortSide
    ? alert.recentNetSellLots ?? Math.max(0, -alert.recentNetBuyLots)
    : Math.max(0, alert.recentNetBuyLots);
  const ratio = shortSide
    ? alert.sellBuyRatio ?? (alert.recentBuyLots > 0 ? alert.recentSellLots / alert.recentBuyLots : 99)
    : alert.buySellRatio;
  const steps = shortSide ? alert.negativeSteps ?? 0 : alert.positiveSteps;
  const priceAligned = shortSide
    ? quote.changePercent <= -MIN_QUOTE_MOVE_PERCENT
    : quote.changePercent >= MIN_QUOTE_MOVE_PERCENT;
  const smallOrdersAligned = shortSide
    ? alert.recentSmallNetBuyLots < 0
    : alert.recentSmallNetBuyLots > 0;
  const groupAligned = resonance?.direction === (shortSide ? "down" : "up");
  const overlyExtended = Math.abs(quote.changePercent) >= MAX_CHASE_MOVE_PERCENT;

  let score = 0;
  const reasons: string[] = [];
  const cautions: string[] = [];

  if (alert.currentQualifies) {
    score += 20;
    reasons.push("目前大單條件成立");
  } else cautions.push("目前大單條件已不成立");
  if (!alert.isWarning) score += 8;
  else cautions.push(shortSide ? "賣壓正在衰退" : "買盤動能正在衰退");
  if (force >= 10) {
    score += force >= 20 ? 15 : 10;
    reasons.push(`近段大單${shortSide ? "賣超" : "買超"} ${force.toFixed(1)} 張`);
  } else cautions.push("大單淨量不足 10 張");
  if (ratio >= 1.5) {
    score += ratio >= 2 ? 14 : 10;
    reasons.push(`${shortSide ? "賣買比" : "買賣比"} ${ratio.toFixed(2)}`);
  } else cautions.push(`${shortSide ? "賣買比" : "買賣比"}未達 1.5`);
  if (steps >= 2) {
    score += steps >= 3 ? 12 : 8;
    reasons.push(`連續 ${steps} 段同向`);
  } else cautions.push("同向連續性不足");
  if (alert.occurrenceCount >= 2) {
    score += alert.occurrenceCount >= 3 ? 10 : 7;
    reasons.push(`已重複確認 ${alert.occurrenceCount} 次`);
  } else cautions.push("訊號只出現一次");
  if (priceAligned) {
    score += 12;
    reasons.push(`股價同步${shortSide ? "下跌" : "上漲"} ${Math.abs(quote.changePercent).toFixed(2)}%`);
  } else cautions.push("股價尚未與大單方向同步");
  if (smallOrdersAligned) score += 6;
  else cautions.push("小單方向未同步");
  if (alert.reinforced || alert.trend === "strengthening") score += 7;
  if (groupAligned) {
    score += 8;
    reasons.push(`${resonance.group}族群同步${shortSide ? "下跌" : "上漲"}`);
  }
  if (overlyExtended) {
    score -= 18;
    cautions.push(`股價已${shortSide ? "下跌" : "上漲"}超過 ${MAX_CHASE_MOVE_PERCENT}%，避免追價`);
  }
  score = Math.max(0, Math.min(100, score));

  const mandatoryConfirmed = alert.currentQualifies
    && !alert.isWarning
    && force >= 10
    && ratio >= 1.5
    && steps >= 2
    && alert.occurrenceCount >= 2
    && priceAligned
    && !overlyExtended;
  if (!mandatoryConfirmed || score < 78) {
    return {
      action: "watch",
      label: "建議觀望",
      score,
      reasons: reasons.slice(0, 4),
      cautions: cautions.length ? cautions.slice(0, 3) : ["確認條件尚未達保守門檻 78 分"],
    };
  }
  const continuousLargeOrderAccumulation = alert.reinforced
    && alert.trend === "strengthening"
    && alert.trendStreak >= 2
    && alert.occurrenceCount >= 3
    && steps >= 3
    && force >= 20
    && ratio >= 2;
  const highCrossConfirmation = score >= 92
    && smallOrdersAligned
    && alert.simultaneousIncrease
    && groupAligned
    && Math.abs(quote.changePercent) >= 0.5;
  const strongSignal = score >= 90
    && (continuousLargeOrderAccumulation || highCrossConfirmation);
  if (strongSignal) {
    const strongReason = continuousLargeOrderAccumulation
      ? `大單連續累積 ${alert.trendStreak} 次，訊號已多次增強`
      : "大單、股價、小單與族群方向高度一致";
    return {
      action: shortSide ? "strong_short" : "strong_buy",
      label: shortSide ? "強烈建議放空" : "強烈建議買進",
      score,
      reasons: [strongReason, ...reasons].slice(0, 4),
      cautions: [
        ...(shortSide ? ["下單前仍須確認券源與可放空資格"] : []),
        ...cautions,
      ].slice(0, 3),
    };
  }
  return {
    action: shortSide ? "short" : "buy",
    label: shortSide ? "建議放空" : "建議買進",
    score,
    reasons: reasons.slice(0, 4),
    cautions: [
      ...(shortSide ? ["下單前仍須確認券源與可放空資格"] : []),
      ...cautions,
    ].slice(0, 3),
  };
}
