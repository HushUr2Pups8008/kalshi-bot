# EDGE-004 closure-path TL;DR — v3 (2026-05-05 refresh)

**Status:** operator-facing one-pager. Supersedes v2.2.
**Drafted:** 2026-05-05.
**Why v3:** v2.2 predated this cycle's locks: Lever B 0.04 floor (`2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md`); Lever C v1 §3.2 hash + INV-6 boundary (`2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md`); Lever D escalation criteria (`2026-05-05-edge-004-lever-d-escalation-criteria-design.md`); Wave-2 A.1+ 3-branch decision table (`2026-05-05-wave-2-a1plus-branch-decision-table.md`).

## What changed v2.2 → v3

1. **Lever B locked** at `G1_CONFIDENCE_THRESHOLD = 0.04` / `G1_FAILSAFE = 0.08` / 2× ratio invariant. Harness pre-load deferral lifted; Codex authoring `tests/test_lever_b_g1_floor_lock.py` per addendum.
2. **Lever C v1 locked.** §3.2 normalized-string hash, 3600 s default window, BlendTask gate placement (record hash AFTER readiness gate pass, not at entry). INV-6 boundary attested (Lever C is suppression-only; INV not violated).
3. **Lever D escalation criteria spec'd.** Branch D triggers: 2-branch stall (A+C 0 PAPER_TRADE) OR negative realized P&L on Branch C admitted candidates OR operator §2.3 override. Branch D is **handoff** to PROFIT-LLM-001 / P4-GATE Appendix A, not a new lever.
4. **Lever D nomenclature ambiguity surfaced and resolved.** Lever-menu §3 "Lever D" (pre-LLM gate re-enablement, closed) ≠ closure-path "Branch D" (escalation). Both retained; nomenclature distinguished.
5. **Wave-2 A.1+ 3-branch decision table authored.** Operator decision input for Day-14 deploy: Branch A (passive observe; recommended FIRST), Branch C (open-RSS legal-analyst; recommended SECOND), option-A (geopolitics specialist; recommended LAST or parallel).
6. **Day-7 close infrastructure complete.** `wave-1-post-deploy-observation-plan.md` + `2026-05-05-rollback-runbook-validation.md` + `PROFIT-PHASE2-002-onboarding.md` + Codex's smoke + distribution-v2 scripts all landed.

## Current closure path (post-2026-05-05 locks)

```
Wave-1 close (2026-05-08+)
    │
    ▼
A.1 deploy (silent; prerequisite hygiene; no archive lift expected)
    │
    ▼
A.1+ deploy (Wave-2; Day-14; first edge-production attempt)
    │
    ├── Branch A (passive observe, 14 d) ─────► EDGE-004 closes if ≥ 1 legal-niche PAPER_TRADE
    │       │
    │       └── 0 legal-niche PAPER_TRADE in 14 d
    │              ▼
    │           Branch C (open-RSS legal-analyst, deploy + 14 d) ─► EDGE-004 closes if ≥ 1 PAPER_TRADE w/ +P&L
    │                   │
    │                   └── 0 PAPER_TRADE OR -P&L
    │                          ▼
    │                       Branch D fires per Lever-D spec §2
    │                          │
    │                          ▼
    │                       PROFIT-LLM-001 sizing → P4-GATE Appendix A sizing
    │                          │
    │                          ▼
    │                       EDGE-004 closes DEFERRED-CEILING (or pivots)
    │
    └── option-A (parallel geopolitics specialist) ─► sub-linear; tertiary lift
```

Wave-3 levers (B + C) deploy IF Wave-2 stalls AND Branch D not yet fired:
- **Lever B G1=0.04** (≥ 2026-06-06): attribution lever; predicted 1-2 trades / 14 d lift; not edge-production.
- **Lever C cross-series headline** (≥ 2026-06-20): risk-control; suppression-only; lifts no trades.

## Lever map at a glance (v3)

| lever | role | locked? | deploy timing |
|---|---|---|---|
| A.1 | prerequisite hygiene | ✅ classifier patch ready | 2026-05-08+ (Wave-1 commit 6) |
| A.1+ Branch A | passive observe | ✅ no code change | 2026-05-15+ (Day-14 default) |
| A.1+ Branch C | open-RSS legal-analyst | ⏳ feed selection per §A.1+1.5 selection-rubric | 2026-05-29+ if Branch A fails |
| A.1+ option-A | geopolitics specialist | ✅ URL list locked per parent spec §3.1 | parallel-discretion or fallback |
| **Lever B G1=0.04** | attribution; calibration | ✅ floor + failsafe + ratio LOCKED 2026-05-05 | 2026-06-06+ if A+C stall |
| **Lever C cross-series** | risk-control | ✅ v1 §3.2 hash + 3600 s + placement LOCKED 2026-05-05 | 2026-06-20+ if A+B stall |
| **Branch D escalation** | handoff | ✅ triggers spec'd 2026-05-05 | fires when §2 triggers met |
| Lever D (lever-menu §3) | pre-LLM gate (closed) | ✅ closed 2026-05-03 | n/a |
| Lever E (multi-source) | closed | ✅ closed 2026-05-03 | n/a |
| Lever F (P4-GATE App A) | out of EDGE-004 scope | ROADMAP-tracked | post-Branch-D |

## Sequencing-history (7 revisions)

1. Original draft: A → D → B → E → C
2. Post-Lever-D audit: A → B → E → C → D (D demoted)
3. Post-Lever-E audit: A → B → C → D (E closed)
4. Post-Lever-A.1 archive replay: A.1 (prerequisite) → A.1+ → B → C → D
5. Post-per-source audit (2026-05-04): A.1 → A.1+ {option-A | option-B} → B → C → D
6. Post-Codex-direct-RSS-probe (2026-05-05): A.1 → A.1+ {Branch A | Branch C | option-A} → B → C → D (Branch B dropped)
7. **Post-2026-05-05 lock cycle:** A.1 → A.1+ {A | C | option-A} → B (locked 0.04) → C (v1 locked) → Branch D (handoff)

## What "closure" looks like (v3)

EDGE-004 closes OPEN → COMPLETE when:

1. One A.1+ branch (A or C, with option-A as parallel discretion) produces ≥ 5 % conversion lift over 14 d AND
2. Aggregate realized P&L on the new admitted PAPER_TRADE candidates is non-negative AND
3. Per-lane attribution (post-OBS-003 SKIPPED stream) confirms the closure is driven by the new feed, not background noise.

EDGE-004 closes OPEN → DEFERRED-CEILING when Branch D fires AND PROFIT-LLM-001 + P4-GATE Appendix A sizing both return inadequate. **Real possibility, not a fail-safe abstraction.**

## Honest read (v3)

The closure path now hangs on TWO empirical questions, asked in order:

1. **Does Branch A (passive Google News observe) surface VitalLaw-equivalent legal-niche PAPER_TRADE in 14 d?** If yes: closure on the cheapest path; no new code. If no:
2. **Does Branch C (open-RSS legal-analyst onboard) admit PAPER_TRADE with positive P&L?** If yes: closure on minimal-effort code change. If no: Branch D fires; intake-side experimentation exhausted.

**Probability ranking (refreshed 2026-05-05):** Branch A succeeds (~30 %) > Branch C succeeds (~40 % conditional on A failing) > Branch D fires and PROFIT-LLM-001 lands within 30 d (~20 %) > Branch D fires and EDGE-004 closes DEFERRED-CEILING (~10 %).

Probability of intake-side closure: ~30 % + (1 − 0.30) × 0.40 = **~58 %**. Reasonable but not high.

## Cross-links

- `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` — full lever menu (post-update at task 8)
- `docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` — A.1+ option-A spec
- `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` — A.1+1.5 option-B/Branch-C spec
- `docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md` — Lever B parent
- `docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` — **Lever B 0.04 LOCK**
- `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` — Lever C parent
- `docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` — **Lever C v1 LOCK**
- `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — **Branch D triggers**
- `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — Wave-2 operator decision input
- `docs/governance/wave-1-post-deploy-observation-plan.md` — 14-row regression watch
- `docs/governance/PROFIT-PHASE2-002-onboarding.md` — next-soak setup
- `docs/governance/2026-05-05-rollback-runbook-validation.md` — runbook drift findings (G1 env-revert fictional)
