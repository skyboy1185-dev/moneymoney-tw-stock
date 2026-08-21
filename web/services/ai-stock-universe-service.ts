import type { StockMeta, StockTheme } from "@/lib/types";
import { backendJson } from "@/services/backend-client";
import { thematicStockCatalog } from "@/services/stock-service";
import { TARGET_STOCK_THEMES } from "@/services/theme-stock-universe";

type UniverseRow = {
  symbol?: unknown;
  name?: unknown;
  market?: unknown;
  industry?: unknown;
  themes?: unknown;
};

type UniversePayload = { items?: UniverseRow[]; total?: number };

const validThemes = new Set<string>(TARGET_STOCK_THEMES);
let cached: StockMeta[] | null = null;

export function normalizeAIStockUniverse(payload: UniversePayload): StockMeta[] {
  if (!Array.isArray(payload.items)) return [];
  const rows = payload.items.flatMap((row): StockMeta[] => {
    const symbol = String(row.symbol ?? "").trim();
    const name = String(row.name ?? "").trim();
    const industry = String(row.industry ?? "").trim();
    const market = row.market === "上市" || row.market === "上櫃" ? row.market : null;
    const themes = Array.isArray(row.themes)
      ? row.themes.map(String).filter((theme): theme is StockTheme => validThemes.has(theme))
      : [];
    if (!/^\d{4}$/.test(symbol) || !name || !industry || !market || !themes.length) return [];
    return [{
      symbol, name, industry, market,
      peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null,
      themes: [...new Set(themes)],
    }];
  });
  return [...new Map(rows.map((row) => [row.symbol, row])).values()];
}

export async function getAIStockUniverse(): Promise<StockMeta[]> {
  if (cached) return cached.map((stock) => ({ ...stock, themes: [...(stock.themes ?? [])] }));
  try {
    const rows = normalizeAIStockUniverse(await backendJson<UniversePayload>("/ai-stock-universe", undefined, 10_000));
    if (rows.length) cached = rows;
  } catch {
    // Keep the original curated universe available if the backend is restarting.
  }
  const selected = cached ?? thematicStockCatalog;
  return selected.map((stock) => ({ ...stock, themes: [...(stock.themes ?? [])] }));
}

export function resetAIStockUniverseCacheForTests() {
  cached = null;
}
