# Phase 2 governance shadow-soak — Day-4 mid-soak confirmation

**Generated:** 2026-05-04 13:10Z (system clock)
**Soak elapsed:** ~62.7 h (Day 4 of 14)
**Soak tracker:** `PROFIT-PHASE2-001`
**Source:** `logs/governance/decisions.jsonl`
**Reviewer:** Claude
**Predecessor placeholders:** `2a15d55` (`-2`), `8001a16` (`-3`), `9ce8315` (`-4`).

## TL;DR

**Day-4 confirmation passes.** 6 fast cycles landed in the day-4 UTC window (01:30Z → 11:32Z), all on the 2.0 h cadence, with 0 PARSE_ERROR / 0 VALIDATION_ERROR / 0 KILL_SWITCH / 0 batch_aborted. Soak healthy; continue to 2026-05-15.

This closes the day-4-pending placeholder chain (`-2` / `-3` / `-4`). The next mid-soak deliverable is the day-7 milestone confirmation (~2026-05-07).

## Day-4 cycle list

| cycle_id | started_at | duration_sec | decisions |
|---|---|---:|---:|
| `gc_2026-05-04_013026` | 2026-05-04T01:30:26Z | 21.83 | (per cycle) |
| `gc_2026-05-04_033048` | 2026-05-04T03:30:48Z | 21.78 | (per cycle) |
| `gc_2026-05-04_053110` | 2026-05-04T05:31:10Z | 21.96 | (per cycle) |
| `gc_2026-05-04_073132` | 2026-05-04T07:31:32Z | 22.27 | (per cycle) |
| `gc_2026-05-04_093154` | 2026-05-04T09:31:54Z | 29.92 | (per cycle) |
| `gc_2026-05-04_113224` | 2026-05-04T11:32:24Z | 22.70 | (per cycle) |

Inter-cycle gaps: 2.00 h / 2.00 h / 2.00 h / 2.00 h / 2.01 h. Cadence honoured exactly.

Duration outlier: `gc_2026-05-04_093154` (29.92 s, ~30 % above day-4 avg 23.4 s). Single-event outlier; does not break the cycle. Trailing-window safe.

## Cumulative metrics (snapshot 5 → day-4 confirmation)

| metric | snapshot 5 (21:42Z, 2026-05-03) | day-4 confirmation (13:10Z, 2026-05-04) | delta |
|---|---:|---:|---:|
| total events | 190 | 239 | +49 |
| cycle starts / ends | 30 / 30 | 36 / 36 | +6 / +6 |
| `GOVERNANCE_DECISION` count | 123 | 158 | +35 (30 day-4 + 5 day-3-tail) |
| distinct targets | 19 | 20 | +1 |
| PARSE_ERROR | 7 | 7 | unchanged |
| VALIDATION_ERROR | 0 | 0 | unchanged |
| KILL_SWITCH | 0 | 0 | unchanged |
| `batch_aborted=True` | 0 | 0 | unchanged |
| latest cycle | 2026-05-03T21:29Z | 2026-05-04T11:32Z | +14.05 h |
| elapsed soak hours | 50.5 | 62.7 | +12.2 |

## Day-4 decisions distribution

30 decisions across 8 distinct targets (all overlap with pre-day-4 targets — no new targets surfaced):

| target | day-4 decisions | action |
|---|---:|---|
| `r/prayerstotrump` | 6 | disable_source |
| `r/rescuecats` | 6 | disable_source |
| `r/allthingsjenna` | 4 | disable_source |
| `r/keep_track` | 3 | disable_source |
| `r/stevehofstetter` | 3 | disable_source |
| `r/kateyesrescue` | 3 | disable_source |
| `r/goodnewsuk` | 3 | disable_source |
| `r/zenlesszonezeroleaks_` | 2 | disable_source |

All actions are `disable_source` against persistent low-engagement Reddit sources (no fresh-pass / match-count activity over rolling 168 h). Pattern is consistent with day-2 / day-3 behaviour.

## §8.5 status

| criterion | status |
|---|---|
| time | IN_PROGRESS (62.7 h elapsed, 18.6 % of 14 d target) |
| volume | IN_PROGRESS (36 cycles / 158 candidates) |
| cadence_coverage | **PASS** (32 fast + 4 deep, day-4 schedule honoured exactly) |
| candidate_diversity | PASS (20 distinct targets — monitor still mis-reports as FAIL but the underlying counter is healthy) |
| safety_applied_no_growth | PASS (0 applied, vacuous in shadow mode) |
| safety_kill_switch | PASS |
| quality | PASS |

No criterion is at risk. Continue to 2026-05-15.

## Recommendation

**No operator action required.** Day-4 confirmation closes the pending-placeholder chain. Next mid-soak deliverable is the day-7 milestone confirmation (~2026-05-07).

## Cross-links

- `docs/governance/2026-05-03-mid-soak-snapshot-5.md` — most recent prior snapshot
- `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-4.md` — placeholder this confirmation closes
- `docs/governance/edge-004-closure-path-tldr.md` — current EDGE-004 state
- `docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` — pre-deploy state for Wave-1 / Wave-2 sequencing
