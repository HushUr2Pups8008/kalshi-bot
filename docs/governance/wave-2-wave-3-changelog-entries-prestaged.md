# Wave-2 + Wave-3 deploy — pre-staged CHANGELOG entries

> **🛑 HALTED PER IC §16 (cycle-11.5 strategic redirect, 2026-05-06).** Do NOT paste these blocks into `CHANGELOG.md`. The pre-staged content below was authored under the cycle-1-to-11 working assumption that Wave-2/3 would auto-deploy after Wave-1. That assumption no longer holds. Wave-2 (feed onboarding) and Wave-3 (Lever B/C) are blocked pending Cycle-12 replay harness output (see `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` and IC §16). The blocks remain in this file as documentation of what WAS planned, not as executable deploy artifacts. If replay evidence approves a Wave-2 candidate later, this file's headlines + version refs may need rewriting against the actual approved feature, not the speculative legal/geopolitics feeds spec'd here.

**Status:** HALTED PER IC §16 (was: pre-stage; copy into `CHANGELOG.md` at each Wave's close commit. Do not insert pre-soak-close — would lock version numbers prematurely.)
**Drafted:** 2026-05-04 (during PROFIT-PHASE2-001 soak); sibling to `wave-1-changelog-entry-prestaged.md`
**Rationale:** reduces deploy-day cognitive load. Operator copies the relevant block at each Wave close, fills in the actual VERSION value, runs the pre-commit hook to sync README badges, commits.

## Version sequence (planned — refreshed 2026-05-05 cycle 5 per drift-check F1)

| step | tracker | landing day | proposed VERSION |
|---|---|---|---|
| Wave-1 base stack | OBS-005 / MATCH-001 / OBS-003 / EXEC-002 / GOV-003 / Lever A.1 | 2026-05-08+ (§8.5.1 early-close path) or 2026-05-15+ (default) | 0.30.0 |
| Wave-2 Branch A start | passive Google News observe (NO code change; tag only) | 2026-05-18 (Wave-1 + 48h burn-in) | n/a (no bump) |
| Wave-2 Branch C deploy | open-RSS legal-analyst onboard (only if Branch A returns 0 PAPER_TRADE in 14d) | 2026-06-02+ | 0.31.0 |
| Wave-2 option-A deploy | geopolitics specialist (parallel-discretion or fallback) | 2026-05-18+ (parallel) or 2026-06-16+ (fallback) | 0.31.x or 0.32.0 |
| Wave-3 commit 1 (Lever B G1=0.04) | tunable gate change | 2026-06-17+ (only if Wave-2 stalls AND Branch D not fired) | 0.32.0 (or 0.33.0 if option-A landed) |
| Wave-3 commit 2 (Lever C cross-series) | new BlendTask gate; suppression-only | 2026-07-01+ (14 d after Wave-3 commit 1) | 0.33.0 (or 0.34.0) |

Wave-2 Branch C = minor bump (new feed onboarding + classifier branch addition). Wave-3 = minor bump per commit (each is single-feature).

**Cycle-3 LOCK references (load-bearing for Wave-3 deploy commits):**
- Lever B floor LOCKED at 0.04 / failsafe 0.08 / 2× ratio invariant per [`docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md`](../superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md).
- Lever C v1 LOCKED: §3.2 normalized hash / 3600s default window / record-after-gate-pass placement / INV-6 boundary attestation per [`docs/_archive/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md`](../_archive/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md) (ARCHIVED Stream G R33).

If the operator chooses different sub-versions (e.g., 0.30.0 → 0.30.1 → 0.30.2 with Lever B and Lever C as separate patch bumps), update this doc + the actual deploy commit. The pre-staged blocks below assume the planned sequence.

**Cycle-5 nomenclature note:** "option-C-Branch-C-fallback" used in earlier draft has been simplified to "Branch C" (canonical per cycle-3 nomenclature cleanup audit). Updated below.

## Wave-2 pre-staged CHANGELOG block

Insert ABOVE `## [0.30.0] - 2026-05-15` (after Wave-1 lands) once Wave-2 is ready:

> **Audit note:** `[text](docs/...)` links inside the fenced blocks below are
> repo-root-relative for `CHANGELOG.md` paste-target context. They resolve
> correctly once pasted into `CHANGELOG.md`. `scripts/doc_xref_audit.py` honors
> the `<!-- audit-skip-block -->` markers wrapping the fenced blocks below.

<!-- audit-skip-block: prestaged Wave-2 CHANGELOG content -->


```markdown
## [0.31.0] - 2026-05-22  (or 2026-05-23 — fill in actual deploy date)

### Added (PROFIT-EDGE-004 — Wave-2 first feed onboarding)

#### PROFIT-EDGE-004 Lever A.1+ — first specialist feed onboarding

Branch chosen at deploy time per [`docs/governance/edge-004-closure-path-tldr.md`](docs/governance/edge-004-closure-path-tldr.md) v2.1+:

- **Option-A** (specialist-geopolitics): added one of war on the rocks /
  CSIS / ISW / CFR / Atlantic Council to `config.py:RSS_FEEDS`. Spec:
  [`docs/_archive/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md`](docs/_archive/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md) (ARCHIVED Stream G R27).
  Companion classifier branch addition (`analysis` source-class) in
  `main.py:_source_class_for_evidence`. Strict-xfail markers removed:
  - `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1PlusAnalysisBranch` (6 markers)
  - `tests/test_lever_a1plus_feed_config.py::test_at_least_one_specialist_analyst_url_in_rss_feeds` (1 marker)

- **Option-C-Branch-C** (legal-analyst fallback): added one of Lawfare /
  Just Security / SCOTUSblog / Politico Legal to `config.py:RSS_FEEDS`.
  Spec: [`docs/_archive/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md`](docs/_archive/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md) (ARCHIVED Stream G R32).
  Companion `legal` source-class in `main.py:_source_class_for_evidence`
  + `_SOURCE_CLASS_QUALITY["legal"] = 0.65` in `analysis/evidence_scorer.py`.
  Strict-xfail markers removed:
  - `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1Plus15LegalBranch` (7 markers)
  - `tests/test_lever_a1plus1_5_evidence_scorer_legal_weight.py::test_legal_class_weight_set_to_spec_value` (1 marker)
  - `tests/test_lever_a1plus_feed_config.py::test_vital_law_or_legal_analyst_feed_present_post_a1plus` (1 marker)

NOTE: A.1+ DECISION POINT at Day-14 deploy was option-A vs option-C
based on probe-time tractability per the rehearsal checklist §7. The
Branch-A passive-observation phase (14 d post-Wave-1) ran first; only
proceeded to active deploy if 0 legal-niche PAPER_TRADE materialised
under the already-active Google News query family. Forensics finding
(commit `37063d8`) confirmed Google News is the historical
load-bearing path; Branch B (direct VitalLaw RSS) was empirically
ruled out by Codex 2026-05-05 probe (`a45c06c`).

### Soak-window-active assertion

Wave-2 deploy assumes the post-Wave-1 base-stack from `0.30.0` is
stable in production for ≥ 14 d. Pre-deploy validation:
- `pytest tests/test_lever_a1plus*.py -q` — confirm 0 strict-xfail
  remaining for the deployed branch
- `python scripts/simulations/post_obs003_skipped_attribution_audit.py`
  — confirm SKIPPED stream shape matches OBS-003 contract under live
  data

### Operator deploy commands

```bash
echo "0.31.0" > VERSION
git add VERSION  # pre-commit hook syncs README

# Apply RSS_FEEDS + classifier + (option-C only) evidence_scorer changes
# Remove the strict-xfail markers in the SAME hunk

.venv/bin/python -m pytest -q
.venv/bin/ruff check .

git tag -a v0.31.0 -m "Wave-2 first feed onboarding"
git push origin main --tags
```

```

<!-- /audit-skip-block -->

## Wave-3 pre-staged CHANGELOG block

Insert ABOVE `## [0.31.0]` once Wave-3 is ready:

<!-- audit-skip-block: prestaged Wave-3 CHANGELOG content -->

```markdown
## [0.32.0] - 2026-06-13  (fill in actual deploy date — typically 14+ d after Wave-2 close)

### Added (PROFIT-EDGE-004 — Wave-3 attribution + risk-control levers)

#### PROFIT-EDGE-004 Lever B — G1 calibration tightening

`G1_CONFIDENCE_THRESHOLD` lowered from 0.05 → 0.04 (and
`G1_FAILSAFE_CONFIDENCE_THRESHOLD` proportionally 0.10 → 0.08) in
`tasks/trade_readiness_gate.py`. Per Codex's 2026-05-03 G1 admittance
counterfactual, this is an **attribution / calibration lever**, not
edge-production. Predicted lift: 1-2 PAPER_TRADE / 14 d. Spec:
[`docs/_archive/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md`](docs/_archive/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md) (ARCHIVED Stream G R30).
Strict-xfail markers removed:
- `tests/test_lever_b_g1_calibration.py::test_g1_confidence_threshold_lowered_to_spec_value` (1 marker)

#### PROFIT-EDGE-004 Lever C — cross-series headline correlation guard

New BlendTask enqueue-time check: candidates whose normalized
headline hash matches a previously-enqueued candidate within
`cfg.cross_series_correlation_window_seconds` (default 3600 s) are
suppressed with reason `cross_series_headline_in_window`. Per
Codex's 2026-05-03 cross-series overlap audit (49.2 % overlap on
the 13-day archive). Spec:
[`docs/_archive/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md`](docs/_archive/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md) (ARCHIVED Stream G R29).
Lever C is a **risk-control lever**: expected outcome is fewer
correlated-burst paper trades, not higher trade count. Strict-xfail
markers removed:
- `tests/test_lever_c_cross_series_correlation.py::test_cross_series_correlation_window_config_knob_exists` (1 marker)
- `tests/test_lever_c_cross_series_correlation.py::test_blend_task_uses_cross_series_headline_in_window_reason_string` (1 marker)
- `tests/test_lever_c_cross_series_correlation.py::test_normalized_headline_hash_function_exists` (1 marker)

### Closure decision

If Wave-3 + earlier waves still do not clear the EDGE-004 closure
target (≥ 5 % conversion over 14 d post-deploy), escalate per
[`docs/governance/post-edge-004-escalation-paths.md`](docs/governance/post-edge-004-escalation-paths.md):
PROFIT-LLM-001 (Path 1) → P4-GATE Appendix A (Path 2) → strategy
pivot (Path 3, last resort).

### Operator deploy commands

```bash
echo "0.32.0" > VERSION
git add VERSION  # pre-commit hook syncs README

# Apply Lever B threshold change in tasks/trade_readiness_gate.py:69-70
# Apply Lever C BlendTask enqueue-time guard
# Add cfg.cross_series_correlation_window_seconds knob
# Remove all 4 strict-xfail markers in the SAME hunk

.venv/bin/python -m pytest -q
.venv/bin/ruff check .

git tag -a v0.32.0 -m "Wave-3 attribution + risk-control levers"
git push origin main --tags
```

```

<!-- /audit-skip-block -->

## Cross-links

- `docs/governance/wave-1-changelog-entry-prestaged.md` — Wave-1 sibling
- `docs/governance/post-soak-close-rehearsal-checklist.md` — operator deploy guide
- `docs/governance/post-soak-rollback-runbook.md` — incident-response
- `docs/governance/post-edge-004-escalation-paths.md` — Wave-3 stall escalation
