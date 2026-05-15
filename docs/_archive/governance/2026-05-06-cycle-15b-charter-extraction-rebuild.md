# Cycle-15B charter — extraction rebuild + replay validation

**Type:** focused single-deliverable cycle. Behavioral fix scope; replay-gated per IC §16.
**Drafted:** 2026-05-06 (cycle-14 verdict landing).
**Authority:** Cycle-14 verdict = `extraction_broken` (`docs/_archive/governance/edge-replay-cycle14-diagnosis.md`); skeleton §B (`docs/_archive/governance/cycle-15-conditional-charter-skeletons.md`); task split (`docs/governance/2026-05-06-cycle-15b-task-split.md`).
**Owner:** Codex (implementation, C1-C10); Claude (review + governance + scaffolding, L1-L10).
**Tracker:** PROFIT-EDGE-008.
**Status:** ACTIVE.

## TL;DR

Cycle-14 Lane B returned `model_prob=0.500` and `delta=0.000` on both crystal-clear-YES and crystal-clear-NO synthetic fixtures — extraction emits no signal at all on directional input. Cycle-15B identifies the extraction step that zeroes magnitude, applies one sub-fix, proves post-fix Lane B passes, re-ingests the 16-day evidence window, and re-runs the Cycle-13 replay. IC §16 gate: ≥ 1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.

**Cycle-15B answers ONE question:** does fixing the named extraction step produce post-fix Lane B pass AND a positive-EV replay slice?

**Behavioral fix scope.** Single sub-fix at a single named extraction step. Multi-step sub-fixes require explicit operator scope-extension authorization (locked below).

## Pre-stated decision criteria (LOCK BEFORE PER-STEP TRACE RUNS)

These criteria mirror cycle-14 charter §"Pre-stated decision criteria" pattern. Operator does NOT change them post-hoc.

### Zero-collapse-step identification (Codex C2 output)

A step "zeroes magnitude" iff:
- Step input has `|signal_magnitude| > 0.05`.
- Step output has `|signal_magnitude| < 0.01` (matches `movement_floor` from cycle-14).

If multiple steps each individually meet the collapse criterion, record the FIRST (root-cause-tracing rule per `superpowers:systematic-debugging`).

If no single step zeroes alone but the cumulative effect across multiple steps is collapse, that is a SECOND-CLASS finding requiring multi-step sub-fix scope. Multi-step sub-fix triggers operator scope-extension conversation BEFORE C7 implementation.

### Sub-fix selection acceptance (Codex C6 output → Claude L6 verdict)

A sub-fix selection (C6) passes verdict (L6) iff:
- Single-step fix at the C2-identified zero-collapse step.
- Before/after pseudocode named in C6 doc with file:line.
- Codex + Claude + operator agreement on file:line + before/after (replicates cycle-14 charter §"Sign-inversion verification gate" condition 2).
- Rationale cites C2-C5 trace evidence; does not invent evidence.

### Lane B post-fix verification (Codex C8 output)

C8 passes iff:
- ≥ 6 of 10 fixtures in `tests/fixtures/cycle14_synthetic_evidence.json` produce `|delta| > 0.05` after extraction.
- Of those 6+, ≥ 90% have direction matching `expected_direction` (NEUTRAL fixtures F8/F9 excluded from direction-correctness denominator).
- F10 BSR-5 repetition-damping holds: `|delta_F10| < 0.5 × |delta_F1|`.
- F8/F9 NEUTRAL fixtures stay within `expected_magnitude_max` (0.02 / 0.005 respectively) — extraction must not over-react to noise.

### Cycle-15B IC §16 acceptance (Codex C10 output)

The cycle passes IC §16 iff:
- Post-fix Cycle-13 replay (24 resolved markets, 255+ rows) shows ≥ 1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.
- Reproducible via documented commands in `edge-replay-cycle15b-report.md`.

If 0 positive-EV slices despite Lane B post-fix pass:
- Verdict = `extraction_fixed_but_information_frontier_holds`.
- Wave-2/Wave-3/Branch-D remain HALTED.
- Cycle-16 = source-onboarding (skeleton §C) OR strategic-pivot (skeleton §F) per operator decision.

If Lane B post-fix fails (< 6/10):
- Verdict = `extraction_rebuild_failed`.
- Cycle-15B re-opens for second sub-fix attempt OR escalates to Cycle-16F redesign per operator decision.
- 3 failed sub-fixes across Cycle-15B + Cycle-15B-extension trigger architectural conversation per `superpowers:systematic-debugging` "if 3+ fixes failed: question architecture" rule.

## Cycle-15B deliverables (Codex authors C1-C10)

Per `docs/governance/2026-05-06-cycle-15b-task-split.md` Codex 10 tasks. Charter does not duplicate task descriptions; refer to task split doc.

Strict sequencing:
- L1 (this charter) + L9 + L10 land BEFORE Codex C1.
- L2 lands BETWEEN C2 trace and C6 sub-fix selection.
- L7 atomicity review lands BEFORE C10 consumes C9 re-ingestion output.
- L8 cohort note lands BEFORE C10 reporting.

## Out of scope for Cycle-15B

- Multi-step extraction sub-fixes (locked above; require operator scope-extension).
- Source onboarding. (Cycle-16 §C scope if Cycle-15B verdict = `fixed_but_frontier_holds`.)
- Wave-2 / Wave-3 / Branch-D deploys. Stay HALTED until Cycle-15B IC §16 acceptance lands.
- Live-trading flag flip. PAPER-ONLY remains locked.
- Re-running pre-Cycle-15B paper trades for calibration. Per L8 cohort note: pre-Cycle-15B paper-trade data is not ground truth post-fix.
- Wave-1 deploy work (independent track; ships 2026-05-08T19:01Z per existing plan).

## Sole exception to "single sub-fix"

Trivial measurement bug **in the diagnostic tooling itself** (e.g., trace harness emits a wrong field name, JSON parse bug). NOT in extraction code. If found, fix and re-run trace; does NOT count toward "3 failed sub-fixes" architectural trigger.

## Capital posture

PAPER-ONLY. NO LIVE CAPITAL. Wave-2 + Wave-3 + Branch-D HALTED until Cycle-15B IC §16 acceptance. Live-trading flip requires explicit operator override per `tests/test_paper_mode_lock_post_wave1.py`.

## Sequencing relative to Wave-1 deploy

Cycle-15B can run in parallel with Wave-1 deploy commits 1-6 (2026-05-08T19:01Z → 2026-05-16T06:00Z+). Wave-1 ships cleanup/observability hygiene only — independent track.

Cycle-15B C7 sub-fix implementation MUST land in a separate commit from any Wave-1 deploy commit. Wave-1 deploys do NOT touch `analysis/` extraction code; `tests/test_paper_mode_lock_post_wave1.py` enforces no live-trading-flag flip in either track.

## Cycle-15B success criterion

C10 report produced + signed by both Codex and Claude. Verdict matches one of:
- `extraction_fixed_with_positive_ev_slice` (Lane B post-fix ≥ 6/10 AND ≥ 1 IC §16 slice)
- `extraction_fixed_but_information_frontier_holds` (Lane B post-fix ≥ 6/10 AND 0 IC §16 slices)
- `extraction_rebuild_failed` (Lane B post-fix < 6/10)

Cycle-16 scope derived FROM verdict via `cycle-16-conditional-charter-skeletons.md` (L10), not invented to fit a preferred path.

## Cross-links

- `docs/_archive/governance/edge-replay-cycle14-diagnosis.md` — Cycle-14 verdict source.
- `docs/_archive/governance/cycle-15-conditional-charter-skeletons.md` §B — extraction rebuild skeleton.
- `docs/governance/2026-05-06-cycle-15b-task-split.md` — 20-task split (10 Codex + 10 Claude).
- `docs/governance/cycle-15b-post-verdict-action-checklist.md` — L9 post-verdict checklist (pre-staged).
- `docs/_archive/governance/cycle-16-conditional-charter-skeletons.md` — L10 conditional Cycle-16 skeletons (pre-staged).
- `docs/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` — L8 cohort note.
- `docs/_archive/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` — sites 2/3/6/7 prime trace targets.
- `tests/fixtures/cycle14_synthetic_evidence.json` — 10 Lane B fixtures.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs C10 acceptance).
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-008` — debt entry tracking this cycle.
- `docs/EDGE_STATUS.md` — operator-facing dashboard (refreshed at end of Cycle-15B).
