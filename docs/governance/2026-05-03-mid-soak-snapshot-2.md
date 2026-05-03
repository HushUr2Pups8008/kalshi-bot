# Phase 2 governance shadow-soak — snapshot 2 (~4 h soak-hour delta from snapshot 1)

**Generated:** 2026-05-03 (~19:30Z, latest cycle 19:29Z)
**Soak tracker:** `PROFIT-PHASE2-001`
**Source:** `logs/governance/decisions.jsonl`
**Reviewer:** Claude
**Baseline:** `docs/governance/2026-05-03-mid-soak-health-report.md` (snapshot 1; latest cycle 15:28Z, soak elapsed 44.5 h)

> **Time-axis note:** the "delta" throughout this report is the *soak-hour* delta (latest-cycle timestamp difference), not the report-generation wall-clock delta. Snapshot 1 was generated ~13:30Z but its data slice extended through latest-cycle 15:28Z (+5.5 h after wall-clock-of-generation due to the immediate-prior cycle finishing during analysis). Snapshot 2 generated ~19:30Z, latest-cycle 19:29Z. Soak-hour delta is 48.5 − 44.5 = 4.0 h, not the 6 h wall-clock difference.

## TL;DR

Soak healthy. **No drift, no new safety events, parse-error rate stable at 0 % in trailing window.** Continue to 2026-05-15.

## Delta vs snapshot 1

| metric | snapshot 1 (13:30Z) | snapshot 2 (19:30Z) | delta |
|---|---:|---:|---:|
| elapsed soak hours | 44.5 | 48.5 | +4.0 |
| cycle starts | 27 | 29 | +2 |
| cycle ends | 27 | 29 | +2 |
| unmatched starts / orphan ends | 0 / 0 | 0 / 0 | unchanged |
| `GOVERNANCE_DECISION` count | 108 | 118 | +10 |
| distinct targets | 19 | 19 | unchanged |
| distinct actions | 1 (`disable_source`) | 1 (`disable_source`) | unchanged |
| PARSE_ERROR | 7 | 7 | **unchanged — 0 in trailing window** |
| VALIDATION_ERROR | 0 | 0 | unchanged |
| KILL_SWITCH | 0 | 0 | unchanged |
| `batch_aborted=True` | 0 | 0 | unchanged |

## Trailing-window PARSE_ERROR confirmation

All 7 PARSE_ERRORs remain dated `2026-05-01` and `2026-05-02` (early-cycle qwen3 grammar warm-up cluster, per snapshot 1 §5). **Zero PARSE_ERRORs added since snapshot 1.** The transient hypothesis from snapshot 1 holds: parse errors are not regressive.

## §8.5 status (per snapshot 1's analysis, unchanged)

| criterion | status |
|---|---|
| time | IN_PROGRESS (48.5h elapsed, 14.4 % of 14d target) |
| volume | IN_PROGRESS (29 cycles / 118 candidates) |
| cadence_coverage | PASS (27 fast + 2 deep, schedule honoured) |
| candidate_diversity | PASS (19 distinct, monitor still mis-reports as FAIL — see snapshot 1 §1) |
| safety_applied_no_growth | PASS (0 applied, vacuous in shadow mode) |
| safety_kill_switch | PASS (0 events) |
| quality | PASS (reasoning coherence intact; deep-cycle ran 2026-05-03) |

## Latest cycle reference

- `cycle_id`: `gc_2026-05-03_192918`
- `started_at`: 2026-05-03T19:29:18Z
- `duration_sec`: 22.10
- `decisions_proposed`: 5
- `batch_aborted`: False

## Recommendation

No operator action required. Next checkpoint: Codex's day-4 mid-soak report once `2026-05-04` records exist (currently pending per `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation.md`).
