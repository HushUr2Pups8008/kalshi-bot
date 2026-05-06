# PROFIT-PHASE2-001 early-close attestation

**Status:** PRE-STAGE — populated 2026-05-06 cycle 11 from current soak counters; placeholders denote fire-time fields. Operator finalizes at close commit (target 2026-05-08 under §8.5.1).
**Closes:** PROFIT-PHASE2-001 governance shadow-soak per `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5.1.

## Close metadata (FINAL FILL AT CLOSE TIME)

- **Close timestamp (UTC):** `<TBD at fire time>` (target: 2026-05-08T19:01Z early-close, or default 2026-05-15T19:01Z)
- **Soak duration:** `<TBD>` days `<TBD>` hours (target: ≥ 7 d 0 h)
- **First cycle:** `gc_2026-05-01_190127` (2026-05-01T19:01:27Z)
- **Last cycle in window:** `<TBD at fire time>`
- **Soak window:** 2026-05-01T19:01Z → `<TBD>`

## §8.5.1 gate verification

Pre-stage values reflect cumulative state through Day-4 confirmation (2026-05-04T13:10Z, per `2026-05-04-day-4-mid-soak-confirmation.md`). Final figures fill at fire-time.

- [ ] **Gate 1: Volume.** GOVERNANCE_DECISION count: `<TBD>` (Day-4: 158; ≥ 30 required → 5.3× over-clear at Day 4)
- [ ] **Gate 2: Calendar floor.** Continuous shadow-mode runtime: `<TBD>` d `<TBD>` h (Day-4: 62.7 h; ≥ 7 d / 168 h required)
- [ ] **Gate 3: Safety counters.** KILL_SWITCH: `<TBD>`, batch_aborted: `<TBD>`, VALIDATION_ERROR: `<TBD>` (Day-4: 0/0/0; all must remain 0)
- [ ] **Gate 4: PARSE_ERROR trailing 72 h.** Count in last 72 h: `<TBD>` (Day-4 trailing 72 h: 0; must remain 0)
- [ ] **Gate 5: Cadence stability.** Max inter-cycle gap: `<TBD>` h (Day-4: 2.01 h max; must be ≤ 3 h); cadence-deviation > 10 %: `<TBD>` events (Day-4: 0)
- [ ] **Gate 6: Manual review.** Decisions reviewed: `<TBD>` / `<TBD>`; reasonable count: `<TBD>`; reasonable rate: `<TBD>` % (≥ 85 % required; cycle-9 manual review of 67 day-1-to-day-3 decisions returned 100 % reasonable per commit `9f8deef`)
- [ ] **Gate 7: No mid-soak code change OR §8.5.2 policy-equivalence carve-out.** `bash scripts/check_soak_invariant.sh` returned: `<TBD>` (0 = clean). Pre-cycle-10 surfaced 5 commits (all docs/scripts; §8.5.2 carve-out applied per `PROFIT-PHASE2-001-early-close-criteria.md`); §8.5.2 invocation table needs cycle-10/-11 commits appended at fire-time.
- [ ] **Gate 8: Attestation written.** This document committed.

## Final tally (fire-time fill)

| event type | count |
|---|---:|
| total events | `<TBD>` (Day-4: 239) |
| GOVERNANCE_CYCLE_START | `<TBD>` (Day-4: 36) |
| GOVERNANCE_CYCLE_END | `<TBD>` (Day-4: 36) |
| GOVERNANCE_DECISION | `<TBD>` (Day-4: 158) |
| GOVERNANCE_DECISION_PARSE_ERROR | `<TBD>` (Day-4: 7, all in days 1-2) |
| GOVERNANCE_VALIDATION_ERROR | `<TBD>` (Day-4: 0; assert remains 0) |
| KILL_SWITCH | `<TBD>` (Day-4: 0; assert remains 0) |
| `batch_aborted=True` | `<TBD>` (Day-4: 0; assert remains 0) |

## §8.5.2 policy-equivalence carve-out attestation

Per `PROFIT-PHASE2-001-early-close-criteria.md` §"§8.5.2 policy-equivalence carve-outs invoked", the canonical commits surfacing as of 2026-05-05 cycle 5 were:

| commit | window-time | scope | invocation |
|---|---|---|---|
| `fae72fa` | 2026-05-02T04:15Z | `governance/llm.py` think=False fix | INVOKED (effective soak start = post-fix decision time) |
| `b47ca71` | 2026-05-03T15:28Z | A5 SYSTEM_PROMPT addition (anchor_rate) | INVOKED (canonical §8.5.2 example) |
| `b44dda2` | 2026-05-05T~12:30Z | docs/governance/ + docs/superpowers/specs/ ONLY | OUT-OF-SCOPE (doc artifact only) |
| `80932cb` | 2026-05-05T~12:35Z | scripts/ + tests/ ONLY (0 prod-code touch) | OUT-OF-SCOPE (script + test artifacts only) |

**Cycles 6-11 commits to be appended at fire-time.** Operator runs `bash scripts/check_soak_invariant.sh --json` against the close-time SHA, walks any newly-surfaced commits, and assigns each to INVOKED / OUT-OF-SCOPE / GATE-FAIL based on §8.5.2 criteria. Documented in `PROFIT-PHASE2-001-early-close-criteria.md` (canonical reference).

Pre-stage expectation: cycles 6-11 commits all touched docs/, scripts/, or tests/ ONLY (no prod-code in `analysis/` / `tasks/` / `feeds/` / `governance/` / `trading/` / `kalshi/` / `main.py` / `config.py`). Gate-7 should remain clean post-fire-time enumeration; OUT-OF-SCOPE invocation expected for each.

## Operator attestation (FILL AT CLOSE TIME)

I, `<TBD operator>`, confirm:

1. All §8.5.1 gates above evaluated TRUE at close time.
2. The 14-day calendar floor in §8.5 is intentionally relaxed to 7 days per the §8.5.1 addendum, justified by the volume gate clearing 5.3× at Day-4 and the safety counters running clean from day 1.
3. No mid-soak behavioural code change occurred in the running bot. Doc / script / test commits are §8.5.2 OUT-OF-SCOPE.
4. Wave-1 deploy may begin from this commit per `docs/governance/post-soak-close-rehearsal-checklist.md`.

Signed (commit author): `<TBD>`
Tag applied: `phase2-soak-closed`
Wave-1 deploy ETA: `<TBD; nominally same-day as close commit>`

## Fire-time agent instructions

When this attestation file fires (2026-05-07/08 close commit window):

1. Read `logs/governance/decisions.jsonl` for the full soak window (2026-05-01T19:01Z → close time).
2. Populate every `<TBD>` placeholder with live counts.
3. Run `bash scripts/check_soak_invariant.sh --json` against close-time SHA. If non-empty, walk surfaced commits and append §8.5.2 invocation rows above.
4. Manual review (gate 6): per `feedback_soak_confirmation_cadence.md` (cap 1 confirmation per UTC day per agent), use the day-7 mid-soak confirmation's review batch.
5. Operator signs §"Operator attestation" + commits this file in same hunk as the close-day VERSION/CHANGELOG bump.
6. Tag with `phase2-soak-closed`.

## Cross-links

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — operator runbook + §8.5.2 invocation table
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` — original blank template
- `docs/governance/2026-05-04-day-4-mid-soak-confirmation.md` — Day-4 baseline
- `docs/governance/2026-05-07-day-7-pre-soak-confirmation.md` — Day-7 fire-time skeleton
- `docs/governance/post-soak-close-rehearsal-checklist.md` — Wave-1 deploy plan
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation
- `scripts/check_soak_invariant.sh` — gate-7 mechanism
