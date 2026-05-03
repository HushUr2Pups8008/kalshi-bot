# Day-3 Mid-Soak Confirmation Pending

Requested target: day-3 confirmation for Sunday `2026-05-04`.

Run context: this repository session date is `2026-05-03`, and `logs/governance/decisions.jsonl` contains no `2026-05-04` records yet. Latest observed governance record date is `2026-05-03`. This report is therefore a drift check through the latest available data, not a day-3 confirmation.

## Latest Available Counts

| date | cycles_started | cycles_ended | decisions | parse_errors | validation_errors | batch_aborted | kill_switch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-05-01 | 3 | 3 | 0 | 3 | 0 | 0 | 0 |
| 2026-05-02 | 14 | 14 | 46 | 4 | 0 | 0 | 0 |
| 2026-05-03 | 11 | 11 | 67 | 0 | 0 | 0 | 0 |

## Drift Check

- Total records: 176
- GOVERNANCE_DECISION records: 113
- Action distribution: `disable_source` 113
- Distinct targets: 19
- Effective sample size: 25 distinct `(target, reasoning)` tuples from 113 raw decisions (4.52x duplication)
- Post-GOV-002-fix decisions since `2026-05-03T15:28:40Z`: 10 across 5 targets
- Trailing `2026-05-03` parse-error rate: 0/67 decisions and 0/11 cycles
- Validation errors: 0
- Batch aborted: 0
- KILL_SWITCH: 0

## Read

Operational soak health is stable through the latest available data. The only parse errors in the file remain pre-`2026-05-03` and do not recur in the current day slice. The day-3 report should be regenerated once `2026-05-04` records exist; using this file as day-3 evidence would be a date error.
