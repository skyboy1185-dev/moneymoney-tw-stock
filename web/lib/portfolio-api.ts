export function getUserId(request: Request): string | null {
  const value = request.headers.get("x-user-id");
  return value && /^[a-zA-Z0-9-]{8,80}$/.test(value) ? value : null;
}

export function validHoldingInput(value: unknown): value is { symbol: string; cost: number; lots: number; buyDate: string; fromWatchlist?: boolean } {
  if (!value || typeof value !== "object") return false;
  const input = value as Record<string, unknown>;
  return typeof input.symbol === "string" && /^\d{4,6}$/.test(input.symbol)
    && typeof input.cost === "number" && Number.isFinite(input.cost) && input.cost > 0
    && typeof input.lots === "number" && Number.isFinite(input.lots) && input.lots > 0
    && typeof input.buyDate === "string" && /^\d{4}-\d{2}-\d{2}$/.test(input.buyDate);
}
