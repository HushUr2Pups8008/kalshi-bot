# Read-Only Kalshi Execution Ledger Design

## Problem

The historical paper ledger does not contain authenticated Kalshi fills, fees,
or order receipts. It cannot establish fee-net realized P&L, and the legacy
order path has no exchange `client_order_id` correlation. A collector must not
turn incomplete historical data into an economic claim or weaken the
fail-closed live-submission hold.

## Goal

Add a separate, durable SQLite ledger and an explicit, GET-only reconciliation
path for official Kalshi order and fill receipts. The first slice is default
off, is not scheduled by `main.py`, and cannot submit, cancel, resize, or
release a live-submission hold.

## Correlation Boundary

The collector accepts only an explicit official `order_id`. In the current
legacy path, valid sources are a durable `LIVE_ORDER.order_id` or the
`venue_order_id` recorded after a post-response journal failure. It must never
infer an order from ticker, side, price, or time, and must never clear an
unknown-submission hold. V2 `client_order_id` correlation is a separate later
submission migration.

This first CLI accepts manually supplied IDs only. It persists those records
as `source_kind=unattributed_manual`; they are not bot-attributed evidence and
must be excluded from future bot-profit reporting until a separate,
contract-tested journal-provenance path exists.

## Design

### Read-Only Kalshi API

`KalshiRestClient` gains narrow signed GET methods for:

- `GET /portfolio/orders/{order_id}`
- `GET /portfolio/fills` with `order_id`, time, cursor, limit, and subaccount
  filters.

They validate the outer response shape before returning data. Unexpected fill
payloads are intentionally handed to the ledger, which preserves them in its
quarantine rather than discarding exchange evidence. This slice adds no V2
POST, no generic account scan, and no executor or venue-client change. Kalshi
docs define V2 fixed-point values and fill-level fees as exchange facts; this
slice preserves their raw canonical values without calculating P&L.

### Ledger

`data/live_execution_ledger.db` is independent from `paper_trades.db` and
uses WAL, foreign keys, full synchronous writes, and explicit transactions.
It stores:

- schema metadata;
- immutable order snapshots keyed by `(order_id, payload_sha256)`;
- immutable fill receipts keyed by `fill_id`;
- a quarantine for malformed, unmatched, or conflicting receipts.

All raw receipt payloads are canonical JSON with SHA-256 hashes. A duplicate
fill with the same hash is idempotent. A duplicate `fill_id` with a different
hash is quarantined and never overwrites the first receipt. Order status can
change, so order snapshots are append-only. The order projection carries a
permanent `historical_cutoff_unknown` coverage state, because the recent fills
endpoint cannot establish complete historical fill or fee coverage. Exact DDL
validation plus immutable SQLite triggers make schema and receipt mutation fail
closed.

### Collector And CLI

`KalshiExecutionLedgerCollector.collect_order(order_id)` fetches one explicit
order and pages only its fills. For this bounded first slice it re-pages that
single order from the beginning on every run and applies each complete page in
one ledger transaction. A fetch, validation, or write failure creates no
claim of complete fill coverage; safe replay is supported by receipt
idempotency. Persistent cursors and historical endpoint traversal are later
work with their own cutoff contract.

The one-shot CLI requires both an explicit order ID and `--allow-network`.
It defaults to no network and no writes; persistence additionally requires
`--write`. It emits `complete_coverage=false`, the durable coverage state,
manual attribution source, and an integrity flag; conflicts or quarantines
produce a nonzero exit. It is not wired into `main.py` or launchd.

## Non-Goals

- No V2 order submission, client-order-ID generation, or live-mode activation.
- No order discovery by account-wide scan or ticker/time heuristic.
- No automatic cancellation, resubmission, or release of
  `LiveSubmissionHoldStore` reservations.
- No paper-ledger backfill, fee-net P&L calculation, or settlement attribution.
- No historical-endpoint traversal until its cutoff contract is separately
  tested.

## Acceptance Criteria

- Normal startup creates no ledger, makes no collector request, and changes no
  order path.
- The collector reaches only injected GET methods and is tested to issue zero
  POSTs.
- Missing/malformed receipts are quarantined without claiming complete
  coverage.
- Page replay is idempotent; conflicting external receipts are retained in
  quarantine rather than overwritten.
- A known legacy order can be reconciled only through an explicit order ID.
- Manually supplied IDs remain durably unattributed, and the recent endpoint's
  historical cutoff remains visible in the database and CLI result.
- No result is labeled fee-net or profitable until isolated, complete official
  fill and settlement evidence exists.
