# Mac Studio dead-bot / reboot procedure runbook

**Type:** operator runbook (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator who returns to find the bot dead — launchd job exited, OS rebooted, Mac Studio power-cycled, or `launchctl list | grep com.jake.kalshi-bot` returns empty/exit-code != 0.
**Companion:** `docs/_archive/governance/post-soak-rollback-runbook.md` §0 (when to use); `kill-switch-fire-procedure-runbook.md` (related incident shape).
**Wall-clock target:** 10-20 min from detection to bot stable.

## Detection — operator notices the bot is dead

Symptoms:
- `launchctl list | grep com.jake.kalshi-bot` returns nothing OR last-exit-code != 0
- `tail -50 logs/app/bot.log` shows recent traceback OR no entries in last 30+ min
- Trade-log file `logs/trades/live/trades.jsonl` hasn't grown in last 30+ min
- Operator notification system (if wired) reports bot down

If unsure: run `launchctl list | grep com.jake.kalshi-bot` and compare output PID/exit-code against `~/Library/LaunchAgents/com.jake.kalshi-bot.plist` `KeepAlive=SuccessfulExit:false` semantics. If KeepAlive is honoring the spec, dead bot means the OS-level launchd respawn loop has exhausted — investigate.

## §1 — Quick triage (5 min)

```bash
cd ~/vscode/kalshi-bot

# What state is launchd in?
launchctl list | grep com.jake.kalshi-bot
# Expected: <PID> <exit-code> com.jake.kalshi-bot
# If empty: job is fully unloaded
# If PID = 0 + exit != 0: job ran, exited non-zero, KeepAlive may be backing off

# What did the bot last say?
tail -50 logs/app/launchd.stdout.log
tail -50 logs/app/launchd.stderr.log
tail -50 logs/app/bot.log
```

Look for:
- Stack traces (likely cause)
- "Out of memory" / OOM kill (Mac Studio rare but possible)
- `aiohttp` connection errors (network issue)
- Kalshi API authentication failures (RSA-PSS signing per CLAUDE.md gotchas)
- Database lock errors (sqlite WAL contention)
- Ollama / governance LLM connection refused

## §2 — Categorize the failure

### Category A: Transient (network / Ollama / Kalshi API hiccup)

Symptoms: occasional 5xx / connection-refused / timeout errors that recovered on retry.

**Action:** restart the bot per §3. No code change needed.

### Category B: Code-level exception (bug)

Symptoms: stack trace pointing at a specific module; reproducible on restart.

**Action:** investigate the trace; if it's a recent Wave-1 commit's regression, follow `docs/_archive/governance/post-soak-rollback-runbook.md` §4 (code revert). If it's pre-existing: open new debt entry; decide hotfix vs revert vs quarantine.

### Category C: Resource exhaustion

Symptoms: OOM in launchd.stderr; fd-leak; disk-full.

**Action:** investigate root cause. Don't just restart — bot will hit the same wall.

```bash
df -h                                          # disk space
ulimit -n                                      # fd limit
ls -la logs/                                   # log file growth rate; possible log-rotation issue
```

### Category D: Mac Studio rebooted / power-cycled

Symptoms: launchctl list returns the job entry but PID = 0; bot.log timestamps show a long gap aligned with system uptime.

**Action:** verify launchd jobs are loaded (RunAtLoad should auto-bootstrap on boot per `com.jake.kalshi-bot.plist`); if not loaded, manually bootstrap per §3.

```bash
uptime                                         # how long has Mac Studio been up?
last reboot | head -3                          # when was last reboot?
```

## §3 — Restart procedure (for Categories A or D)

```bash
# Confirm latest code on origin
git pull origin main
git log -1 --oneline

# Run pre-flight smoke test (don't restart against broken code)
.venv/bin/python -m pytest -q tests/test_main_pipeline.py 2>&1 | tail -5
.venv/bin/ruff check . 2>&1 | tail -3

# Confirm Ollama is up (governance + bot both depend on it)
curl -s http://localhost:11434/api/tags | head -3

# Restart the bot
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist

# Verify alive
sleep 30
launchctl list | grep com.jake.kalshi-bot     # PID > 0; exit 0
tail -50 logs/app/bot.log                     # recent activity, no exceptions
```

If the bot stays alive 5+ min without re-failing → restart succeeded.

If it dies again within 5 min → not Category A or D; back to §2 to recategorize.

## §4 — Governance-side check

The bot is one of THREE launchd jobs on the Mac Studio:

- `com.jake.kalshi-bot` — main bot
- `com.kalshi.governance.fast` — governance fast cycle (2 h cadence currently)
- `com.kalshi.governance.deep` — governance deep cycle (24 h cadence currently)

If the BOT is dead, governance jobs may also be affected:

```bash
launchctl list | grep com.kalshi.governance
# Expected: both jobs alive
```

If governance jobs are also dead: more serious system event. Investigate launchd state + system uptime; possibly a Mac Studio reboot occurred.

## §5 — Post-restart verification

After restart, run smoke wrapper:

```bash
bash scripts/wave1_post_deploy_smoke.sh    # if Wave-1 has deployed; otherwise skip
bash scripts/bothealth.sh                  # baseline health
```

Both should exit 0. If either flags a regression, treat as fresh incident per Category B.

## §6 — Document the incident

```bash
# Append to operator log (any local doc; suggest profit_path_debt_log.md PROFIT-PHASE2-001 entry)
# Capture:
# - When was bot last alive (per logs/app/bot.log timestamps)
# - When was bot detected dead (current time)
# - Category (A/B/C/D)
# - Restart action taken
# - Bot-stable-after-restart verdict
```

If the dead-bot was during PHASE2-001 soak: this incident is a §8.5 invariant question. Document for the gate-7 attestation. If the dead-bot caused a cycle gap > 3 h, gate 5 (cadence stability) is at risk.

## What NOT to do

- **DON'T `launchctl bootstrap` without checking why the bot died.** Untreated Category B / C will keep firing.
- **DON'T `git stash` local changes to "clean up" before restart.** Investigate stash content first; may be in-progress work.
- **DON'T edit launchd.stdout.log / launchd.stderr.log.** Evidence preservation.
- **DON'T `rm -rf logs/`.** Evidence preservation; logs are the post-hoc audit trail.

## Cross-links

- `~/Library/LaunchAgents/com.jake.kalshi-bot.plist` — bot launchd config (RunAtLoad + KeepAlive=SuccessfulExit:false)
- `docs/governance/post-soak-rollback-runbook.md` §0 + §2 — when bot dies post-Wave-1 deploy
- `docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md` — sibling incident shape
- `scripts/wave1_post_deploy_smoke.sh` — post-restart verification
- `scripts/bothealth.sh` — daily health check
- `CLAUDE.md` — Kalshi gotchas (signing / websockets header / market status)
