# Live Submission No-Retry Design

## Problem

The legacy Kalshi order client has no durable client order ID or recovery
lookup. The executor retries a failed or timed-out POST up to three times. A
server may accept the first request while its response is lost, so a retry can
create duplicate exposure.

## Decision

For each live candidate, the executor makes exactly one legacy submission
attempt.

Before the POST, it durably writes a `LIVE_SUBMISSION_INTENT` JSONL event with a
new submission ID and immutable order summary, then atomically reserves that
ticker in the durable hold state. A failed or already-held reservation makes
zero POSTs. The reservation is exclusive across same-process executors and is
reloaded while a process lock is held before every claim.

On any exception or error result, it durably writes
`LIVE_SUBMISSION_UNKNOWN` with the same ID and keeps the reservation. The
executor returns no order ID, does not retry or claim rejection, and blocks
subsequent submissions for that ticker across restart. The same reservation
persists when a response has no verifiable order ID, when a successful venue
response cannot be durably journaled, or when cancellation occurs while the
worker-thread POST may be in flight.

After a verified receipt and durable `LIVE_ORDER` journal, the known-success
reservation is automatically released. A release failure remains fail-closed,
is logged, and never causes another POST. Unknown-outcome holds are never
automatically released; later authenticated reconciliation and an explicit
operator workflow remain required to resolve them.

Legacy order POSTs disable redirects, any 3xx response is rejected before its
body can be parsed as an accepted order, and the transport retry policy
explicitly excludes `POST`.

## Scope

- Remove executor-level retry/backoff for legacy live order POSTs.
- Add durable intent and unknown-outcome event writers.
- Persist an exclusive durable reservation after intent and before every live
  POST; release it only after a verified receipt and durable `LIVE_ORDER`.
- Hold and surface post-response journal failures and in-flight cancellation
  before returning or re-raising cancellation.
- Disable redirects for legacy order POSTs and reject 3xx responses as errors.
- Add tests proving a single POST for transport, 429, and 5xx-style errors,
  exception handling, event linkage, and unchanged success behavior.

## Non-Goals

- Do not submit V2 orders, change endpoints, alter `client_order_id` behavior,
  or infer the V2 YES/NO direction mapping.
- Do not change paper mode, live-mode activation, credentials, sizing, source
  selection, or risk thresholds.
- Do not automatically recover, cancel, or resubmit an unknown submission.

## Residual Risk

This removes automatic duplicate POSTs but is not full authenticated recovery.
The durable JSONL intent and hold are an audit handoff for the later V2 SQLite
ledger and read-only reconciliation collector. Live trading remains disabled
until those controls and fee-net attribution are separately verified.

The hold file uses file fsync plus atomic replacement. Same-process callers
serialize claims, and POSIX runtimes also use an advisory file lock while
reloading and persisting the state. A persistent filesystem or power-loss
failure remains fail-closed when a temporary checkpoint survives, but this is
not a substitute for the later immutable SQLite ledger.
