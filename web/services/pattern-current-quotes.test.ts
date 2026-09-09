import { expect, it } from "vitest";
import { parsePatternQuote } from "./pattern-current-quotes";

const now = Date.parse("2026-09-09T03:00:00Z");
const row = {symbol:"3532.TW",exchangeDataDelayedBy:0,regularMarketTime:"2026-09-09T02:59:58Z",
  price:{raw:"100"},regularMarketOpen:{raw:"99"},regularMarketDayHigh:{raw:"101"},regularMarketDayLow:{raw:"98"},
  volume:"100000",turnoverM:"10",avgPrice:"100",marketStatus:"open"};

it("preserves exchange timestamp and actual turnover units", () => {
  expect(parsePatternQuote(row,"3532.TW",now)).toMatchObject({price:100,date:"2026-09-09",volume:100000,
    turnover:10000000,vwap:100,timestamp:"2026-09-09T02:59:58.000Z",realtime:true});
});
it("rejects other symbols, future timestamps and previous-day quotes", () => {
  expect(parsePatternQuote(row,"3443.TW",now)).toBeNull();
  expect(parsePatternQuote({...row,regularMarketTime:"2026-09-09T03:00:01Z"},"3532.TW",now)).toBeNull();
  expect(parsePatternQuote({...row,regularMarketTime:"2026-09-08T03:00:00Z"},"3532.TW",now)).toBeNull();
  expect(parsePatternQuote({...row,price:{raw:"NaN"}},"3532.TW",now)).toBeNull();
});
it("does not label old current-day observations as realtime", () => {
  expect(parsePatternQuote({...row,regularMarketTime:"2026-09-09T02:55:00Z"},"3532.TW",now)?.realtime).toBe(false);
});
