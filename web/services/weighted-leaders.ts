import type { StockMeta } from "@/lib/types";

export interface WeightedLeaderDefinition {
  rank: number;
  symbol: string;
  name: string;
  weight: number;
  industry: string;
}

export const WEIGHTED_LEADER_SOURCE_DATE = "2026-07-24";
export const WEIGHTED_LEADER_SOURCE_URL = "https://www.yuantaetfs.com/product/detail/0050/ratio";

export const TOP_WEIGHTED_LEADERS: WeightedLeaderDefinition[] = [
  { rank: 1, symbol: "2330", name: "台積電", weight: 58.96, industry: "半導體" },
  { rank: 2, symbol: "2454", name: "聯發科", weight: 5.67, industry: "半導體" },
  { rank: 3, symbol: "2308", name: "台達電", weight: 3.54, industry: "電子零組件" },
  { rank: 4, symbol: "2317", name: "鴻海", weight: 3.16, industry: "其他電子" },
  { rank: 5, symbol: "3711", name: "日月光投控", weight: 2.09, industry: "半導體" },
  { rank: 6, symbol: "2303", name: "聯電", weight: 1.54, industry: "半導體" },
  { rank: 7, symbol: "2383", name: "台光電", weight: 1.42, industry: "電子零組件" },
  { rank: 8, symbol: "2891", name: "中信金", weight: 1.16, industry: "金融保險" },
  { rank: 9, symbol: "3037", name: "欣興", weight: 1.14, industry: "電子零組件" },
  { rank: 10, symbol: "2345", name: "智邦", weight: 1.13, industry: "通信網路" },
  { rank: 11, symbol: "2881", name: "富邦金", weight: 1.09, industry: "金融保險" },
  { rank: 12, symbol: "2327", name: "國巨*", weight: 1.04, industry: "電子零組件" },
  { rank: 13, symbol: "2882", name: "國泰金", weight: 0.95, industry: "金融保險" },
  { rank: 14, symbol: "1303", name: "南亞", weight: 0.90, industry: "塑膠工業" },
  { rank: 15, symbol: "2382", name: "廣達", weight: 0.89, industry: "電腦及週邊" },
];

export function weightedLeaderMeta(leader: WeightedLeaderDefinition): StockMeta {
  return {
    symbol: leader.symbol,
    name: leader.name,
    industry: leader.industry,
    market: "上市",
    peRatio: null,
    dividendYield: null,
    priceToBook: null,
    eps: null,
    marketCap: null,
  };
}
