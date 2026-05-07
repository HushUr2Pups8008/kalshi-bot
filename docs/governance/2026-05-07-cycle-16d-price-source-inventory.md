# Cycle-16D price-source inventory

Generated: 2026-05-07

Scope: replay-harness price reconstruction only. No bot extraction, source onboarding, re-ingestion, or live-trading changes.

## Recommendation

Use primary path D3: merge documented Kalshi trade endpoints:

1. `GET /markets/trades?ticker=...`
2. `GET /historical/trades?ticker=...`

D1 proved both return usable trade rows. M2/M4 clarified that single-endpoint querying under-covers the replay window because `GET /markets/trades` is cutoff by `trades_created_ts`; older rows need `GET /historical/trades`.

## Endpoint matrix

| Candidate | Granularity | Historical depth | Auth / tier cost | D16D use |
|---|---|---|---|---|
| `GET /markets/trades?ticker=...` | per trade | recent/live trade window | existing Kalshi REST auth works | primary recent-price source |
| `GET /historical/trades?ticker=...` | per trade | older historical trade window | existing Kalshi REST auth works | primary older-price source |
| `GET /historical/markets/{ticker}/candlesticks` | time bucket OHLC | historical candlestick window | existing Kalshi REST auth likely; not yet probed in D3 | fallback coverage extender if trade rows sparse |
| `GET /series/{series}/markets/{market_ticker}/candlesticks` | time bucket OHLC | current/recent candlestick window | existing Kalshi REST auth likely; not yet probed in D3 | fallback coverage extender if trade rows sparse |
| Orderbook endpoints | current orderbook snapshot | not historical unless separately archived | existing Kalshi REST/WebSocket auth; no arbitrary past timestamp | not sufficient for C10 historical replay; useful only if local archive exists |
| Settlement/resolution price | final binary outcome | complete for resolved markets | no extra auth beyond resolved-market metadata | sanity-check only; not decision-time executable price |

## Path selection

D3 is selected. D4 fallback is not selected because D1 found a documented, usable primary trade path.

If D5 coverage is below threshold, the first extension candidate is candlesticks, not settlement-price approximation. Settlement is too coarse for executable P&L and would require D7 imprecision modeling.

## Non-goals

- Do not overwrite `logs/edge_replay/cycle13_live/historical_prices.json`.
- Do not infer prices from final settlement during D3.
- Do not use orderbook snapshots as historical prices unless an archive is found.
- Do not mix PRE_FIX `paper_trades` into POST_FIX_REBUILT replay.
