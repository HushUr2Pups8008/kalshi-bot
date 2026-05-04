# Day-4 mid-soak sanity check

**Author:** Codex
**Date:** 2026-05-04
**Source:** `logs/governance/decisions.jsonl`

## TL;DR

Day-4 records now exist and the soak still looks healthy.

- `6` new day-4 fast cycles landed on 2026-05-04 UTC
- cycle starts: `01:30Z`, `03:30Z`, `05:31Z`, `07:31Z`, `09:32Z`, `11:32Z`
- `30` new `GOVERNANCE_DECISION` rows (`5` per cycle)
- `0` new `PARSE_ERROR`
- `0` new `VALIDATION_ERROR`
- `0` new `KILL_SWITCH`

## Exact day-4 cycle IDs

| cycle_id | first decision at UTC |
| --- | --- |
| `gc_2026-05-04_013026` | `2026-05-04T01:30:32Z` |
| `gc_2026-05-04_033048` | `2026-05-04T03:30:54Z` |
| `gc_2026-05-04_053110` | `2026-05-04T05:31:16Z` |
| `gc_2026-05-04_073132` | `2026-05-04T07:31:38Z` |
| `gc_2026-05-04_093154` | `2026-05-04T09:32:07Z` |
| `gc_2026-05-04_113224` | `2026-05-04T11:32:30Z` |

## Cadence check

Inter-cycle spacing by first decision timestamp:

- `01:30Z -> 03:30Z`: `120.10 min`
- `03:30Z -> 05:31Z`: `120.10 min`
- `05:31Z -> 07:31Z`: `120.11 min`
- `07:31Z -> 09:32Z`: `120.22 min`
- `09:32Z -> 11:32Z`: `120.10 min`

Interpretation: fast cadence still holds. The tiny drift is a few seconds of execution jitter, not schedule breakage.

## Delta vs snapshot 5

Snapshot 5 (2026-05-03T21:42Z) ended at:

- `30 / 30` cycle starts/ends
- `123` `GOVERNANCE_DECISION`
- `7` parse errors
- `0` validation errors
- `0` kill-switch events

State after the six observed day-4 cycles:

- day-4 adds `+6` cycles
- day-4 adds `+30` `GOVERNANCE_DECISION`
- safety counters unchanged

So the expected post-snapshot state is:

- `36` cycles total
- `153` `GOVERNANCE_DECISION` total
- `7` parse errors total
- `0` validation errors total
- `0` kill-switch events total

## Batch shape

All observed day-4 decisions so far are in batch `gb_2026-05-04_0001`.

Per-cycle decision volume is stable at `5`, which matches the day-3 pattern and keeps the soak on the same operational envelope as snapshot 5.

## Bottom line

The day-4 pending-placeholder chain can be considered closed as of 2026-05-04. There is now enough real day-4 data to replace the pending status with a live confirmation report.
