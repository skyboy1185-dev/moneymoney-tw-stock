"use client";

import type {
  WhaleAccumulationFilters,
  WhaleAccumulationResponse,
  WhaleTrendResponse,
} from "@/lib/whale-accumulation-types";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json().catch(() => null) as { error?: string; detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? payload?.error ?? "大戶偷掃貨資料讀取失敗");
  return payload as T;
}

export function getWhaleAccumulation(filters: WhaleAccumulationFilters) {
  const search = new URLSearchParams({
    startDate: filters.startDate,
    endDate: filters.endDate,
    rankingType: filters.rankingType,
    limit: String(filters.limit),
    keyword: filters.keyword,
    industry: filters.industry,
    minBig400: String(filters.minBig400),
    minBig1000: String(filters.minBig1000),
    minLots: String(filters.minLots),
    minValue: String(filters.minValue),
    maxPriceChange: String(filters.maxPriceChange),
    minScore: String(filters.minScore),
  });
  return request<WhaleAccumulationResponse>(`/api/large-holders/accumulation?${search}`);
}

export function getWhaleTrend(stockCode: string, startDate: string, endDate: string) {
  const search = new URLSearchParams({ startDate, endDate });
  return request<WhaleTrendResponse>(
    `/api/large-holders/accumulation/stocks/${encodeURIComponent(stockCode)}/trend?${search}`,
  );
}
