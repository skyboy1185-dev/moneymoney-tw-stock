import type { StockPayload } from "@/lib/types";

const backendBaseUrl = process.env.FASTAPI_URL?.replace(/\/$/, "");

export class BackendUnavailableError extends Error {}

export async function backendJson<T>(path: string, init?: RequestInit): Promise<T> {
  if (!backendBaseUrl) throw new BackendUnavailableError("FASTAPI_URL 尚未設定");
  try {
    const response = await fetch(`${backendBaseUrl}/api/v1${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
      signal: AbortSignal.timeout(5_000),
      cache: "no-store",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(payload?.detail ?? `FastAPI 回應 ${response.status}`);
    }
    if (response.status === 204) return {} as T;
    return await response.json() as T;
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("FastAPI 回應")) throw error;
    throw new BackendUnavailableError(error instanceof Error ? error.message : "FastAPI 無法連線");
  }
}

export async function getBackendStock(query: string): Promise<StockPayload | null> {
  if (!backendBaseUrl) return null;
  const search = await backendJson<{ items: { symbol: string }[] }>(`/stocks/search?q=${encodeURIComponent(query)}`);
  const symbol = search.items[0]?.symbol;
  return symbol ? backendJson<StockPayload>(`/stocks/${encodeURIComponent(symbol)}`) : null;
}

export function isBackendConfigured() {
  return Boolean(backendBaseUrl);
}
