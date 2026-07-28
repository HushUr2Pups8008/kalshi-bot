# Same-Window Lifecycle Attribution Plan

1. Add RED fixtures to `test_decision_funnel_summary.py` for unique lifecycle
   attribution of opportunities, explicit G7 skips, zero-cap skips, pending
   opportunities, exact duplicate retries, missing identities, and reused IDs
   with conflicting identities or nonidentical opportunity records.
2. Add a pure summary helper that consumes the existing time-filtered trade-log
   stream, keeps raw event counts unchanged, and separately reports only
   lifecycle sets with a complete shared `(venue, ticker, side)` identity. Never
   use a placeholder ID; quarantine incomplete, conflicting, or ambiguous reuse.
3. Classify each attributed opportunity exactly once as G7 skip, zero-cap skip,
   other skip, linked paper trade, unresolved live submission, pending, or
   conflict. A `LIVE_ORDER` is submission evidence only, never a fill, P&L, or
   conversion claim. Exclude ambiguous lifecycles from arithmetic and expose
   their counts.
4. Render the attribution in `daily_review.py` with the window bounds and an
   explicit note that settlement/mark P&L is outside this lifecycle slice.
5. Promote canonical terminal identity at emission time: executor and BlendTask
   SKIPPED records carry `side`, and LIVE_ORDER records carry `venue`. Preserve
   historical incomplete rows as quarantined; do not backfill or infer fields
   from nested legacy payloads.
6. Add daily-review rendering coverage and run focused tests, lint, a review,
   and full CI. Do not alter gates, sizing, configuration, services, or runtime
   state; do not join resolutions by ticker or call current marks realized P&L.
7. Journal `LIVE_SUBMISSION_INTENT` before reservation and POST with its exact
   `submission_id`, venue, and promoted lifecycle identity. Treat intent-only
   rows as intent without a matching terminal journal, never as proof of POST,
   fill, P&L, or conversion. Reconcile them only against an exact terminal
   `submission_id`.
8. Require stable terminal receipt identities. A `LIVE_ORDER` requires a
   verified venue `order_id`; its local `submission_id` only correlates intent.
   An unknown journal may contribute a verified `venue_order_id` in the same
   receipt namespace. Exact retry copies with the same semantic payload are
   idempotent; changed payloads, receipt reuse across lifecycle IDs, missing
   receipt IDs, mismatched intent/terminal submission IDs, or mixed terminal
   classes are conflicts and remain quarantined.
9. Keep report labels explicit: the contribution view covers paper-trade records
   and live submissions, and live submissions are not fill or P&L evidence.
