# Day-7 → Wave-1-commit-1 hand-off procedure

**Type:** operator runbook (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator at the 30-60 min transition window between `phase2-soak-closed` tag and Wave-1 commit-1 deploy fire.
**Companion:** `2026-05-05-day-7-fire-time-compact-checklist.md` (close); `2026-05-05-wave-1-fire-time-per-commit-checklist.md` (deploy).

## Why this doc exists

The Day-7 close playbook ends at `git tag -a phase2-soak-closed`; the Wave-1 commit-1 playbook starts at "pull origin main, deploy OBS-005." The 30-60 min between those is operator-facing transition time. Without this doc, the operator's choice of "fire commit-1 now vs wait" is implicit. This doc makes the gate explicit.

## TL;DR

After `phase2-soak-closed` tag pushes, **wait** for these conditions before firing Wave-1 commit-1:

1. ≥ 30 min for the tag + attestation to settle on origin and any external mirrors
2. UTC weekday window (Mon-Thu 18:00-22:00) — likely satisfied since close fires at 19:01Z
3. No fresh PARSE_ERROR / VALIDATION_ERROR / KILL_SWITCH events in the 30-min post-close window
4. Operator has ≥ 30 min uninterrupted to babysit the 24h-watch handoff

If any condition fails: defer Wave-1 commit-1 to next valid window. Soak is closed; no time pressure on first commit.

## The hand-off gate

After running the Day-7 close playbook through step 10 (commit + tag + push the attestation), the operator is at this state:

- `phase2-soak-closed` tag exists on origin/main
- attestation committed
- bot still running (no restart performed during close)
- Wave-1 deploy is now AUTHORIZED but not yet fired

Run this 5-min gate before firing commit-1:

```bash
cd ~/vscode/kalshi-bot

# 1. Tag verified on origin
git ls-remote origin phase2-soak-closed
# Expected: <SHA> refs/tags/phase2-soak-closed

# 2. UTC + day-of-week
date -u +"%Y-%m-%dT%H:%M:%SZ %a"
# Expected: weekday Mon-Thu, hour 18-22 UTC

# 3. Bot health post-close
launchctl list | grep com.jake.kalshi-bot
# Expected: PID > 0; exit 0

# 4. No fresh safety events in last 30 min
.venv/bin/python -c "
import json
from datetime import datetime, timedelta, timezone
cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
counts = {'KILL_SWITCH': 0, 'GOVERNANCE_VALIDATION_ERROR': 0, 'GOVERNANCE_DECISION_PARSE_ERROR': 0}
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    t = r.get('type','')
    if t not in counts: continue
    ts = r.get('ts') or r.get('timestamp') or r.get('started_at','')
    try:
        if datetime.fromisoformat(ts.replace('Z','+00:00')) >= cutoff:
            counts[t] += 1
    except: pass
print(counts)
"
# Expected: all 0

# 5. Operator has ≥ 30 min for commit-1 deploy + smoke handoff
# (operator-judgment; not scriptable)
```

## Outcomes

### All conditions pass → fire commit-1

Proceed to `2026-05-05-wave-1-fire-time-per-commit-checklist.md` step 1.

### Any condition fails → defer

Don't fire commit-1. Document the delay reason. Re-run the gate at the next valid window:

| failure | next valid window |
|---|---|
| Outside UTC Mon-Thu 18:00-22:00 | next Mon 18:00Z (or Tue/Wed/Thu) |
| Fresh PARSE_ERROR event | investigate per `2026-05-05-network-api-outage-runbook.md` first; fire commit-1 only after root-cause |
| Fresh VALIDATION_ERROR / KILL_SWITCH | this is a major incident; treat per `2026-05-05-kill-switch-fire-procedure-runbook.md`; commit-1 deferred indefinitely until investigation completes |
| Bot dead | resolve per `2026-05-05-mac-studio-dead-bot-reboot-runbook.md` first |
| Operator not free | wait for free window |

## Hand-off checklist (paste into operator notes at fire-time)

```
PROFIT-PHASE2-001 Day-7 close → Wave-1 commit-1 hand-off

Close attestation commit: ____________
Close tag pushed:          ____________
Hand-off gate run UTC:     ____________

Gate verification:
  [ ] phase2-soak-closed tag on origin
  [ ] UTC Mon-Thu 18:00-22:00
  [ ] Bot alive (PID > 0)
  [ ] No fresh PARSE/VALIDATION/KILL_SWITCH (30-min window)
  [ ] Operator availability ≥ 30 min

Decision:
  [ ] Fire Wave-1 commit-1 (OBS-005) → proceed to wave-1-fire-time-per-commit-checklist.md
  [ ] Defer fire — reason: ____________; next window: ____________
```

## Special-case timing

### Close fires Friday 18:01Z+ (close window opened Mon-Thu but operator delayed to Fri)

UTC Fri 18:00Z = NZ Sat 06:00. Operator-availability window. **Defer commit-1 to Mon 18:00Z+.** Wave-1 timing recommends Mon-Thu deploys; Fri starts the weekend window where regression-watch evidence is thin.

### Close fires Sat/Sun (calendar-floor met)

Defer commit-1 to Mon 18:00Z+. Same logic as above.

### Close fires during Kalshi market high-volume hour

Even within UTC Mon-Thu 18:00-22:00, avoid the US-evening news cycle (21:00-23:00 UTC). 18:00-21:00 UTC is the lull window per `2026-05-05-wave-1-deploy-day-timing.md` §1.2.

## What NOT to do

- **DON'T fire commit-1 immediately after `phase2-soak-closed` tag** to "save time." Soak closed = no time pressure. First commit benefits from settled state.
- **DON'T skip the safety-counter gate** because soak just closed cleanly. Fresh PARSE_ERROR in the 30-min post-close window is unusual and warrants investigation.
- **DON'T fire commit-1 if operator can't babysit 30+ min.** Deploy + smoke + 24h-watch handoff requires hands-on. Better to defer than rush.

## Cross-links

- `2026-05-05-day-7-fire-time-compact-checklist.md` — Day-7 close playbook (predecessor)
- `2026-05-05-wave-1-fire-time-per-commit-checklist.md` — Wave-1 deploy playbook (successor)
- `2026-05-05-wave-1-deploy-day-timing.md` — UTC timing rationale
- `wave-1-deploy-commit-order-decision.md` — locked commit order
- `2026-05-05-kill-switch-fire-procedure-runbook.md` — KILL_SWITCH response
- `2026-05-05-network-api-outage-runbook.md` — PARSE_ERROR root-causing
