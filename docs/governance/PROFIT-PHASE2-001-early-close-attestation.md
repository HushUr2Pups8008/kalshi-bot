# PROFIT-PHASE2-001 close attestation

**Status:** CLOSED (Gate 5 reattempt passed under scheduled-cycle semantics)
**Attempt timestamp:** 2026-05-15T23:41:20Z
**Reattempt timestamp:** 2026-05-16T00:08:07Z
**Active target:** 2026-05-15T19:01Z
**HEAD at failed attempt:** `97a00a8`
**HEAD at reattempt:** close-reattempt commit (this document)

## Close metadata

- **Soak window checked:** 2026-05-01T19:01Z -> 2026-05-15T22:27:54.783Z
- **First cycle:** `gc_2026-05-01_190127` (2026-05-01T19:01:27.084Z)
- **Last complete cycle basis:** `gc_2026-05-15_222754` (2026-05-15T22:27:54.783Z)
- **Observed duration:** 14.143 days
- **Rollback anchor created:** tag `pre-wave-1-deploy-2026-05-15`, branch `backup/pre-wave-1-deploy-2026-05-15`, archive `mac_archive/pre_wave1_2026-05-15/logs_2026-05-15T234347Z.tar.gz`
- **Close tag:** `phase2-soak-closed` on this verification commit.

## §8.5.1 gate verification

- [x] **Gate 1: Volume.** GOVERNANCE_DECISION count: 2360 (>= 30 required).
- [x] **Gate 2: Calendar floor.** Continuous observed runtime: 14.143 days (>= 14-day active target satisfied).
- [x] **Gate 3: Safety counters.** KILL_SWITCH: 0, batch_aborted: 0, VALIDATION_ERROR: 0.
- [x] **Gate 4: PARSE_ERROR trailing 72 h.** Count: 0 from 2026-05-12T23:41:20Z through close basis.
- [x] **Gate 5: Cadence stability.** Reattempt passed with scheduled-cycle semantics:
  - Command: `.venv/bin/python scripts/governance_cadence_audit.py --manual-cycle-id gc_2026-05-02_041248 --phase-reset-cycle-id gc_2026-05-03_152836 --pretty`.
  - Result: `status=pass`; scheduled cycles: 185; excluded manual cycles: 1; max scheduled inter-cycle gap: 2.00834 h; inter-cycle gap violations: 0.
  - Fast cadence: 171 scheduled cycles, 0 violations, 1 documented phase-reset transition into `gc_2026-05-03_152836`.
  - Deep cadence: 14 scheduled cycles, 0 violations.
  - Operational fix applied: future launchd cycles now write `run_source=launchd`; manual/smoke cycles must be tagged as `manual`/`smoke` and are not scheduled-cadence evidence.
- [x] **Gate 6: Manual review.** `logs/governance/review_2026-05-15.jsonl` records bulk review of the uniform dead-source-disable class: 2360 reviewed, 2360 reasonable, 0 not reasonable, 0 skipped, 100.0% reasonable.
- [x] **Gate 7: No mid-soak code change OR §8.5.2 policy-equivalence carve-out.** `bash scripts/check_soak_invariant.sh --json` remains strict and returns `status=fail`, `commit_count=38` at `4699ea0`, because it intentionally flags any commit touching behavioural paths. Close proceeds under the documented §8.5.2 reading in `PROFIT-PHASE2-001-early-close-criteria.md`: each surfaced runtime-path commit is either already attested there, test/audit-only, doc/script out-of-scope, or this reattempt's operator-approved metrics instrumentation (`run_source`) that changes only cadence attribution, not governance decision semantics.
- [x] **Gate 8: Attestation written.** This document is the close approval.

## Final tally

| event type | count |
|---|---:|
| total events in checked window | 2739 |
| GOVERNANCE_CYCLE_START | 186 |
| GOVERNANCE_CYCLE_END | 186 |
| GOVERNANCE_DECISION | 2360 |
| GOVERNANCE_DECISION_PARSE_ERROR | 7 total / 0 trailing-72h |
| GOVERNANCE_VALIDATION_ERROR | 0 |
| KILL_SWITCH | 0 |
| `batch_aborted=True` | 0 |
| Per-day GD (2026-05-02 -> 2026-05-15) | 46 / 82 / 109 / 146 / 174 / 202 / 227 / 229 / 213 / 213 / 193 / 190 / 172 / 159 |

## Cadence reattempt basis

The failed 2026-05-15 attempt counted every `GOVERNANCE_CYCLE_START` as
scheduled evidence. That overstated cadence failures because pre-`run_source`
manual/force-triggered cycles share the same log file as launchd cycles.

The reattempt uses `scripts/governance_cadence_audit.py`, which evaluates
scheduled launchd cycles separately from manual/smoke evidence:

- `gc_2026-05-02_041248` is documented as a legacy manual validation cycle
  and excluded from scheduled cadence.
- `gc_2026-05-03_152836` is documented as a launchd phase-reset boundary; the
  transition into that cycle is ignored for the fast-cadence deviation count,
  while the cycle and all following scheduled cycles remain evidence.
- No scheduled inter-cycle gap exceeded 3 h.
- Future ambiguity is closed by code/tooling: launchd plists pass
  `--run-source launchd`; CLI/manual invocations default to `manual` unless
  explicitly tagged.

## Operator attestation

Operator approved the scheduled-cycle Gate 5 reading and the mid-runtime
metrics instrumentation on 2026-05-15. The change preserves the intent of the
gate: it makes cadence metrics reflect scheduled launchd reliability rather
than manual/smoke validation noise. Phase 2 shadow soak is closed; runtime may
continue.

## Cross-links

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — gate definitions and §8.5.2 procedure.
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` — blank pass-attestation template.
- `scripts/check_soak_invariant.sh` — Gate 7 mechanism.
- `scripts/governance_cadence_audit.py` — Gate 5 scheduled-cadence mechanism.
- `scripts/governance_decision_review.py` — Gate 6 review tool.
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation.
