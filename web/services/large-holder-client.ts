"use client";

import type {
  LargeHolderHistoryResponse,
  LargeHolderMarketFilter,
  LargeHolderRankingResponse,
  LargeHolderRankingType,
} from "@/lib/large-holder-types";

export interface LargeHolderFilters {
  market: LargeHolderMarketFilter;
  industry: string;
  keyword: string;
  minAverageTurnover: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, cache: "no-store" });
  const payload = await response.json().catch(() => null) as { error?: string } | null;
  if (!response.ok) throw new Error(payload?.error ?? "大戶持股資料讀取失敗");
  return payload as T;
}

export function getLargeHolderRankings(
  type: LargeHolderRankingType,
  filters: LargeHolderFilters,
  refresh = false,
) {
  const search = new URLSearchParams({
    type,
    limit: "20",
    market: filters.market,
    industry: filters.industry,
    keyword: filters.keyword,
    minAverageTurnover: String(filters.minAverageTurnover),
    excludeEtf: "true",
    sortBy: "changePoint",
    sortOrder: "desc",
    refresh: refresh ? "true" : "false",
  });
  return request<LargeHolderRankingResponse>(`/api/large-holders/rankings?${search}`);
}

export function getLargeHolderHistory(stockCode: string) {
  return request<LargeHolderHistoryResponse>(
    `/api/large-holders/stocks/${encodeURIComponent(stockCode)}/history?weeks=12`,
  );
}

export function addLargeHolderAction(
  userId: string,
  item: { stockCode: string; stockName: string; latestPrice?: number | null },
  monitorType: LargeHolderRankingType,
  action: "watchlist" | "ai" | "line",
) {
  if (action === "watchlist") {
    return request<{ status: string; message?: string }>("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-user-id": userId },
      body: JSON.stringify({
        symbol: item.stockCode,
        name: item.stockName,
        price: item.latestPrice,
        score: 0,
        source: "large-holder",
        reasons: ["大戶持股比例本週增加"],
      }),
    }).then((result) => ({ ...result, message: result.message ?? "已加入自選觀察" }));
  }
  return request<{ status: string; message: string }>("/api/large-holders/monitors", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-user-id": userId },
    body: JSON.stringify({
      stock_code: item.stockCode,
      stock_name: item.stockName,
      monitor_type: monitorType,
      action,
      current_price: item.latestPrice ?? undefined,
    }),
  });
}
