# KXHIGHNY Weather Shadow Capture Design

## Problem

The research pipeline has no leakage-free `KXHIGHNY` corpus of contemporaneous
forecasts, observations, complete sibling quotes, and later outcomes. Existing
research rows represent per-contract gate verdicts and cannot safely model an
event ladder.

The capture path must preserve the current fail-closed admission boundary. It
may not create a probability, trade candidate, paper admission, live-cache entry,
sizing input, or live order.

## Decision

### Add A Lazy, Shadow-Only Weather Store

Weather calibration data will use a dedicated `WeatherShadowStore` backed by
`data/weather_shadow.db`. It will not connect to `data/evidence_store.db` or
extend `research_runs`, `research_evidence`, or `research_paper_admissions`.

The store is lazy:

- `ENABLE_WEATHER_SHADOW_CAPTURE` defaults to `false`.
- When disabled, startup does not import the capture implementation, instantiate
  the store, connect to SQLite, execute DDL, create a journal, or create a task.
- The store constructor performs no I/O.
- `initialize()` creates weather tables only after the enabled runtime task
  starts.
- Weather tables have no foreign keys to research dossier, admission, paper, or
  trading tables.
- The capture module imports no executor, paper trader, blender, sizing, or order
  client. Its Kalshi interface exposes public GET-only market/event methods.

Merging and restarting with the flag absent must not create
`data/weather_shadow.db` or invoke its connection path. Byte-for-byte equality is
required only in isolated disabled-factory tests; live verification checks file
absence and instrumentation because unrelated bot databases continue changing.

## Weather Schema

### `research_weather_shadow_snapshots`

- `snapshot_id TEXT PRIMARY KEY`
- `capture_key TEXT NOT NULL`
- `event_ticker TEXT NOT NULL`
- `target_date TEXT NOT NULL`
- `capture_started_at TEXT NOT NULL`
- `capture_finished_at TEXT NOT NULL`
- `as_of TEXT NOT NULL`
- `close_time TEXT NOT NULL`
- `seconds_to_close REAL NOT NULL CHECK (seconds_to_close >= 0)`
- `horizon_bucket TEXT NOT NULL`
- `forecast_issued_at TEXT NOT NULL`
- `forecast_valid_start TEXT NOT NULL`
- `forecast_valid_end TEXT NOT NULL`
- `observation_measured_at TEXT NOT NULL`
- `observation_coverage_start TEXT NOT NULL`
- `observation_count INTEGER NOT NULL CHECK (observation_count > 0)`
- `weather_retrieved_at TEXT NOT NULL`
- `grid_forecast_high_f REAL NOT NULL`
- `hourly_forecast_high_f REAL NOT NULL`
- `running_observed_high_f REAL NOT NULL`
- `forecast_spread_f REAL NOT NULL`
- `target_weekday INTEGER NOT NULL CHECK (target_weekday BETWEEN 0 AND 6)`
- `source_payload_hash TEXT NOT NULL`
- `source_payload_json TEXT NOT NULL`
- `quotes_hash TEXT NOT NULL`
- `fee_schedule_version TEXT NOT NULL`
- `model_version TEXT NOT NULL`
- `shadow_only INTEGER NOT NULL DEFAULT 1 CHECK (shadow_only = 1)`
- `diagnostic_only INTEGER NOT NULL DEFAULT 1 CHECK (diagnostic_only = 1)`
- `created_ts TEXT NOT NULL`
- `UNIQUE(capture_key)`

`capture_key` is the canonical hash of event ticker, horizon bucket, and model
version. `snapshot_id` is the canonical hash of every evaluation input,
including `capture_key`, normalized weather payload, normalized quote ladder,
contract/rules fingerprints, timing fields, and fee-schedule version. The store
atomically claims the capture key with the first complete snapshot. An identical
retry is ignored. Normal later cycles see the committed claim and skip before
network work. A genuinely non-identical concurrent claimant is written to the
conflict table, does not replace the snapshot, and quarantines that capture key.

### `research_weather_shadow_quotes`

- `snapshot_id TEXT NOT NULL`
- `market_ticker TEXT NOT NULL`
- `close_time TEXT NOT NULL`
- `lower_bound_f INTEGER`
- `upper_bound_f INTEGER`
- `is_lower_tail INTEGER NOT NULL CHECK (is_lower_tail IN (0, 1))`
- `is_upper_tail INTEGER NOT NULL CHECK (is_upper_tail IN (0, 1))`
- `contract_fingerprint TEXT NOT NULL`
- `rules_source_fingerprint TEXT NOT NULL`
- `settlement_source_fingerprint TEXT NOT NULL`
- `yes_bid_cents INTEGER NOT NULL CHECK (yes_bid_cents BETWEEN 0 AND 100)`
- `yes_ask_cents INTEGER NOT NULL CHECK (yes_ask_cents BETWEEN 0 AND 100)`
- `no_bid_cents INTEGER NOT NULL CHECK (no_bid_cents BETWEEN 0 AND 100)`
- `no_ask_cents INTEGER NOT NULL CHECK (no_ask_cents BETWEEN 0 AND 100)`
- `yes_bid_size_fp TEXT NOT NULL`
- `yes_ask_size_fp TEXT NOT NULL`
- `no_bid_size_fp TEXT NOT NULL`
- `no_ask_size_fp TEXT NOT NULL`
- `last_price_cents INTEGER`
- `volume_fp TEXT`
- `price_retrieved_at TEXT NOT NULL`
- `raw_payload_hash TEXT NOT NULL`
- `PRIMARY KEY(snapshot_id, market_ticker)`
- `FOREIGN KEY(snapshot_id) REFERENCES research_weather_shadow_snapshots(snapshot_id)`

No free-form feature, probability, side, edge, sizing, or promotion column
exists. Every initial diagnostic feature has a typed schema field.

All timestamps are canonical RFC3339 UTC values ending in `Z`. Price cents must
be integers in `[0, 100]`. Fixed-point sizes and volume use canonical nonnegative
decimal text and must round-trip without precision loss. Application validation
rejects crossed quotes, inconsistent complements, mismatched close times, and a
`seconds_to_close` value inconsistent with `close_time - as_of`.

### `research_weather_shadow_outcomes`

- `outcome_id TEXT PRIMARY KEY`
- `outcome_batch_id TEXT NOT NULL`
- `market_ticker TEXT NOT NULL`
- `event_ticker TEXT NOT NULL`
- `expected_sibling_count INTEGER NOT NULL CHECK (expected_sibling_count > 1)`
- `result TEXT NOT NULL CHECK (result IN ('yes', 'no'))`
- `kalshi_status TEXT NOT NULL CHECK (kalshi_status IN ('finalized', 'settled'))`
- `settlement_observed_at TEXT NOT NULL`
- `source_payload_hash TEXT NOT NULL`
- `contract_fingerprint TEXT NOT NULL`
- `rules_source_fingerprint TEXT NOT NULL`
- `settlement_source_fingerprint TEXT NOT NULL`
- `official_high_f REAL NOT NULL`
- `official_evidence_id TEXT NOT NULL`
- `official_source_url TEXT NOT NULL`
- `official_product_id TEXT NOT NULL`
- `official_issued_at TEXT NOT NULL`
- `official_retrieved_at TEXT NOT NULL`
- `label_available_at TEXT NOT NULL`
- `created_ts TEXT NOT NULL`
- `UNIQUE(market_ticker, source_payload_hash)`

### `research_weather_shadow_conflicts`

- `conflict_id TEXT PRIMARY KEY`
- `entity_type TEXT NOT NULL CHECK (entity_type IN ('snapshot', 'outcome'))`
- `entity_key TEXT NOT NULL`
- `existing_hash TEXT NOT NULL`
- `incoming_hash TEXT NOT NULL`
- `observed_at TEXT NOT NULL`
- `details_json TEXT NOT NULL`
- `created_ts TEXT NOT NULL`

### `research_weather_shadow_outcome_checks`

- `check_id TEXT PRIMARY KEY`
- `event_ticker TEXT NOT NULL`
- `check_date_utc TEXT NOT NULL`
- `checked_at TEXT NOT NULL`
- `check_kind TEXT NOT NULL CHECK (check_kind IN ('daily', 'seal'))`
- `observed_batch_hash TEXT NOT NULL`
- `baseline_batch_hash TEXT NOT NULL`
- `agrees_with_baseline INTEGER NOT NULL CHECK (agrees_with_baseline IN (0, 1))`
- `details_json TEXT NOT NULL`
- `created_ts TEXT NOT NULL`
- `UNIQUE(event_ticker, check_date_utc, check_kind)`

Snapshots and their complete quote ladders are inserted in one transaction.
Deterministic content IDs make identical retries idempotent. Non-identical
snapshot retries are audited without replacing the first complete fixed-horizon
claim. Distinct outcome versions remain append-only. Calibration readers
quarantine an event when a ticker has multiple distinct results or the final
ladder does not contain exactly one YES. SQLite triggers abort every `UPDATE` and
`DELETE` against all five tables, and every connection enables
`PRAGMA foreign_keys=ON`.

## Capture Flow

A new `tasks/kxhighny_shadow_capture.py` module will provide an isolated
`WeatherShadowCaptureTask` that:

1. Strictly group `KXHIGHNY-YYMONDD-*` siblings by event ticker and target date.
   `target_date` is the `America/New_York` civil date. KNYC observations are
   included only when their measurement timestamps fall within that local
   midnight-to-midnight interval after DST-aware conversion.
2. Select only fixed horizons: `T-24h`, `T-12h`, `T-6h`, and `T-1h`. A bucket
   is captured only during the first cycle whose seconds-to-close value falls in
   `[horizon - 900 seconds, horizon]`. Missed buckets are not backfilled.
   A capture key already committed is skipped before network work; failed or
   rolled-back attempts remain eligible within the window.
3. Resolve the Central Park coordinate through the NWS `/points` endpoint, follow
   its canonical `forecastGridData` and hourly forecast URLs, and fetch KNYC
   observations once per event, not once per sibling. Deterministic features are:
   - `grid_forecast_high_f`: maximum grid `maxTemperature` value whose valid-time
     interval overlaps the New York target civil day;
   - `hourly_forecast_high_f`: maximum hourly-period temperature whose
     `startTime` falls inside that civil day;
   - `running_observed_high_f`: maximum QC-passing KNYC measurement inside the
     civil day and at or before `as_of`;
   - `forecast_spread_f`: grid high minus hourly high; and
   - `target_weekday`: Python weekday for the New York target date.

   Celsius values use exact decimal `F = C * 9 / 5 + 32` conversion and are
   quantized to 0.1F with round-half-up. Native Fahrenheit values are quantized
   identically. Missing, empty, QC-failed, or ambiguous required products make
   the bucket ineligible. Source revisions after the immutable snapshot do not
   rewrite it; their issue/version fields remain visible in the normalized
   payload hash.
4. Fetch and validate weather first, then retrieve the complete quote ladder.
   Define `as_of = capture_finished_at`. Require
   `capture_started_at <= every retrieval time <= as_of < close_time`, a total
   sweep of at most 10 seconds, and every forecast issue/publication time and
   station measurement time to be `<= as_of`. The forecast valid interval must
   cover the New York target date, the station must be KNYC, and
   `weather_retrieved_at <= MIN(price_retrieved_at)` across all siblings.
5. Enumerate the event from the event/series API, not from only active or returned
   price rows. Prove unique tickers, common rules/source/close/status, exactly two
   tails, contiguous non-overlapping internal integer ranges, and a one-hot
   mapping from every feasible final integer high to exactly one sibling.
   Missing, API-omitted, malformed, or crossed legs make the snapshot ineligible.
   Quote retrieval timestamps must share the same 10-second sweep.
6. Store only public, normalized source fields and their hashes. HTTP headers,
   credentials, and unrelated payload fields are excluded.
7. Persist point forecasts and observed features without fabricating a
   probability.

The task has its own bounded periodic loop and fetches the active `KXHIGHNY`
series through a public GET-only market reader. It does not consume the
research prewarm provider's already capped 25-market selection, because that
selection cannot guarantee a complete sibling ladder. The task performs one
series fetch and at most one NWS forecast/observation bundle per event per cycle.
Capture failures are logged and cannot change or abort ordinary prewarm work.

The public market reader reads only the public market-data base URL, never reads
API key/private-key configuration, and exposes no order, balance, position, or
account method. The NWS reader is likewise GET-only. Neither module imports the
trading executor or venue execution client.

The loop runs every 300 seconds with concurrency one, at most two events per
cycle, an eight-second per-request timeout, and a 20-second total cycle budget.
The network/build phase must finish inside that budget before persistence starts.
Budget expiry during that phase cancels the capture and commits nothing. The
short SQLite transaction is then shielded and awaited through shutdown; timeout
or cancellation tests prove no background thread commits after the caller has
reported failure. It shares no SQLite file, HTTP session, or task semaphore with
research, settlement, paper, or execution work.

The enabled factory creates exactly one capture task. No prewarm task interface
or scheduling behavior changes.

## Outcome Flow

The isolated weather task performs outcome labeling through the same public
GET-only market reader and NWS reader. It does not hook into the paper settlement
poll, so zero open paper trades, paper early returns, or paper failures cannot
affect labeling.

- Query captured event tickers with no complete label or still inside the
  correction-monitoring window.
- Persist labels only when every sibling is `finalized` or `settled` with an
  explicit `yes` or `no` result and the temporally valid NWS CLI daily high is
  available.
- Insert the complete sibling outcome ladder and official NWS label in one
  transaction. An injected mid-write failure leaves zero new outcome rows.
- `settlement_observed_at` records when the bot observed the complete finalized
  ladder; it does not claim to be Kalshi's internal settlement time.
- `label_available_at` is the later of the public ladder retrieval and official
  CLI retrieval. Official issue, retrieval, URL, product identity, and evidence
  ID are retained for temporal audit.
- If either source is incomplete, insert nothing and retry on the next weather
  cycle under an independent eight-second request and 20-second labeling budget.
  Label failure cannot delay or abort capture, prewarm, paper settlement, or
  execution work.
- Canonical settlement hashes cover stable result, status, ticker,
  contract/rules/settlement-source fingerprints, and official-high fields,
  excluding quote/volume drift. All three fingerprints are compared with the
  capture-time quote row; any drift quarantines the event.
- Recheck each labeled event once per day for seven days after its first complete
  label. Every attempt writes an append-only outcome-check row keyed by UTC check
  date. A seal row is allowed only after seven distinct successful daily checks
  whose stable batch hashes match the baseline. A correction or missing daily
  check prevents sealing and quarantines the event. After sealing, later external
  corrections are reported as residual risk rather than silently backfilled.
- Readers accept only events whose versions agree and whose complete ladder has
  exactly one YES matching the official integer high.
- Never call paper resolution, admission, blending, or execution code for a
  weather shadow row.

## Safety Invariants

- Feature disabled means zero weather SQLite mutation.
- Weather capture uses a separate database and public GET-only clients.
- Capture is append-only and shadow-only.
- No weather table is read by `analysis/research_gate.py`, live cache, paper
  admission, blender, sizing, or executor code.
- Probability, side, edge, sizing, and promotion fields do not exist.
- All feature and quote availability times are at or before the common `as_of`;
  the bounded capture must finish before market close.
- A snapshot requires an event-enumerated, structurally one-hot sibling ladder.
- Outcome labels must be finalized, explicit, versioned, and coherent; conflicts
  quarantine the event.
- Updates and deletes are prohibited by database triggers.

## Verification

### Weather Store And Capture

- Missing environment flag defaults false.
- Disabled startup does not import/construct the store, call `sqlite3.connect`,
  execute DDL, or create a task.
- An isolated temporary database directory has identical contents and hash before
  and after disabled startup; no weather database file exists.
- Enabled initialization is idempotent.
- Snapshot and quotes commit atomically; injected failures roll back both.
- The first complete capture atomically claims its capture key; later normal
  cycles skip before network work, identical retries are ignored, and differing
  races create an audit conflict without replacing the snapshot.
- Conflicting snapshot/outcome versions remain append-only and quarantine their
  capture key or event.
- `UPDATE` and `DELETE` attempts fail.
- Concurrent writers preserve deterministic content IDs without lost versions.
- Target parsing, bounded horizon windows, common-as-of timing, issue/measurement
  validation, target-date matching, KNYC enforcement, event-enumerated one-hot
  ladders, capture-time bounds/rules/source fingerprints, deterministic grid and
  hourly aggregation, decimal conversion, QC/missing-value rejection, New York
  civil-day boundaries, and absence of probability fields are covered.
- Resource caps cancel and roll back the capture without delaying prewarm.
- Capture-task failure does not abort prewarm or settlement work.
- Complete outcome ladders commit atomically; correction polling, stable hashes,
  append-only daily checks, restart-safe seven-day sealing, capture-to-label
  fingerprint continuity, source-availability timestamps, and quarantines are
  covered.
- Weather labeling uses the public client and runs independently with zero open
  paper trades; it never invokes paper resolution.
- Admission and live-cache regression tests prove weather rows are unreachable.

Full repository tests, lint, protected CI, independent review, restart-boundary
checks, and read-only runtime verification are required before enablement.

## Promotion Boundary

This design does not promote a weather model. A future design may populate
probabilities only after all of the following are proven on independent days:

- at least 60 resolved, non-quarantined event-days, with the final 20 consecutive
  days held untouched after model/version freeze;
- leakage-free walk-forward evaluation before the holdout and one final holdout
  evaluation with no retuning or multiple-model selection;
- coherent mutually exclusive sibling probabilities;
- better Brier score and log loss than contemporaneous market probabilities,
  where each sibling midpoint is `(yes_bid + yes_ask) / 2` and the event vector
  is normalized as `q_i = midpoint_i / sum(midpoint)` only when every leg has a
  valid two-sided quote;
- positive executable EV at one-contract notional, capped by recorded top size,
  using executable asks, the captured fee-schedule version, measured latency,
  and no midpoint substitution;
- a positive 2.5th percentile of mean net P&L from 10,000 event-day bootstrap
  resamples and maximum peak-to-trough loss below 20% of a predeclared $100
  evaluation bankroll;
- all evaluation captures passing timing, ladder, source, and conflict checks,
  with at least 90% coverage of eligible fixed horizons;
- explicit operator approval for paper admission.

Any probability population requires a new schema/design review, a frozen model
artifact, and separate operator approvals for paper influence and live influence.
The coverage denominator is every scheduled fixed-horizon bucket for every active
event enumerated after enablement; provider failures and invalid captures remain
in the denominator and cannot be selectively excluded.

All sealed labels must be re-fetched through both public sources immediately
before model freeze, final holdout scoring, and any promotion approval. Changed,
incoherent, or unavailable labels are quarantined; a prior seal is never treated
as sufficient by itself.

## Deployment And Rollback

1. Land weather code in a protected PR with
   `ENABLE_WEATHER_SHADOW_CAPTURE` absent. Disabled restart verification must
   prove `data/weather_shadow.db` absent and no store/client/task constructor
   called.
2. Obtain a separate operator approval to change the environment used by
   `~/Library/LaunchAgents/com.jake.kalshi-bot.plist` (or its referenced runtime
   profile). Config uses `_parse_bool_env(..., default="false")` and a typed
   `Config.enable_weather_shadow_capture` field.
3. Add `ENABLE_WEATHER_SHADOW_CAPTURE=true`, validate the effective LaunchAgent
   configuration, and perform protected `launchctl bootout`/`bootstrap` or the
   repository-equivalent reload followed by a restart-boundary PID check.
4. Verify one capture task, separate weather schema presence, bounded row growth,
   and valid fixed-horizon rows. Use attribution checks rather than requiring
   unrelated live systems to remain idle: no weather lifecycle ID, source type,
   import, foreign reference, admission, paper row, cache entry, or order may
   exist outside `weather_shadow.db`.
   Register the separate database with the existing daily snapshot backup only
   after enablement, then verify `PRAGMA integrity_check` on a restored copy.
5. Roll back capture by removing or disabling the flag and restarting. Verify the
   task disappears and row counts stop increasing; existing append-only rows
   remain untouched.

## Alternatives Rejected

### Append-Only JSONL

Rejected because rotation-aware joins, atomic snapshot-plus-ladder writes,
deduplication, conflict detection, and outcome reconciliation would require a
second compaction/indexing system.

### Extend Existing Research Tables

Rejected because `research_runs` represents one contract verdict and one selected
price. Mixing event ladders into it risks admission contamination and cannot
represent one shared forecast joined to every sibling quote.

### Immediate Probability Model

Rejected because the current corpus contains zero usable pre-close predictive
probability rows. Existing 0.95/0.05 rows are finalized settlement observations
or temporally contaminated historical evidence.

## Ownership And Execution

| work | primary_agent | second_agent_review_required | operator_gate_required | recommended_workflow | why_this_assignment | safe_while_bot_running | recommended_execution_mode |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Weather store and capture | Codex | yes | schema approval, merge, enablement, restart | TDD, independent data-integrity and admission-boundary review | Persists new financial research state | code/tests yes; enablement no | isolated branch, feature disabled and separate task |
| LaunchAgent enablement | Operator | yes | explicit human action | checklist, protected restart, read-only verification | Changes runtime configuration and creates schema/rows | no | operator-controlled cutover |

## Out Of Scope

- Changing research evidence thresholds or decision-grade requirements.
- Producing forecast probabilities.
- Requeueing historical candidates.
- Paper or live admission.
- Sizing or capital allocation changes.
- Backfilling historical forecasts from data unavailable at the original cutoff.
