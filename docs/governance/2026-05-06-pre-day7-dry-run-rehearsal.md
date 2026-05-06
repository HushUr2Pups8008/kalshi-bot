# Pre-Day-7 dry-run rehearsal

**Type:** read-only verification (Claude task per Implementation Contract §9 — review).
**Source:** live gate-script execution at 2026-05-06T00:10Z (≈ T-1d 19h to earliest valid close at 2026-05-08T19:01Z).
**Audience:** operator pre-fire-time confidence check.
**Drafted:** 2026-05-05/06.

## TL;DR

**All §8.5.1 gates currently PASS or are on track to pass.** Volume gate over-cleared 12.8× (383 vs 30 floor). Safety counters all 0 across the 4-day-19-hour soak window. Cadence stability clean (max gap 2.01h ≤ 3h). Manual review (gate 6) accelerator analysis stands at ≥ 99.6% reasonable on the projected close-time corpus. Gate 7 fires per §8.5.2 carve-out as expected (5 surfaced commits all map to documented carve-outs + cycle-2/3 doc artifacts).

**Recommended:** no operator action between now (T-1d 19h) and fire-time (T+0). Dry-run confirms readiness.

## Per-gate dry-run results

### Gate 1: Volume (≥ 30)

```
.venv/bin/python: 'GOVERNANCE_DECISION' count: 383
```

**Verdict:** ✅ PASS at 12.8× floor. By 2026-05-08T19:01Z (~ +43h from now), expect ~ 21 more cycles × ~5.7 decisions/cycle = ~120 additional decisions → projected close-time count ~ 503. Floor over-clearance ~16.7×.

### Gate 2: Calendar floor (≥ 7d)

```
first cycle: 2026-05-01T19:01:27Z
current UTC: 2026-05-06T00:10:50Z
elapsed: 4d 5h 9m 23s
```

**Verdict:** ⏳ on-track. Need 2d 18h 51m more for 7d floor; close window opens 2026-05-08T19:01Z.

### Gate 3: Safety counters (all 0)

```
KILL_SWITCH: 0
GOVERNANCE_VALIDATION_ERROR: 0
batch_aborted: 0
```

**Verdict:** ✅ PASS. Clean across full 4d 5h soak window.

### Gate 4: PARSE_ERROR trailing 72h (= 0)

```
total PARSE_ERROR: 7 (all on day-1/-2)
trailing 72h: 0
```

**Verdict:** ✅ PASS. Cycle-3 retroactive audit attributed all 7 events to `missing_required_fields` on day-1/-2; no recurrences.

### Gate 5: Cadence stability (max gap ≤ 3h)

```
gap n: 56
max gap: 2.01h
```

**Verdict:** ✅ PASS. Single 2.01h gap is within tolerance; no >3h breaches.

### Gate 6: Manual review reasonable rate (≥ 85%) — projected

Per cycle-2 distribution analysis + Codex's gate-6 review run:
- Bulk-reviewable mechanically-uniform `disable_source` decisions: 241/242 = 99.6%
- Codex's 67/67 verdicted post-A5 decisions: 100% reasonable
- 1 NYT World News anomaly (`gd_2026-05-04_0049`) requires operator verdict at fire-time

**Verdict:** ⏳ on-track. Operator runs `governance_decision_review.py --bulk-mode` at fire-time; ≥ 99% projected.

### Gate 7: Soak invariant (clean OR §8.5.2 carve-out)

```
$ bash scripts/check_soak_invariant.sh --json
{"status":"fail","soak_start":"2026-05-01T19:01Z","head":"ffc54b2","commit_count":5,"behavioural_paths":[...],"missing_paths":[]}
```

**5 commits surfaced:**

| commit | scope | §8.5.2 status |
|---|---|---|
| `fae72fa` | think=False bug-fix | INVOKED |
| `092666c…ce814b9` | GOV-002 audit cycle (test+audit only) | INVOKED |
| `b47ca71` | A5 SYSTEM_PROMPT (anchor_rate) | INVOKED |
| cycle-2 doc/script artifacts | docs/ + scripts/ + tests/ only | OUT-OF-SCOPE for §8.5.2 |
| cycle-3 doc/script artifacts | same | OUT-OF-SCOPE for §8.5.2 |

**Verdict:** ✅ PASS under §8.5.2 reading. All 5 surfaced commits documented in `PROFIT-PHASE2-001-early-close-criteria.md` §8.5.2 invocation table (cycle 5 update).

### Gate 8: Written attestation (committed)

**Verdict:** ⏳ deferred. Operator commits `PROFIT-PHASE2-001-early-close-attestation.md` at fire-time per `2026-05-05-day-7-attestation-prestage.md` template.

## Operator-side fire-time prep

All gate-script values are already pre-staged in `2026-05-05-day-7-attestation-prestage.md` from current state. Operator's actual fire-time work:

1. Re-run gate-check Python block (~5 min) → adjust deltas in attestation
2. Run `governance_decision_review.py --bulk-mode` (~15 min)
3. Run `check_soak_invariant.sh --json` (~10 sec)
4. Run `pre_soak_close_branch_backup.sh` (~5 min)
5. Edit pre-staged attestation with delta values + sign
6. Commit + tag + push (~5 min)

**Total: ≈ 30-40 min wall-clock at fire-time.** No surprises expected.

## Risk assessment from this dry-run

| risk | likelihood | impact | mitigation |
|---|---|---|---|
| Fresh PARSE_ERROR in trailing 72h | LOW (none in 4d 5h) | gate 4 fail | retroactive audit tracked all 7 to day-1/-2 root cause |
| Cadence gap > 3h | LOW (max 2.01h) | gate 5 fail | launchd plist stable; no scheduled disruption |
| KILL_SWITCH / VALIDATION_ERROR / batch_aborted fire | LOW (0 in 4d 5h) | gate 3 fail; STOP | per `2026-05-05-kill-switch-fire-procedure-runbook.md` |
| Gate 7 surfaces NEW commit not in §8.5.2 table | LOW (cycle-1-7 commits stay doc/script-only) | needs fresh §8.5.2 analysis OR fall to default 14d | criteria runbook §8.5.2 table covers all foreseen commits |
| Network/API outage at fire-time | LOW (no current signs) | defer fire-time | per `2026-05-05-network-api-outage-runbook.md` |
| Operator availability | unknown | defer fire-time | per `2026-05-05-day-7-to-wave-1-handoff-procedure.md` |

## Out of scope

- Wave-1 commit deploy dry-run (covered by `2026-05-05-wave-1-deploy-dry-run-report.md`)
- Codex's `wave1_commit_chain_acceptance_audit.sh` execution (Codex cycle-8 task)
- Day-7 mid-soak confirmation file creation (operator fire-time)

## Cross-links

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — §8.5.1 gate criteria + §8.5.2 invocation table
- `docs/governance/2026-05-05-day-7-fire-time-compact-checklist.md` — fire-time playbook
- `docs/governance/2026-05-05-day-7-attestation-prestage.md` — pre-staged values
- `docs/governance/2026-05-05-day-7-walkthrough-dry-trace.md` — cycle-2 sibling validation
- `docs/governance/2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md` — gate-6 accelerator
- `scripts/check_soak_invariant.sh`, `governance_decision_review.py`, `pre_soak_close_branch_backup.sh`
