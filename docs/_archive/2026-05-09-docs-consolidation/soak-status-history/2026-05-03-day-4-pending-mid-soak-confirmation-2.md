# Day-4 Mid-Soak Confirmation — Pending Data

**Checked:** 2026-05-03
**Source:** `logs/governance/decisions.jsonl`
**Task:** Codex day-4 confirmation report once `2026-05-04` records exist.

No `2026-05-04` governance records are present yet. Do not treat this as a day-4 health report; it is a data-availability marker.

## Current Last Available State

The current monitor read still ends on `2026-05-03`:

| date | cycles_started | cycles_ended | decisions | parse_errors | validation_errors | batch_aborted | kill_switch |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-05-01 | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| 2026-05-02 | 14 | 14 | 46 | 0 | 0 | 0 | 0 |
| 2026-05-03 | 12 | 12 | 72 | 0 | 0 | 0 | 0 |

Totals through the current log tail:

- `GOVERNANCE_DECISION`: 118
- distinct targets: 19
- actions: `disable_source` only
- effective sample size: 25 distinct `(target, reasoning)` tuples of 118 raw decisions
- no `KILL_SWITCH`
- no `batch_aborted`
- no `2026-05-04` row yet

## Next Action

Re-run the day-4 confirmation after the first `gc_2026-05-04_*` or `gd_2026-05-04_*` record appears.
