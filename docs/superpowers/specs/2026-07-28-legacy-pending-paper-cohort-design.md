# Legacy-Pending Paper Cohort Design

## Objective

Collect fresh, isolated paper-trading evidence while historical legacy paper
positions remain unresolved, without pretending that the historical ledger is
reconciled or creating any route to live trading.

## Invariants

- Normal active cutover is unchanged: it requires zero unresolved legacy
  `paper_trades` and refuses an unresolved predecessor.
- A pending cohort lives only below
  `data/legacy_pending_paper_cohorts/<cohort-id>/`. It never shares a database
  or manifest root with an active cohort.
- Provisioning copies the root legacy database under the runtime lock and binds
  the manifest to its SHA-256, explicit historical bankroll, unresolved-row
  count, and deterministic unresolved-row fingerprint.
- The root legacy database may later settle. Pending validation continues to
  verify the immutable snapshot and fingerprint, not current root hash equality.
- Active and pending cohort roots cannot coexist. A malformed root, symlink,
  hard link, unbound runtime path, or snapshot mismatch fails closed.
- Runtime and CLI `PaperTrader` construction require an explicit cohort storage
  root for filesystem databases. This prevents an arbitrary external database
  from becoming a parallel path outside the configured cohort topology.
- A pending cohort is permanently paper-only. It blocks persisted auto-live
  restoration, direct `confirm_go_live`, the CLI gate, and aggregate go-live
  evaluation even if other profit checks are mocked or pass.

## Operating Contract

Use `scripts/initialize_legacy_pending_paper_cohort.py` only after reviewing
the legacy ledger. It requires a repeated cohort ID, repeated historical
baseline, exact current unresolved-row count and fingerprint, and literal
`PAPER_ONLY` acknowledgement. The initializer creates only a new isolated
paper database and manifest; it does not resolve, rewrite, or delete legacy
state.

No finalization path is implemented here. A future transition must be a
separate reviewed change with canonical settlement evidence and an explicit
operator certificate; it must not rewrite the pending snapshot to make an
unresolved historical record disappear.

## Non-Goals

- This does not prove profitability, authorize live trading, or alter trading
  allocation.
- This does not fabricate settlement receipts, fee-net P&L, or legacy results.
- This does not loosen active-cohort reconciliation rules.
