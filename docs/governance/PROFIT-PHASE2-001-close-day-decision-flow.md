# PROFIT-PHASE2-001 close-day decision flow

**Audience:** operator on close day; quick visual reference.
**Drafted:** 2026-05-05.
**Companion:** `PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` (the linear playbook); this doc is the quick-look flowchart for "where am I in the close sequence?"

## The flow

```
                                    ┌─────────────────────┐
                                    │   START close-day   │
                                    │   (>= 2026-05-08    │
                                    │      19:01Z)        │
                                    └──────────┬──────────┘
                                               │
                                               ▼
                              ┌─────────────────────────────────┐
                              │  Pre-flight                     │
                              │  - git pull origin main         │
                              │  - confirm bot HEAD on Studio   │
                              │  - re-read criteria runbook     │
                              └──────────┬──────────────────────┘
                                         │
                                         ▼
                                ┌──────────────────┐
                                │  Gate 1: volume  │
                                │  >= 30 decisions │
                                └─────────┬────────┘
                                  PASS    │  FAIL
                                          │   └──────► (rare; investigate; fall to default close)
                                          ▼
                                ┌──────────────────────┐
                                │  Gate 2: calendar    │
                                │  >= 7 d since start  │
                                └─────────┬────────────┘
                                  PASS    │  FAIL
                                          │   └──────► (reschedule; wait until threshold)
                                          ▼
                              ┌────────────────────────────┐
                              │  Gate 3: safety counters   │
                              │  KILL_SWITCH = 0 AND       │
                              │  batch_aborted = 0 AND     │
                              │  VALIDATION_ERROR = 0      │
                              └─────────┬──────────────────┘
                                  PASS  │  FAIL
                                        │   └─────► STOP. Investigate. ABORT close.
                                        ▼
                              ┌──────────────────────────┐
                              │  Gate 4: PARSE_ERROR     │
                              │  in trailing 72 h = 0    │
                              └─────────┬────────────────┘
                                  PASS  │  FAIL
                                        │   └─────► (rare; investigate)
                                        ▼
                              ┌──────────────────────────┐
                              │  Gate 5: cadence stable  │
                              │  max gap <= 3 h          │
                              └─────────┬────────────────┘
                                  PASS  │  FAIL
                                        │   └─────► (investigate launchd; reschedule)
                                        ▼
                          ┌──────────────────────────────────┐
                          │  Gate 6: operator manual review  │
                          │  >= 85% reasonable               │
                          │  (~30-50 min; tool: gate-6 cli)  │
                          └─────────┬────────────────────────┘
                                    │  PASS              FAIL
                                    │                    └─────► spec-level review;
                                    ▼                            operator decision
                          ┌──────────────────────────────────┐
                          │  Gate 7: soak invariant          │
                          │  check_soak_invariant.sh         │
                          └─────────┬────────────────────────┘
                                    │
                              ┌─────┴──────┐
                              │            │
                          clean         FAIL
                              │            │
                              │            ▼
                              │   ┌────────────────────────────────┐
                              │   │ All surfaced commits in §8.5.2 │
                              │   │ invocation table?              │
                              │   │ (4 expected; criteria-runbook) │
                              │   └────────┬───────────────────────┘
                              │       YES  │  NO
                              │            │   │
                              │            │   └─► STOP. Either:
                              │            │       (a) write fresh §8.5.2 evidence-coverage
                              │            │           analysis for the new commit, OR
                              │            │       (b) fall through to default 14-d close.
                              │            ▼
                              │   ┌──────────────────────────────┐
                              │   │ §8.5.2 carve-out INVOKED for │
                              │   │ all surfaced commits.         │
                              │   │ Gate 7 passes under §8.5.2.   │
                              │   └──────────────┬────────────────┘
                              │                  │
                              ▼                  ▼
                          ┌────────────────────────────────────────┐
                          │  Step 8: pre_soak_close_branch_backup  │
                          │  - tag pre-wave-1-deploy-${DATE}       │
                          │  - branch backup/pre-wave-1-deploy-... │
                          │  - logs tarball in mac_archive/        │
                          └─────────────────┬──────────────────────┘
                                            ▼
                          ┌────────────────────────────────────────┐
                          │  Step 9: fill close attestation        │
                          │  - copy template -> filled doc         │
                          │  - tick all 8 gates                    │
                          │  - paste §8.5.2 invocation table       │
                          │  - operator signs                      │
                          └─────────────────┬──────────────────────┘
                                            ▼
                          ┌────────────────────────────────────────┐
                          │  Step 10: commit + tag                 │
                          │  - git commit (attestation)            │
                          │  - git tag phase2-soak-closed          │
                          │  - git push origin main --tags         │
                          └─────────────────┬──────────────────────┘
                                            ▼
                                  ┌──────────────────┐
                                  │  CLOSE COMPLETE  │
                                  │  Wave-1 deploy   │
                                  │  may begin       │
                                  └──────────────────┘
```

## Quick-reference timing

| stage | wall-clock | tool |
|---|---|---|
| Pre-flight | ~5 min | `git pull` + read |
| Gates 1-5 | ~5 min total | shell one-liners (in walkthrough §1-§5) |
| Gate 6 (manual review) | **30-50 min** (the big one) | `scripts/governance_decision_review.py` |
| Gate 7 + §8.5.2 carve-out | ~5 min | `scripts/check_soak_invariant.sh` + criteria-runbook table lookup |
| Steps 8-10 | ~10 min | `scripts/pre_soak_close_branch_backup.sh` + manual attestation + tag |
| **TOTAL** | **~60-90 min** | |

## Decision points at close-day

The operator's only real decisions are:

1. **Gate 6 verdict** — y/n on each decision under review. Operator-only per spec §8.5; no agent assistance.
2. **§8.5.2 fresh-commit case** (rare) — if the gate-7 script surfaces a commit NOT in the canonical §8.5.2 invocation table (i.e., a behavioural commit landed AFTER 2026-05-05 that we didn't pre-document), operator must either:
   - Write a fresh §8.5.2 evidence-coverage analysis (~30-60 min ad-hoc; affects-slice analysis on the new commit's evidence dependencies), OR
   - Fall through to default 14-day close (no § analysis needed).

Everything else is mechanical execution.

## Cross-links

- Linear playbook: `PROFIT-PHASE2-001-day-7-close-day-walkthrough.md`
- Gate criteria: `PROFIT-PHASE2-001-early-close-criteria.md`
- Attestation template: `PROFIT-PHASE2-001-early-close-attestation-template.md`
- Spec: `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5 + §8.5.1 + §8.5.2
