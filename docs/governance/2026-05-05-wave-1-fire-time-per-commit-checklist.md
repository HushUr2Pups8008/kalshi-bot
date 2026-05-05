# Wave-1 fire-time per-commit checklist

**Type:** operator-runnable single-page playbook (per Implementation Contract §9 — operator decision input).
**Audience:** operator at fire-time for each Wave-1 commit (6 commits between 2026-05-08T19:01Z+ and ~2026-05-16).
**Drafted:** 2026-05-05.
**Companion:** `wave-1-deploy-commit-order-decision.md` (locked order); `2026-05-05-wave-1-deploy-day-timing.md` (timing); `wave-1-post-deploy-observation-plan.md` (24h watch).
**Wall-clock target per commit:** 30 min landing + 24h passive observation handoff.

## Per-commit checklist (use for each of 6 Wave-1 commits)

### 1. Pre-flight (5 min)

```bash
cd ~/vscode/kalshi-bot
git pull origin main
git log -1 --oneline
date -u +%Y-%m-%dT%H:%M:%SZ                    # within UTC Mon-Thu 18:00-22:00 window
launchctl list | grep com.jake.kalshi-bot     # PID > 0; exit 0
launchctl list | grep com.kalshi.governance.fast | awk '{print $2}'  # confirm not within 30 min of next cycle
```

**Abort if:** outside UTC Mon-Thu 18:00-22:00 OR within governance-fast-cycle pre-cycle window.

### 2. Land the spec (10 min)

Per `wave-1-deploy-commit-order-decision.md` for THIS commit number:

1. Apply the spec's hunk (single file or two files per spec).
2. Remove `pytest.mark.xfail` decorators in same hunk per `wave-1-changelog-entry-prestaged.md` "Removed pytest.mark.xfail markers" list.
3. If commit 6 (final): `echo "0.30.0" > VERSION`.
4. Run pre-commit hook (auto-syncs README badges if VERSION changed).
5. Commit with descriptive message naming the spec.

```bash
.venv/bin/python -m pytest -q tests/test_<spec>.py    # spec-specific harness; 0 failed expected
.venv/bin/ruff check .                                # clean
git add <files>
git commit -m "<spec>: Wave-1 commit N — <one-line summary>"
```

### 3. Push + restart bot (5 min)

```bash
git push origin main

# Bot restart (so new code path is live)
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl list | grep com.jake.kalshi-bot          # PID > 0; exit 0
sleep 30
tail -50 logs/app/bot.log                          # no exceptions in last 50 lines
```

**Abort if:** PID = 0, exit code != 0, OR Traceback in last 50 log lines → `post-soak-rollback-runbook.md` §2.

### 4. Smoke check (5 min)

Run Wave-1 smoke wrapper (Codex cycle 2):

```bash
bash scripts/wave1_post_deploy_smoke.sh
```

Returns 0 if all 14 monitoring rows clean. Returns non-zero on regression → see `post-soak-rollback-runbook.md` §3 (env revert) or §4 (code revert) for the specific commit's rollback path.

### 5. Tag (1 min — only on commit 6)

```bash
git tag -a v0.30.0 -m "Wave-1 base-stack post-soak deploy"
git push origin v0.30.0
```

(Tags 1-5 are not bumped; per-commit micro-tags are operator-discretion.)

### 6. Hand off to 24h regression watch (passive)

Document in operator notes: "Commit N landed at ${UTC}; 24h watch ends at ${UTC + 24h}." Re-run `wave1_post_deploy_smoke.sh` at +12h and +24h checkpoints. If clean, proceed to next commit per `2026-05-05-wave-1-deploy-day-timing.md` §4 cadence (recommended 36h between commits).

## Cadence matrix (recommended; 36h between commits)

| commit | spec | recommended UTC start | regression watch ends | next-commit allowed |
|---|---|---|---|---|
| 1 | OBS-005 | 2026-05-08T20:00Z (Fri) | 2026-05-09T20:00Z | 2026-05-10T08:00Z |
| 2 | MATCH-001 (B') | 2026-05-09T20:00Z (Sat) | 2026-05-10T20:00Z | 2026-05-11T08:00Z |
| 3 | OBS-003 | 2026-05-11T18:00Z (Mon) | 2026-05-12T18:00Z | 2026-05-13T06:00Z |
| 4 | EXEC-002 | 2026-05-13T06:00Z (Wed) | 2026-05-14T06:00Z | 2026-05-14T18:00Z |
| 5 | GOV-003 | 2026-05-14T18:00Z (Thu) | 2026-05-15T18:00Z | 2026-05-16T06:00Z |
| 6 | EDGE-004 A.1 | 2026-05-16T06:00Z (Sat) | 2026-05-18T06:00Z (48h watch on final) | Wave-2 Branch A start |

## Rollback decision tree

```
Smoke wrapper returns non-zero?
    │
    ▼
Check which row(s) triggered
    ├── env-driven rollback available? (EXEC-002 only)
    │       ▼
    │    launchctl setenv <var> 0; bot restart; document in debt log
    └── code revert needed (OBS-005 / MATCH-001 / OBS-003 / GOV-003 / Lever A.1)
            ▼
        per post-soak-rollback-runbook.md §4.<commit-N>; revert; push; restart
```

## Cross-links

- `wave-1-deploy-commit-order-decision.md` — locked commit order
- `wave-1-changelog-entry-prestaged.md` — VERSION + xfail-marker-removal table
- `wave-1-post-deploy-observation-plan.md` — 14-row regression watch (smoke wrapper basis)
- `2026-05-05-wave-1-deploy-day-timing.md` — full timing rationale
- `post-soak-rollback-runbook.md` — incident response
- `scripts/wave1_post_deploy_smoke.sh` — Codex cycle 2 wrapper
- `2026-05-05-day-7-fire-time-compact-checklist.md` — Day-7 close (this cycle's companion)
