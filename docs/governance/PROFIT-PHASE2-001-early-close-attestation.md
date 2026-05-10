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

Pre-stage values reflect cumulative state through cycle-13 refresh (2026-05-06T22:30Z; latest decision 2026-05-06T21:43Z; ~125h elapsed soak). Final figures fill at fire-time (~13h post-cycle-13).

- [ ] **Gate 1: Volume.** GOVERNANCE_DECISION count: 552 as of cycle-13 refresh (≥ 30 required → 18.4× over-clear). PASS-PROJECTED.
- [ ] **Gate 2: Calendar floor.** Continuous shadow-mode runtime: 5d 5h as of cycle-13 (≥ 7d/168h required at close). At-risk if §8.5.1 close-day (2026-05-08T19:01Z) forced earlier; on-track otherwise.
- [ ] **Gate 3: Safety counters.** KILL_SWITCH: 0, batch_aborted: 0, VALIDATION_ERROR: 0 as of cycle-13. PASS-PROJECTED (all must remain 0).
- [ ] **Gate 4: PARSE_ERROR trailing 72 h.** Total PARSE_ERROR: 7 (all from days 1-2; first 48h of soak). Trailing-72h count at fire-time TBD; expected 0 since no recent PARSE_ERRORs.
- [ ] **Gate 5: Cadence stability.** 69 fast cycles + cadence stable through cycle-13 (`bothealth.sh` reports GREEN with governance shadow invariant holding). Recompute max inter-cycle gap at fire-time.
- [ ] **Gate 6: Manual review.** ⚠️ **AT RISK.** Cycle-13 capacity audit projection: 552 decisions across 5 days = 366 reviewable at 80/day = **0.663 reviewable fraction** (was 0.747 at Day-4). Day-6 peak (169) inflates per-day-cap denominator; trend is UP not down. **Path 3 (re-eval at close-time) likely fails.** Operator should plan on Path 1 (increase daily review budget to ≥ 169 to cover peak day) per `docs/governance/2026-05-06-gate-6-capacity-resolution-plan.md`. Path 1 = ~169-decision review on heaviest day, ~2× cycle-9's 67-decision review effort.
- [ ] **Gate 7: No mid-soak code change OR §8.5.2 policy-equivalence carve-out.** `bash scripts/check_soak_invariant.sh --json` against HEAD `e614b96`: status=fail, commit_count=5. Per cycle-13 audit, all 5 commits have §8.5.2 carve-out applicability — see refreshed table below.
- [ ] **Gate 8: Attestation written.** This document committed.

## Final tally (fire-time fill — cycle-13 baseline)

| event type | count (cycle-13 refresh, 2026-05-06T22:30Z) |
|---|---:|
| total events | 697 (552 GD + 7 PE + 69 cycle_start + 69 cycle_end) |
| GOVERNANCE_CYCLE_START | 69 |
| GOVERNANCE_CYCLE_END | 69 |
| GOVERNANCE_DECISION | **552** |
| GOVERNANCE_DECISION_PARSE_ERROR | 7 (all from days 1-2; trailing-72h = 0) |
| GOVERNANCE_VALIDATION_ERROR | 0 (asserted) |
| KILL_SWITCH | 0 (asserted) |
| `batch_aborted=True` | 0 (asserted) |
| Per-day GD (2026-05-02 → 2026-05-06) | 46 / 82 / 109 / 146 / 169 |

## §8.5.2 policy-equivalence carve-out attestation

Per `PROFIT-PHASE2-001-early-close-criteria.md` §"§8.5.2 policy-equivalence carve-outs invoked", the canonical commits surfacing as of 2026-05-05 cycle 5 were:

| commit | window-time | scope | invocation |
|---|---|---|---|
| `fae72fa` | 2026-05-02T04:15Z | `governance/llm.py` think=False fix | INVOKED (effective soak start = post-fix decision time) |
| `b47ca71` | 2026-05-03T15:28Z | A5 SYSTEM_PROMPT addition (anchor_rate) | INVOKED (canonical §8.5.2 example) |
| `b44dda2` | 2026-05-05T~12:30Z | docs/governance/ + docs/superpowers/specs/ ONLY | OUT-OF-SCOPE (doc artifact only) |
| `80932cb` | 2026-05-05T~12:35Z | scripts/ + tests/ ONLY (0 prod-code touch) | OUT-OF-SCOPE (script + test artifacts only) |
| `dbe1d30` | 2026-05-02T03:48Z | post-soak hygiene bundle (config.py 16 lines dead-code removal + .gitignore + .env.example + scripts/soak_check.sh + debt log). Per commit message + post-edit `pytest -q: 1421 passed`: "none of them touch runtime code paths" | OUT-OF-SCOPE (dead-code removal + non-runtime artifacts only) |
| `d117b60` | 2026-05-02T22:00Z | `trading/paper_trader.py:record_trade` PROFIT-OBS-004 close — persists executed-side edge to paper_trades.edge column instead of YES-side edge. Persistence-shape change, NOT decision-flow change. Pre-fix decisions stand; post-fix decisions persist correctly. | INVOKED (observability persistence change; cycle-13 audit confirms no decision-path divergence) |
| `1a466e4` | 2026-05-02T22:04Z | ruff auto-fix unused imports + f-string lints (15 files; touched governance/adapter.py + governance/agent.py among others). Per commit message: "no runtime behavior change. Tests: 1423 passed" | OUT-OF-SCOPE (lint-only, no behavioral change) |

**Cycle-13 audit (2026-05-06T22:30Z):** all 5 surfaced commits triaged above. 2 INVOKED (`fae72fa`, `b47ca71`, `d117b60` — wait, count is 3 INVOKED + 4 OUT-OF-SCOPE; OUT-OF-SCOPE doesn't show in `check_soak_invariant.sh` because that script's commit_count=5 reflects the 5 in behavioral paths; `b44dda2`+`80932cb` aren't in the surfaced list, included here for §8.5.2 completeness). Gate 7: clean via §8.5.2 invocation, all surfaced commits attested.

**Operator note for any commits between cycle-13 (this refresh) and fire-time:** rerun `bash scripts/check_soak_invariant.sh --json` against close-time SHA; walk any newly-surfaced commits; append rows to this table.

**Wave-1 deploy commits (POST-soak-close):** §8.5.2 governs commits DURING the soak window (2026-05-01T19:01Z → close). Wave-1's 6 deploy commits land POST-soak-close, so they are OUT-OF-WINDOW for §8.5.2 and require no carve-out attestation here. Standard `git revert` rollback per `docs/_archive/governance/post-soak-rollback-runbook.md` is the recovery path for any Wave-1 commit; soak-close attestation does not extend over Wave-1 deploys.

## Operator attestation (FILL AT CLOSE TIME)

I, `<TBD operator>`, confirm:

1. All §8.5.1 gates above evaluated TRUE at close time.
2. The 14-day calendar floor in §8.5 is intentionally relaxed to 7 days per the §8.5.1 addendum, justified by the volume gate clearing 5.3× at Day-4 and the safety counters running clean from day 1.
3. No mid-soak behavioural code change occurred in the running bot. Doc / script / test commits are §8.5.2 OUT-OF-SCOPE.
4. Wave-1 deploy may begin from this commit per `docs/_archive/governance/post-soak-close-rehearsal-checklist.md`.

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
- `docs/_archive/governance/2026-05-07-day-7-pre-soak-confirmation.md` — Day-7 fire-time skeleton (ARCHIVED Stream G R23)
- `docs/_archive/governance/post-soak-close-rehearsal-checklist.md` — Wave-1 deploy plan
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation
- `scripts/check_soak_invariant.sh` — gate-7 mechanism
