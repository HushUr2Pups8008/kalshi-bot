# PROFIT-PHASE2-001 early-close attestation — TEMPLATE

**Status:** template; copy to `PROFIT-PHASE2-001-early-close-attestation.md` (no `-template` suffix) and fill in at close time.
**Closes:** PROFIT-PHASE2-001 governance shadow-soak per `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5.1.

## Close metadata (FILL IN AT CLOSE TIME)

- **Close timestamp (UTC):** _________________________
- **Soak duration:** _____ days _____ hours (target: active close window)
- **First cycle:** `gc_2026-05-01_190127` (2026-05-01T19:01:27Z)
- **Last cycle in window:** _________________________
- **Soak window:** 2026-05-01T19:01Z → ____________________

## §8.5.1 gate verification

- [ ] **Gate 1: Volume.** GOVERNANCE_DECISION count: _______ (≥ 30 required)
- [ ] **Gate 2: Calendar floor.** Continuous shadow-mode runtime: _____ d _____ h (≥ 7 d required)
- [ ] **Gate 3: Safety counters.** KILL_SWITCH: ___, batch_aborted: ___, VALIDATION_ERROR: ___ (all must be 0)
- [ ] **Gate 4: PARSE_ERROR trailing 72 h.** Count in last 72 h: ___ (must be 0)
- [ ] **Gate 5: Cadence stability.** Max inter-cycle gap: _____ h (must be ≤ 3 h); cadence-deviation > 10 %: ___ events (must be 0)
- [ ] **Gate 6: Manual review.** Decisions reviewed: ___ / ___; reasonable count: ___; reasonable rate: ___ % (≥ 85 % required)
- [ ] **Gate 7: No mid-soak code change OR §8.5.2 policy-equivalence carve-out.** `bash scripts/check_soak_invariant.sh` returned: ___ (0 = clean; non-zero = reconcile each surfaced commit in the §8.5.2 section below)
- [ ] **Gate 8: Attestation written.** This document committed.

## Final tally

| event type | count |
|---|---:|
| total events | ___ |
| GOVERNANCE_CYCLE_START | ___ |
| GOVERNANCE_CYCLE_END | ___ |
| GOVERNANCE_DECISION | ___ |
| GOVERNANCE_DECISION_PARSE_ERROR | ___ |
| GOVERNANCE_VALIDATION_ERROR | 0 (asserted) |
| KILL_SWITCH | 0 (asserted) |
| `batch_aborted=True` | 0 (asserted) |

## §8.5.2 policy-equivalence carve-out attestation (FILL IN FOR EACH COMMIT SURFACED BY GATE 7)

For each behavioural commit gate-7 surfaces, attest that §8.5.2 carve-out applies:

### Commit: ___________________________ (hash, subject)

- **Window-time:** _________________
- **Scope:** _________________________
- **Evidence-coverage analysis:**
  - Affected evidence field(s): _________________
  - Total decisions in soak: _____
  - Decisions in affected slice: _____ (___% of total)
- **Affected-slice manual review verdict:** _________________
- **Carve-out invoked?** [ ] yes [ ] no — if no, gate 7 fails and the early-close path aborts.

(Repeat the block above for each commit gate-7 surfaces. For PROFIT-PHASE2-001 specifically, see `PROFIT-PHASE2-001-early-close-criteria.md` for the canonical 9 commits + invocations.)

## Operator attestation

I, _____________________ (operator), confirm:

1. All close gates above evaluated TRUE.
2. The active close timing matches the operator-approved Phase-2 close target.
3. Gate 7 is either strict-clean or every surfaced commit has a documented §8.5.2 attestation.
4. Wave-1 deploy may begin from this commit per `docs/_archive/governance/post-soak-close-rehearsal-checklist.md`.

Signed (commit author): _____________________
Tag applied: `phase2-soak-closed`
Wave-1 deploy ETA: _____________________

## Cross-links

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — operator runbook
- `docs/_archive/governance/post-soak-close-rehearsal-checklist.md` — Wave-1 deploy plan
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation
- `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5 + §8.5.1
