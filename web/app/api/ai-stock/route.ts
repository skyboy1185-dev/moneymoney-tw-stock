import { NextRequest, NextResponse } from "next/server";
import { backendJson, BackendUnavailableError } from "@/services/backend-client";
import { buildMarketSnapshot } from "@/services/market-snapshot-service";
import { getUserId } from "@/lib/portfolio-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function quoteTimestamp(date?: string, time?: string) {
  const safeTime = time && /^\d{2}:\d{2}:\d{2}$/.test(time) ? time : "13:30:00";
  return `${date ?? "1970-01-01"}T${safeTime}+08:00`;
}

export async function GET(request: NextRequest) {
  const userId = getUserId(request);
  if (!userId) return NextResponse.json({ error: "缺少有效的使用者識別。" }, { status: 401 });
  try {
    const snapshot = await buildMarketSnapshot(true);
    if (snapshot.featured.length) {
      await backendJson("/ai-stock-monitor/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-user-id": userId },
        body: JSON.stringify({
          items: snapshot.featured.map((row) => ({
            signal_id: row.signalId,
            symbol: row.symbol,
            stock_name: row.name,
            market: row.market,
            industry: row.industry,
            strategy_name: row.strategyName,
            secondary_strategies: row.secondaryStrategies,
            total_score: row.score,
            strategy_fit: row.strategyFit,
            market_fit: row.marketFit,
            health_score: row.healthScore,
            current_price: row.price,
            entry_min: row.entryMin,
            entry_max: row.entryMax,
            stop_loss: row.stopLoss,
            target_1: row.target1,
            target_2: row.target2,
            risk_reward_ratio: row.riskRewardRatio,
            reasons: row.reasons.slice(0, 5),
            warnings: [...row.riskTags, ...row.hardRiskFailures].slice(0, 10),
            quote_source: row.priceSource ?? "unknown",
            quote_timestamp: quoteTimestamp(row.priceDate, row.priceTime),
            expired_at: new Date(Date.now() + 10 * 60_000).toISOString(),
          })),
        }),
      });
    }
    const dashboard = await backendJson<Record<string, unknown>>("/ai-stock-dashboard", {
      headers: { "x-user-id": userId },
    });
    return NextResponse.json({ ...dashboard, featured: snapshot.featured, candidates: snapshot.rankings });
  } catch (error) {
    const message = error instanceof BackendUnavailableError
      ? "AI監控後端目前無法連線，候選掃描仍可顯示，但無法保存持倉。"
      : error instanceof Error ? error.message : "AI監控載入失敗";
    return NextResponse.json({ error: message }, { status: 503 });
  }
}
