# Evidence Store Schema

S2.1 defines the schema contract for the future evidence store. Runtime accessors
belong to S2.2; this document and `docs/evidence_store_schema.sql` are the
authoritative schema specification for that implementation.

The evidence store uses a separate SQLite database:

```text
data/evidence_store.db
```

It must not be merged into `data/paper_trades.db`. The paper-trading database is
execution/accounting state; the evidence store is accumulation-lane belief state
and replay history.

## Contract Drivers

- `/tasks` owns evidence-store reads/writes; `/analysis` remains pure and does
  not perform database I/O.
- S2.1 must support replay with immutable event IDs.
- S2.2 will expose `get_dossier`, `update_dossier`, and `add_evidence`.
- S2.5 will emit `EVIDENCE_INGESTION` and `DOSSIER_UPDATE` events aligned with
  the fields already defined in Section 8 of `IMPLEMENTATION_CONTRACT.md`.
- BSR-1 requires each evidence item to be classified as exactly one of
  `state` or `confidence`.
- BSR-3 requires persistence of drift and recovery state.
- BSR-4 requires storing undecayed original evidence weights so time decay can
  be applied at read time.
- BSR-5 and BSR-7 require retaining source class, timestamps, content identity,
  and n-gram fingerprint/proximity metadata for same-class diminishing returns
  and evidence identity approximation.

## Tables

### `dossiers`

Current per-market dossier state. A dossier is identified by `market_ticker`;
there is one active dossier row per market.

| Column | Type | Null | Notes |
|---|---:|---:|---|
| `market_ticker` | `TEXT` | no | Primary key; stable Kalshi market identifier. |
| `dossier_version` | `INTEGER` | no | Current version, starts at `0`, increments per persisted update. |
| `current_estimate` | `REAL` | yes | Current accumulated probability estimate, `0.0..1.0`. |
| `confidence` | `REAL` | no | Current raw dossier confidence, `0.0..0.95`. |
| `prior_estimate` | `REAL` | yes | Last cross-class anchor estimate used by drift detection. |
| `drift_suspect` | `INTEGER` | no | Boolean `0/1`. |
| `in_recovery` | `INTEGER` | no | Boolean `0/1`. |
| `freeze_started_ts` | `TEXT` | yes | UTC ISO timestamp for drift freeze start. |
| `recovery_started_ts` | `TEXT` | yes | UTC ISO timestamp for recovery start. |
| `recovery_until_ts` | `TEXT` | yes | UTC ISO timestamp for expected recovery end. |
| `last_cross_class_state_update_ts` | `TEXT` | yes | Supports BSR-3 anchor tracking. |
| `created_ts` | `TEXT` | no | UTC ISO creation timestamp. |
| `updated_ts` | `TEXT` | no | UTC ISO last update timestamp. |

### `evidence`

Append-only raw/scored evidence event table. `evidence_id` is the immutable
event identifier and is globally unique.

| Column | Type | Null | Notes |
|---|---:|---:|---|
| `evidence_id` | `TEXT` | no | Primary key; immutable replay identity. |
| `market_ticker` | `TEXT` | no | Foreign key to `dossiers.market_ticker`. |
| `source` | `TEXT` | no | Concrete source label, e.g. feed/source string. |
| `source_class` | `TEXT` | no | Normalized source class used for BSR-1/BSR-5/BSR-7. |
| `headline` | `TEXT` | no | Evidence headline/title. |
| `url` | `TEXT` | yes | Source URL if available. |
| `published_ts` | `TEXT` | yes | Source publication timestamp if available. |
| `ingested_ts` | `TEXT` | no | UTC ISO timestamp when accepted into evidence store. |
| `raw_payload_json` | `TEXT` | yes | Optional raw normalized source payload for replay/audit. |
| `content_hash` | `TEXT` | no | Deterministic content identity for duplicate inspection. |
| `headline_ngram_fingerprint` | `TEXT` | yes | Supports BSR-1/BSR-7 n-gram overlap reconstruction. |
| `correlation_cluster_id` | `TEXT` | yes | Nearest cluster identity if classified as correlated/duplicate. |
| `is_duplicate` | `INTEGER` | no | Boolean `0/1`, aligns with `EVIDENCE_INGESTION`. |
| `correlation_discount_applied` | `INTEGER` | no | Boolean `0/1`, aligns with `EVIDENCE_INGESTION`. |
| `update_type` | `TEXT` | no | `state` or `confidence`; aligns with BSR-1. |
| `quality_score` | `REAL` | yes | Scorer output, `0.0..1.0`, used by confidence evolution. |
| `original_weight` | `REAL` | yes | Undecayed evidence weight; decay is applied at read time. |
| `dossier_version_before` | `INTEGER` | no | Version before ingest/update. |
| `dossier_version_after` | `INTEGER` | no | Version after ingest/update. |
| `p0_contract_version` | `INTEGER` | no | Cohort discriminator; legacy pre-P0 rows migrate to `0`, new writes use `1`. |

### `dossier_updates`

Append-only per-version update history. This table stores snapshots sufficient
to audit actual update outcomes without recomputing the dossier.

| Column | Type | Null | Notes |
|---|---:|---:|---|
| `market_ticker` | `TEXT` | no | Part of composite primary key. |
| `dossier_version` | `INTEGER` | no | Part of composite primary key; version after update. |
| `created_ts` | `TEXT` | no | UTC ISO timestamp for update event. |
| `trigger_evidence_id` | `TEXT` | no | Evidence item that triggered this update. |
| `prior_estimate` | `REAL` | yes | Estimate before update. |
| `new_estimate` | `REAL` | yes | Estimate after update. |
| `update_delta` | `REAL` | no | `new_estimate - prior_estimate`. |
| `confidence_before` | `REAL` | no | Confidence before update, `0.0..0.95`. |
| `confidence_after` | `REAL` | no | Confidence after update, `0.0..0.95`. |
| `update_type` | `TEXT` | no | `state` or `confidence`. |
| `llm_called` | `INTEGER` | no | Boolean `0/1`, aligns with `DOSSIER_UPDATE`. |
| `drift_suspect` | `INTEGER` | no | Boolean `0/1`, post-update state. |
| `in_recovery` | `INTEGER` | no | Boolean `0/1`, post-update state. |
| `p0_contract_version` | `INTEGER` | no | Cohort discriminator; legacy pre-P0 rows migrate to `0`, new writes use `1`. |

### `dossier_update_evidence`

Append-only join table representing the `evidence_ids_contributing` list for a
`DOSSIER_UPDATE` / dossier version.

| Column | Type | Null | Notes |
|---|---:|---:|---|
| `market_ticker` | `TEXT` | no | Part of composite primary key and update FK. |
| `dossier_version` | `INTEGER` | no | Part of composite primary key and update FK. |
| `evidence_id` | `TEXT` | no | Part of composite primary key and evidence FK. |
| `contribution_role` | `TEXT` | no | Defaults to `current_belief_state`; reserved for audit labeling. |

### `structural_priors`

Current structural prior state. There is one structural prior row per market,
upserted after each scheduled or dossier-update-triggered recompute.

| Column | Type | Null | Notes |
|---|---:|---:|---|
| `market_ticker` | `TEXT` | no | Primary key; stable Kalshi market identifier. |
| `prior_estimate` | `REAL` | yes | Current structural probability estimate, `0.0..1.0`. |
| `confidence` | `REAL` | no | Structural prior confidence, `0.0..0.95`. |
| `computed_ts` | `TEXT` | no | UTC ISO timestamp from `PriorEstimate.computed_ts`. |
| `recompute_trigger` | `TEXT` | yes | Short reason, e.g. `scheduled` or `dossier_update`. |
| `input_source_count` | `INTEGER` | no | Number of evidence records consumed in synthesis. |
| `llm_called` | `INTEGER` | no | Boolean `0/1`. |

## Keys, Constraints, And Indexes

Primary keys:

- `dossiers.market_ticker`
- `evidence.evidence_id`
- `dossier_updates (market_ticker, dossier_version)`
- `dossier_update_evidence (market_ticker, dossier_version, evidence_id)`
- `structural_priors.market_ticker`

Foreign keys:

- `evidence.market_ticker -> dossiers.market_ticker`
- `dossier_updates.market_ticker -> dossiers.market_ticker`
- `dossier_updates (market_ticker, trigger_evidence_id) -> evidence (market_ticker, evidence_id)`
- `dossier_update_evidence (market_ticker, dossier_version) -> dossier_updates`
- `dossier_update_evidence (market_ticker, evidence_id) -> evidence`

Indexes:

- `idx_evidence_market_ingested` supports fetching evidence for a market ordered
  by event/ingest time.
- `idx_evidence_market_source_class_ingested` supports same-class rolling-window
  lookup for BSR-1/BSR-5.
- `idx_evidence_market_content_hash` supports duplicate inspection without
  preventing duplicate rows from being recorded.
- `idx_evidence_market_version_after` supports version-bounded replay/debugging.
- `idx_dossier_updates_market_created` supports chronological audit/replay.
- `idx_dossier_update_evidence_evidence` supports reverse traceability from
  evidence to dossier updates.

## Replay And Immutability

Replay can reconstruct state by loading a market's evidence rows ordered by
`ingested_ts` or `dossier_version_after`, then applying deterministic S2.3/S2.4
logic. `dossier_updates` provides the persisted update history for audit and
comparison against replay output.

Immutable identity:

- `evidence.evidence_id` is the immutable event ID.
- `evidence.evidence_id`, `evidence.market_ticker`, and `evidence.ingested_ts`
  are protected by triggers from mutation.
- `evidence` rows, `dossier_updates` rows, and `dossier_update_evidence` links
  are append-only and protected from deletion by triggers.
- `dossiers` is the only mutable table; it represents current state and can be
  rebuilt from append-only history.

## Separation From `paper_trades.db`

The evidence store path is `data/evidence_store.db`. It contains no trade
execution/accounting tables and has no foreign keys into `paper_trades.db`.
`paper_trades.db` remains the paper/live execution outcome store; this database
is solely for accumulation-lane belief state and replay.

## Assumptions

- Dossier identity is per `market_ticker`, matching the contract's traceability
  and event schemas.
- Evidence IDs are caller-generated immutable strings in S2.2/S2.5. This schema
  requires uniqueness but does not mandate a UUID format.
- `input_sources` for structural priors are intentionally not stored here; S3
  structural prior persistence is out of scope for S2.1.
- `content_hash` is not unique because duplicate/correlated evidence must remain
  auditable rather than being silently dropped.
