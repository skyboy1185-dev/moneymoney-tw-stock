export type PatternQuote = { price:number; open:number; high:number; low:number; volume:number;
  turnover:number; date:string; source:string; timestamp?:string; realtime?:boolean; vwap?:number };

export function parsePatternQuote(row: Record<string, unknown>, ticker: string, now: number): PatternQuote | null {
  if (row.symbol !== ticker || row.exchangeDataDelayedBy !== 0) return null;
  const stamp = Date.parse(String(row.regularMarketTime));
  if (!Number.isFinite(stamp) || stamp > now) return null;
  const date = new Intl.DateTimeFormat("en-CA", { timeZone:"Asia/Taipei" }).format(stamp);
  if (date !== new Intl.DateTimeFormat("en-CA", { timeZone:"Asia/Taipei" }).format(now)) return null;
  const raw = (key:string) => Number((row[key] as { raw?:unknown } | undefined)?.raw);
  const price=raw("price"), open=raw("regularMarketOpen"), high=raw("regularMarketDayHigh"), low=raw("regularMarketDayLow");
  const volume=Number(row.volume), turnover=Number(row.turnoverM)*1_000_000, vwap=Number(row.avgPrice);
  if (![price,open,high,low,volume,turnover,vwap].every(value => Number.isFinite(value) && value > 0)
      || low > price || price > high || low > open || open > high) return null;
  return { price, open, high, low, volume, turnover, vwap, date, source:"Yahoo 台灣股市",
    timestamp:new Date(stamp).toISOString(), realtime:row.marketStatus === "open" && now-stamp <= 60_000 };
}

export async function patternCurrentQuotes(companies: Array<{symbol:string;market:string}>) {
  if (companies.length > 50) {
    const result = new Map<string,PatternQuote>();
    for (let offset=0; offset<companies.length; offset+=50)
      for (const [symbol,quote] of await patternCurrentQuotes(companies.slice(offset,offset+50))) result.set(symbol,quote);
    return result;
  }
  if (!companies.length) return new Map<string,PatternQuote>();
  const symbols = new Map(companies.map(c => [`${c.symbol}.${c.market === "上櫃" ? "TWO" : "TW"}`, c.symbol]));
  const response = await fetch(`https://tw.stock.yahoo.com/_td-stock/api/resource/StockServices.stockList;symbols=${[...symbols.keys()].join(",")}?_=${Date.now()}`, {
    cache:"no-store", headers:{"User-Agent":"Mozilla/5.0"}, signal:AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error(`Current quote HTTP ${response.status}`);
  const rows:unknown = await response.json();
  if (!Array.isArray(rows)) throw new Error("Invalid current quote response");
  const quotes = new Map<string,PatternQuote>();
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const ticker = String(row.symbol), symbol = symbols.get(ticker);
    const quote = symbol ? parsePatternQuote(row, ticker, Date.now()) : null;
    if (symbol && quote) quotes.set(symbol,quote);
  }
  return quotes;
}
