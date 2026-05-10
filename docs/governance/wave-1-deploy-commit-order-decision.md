# Wave-1 deploy — commit-order decision

**Drafted:** 2026-05-04 (during PROFIT-PHASE2-001 soak; pre-stage operator decision aid)
**Resolves:** rehearsal checklist `post-soak-close-rehearsal-checklist.md` §5 — left "single bundled commit vs per-feature commit" as operator preference. This spec recommends per-feature; rehearsal §5 should reference this doc as the locked recommendation.

## TL;DR

**Recommended: per-feature commit (six commits), NOT a single bundle.** Bisect-friendly rollback granularity outweighs the (small) deploy-day overhead.

## The choice

Wave-1 lands six features on the same operator-day:

1. PROFIT-OBS-005 cooldown sentinel-default fix
2. PROFIT-MATCH-001 (B′) token-guard refinement
3. PROFIT-OBS-003 BlendTask SKIPPED-emission
4. PROFIT-EXEC-002 series-correlation guard (NB: not Lever C; this is the FISA-class same-series correlation guard from EXEC-002)
5. PROFIT-GOV-003 governance_monitor.py fix
6. PROFIT-EDGE-004 Lever A.1 source-class classifier prerequisite

Two commit-shape options per the rehearsal checklist §5:

- **Option A (single bundle):** one commit landing all 6 features + VERSION bump + CHANGELOG entry + xfail-marker removals. Tag once.
- **Option B (per-feature):** six commits landing one feature each, each removing its own xfail markers. Final commit bumps VERSION + CHANGELOG. Tag once on the final commit.

## Per-feature commit (Option B) — recommended

### Pros

- **Bisect granularity.** If a regression appears in production after Wave-1 lands, `git bisect` between commits resolves to a single feature in O(log 6) steps. With the bundled commit, bisect resolves to the bundle and the operator must manually identify the regressed feature by reading the diff.
- **Per-feature attribution.** Each commit's CI run (pytest + ruff) attests to that single feature passing in isolation. Bundled CI runs only attest to the joint state — a feature that passes in isolation but breaks under interaction with another feature would be hidden.
- **Per-feature revert.** If the operator wants to roll back ONE feature post-deploy (e.g., OBS-005 was wrong), `git revert <hash>` is mechanical. Bundle revert pulls back all six.
- **PR-review clarity.** Self-review (or any future Codex-equivalent review) reads each feature in isolation; less cross-cutting cognitive load.

### Cons

- **6× CI runs.** Each commit triggers full pytest + ruff. Locally ~3 s × 6 = 18 s; CI ~30 s × 6 = 3 min. Negligible.
- **Operator focus required for 6 commits.** Each commit must include the feature's prod code, the test xfail-marker removals, and any localised CHANGELOG note. Operator could rush and miss a marker.
- **Final VERSION bump must be the LAST commit.** Per `~/.claude/rules/release_versioning.md` and the project-local CLAUDE.md: "Trigger: when bumping VERSION. Stage VERSION first; the hook handles README. Add a CHANGELOG.md entry in the same commit." This means VERSION goes in commit 6 only, with CHANGELOG covering all 6 features.

### Recommended commit sequence

| commit | scope | xfail markers removed |
|---|---|---|
| 1 | PROFIT-OBS-005 cooldown sentinel-default | `tests/test_executor.py` — `_OBS005_XFAIL_REASON` declaration + 5 decorators (lines 1052, 1071, 1086, 1100, 1139) |
| 2 | PROFIT-MATCH-001 (B′) token-guard | `tests/test_market_matcher.py` — `_MATCH001_XFAIL_REASON` declaration + 7 decorators (lines 502, 537, 605, 650, 722, 794, 803) |
| 3 | PROFIT-OBS-003 BlendTask SKIPPED-emission | `tests/test_blend_task.py` — `_OBS003_XFAIL_REASON` declaration + 4 decorators (lines 546, 627, 718, 783) |
| 4 | PROFIT-EXEC-002 series-correlation guard | `tests/test_blend_task.py` — `_EXEC002_XFAIL_REASON` declaration + 3 decorators (lines 895, 917, 989) |
| 5 | PROFIT-GOV-003 governance_monitor.py fix | `tests/test_governance_monitor.py` — `_GOV_MONITOR_XFAIL_REASON` declaration + 7 decorators (lines 143, 161, 179, 208, 235, 265, 291) |
| 6 | PROFIT-EDGE-004 Lever A.1 classifier prerequisite + VERSION bump 0.30.0 + CHANGELOG entry + tag `v0.30.0` | `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1` — `_LEVER_A_A1_XFAIL_REASON` declaration + 6 decorators (lines 1602, 1607, 1615, 1620, 1628, 1633) |

**Note (2026-05-06 cycle-11 update):** earlier drafts of this table referenced test class names like `TestObs005CooldownSentinelDefault`, `TestMatch001TokenGuardRefinement`, etc. Those classes do not exist — markers are decorators on plain functions, keyed off spec-named `_<SPEC>_XFAIL_REASON` constants. Line numbers + constant names verified against tree HEAD 2026-05-06.

The order above lands MATCH-001 + OBS-003 + EXEC-002 in the middle (the highest-impact features) so a regression detected at commits 7+ bisects to the most-suspect feature first. OBS-005 + GOV-003 are observability / governance fixes with low blast radius — landing first or last is interchangeable. Lever A.1 lands LAST because it bundles the VERSION bump (cheapest commit conceptually; safest to combine with the version-bump ceremony).

## Bundle commit (Option A) — not recommended

### Pros

- **One commit to review.** Operator + CI run once; minor wall-clock saving on deploy day.
- **Atomic rollback.** Revert is one operation that returns to pre-Wave-1 state.

### Cons

- **Lossy bisect.** A future regression cannot be triaged faster than reading the bundle diff.
- **Joint CI.** A feature that breaks only in interaction is invisible until production data shows it.
- **VERSION-bump scope ambiguity.** Bundle has 6 feature changes + version bump in one commit; the project rule "bump VERSION in the same commit as shipped behaviour" is over-satisfied (the rule is satisfied with one of the 6 feature commits doing it; bundling 6 with VERSION conflates the granularity).

## Decision: lock per-feature

**Rehearsal checklist §5 should be updated to remove the "either is defensible" wording and direct operator to the per-feature sequence in this doc.** Bundle stays as a fallback only if deploy-day pressure is high (e.g., an unrelated incident requires fast Wave-1 close).

## Cross-links

- `docs/governance/post-soak-close-rehearsal-checklist.md` §5 — currently says "either choice is defensible"; needs update referencing this doc
- `docs/_archive/governance/wave-1-changelog-entry-prestaged.md` — pre-staged CHANGELOG block (ARCHIVED Stream G R49)
- `docs/governance/post-soak-rollback-runbook.md` — incident-response runbook (assumes per-feature granularity)
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation
- `~/.claude/rules/release_versioning.md` + project CLAUDE.md — VERSION-bump conventions
