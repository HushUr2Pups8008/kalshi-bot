# Wave-2 deploy commit-order decision

**Type:** ambiguity resolution (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator scheduling Wave-2 deploys at ≥ 2026-05-18 post-Wave-1 stabilisation.
**Companion:** `docs/governance/wave-1-deploy-commit-order-decision.md` (Wave-1 analog); `2026-05-05-wave-2-a1plus-branch-decision-table.md`.

## TL;DR

Wave-2 lands AT MOST 2 commits in this order:

1. **Branch A start** — operator-tag only; NO code change.
2. **Branch C deploy** (only if Branch A returns 0 PAPER_TRADE in 14 d) — single-commit feature.

Plus parallel-discretion option-A geopolitics specialist deploy (single commit; same shape as Branch C).

## Locked commit order

### Wave-2 Step 1: Branch A start tag (NO CODE CHANGE)

**Type:** operator action only.
**Trigger:** Wave-1 commit 6 + 48 h post-deploy watch passes (≥ 2026-05-18).
**Action:** `git tag -a a1plus-branch-a-start-${UTC_DATE}` + debt-log entry.
**Validation window:** 14 d (passive observation per Wave-2 branch decision table).
**Rollback:** none needed; passive observation is operator-mind-state, not bot state.

### Wave-2 Step 2: Branch C deploy (only if Branch A returns 0 PAPER_TRADE)

**Type:** single behavioural commit.
**Trigger:** Branch A 14 d window concludes with 0 legal-niche PAPER_TRADE (≥ 2026-06-01).
**Action:** single commit per `docs/_archive/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` §"Deploy procedure" (ARCHIVED Stream G R37):
1. Edit `config.py:RSS_FEEDS` — add 1-2 selected URLs (Just Security primary + Lawfare secondary recommended).
2. Edit `main.py:_source_class_for_evidence` — add tokens for selected sources to bucket as `analysis` class.
3. Remove `pytest.mark.xfail` decorator from `tests/test_lever_a1plus_feed_config.py::test_vital_law_or_legal_analyst_feed_present_post_a1plus` AND from any test pinning specific URL selection per Codex's harness expansion (this cycle).
4. Bump VERSION 0.30.0 → 0.31.0 (single-feature minor bump per `docs/_archive/governance/wave-1-changelog-entry-prestaged.md` versioning, ARCHIVED Stream G R49).
5. Run pre-commit hook (auto-syncs README badges).
6. Single commit including all 5 changes.
7. Tag: `git tag -a v0.31.0`.
8. Push to origin; deploy + 24 h post-deploy regression watch + 14 d acceptance window.

**Validation window:** 14 d. Acceptance per Wave-2 branch decision table: ≥ 1 PAPER_TRADE w/ non-negative aggregate realized P&L.

**Rollback:** code revert (no env-var). Per `post-soak-rollback-runbook.md` shape: revert the single commit; no cascading complexity (Wave-2 has no follow-up commits to unwind).

### Wave-2 Step 3 (parallel-discretion): option-A geopolitics specialist

**Type:** single behavioural commit IF operator pursues parallelism.
**Trigger:** operator-discretion. Recommended deferred unless Branch C also stalls.
**Action:** same shape as Branch C; different feed URLs (war on the rocks / CSIS / ISW / CFR / Atlantic Council per `docs/_archive/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` §3.1, ARCHIVED Stream G R27).
**VERSION:** 0.31.0 if option-A first; 0.31.1 if after Branch C; 0.32.0 if part of a Branch-C-stall recovery.
**Validation:** 14 d. Acceptance: ≥ 1 PAPER_TRADE.
**Rollback:** code revert.

## Why this order is locked

**Branch A first** because it's free — passive observation has no deploy cost. If it succeeds, Branch C never deploys; if it fails, Branch C's empirical case is strengthened (legal-niche PAPER_TRADE doesn't surface from Google News passive query family).

**Branch C before option-A** because the legal-analyst class has historical PAPER_TRADE evidence (3/3 on the 13-day archive); option-A's geopolitics class has 0/18. Higher-EV move first.

**No bundled commit** — Branch A is non-code; Branch C is single-commit; option-A is single-commit. No bundling shape applicable.

## Inter-commit cadence

| step transition | minimum gap | recommended gap | rationale |
|---|---|---|---|
| Wave-1 commit 6 → Wave-2 Step 1 (Branch A tag) | 48 h post-deploy | 48 h | Wave-1 stabilisation buffer per Wave-1 timing recommendation |
| Branch A start → Branch C deploy | 14 d (Branch A window) | 14 d + 1-2 day decision slack | Branch A acceptance window must close before Branch C decision |
| Branch C deploy → option-A deploy (if pursued) | 14 d (Branch C window) | 14 d + 1-2 day decision slack | same logic |
| Branch A → option-A (parallel; if pursued) | 0 (concurrent) | 0 | parallelism mode |

## Operator decision points

1. **Branch A start trigger.** Operator decides "Wave-1 stabilised" verdict at ≥ 2026-05-18. Recommended: any 24 h window with all 14 wave-1-post-deploy-observation-plan rows clean.
2. **Branch C feed selection.** Per the feed-selection rubric. Operator finalizes at fire-time.
3. **option-A parallel vs serial.** Recommended serial (option-A only after Branch C stalls). Parallelism risks attribution muddle.
4. **VERSION bump strategy.** Recommended per-feature minor bumps (0.30.0 → 0.31.0 → 0.31.1 → 0.32.0). Alternative: major bump 0.30.0 → 1.0.0 if operator wants to mark "first edge-production feature post-PAPER" milestone — out of project versioning convention.

## Out of scope

- Wave-2 lookahead: Wave-3 commit order is covered in `2026-05-05-wave-3-deploy-day-timing.md`.
- Branch D nomenclature: covered in `2026-05-05-lever-d-nomenclature-cleanup-audit.md`.
- Per-feed RSS-probe protocol: covered in feed-selection rubric.

## Cross-links

- `docs/governance/wave-1-deploy-commit-order-decision.md` — Wave-1 analog
- `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — branch sequence + acceptance
- `docs/_archive/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` — Branch C feed selection (ARCHIVED Stream G R37)
- `docs/governance/2026-05-05-wave-2-deploy-day-timing.md` — Wave-2 timing recommendation
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` — pre-staged CHANGELOG entries
- `docs/_archive/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` — Branch C parent spec (ARCHIVED Stream G R32)
- `docs/_archive/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` — option-A parent spec (ARCHIVED Stream G R27)
