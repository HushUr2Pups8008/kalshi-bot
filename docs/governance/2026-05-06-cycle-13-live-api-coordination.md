# Cycle-13 live-API coordination note

**Type:** brief operational coordination for Codex's `fetch_resolved_markets.py --live-kalshi` run.
**Drafted:** 2026-05-06 cycle 13.

## Estimated request volume

| operation | approx requests |
|---|---:|
| `get_markets(status='settled')` paged through universe | ~200 |
| `get_markets(status='finalized')` paged through universe | ~200 |
| `get_market(ticker)` × 24 evidence_store markets (verification) | 24 |
| **Total** | **~424** |

## Existing rate-limit guard

`kalshi/rest_client.py` enforces:
- `_MIN_REQUEST_INTERVAL = 0.12` seconds (line 45) → max ~8 req/s
- 429 retry with 0.5 backoff factor (line 68-69)

At max-rate, ~424 requests = ~52 seconds total. Acceptable.

## Concurrency with running bot

`com.jake.kalshi-bot` is currently running (paper mode). It makes its own `/markets` calls during intake cycles (~every 30s during active hours, lighter overnight). Concurrent run = up to ~16 req/s peak during overlap window. Kalshi's published rate ceiling is roughly 30 req/s; collision risk **moderate but not blocking**.

The `KalshiRestClient` rate-limit guard is per-instance, so a Codex-spawned client and the bot's client are independent. They will compete but each respects the 0.12s floor.

## Operator options

1. **No coordination (recommended).** Run `fetch_resolved_markets.py --live-kalshi` while bot continues. Worst case: occasional 429 retries on either side, recovered via existing backoff. Test cost: ~1 minute; downside negligible.

2. **Stop bot during fetch.** If 429 errors observed:
   ```bash
   launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
   .venv/bin/python scripts/edge_replay/fetch_resolved_markets.py --live-kalshi --output ...
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
   ```
   Bot misses ~1 minute of intake; trivial.

3. **Schedule fetch during low-activity window.** Bot intake is heaviest US 13:00-15:00 UTC + 21:00-23:00 UTC (per project CLAUDE.md). Run fetch outside these windows.

## Recommendation

**Path 1 (no coordination).** Existing rate-limit handling is sufficient. If Codex observes 429 retries during fetch, fall back to Path 2.

## Codex action item

Codex's `fetch_resolved_markets.py --live-kalshi` should:
1. Re-use existing `KalshiRestClient` (inherits rate limit + retry).
2. Log paged-request count + any 429s observed.
3. Fail gracefully with operator-actionable error if rate limit hit hard (e.g. "got 5+ 429s; consider stopping bot per coordination doc").

If Codex's existing implementation already does this, no change needed. If not, the addition is small.

## Cross-links

- `kalshi/rest_client.py` lines 45 + 67-69 + 119-129 — rate-limit infrastructure
- `docs/governance/2026-05-06-cycle-13-replay-scope-expansion-charter.md` — Cycle-13 charter
- `scripts/edge_replay/fetch_resolved_markets.py` — fetch script
