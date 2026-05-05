# Wave-2 + Wave-3 deploy — pre-staged CHANGELOG entries

**Status:** pre-stage; copy into `CHANGELOG.md` at each Wave's close commit. **Do not insert pre-soak-close** — would lock version numbers prematurely.
**Drafted:** 2026-05-04 (during PROFIT-PHASE2-001 soak); sibling to `wave-1-changelog-entry-prestaged.md`
**Rationale:** reduces deploy-day cognitive load. Operator copies the relevant block at each Wave close, fills in the actual VERSION value, runs the pre-commit hook to sync README badges, commits.

## Version sequence (planned)

| wave | tracker | landing day | proposed VERSION |
|---|---|---|---|
| 1 | OBS-005 / MATCH-001 / OBS-003 / EXEC-002 / GOV-003 / Lever A.1 | 2026-05-08 (early-close path) or 2026-05-15 (default) | 0.30.0 |
| 2 | Lever A.1+ first feed (option-A or option-C-Branch-C-fallback) | 2026-05-15+ (early-close) or 2026-05-22+ (default) | 0.30.1 |
| 3 | Lever B G1=0.04 + Lever C cross-series guard | 2026-06-06+ (early-close) or 2026-06-13+ (default) | 0.31.0 |

Wave-2 = patch bump (single feed onboarding; minor behavioural change). Wave-3 = minor bump (Lever B is a tunable gate change; Lever C is new functionality at the BlendTask enqueue point).

If the operator chooses different sub-versions (e.g., 0.30.0 → 0.30.1 → 0.30.2 with Lever B and Lever C as separate patch bumps), update this doc + the actual deploy commit. The pre-staged blocks below assume the planned sequence.

## Wave-2 pre-staged CHANGELOG block

Insert ABOVE `## [0.30.0] - 2026-05-15` (after Wave-1 lands) once Wave-2 is ready:

```markdown
## [0.30.1] - 2026-05-22  (or 2026-05-23 — fill in actual deploy date)

### Added (PROFIT-EDGE-004 — Wave-2 first feed onboarding)

#### PROFIT-EDGE-004 Lever A.1+ — first specialist feed onboarding

Branch chosen at deploy time per [`docs/governance/edge-004-closure-path-tldr.md`](docs/governance/edge-004-closure-path-tldr.md) v2.1+:

- **Option-A** (specialist-geopolitics): added one of war on the rocks /
  CSIS / ISW / CFR / Atlantic Council to `config.py:RSS_FEEDS`. Spec:
  [`docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md`](docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md).
  Companion classifier branch addition (`analysis` source-class) in
  `main.py:_source_class_for_evidence`. Strict-xfail markers removed:
  - `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1PlusAnalysisBranch` (6 markers)
  - `tests/test_lever_a1plus_feed_config.py::test_at_least_one_specialist_analyst_url_in_rss_feeds` (1 marker)

- **Option-C-Branch-C** (legal-analyst fallback): added one of Lawfare /
  Just Security / SCOTUSblog / Politico Legal to `config.py:RSS_FEEDS`.
  Spec: [`docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md`](docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md).
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
echo "0.30.1" > VERSION
git add VERSION  # pre-commit hook syncs README

# Apply RSS_FEEDS + classifier + (option-C only) evidence_scorer changes
# Remove the strict-xfail markers in the SAME hunk

.venv/bin/python -m pytest -q
.venv/bin/ruff check .

git tag -a v0.30.1 -m "Wave-2 first feed onboarding"
git push origin main --tags
```

```

## Wave-3 pre-staged CHANGELOG block

Insert ABOVE `## [0.30.1]` once Wave-3 is ready:

```markdown
## [0.31.0] - 2026-06-13  (fill in actual deploy date — typically 14+ d after Wave-2 close)

### Added (PROFIT-EDGE-004 — Wave-3 attribution + risk-control levers)

#### PROFIT-EDGE-004 Lever B — G1 calibration tightening

`G1_CONFIDENCE_THRESHOLD` lowered from 0.05 → 0.04 (and
`G1_FAILSAFE_CONFIDENCE_THRESHOLD` proportionally 0.10 → 0.08) in
`tasks/trade_readiness_gate.py`. Per Codex's 2026-05-03 G1 admittance
counterfactual, this is an **attribution / calibration lever**, not
edge-production. Predicted lift: 1-2 PAPER_TRADE / 14 d. Spec:
[`docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md`](docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md).
Strict-xfail markers removed:
- `tests/test_lever_b_g1_calibration.py::test_g1_confidence_threshold_lowered_to_spec_value` (1 marker)

#### PROFIT-EDGE-004 Lever C — cross-series headline correlation guard

New BlendTask enqueue-time check: candidates whose normalized
headline hash matches a previously-enqueued candidate within
`cfg.cross_series_correlation_window_seconds` (default 3600 s) are
suppressed with reason `cross_series_headline_in_window`. Per
Codex's 2026-05-03 cross-series overlap audit (49.2 % overlap on
the 13-day archive). Spec:
[`docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md`](docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md).
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
echo "0.31.0" > VERSION
git add VERSION  # pre-commit hook syncs README

# Apply Lever B threshold change in tasks/trade_readiness_gate.py:69-70
# Apply Lever C BlendTask enqueue-time guard
# Add cfg.cross_series_correlation_window_seconds knob
# Remove all 4 strict-xfail markers in the SAME hunk

.venv/bin/python -m pytest -q
.venv/bin/ruff check .

git tag -a v0.31.0 -m "Wave-3 attribution + risk-control levers"
git push origin main --tags
```

```

## Cross-links

- `docs/governance/wave-1-changelog-entry-prestaged.md` — Wave-1 sibling
- `docs/governance/post-soak-close-rehearsal-checklist.md` — operator deploy guide
- `docs/governance/post-soak-rollback-runbook.md` — incident-response
- `docs/governance/post-edge-004-escalation-paths.md` — Wave-3 stall escalation
