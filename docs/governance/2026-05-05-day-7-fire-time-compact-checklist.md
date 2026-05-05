# Day-7 fire-time compact checklist

**Type:** operator-runnable single-page playbook (Claude task per Implementation Contract §9 — operator decision input).
**Audience:** operator at fire-time on 2026-05-08T19:01Z+.
**Drafted:** 2026-05-05.
**Companion (longer):** `PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` (11-step linear playbook); `2026-05-05-day-7-attestation-prestage.md` (pre-staged values).
**Wall-clock target:** 30-45 min total.

## Pre-flight (5 min)

```bash
cd ~/vscode/kalshi-bot
date -u +%Y-%m-%dT%H:%M:%SZ                    # ≥ 2026-05-08T19:01Z to proceed
git pull origin main
git log -1 --oneline                           # confirm latest cycle landed
launchctl list | grep com.kalshi.governance    # both jobs alive
```

## Gate verification (10 min)

Run the unified gate-check in one block:

```bash
.venv/bin/python -c "
import json
from datetime import datetime, timedelta, timezone
from collections import Counter
c = Counter(); batch_ab = 0; prev = None; gaps = []; parse_72h = 0
cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    t = r.get('type','')
    c[t] += 1
    if r.get('batch_aborted'): batch_ab += 1
    if t == 'GOVERNANCE_CYCLE_START':
        try:
            dt = datetime.fromisoformat(r.get('started_at','').replace('Z','+00:00'))
            if prev: gaps.append((dt-prev).total_seconds()/3600)
            prev = dt
        except: pass
    if t == 'GOVERNANCE_DECISION_PARSE_ERROR':
        try:
            ts = r.get('ts') or r.get('timestamp') or ''
            if datetime.fromisoformat(ts.replace('Z','+00:00')) >= cutoff:
                parse_72h += 1
        except: pass
print('Gate 1 volume (≥30):', c.get('GOVERNANCE_DECISION',0))
print('Gate 3 KILL_SWITCH (=0):', c.get('KILL_SWITCH',0))
print('Gate 3 VALIDATION_ERROR (=0):', c.get('GOVERNANCE_VALIDATION_ERROR',0))
print('Gate 3 batch_aborted (=0):', batch_ab)
print('Gate 4 PARSE_ERROR trailing 72h (=0):', parse_72h)
print(f'Gate 5 max gap (≤3h): {max(gaps):.2f}h')
print(f'Gate 5 >3h gaps: {sum(1 for g in gaps if g>3)}')
"
```

**Pass criteria:** Gate 1 ≥ 30; Gate 3 all 0; Gate 4 = 0; Gate 5 max ≤ 3.0 h. **Record values for attestation.**

## Gate 6 manual review (~15 min with --bulk-mode)

```bash
.venv/bin/python scripts/governance_decision_review.py \
    --since 2026-05-03T15:28Z \
    --output review_${USER}_$(date -u +%Y-%m-%d).jsonl \
    --bulk-mode
```

Codex's `--bulk-mode` (cycle 2) collapses the 241 mechanically-uniform `disable_source` dead-Reddit-sub decisions into a single bulk verdict. Operator reviews the 1 anomaly (`gd_2026-05-04_0049` NYT World News, anchor_rate=1.0). **Verdict pass criterion: ≥ 85 % reasonable.**

## Gate 7 soak invariant + §8.5.2 carve-out (5 min)

```bash
bash scripts/check_soak_invariant.sh --json
```

Expected output: `status=fail`, surfaced commits = the §8.5.2 invocation table from `PROFIT-PHASE2-001-early-close-criteria.md` + this-cycle commits (cycles 2 + 3). All commits in the table → **gate 7 passes under §8.5.2 reading.**

If a NEW commit not in table: STOP. Either write fresh §8.5.2 evidence-coverage analysis OR fall through to default 14-day close.

## Rollback anchor + attestation + tag (10 min)

```bash
# Rollback anchor (creates pre-wave-1-deploy-${UTC_DATE} tag + branch)
bash scripts/pre_soak_close_branch_backup.sh --no-push     # dry-trace first
bash scripts/pre_soak_close_branch_backup.sh               # actual

# Fill attestation (use 2026-05-05-day-7-attestation-prestage.md as template)
cp docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md \
   docs/governance/PROFIT-PHASE2-001-early-close-attestation.md
# Edit: fill close timestamp, soak duration, last cycle id, recorded gate values
$EDITOR docs/governance/PROFIT-PHASE2-001-early-close-attestation.md

# Sign + commit + tag
git add docs/governance/PROFIT-PHASE2-001-early-close-attestation.md
git commit -m "docs(governance): PROFIT-PHASE2-001 early close — §8.5.1 attestation"
git tag -a phase2-soak-closed -m "PROFIT-PHASE2-001 early-closed day-7 per §8.5.1; carve-out per §8.5.2"
git push origin main --tags

# Verify
git ls-remote origin phase2-soak-closed
```

## Done

PROFIT-PHASE2-001 closed. Wave-1 deploy may begin per `wave-1-deploy-commit-order-decision.md` + `2026-05-05-wave-1-deploy-day-timing.md`.

## If anything fails

| gate | failure response |
|---|---|
| Gate 1 (volume < 30) | rare; investigate — should have ≥ 200+ at 7-day mark |
| Gate 3 (safety counter > 0) | STOP. Investigate the firing event before any close attempt. |
| Gate 4 (PARSE_ERROR trailing 72h > 0) | rare; investigate launchd / governance/ |
| Gate 5 (max gap > 3 h) | investigate launchd; reschedule if cron drift |
| Gate 6 (< 85 % reasonable) | spec-level review; operator's call (per §8.5 spec) |
| Gate 7 (new commit not in §8.5.2 table) | STOP. Write fresh evidence-coverage analysis OR fall to 14-day default close |

## Cross-links

- `PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` — full 11-step playbook
- `2026-05-05-day-7-attestation-prestage.md` — pre-staged values
- `2026-05-05-day-7-walkthrough-dry-trace.md` — 2026-05-05 validation against HEAD
- `PROFIT-PHASE2-001-early-close-criteria.md` — gate criteria + §8.5.2 invocation table
