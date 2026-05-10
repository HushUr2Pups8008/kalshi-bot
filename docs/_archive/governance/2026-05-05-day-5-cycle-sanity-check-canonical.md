# 2026-05-05 Day-5 Cycle Sanity Check

**Status:** pending canonical Day-5 cycle rows.

**Source of truth:** `logs/governance/decisions.jsonl`

## Current Read

As of this check, `decided_at` contains no `2026-05-05` UTC rows.

Rows containing the string `2026-05-05` exist, but they are prediction-maturity rows where `predicted_effect.evaluate_at` is `2026-05-05`. Their actual `decided_at` timestamps are `2026-05-04`, so they are not Day-5 cycles.

## Metrics

| Metric | Value |
|---|---:|
| `GOVERNANCE_DECISION` rows with `decided_at` on 2026-05-05 UTC | 0 |
| Fast-cycle decisions | 0 |
| Deep-cycle decisions | 0 |
| `PARSE_ERROR` | 0 |
| `VALIDATION_ERROR` | 0 |
| `KILL_SWITCH` | 0 |
| `batch_aborted` | 0 |

## Interpretation

Do not backfill from `evaluate_at`. Day-specific cycle sanity checks should key off cycle decision time (`decided_at`) so the cadence and safety counters remain comparable to day-4.

## Next Action

Refresh after the first `2026-05-05` UTC `decided_at` rows appear. Preserve the one-canonical-fill-per-UTC-day convention.
