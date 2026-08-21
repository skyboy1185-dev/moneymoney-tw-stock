import type { StockPayload } from "@/lib/types";
import { getBackendBaseUrl } from "@/lib/runtime-config";

export class BackendUnavailableError extends Error {}
class BackendResponseError extends Error {}

export async function backendJson<T>(path: string, init?: RequestInit, timeoutMs = 5_000): Promise<T> {
  const backendBaseUrl = getBackendBaseUrl();
  try {
    const response = await fetch(`${backendBaseUrl}/api/v1${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
      signal: AbortSignal.timeout(timeoutMs),
      cache: "no-store",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null) as { detail?: string } | null;
      throw new BackendResponseError(payload?.detail ?? `FastAPI 回應 ${response.status}`);
    }
    if (response.status === 204) return {} as T;
    return await response.json() as T;
  } catch (error) {
    if (error instanceof BackendResponseError) throw error;
    throw new BackendUnavailableError(error instanceof Error ? error.message : "FastAPI 無法連線");
  }
}

export async function getBackendStock(query: string): Promise<StockPayload | null> {
  const search = await backendJson<{ items: { symbol: string }[] }>(`/stocks/search?q=${encodeURIComponent(query)}`);
  const symbol = search.items[0]?.symbol;
  return symbol ? backendJson<StockPayload>(`/stocks/${encodeURIComponent(symbol)}`) : null;
}

export function isBackendConfigured() {
  try {
    return Boolean(getBackendBaseUrl());
  } catch {
    return false;
  }
}
