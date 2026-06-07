# Polymarket Plan Readiness Review

Reviewed: 2026-06-07

Plan: `/Users/jacobparenti/vscode/kalshi-bot/docs/governance/2026-06-06_111138-polymarket-trading-integration.md`

## Verdict

Not ready for implementation as-is.

The plan has the right safety direction: Polymarket US only, paper-first, binary-only, no initial live orders, no gate relaxation, and no launchd restart without operator approval. It is useful as a starting draft.

It is not yet a complete handoff for implementation because it encodes unverified Polymarket API/auth assumptions as concrete code and tests, leaves repo-specific integration gaps as "likely fixes", and does not fully define paper-trading correctness for a second venue.

## Blocking Findings

### 1. API contract assumptions are still open but already baked into implementation tasks

Severity: High

Evidence:

- The plan tells implementers to build raw Ed25519 signing over `timestamp + method + path + body` and to pass `body` into the signature message in Task 4, lines 480-483.
- The plan uses authenticated `https://api.polymarket.us`-style client config and `GET /v1/markets`, `GET /v1/markets/{market_id}`, `GET /v1/portfolio/balance` in Task 6, lines 756-805.
- The same plan admits these are unresolved before implementation: "Confirm exact Polymarket US REST paths and payload field names..." and "Confirm whether `POLYMARKET_US_SECRET` is raw 32-byte private key base64, PKCS8 PEM, or SDK-specific secret format", lines 1757-1760.
- Current official Polymarket US docs split the API into authenticated trading/account endpoints at `https://api.polymarket.us` and unauthenticated public market data at `https://gateway.polymarket.us`. The official Get Markets example uses `https://gateway.polymarket.us/v1/markets`, not the plan's authenticated client default for markets.
- Official auth docs show raw requests signed from timestamp + method + path, with milliseconds timestamps and the three `X-PM-*` headers. They do not include request body in the displayed signature message for the shown example.

Impact:

Implementation from this plan can pass mocked tests while being off-contract against the real Polymarket US API. That violates the project requirement to strictly adhere to exchange APIs and detect drift instead of building local assumptions.

Required plan change:

Add a Task 0 before config/auth/client work:

- Capture current official Retail API docs for auth, public market data, account balances, orders, and rate limits.
- Record exact endpoint base URLs, paths, auth requirements, payload schemas, pagination shape, and rate limits in a checked-in fixture/spec file.
- Build tests from those captured fixtures, not from archived guesses.
- Explicitly separate public gateway market data from authenticated trading/account endpoints.
- Confirm secret format with developer portal/SDK docs before implementing the auth helper.

Sources:

- https://docs.polymarket.us/api-reference/introduction
- https://docs.polymarket.us/api-reference/authentication
- https://docs.polymarket.us/api-reference/markets/get-markets
- https://docs.polymarket.us/trader-guide/rate-limits

### 2. Paper-trading correctness is underspecified for a second venue

Severity: High

Evidence:

- The plan adds venue tagging and a paper execution smoke test, but Task 13 only proves a row lands with `venue='polymarket_us'`, lines 1393-1443.
- The current `paper_trades` schema has a single `trade_id` primary key, `ticker`, price, edge, PnL, and resolution fields with no venue column today, `trading/paper_trader.py` lines 55-86.
- The current paper trader repairs executed-side edge from side, probability, and entry price, `trading/paper_trader.py` lines 525-544, and records edge from executed price, lines 835-844. The plan does not say how Polymarket fees, settlement prices, market-side IDs, fills, partial fills, cancels, or order lifecycle state map into this same accounting model.
- The future live-enable section requires at least two weeks of positive expected value net of fees, plan line 1680, but the plan never defines fee capture or net-of-fee paper accounting for Polymarket.

Impact:

A venue column alone proves observability, not trading correctness. Paper evidence collected under this plan would be too weak to support a later live gate because it may not reflect Polymarket fees, settlement mechanics, or execution state.

Required plan change:

Add a dedicated paper accounting task before the paper execution smoke:

- Define Polymarket market ID, side ID/outcome ID, fee coefficient, minimum quantity, tick size, best bid/ask source, and settlement source fields.
- Add fixtures for open, closed, settled, cancelled, and fee-bearing examples.
- Extend paper DB/schema/reporting tests so PnL and edge can be computed net of fees.
- Decide whether `ticker` remains a display identifier or becomes a venue-namespaced market key.

### 3. Executor boundary remains Kalshi-shaped and can refetch through the wrong client

Severity: High

Evidence:

- The plan's candidate adapter makes a `SimpleNamespace` that looks like a Kalshi market, lines 1331-1375.
- Task 13 passes that adapted `SignalAnalysis` directly to `executor.execute(analysis)`, lines 1435-1438.
- Current `TradeExecutor.execute()` always calls `_analysis_from_candidate(candidate)`, `trading/executor.py` lines 104-108.
- Current `_analysis_from_candidate()` returns legacy `SignalAnalysis` unchanged only if `fast_lane_analysis` is absent, otherwise it refetches `candidate.market.ticker` through `self._rest.get_market`, lines 355-385. The executor is still constructed with one `rest` object, and the existing refetch logic is Kalshi-specific by shape and comments.
- Current prefix risk uses `analysis.market.ticker.split("-", 1)[0]`, lines 238-253. Task 13 treats non-`KX...` prefix handling as a "likely fix", lines 1452-1458, not a concrete prerequisite.

Impact:

The plan risks routing Polymarket candidates through Kalshi-specific assumptions. The direct `SignalAnalysis` smoke path avoids the blended-candidate refetch path, so it does not prove venue-aware execution under the actual post-blend route.

Required plan change:

Introduce a concrete venue execution boundary before candidate adaptation:

- Executor receives or resolves a `VenueClient` per venue.
- Refetch is delegated to the candidate's venue client, not the default Kalshi REST client.
- Prefix/exposure keys are explicit: either `venue:market_id` or matched-event IDs.
- Tests cover both direct `SignalAnalysis` and blended-candidate/refetch paths for Polymarket.

### 4. The implementation handoff still contains placeholders and conditional work

Severity: Medium

Evidence:

- The plan says "If `BotConfig` does not expose `validation_errors()` today..." at line 320. Current `BotConfig` has `__post_init__` that accumulates errors then exits, `config.py` lines 1424-1520; no `validation_errors()` helper exists.
- Task 8 says to include venue in logs "if `TradeLog` supports extra kwargs" and otherwise "add a separate task", lines 1038-1040.
- Task 13 says "Modify: only files needed by failures from the test", line 1399, and lists "Likely fixes", lines 1452-1458.

Impact:

This violates the plan's own need to be implementable by agents task-by-task. It leaves important repo-specific integration decisions to whoever happens to execute the task.

Required plan change:

Replace conditional language with concrete current-state tasks:

- First refactor config validation into a testable helper or write tests against current `SystemExit` behavior.
- Inspect and extend `TradeLog` schema before adding venue kwargs.
- Define the exact `PaperTrader`, `Portfolio`, and executor changes required for non-Kalshi IDs.

### 5. Security checks are incomplete for credentials and external side effects

Severity: Medium

Evidence:

- The plan correctly keeps Polymarket live orders blocked initially, lines 30-34 and 1670-1685.
- It does not include explicit tests that Polymarket key IDs/secrets are redacted from logs, exceptions, trade logs, reports, and artifact output.
- `.env.example` says the secret is shown once and cannot be recovered, lines 36-42, and current official docs also warn to store keys safely and never commit them.
- The plan does not include a same-day state/regulatory eligibility recheck before observer enablement; `.env.example` says Colorado was clear as of 2026-04-22 and to re-verify before each enable action, lines 44-46.

Impact:

This integration touches credentials and a financial external API. The plan should prove secret hygiene and pre-enable eligibility checks before any observer, account, or trading API path is enabled.

Required plan change:

Add explicit security acceptance tests:

- No Polymarket secret/key material appears in logs, exceptions, JSONL trade logs, health reports, CI artifacts, or failure output.
- Missing credentials fail closed without printing secret-derived content.
- Public market observer can run with no credentials; authenticated account/trading paths require credentials.
- Operator must re-confirm eligibility and account/API access on the day of any enablement.

## Non-Blocking Strengths

- The plan explicitly excludes Global CLOB, multi-outcome support, initial live orders, gate relaxation, and unauthorized runtime restarts, lines 28-34.
- It recognizes the worktree is dirty and warns implementation agents not to overwrite user changes, line 26.
- It proposes venue-neutral types, venue-aware reporting, cross-venue diagnostics, and future live enablement only in a separate operator-approved phase.
- Existing dependencies already include `requests`, `websockets`, and `cryptography`, `requirements.txt` lines 4-11, so dependency churn can stay minimal.

## Readiness Checklist

- Completeness: Partial. Architecture and phases are broad enough, but API fixture capture, paper accounting, secret hygiene, and blended executor routing are missing or too weak.
- Implementation readiness: No. At least five blocking/medium issues above need plan edits before agents should execute.
- Safety readiness: Partial. Live order safety is strong; credential and eligibility checks need explicit tests.
- API-contract readiness: No. Current official docs conflict with key plan assumptions around market data base URL/schema, and the plan itself flags endpoint/secret format as unresolved.
- Repo-specific readiness: Partial. The plan has good file targeting but still leaves known current-state decisions as conditional language.

## Recommended Next Step

Do a plan-repair pass, not implementation:

1. Add Task 0 for official Polymarket US contract capture and fixtures.
2. Split public market data client from authenticated account/order client.
3. Add paper accounting schema/task for venue IDs, side IDs, fees, settlement, and net PnL.
4. Replace conditional steps with exact current-repo edits.
5. Add secret-redaction and same-day eligibility checks.

After those edits, re-run this readiness review before assigning implementation agents.
