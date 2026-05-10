# Wave-1 commit-by-commit acceptance matrix

**Type:** consolidation reference (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator at each Wave-1 commit fire-time. Single page consolidating per-commit verification + acceptance + hand-off rules.
**Companion:** `2026-05-05-wave-1-fire-time-per-commit-checklist.md` (operational steps); `wave-1-post-deploy-observation-plan.md` (14-row regression watch).

## Wave-1 commit chain

| # | spec | code touched | xfail markers removed | spec-harness file |
|---|---|---|---|---|
| 1 | OBS-005 cooldown sentinel | `trading/executor.py:208,276` | `tests/test_executor.py::TestObs005CooldownSentinelDefault` (multiple) | `tests/test_executor.py` |
| 2 | MATCH-001 (B') token-guard | `analysis/market_matcher.py:_meets_suppression_criteria` | `tests/test_market_matcher.py::TestMatch001TokenGuardRefinement` (multiple) | `tests/test_market_matcher.py` |
| 3 | OBS-003 SKIPPED-emission | `tasks/blend_task.py:emit_skipped()` + call sites | `tests/test_blend_task.py::TestObs003SkippedEmission` (multiple) | `tests/test_blend_task.py` |
| 4 | EXEC-002 series-correlation | `tasks/blend_task.py:series_correlation_in_window` + `config.py:series_correlation_window_seconds` | `tests/test_executor.py::TestExec002SeriesCorrelationGuard` (multiple) | `tests/test_executor.py` |
| 5 | GOV-003 governance_monitor.py fix | `governance/monitor.py` (2 hunks) | `tests/test_governance_monitor.py::TestGov003Fix` (multiple) | `tests/test_governance_monitor.py` |
| 6 | EDGE-004 Lever A.1 classifier | `main.py:_source_class_for_evidence` (2 hunks) | `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1` (6 markers) + `tests/test_lever_a1_classifier_counterfactual.py` | `tests/test_main_pipeline.py` |

## Per-commit acceptance criteria (smoke + 24h watch + 14-day deferred)

### Commit 1: OBS-005 cooldown sentinel default fix

**Spec:** `docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md`.

| stage | criterion | source |
|---|---|---|
| Smoke (≤ 5min) | `pytest -q tests/test_executor.py::TestObs005CooldownSentinelDefault`: 0 failed | spec-harness re-run |
| 24h watch | First OPPORTUNITY post-deploy: `cooldown_state` field uses None for first-time keys (not 0.0 sentinel) | trade-log inspection per `wave-1-post-deploy-observation-plan.md` row 1 |
| 24h watch | No `cooldown_state == "0.0"` for any never-traded ticker | trade-log inspection row 2 |
| Hand-off → commit 2 | All 24h watch rows clean | gate per `2026-05-05-wave-1-fire-time-per-commit-checklist.md` cadence matrix |

**Rollback trigger:** sentinel-leak signature (`cooldown_state == "0.0"` for never-traded ticker). Code revert per `post-soak-rollback-runbook.md` §4.1.

### Commit 2: MATCH-001 (B') token-guard refinement

**Spec:** `docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md`.

| stage | criterion | source |
|---|---|---|
| Smoke | `pytest -q tests/test_market_matcher.py::TestMatch001TokenGuardRefinement`: 0 failed | spec-harness re-run |
| 24h watch | MATCH_DIAGNOSTIC suppression-rate stable within ±30% of pre-deploy (current corpus has 175 MATCH_SUPPRESSED / 450 MATCH_DIAGNOSTIC = ~38% suppression-rate) | observation plan row 3 |
| 24h watch | No `KXPSL*` ticker emits `low_match_quality=true` (allow-list bypass) | observation plan row 4 |
| Hand-off → commit 3 | Both watch rows clean | cadence matrix |

**Rollback trigger:** suppression-rate drift > ±30% OR allow-list bypass. Code revert per `post-soak-rollback-runbook.md` §4.2.

### Commit 3: OBS-003 BlendTask SKIPPED-emission

**Spec:** `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md`.

| stage | criterion | source |
|---|---|---|
| Smoke | `pytest -q tests/test_blend_task.py::TestObs003SkippedEmission`: 0 failed | spec-harness re-run |
| 24h watch | At least one SKIPPED record emits in the 24h window IF a candidate fails the readiness gate | observation plan row 5; **NOTE per `2026-05-05-pre-wave1-baseline-interpretation.md`: post-cutover has 0 OPPORTUNITY events; SKIPPED requires a candidate to first reach BlendTask. Baseline-rebased rule: ≥ 1 BLEND_DECISION event in 7d post-deploy** |
| 24h watch | All SKIPPED records have `reason` populated; `reason` ∈ {G1_blended_confidence, G2_diversity, G3_disagreement, G4_regime_confidence, G5_drift_suspect, G6_recency} | observation plan rows 6+7 |
| Hand-off → commit 4 | All watch rows clean OR BLEND_DECISION emerging within 7d | cadence matrix |

**Rollback trigger:** SKIPPED emits with `reason=null` OR unknown reason. Code revert per `post-soak-rollback-runbook.md` §4.3.

### Commit 4: EXEC-002 series-correlation guard

**Spec:** `docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md`.

| stage | criterion | source |
|---|---|---|
| Smoke | `pytest -q tests/test_executor.py::TestExec002SeriesCorrelationGuard`: 0 failed; `cfg.series_correlation_window_seconds` exists with default 3600 | spec-harness + harness expansion test |
| 24h watch | Suppression count for `series_correlation_in_window`: 0-5/day expected (low because trade-rate is currently 0); env-revert path tested (`launchctl getenv SERIES_CORRELATION_WINDOW_SECONDS` returns 3600) | observation plan rows 8+9 |
| Hand-off → commit 5 | Both watch rows clean | cadence matrix |

**Rollback trigger:** suppression count > 5/day (under-suppression) OR env var lost. Env-revert via `launchctl setenv SERIES_CORRELATION_WINDOW_SECONDS 0` per `post-soak-rollback-runbook.md` §3 (preferred) OR §4.4 code revert.

### Commit 5: GOV-003 governance_monitor.py fix

**Spec:** `docs/_archive/specs/2026-05-03-governance-monitor-fix-design.md` (ARCHIVED Stream G R19; CLOSED via commit `e3d4e8d`).

| stage | criterion | source |
|---|---|---|
| Smoke | `pytest -q tests/test_governance_monitor.py::TestGov003Fix`: 0 failed; `pytest -q tests/test_governance_parse_error_regression.py` (Codex cycle 6) | spec-harness + cycle-6 regression coverage |
| 24h watch | 0 KILL_SWITCH; 0 VALIDATION_ERROR; 0 batch_aborted | observation plan rows 10+11 |
| 24h watch | Cycle cadence: max gap ≤ 3h | observation plan row 12 |
| Hand-off → commit 6 | All 3 watch rows clean | cadence matrix |

**Rollback trigger:** ANY KILL_SWITCH / VALIDATION_ERROR / batch_aborted post-deploy. STOP per `2026-05-05-kill-switch-fire-procedure-runbook.md`. Code revert per `post-soak-rollback-runbook.md` §4.5.

### Commit 6: EDGE-004 Lever A.1 classifier (FINAL — 48h watch)

**Spec:** `docs/_archive/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` (ARCHIVED Stream G R28; Wave-1 Commit 6 SHIPPED).

| stage | criterion | source |
|---|---|---|
| Smoke | `pytest -q tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1`: 0 failed; `pytest tests/test_lever_a1_classifier_counterfactual.py`: 0 failed | spec-harness re-run |
| **48h watch** (final commit; longer than 24h) | EVIDENCE_INGESTION source_class distribution: `analysis` class share doesn't drop below pre-deploy baseline | observation plan row 13 |
| 48h watch | Trade-rate stays ≤ 2× pre-deploy baseline (per Codex `8001a16` archive replay confirmation that A.1 is archive-neutral); pre-deploy baseline is 0/day so any post-deploy paper-trade is a feature, not regression | observation plan row 14; **rebased rule per cycle-5 baseline interpretation** |
| Hand-off → Wave-2 Branch A start | All watch rows clean for 48h | gate per `2026-05-05-wave-2-fire-time-per-commit-checklist.md` step 1 trigger |

**Rollback trigger:** classifier silently fails to relabel `analysis` class evidence OR trade-rate explodes (≥ 5×). Code revert per `post-soak-rollback-runbook.md` §4.6.

## VERSION sequence

Per `wave-2-wave-3-changelog-entries-prestaged.md` (refreshed cycle 5):

- Pre-Wave-1: 0.29.59
- Wave-1 commit 6: VERSION → 0.30.0; tag `v0.30.0` (per project convention; per-commit micro-tags optional)

## Hand-off matrix between commits

```
commit 1 OBS-005 (Fri 20:00Z) → 24h watch → 36h cadence → commit 2 MATCH-001 (Sun 08:00Z)
commit 2 → 24h watch → reset weekday cadence → commit 3 OBS-003 (Mon 18:00Z)
commit 3 → 36h watch → commit 4 EXEC-002 (Wed 06:00Z)
commit 4 → 36h watch → commit 5 GOV-003 (Thu 18:00Z)
commit 5 → 36h watch → commit 6 EDGE-004 A.1 (Sat 06:00Z)
commit 6 → 48h watch → Wave-2 Branch A start (Mon 18:00Z+)
```

Total Wave-1 wall-clock: 7-8 days from commit-1 fire to commit-6 watch-end.

## Cross-links

- `wave-1-deploy-commit-order-decision.md` — locked commit order (this matrix's source-of-truth)
- `wave-1-post-deploy-observation-plan.md` — 14-row regression watch (per-row criteria source)
- `2026-05-05-wave-1-fire-time-per-commit-checklist.md` — operational steps per commit
- `2026-05-05-wave-1-deploy-day-timing.md` — cadence rationale + UTC windows
- `2026-05-05-pre-wave1-baseline-interpretation.md` — 0-OPPORTUNITY-post-cutover finding (rebased rules)
- `wave-1-changelog-entry-prestaged.md` — VERSION + xfail-marker-removal table
- `post-soak-rollback-runbook.md` §4 — per-commit rollback procedures
- `2026-05-05-kill-switch-fire-procedure-runbook.md` — KILL_SWITCH response
