# Wave-1 deploy — pre-staged per-commit messages

**Status:** pre-stage; copy each `git commit -m` block at deploy time. Lock per `wave-1-deploy-commit-order-decision.md` (per-feature, 6 commits, NOT bundle).
**Drafted:** 2026-05-06 (cycle 10).
**Companion:** `wave-1-deploy-commit-order-decision.md` (sequence) + `wave-1-changelog-entry-prestaged.md` (CHANGELOG content lands in commit 6 only).

## Drift note (load-bearing)

`wave-1-deploy-commit-order-decision.md` lines 45-50 + `wave-1-changelog-entry-prestaged.md` "Removed `pytest.mark.xfail` markers" section reference test class names that **do not exist** in the live tests:

| doc-claimed class | actual location |
|---|---|
| `TestObs005CooldownSentinelDefault` | actual: `TestCooldownSentinelOBS005` (test_executor.py:1043) — 5 markers |
| `TestMatch001TokenGuardRefinement` | actual: `TestSuppressionTokenGuardMATCH001` (test_market_matcher.py:784) PLUS `TestLowQualityMatchSuppression` (line 459) hosts more `_MATCH001_XFAIL_REASON` markers — total 7 markers |
| `TestObs003SkippedEmission` | NOT a class — markers are 4 plain functions in test_blend_task.py keyed off `_OBS003_XFAIL_REASON` |
| `TestExec002SeriesCorrelationGuard` | NOT a class — markers are 3 plain functions in test_blend_task.py keyed off `_EXEC002_XFAIL_REASON` |
| `TestGov003Fix` | NOT a class — markers are 7 plain functions in test_governance_monitor.py keyed off `_GOV_MONITOR_XFAIL_REASON` |
| `TestSourceClassClassifierLeverA1` | actual: `TestSourceClassClassifierLeverA1` (test_main_pipeline.py:1595) ✅ — 6 markers |

The grep-friendly path forward is the spec-keyed constant. Each commit removes:
- the `_<SPEC>_XFAIL_REASON = (...)` declaration
- every `@pytest.mark.xfail(reason=_<SPEC>_XFAIL_REASON, strict=True)` decorator referring to it

Verified marker counts (`grep -c "_<SPEC>_XFAIL_REASON" tests/*.py` 2026-05-06):

| spec | file | markers | constant |
|---|---|---:|---|
| OBS-005 | tests/test_executor.py | 5 | `_OBS005_XFAIL_REASON` |
| MATCH-001 | tests/test_market_matcher.py | 7 | `_MATCH001_XFAIL_REASON` |
| OBS-003 | tests/test_blend_task.py | 4 | `_OBS003_XFAIL_REASON` |
| EXEC-002 | tests/test_blend_task.py | 3 | `_EXEC002_XFAIL_REASON` |
| GOV-003 | tests/test_governance_monitor.py | 7 | `_GOV_MONITOR_XFAIL_REASON` |
| Lever A.1 | tests/test_main_pipeline.py | 6 | `_LEVER_A_A1_XFAIL_REASON` |

Total: 32 markers across 5 files.

Follow-up: cycle-10/-11 should fix `wave-1-deploy-commit-order-decision.md` + `wave-1-changelog-entry-prestaged.md` "removed markers" section to use these names. Out of scope for this file's drafting — flagging only.

## Commit 1 — PROFIT-OBS-005 cooldown sentinel-default

```bash
git commit -m "$(cat <<'EOF'
fix(executor): PROFIT-OBS-005 cooldown sentinel-default — None semantics

trading/executor.py:208 + 276 — return None for missing keys instead
of 0.0 (indistinguishable from "cooldown just expired"). Callers
updated to handle None. Spec:
docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md

Removes 5 strict-xfail markers in tests/test_executor.py:
  _OBS005_XFAIL_REASON declaration + 5 decorators (line 1052+)

Drops PAPER_TICKER_COOLDOWN=0 / LIVE_TICKER_COOLDOWN=0 stubs from
tests/conftest.py:_ci_stub_env per spec §7 acceptance #3.

Co-Authored-By: <agent>
EOF
)"
```

## Commit 2 — PROFIT-MATCH-001 (B') token-guard refinement

```bash
git commit -m "$(cat <<'EOF'
fix(market_matcher): PROFIT-MATCH-001 (B') token-guard — substring path

analysis/market_matcher.py:_meets_suppression_criteria — switch from
pre-fix _token_not_in_ticker (set-difference) to post-fix
_has_supporting_non_ticker_token (substring containment) per spec
§5.1 option (a). Pre-deploy validation:
- scripts/simulations/match001_bprime_anchor_sizing.py: 0/5 canonical
  suppressed; 1,076 keys in scope
- scripts/simulations/match001_bprime_false_negative_audit.py: 0
  likely false negatives

Spec:
docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md

Removes 7 strict-xfail markers in tests/test_market_matcher.py:
  _MATCH001_XFAIL_REASON declaration + 7 decorators (lines 502, 537,
  605, 650, 722, 794, 803). Updates pre-existing
  TestLowQualityMatchSuppression cases that depended on pre-fix
  predicate behaviour per spec §6 step 2.

Co-Authored-By: <agent>
EOF
)"
```

## Commit 3 — PROFIT-OBS-003 BlendTask SKIPPED-emission

```bash
git commit -m "$(cat <<'EOF'
feat(blend_task): PROFIT-OBS-003 SKIPPED-emission

tasks/blend_task.py — add _emit_skipped(reason, signal_meta) and
invoke before each blocked-reason early return. Forwarded via the
existing BlendDecisionLogger injection point. Enables Lever B G1
attribution + post-OBS-003 SKIPPED-stream audit.

scripts/bothealth.sh — surface Skipped-trades section + accept
SKIPPED_LOG_OVERRIDE env (F2 follow-up).

Spec:
docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md

Removes 4 strict-xfail markers in tests/test_blend_task.py:
  _OBS003_XFAIL_REASON declaration + 4 decorators (lines 546, 627,
  718, 783).

Co-Authored-By: <agent>
EOF
)"
```

## Commit 4 — PROFIT-EXEC-002 series-correlation guard

```bash
git commit -m "$(cat <<'EOF'
feat(blend_task): PROFIT-EXEC-002 series-correlation guard

tasks/blend_task.py — suppress correlated-burst trades within a
single market series within cfg.series_correlation_window_seconds.
Env-revert path: SERIES_CORRELATION_WINDOW_SECONDS=0 disables.
Pre-deploy archive replay: 2 FISA-class paper-trade bursts suppressed.

NOTE: This is the SAME-series guard (Wave 1 / EXEC-002), not the
CROSS-series guard (Wave 3 / Lever C — separate spec).

Spec:
docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md

Removes 3 strict-xfail markers in tests/test_blend_task.py:
  _EXEC002_XFAIL_REASON declaration + 3 decorators (lines 895, 917,
  989).

Co-Authored-By: <agent>
EOF
)"
```

## Commit 5 — PROFIT-GOV-003 governance_monitor.py fix

```bash
git commit -m "$(cat <<'EOF'
fix(governance_monitor): PROFIT-GOV-003 — KALSHI_HOME path + type-set membership

scripts/governance_monitor.py — two hunks:
1. Default logfile + overrides resolve to repo root regardless of
   KALSHI_HOME env var.
2. Aggregator counts GOVERNANCE_DECISION_PARSE_ERROR /
   GOVERNANCE_DECISION_VALIDATION_ERROR / batch_aborted=True via
   type-set membership instead of fragile string equality.

Soak-shadow-validated 2026-05-01 → 2026-05-08 with 0 KILL_SWITCH /
VALIDATION_ERROR / batch_aborted across 14 d window.

Spec:
docs/_archive/specs/2026-05-03-governance-monitor-fix-design.md (ARCHIVED Stream G R19)

Removes 7 strict-xfail markers in tests/test_governance_monitor.py:
  _GOV_MONITOR_XFAIL_REASON declaration + 7 decorators (lines 143,
  161, 179, 208, 235, 265, 291).

Co-Authored-By: <agent>
EOF
)"
```

## Commit 6 — PROFIT-EDGE-004 Lever A.1 + VERSION 0.30.0 + CHANGELOG + tag

This is the **final** Wave-1 commit. Bundles the Lever A.1 classifier-prerequisite change with the VERSION bump and the full CHANGELOG block. Project pre-commit hook auto-syncs README badges when VERSION is staged.

```bash
echo "0.30.0" > VERSION
git add VERSION  # pre-commit hook syncs README

# Apply Lever A.1 classifier patch per spec §2.1 + §2.2:
#   main.py:_source_class_for_evidence — token additions for
#   official + specialist classes
git add main.py

# Remove 6 strict-xfail markers + _LEVER_A_A1_XFAIL_REASON declaration
# in tests/test_main_pipeline.py (lines 1589, 1602, 1607, 1615, 1620,
# 1628, 1633) — same hunk
git add tests/test_main_pipeline.py

# Paste Wave-1 CHANGELOG block from
# docs/governance/wave-1-changelog-entry-prestaged.md ABOVE the
# current ## [0.29.59] heading
git add CHANGELOG.md

# Confirm clean
.venv/bin/python -m pytest -q
.venv/bin/ruff check .

git commit -m "$(cat <<'EOF'
feat(main): PROFIT-EDGE-004 Lever A.1 classifier prerequisite + Wave-1 close

main.py:_source_class_for_evidence — token additions so existing
official / specialist sources classify correctly. Classifier-only
change; 0 archive trade-rate lift expected per Codex 2026-05-03
archive replay.

Closes Wave-1 base stack post-soak deploy:
- 0.29.59 → 0.30.0 (minor bump; multi-feature behavioural)
- VERSION bumped; README synced via pre-commit hook
- CHANGELOG.md entry added per
  docs/governance/wave-1-changelog-entry-prestaged.md
- 6 strict-xfail markers removed in tests/test_main_pipeline.py:
    _LEVER_A_A1_XFAIL_REASON declaration + 6 decorators (lines
    1602, 1607, 1615, 1620, 1628, 1633)

Spec:
docs/_archive/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md (ARCHIVED Stream G R28)

Co-Authored-By: <agent>
EOF
)"

git tag -a v0.30.0 -m "Wave-1 base-stack post-soak deploy"
git push origin main --tags
```

## Validation between commits

After EACH commit (1-6):

```bash
.venv/bin/python -m pytest -q   # full suite must pass; just-removed xfails must xpass
.venv/bin/ruff check .          # lint clean
```

If pytest fails between commits, halt and follow `post-soak-rollback-runbook.md` §2 (emergency revert of the just-landed commit).

## Cross-links

- `docs/governance/wave-1-deploy-commit-order-decision.md` — sequence LOCK (correct path forward; class-name refs need cycle-10/-11 fix per drift note above)
- `docs/governance/wave-1-changelog-entry-prestaged.md` — CHANGELOG content for commit 6 (same drift)
- `docs/governance/post-soak-close-rehearsal-checklist.md` — operator checklist
- `docs/governance/post-soak-rollback-runbook.md` — incident response
- `docs/_archive/governance/2026-05-06-pre-wave1-deploy-dry-run-rehearsal.md` — cycle-10 dry-run audit (ARCHIVED Stream G R5)
