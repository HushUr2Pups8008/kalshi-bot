# Cycle-16D M3 — price-fetch code review

**Type:** code review of Codex D3 fetch implementation.
**Drafted:** 2026-05-07 post-Codex D2-D3 commit `0dea2de`.
**Authority:** Cycle-16D charter §"Cycle-16D deliverables" + task split M3; CLAUDE.md "Kalshi API: Signing algorithm is RSA-PSS/SHA-256" gotcha.
**Gates:** Codex D5 / D6 do NOT consume `historical_prices_cycle16d.json` until this review passes.

## TL;DR

`scripts/edge_replay/fetch_historical_prices.py` review against five M3 dimensions: auth correctness, rate-limit compliance, idempotence, security/no-key-leak, no-clobber. **PASS — no blocking findings.** Three non-blocking concerns documented.

D5 + M7 + D6 authorized to consume `logs/edge_replay/cycle16d/historical_prices_cycle16d.json`.

## Auth correctness (PASS)

D3 fetch uses `KalshiRestClient._request("GET", endpoint, params=...)` (line 67). Per CLAUDE.md "Kalshi API" gotcha, `kalshi/rest_client.py` handles RSA-PSS/SHA-256 signing with `salt_length=DIGEST_LENGTH` AND PEM newline normalization internally. D3 inherits the canonical signed-request path; no auth headers constructed by D3 directly.

✓ Auth correctness inherited.

## Rate-limit compliance (PASS)

| guarantee | location |
|---|---|
| Inter-ticker pacing default `--sleep-seconds 0.50` | `fetch_historical_prices.py:102` |
| Two requests per ticker (live + historical); 24 tickers × 2 = 48 requests at 0.50s = ~24s minimum walltime | `price_rows_for_ticker` lines 76-87 |
| 429 backoff: handled inside `KalshiRestClient._request` | inherited from existing client |

✓ Rate-limit posture conservative for one-shot backfill.

## Idempotence (PASS)

`merge_price_rows` (line 49-62):
- Deduplicates by `ts`; same timestamp from live + historical sources collapses to single row.
- Priority: `live_trades` (0) preferred over `historical_trades` (1) when same-ts collision.
- Output sorted: `[by_ts[ts] for ts in sorted(by_ts)]` (line 62).
- `round(price, 6)` (line 45) eliminates float-representation drift.

Same Kalshi API responses → same JSON output across runs. ✓

Discrepancy edge case: Kalshi API may return new trades between two re-runs (live trades accumulate). Source DB content can change. **NOT a script idempotence failure** — script is deterministic given fixed input. Document for D5 audit so any pre/post coverage drift is attributed correctly.

## Security / no-key-leak (PASS)

- `_fetch_endpoint_prices` error path (line 68-69): captures only `str(exc)` — exception messages typically do not include API keys.
- Errors output (`historical_prices_cycle16d.errors.json`) reviewed: contains only `ticker` + `error: "no price rows returned"` strings. No keys, no signatures.
- D2 inventory + D3 implementation explicitly avoid orderbook / WebSocket auth paths that might surface auth headers in payload responses.

Codex independently confirmed: "No API key/signature/private-key strings in the committed price artifacts." ✓

## No-clobber (PASS)

D2 inventory §"Non-goals" line 35: "Do not overwrite `logs/edge_replay/cycle13_live/historical_prices.json`."

D3 implementation writes to `logs/edge_replay/cycle16d/historical_prices_cycle16d.json` (separate path). ✓ Original cycle-13 file preserved.

## Coverage result reported by Codex

- 24 markets requested; 23 with price rows; 1 ticker error.
- Probe coverage: 271/272 (99.63%) post-fix dossier rows priced.
- Above ≥90% locked threshold (charter §"Coverage acceptance"); per-ticker anomaly flagged for M7 review.

D6 IC §16 re-run can proceed with current data.

## Non-blocking concerns

### M3.1 — `limit=1000` no-pagination

`_fetch_endpoint_prices` line 67: `params={"ticker": ticker, "limit": 1000}`. Kalshi response includes `cursor` field; current implementation does NOT follow cursor for pagination.

**In practice:** D1 probes showed live trade counts of 145 / 146 / 798 / etc. across tickers. 798 is high but still well under 1000. 99.63% coverage achieved without pagination.

**Risk:** if a higher-volume ticker (`KXTRUMPIRAN` 107 evidence rows in cycle-13; market trade volume could be hundreds-to-thousands) is in scope for a future replay, cursor-less fetch under-covers. Not blocking current Cycle-16D, but worth file as cycle-17+ tech debt. Suggest follow-up commit adds cursor pagination as defensive coverage.

### M3.2 — `min_ts` / `max_ts` parameters not used

D1 docs reference (`MarketsApi.get_trades` per `markets_api.observed` line 26 of script): "get_trades accepts ticker, min_ts, max_ts query parameters."

Current D3 implementation queries the entire time range available per endpoint. For Cycle-16D's specific 16-day evidence window, `min_ts`/`max_ts` would scope the query and reduce response size. Not blocking (current coverage adequate); useful optimization for future high-volume tickers.

### M3.3 — `KXPARDONSTRUMP-26APR-22` 0% coverage anomaly

Codex reports 1-row 0% coverage for this ticker; sidecar error: `"no price rows returned"`. M7 territory. Possible causes:
- Low-volume market — no trades at all in either live or historical endpoint.
- Ticker resolution: cycle-13 evidence may reference a ticker no longer queryable.
- Endpoint mapping issue — ticker format mismatch.

M7 coverage acceptance review will examine. Per locked charter:
- 99.63% overall ≥ 90% → D6 proceeds.
- Per-ticker `KXPARDONSTRUMP-26APR-22` 0% < 80% → flagged anomaly; investigate but does not block D6.

## Summary

| dimension | status |
|---|---|
| Auth correctness | ✓ PASS (inherited from KalshiRestClient) |
| Rate-limit compliance | ✓ PASS (0.50s inter-ticker default) |
| Idempotence | ✓ PASS (deterministic script behavior) |
| Security / no-key-leak | ✓ PASS (errors sidecar reviewed; Codex confirmed) |
| No-clobber of cycle13_live prices | ✓ PASS (separate output path) |
| Non-blocking concerns | 3 (M3.1 no-pagination; M3.2 no min_ts/max_ts; M3.3 KXPARDONSTRUMP anomaly) |

**D5 + M7 + D6 authorized to consume `logs/edge_replay/cycle16d/historical_prices_cycle16d.json`.**

## Cross-links

- `docs/governance/2026-05-07-cycle-16d-charter-price-reconstruction.md` — charter (criteria source).
- `docs/governance/2026-05-07-cycle-16d-task-split.md` M3 — task definition.
- `docs/governance/2026-05-07-cycle-16d-price-source-inventory.md` — D2 inventory.
- `docs/governance/2026-05-07-cycle-16d-pre-execution-criteria-verification.md` — M2 + M4 verification.
- `scripts/edge_replay/fetch_historical_prices.py` — D3 implementation reviewed.
- `tests/test_edge_replay_fetch_historical_prices.py` — D3 test coverage (7 passed per Codex).
- `logs/edge_replay/cycle16d/historical_prices_cycle16d.json` — D3 output (D6 input).
- `logs/edge_replay/cycle16d/historical_prices_cycle16d.errors.json` — D3 error sidecar (1 ticker).
- CLAUDE.md "Kalshi API: Signing algorithm is RSA-PSS/SHA-256" — load-bearing for auth correctness.
