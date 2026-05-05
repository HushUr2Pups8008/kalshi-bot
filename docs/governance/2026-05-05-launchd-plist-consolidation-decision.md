# launchd plist directory consolidation decision

**Type:** ambiguity resolution (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05 (cycle 7).
**Audience:** operator deciding which plist source-of-truth pattern to keep.
**Companion:** `2026-05-05-launchd-plist-drift-audit.md` (cycle 5; surfaced the gap).

## TL;DR

Two repo locations currently host launchd plist source-of-truth:

| location | pattern | files | created | byte-identical compare? |
|---|---|---|---|---|
| `ops/launchd/` | template-render (`*.plist.template` + `install.sh` substitutes `@REPO_ROOT@`/`@VENV_PYTHON@`/`@GOVERNANCE_LLM_MODEL@`) | `governance.fast.plist.template`, `governance.deep.plist.template`, `install.sh`, `.gitignore` | 2026-05-01 (pre-cutover) | NO — substituted before compare |
| `scripts/launchd/` | byte-identical (`*.plist` matches installed) | `com.kalshi.db-backup.plist`, `README.md` | 2026-05-05 cycle 4 | YES |

**Recommendation: keep `ops/launchd/` as the canonical pattern. Migrate db-backup.plist to template form. Deprecate `scripts/launchd/`.**

## Why `ops/launchd/` wins

1. **Operator-clone path independence.** The template approach handles different operators / different Mac Studios with different repo paths. Byte-identical breaks on any `@REPO_ROOT@` mismatch.
2. **Already established pattern (2026-05-01).** Pre-existing for governance plists. Cycle 4 created `scripts/launchd/` because the existing dir was missed during plist drift audit T8 — not because the existing pattern was inadequate.
3. **install.sh provides operator-runnable bootstrap.** Already wired (`bash ops/launchd/install.sh`). The byte-identical pattern's `cp + launchctl bootstrap` is more operator-side ad-hoc.
4. **Drift audit script covers it.** Codex's `scripts/launchd_plist_drift_audit.sh` (cycle 6) renders templates + compares against installed plists. Already integrated.

## Why `scripts/launchd/` was created

Cycle 4 plist drift audit (T8) missed the pre-existing `ops/launchd/` directory because the audit only surveyed `scripts/launchd/` (a directory I created in the same cycle for the new db-backup plist). That oversight created the dual source-of-truth.

**Honest read:** the cycle-4 plist creation should have used `ops/launchd/` template form from the start. Reverting that requires migrating the file + updating cross-references.

## Migration plan (recommended for cycle 7)

### Step 1: Move db-backup to template form

```bash
# Convert scripts/launchd/com.kalshi.db-backup.plist to template
# Replace hardcoded /Users/jacobparenti/vscode/kalshi-bot/ with @REPO_ROOT@
# Replace hardcoded /bin/bash if appropriate (not needed; /bin/bash is universal on macOS)

cp scripts/launchd/com.kalshi.db-backup.plist ops/launchd/com.kalshi.db-backup.plist.template

# Edit ops/launchd/com.kalshi.db-backup.plist.template:
# - ProgramArguments script path: /Users/.../scripts/db_snapshot_backup.sh → @REPO_ROOT@/scripts/db_snapshot_backup.sh
# - WorkingDirectory: /Users/... → @REPO_ROOT@
# - StandardOutPath/StandardErrorPath: /Users/.../logs/app/db-backup.* → @REPO_ROOT@/logs/app/db-backup.*

# Update ops/launchd/install.sh to also generate + install com.kalshi.db-backup.plist
# (currently only handles governance.fast/deep)

# Update ops/launchd/.gitignore to exclude com.kalshi.db-backup.plist (generated)

# Remove cycle-4 byte-identical plist
git rm scripts/launchd/com.kalshi.db-backup.plist

# Update scripts/launchd/README.md → point at ops/launchd/install.sh
# OR remove scripts/launchd/ entirely
git rm scripts/launchd/README.md
git rm -r scripts/launchd/   # if empty after removals
```

### Step 2: Verify drift audit still passes

```bash
bash scripts/launchd_plist_drift_audit.sh --json
# Expected: status=pass; com.kalshi.db-backup matches via @REPO_ROOT@-substituted template
```

### Step 3: Update cross-references

Affected docs:
- `docs/governance/2026-05-05-db-backup-gap-resolution.md` — operator install procedure references `scripts/launchd/com.kalshi.db-backup.plist` → update to `ops/launchd/install.sh`
- `docs/governance/2026-05-05-launchd-plist-drift-audit.md` — recommendations section
- `docs/profit_path_debt_log.md` cycle 4.5 entry — install procedure
- README.md (if any references)

```bash
grep -rln "scripts/launchd/com.kalshi.db-backup.plist" docs/ README.md 2>/dev/null
# Update each match to point at ops/launchd/install.sh
```

### Step 4: Capture remaining 3 operator-managed plists

`com.jake.kalshi-bot`, `com.jake.kalshi-bothealth`, `com.jake.kalshi-soak-check` are still operator-managed without repo source-of-truth. Cycle-5 plist drift audit T8 F2 finding stands. Convert each to template form and add to `ops/launchd/`:

```bash
# For each operator-managed plist on Mac Studio:
#   1. Cat the installed plist
#   2. Replace hardcoded paths with @REPO_ROOT@ / @VENV_PYTHON@ tokens
#   3. Save as ops/launchd/<label>.plist.template
#   4. Update ops/launchd/install.sh to generate + install
#   5. Verify drift audit still passes
```

This is operator-side because operator knows which paths/env-vars are load-bearing. Recommend deferring to post-Wave-1 close (mid-Wave-2 prep).

## Total wall-clock for steps 1-3

~30-45 min. Not blocking Wave-1 deploy. Recommended pre-Wave-2 deploy so `bash ops/launchd/install.sh` is the canonical install path going forward.

## Why NOT do this pre-Wave-1 close

- `scripts/launchd/com.kalshi.db-backup.plist` is currently installed + working
- Bootstrap procedure documented + executed cycle 4.5
- No urgency until a fresh operator clones the repo (or Mac Studio is rebuilt)

## Rollback

If migration breaks something: `git revert <migration-commit>`; old `scripts/launchd/` returns; installed plist is unchanged on Mac Studio.

## Out of scope

- Renaming `ops/` directory itself. Pre-existing convention.
- Deduplicating other config locations (`scripts/`, `data/`, `logs/`). Out of plist scope.
- Capturing the 3 operator-managed plists into the repo. Step 4 above; deferred.

## Cross-links

- `docs/governance/2026-05-05-launchd-plist-drift-audit.md` — cycle 5; surfaced the dual source-of-truth
- `ops/launchd/install.sh` — canonical install entry point (governance.fast/deep currently; will gain db-backup post-migration)
- `scripts/launchd/com.kalshi.db-backup.plist` — cycle 4 byte-identical plist; migration target
- `scripts/launchd_plist_drift_audit.sh` — Codex cycle 6 drift audit; already template-aware
