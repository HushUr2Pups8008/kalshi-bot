# Read-Only Execution Ledger Plan

1. Add RED tests for signed GET order/fill access, official current wire-field
   validation, API bounds, and a POST-excluding collector protocol.
2. Add RED tests for a separate SQLite schema, exact schema-drift detection,
   immutable order/fill receipts, idempotent replay, cross-order rejection,
   conflict quarantine, and durable incomplete-coverage behavior.
3. Implement the isolated ledger and injected explicit-order collector without
   changing executor, venue-client, `main.py`, paper accounting, or runtime
   configuration.
4. Add a one-shot CLI that is network-off and write-off by default, requiring
   explicit `--allow-network`, `--write`, and `--order-id` choices, and
   labeling every run as manually unattributed with historical coverage unknown.
5. Run focused ledger/collector/rest tests, full static checks, an independent
   safety review, and repository CI. Do not call the authenticated API or
   activate live trading during implementation.
