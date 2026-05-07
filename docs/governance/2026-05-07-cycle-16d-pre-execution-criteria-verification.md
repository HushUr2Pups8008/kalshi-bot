# Cycle-16D pre-execution criteria-lock verification + independent endpoint read (M2 + M4)

**Type:** verification artifact. Combines M2 (endpoint-diagnosis criteria-lock verification) + M4 (independent endpoint read) per `2026-05-07-cycle-16d-task-split.md`.
**Drafted:** 2026-05-07 post-Codex D1 commit `01910be`.
**Authority:** Cycle-16D charter §"Endpoint-diagnosis criteria" (`2026-05-07-cycle-16d-charter-price-reconstruction.md` — pending; criteria mirrored here from task-split doc).
**Gates:** Codex D3/D4 path selection does NOT proceed until this verification passes.

## TL;DR

M2: Codex D1 classification `solvable_auth_or_param` matches the locked charter criterion **with classification-precision note**: the fix is technically a route swap (path-segment ticker → query-param ticker), which straddles `solvable_auth_or_param` and `solvable_alternate_endpoint`. Both labels reasonable; both route to D3 primary backfill. **PASS** — D3 selection authorized.

M4: independent endpoint read confirms `/markets/{ticker}/trades` is dead and `/markets/trades?ticker=...` + `/historical/trades?ticker=...` are usable. **3 missed-candidate flags for D2 inventory:** `/historical/markets/{ticker}/candlesticks`, orderbook endpoints, settlement endpoints. D2 should formally inventory all three.

## M2 — endpoint-diagnosis criteria-lock verification

### Locked criterion (from task split)

```
solvable_auth_or_param: auth headers fixed OR query parameters adjusted produces 200 with usable data
solvable_alternate_endpoint: different Kalshi endpoint returns per-decision-time price data
permanently_dead: endpoint returns 404 / 410 / Gone for ≥5 distinct ticker probes,
                   AND no Kalshi API doc revision references the change,
                   AND no operator-known auth-tier change
```

### Codex D1 output

```json
{
  "classification": "solvable_auth_or_param",
  "classification_rationale": "legacy per-ticker path is not the documented shape; documented_live_trades returned usable /markets/trades?ticker=... shape.",
  "probe_count": 18,
  "ticker_count": 6 (3 resolved + 3 open)
}
```

Per-variant evidence:
- `legacy_per_ticker_trades` (`/markets/{ticker}/trades`): 6/6 → 404
- `documented_live_trades` (`/markets/trades?ticker=...`): 6/6 → 200, 798 trade rows total
- `documented_historical_trades` (`/historical/trades?ticker=...`): 6/6 → 200, 24 rows total

### Drift check

| charter requirement | Codex output | match |
|---|---|---|
| ≥5 distinct ticker probes | 6 distinct tickers (3 resolved + 3 open) | ✓ |
| Cross-reference Kalshi API docs | `docs_reference.markets_api` + `docs_reference.historical_data` URLs cited | ✓ |
| No auth-tier escalation needed | All three documented endpoints returned 200 with same auth tier as legacy | ✓ |
| Classification mapped to one of three labels | `solvable_auth_or_param` | ✓ (with precision note below) |

### Classification-precision note

Codex chose `solvable_auth_or_param`. Strict reading of the criteria suggests `solvable_alternate_endpoint` is more precise: the fix is `/markets/{ticker}/trades` → `/markets/trades?ticker=...`, which is a different URL path (different endpoint), not the same endpoint with adjusted parameters.

However:
- Both labels route to D3 primary backfill from a working Kalshi REST endpoint.
- The downstream Cycle-16D path selection (D3 vs D4 fallback) is identical regardless of label.
- The Codex rationale "legacy per-ticker path is not the documented shape" reads it as "the bot was using a non-documented path; the documented path works" — fair framing.

**M2 verdict: PASS.** Classification is reasonable; downstream impact identical between the two candidate labels. Recommend D3 path selection proceed; report-time precision can use either label.

### What this verification does NOT cover

- D3 / D4 fetch-code review (M3, post-D3/D4).
- D2 alternative-endpoint inventory completeness (partial M4 below; full review post-D2).
- Auth correctness for D3 production-mode runs (M3).

## M4 — independent endpoint read

### Read sequence

Read D1 raw probe outputs in `logs/edge_replay/cycle16d/endpoint_diagnosis.json` WITHOUT consulting Codex's classification rationale or anticipated D2 inventory. Formed independent diagnosis. Then compared.

### Independent classification

`solvable_auth_or_param` (or `solvable_alternate_endpoint` — see M2 precision note). The legacy `/markets/{ticker}/trades` path is functionally dead; documented endpoints work without auth-tier escalation.

Concur with Codex.

### Endpoint capability summary (independent reading)

| endpoint | status | rows total | coverage range | auth tier observed |
|---|---|---|---|---|
| `/markets/{ticker}/trades` (legacy path) | 404 across 6 tickers | 0 | N/A | base |
| `/markets/trades?ticker=...` (documented live) | 200 across 6 tickers | 798 (~133/ticker) | recent — cutoff by `trades_created_ts` per docs | base |
| `/historical/trades?ticker=...` (documented historical) | 200 across 6 tickers | 24 (~4/ticker) | older — sparse | base |

Two viable endpoints surfaced. **They are complementary, not redundant.**

Per Codex's `docs_reference.historical_data` (line 11 of diagnosis JSON): "live `GET /markets/trades` is cutoff by trades_created_ts; older trades route to `GET /historical/trades`."

For Cycle-16D's 24-market replay window (~16 days of evidence per cycle-13), trades within that window may straddle the live/historical cutoff. **D3 backfill must query BOTH endpoints + merge results to achieve ≥90% coverage per locked criterion.** Single-endpoint query likely under-covers.

### D2 missed-candidate flags

D1 output references `docs_reference.markets_api` line 7 and `docs_reference.historical_data` line 11. Endpoints surfaced in those references but NOT probed in D1:

1. **`/historical/markets/{ticker}/candlesticks`** (mentioned in `historical_data.observed` line 35 of script). Per-decision-time candlestick prices (open/high/low/close per timestamp bucket). Could provide price granularity at decision instants without per-trade aggregation. Worth D2 inventory and possibly D3 use as a coverage extender.
2. **`/series/{series}/markets/{market_ticker}/candlesticks`** (mentioned in `markets_api.observed` line 27). Live candlestick equivalent. Same use case as above for recent windows.
3. **Orderbook endpoints** (e.g., `/markets/{ticker}/orderbook` or `/markets/orderbook`). Not in Codex's docs reference. Orderbook snapshot at a decision instant provides implied `yes_price` (best bid/ask midpoint) without needing actual trades. Useful when trade volume in the decision window is sparse. **Worth checking** Kalshi API docs for orderbook endpoint existence + rate limits.
4. **Settlement / resolution price** for resolved markets. For the 24 resolved markets in `resolved_markets_full.json`, final settlement is 0.0 or 1.0; pre-settlement final-trade price can serve as a sanity-check anchor.

D2 should formally inventory the four candidates. Recommendation matrix: granularity (per-trade > candlestick > orderbook midpoint > settlement) × depth (live cutoff + historical depth + orderbook freshness) × auth-cost.

### Auth / security spot-check (D1 implementation only)

`scripts/edge_replay/endpoint_diagnosis.py` reviewed:
- `_safe_headers` allowlist filters response headers to non-sensitive set (line 70-81). ✓
- `_sanitize_body` redacts response body if it contains `kalshi-access-key`, `kalshi-access-signature`, `authorization`, `private_key`, `api_key` substrings (line 84-90). ✓
- No API keys leaked in `endpoint_diagnosis.json` (verified by reading first 120 lines + body snippets — only public price data + Cloudflare timestamps). ✓

Full M3 fetch-code review fires when D3/D4 land.

### Forward concern (not blocking)

The legacy `/markets/{ticker}/trades` 404 was first surfaced in cycle-13's `fetch_historical_prices.py` probe and noted in cycle-14 charter §"Historical price endpoint gap." Until Cycle-15B C10, the gap was deemed not-a-blocker. Cycle-16D's D3 backfill will use a different code path (`/markets/trades?ticker=...`); old `fetch_historical_prices.py` should be either updated or marked deprecated to prevent future regressions. Worth filing as a follow-up cleanup task — NOT in Cycle-16D scope, but flag here so it doesn't get lost.

## Summary

| dimension | status |
|---|---|
| M2 criteria-lock classification | ✓ PASS (with precision note) |
| M4 independent endpoint read | ✓ concur with Codex |
| M4 D2 missed-candidate flags | 4 candidates flagged for D2 inventory |
| Auth/security spot-check (D1 only) | ✓ clean |

**Codex D3/D4 path selection authorized to proceed.** D3 = primary backfill via `/markets/trades?ticker=...` + `/historical/trades?ticker=...` MERGE (not single-endpoint).

D2 inventory should formally include the 4 missed candidates flagged above.

## Cross-links

- `docs/governance/2026-05-07-cycle-16d-task-split.md` — M2 + M4 task definitions.
- `docs/governance/cycle-16-conditional-charter-skeletons.md` §D — scope skeleton.
- `docs/governance/edge-replay-cycle15b-report.md` — Cycle-15B verdict driving Cycle-16D.
- `logs/edge_replay/cycle16d/endpoint_diagnosis.json` — Codex D1 output verified.
- `scripts/edge_replay/endpoint_diagnosis.py` — Codex D1 script reviewed.
- `tests/test_edge_replay_endpoint_diagnosis.py` — D1 test coverage (3 passed per Codex).
- CLAUDE.md "Kalshi API: Signing algorithm is RSA-PSS/SHA-256" — load-bearing for D3/D4 (M3 review).
- `docs/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md` §"Historical price endpoint gap" — origin of the 404 finding.
