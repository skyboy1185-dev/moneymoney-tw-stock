import { ArrowDown, ArrowRight, ArrowUp, Minus } from "lucide-react";
import type { PowerScoreResult } from "@/lib/power-score";

export function PowerTrendIndicator({
  score,
  showLabel = false,
}: {
  score: PowerScoreResult;
  showLabel?: boolean;
}) {
  const label = score.powerChange == null
    ? "昨日馬力資料不足"
    : `昨日 ${score.previousPowerValue} 馬力，今日 ${score.powerValue} 馬力，${score.powerChange > 0 ? "增加" : score.powerChange < 0 ? "減少" : "持平"} ${Math.abs(score.powerChange)}`;
  const Icon = score.powerTrend === "up"
    ? ArrowUp
    : score.powerTrend === "down"
      ? ArrowDown
      : score.powerTrend === "flat" ? ArrowRight : Minus;
  const change = score.powerChange == null ? "—" : score.powerChange > 0 ? `+${score.powerChange}` : `${score.powerChange}`;

  return (
    <span className={`power-trend ${score.powerTrend}`} title={label} aria-label={label}>
      <Icon size={12} aria-hidden="true" />
      {showLabel && <em>較昨日</em>}
      <b>{change}</b>
    </span>
  );
}
