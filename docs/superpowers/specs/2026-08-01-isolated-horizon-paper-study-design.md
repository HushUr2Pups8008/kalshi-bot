# Isolated Polymarket Horizon Paper Study Design

## Objective

Run one manifest-bound, paper-only Polymarket study for the disjoint horizon
band `(14.0, 30.0]` days to close. The study is labelled `15-30d`, but the
exact lower bound is exclusive at `14.0` so no interval between the primary
`0-14d` regime and the study is silently discarded.

The study collects outcome evidence without changing the primary runtime. It
must not make live orders possible, widen `PAPER_ADMISSION_MAX_DAYS_TO_CLOSE`,
write primary journals or databases, mutate primary feedback state, or promote
itself into the primary routing policy.

## Current Boundary

- The primary `main.py` process has one writable `PaperTrader`, one configured
  cohort, and a `0-14d` Polymarket admission horizon.
- `polymarket/paper_runtime.py` currently records `POLYMARKET_HORIZON_SHADOW`
  telemetry from inside that process. It deliberately calls its normal
  `route_analysis` callback only for the primary horizon. That telemetry is
  useful selection evidence, but it is not an isolated paper cohort and cannot
  establish horizon P&L.
- The existing `trading/paper_cohorts.py`, `PaperTrader`, and
  `runtime_paper_cohort_attestation.py` own primary/active/legacy state. Their
  schemas and types are not an extension point for the study.
- The legacy-pending cohort may retain open exposure for long-dated contracts.
  The study must therefore prove safe coexistence by using a separate root,
  ledger, state, attestation, journals, logs, process, and service label. It
  may not wait for, alter, or finalize the legacy-pending family.

## Decisions

### Separate Process, Not a Wider Primary Runtime

The study runs under the dedicated launchd label
`com.jake.kalshi-horizon-paper-study`, executing
`scripts/run_horizon_paper_study.py`. It is never a task started by `main.py`,
never shares its asyncio queues, and never imports or constructs `PaperTrader`
inside the study or primary process.

The primary service remains `com.jake.kalshi-bot` with its existing
environment, cohort, database, log paths, and `0-14d` policy. Installing or
restarting the study label does not bootstrap, bootout, reload, or otherwise
touch the primary label.

The study launcher has these fixed environment invariants:

```
LIVE_TRADING_ENABLED=false
HORIZON_STUDY_ID=pm-horizon-15-30-20260805
HORIZON_STUDY_KIND=polymarket_horizon_15_30
HORIZON_STUDY_STORAGE_ROOT=data/horizon_paper_studies
HORIZON_STUDY_HORIZON_LOWER_EXCLUSIVE_DAYS=14.0
HORIZON_STUDY_HORIZON_UPPER_INCLUSIVE_DAYS=30.0
```

`run_horizon_paper_study.py` parses those values only to compare them with the
manifest. It refuses startup when any differs, when
`LIVE_TRADING_ENABLED` is truthy, or when its service label, lock, attestation
path, manifest, database identity, policy snapshot, or fee schedule binding is
invalid. It derives every study path from the manifest study ID and the fixed
repository roots; a supplied path outside `data/horizon_paper_studies/`,
`logs/state/horizon_paper_studies/`, or the dedicated study log paths is a
fatal non-study-path violation. The runtime has no import or construction path to `LiveTrader`, a
Kalshi order client, or `main.Bot`.

### Dedicated Study Contract and Storage Topology

The one canonical study kind is `polymarket_horizon_15_30`. It appears exactly
as `study_kind` in the manifest and attestation and as
`HORIZON_STUDY_KIND=polymarket_horizon_15_30` in the dedicated service
environment. It is not a `paper_cohort_kind`, is never added to the primary
cohort-kind allowlist, and is never accepted by `main.py` as a writable runtime
selection.

Create separate modules `trading/horizon_paper_study_manifest.py`,
`trading/horizon_paper_study_ledger.py`,
`trading/horizon_paper_study_accounting.py`, and
`trading/horizon_paper_study_attestation.py`. They may reuse pure utilities,
but they must not import or change `PaperTrader`, `trading/paper_cohorts.py`,
the primary paper-trade DDL, or the primary runtime-attestation schema.

`scripts/initialize_horizon_paper_study.py` accepts a new study beside a live
legacy-pending family only after `validate_study_coexistence()` proves every
study path is a plain, non-symlinked descendant of
`data/horizon_paper_studies/pm-horizon-15-30-YYYYMMDD` and disjoint from
`data/paper_trades.db`, `data/paper_cohorts/`,
`data/legacy_pending_paper_cohorts/`, `logs/trades/live/`,
`logs/state/runtime_paper_cohort_attestation.json`, and the primary log paths.
It refuses any target that already has a study manifest, ledger, state DB, or
artifact journal. It does not query, mutate, stop, archive, or wait for a
legacy-pending cohort.

For study ID `pm-horizon-15-30-YYYYMMDD`, use exactly these paths:

```
data/horizon_paper_studies/pm-horizon-15-30-YYYYMMDD/
  manifest.json
  policy_snapshot.json
  fee_schedule.json
  study_ledger.db
  study_state.db
  artifacts/inputs.jsonl
  artifacts/shadow_admissions.jsonl
  artifacts/decisions.jsonl
  artifacts/executions.jsonl
  artifacts/settlements.jsonl
  artifacts/aborts.jsonl
  reports/current.json
  reports/current.md
  locks/runtime.lock

logs/state/horizon_paper_studies/pm-horizon-15-30-YYYYMMDD/
  runtime_attestation.json
```

No study file may be placed in `data/paper_trades.db`,
`data/paper_cohorts/`, `data/legacy_pending_paper_cohorts/`,
`logs/trades/live/trades.jsonl`, `logs/state/runtime_paper_cohort_attestation.json`,
or the primary `logs/app/bot.log` paths. Study logs use
`logs/app/horizon-paper-study.log` and
`logs/app/horizon-paper-study.error.log`.

`manifest.json` is canonical JSON, atomically written once with mode `0600`.
It is immutable after successful initialization and contains:

```json
{
  "schema_version": 1,
  "study_id": "pm-horizon-15-30-YYYYMMDD",
  "study_kind": "polymarket_horizon_15_30",
  "venue": "polymarket_us",
  "created_at_utc": "RFC3339 UTC timestamp",
  "ledger_path": "study_ledger.db",
  "state_db_path": "study_state.db",
  "database_identity": "sha256-bound initialized database identity",
  "starting_bankroll": "explicit positive decimal",
  "horizon_lower_exclusive_days": 14.0,
  "horizon_upper_inclusive_days": 30.0,
  "policy_snapshot_sha256": "sha256 of policy_snapshot.json",
  "fee_schedule_sha256": "sha256 of fee_schedule.json",
  "paper_execution_mode": "isolated_paper_only",
  "live_order_forbidden": true,
  "profit_receipt_attested": false,
  "manifest_sha256": "canonical self-excluding manifest digest"
}
```

`policy_snapshot.json` copies the exact matcher-weight snapshot, matching
threshold, candidate cap, model/prompt identifiers, source policy identifiers,
and horizon values that the study will use. It is read-only and hash-bound by
the manifest. The study never reads a later mutable
`data/matcher_token_weights.json` during a run and never writes to it.

The starting bankroll is a new explicit study bankroll. It is not copied from
`cfg.bankroll`, the legacy cohort, or the active cohort. It contributes only to
the study report and never to primary performance, conversion, or realized-P&L
calculation. Its existence and unresolved exposure are nevertheless visible to
the primary live-transition guard as fail-closed blocking information.

The primary live-transition guard discovers only study manifests through a
read-only `discover_polymarket_horizon_15_30_manifest_blockers(data_root)`
helper. A valid manifest produces the permanent block
`"horizon paper study remains permanently isolated from live trading"`; an
invalid manifest produces a distinct invalid-study fail-closed block. It does
not open `study_ledger.db`, aggregate study exposure, or read a study
attestation. Primary performance, conversion, decision-funnel, daily-review,
and realized-P&L reports exclude all study paths and event types. Thus a study
cannot be ignored by a later live decision, yet it cannot contaminate primary
performance evidence.

`trading/horizon_paper_study_attestation.py` is path-scoped rather than a
generic kind allowlist. It accepts only a validated
`polymarket_horizon_15_30` manifest and writes only
`logs/state/horizon_paper_studies/pm-horizon-15-30-YYYYMMDD/runtime_attestation.json`
after resolving that exact path below the study state root without symlinks. The
receipt contains `schema_version`, `study_id`, `study_kind`, `service_label`,
PID, UTC start time, `ledger_path_relative_to_study_root`, database identity,
manifest SHA-256, and `live_trading_enabled=false`. It rejects the primary
attestation path and every other output path. The primary attestation reader
does not parse this receipt.

### Inputs and Non-Routing Shadow Admission

The study obtains its own read-only feed and public market snapshots. It does
not consume a primary queue, scrape primary trade logs, or call the primary
`route_analysis` callback. A shared pure selection helper may be used, but no
shared runtime side effect may be used.

For every candidate source item, the collector first produces one
`POLYMARKET_HORIZON_STUDY_INPUT` record in `artifacts/inputs.jsonl`. It has:

```json
{
  "schema_version": 1,
  "record_type": "POLYMARKET_HORIZON_STUDY_INPUT",
  "study_id": "manifest study id",
  "manifest_sha256": "manifest digest",
  "input_id": "sha256 of canonical source identity and content hashes",
  "observed_at_utc": "RFC3339 UTC timestamp",
  "source": "source identifier",
  "source_url": "canonical URL or null",
  "source_published_at_utc": "RFC3339 UTC timestamp or null",
  "headline_sha256": "sha256",
  "body_sha256": "sha256",
  "market_snapshot_id": "sha256",
  "market_snapshot_sha256": "sha256",
  "policy_snapshot_sha256": "manifest-bound digest",
  "routing_prohibited": true,
  "record_sha256": "canonical self-excluding record digest"
}
```

The admission engine consumes only a valid input record and the pinned policy
snapshot. It calls a pure public horizon-band selector with
`lower_exclusive_days=14.0` and `upper_inclusive_days=30.0`. It emits a
`POLYMARKET_HORIZON_STUDY_SHADOW_ADMISSION` record for every pre-admission
matchable market in the band, whether matched or rejected. Its required fields
are:

```json
{
  "schema_version": 1,
  "record_type": "POLYMARKET_HORIZON_STUDY_SHADOW_ADMISSION",
  "study_id": "manifest study id",
  "manifest_sha256": "manifest digest",
  "admission_id": "sha256(study_id,input_id,market_id,market_snapshot_id,policy_snapshot_sha256)",
  "input_id": "input receipt id",
  "venue": "polymarket_us",
  "market_id": "public market identifier",
  "market_close_time_utc": "RFC3339 UTC timestamp",
  "days_to_close": "finite decimal in (14.0,30.0]",
  "market_snapshot_sha256": "sha256",
  "match_score": "finite decimal",
  "min_match_score": "pinned finite decimal",
  "selection_status": "qualified|no_token_overlap|below_min_post_weight_score|weight_demoted_below_min_score|invalid_market",
  "policy_snapshot_sha256": "pinned digest",
  "routing_prohibited": true,
  "primary_route_called": false,
  "record_sha256": "canonical self-excluding record digest"
}
```

`qualified` means only that the study must independently rerun analysis and
research using the immutable input, market snapshot, and pinned study policy.
The study decision record must bind `analysis_input_sha256`,
`research_snapshot_sha256`, `counter_evidence_status`, `market_price_snapshot`,
and `estimated_edge` before a study-ledger entry is allowed. It may not reuse a
primary analysis result, research dossier, cache eligibility, or routing
decision. A qualified shadow admission is not a primary opportunity, does not create a
normal `OPPORTUNITY` or `PAPER_TRADE` journal entry, and cannot update primary
matcher weights, keyword outcomes, source credibility, calibration, bankroll,
or risk state.

### Decision and Paper Execution

`polymarket/horizon_paper_study.py` owns a study-only decision adapter. It may
reuse deterministic parsing, scoring, and research functions, but it receives
the immutable input/admission payloads and returns a study decision payload. It
does not call `main.Bot._route_analysis`, `BlendTask`, a primary queue, or any
live execution interface.

`trading/horizon_paper_study_ledger.py` owns the study-only SQLite DDL in
`study_ledger.db`. Its `study_trades` table has a generated
`study_trade_id` primary key, non-null `study_id`, non-null `admission_id`,
market identity, side, entry-price snapshot, size, entry timestamp, terminal
fields, and a unique `(study_id, admission_id)` constraint. It has no
`paper_trades` table, foreign key, migration, import, or write path to any
primary database. The execution adapter accepts only a validated study
decision, atomically claims its `admission_id` in `study_state.db`, inserts one
ledger row, and writes one `POLYMARKET_HORIZON_STUDY_EXECUTION` receipt. A
retry/restart reconciles the unique ledger link; ambiguity aborts rather than
opening a second simulated position.

`trading/horizon_paper_study_accounting.py` owns modeled study accounting. It
does not invoke `PaperAccountingHandlers`, the fee-net runtime flag, existing
paper-trade settlement economics, or a primary outbox. It receives only the
manifest-bound fee schedule and a study ledger entry. The separate boundary is
intentional: all results are study-local modeled outcomes and cannot be
mistaken for the primary fee-net pipeline.

### Artifact Integrity and Deduplication

`study_state.db` is the authoritative atomic journal for artifact IDs, hashes,
payloads, and execution claims. It inserts each input or shadow-admission
record and its canonical hash in one SQLite transaction before emitting the
matching JSONL audit mirror. A crash after the state transaction and before the
mirror is repaired by regenerating the one missing canonical line; a crash
before commit leaves neither an authoritative record nor an executable
admission. It has exactly these constrained keys:

- `input_receipts(input_id PRIMARY KEY, record_sha256 NOT NULL, observed_at_utc NOT NULL)`;
- `shadow_admissions(admission_id PRIMARY KEY, input_id NOT NULL, market_id NOT NULL, policy_snapshot_sha256 NOT NULL, record_sha256 NOT NULL, UNIQUE(input_id, market_id, policy_snapshot_sha256))`;
- `execution_claims(admission_id PRIMARY KEY, state NOT NULL, study_trade_id UNIQUE, claimed_at_utc NOT NULL, updated_at_utc NOT NULL)`;
- `settlement_receipts(study_trade_id PRIMARY KEY, settlement_observation_sha256 NOT NULL, record_sha256 NOT NULL)`.

The artifact writer is single-process and lock-protected by
`locks/runtime.lock`, created with `O_CREAT|O_EXCL`. Before every transaction
it validates schema, manifest hash, record hash, and ID. It writes one
canonical JSON audit line with `O_APPEND` and `fsync` only after the
authoritative state commit. If a restart sees a committed state record without
its mirror line, it reconstructs the identical line. If the same ID has another
hash, an invalid line, an orphaned execution claim, or multiple candidate
study-ledger rows, startup aborts and never records another simulated position.

An input is deduplicated by canonical source identity and content hashes. An
admission is deduplicated by the five-part `admission_id`. Primary and study
deduplication domains are intentionally disjoint: the same news or market may
appear in both without giving either process write access to the other.

### Settlement and Fee Provenance

The study settlement task is
`tasks/horizon_paper_study_settlement_task.py`. It reads only the study database
and public Polymarket terminal-observation payloads. For every terminal result
it records source endpoint identity, observed time, terminal status/outcome,
canonical raw payload hash, normalized result, and the manifest/study/trade
identity in `artifacts/settlements.jsonl`. It does not invoke the primary
canonical settlement outbox, primary feedback consumers, or primary reporting.

`fee_schedule.json` is an immutable, hash-bound study input. It must name the
source document URL, retrieval timestamp, schedule effective range, currency,
entry fee function, settlement fee function, and its own canonical hash. The
only accepted accounting states are:

- `unscorable`: no schedule covers the trade or a required fee is unknown;
- `modeled_pinned_schedule`: both fee functions cover the trade and retain the
  schedule hash plus exact entry and settlement fee calculations;
- `authoritative_receipt`: reserved for a future independently received
  exchange fill receipt and not produced by this paper study.

Every settlement receipt includes `gross_pnl_cents`, nullable
`modeled_fee_net_pnl_cents`, `entry_fee_provenance`,
`settlement_fee_provenance`, `fee_schedule_sha256`,
`profit_receipt_attested=false`, and `live_readiness_eligible=false`. It must
not infer a zero fee, report a modeled amount as a receipt, or use the existing
Kalshi binary zero-settlement policy, `ENABLE_FEE_NET_PAPER_ACCOUNTING`,
`PaperAccountingHandlers`, or primary settlement outbox for Polymarket. Missing
fee provenance is an unscorable settlement, not a zero-cost assumption.

### Failure and Abort Behavior

Fatal startup conditions are a missing/changed manifest, bad database identity,
symlinked path, inconsistent service environment, non-exclusive lock,
`LIVE_TRADING_ENABLED=true`, incorrect horizon, missing policy snapshot,
malformed artifact, conflicting duplicate ID, unavailable fee schedule,
ambiguous recovery, or any attempted primary/live interface. The runner writes
one best-effort `POLYMARKET_HORIZON_STUDY_ABORT` receipt, exits nonzero, and
makes no new admissions or study-ledger entries.

Transient feed and public-market failures produce a bounded retry record. They
cannot reuse stale market data to execute a new study-ledger entry. Backoff is local
to the study process and does not restart, pause, or alter the primary bot.

The explicit operator abort command requires `--study-id` and
`--confirm-study-id` with exact equality. It stops new inputs/admissions,
preserves existing study-ledger entries for settlement, writes an immutable abort
receipt with the reason and manifest hash, and does not delete, reset, or reuse
the study ID. A stopped or aborted study is never reopened.

### Reporting and Interpretation

`scripts/horizon_paper_study_report.py` verifies the manifest, state database,
artifact hashes, and study attestation before producing
`reports/current.json` and `reports/current.md`. It reports separate counts for
inputs, valid admissions, decision rejects, execution claims, open study positions,
terminal rows, gross P&L, modeled fee-net P&L, unscorable fee rows, duplicate
recovery, and abort state.

Every report contains this fixed conclusion field:

```
"cannot_change_primary_horizon_or_live_readiness": true
```

It also includes the current primary cohort identity only as an observation,
never as a shared aggregate. `scripts/horizon_paper_study_check.py` validates
the separate service and its path-scoped receipt. `scripts/botcheck.py` reads
only manifests, never `study_ledger.db`, study journals, or study attestations.
For one valid study manifest it validates path/schema/hash and prints exactly
one summary: `polymarket_horizon_15_30_manifests=1, valid=1, invalid=0,
live_transition_blocked=true`. Any invalid
manifest makes botcheck fail closed. `scripts/decision_funnel_summary.py`,
`scripts/daily_review.py`, and primary profit/readiness reports continue to
exclude the study paths and event types.

`scripts/horizon_paper_study_contamination_audit.py` is a separately invoked,
offline evidence tool. It receives explicit primary log, database, and rendered
report paths plus one study root, then writes a study-local audit report naming
the exact inputs and hashes examined. It checks the study ID, manifest hash,
study artifact record types, and study root paths are absent from those primary
artifacts. It is never imported by the primary runtime or botcheck and does not
make an operational health claim; it is the distinct evidence for report/data
lineage exclusion.

Study evidence may later support a human-reviewed decision to design a new
primary experiment. It never changes `PAPER_ADMISSION_MAX_DAYS_TO_CLOSE`,
enables live trading, changes sizing, or passes a primary readiness gate on its
own. Modeled paper fee net is explicitly not independently attested profit.

## Rejected Alternatives

- Set the primary admission maximum to 30 days. This changes active selection,
  execution, risk, and reports before out-of-sample evidence exists.
- Reuse `PaperTrader`, `paper_trades`, the primary paper DDL, a shared fee-net
  handler, or a primary database migration. The study ledger and accounting
  boundary must remain separate.
- Reuse `POLYMARKET_HORIZON_SHADOW` as execution input. It is telemetry from
  the primary process and lacks study-local input, manifest, decision, and
  dedup lineage.
- Write study receipts into `logs/trades/live/trades.jsonl`, the primary
  settlement outbox, or primary reports. That would contaminate realized and
  conversion evidence.
- Use mutable current weights, default bankroll, a copied primary DB, or a
  generic `active` cohort identity. Each breaks the out-of-sample or isolation
  boundary.
- Infer fees, mark modeled fees as receipts, or use study P&L to automatically
  promote live execution.

## Acceptance Criteria

1. The primary `com.jake.kalshi-bot` service remains on `0-14d`, retains its
   existing cohort/DB/log paths, and has no code path that starts the study.
2. The separate service refuses every invalid environment/binding and cannot
   instantiate a live executor even if an environment variable is changed.
3. A provisioned study has a verified immutable manifest, policy snapshot, fee
   schedule, isolated database/state/journals, and process attestation.
4. Every study candidate has explicit non-routing input and shadow-admission
   evidence, with exact `(14.0, 30.0]` bounds and deterministic deduplication.
5. A crash/restart cannot duplicate an input, admission, or simulated
   study-ledger position; any
   ambiguous recovery aborts rather than trading.
6. Settlement reporting distinguishes gross, modeled, unscorable, and
   independently attested values; this study produces no attested-profit claim.
7. Primary botcheck validates only study-manifest identity and the permanent
   live block. Study check/report independently validate the study; report
   scoping tests plus a separately invoked offline contamination audit establish
   study exclusion from selected primary artifacts.
8. Deployment proves coexistence with any legacy-pending family: all study
   paths are disjoint, no primary DB/schema/log/state path changes, primary live
   transition remains permanently blocked, and primary reports exclude study
   records.
