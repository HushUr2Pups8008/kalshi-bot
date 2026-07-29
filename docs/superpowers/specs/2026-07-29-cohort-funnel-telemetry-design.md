# Cohort Funnel Telemetry Design

## Objective

Make the fresh `legacy_pending` paper cohort measurable without changing
matching, pricing, sizing, horizons, paper admission, or live-trading policy.
The runtime must distinguish an empty market cache, a no-match outcome, and a
downstream admission decision for each new cohort.

## Invariants

- The change is observability-only. It must not widen a market universe, lower
  a score, alter an order size, or create a live-trading path.
- `legacy_pending` remains permanently paper-only.
- Existing JSONL consumers tolerate the additional fields and event types.
- Existing legacy settlement reporting remains rooted at `data/paper_trades.db`.
- Botcheck reads pending-cohort metadata without creating, migrating, or
  mutating a database.

## Design

### Runtime lineage

`TradeLogger` gains an optional immutable runtime context. `TradingBot` binds
the selected paper cohort ID and cohort kind immediately after resolving the
runtime cohort. Every newly appended primary JSONL record receives those two
fields unless the caller explicitly supplies them. Standalone logger use and
historical records remain unchanged when no context is bound.

### Polymarket terminal and cache events

`PolymarketPaperRuntime.process_news()` emits `MATCH_NO_CANDIDATE` when a real
news item cannot advance because the eligible cache is empty or no cached
market meets the matching score. The event includes the source, headline,
venue, eligible-market count, and a machine-readable reason.

`PolymarketPaperRuntime._get_markets()` emits `POLYMARKET_MARKET_CACHE` after a
successful refresh. It records the raw fetched count, whether the provider
returned a cursor, the 30-day eligible count, the configured paper-admission
horizon count, and the configured fetch limit. It does not fetch another page
or change either horizon filter.

### Botcheck cohort status

`scripts/botcheck.py` adds a separate read-only pending-cohort section. It
reports a missing root, invalid root topology, malformed manifest, or each
provisioned pending cohort ID and database path. It does not replace the
legacy-ledger settlement section and does not instantiate `PaperTrader`.

### Regression coverage

Tests verify logger context propagation, both Polymarket no-candidate reasons,
cache-count event fields, and read-only botcheck pending-manifest behavior.
The pending G7 regression verifies that fresh paper admission evaluates the
pending database while the immutable legacy baseline still participates in the
separate permanent live-transition block.

## Success Criteria

- Every new JSONL record emitted by the primary logger after runtime startup is
  attributable to a cohort ID and kind.
- A fresh unmatched headline produces one structured terminal outcome instead
  of an app-log-only message.
- A cache refresh makes raw, 30-day, and admission-horizon counts observable.
- Botcheck can identify the active pending cohort without touching the legacy
  settlement ledger.
- Focused unit and startup suites pass without a matching or trading-policy
  change.
