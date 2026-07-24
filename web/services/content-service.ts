import { stockCatalog, stockService } from "@/services/stock-service";

export interface IndustryHotspot {
  industry: string;
  changePercent: number;
  momentum: number;
  stockCount: number;
  leaders: { symbol: string; name: string; changePercent: number }[];
  status: "強勢" | "偏多" | "整理" | "偏弱";
}

export interface NewsItem {
  id: string;
  title: string;
  summary: string;
  category: string;
  symbols: string[];
  source: string;
  publishedAt: string;
  sentiment: "positive" | "neutral" | "negative";
}

export const MOCK_NEWS: NewsItem[] = [
  { id: "n1", title: "晶圓代工與先進封裝供應鏈維持高能見度", summary: "AI 伺服器需求帶動先進製程與封裝相關供應鏈關注度。", category: "半導體", symbols: ["2330", "2454"], source: "展示新聞中心", publishedAt: "2026-07-24T12:20:00+08:00", sentiment: "positive" },
  { id: "n2", title: "伺服器供應鏈觀察出貨與匯率變化", summary: "市場關注下半年伺服器出貨節奏、零組件供應與匯率影響。", category: "電腦及週邊", symbols: ["2317", "2382", "6669"], source: "展示新聞中心", publishedAt: "2026-07-24T11:05:00+08:00", sentiment: "neutral" },
  { id: "n3", title: "金融股除息行情與資產品質成焦點", summary: "投資人持續評估股利政策、利差與資產品質表現。", category: "金融保險", symbols: ["2881", "2882"], source: "展示新聞中心", publishedAt: "2026-07-24T09:40:00+08:00", sentiment: "neutral" },
  { id: "n4", title: "航運報價波動，市場關注旺季需求", summary: "運價與供需變化使航運族群波動放大，需留意風險。", category: "航運業", symbols: ["2603"], source: "展示新聞中心", publishedAt: "2026-07-23T16:10:00+08:00", sentiment: "negative" },
  { id: "n5", title: "電子紙應用擴張帶動光電族群話題", summary: "零售、物流與低耗能顯示應用持續擴張。", category: "光電", symbols: ["8069", "3008"], source: "展示新聞中心", publishedAt: "2026-07-23T14:35:00+08:00", sentiment: "positive" },
];

export async function buildMockIndustryHotspots(): Promise<IndustryHotspot[]> {
  const rows = await Promise.all(stockCatalog.map(async (stock) => {
    const payload = await stockService.getStock(stock.symbol);
    if (!payload) return null;
    const latest = payload.prices.at(-1)!;
    const previous = payload.prices.at(-2)!;
    return {
      ...stock,
      changePercent: (latest.close - previous.close) / previous.close * 100,
    };
  }));
  const groups = new Map<string, NonNullable<(typeof rows)[number]>[]>();
  rows.filter((row): row is NonNullable<typeof row> => row !== null).forEach((row) => {
    groups.set(row.industry, [...(groups.get(row.industry) ?? []), row]);
  });
  return [...groups.entries()].map(([industry, members]) => {
    const changePercent = members.reduce((sum, member) => sum + member.changePercent, 0) / members.length;
    return {
      industry,
      changePercent,
      momentum: Math.round(Math.min(100, Math.max(0, 50 + changePercent * 12))),
      stockCount: members.length,
      leaders: members.sort((a, b) => b.changePercent - a.changePercent).slice(0, 3)
        .map(({ symbol, name, changePercent: change }) => ({ symbol, name, changePercent: change })),
      status: changePercent >= 1 ? "強勢" as const : changePercent > 0 ? "偏多" as const : changePercent > -1 ? "整理" as const : "偏弱" as const,
    };
  }).sort((a, b) => b.changePercent - a.changePercent);
}
