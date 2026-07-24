import type { MarketContext, MarketDirection, RobotResult, StrategyRobot } from "@/lib/market-types";
import type { StockPayload } from "@/lib/types";

export abstract class BaseRobot implements StrategyRobot {
  abstract id: string;
  abstract name: string;
  abstract supportedRegimes: MarketDirection[];
  protected abstract evaluate(stock: StockPayload, market: MarketContext): RobotResult;

  analyze(stock: StockPayload, market: MarketContext) { return this.evaluate(stock, market); }
  filter(stock: StockPayload, market: MarketContext) { return this.evaluate(stock, market).matched; }
  score(stock: StockPayload, market: MarketContext) { return this.evaluate(stock, market).score; }
  recommend(stock: StockPayload, market: MarketContext) { return this.evaluate(stock, market).reasons; }
}
