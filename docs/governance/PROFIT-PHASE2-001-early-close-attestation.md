# PROFIT-PHASE2-001 close attestation

**Status:** CLOSE ATTEMPT FAILED (Gate 5 cadence stability)
**Attempt timestamp:** 2026-05-15T23:41:20Z
**Active target:** 2026-05-15T19:01Z
**HEAD at attempt:** `97a00a8`

## Close metadata

- **Soak window checked:** 2026-05-01T19:01Z -> 2026-05-15T22:27:54.783Z
- **First cycle:** `gc_2026-05-01_190127` (2026-05-01T19:01:27.084Z)
- **Last complete cycle basis:** `gc_2026-05-15_222754` (2026-05-15T22:27:54.783Z)
- **Observed duration:** 14.143 days
- **Rollback anchor created:** tag `pre-wave-1-deploy-2026-05-15`, branch `backup/pre-wave-1-deploy-2026-05-15`, archive `mac_archive/pre_wave1_2026-05-15/logs_2026-05-15T234347Z.tar.gz`
- **Close tag:** NOT APPLIED (`phase2-soak-closed` must not be created until all gates pass)

## §8.5.1 gate verification

- [x] **Gate 1: Volume.** GOVERNANCE_DECISION count: 2355 (>= 30 required).
- [x] **Gate 2: Calendar floor.** Continuous observed runtime: 14.143 days (>= 14-day active target satisfied).
- [x] **Gate 3: Safety counters.** KILL_SWITCH: 0, batch_aborted: 0, VALIDATION_ERROR: 0.
- [x] **Gate 4: PARSE_ERROR trailing 72 h.** Count: 0 from 2026-05-12T22:27:54.783Z through close basis.
- [ ] **Gate 5: Cadence stability.** FAILED. No inter-cycle gap exceeded 3 h, and deep cadence had 0 deviations, but three fast-cycle gaps exceeded the +/-10% tolerance:
  - 2026-05-02T03:01:40.945649Z -> 2026-05-02T04:12:48.398593Z: 1.185 h, 40.7% deviation.
  - 2026-05-02T04:12:48.398593Z -> 2026-05-02T05:01:44.627068Z: 0.816 h, 59.2% deviation.
  - 2026-05-03T15:07:04.972374Z -> 2026-05-03T15:28:36.183310Z: 0.359 h, 82.1% deviation.
- [x] **Gate 6: Manual review.** `logs/governance/review_2026-05-15.jsonl` records bulk review of the uniform dead-source-disable class: 2360 reviewed, 2360 reasonable, 0 not reasonable, 0 skipped, 100.0% reasonable.
- [ ] **Gate 7: No mid-soak code change OR §8.5.2 policy-equivalence carve-out.** NOT COMPLETED because Gate 5 already blocks close. `bash scripts/check_soak_invariant.sh --json` at `97a00a8` returned `status=fail`, `commit_count=38`; a future close needs a complete §8.5.2 table for all surfaced commits unless the stable-window reset supersedes the original window.
- [x] **Gate 8: Attestation written.** This failed-close attestation is recorded; it is not a close approval.

## Final tally

| event type | count |
|---|---:|
| total events in checked window | 2734 |
| GOVERNANCE_CYCLE_START | 186 |
| GOVERNANCE_CYCLE_END | 186 |
| GOVERNANCE_DECISION | 2355 |
| GOVERNANCE_DECISION_PARSE_ERROR | 7 total / 0 trailing-72h |
| GOVERNANCE_VALIDATION_ERROR | 0 |
| KILL_SWITCH | 0 |
| `batch_aborted=True` | 0 |
| Per-day GD (2026-05-02 -> 2026-05-15) | 46 / 82 / 109 / 146 / 174 / 202 / 227 / 229 / 213 / 213 / 193 / 190 / 172 / 159 |

## Stable-window reset candidate

The last Gate 5 cadence deviation occurred at 2026-05-03T15:28:36.183Z.
From that point through this attempt:

- fast cycles: 148, deviations over +/-10%: 0.
- deep cycles: 12, deviations over +/-10%: 0.
- decisions: 2257.
- parse errors: 0.
- safety counters: 0 KILL_SWITCH / 0 VALIDATION_ERROR / 0 batch_aborted.

If the operator requires a clean 14-day effective window with no cadence carve-out, the earliest re-check is 2026-05-17T15:28:36.183Z. Runtime should continue; do not restart solely for the close attempt.

## Operator attestation

No close attestation is signed. Wave-1/Phase-2 successor actions remain blocked by Gate 5 until either:

1. the operator explicitly approves a cadence-equivalence carve-out for the three early fast-cycle deviations; or
2. the clean stable-window reset reaches its 14-day floor and all gates are re-run.

## Cross-links

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — gate definitions and §8.5.2 procedure.
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` — blank pass-attestation template.
- `scripts/check_soak_invariant.sh` — Gate 7 mechanism.
- `scripts/governance_decision_review.py` — Gate 6 review tool.
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation.
