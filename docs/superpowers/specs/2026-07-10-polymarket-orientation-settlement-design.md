# Polymarket Orientation and Settlement Safety Design

Date: 2026-07-10

## Problem

The Polymarket normalization boundary currently creates named outcomes by zipping
`outcomes` and `outcomePrices`. Live payloads can disagree with that positional
ordering. Several held positions were therefore marked against the opposite side,
overstating marked value by about $5.81 and reducing the code-reported drawdown
from a corrected 23.31% to 11.68%.

The production settlement source also reads generic market metadata and treats
`closed=true` as settled. It then infers the winner from the same positional price
arrays. This produced 54 ambiguous settlement failures and left an expired paper
position unresolved.

Post-restart runtime evidence confirms the marking defect affects a live safety
gate: G7 bound zero of 17 decisions and two candidates passed readiness while
corrected drawdown exceeded the 20% limit. Later concentration and correlation
guards prevented paper execution.

## Scope

This change owns five boundaries:

1. Polymarket market price normalization.
2. Price-orientation provenance carried by normalized market objects and paper
   snapshots.
3. Fail-closed snapshot fallback for open-position marking.
4. Public settlement endpoint access.
5. Authoritative binary settlement parsing.

It does not change bankroll accounting, paper resolution atomicity, readiness
thresholds, sizing, live/paper mode, or the dirty research-admission worktree.

## Authoritative Contracts

Modern market payloads identify binary sides through `marketSides[].long`.
Direct numeric-ID payloads publish the long/YES book as top-level
`bestBidQuote` and `bestAskQuote`; slug-list payloads can omit that BBO while
retaining an explicitly oriented quote on each market side.

- YES ask = top-level `bestAskQuote.value`.
- NO ask = `1 - top-level bestBidQuote.value`.
- When top-level BBO is unavailable, YES and NO prices come from the respective
  `marketSides[].quote.value` selected by `long=true|false`.
- A usable payload must contain exactly one `long=true` side and one `long=false`
  side.
- Prices must be finite decimals in `[0, 1]`.

Legacy embedded outcome dictionaries remain supported only when each dictionary
names itself `Yes` or `No` and carries its own `bestAsk`. String outcomes plus
positional `outcomePrices` are never a price authority.

Settlement authority is `GET /v1/markets/{slug}/settlement`. Its `settlement`
field is terminal only at exactly `0` or `1`:

- `1` resolves long/YES.
- `0` resolves short/NO.
- Missing, nonnumeric, nonfinite, out-of-range, or fractional values fail closed.

The returned slug must match the requested canonical slug. Numeric stored market
IDs are first resolved to a slug through the existing market lookup path.

## Design

### Normalization Boundary

`normalize_polymarket_market()` becomes the only place that assigns side prices.
It first validates binary side identity. When authoritative top-level BBO fields
are present, it derives YES and NO asks from that long book and stamps
`price_source="polymarket_public"` and `price_method="pm_long_book_v1"`.

If top-level BBO is absent, the normalizer may use `marketSides` quotes selected
by the explicit `long` identity and stamp `price_method="pm_named_sides_v1"`.
Explicitly named embedded outcome dictionaries remain a final legacy-compatible
fallback stamped `price_method="pm_named_outcomes_v1"`. Positional string arrays
yield an unpriced market rather than a guessed mapping.

Invalid price inputs are not clamped into the tradable range. They produce
`None`, leaving `PolymarketMarket.is_tradeable()` false.

### Provenance

`PolymarketMarket` gains optional `price_source` and `price_method` fields with
empty defaults. Existing construction remains source compatible. The current
paper snapshot serializer already serializes dataclass fields, so new entries
carry provenance without a database migration.

### Snapshot Fallback

`_poly_snapshot_mark_cents()` accepts only snapshots whose `price_method` is a
known orientation-safe method. Existing unversioned snapshots are unpriced and
contribute zero marked value. This is intentionally conservative: unknown cost
lowers computed equity and keeps G7 fail-closed.

Live marks always win over snapshot fallback.

### Settlement Source

`PolymarketPublicClient.get_market_settlement(market_id)` resolves the canonical
slug, calls the dedicated settlement endpoint, validates the response object and
slug, and returns the payload.

`PolymarketPublicSettlementSource` uses that method exclusively in production.
It no longer treats generic `closed=true` metadata as proof of settlement.

`_resolved_yes_from_payload()` accepts the dedicated numeric `settlement` field.
Explicit `resolvedOutcome=YES|NO` remains supported for injected sources and
backward-compatible tests. `outcomePrices` alone is rejected.

### Failure Semantics

- A missing market or settlement remains `SettlementNotFound`.
- Contract drift remains `SettlementDriftError` and halts the reconciliation
  cycle before any database write.
- `PaperTrader._resolve_market_sync()` remains the sole atomic accounting write.
- No live database repair or manual settlement is part of this change.

## Verification

Tests must prove:

- Reversed positional arrays cannot reverse an authoritative long book.
- Ambiguous side identity and malformed BBO fail closed.
- Legacy named outcome dictionaries remain compatible.
- Legacy unversioned snapshots cannot inflate equity.
- The dedicated settlement endpoint is called with the canonical slug.
- Only `0` and `1` settle a market.
- Drift leaves the paper row unresolved.
- Corrected open-position marks restore drawdown above 20% and G7 binds.

Before merge, run the focused P0 suite, the complete Polymarket suite, Ruff on all
changed files, and an independent financial-path review.

After merge, fast-forward the running worktree to `origin/main`, restart with
`zsh -ic botrestart`, recompute marks, and inspect post-boot gate records. Expected
runtime state: paper mode remains enabled, corrected drawdown is near 23.31%, and
`G7_open_exposure_drawdown` binds until actual marked equity recovers.

## Non-Goals

- No readiness threshold relaxation.
- No database mutation or historical snapshot rewrite.
- No matcher, research, lifecycle telemetry, or source-weight changes.
- No attempt to force a paper trade.
