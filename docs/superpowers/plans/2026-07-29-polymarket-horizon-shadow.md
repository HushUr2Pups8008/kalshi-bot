# Polymarket 15-30 Day Horizon Shadow

## Objective

Measure whether the next 15 days of the existing Polymarket universe contain
materially more semantically qualified candidates than the current 0-14 day
paper-admission horizon. This is an evidence collection change, not a trade
policy change.

## Invariants

- Production matching, analysis, paper execution, sizing, and live trading use
  only the configured 0-14 day admission horizon.
- The comparison considers only the disjoint 15-30 day band, paired with the
  fixed 0-14 day production horizon.
- Production and shadow matching share one loaded token-weight snapshot per
  news item. Shadow matching emits no normal weight event and never calls
  analysis or routing.
- Tokenless news emits no horizon snapshot. The summary excludes any other
  horizon tuple rather than mixing it into this fixed study.
- It records a bounded counterfactual snapshot for rejected candidates and
  exact candidate/rejection counts for both bands.
- No code path promotes the wider band automatically.

## Review Rule

Run `scripts/polymarket_horizon_shadow_summary.py` against the verified runtime
cohort. Manual review is required after either 50 valid paired snapshots or
seven days from the first valid snapshot. Promotion remains prohibited unless
the review shows materially higher semantic quality and passes the normal
paper-only evidence and risk review.

## Verification

- The 15-30 day comparison test proves a matching shadow market cannot route
  to analysis.
- Logger tests prove bounded counterfactual records persist in the shared
  runtime lineage.
- Summary tests reject malformed or other-cohort records, exclude changed
  horizon regimes, and keep promotion disabled even after the review threshold.
