# Wave-2 deploy decision flow

**Audience:** operator at Wave-2 fire-time. Quick-look flowchart sibling to `PROFIT-PHASE2-001-close-day-decision-flow.md`.
**Drafted:** 2026-05-05.
**Companion:** `2026-05-05-wave-2-fire-time-per-commit-checklist.md` (linear playbook); `2026-05-05-wave-2-a1plus-branch-decision-table.md` (decision criteria); `wave-2-deploy-commit-order-decision.md` (locked order).

## The flow

```
                            ┌─────────────────────────────┐
                            │   START Wave-2              │
                            │   (≥ 2026-05-18; Wave-1 +   │
                            │    48h burn-in clean)       │
                            └──────────┬──────────────────┘
                                       │
                                       ▼
                          ┌────────────────────────────────┐
                          │  Wave-1 commit 6 +             │
                          │  48h watch all rows clean?     │
                          └─────────┬──────────────────────┘
                            YES     │   NO
                                    │   └────► STOP. Wave-1 not yet stable.
                                    │          Re-evaluate Wave-2 trigger.
                                    ▼
                          ┌────────────────────────────────┐
                          │  Step 1: Branch A start        │
                          │  - tag a1plus-branch-a-start-  │
                          │    ${UTC_DATE}                 │
                          │  - debt-log entry              │
                          │  (NO code change; 5 min)       │
                          └─────────┬──────────────────────┘
                                    │
                                    ▼
                          ┌────────────────────────────────┐
                          │  14-day passive observation    │
                          │  window: legal-niche           │
                          │  PAPER_TRADE watch             │
                          └─────────┬──────────────────────┘
                                    │
                              ┌─────┴──────┐
                              │            │
                          ≥ 1 PAPER_TRADE  0 PAPER_TRADE
                              │            │
                              ▼            ▼
                      ┌────────────┐  ┌────────────────────────────────┐
                      │ EDGE-004   │  │  Step 2: Branch C deploy       │
                      │ closes via │  │  (legal-analyst onboard)       │
                      │ Branch A   │  │                                │
                      │            │  │  - Just Security primary       │
                      │ STOP.      │  │  - Lawfare secondary           │
                      └────────────┘  │  - VERSION 0.31.0              │
                                      │  (30 min commit + 24h smoke)   │
                                      └─────────┬──────────────────────┘
                                                │
                                                ▼
                                      ┌─────────────────────────────┐
                                      │  Branch C 14-day window     │
                                      │  + per-lane attribution     │
                                      └─────────┬───────────────────┘
                                                │
                                          ┌─────┴──────┐
                                          │            │
                                     ≥ 1 PAPER_TRADE  0 OR -P&L
                                     w/ +P&L          │
                                          │            ▼
                                          ▼            ┌─────────────────────┐
                                  ┌────────────┐       │  Branch D fire      │
                                  │ EDGE-004   │       │  per Lever-D §2     │
                                  │ closes via │       │                     │
                                  │ Branch C   │       │  - debt-log         │
                                  │            │       │  - tag fire         │
                                  │ STOP.      │       │  - TLDR v4          │
                                  └────────────┘       │  - PROFIT-LLM-001   │
                                                       │    sizing audit     │
                                                       └─────────────────────┘
```

## Quick-reference timing

| stage | wall-clock | tool |
|---|---|---|
| Branch A start | 5 min | `git tag` + debt-log |
| Branch A passive observe | 14 d | trade-log inspection |
| Branch C deploy + smoke | 30 min + 24h watch | per-commit checklist |
| Branch C 14-day acceptance | 14 d | trade-log + CALIBRATION_CHECK |
| Branch D handoff (if fires) | ~30 min initial; ~5 d sizing audit | per Branch-D fire procedure runbook |

**Total Wave-2 wall-clock to a Branch A → C verdict:** 14 + 14 = 28 days. Branch D handoff fires at end if both stalled.

## Decision points at fire-time

The operator's only real decisions during Wave-2 are:

1. **Branch A start trigger.** "Is Wave-1 stable enough to start observing?" — Wave-1 commit 6 + 48h watch all clean per `2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md`.
2. **Branch C feed selection.** Per `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md`: Just Security primary + Lawfare secondary recommended. Operator confirms at fire-time after RSS-probe.
3. **Branch D fire vs Wave-3 deploy.** When Branch C 14-day window concludes with stall: operator chooses Wave-3 (more attribution data) OR Branch D (escalate now). See `2026-05-05-wave-3-decision-flow.md` (this cycle) for the Wave-3 trigger conditions.

Everything else is mechanical execution.

## option-A parallel-discretion variant

The Wave-2 branch decision table allows operator-discretion `option-A` (geopolitics specialist feed) deploy in parallel with Branch A, OR as a fallback after Branch C:

```
Parallel:  Branch A (Day-14) ────┬──── 14d ──── Branch A verdict
                                 │
                                 └─ option-A deploy ── 14d ── option-A verdict
                                    (compresses 28d walk to ~14d)

Fallback:  Branch A → Branch C → option-A → Branch D (full walk; 42-56d)
```

Recommended: **serial walk** (no parallelism). Attribution is cleaner.

## Cross-links

- `2026-05-05-wave-2-fire-time-per-commit-checklist.md` — linear playbook
- `2026-05-05-wave-2-a1plus-branch-decision-table.md` — decision criteria
- `2026-05-05-wave-2-deploy-day-timing.md` — UTC timing windows
- `2026-05-05-wave-3-decision-flow.md` — Wave-3 flowchart (sibling)
- `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` — feed selection
- `2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D triggers
- `wave-2-deploy-commit-order-decision.md` — locked commit order
