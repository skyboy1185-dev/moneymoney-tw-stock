import type { MarketDirection, StrategyRecommendation } from "@/lib/market-types";
import { allRobots } from "@/robots";

const MAP: Record<MarketDirection, string[]> = {
  strong_bull: ["strong-breakout", "trend-start", "bull-pullback"],
  bull: ["trend-start", "bull-pullback", "sideways-breakout"],
  sideways: ["sideways-breakout", "range-rebound"],
  bear: ["exit-warning", "bear-rebound"],
  strong_bear: ["exit-warning", "bear-rebound"],
  transition: ["sideways-breakout", "exit-warning"],
};

export class StrategySelector {
  select(direction: MarketDirection): StrategyRecommendation[] {
    const ids = MAP[direction];
    return allRobots.map((robot): StrategyRecommendation => {
      const position = ids.indexOf(robot.id);
      const fit = position === -1 ? 42 : Math.max(64, 91 - position * 9 - (direction === "transition" ? 12 : 0));
      const highRisk = robot.id === "bear-rebound";
      return {
        id: robot.id,
        name: robot.name,
        fit,
        stars: Math.max(1, Math.round(fit / 20)),
        reason: position === -1 ? "目前盤勢適配度較低" : `符合目前${direction === "sideways" ? "盤整" : direction.includes("bull") ? "偏多" : direction.includes("bear") ? "偏空" : "轉折"}環境`,
        risk: highRisk ? "高" : robot.id === "exit-warning" ? "中" : "中低",
        enabled: position !== -1,
      };
    }).sort((a, b) => b.fit - a.fit);
  }
}

export const strategySelector = new StrategySelector();
