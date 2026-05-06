# 2026-05-06 pre-Wave-1 deploy dry-run rehearsal

**Type:** read-only walkthrough of `post-soak-close-rehearsal-checklist.md` against current pre-deploy repo state. Asserts each referenced command, file, line-number, and test class resolves cleanly. Surfaces gaps before the live deploy day.
**Drafted:** 2026-05-06 (cycle 10).
**Companion:** `docs/governance/post-soak-close-rehearsal-checklist.md` (the checklist under test).
**Prior:** `docs/governance/2026-05-04-day-4-mid-soak-confirmation.md` (mid-soak gate-check pattern).

## TL;DR

**All referenced artifacts resolve.** No missing files, no stale line refs, no renamed test classes. One structural-ambiguity note (bundled vs per-day deploy paths — already documented in the prestaged Wave-1 file).

Ready for deploy day. Operator can execute the checklist top-to-bottom without fix-before-fire patches.

## Per-section checks

### §0 T-minus-1 day

Pre-deploy state checks (recent commits, working tree clean, backup script). Read-only. ✅

### §1 Day 0 — OBS-005

| reference | resolves? | note |
|---|---|---|
| `tests/test_executor.py::TestCooldownSentinelOBS005` | ✅ line 1043 | 5-test class as expected |
| `trading/executor.py:208` (paper sentinel) | ✅ | currently `0.0` (pre-fix) |
| `trading/executor.py:276` (live sentinel) | ✅ | currently `0.0` (pre-fix) |
| `tests/conftest.py:_ci_stub_env` | ✅ line 22 | `PAPER_TICKER_COOLDOWN` / `LIVE_TICKER_COOLDOWN` stubs at line 57 (single var present; spec §7 acceptance #3 calls for both removed) |
| `2026-05-03-obs-005-cooldown-sentinel-fix-design.md` §2 | ✅ |  |

### §2 Day 1 — MATCH-001 (B')

| reference | resolves? | note |
|---|---|---|
| `scripts/simulations/match001_bprime_anchor_sizing.py` | ✅ |  |
| `tests/test_market_matcher.py::TestSuppressionTokenGuardMATCH001` | ✅ line 784 |  |
| `2026-05-03-match-001-token-guard-refinement-design.md` §5.1 | ✅ |  |
| `analysis/market_matcher.py:_meets_suppression_criteria` | ✅ line 645 |  |

### §3 Day 4 — OBS-003

| reference | resolves? | note |
|---|---|---|
| `scripts/simulations/obs003_skipped_stream_synthesis.py` | ✅ |  |
| `tasks/blend_task.py` | ✅ | OBS-003 hunk pre-deploy (no `_emit_skipped` yet) |
| `scripts/bothealth.sh` | ✅ | F2-update tracker for SKIPPED aggregator |
| `2026-05-03-obs-003-blendtask-skipped-emission-design.md` §2 + §5 | ✅ |  |

### §4 Day 6 — EXEC-002

| reference | resolves? | note |
|---|---|---|
| `2026-05-03-exec-002-series-correlation-guard-design.md` §9 | ✅ |  |
| env-var `SERIES_CORRELATION_WINDOW_SECONDS` | will wire at deploy | env-revert path; spec-driven |

### §5 Day 13 — Wave-1 closed; governance_monitor fix

| reference | resolves? | note |
|---|---|---|
| `docs/governance/wave-1-deploy-commit-order-decision.md` | ✅ | LOCKED 2026-05-04 — 6 per-feature commits |
| `python scripts/governance_monitor.py` | ✅ | runnable; Day-7 mid-soak script |
| `2026-05-03-governance-monitor-fix-design.md` §2 | ✅ |  |

### §6 Day 13 — Lever A.1 classifier patch

| reference | resolves? | note |
|---|---|---|
| `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1` | ✅ line 1595 |  |
| `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1PlusAnalysisBranch` | ✅ line 1669 |  |
| `scripts/simulations/lever_a1_classifier_counterfactual.py` | ✅ |  |
| `2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` | ✅ |  |

### §7 Day 14+ — Wave-2 Lever A.1+ first feed

Branch tree depends on observed Wave-1 trade-rate; refs resolve. ✅

### §8 Day 28+ — Wave-3 escalation

Refs to LOCK addenda (cycle-3 cycle): both LOCK files exist; both deploy hunks pre-loaded behind strict-xfail. ✅

## Structural ambiguity (already known)

§5 calls for **6 per-feature commits** (LOCKED 2026-05-04). The prestaged Wave-1 CHANGELOG block in `wave-1-changelog-entry-prestaged.md` is written for the **bundled all-in-one** path. Prestaged file disclaimer covers both paths:

> If the operator deploys Wave-1 in stages (one per day per the rehearsal checklist §1-§4 cadence), bump to `0.29.60` per stage and reserve `0.30.0` for the day-13 base-stack-closed commit.

Operator picks at deploy time. Both have prestaged content; per-day path uses the disclaimer guidance, bundled path uses the prestaged block as-is. No fix-before-fire required.

## Process notes

- Strict-xfail markers' removal-in-same-hunk is called out in 6/6 deploy steps. Cycle-10 Codex item 5 pre-stages these as `.patch` files under `docs/governance/wave-1-deploy-hunks/`.
- VERSION-bump + CHANGELOG.md update + tag are step-3 in every deploy section. README sync is automatic via pre-commit hook (per project CLAUDE.md).
- Rollback runbook (§4) cross-referenced from each deploy step's "if restart-trigger fires" branch. Rollback runbook tabletop (cycle-10 Claude item 7) confirmed 100% file-resolution.

## Cross-links

- `docs/governance/post-soak-close-rehearsal-checklist.md` — the checklist (under audit)
- `docs/governance/wave-1-deploy-commit-order-decision.md` — 2026-05-04 LOCK
- `docs/governance/wave-1-changelog-entry-prestaged.md` — prestaged Wave-1 block
- `docs/governance/post-soak-rollback-runbook.md` — rollback companion
- `docs/governance/2026-05-06-changelog-drift-check-cycle-10.md` — cycle-10 drift fix
