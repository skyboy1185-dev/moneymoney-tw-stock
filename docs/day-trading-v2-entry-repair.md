# V2 entry data and signal isolation repair — 2026-09-10

Production inspection identified two defects affecting paper entries:

- The Yahoo Taiwan stock-list response contains bid, ask and orderbook fields, but the adapter discarded them. Missing books then appeared as a measured 999% spread.
- Signal primary keys did not include the account or mode. An account's skipped signal could prevent another account from evaluating the same stock, strategy and bar.

The September 10 audit found 77,090 controller candidate evaluations whose duplicate-signal reason matched a signal owned by another account. It also found 41,249 evaluations scoring at least 80 with a spread rejection. These are repeated evaluations, not unique opportunities, and neither count establishes how many trades should have filled.

## Changes

Yahoo books are validated against the snapshot's top bid/ask, price ordering and positive share quantities. Their timestamp remains the source's regularMarketTime, never the HTTP receipt time. Malformed, missing or stale books cannot authorize entry. A valid native book is preserved instead of being overwritten by an older MIS book.

The entry gate calculates spread from the validated quote and distinguishes missing/stale books from a genuinely wide spread. Zero spread is no longer replaced by a sentinel. The final entry check repeats the book freshness and maximum-spread checks.

Paper signal IDs now include account, mode, stock, strategy and normalized bar time in a bounded hash. Existing legacy fills still block duplicate execution for their owner. Skipped signals can be reevaluated in a later scan, with all controller, capital, timing and quote gates repeated. Successful execution updates the skipped signal and prevents subsequent duplicate fills.

The dashboard reports how many quotes have both a fresh trade and a valid fresh book. Liquidity, confidence, allocation, loss limits and maximum spread thresholds were not reduced.

## Evidence and limits

Regression scenarios cover two independent accounts, stale-book recovery on the same bar, duplicate execution, legacy IDs, malformed books, and a Yahoo-parsed quote flowing through the persisted controller into a paper order. Widening spreads are blocked again immediately before entry.

A saved real Yahoo response from September 9 at 10:01:43 Taiwan time contained five levels. The repaired parser produced a 0.1203369% spread and retained the source timestamp and share quantities. This is adapter validation, not a reconstructed September 10 trade.

Today's historic results are unchanged. Discarded historical book data cannot be reconstructed faithfully from later snapshots. Deployment occurs after the September 10 close; the next live session is required to establish sustained coverage and actual paper-trading outcomes.
