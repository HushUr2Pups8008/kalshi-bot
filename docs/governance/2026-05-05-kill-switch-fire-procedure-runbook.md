# KILL_SWITCH fire procedure runbook

**Type:** operator runbook (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator when a `KILL_SWITCH` event fires in `logs/governance/decisions.jsonl` during/post-Wave-1.
**Companion:** `post-soak-rollback-runbook.md` §2 (emergency revert); governance spec §8.5 / §8.5.1 / §8.5.2 (KILL_SWITCH definition + triggers).
**Wall-clock target:** 15-30 min from fire detection to bot stable (revert) or quarantine (preserve).

## What is KILL_SWITCH

A `KILL_SWITCH` event is emitted by the governance agent when its safety-counter logic detects an invariant violation that requires immediate halt. Per `governance/llm.py` + `governance/monitor.py`, KILL_SWITCH fires when:

- Decision-distribution collapse detected (e.g., > 90 % decisions in a single category, sudden shift)
- Invariant-violation in decision schema (more aggressive than VALIDATION_ERROR)
- Manual operator-trigger (TBD if spec'd)
- Cross-cycle batch_aborted cascade

KILL_SWITCH is **rare** but **irreversible without operator action**. Per PHASE2-001 soak (267 decisions through Day-4): 0 KILL_SWITCH fires.

## Fire detection

Operator-side monitoring path:

```bash
# Real-time check (run on demand)
.venv/bin/python -c "
import json
n = 0
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('type') == 'KILL_SWITCH': n += 1; print(line.rstrip())
print(f'KILL_SWITCH count: {n}')
"
```

If `n > 0` → KILL_SWITCH has fired. Continue per §1.

If automated alert routing is wired (per Codex cycle 4 task `operator_alert_routing_audit.sh`): operator gets notification before manual check needed.

## §1 — Stop the bot (FIRST ACTION; ~1 min)

Don't analyse first. Stop the bot to freeze damage:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl list | grep com.jake.kalshi-bot     # PID = 0; bot stopped
```

Governance jobs continue running (they're separate launchd jobs). Stopping the bot prevents new BlendTask / OPPORTUNITY / executor activity. Governance continues to record decisions; that's fine.

## §2 — Capture the KILL_SWITCH context (~5 min)

```bash
# Find the offending event
.venv/bin/python -c "
import json
with open('logs/governance/decisions.jsonl') as f:
    for line in f:
        try: r = json.loads(line)
        except: continue
        if r.get('type') == 'KILL_SWITCH':
            print('=== KILL_SWITCH ===')
            print(json.dumps(r, indent=2))
" | head -50
```

Note:
- `cycle_id` of the offending cycle
- `reason` field (what triggered KILL_SWITCH)
- Surrounding decisions (5 before / 5 after) for context

```bash
# Surrounding decisions
grep -B 5 -A 5 "KILL_SWITCH" logs/governance/decisions.jsonl | head -50
```

## §3 — Decide: REVERT vs QUARANTINE (~5 min)

Two paths:

### Path A: REVERT (bot returns to pre-KILL_SWITCH commit)

If KILL_SWITCH happened during/post-Wave-1 deploy of a specific commit AND symptoms map to that commit:

1. Identify the suspect commit: `git log --since "2026-05-08" --until "now" --oneline`
2. Per `post-soak-rollback-runbook.md` §2:
   ```bash
   git revert <suspect-commit-sha>
   git push origin main
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
   ```
3. Confirm bot alive: `launchctl list | grep com.jake.kalshi-bot` (PID > 0)
4. Monitor `logs/governance/decisions.jsonl` for 30 min — no fresh KILL_SWITCH.
5. Document: `docs/profit_path_debt_log.md` PROFIT-PHASE2-001 entry — append KILL_SWITCH-fire log.

### Path B: QUARANTINE (preserve state for analysis; don't restart)

If KILL_SWITCH happened mid-soak (not during a Wave-1 deploy) OR root cause unclear:

1. Tar the logs as evidence:
   ```bash
   tar czf logs_killswitch_$(date -u +%Y%m%d_%H%M).tar.gz logs/
   ```
2. Don't restart the bot. Investigate `governance/llm.py` + `governance/monitor.py` per the captured `reason`.
3. If decision-distribution collapse: investigate decisions.jsonl in detail; was there a corpus shift?
4. If invariant violation: cross-check decision schema vs `governance/decision.py` invariants.
5. If batch_aborted cascade: check sibling launchd jobs (governance fast/deep).
6. Document in profit_path_debt_log.md as a NEW debt entry (`PROFIT-KILL-001` if first time; reuse existing if recurrent).

## §4 — Post-incident actions

### After REVERT path

- [ ] Bot alive (PID > 0) for 30+ min without re-firing KILL_SWITCH
- [ ] Trade-log structure intact (`bothealth.sh` runs clean)
- [ ] Document the rollback in profit_path_debt_log.md PROFIT-PHASE2-001 entry
- [ ] If during Wave-1 deploy: pause subsequent commit deploys until root cause identified
- [ ] Tag the incident: `git tag -a kill-switch-fire-$(date -u +%Y-%m-%d)` for audit-trail

### After QUARANTINE path

- [ ] Analyse the captured `reason` field; root-cause investigation
- [ ] Open new debt entry OR update existing
- [ ] Decide bot restart strategy:
  - **Same code, just restart:** if root cause is transient (e.g., Ollama crash producing a single bad batch); restart and watch
  - **Code change required:** revert the offending commit OR ship a bug-fix; then restart
  - **Long quarantine:** if root cause requires multi-day investigation, leave bot stopped; document handoff state

## What NOT to do

- **DON'T just restart without investigation.** The KILL_SWITCH is meaningful — restarting masks the underlying issue.
- **DON'T edit `logs/governance/decisions.jsonl` to suppress KILL_SWITCH events.** That's evidence-destruction.
- **DON'T `git push --force` to remove the offending commit from history.** Use `git revert` to preserve audit trail.
- **DON'T assume Wave-1 deploy is the cause.** KILL_SWITCH can fire from governance-side issues unrelated to bot deploy code.

## If KILL_SWITCH fires DURING Day-7 close (rare, severe)

If KILL_SWITCH fires between fire-time pre-flight and tag-the-close (~30-45 min window):

- **Abort the Day-7 close.** Don't tag `phase2-soak-closed`.
- Stop the bot per §1.
- Quarantine per §3 Path B.
- Push close to default 14-day floor (2026-05-15) per §8.5 acceptance criteria.

## Cross-links

- `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5 — KILL_SWITCH definition
- `docs/governance/post-soak-rollback-runbook.md` — full revert procedures
- `docs/profit_path_debt_log.md` PROFIT-PHASE2-001 entry — KILL_SWITCH-fire log destination
- `governance/llm.py`, `governance/monitor.py` — KILL_SWITCH emit logic
- `scripts/operator_alert_routing_audit.sh` (Codex cycle 4) — verifies alert routing for KILL_SWITCH
- `scripts/wave1_post_deploy_smoke.sh` — Wave-1 regression watch (KILL_SWITCH detection row)
