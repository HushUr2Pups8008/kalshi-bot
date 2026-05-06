# 2026-05-06 CHANGELOG drift-check refresh (cycle 10)

**Type:** read-and-fix audit. Re-runs cycle-5 drift-check methodology against the current pre-staged Wave-1/2/3 entries. Surfaces version-number drift; applies corrections.
**Drafted:** 2026-05-06 (cycle 10).
**Companions:**
- `2026-05-05-changelog-drift-check.md` (cycle-5 original drift-check)
- `wave-1-changelog-entry-prestaged.md` (Wave-1 prestaged block)
- `wave-2-wave-3-changelog-entries-prestaged.md` (Wave-2/3 prestaged blocks)

## TL;DR

Cycle-5 drift-check refreshed the **version sequence table** at the top of `wave-2-wave-3-changelog-entries-prestaged.md` but did NOT re-align the **prestaged-block headlines + operator deploy commands** below. Result: 6 stale version refs in the prestaged blocks vs the table.

Cycle-10 fix: re-aligned all 6 refs. Audit clean post-fix.

## Findings

| location | stale value | correct value | risk if unfixed |
|---|---|---|---|
| Wave-2 block headline (line 41) | `[0.30.1] - 2026-05-22` | `[0.31.0] - 2026-05-22` | operator paste at deploy commits a wrong VERSION line; pre-commit README sync would fail or silently mis-link the badge |
| Wave-2 deploy commands (line 89) | `echo "0.30.1" > VERSION` | `echo "0.31.0" > VERSION` | same |
| Wave-2 deploy commands (line 98) | `git tag -a v0.30.1` | `git tag -a v0.31.0` | tag mismatch with VERSION/CHANGELOG; rollback anchor lookup breaks |
| Wave-3 "insert above" pointer (line 108) | `Insert ABOVE [0.30.1]` | `Insert ABOVE [0.31.0]` | guidance text drift; operator confusion at deploy |
| Wave-3 block headline (line 113) | `[0.31.0] - 2026-06-13` | `[0.32.0] - 2026-06-13` | colliding version with Wave-2; CHANGELOG would have two entries claiming 0.31.0 |
| Wave-3 deploy commands (line 155, 166) | `0.31.0` / `v0.31.0` | `0.32.0` / `v0.32.0` | same |

## Root cause

Cycle-5 drift-check operated on the version sequence table only. The prestaged blocks below — which contain the actual paste-target content — were not re-aligned in the same cycle. The two artifacts diverged silently because:

1. The table is at the top of the file, immediately visible to a reviewer.
2. The blocks are below ``` markdown ``` fences (now wrapped in `<!-- audit-skip-block -->` per cycle 9), so they don't contribute to the audit's normal markdown link-resolution surface.
3. The prestaged-block content is paste-target-relative — easy to forget the headlines themselves are also subject to drift.

Cycle-9 added the `<!-- audit-skip-block -->` markers, which silenced the now-correct repo-root link refs but had no effect on the version-string drift inside the block.

## Action taken

Edited `wave-2-wave-3-changelog-entries-prestaged.md`:
- Wave-2 block headline `0.30.1` → `0.31.0` (line 41)
- Wave-2 deploy `echo "0.30.1"` → `echo "0.31.0"` (line 89)
- Wave-2 deploy `git tag -a v0.30.1` → `git tag -a v0.31.0` (line 98)
- Wave-3 insert pointer `[0.30.1]` → `[0.31.0]` (line 108)
- Wave-3 block headline `0.31.0` → `0.32.0` (line 113)
- Wave-3 deploy `echo "0.31.0"` → `echo "0.32.0"` (line 155)
- Wave-3 deploy `git tag -a v0.31.0` → `git tag -a v0.32.0` (line 166)

Wave-1 prestaged file: re-checked. `0.30.0` consistently used; no drift. ✅

## Note on Wave-3 single-vs-two-commit nuance

The table at the top of `wave-2-wave-3-changelog-entries-prestaged.md` lists Wave-3 as **two commits** (Lever B at `0.32.0`; Lever C at `0.33.0`). The prestaged Wave-3 block bundles both into a **single commit** at `0.32.0`. Both paths are valid per the file's disclaimer:

> If the operator chooses different sub-versions [...] update this doc + the actual deploy commit. The pre-staged blocks below assume the planned sequence.

The bundled-block convention represents the simpler all-in-one path. If operator picks two-commit path at deploy time, the Lever C commit takes the next minor (`0.33.0`); the prestaged block currently optimizes for the bundled path.

## Process improvement

For future drift-checks: when refreshing the version sequence table, **also grep the same file** for `echo "X.Y.Z"`, `git tag -a vX.Y.Z`, and `## [X.Y.Z]` patterns. The table is the source of truth; blocks must mirror.

Concrete: add to `scripts/doc_xref_audit.py` (or sibling): a per-prestaged-block version-consistency check that asserts headline `[X.Y.Z]` matches `echo "X.Y.Z"` and `git tag -a vX.Y.Z` within the same fenced block. Out of scope for this cycle; queued as low-priority follow-up.

## Cross-links

- `docs/governance/2026-05-05-changelog-drift-check.md` — cycle-5 original
- `docs/governance/wave-1-changelog-entry-prestaged.md` — Wave-1 (clean post-cycle-9 annotation)
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` — Wave-2/3 (post-cycle-10 fix)
- `scripts/doc_xref_audit.py` — cycle-9 improvements; out-of-scope follow-up noted above
