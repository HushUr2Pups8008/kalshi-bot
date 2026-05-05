# Wave-3 deploy decision flow

**Audience:** operator at Wave-3 fire-time. Quick-look flowchart sibling to `2026-05-05-wave-2-decision-flow.md`.
**Drafted:** 2026-05-05.
**Companion:** `2026-05-05-wave-3-fire-time-per-commit-checklist.md` (linear playbook); `2026-05-05-wave-3-deploy-day-timing.md`; cycle-2 LOCK addenda for Lever B + Lever C.

## The flow

```
                        ┌──────────────────────────────────┐
                        │   START Wave-3                   │
                        │   (≥ 2026-06-17;                 │
                        │    Wave-2 stalled AND            │
                        │    Branch D not yet fired)       │
                        └──────────┬───────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────────────────┐
                        │  Wave-2 Branch C 14-day verdict: │
                        │  0 PAPER_TRADE OR -P&L?          │
                        └──────────┬───────────────────────┘
                          YES      │   NO
                                   │   └─────► Wave-3 NOT NEEDED. EDGE-004 closed
                                   │            via Wave-2.
                                   ▼
                        ┌──────────────────────────────────┐
                        │  Operator decision:              │
                        │  Wave-3 (more attribution)       │
                        │  OR                              │
                        │  Branch D (escalate now)?        │
                        └──────────┬───────────────────────┘
                            Wave-3 │  Branch D
                                   │   └─────► fire Branch D per
                                   │            2026-05-05-branch-d-fire-procedure-runbook.md
                                   ▼
                        ┌──────────────────────────────────┐
                        │  Wave-3 commit 1 (Lever B)       │
                        │  G1=0.04 + failsafe=0.08         │
                        │  + 2× ratio invariant            │
                        │                                  │
                        │  - tasks/trade_readiness_gate.py │
                        │  - VERSION 0.32.0 (or 0.33.0     │
                        │    if option-A landed in Wave-2) │
                        │  (30 min + 24h smoke)            │
                        └──────────┬───────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────────────────┐
                        │  Lever B 14-day window           │
                        │  - G1 SKIPPED count drops ≥30%   │
                        │  - admitted candidates' P&L ≥0   │
                        │  - calibration drift clean       │
                        └──────────┬───────────────────────┘
                                   │
                              ┌────┴──────┐
                              │           │
                           clean       -P&L OR drift
                              │           │
                              ▼           ▼
                              │   ┌────────────────────┐
                              │   │ revert + Branch D  │
                              │   │ (negative P&L =    │
                              │   │  Lever-D §2.2)     │
                              │   └────────────────────┘
                              ▼
                        ┌──────────────────────────────────┐
                        │  Wave-3 commit 2 (Lever C v1)    │
                        │  - §3.2 normalized hash          │
                        │  - 3600s default window          │
                        │  - record-after-gate-pass        │
                        │  - INV-6 boundary attested       │
                        │                                  │
                        │  - config.py + tasks/blend_task  │
                        │  - VERSION 0.33.0 (or 0.34.0)    │
                        │  (30 min + 24h smoke)            │
                        └──────────┬───────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────────────────┐
                        │  Lever C 14-day window           │
                        │  - cross-series suppression > 0  │
                        │  - no false-positive trade       │
                        │    suppression                   │
                        │  - calibration unchanged         │
                        └──────────┬───────────────────────┘
                                   │
                              ┌────┴──────┐
                              │           │
                       PAPER_TRADE     stall
                       lift achieved?     │
                              │           ▼
                              ▼      ┌────────────────────┐
                      ┌────────────┐ │ Branch D fires     │
                      │ EDGE-004   │ │ (intake + Wave-3   │
                      │ closes via │ │  exhausted)        │
                      │ Wave-3     │ └────────────────────┘
                      │            │
                      │ STOP.      │
                      └────────────┘
```

## Quick-reference timing

| stage | wall-clock | tool |
|---|---|---|
| Operator Wave-3-vs-Branch-D decision | 5 min | per `2026-05-05-wave-3-deploy-day-timing.md` |
| Lever B deploy + smoke | 30 min + 24h watch | per-commit checklist |
| Lever B 14-day acceptance | 14 d | trade-log + CALIBRATION_CHECK |
| Lever C deploy + smoke | 30 min + 24h watch | per-commit checklist |
| Lever C 14-day acceptance | 14 d | trade-log + suppression-rate |
| **Wave-3 total** | **30+ d** | from Lever B fire to Lever C window-end |

## Decision points at fire-time

1. **Wave-3 vs Branch D.** At Wave-2 Branch C stall verdict (~2026-06-16): operator chooses. Recommended: Wave-3 first; Branch D after Wave-3 stalls. Wave-3 produces attribution data; Branch D produces architectural-replan data.
2. **Lever B revert vs Wave-3-stall declaration.** If Lever B 14-day window shows -P&L: revert is required (per Lever B parent §6 acceptance). After revert: Branch D fires per Lever-D §2.2; do NOT proceed to Lever C deploy with Lever B reverted.
3. **Lever B-2 (0.03 floor) follow-up.** If Lever B 0.04 lands cleanly: pre-staged stub `2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` may activate. Out of Wave-3 scope; Wave-3.5 or Wave-4 territory.

## Special timing notes

### Independence Day 2026-07-03/04 falls inside Wave-3 commit-2 window

Lever C commit fires ≥ 2026-07-01 (14d after Lever B). If commit-2 lands 2026-07-01 (Mon) or 2026-07-02 (Tue): post-deploy 24h smoke spans 2026-07-02 to 2026-07-03, partially overlapping the Independence Day low-volume window. Recommended: hit 2026-07-01 (Mon) for the deploy + 2026-07-02 (Tue) full-day post-deploy smoke evidence; OR delay to 2026-07-07 (Mon) if operator wants weekend-clear.

### option-A landed in Wave-2 changes VERSION sequence

If Wave-2 deployed both Branch C (v0.31.0) AND option-A (v0.32.0), Wave-3 commits become v0.33.0 + v0.34.0. Update CHANGELOG pre-staged blocks accordingly.

## What does NOT happen in Wave-3

- **No new edge-production lever lands.** Lever B is attribution + 1-2 trades / 14d predicted lift. Lever C is suppression-only. If Wave-2 didn't produce edge, Wave-3 is the empirical-evidence-collector before Branch D fires.
- **No fast-revert to Wave-1 base.** Wave-3 commits are independent of Wave-1 lattice; Wave-1 commits stay deployed regardless of Wave-3 verdict.

## Cross-links

- `2026-05-05-wave-3-fire-time-per-commit-checklist.md` — linear playbook
- `2026-05-05-wave-3-deploy-day-timing.md` — UTC timing rationale
- `specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` — Lever B 0.04 LOCK
- `specs/2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` — Lever B-2 stub (post-success)
- `specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` — Lever C v1 LOCK
- `2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D triggers (post-Wave-3-stall)
- `2026-05-05-branch-d-fire-procedure-runbook.md` — Branch D fire procedure
- `2026-05-05-wave-2-decision-flow.md` — Wave-2 sibling flowchart
