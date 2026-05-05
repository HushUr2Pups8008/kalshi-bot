# PROFIT-PHASE2-001 Day-7 close-day walkthrough

**Audience:** the operator on 2026-05-08 (or 2026-05-10 if §8.5.2 carve-out is contested) executing the §8.5.1 early close.
**Drafted:** 2026-05-05 (during PROFIT-PHASE2-001 wind-down)
**Companion:** `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` (the gates spec); this doc is the linear playbook for actually closing.

End-to-end checklist. Estimated wall-clock: 60-90 min (most of it operator manual review).

## 0. Pre-flight (anytime in the 24 h before close)

- [ ] **Confirm the close window has opened.** Earliest valid close: 2026-05-08T19:01Z (= 2026-05-01T19:01Z + 7 d). If running before that, abort.
- [ ] **Pull latest main.** `git pull origin main`. Confirm at least: `b780fd6` (§8.5.2 carve-out), `bcf4102` (cross-doc date-shift), `747ea15` (gate-7 script), `7b9624b` (gate-6 tool).
- [ ] **Confirm bot still running on the same code.** `git log --oneline origin/main -1` should match HEAD on the running Mac Studio. Verify via the Studio itself if possible.
- [ ] **Re-read** `PROFIT-PHASE2-001-early-close-criteria.md` once. The §8.5.2 carve-out invocation table is load-bearing for gate 7.

## 1. Gate-1: volume

```bash
grep -c '"type": "GOVERNANCE_DECISION"' logs/governance/decisions.jsonl
```

- [ ] Output ≥ 30. **Record the count.**

## 2. Gate-2: calendar floor

```bash
head -1 logs/governance/decisions.jsonl | python3 -c "import sys,json; r=json.loads(sys.stdin.read()); print(r.get('started_at',''))"
date -u +%Y-%m-%dT%H:%M:%SZ
```

- [ ] First cycle started 2026-05-01T19:01Z (or earlier valid; confirm by reading the first row).
- [ ] Current UTC ≥ first_cycle_start + 7 d. **Record the elapsed duration.**

## 3. Gate-3: safety counters

```bash
.venv/bin/python -c "
import json
from collections import Counter
c = Counter()
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    c[r.get('type','')] += 1
    if r.get('batch_aborted'): c['__batch_aborted__'] += 1
print('KILL_SWITCH:', c.get('KILL_SWITCH', 0))
print('GOVERNANCE_VALIDATION_ERROR:', c.get('GOVERNANCE_VALIDATION_ERROR', 0))
print('batch_aborted=True:', c.get('__batch_aborted__', 0))
"
```

- [ ] All three values are 0. **Record.**

## 4. Gate-4: PARSE_ERROR trailing 72 h

```bash
.venv/bin/python -c "
import json
from datetime import datetime, timedelta, timezone
cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
n = 0
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('type') != 'GOVERNANCE_DECISION_PARSE_ERROR': continue
    ts = r.get('ts') or r.get('timestamp') or ''
    try:
        if datetime.fromisoformat(ts.replace('Z','+00:00')) >= cutoff:
            n += 1
    except: pass
print(f'PARSE_ERROR in trailing 72 h: {n}')
"
```

- [ ] Output is 0. **Record.**

## 5. Gate-5: cadence stability

```bash
.venv/bin/python -c "
import json
from datetime import datetime
prev = None
gaps = []
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('type') != 'GOVERNANCE_CYCLE_START': continue
    ts = r.get('started_at','')
    try: t = datetime.fromisoformat(ts.replace('Z','+00:00'))
    except: continue
    if prev is not None:
        gaps.append((t - prev).total_seconds() / 3600)
    prev = t
print(f'gaps n={len(gaps)} max={max(gaps):.2f}h min={min(gaps):.2f}h')
print(f'> 3h gaps: {sum(1 for g in gaps if g > 3.0)}')
print(f'cadence-deviation > ±10% of 2.0h fast: {sum(1 for g in gaps if not (1.8 <= g <= 2.2))}')
"
```

- [ ] Max gap ≤ 3 h. **Record.** Cadence-deviation count > 0 is acceptable IF the deviations are deep-cycle wall-clock-aligned (24 h gap).

## 6. Gate-6: ≥ 85 % reasonable on manual review (operator ONLY)

The §8.5 spec gates manual review as "subjective gate, owner: user." Claude/Codex MUST NOT self-review. Operator runs:

```bash
.venv/bin/python scripts/governance_decision_review.py --since 2026-05-03T15:28Z --output review_${USER}_$(date -u +%Y-%m-%d).jsonl
```

Filter `--since 2026-05-03T15:28Z` scopes to the post-A5-prompt regime (the policy-equivalent regime per §8.5.2). Reviewing only this regime is sufficient if the §8.5.2 carve-out is invoked.

To resume an interrupted session:

```bash
.venv/bin/python scripts/governance_decision_review.py \
    --since 2026-05-03T15:28Z \
    --output review_${USER}_$(date -u +%Y-%m-%d).jsonl \
    --resume review_${USER}_$(date -u +%Y-%m-%d).jsonl
```

- [ ] Operator reviews ≥ 30 decisions (matches gate-1 floor count if gate 6 is being reviewed against the same set).
- [ ] Reasonable rate ≥ 85 %. **Record.**

## 7. Gate-7: soak invariant (with §8.5.2 carve-out)

```bash
bash scripts/check_soak_invariant.sh
```

If output is `PASS — invariant holds`:

- [ ] Gate 7 cleanly satisfied.

If output is `FAIL — invariant violated`:

- [ ] For EACH commit listed: invoke §8.5.2 carve-out check. Per the criteria runbook §8.5.2 table for PROFIT-PHASE2-001, all 4 expected commits already have invocations documented:
  - `fae72fa` (think=False bug-fix)
  - `092666c / 5eadbff / d29bb29 / 8882f4c / 051f391 / 033dc8e / 83bf954 / ce814b9` (GOV-002 audit cycle, test+audit code only)
  - `b47ca71` (A5 SYSTEM_PROMPT — canonical §8.5.2 example)
- [ ] If the script surfaces a NEW commit not in the table: STOP. The carve-out cannot be invoked retroactively without empirical justification. Either document a fresh §8.5.2 evidence-coverage analysis or fall through to default 14-day floor.
- [ ] If all surfaced commits are in the §8.5.2 table: gate 7 passes under §8.5.2 reading.

## 8. Run rollback-anchor automation

```bash
bash scripts/pre_soak_close_branch_backup.sh
```

- [ ] Script reports: tag `pre-wave-1-deploy-${UTC_DATE}` created, branch `backup/pre-wave-1-deploy-${UTC_DATE}` pushed, log archive in `mac_archive/pre_wave1_${UTC_DATE}/`.
- [ ] Verify on origin: `git ls-remote origin "backup/pre-wave-1-deploy-*" "refs/tags/pre-wave-1-deploy-*"` returns the new entries.

## 9. Fill in close attestation

```bash
cp docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md \
   docs/governance/PROFIT-PHASE2-001-early-close-attestation.md
```

Edit `PROFIT-PHASE2-001-early-close-attestation.md`:

- [ ] Fill in close metadata (close timestamp, soak duration, last cycle id).
- [ ] Tick off all 8 gate-verification checkboxes; fill in each gate's recorded value from steps 1-7.
- [ ] Fill in the §8.5.2 carve-out attestation block per commit (already pre-populated for the 4 expected commits in the criteria runbook §8.5.2 table — copy that table contents).
- [ ] Sign the operator attestation.

## 10. Commit + tag the close

```bash
git add docs/governance/PROFIT-PHASE2-001-early-close-attestation.md
git commit -m "docs(governance): PROFIT-PHASE2-001 early close — §8.5.1 attestation"
git tag -a phase2-soak-closed -m "PROFIT-PHASE2-001 early-closed day-7 per §8.5.1; carve-out per §8.5.2"
git push origin main --tags
```

- [ ] Tag `phase2-soak-closed` exists on origin.
- [ ] Latest main commit is the attestation.

## 11. Wave-1 deploy may begin

After all 11 steps complete: Wave-1 deploy may begin per `docs/governance/post-soak-close-rehearsal-checklist.md` §1+ (now references day-7 / 2026-05-08 not day-13 / 2026-05-15).

Suggested next move: open the rehearsal checklist's §1 (OBS-005 deploy) and proceed top-to-bottom through Wave-1's 6 per-feature commits per the order locked in `wave-1-deploy-commit-order-decision.md`.

## Failure handling

If any gate fails:

- **Gates 1, 4, 5:** rare. Investigate the underlying log; re-run the script. If still failing, file an investigation entry and either fix the issue + re-evaluate or fall through to default 14-day close.
- **Gate 2:** reschedule. Wait until 2026-05-08T19:01Z + N hours.
- **Gate 3:** STOP. KILL_SWITCH / batch_aborted / VALIDATION_ERROR fired. Investigate the cycle that produced the event before any close attempt.
- **Gate 6:** if reasonable-rate is below 85 %, decide whether the failures are systematic (a pattern in the LLM's verdicts) or noise. Spec-level review may be needed; operator's call.
- **Gate 7:** new behavioural commit not in §8.5.2 table → fall through to default close (2026-05-15) OR file a new §8.5.2 carve-out evidence-coverage analysis to justify the close.

## Cross-links

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — gate-by-gate criteria with §8.5.2 carve-out invocation table
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` — the attestation skeleton
- `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5 + §8.5.1 + §8.5.2 — Phase 2 acceptance criteria
- `scripts/check_soak_invariant.sh` — gate 7 audit
- `scripts/governance_decision_review.py` — gate 6 manual-review tool
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation
- `docs/governance/post-soak-close-rehearsal-checklist.md` — Wave-1 deploy sequence after close
