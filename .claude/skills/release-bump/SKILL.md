---
name: release-bump
description: Bump VERSION, add CHANGELOG entry, let .githooks/pre-commit auto-sync README badges, optionally tag and push. Enforces kalshi-bot release ritual from CLAUDE.md release-versioning section. Use when user says "bump version", "cut release", "/release-bump", or stages VERSION manually.
disable-model-invocation: true
---

# release-bump

Mechanical release-cut workflow for kalshi-bot. User-only because tagging + pushing are irreversible operator-gate actions.

## Preconditions

Run these checks BEFORE editing anything:

```bash
# Confirm clean tree on main (or current release branch)
git status --short
git rev-parse --abbrev-ref HEAD

# Confirm pre-commit hook is wired (one-time setup per clone)
git config --get core.hooksPath
# Expected: .githooks
# If empty: run `git config core.hooksPath .githooks` first
```

If `core.hooksPath` is unset, the README auto-sync hook is dormant and CI will fail the lint job. Wire the hook first.

## Step 1 — Decide bump

| Change shape | Bump |
|---|---|
| Bug fix, no behavior change | patch (`vX.Y.Z+1`) |
| New feature, backwards compatible | minor (`vX.Y+1.0`) |
| Breaking change, schema break, paper/live cutover, signing change | major (`vX+1.0.0`) — confirm with operator |

Read current VERSION:

```bash
cat VERSION
```

## Step 2 — Edit VERSION + CHANGELOG in one commit

VERSION: replace line with new semver.
CHANGELOG.md: prepend a new section. Format follows existing entries.

```markdown
## vX.Y.Z — YYYY-MM-DD

### Added / Changed / Fixed / Removed
- One-line summary per change, present tense, why-first when non-obvious.
```

Stage VERSION first (hook reads it):

```bash
git add VERSION CHANGELOG.md
git commit -m "release: vX.Y.Z — <one-line summary>"
```

`.githooks/pre-commit` will:
- Run `scripts/sync_readme_version.py` to update README badges + "Current through" line
- Re-stage README.md
- Run `scripts/launchd_template_equivalence_audit.py` if any `.plist.template` is staged

`.githooks/prepare-commit-msg` will NOT auto-append `[skip ci]` because VERSION/CHANGELOG are not in the docs-only allowlist — release commits SHOULD run CI.

## Step 3 — Tag (non-trivial releases only)

Tags are the only rollback anchor and the only thing that makes `git describe HEAD` meaningful.

```bash
git tag -a vX.Y.Z -m "vX.Y.Z — <summary matching CHANGELOG header>"
git push origin main
git push origin vX.Y.Z
```

## Step 4 — Verify

```bash
git describe HEAD                           # should print vX.Y.Z
.venv/bin/python scripts/sync_readme_version.py --check   # should be silent
```

## Edge cases

**Hook failure** (`scripts/sync_readme_version.py --check` fails after the hook ran):
- Investigate before bypassing. The hook re-stages README; if README still drifts, the sync script is broken — fix it.
- Emergency bypass: `git commit --no-verify`. CI will still catch drift on push.

**Tag collision**: `git tag -a vX.Y.Z` fails if tag exists. Delete only if you authored it and it never reached the remote:
```bash
git tag -d vX.Y.Z       # local only
# NEVER `git push --delete origin vX.Y.Z` for a published tag — see v0.30.0 lesson
```

**v0.30.0 lesson** (CLAUDE.md "Kalshi API" section): a published-broken tag is **immobile**. Cut a hotfix (`v0.30.1`) and document the broken tag as published-broken in CHANGELOG. Never force-overwrite a pushed tag.

**Paper/live cutover release**: any release that changes paper→live execution authority requires operator confirmation per `~/.claude/rules/agent_collaboration.md` and CLAUDE.md governance section. Do not auto-cut these.
