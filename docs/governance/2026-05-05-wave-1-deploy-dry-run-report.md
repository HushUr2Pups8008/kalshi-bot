# Wave-1 deploy dry-run — report

**Generated:** 2026-05-05 (during PROFIT-PHASE2-001 wind-down; pre-§8.5.1 close)
**Methodology:** Isolated git worktree (`.claude/worktrees/agent-a6863af4`, branch `worktree-agent-a6863af4`). Assembled the 6 per-feature Wave-1 commits in the order locked by `docs/governance/wave-1-deploy-commit-order-decision.md`. NOT pushed; soak invariant preserved.
**Tool:** `Agent(subagent_type='general-purpose', isolation='worktree')` — main repo's HEAD untouched throughout.

## TL;DR

**All 6 commits landed cleanly** in the spec-locked sequence. Final pytest sweep: **1543 pass / 6 skip / 31 xfail / 7 fail** (Mac Studio environment artifacts, not regressions; verified by re-running pre-Wave-1 commit `58c8f7e` and reproducing the same 7 failures). Ruff clean. README ↔ VERSION parity OK after the v0.30.0 bump.

## Commit shape (in worktree, NOT pushed)

```
5828ad2  feat(main): PROFIT-EDGE-004 Lever A.1 source-class classifier + v0.30.0
e3d4e8d  fix(governance_monitor): PROFIT-GOV-003 path + type-set membership
9d6cce3  feat(blend_task): PROFIT-EXEC-002 series-correlation guard
921c275  feat(blend_task): PROFIT-OBS-003 SKIPPED emission for blocked-reason path
edf38c1  fix(market_matcher): PROFIT-MATCH-001 (B') token-guard refinement
0531367  fix(executor): PROFIT-OBS-005 cooldown sentinel-default → -inf
```

## 7 spec ambiguities surfaced (fix in main BEFORE Day-7 deploy)

These are deploy-day blockers if not resolved. Severity-ordered:

### F1 (Watch) — EXEC-002 spec filename mismatch

`docs/governance/wave-1-deploy-commit-order-decision.md` step 4 references `2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` for EXEC-002 implementation, but the actual EXEC-002 same-series guard spec is `2026-05-03-exec-002-series-correlation-guard-design.md`. Lever C is the cross-series guard (Wave 3); EXEC-002 is the same-series guard (Wave 1). The dry-run agent caught this; the prompt note had flagged it. **Fix:** correct the commit-order doc reference.

### F2 (Watch) — OBS-005 xfail class name typo

Commit-order doc says `TestObs005CooldownSentinelDefault`; actual class in `tests/test_executor.py` is `TestCooldownSentinelOBS005`. **Fix:** correct the doc.

### F3 (Blocker) — OBS-003 test fixture bugs

Two pre-loaded OBS-003 harness tests have bugs that prevent xpass-flip independent of prod code:
1. Bound-method `is`-comparison: `obj.method is obj.method` returns `False` in Python (each access creates a new bound-method object). Test relied on this assumption.
2. Missing `now=` pin against fixture-dated 2026-04-18 evidence: default wall-clock-now triggers G6 recency block before reaching the OBS-003 path.

**Fix:** test-side only; no prod semantics change. Update the harness in main.

### F4 (Blocker) — EXEC-002 test fixture bugs

`_market_for_series` factory missing `status` and `regime_weights`; 4 async tests missing `now=` pin (G6 recency masks EXEC-002 behavior). **Fix:** test-side only; update harness in main.

### F5 (Watch) — MATCH-001 (B′) inverted-semantics impact

Spec §6 step 2 directs updating pre-existing tests that relied on binary `_token_not_in_ticker`. The dry-run updated 5 tests in `tests/test_market_matcher.py` to reflect the new "all overlap tokens in ticker → suppress" semantics. Side effect: weak cross-topic candidates (e.g. KXPSL on Trump headlines) now reach top-3 in the matcher; downstream gates still keep them out of actionable trades. The dry-run also updated `tests/test_simulations_smoke.py::test_match_audit_kxpsl_does_not_match_geo_news` with a bounded leakage allow-list per spec §8 acceptance ("canonical anchor in top-3 ≥ 0.06" preserved; matcher-tightening tracked as EDGE-004 follow-up).

**Fix:** apply the test updates to main pre-deploy. The matcher-tightening follow-up is tracked separately.

### F6 (Watch) — EDGE-004 Lever A.1 counterfactual harness alias misnaming

`tests/test_lever_a1_classifier_counterfactual.py` aliased `classify_pre_fix = main._source_class_for_evidence`. Once Lever A.1 lands, that alias is misnamed — production now equals post-fix. Dry-run replaced the import with a hardcoded `classify_pre_fix` reference (mirroring pre-Wave-1 source) so the historical pre→post delta tests remain meaningful, and renamed the prod-import to `classify_production` per spec §5 acceptance.

**Fix:** apply the same harness rename to main pre-deploy.

### F7 (Note) — Bothealth.sh markers stay xfail

Two `_BOTHEALTH_OBS003_XFAIL_REASON` markers in `tests/test_obs003_skipped_stream_synthesis.py` STAY strict-xfail (bothealth.sh aggregator update is a downstream OBS-003 §6-step-4 step, not part of the BlendTask emission spec). Dry-run correctly preserved. **No fix; informational.**

## What this means for Day-7 deploy

The dry-run validates the per-feature commit-order decision (`e178af6`) and the pre-staged CHANGELOG block (`75a3e39`). Deploy-day operator can:

1. Cherry-pick the 6 commits' diffs from the worktree as reference (or implement directly per the specs — the dry-run is a recipe, not the canonical change).
2. Apply F1-F6 fixes to main BEFORE the deploy commits land. F3 + F4 are Blockers; F1 + F2 + F5 + F6 are Watches but should still be cleaned up.
3. The worktree itself stays as a verification artifact — operator can re-run pytest in the worktree at any time without affecting main.

## Next-cycle action items (Claude / Codex)

- Apply F1-F2 commit-order doc corrections (Claude, mechanical)
- Apply F3-F4 test-fixture fixes (either agent; mechanical)
- Apply F5 test updates to main (either agent; cross-references the spec)
- Apply F6 harness rename to main (either agent; mechanical)
- Codex: review the dry-run's 6 commits independently for spec compliance + xfail-marker correctness

## Cross-links

- `docs/governance/wave-1-deploy-commit-order-decision.md` — locked sequence
- `docs/governance/wave-1-changelog-entry-prestaged.md` — pre-staged 0.30.0 block
- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — §8.5.1 gates
- Worktree: `.claude/worktrees/agent-a6863af4` / branch `worktree-agent-a6863af4` (do NOT push)
