# launchd plist drift audit

**Type:** read-only review (Claude task per Implementation Contract §9 — review).
**Source:** `~/Library/LaunchAgents/com.jake.*.plist + com.kalshi.*.plist` vs `scripts/launchd/` repo template.
**Drafted:** 2026-05-05.
**Audience:** operator considering plist source-of-truth consolidation.

## TL;DR

**6 launchd plists currently loaded on Mac Studio.** Only 1 (`com.kalshi.db-backup.plist`) has a repo template; 5 are operator-managed without repo source-of-truth. **1 LOW finding (db-backup matches; ✓), 1 MEDIUM (5 operator-managed plists have no repo template; partial drift risk).**

Recommend: pre-Wave-2-deploy, capture all 5 operator-managed plists into `scripts/launchd/` so all 6 plists have a single source-of-truth.

## Inventory

```
~/Library/LaunchAgents/com.jake.kalshi-bot.plist           1092 bytes  May  1 07:59
~/Library/LaunchAgents/com.jake.kalshi-bothealth.plist     1182 bytes  May  2 15:53
~/Library/LaunchAgents/com.jake.kalshi-soak-check.plist    1544 bytes  May  1 09:08
~/Library/LaunchAgents/com.kalshi.db-backup.plist          1694 bytes  May  5 08:02
~/Library/LaunchAgents/com.kalshi.governance.deep.plist    1542 bytes  May  1 08:39
~/Library/LaunchAgents/com.kalshi.governance.fast.plist    1470 bytes  May  1 08:39

scripts/launchd/com.kalshi.db-backup.plist                 1694 bytes  May  5 (this cycle)
```

## Per-plist drift status

| plist | role | repo template | drift |
|---|---|---|---|
| `com.jake.kalshi-bot` | main bot launchd job | NONE | F2 (no source of truth) |
| `com.jake.kalshi-bothealth` | bothealth.sh schedule | NONE | F2 |
| `com.jake.kalshi-soak-check` | soak monitoring schedule | NONE | F2 |
| `com.kalshi.db-backup` | DB snapshot schedule (cycle 4 fix) | `scripts/launchd/com.kalshi.db-backup.plist` | ✅ matches |
| `com.kalshi.governance.fast` | fast cycle (2h) | NONE | F2 |
| `com.kalshi.governance.deep` | deep cycle (24h) | NONE | F2 |

`diff` of `~/Library/LaunchAgents/com.kalshi.db-backup.plist` vs `scripts/launchd/com.kalshi.db-backup.plist` returns no output → **byte-identical**. ✅

## Findings

### F1 (LOW) — db-backup plist matches repo template

**Source:** `diff -q ~/Library/LaunchAgents/com.kalshi.db-backup.plist scripts/launchd/com.kalshi.db-backup.plist` returns nothing.

**Verdict:** ✅ no drift. Cycle 4.5 install procedure faithful. Repo template is canonical source-of-truth for this plist.

### F2 (MEDIUM) — 5 operator-managed plists have no repo template

**Affected:** `com.jake.kalshi-bot`, `com.jake.kalshi-bothealth`, `com.jake.kalshi-soak-check`, `com.kalshi.governance.fast`, `com.kalshi.governance.deep`.

**Risk:** if any of these is accidentally modified or deleted on the Mac Studio, there is no repo source to restore from. Operator's only recovery is to re-derive the plist from memory + observed behaviour, OR retrieve from a Mac-level Time Machine backup if available.

**Why this matters pre-Wave-1:** the Wave-1 deploy procedure restarts the bot via `launchctl bootout/bootstrap ~/Library/LaunchAgents/com.jake.kalshi-bot.plist`. If that plist is corrupt or missing at fire-time, the bot doesn't restart, and Wave-1 commit-by-commit deploys stall.

**Recommended fix (~10 min wall-clock):**

```bash
# Capture all 5 operator-managed plists into the repo
cp ~/Library/LaunchAgents/com.jake.kalshi-bot.plist        scripts/launchd/
cp ~/Library/LaunchAgents/com.jake.kalshi-bothealth.plist  scripts/launchd/
cp ~/Library/LaunchAgents/com.jake.kalshi-soak-check.plist scripts/launchd/
cp ~/Library/LaunchAgents/com.kalshi.governance.fast.plist scripts/launchd/
cp ~/Library/LaunchAgents/com.kalshi.governance.deep.plist scripts/launchd/

# Verify byte-identical (now)
for f in scripts/launchd/*.plist; do
    diff -q "$HOME/Library/LaunchAgents/$(basename $f)" "$f"
done
# Expected: no output

git add scripts/launchd/*.plist
git commit -m "docs(launchd): capture operator-managed plist source-of-truth"
```

After fix: Codex's `scripts/launchd_plist_drift_audit.sh` (cycle 5 task) gains a meaningful audit surface. Future plist edits go through repo first → on-disk via `cp + launchctl bootstrap`.

**Severity MEDIUM** because:
- Pre-Wave-1: bot plist is load-bearing for every Wave-1 commit's restart.
- Post-Wave-1: governance plists are load-bearing for the post-Wave-1 PHASE2-002 cadence-tune (per `PROFIT-PHASE2-002-onboarding.md` §4 plist edit procedure).
- Risk profile is "low probability, high impact" — plist corruption is rare but recovery without source-of-truth is operator-stress-inducing.

## Cycle-5 Codex task complement

Codex authoring `scripts/launchd_plist_drift_audit.sh` (cycle 5) covers the AUDIT side. F2's recommended fix covers the SOURCE-OF-TRUTH side. Both are needed; neither is sufficient alone:
- Audit without source-of-truth: drift detection without a "correct" reference
- Source-of-truth without audit: drift can occur without detection

**Recommend both land in cycle 5.**

## Out of scope

- Plist content-correctness review (e.g., verifying `com.kalshi.governance.fast` `StartInterval` is correct value). Out of drift-audit scope; covered separately by `PHASE2_RUNBOOK.md`.
- launchctl-level diagnostic (e.g., `launchctl print` deep-dive). Out of scope; this audit is plist-file-level.
- Cross-host (MacBook archive-only) plist comparison. MacBook is archive-only; plists there are historical record.

## Cross-links

- `scripts/launchd/com.kalshi.db-backup.plist` — only repo-tracked plist (cycle 4.5)
- `scripts/launchd/README.md` — install/verify/uninstall ops
- `scripts/launchd_plist_drift_audit.sh` — cycle 5 Codex task (companion to this audit)
- `~/Library/LaunchAgents/com.jake.kalshi-bot.plist` — main bot plist (operator-managed; not in repo)
- `docs/governance/PHASE2_RUNBOOK.md` — operator-side launchd ops (referenced; not audited here)
