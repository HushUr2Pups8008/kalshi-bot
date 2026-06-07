# Polymarket Integration Timing Assessment

Reviewed: 2026-06-07

Plan: `.hermes/plans/2026-06-06_111138-polymarket-trading-integration.md`

Prior review: `.hermes/reviews/2026-06-07-polymarket-plan-readiness-review.md`

## Verdict

Do not start the full Polymarket integration now.

It is the right time to integrate the plan/review artifacts and start only the lowest-risk preparatory slice: Task 0 contract capture and, after that passes review, Task 1 venue type helpers.

The plan is now much more implementation-ready than the prior version, but current repo/runtime state argues against immediately executing the high-risk trading, DB, executor, observer, or authenticated-account tasks.

## Evidence

### Plan Readiness

The repaired plan now addresses the readiness review blockers:

- Task 0 captures official Polymarket US contract fixtures before client code.
- Public market data and authenticated account clients are split.
- Auth signing is aligned to official `timestamp + method + path`.
- `/v1/account/balances` replaces the stale `/v1/portfolio/balance` assumption.
- Paper accounting now includes venue market IDs, side/outcome IDs, fees, settlement source, and net PnL.
- Executor refetch now has a venue-client boundary.
- Secret hygiene and same-day eligibility preflight are explicit.
- The plan scan showed no stale `PolymarketRestClient`, `test_polymarket_rest_client`, `/v1/portfolio/balance`, `validation_errors`, `TBD`, `TODO`, or "Likely fixes" references.

### Runtime State

Current `botcheck`:

- LaunchAgent is running.
- Bot process uptime is about 1 day 4 hours.
- Bot version is `0.33.0`.
- `LIVE_ORDER=0`.
- `PAPER_TRADE=1` in the recent window.
- Kalshi drift heartbeat is OK.
- Current runtime history is active.

Latest health/report artifacts:

- `logs/reports/health/bothealth_2026-06-07.md` verdict is RED because `POST_FIX_NEW` readiness is NOT_READY: 5 rows below the 200-row gate.
- `logs/reports/daily/daily_review_20260607.txt` shows Kalshi drift halt false, live-readiness NOT_READY, and insufficient after-data.
- `logs/reports/bot_since_restart_assessment_20260607.md` says the bot is operational but not live-ready. It identifies the main bottleneck as fresh-pass to signal/edge conversion, not crashes or parser failures.

### Worktree State

Current worktree is mixed:

- `data/matcher_token_weights.json` is modified runtime state.
- `.hermes/` is untracked and includes the plan/review artifacts.
- `logs/backups/` and `logs/state/` are untracked runtime artifacts.

That is not a safe starting point for high-risk implementation. Runtime state should stay out of the code branch.

### External API State

Official Polymarket US docs checked on 2026-06-07 still support the repaired plan assumptions:

- Authenticated endpoints require API keys; public market data does not.
- Raw auth uses `X-PM-Access-Key`, `X-PM-Timestamp`, and `X-PM-Signature`; the signature is over timestamp + method + path.
- Public markets are fetched from `https://gateway.polymarket.us/v1/markets`.
- Account balances are fetched from `https://api.polymarket.us/v1/account/balances`.
- Current rate limits differ from the archived single 60 req/min assumption.

## Recommendation

Proceed now with a narrow prep branch only:

1. Commit the repaired plan and review/timing artifacts in a docs-only PR.
2. Create a clean implementation branch or worktree from updated `main`.
3. Execute Task 0 only: contract snapshot markdown + JSON fixture + tests.
4. Stop for review after Task 0.
5. If Task 0 passes and the worktree remains clean, execute Task 1 venue helpers as a separate PR.

Do not execute these yet:

- DB/schema tasks.
- `trading/executor.py` venue-refetch changes.
- `trading/paper_trader.py` paper accounting changes.
- Runtime observer wiring.
- Authenticated account client with real credentials.
- Any restart, launchd change, service-manager action, or live order path.

## Preconditions Before High-Risk Integration

Before starting Tasks 3 through 13:

1. Runtime artifacts must be separated from code artifacts.
2. The plan/review artifacts should be committed or otherwise deliberately excluded.
3. Task 0 contract snapshot must be merged and reviewed.
4. The operator should explicitly approve high-risk implementation that touches `trading/`, DB schema, executor logic, credentials, or runtime wiring.
5. Current Kalshi bot instability should not be mistaken for a Polymarket blocker, but the live-readiness RED state means Polymarket work must stay paper-only and disabled by default.
6. Each high-risk task should be its own reviewed PR with focused tests and no runtime restart unless separately approved.

## Bottom Line

Right now is appropriate for contract-capture and planning integration.

Right now is not appropriate for broad Polymarket trading integration into runtime behavior.
