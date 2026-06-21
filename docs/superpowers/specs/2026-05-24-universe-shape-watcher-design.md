# Universe-shape watcher — design spec

**Status:** spec only. Implementation gated on operator approval.
**Date:** 2026-05-24
**Background:** the 13-day zero-trade incident (2026-05-12 → 05-24) was driven by silent universe drift: Kalshi pivoted listings toward sports MVE markets and the bot's effective universe lost policy/macro coverage without any operator-visible signal. PR #33 added per-family-coverage WARN that fires when a single expected series is absent. This spec defines a broader runtime watcher that surfaces shape-level drift.

---

## Detection surface — what the watcher tracks

For each `MarketCache._fetch_geo_markets` cycle (currently every `MARKET_CACHE_TTL_SECONDS`, ~60s during paper mode):

| Signal | Source | Why |
|---|---|---|
| `geo_market_count` | `len(filtered)` after blocklist + days-filter | total tradeable universe size |
| `geo_series_count` | `len(geo_tickers)` | breadth of intake |
| `prior_covered_count` | markets whose `series_ticker` matches an entry in `_SERIES_PRIORS` | tradeable population with regime priors → can clear G4 |
| `expected_present` | `_EXPECTED_POLICY_SERIES` ∩ families-present-in-cache | named-policy coverage |
| `expected_missing` | `_EXPECTED_POLICY_SERIES` ∩ catalog \ cache | already implemented in `_warn_on_missing_expected_families` |
| `sports_share` | (markets whose series starts with sports prefix) / total | how dominated the universe is by sports |
| `g4_eligible_count` | markets with `compute_regime_weights → rc ≥ 0.20` | upper bound on candidates that can clear G4 |

These are computed at refresh time, no extra Kalshi calls beyond what `_fetch_geo_markets` already makes.

---

## Event schema

Single structured log line per refresh, emitted as INFO when nominal and WARN/ERROR on drift trip:

```python
log.info(
    "universe-shape: "
    "geo_markets=%d series=%d prior_covered=%d expected_present=%d expected_missing=%d "
    "sports_share=%.2f g4_eligible=%d verdict=%s",
    geo_market_count, geo_series_count, prior_covered_count,
    len(expected_present), len(expected_missing),
    sports_share, g4_eligible_count, verdict,
)
```

`verdict` ∈ `{"NORMAL", "DEGRADED", "ALARM"}`. ALARM also emits at WARN level with a follow-up line listing missing families and computed share.

---

## Verdict ladder

```
ALARM      if g4_eligible_count == 0
ALARM      if len(expected_present) < ⌊0.5 * len(expected_present_in_catalog)⌋
ALARM      if sports_share > 0.95 and geo_market_count > 0
DEGRADED   if g4_eligible_count < 5
DEGRADED   if len(expected_missing) > 0
DEGRADED   if prior_covered_count / max(geo_market_count, 1) < 0.10
NORMAL     otherwise
```

Numbers are seeds, not load-bearing. Tunable via env (operator-gated):

```
UNIVERSE_WATCH_MIN_G4_ELIGIBLE=5
UNIVERSE_WATCH_MIN_PRIOR_COVERED_RATIO=0.10
UNIVERSE_WATCH_MAX_SPORTS_SHARE=0.95
UNIVERSE_WATCH_MIN_EXPECTED_PRESENT_RATIO=0.50
```

---

## Alert vs soft-halt

Two modes:

| Mode | Behavior |
|---|---|
| **alert-only** (default) | Emit WARN log; operator notification via existing bothealth daily sweep; no runtime action |
| **soft-halt** (opt-in) | On ALARM verdict, set a sentinel file `data/runtime/universe_shape_alarm.json` that the executor consults; executor refuses new trades when sentinel is fresh (≤30 min) |

**Recommendation: alert-only as Phase 1.** Soft-halt requires operator dry-run because it's an automatic gate on trade flow. Phase 2 once alert thresholds have been observed in production for 7+ days.

---

## Where it runs

Inside `MarketCache._refresh()` after `_fetch_geo_markets` returns. One call site:

```python
async def _refresh(self) -> None:
    if time.monotonic() - self._last_fetch < _REFRESH_DEBOUNCE_SECONDS:
        return
    loop = asyncio.get_running_loop()
    try:
        markets, n_series = await loop.run_in_executor(None, self._fetch_geo_markets)
        self._markets = markets
        self._last_fetch = time.monotonic()
        _emit_universe_shape_diagnostic(markets, n_series)   # new
        log.info("Market cache refreshed: %d geo markets from %d series", ...)
    except Exception as exc:
        log.error("Market cache refresh failed: %s", exc)
```

`_emit_universe_shape_diagnostic` is a pure function over the cache state — no extra I/O, no Kalshi calls, no DB writes. ~30 lines.

---

## Relationship to existing PR #33 coverage warning

| Surface | Granularity | Existing? |
|---|---|---|
| PR #33 `_warn_on_missing_expected_families` | per-series binary missing/present | yes, on `main` |
| This watcher | aggregate population shape + categorical verdict | proposed |

The existing per-series WARN is the precise alert for individual gaps. The shape watcher catches the *cumulative* failure mode the 13-day incident demonstrated — many small individual gaps adding up to "bot lost the geopolitical category entirely." Different signal, different cost; both needed.

---

## Tests required at implementation time

1. `test_normal_universe_emits_NORMAL_verdict` — synthetic 100-market cache with healthy ratios → INFO line with `verdict=NORMAL`.
2. `test_zero_g4_eligible_emits_ALARM` — synthetic cache where all `compute_regime_weights` return rc<0.20 → WARN with `verdict=ALARM`.
3. `test_sports_dominant_universe_emits_ALARM` — cache with 96% sports prefixes → WARN.
4. `test_low_expected_present_emits_ALARM` — fewer than half of `_EXPECTED_POLICY_SERIES` present in cache → WARN.
5. `test_below_prior_coverage_ratio_emits_DEGRADED` — cache with <10% prior-covered → WARN at DEGRADED.
6. `test_env_overrides_thresholds` — env-var threshold tuning honored.
7. `test_watcher_emits_once_per_refresh` — no double-emit on debounced refreshes.
8. **Regression pin**: `test_simulated_5_12_universe_would_have_emitted_ALARM` — replay 2026-05-12 universe shape (sports-only effective cache) and assert ALARM.

Test #8 is the load-bearing one: it pins that the watcher would have detected the 13-day incident at hour 1, not day 13.

---

## Acceptance criteria for implementation (when authorized)

- Single new function in `analysis/market_matcher.py` (`_emit_universe_shape_diagnostic` or similar)
- Single new call site in `MarketCache._refresh()`
- Module constants for threshold defaults; env-var overrides
- 8 new tests as enumerated above
- VERSION bump + CHANGELOG entry
- No changes to existing `_warn_on_missing_expected_families` (PR #33 surface preserved)
- No env mutation, no service restart required to ship (next launchd restart picks it up)
