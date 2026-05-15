# Cycle-15B task split — extraction rebuild + replay validation

**Type:** 10-task split per agent (Codex + Claude). Mirrors prior cycle prep pattern (cycle-12/13/14).
**Drafted:** 2026-05-06 cycle-14 verdict landing.
**Authority:** `docs/_archive/governance/cycle-15-conditional-charter-skeletons.md` §B; `docs/_archive/governance/edge-replay-cycle14-diagnosis.md` Claude appendix.
**Tracker:** PROFIT-EDGE-008.

## TL;DR

Cycle-14 verdict = `extraction_broken`. Lane B returned `model_prob=0.500` and `delta=0.000` on both crystal-clear synthetic fixtures — extraction emits **no signal at all**, not an inverted one. Cycle-15B must:

1. Identify the extraction step that zeros magnitude (per-step trace).
2. Apply ONE sub-fix at that step.
3. Prove post-fix Lane B passes (direction + magnitude on ≥6/10 fixtures).
4. Re-ingest 16-day evidence window through fixed extraction.
5. Re-run Cycle-13 replay; require ≥1 positive-EV slice with `trades ≥ 10` per IC §16.

10 tasks per agent, 20 total. Codex implementation-heavy. Claude governance + review + scaffolding.

## Sub-fix decision criteria (locked BEFORE per-step trace runs)

Per Cycle-14 charter pattern: criteria locked first, evidence consumed second, no post-hoc rationalization.

### Zero-collapse-step identification (Codex Task 2 output)

A step "zeroes magnitude" when:
- Step input has `|signal_magnitude| > 0.05` (well-shaped directional input present).
- Step output has `|signal_magnitude| < 0.01` (signal collapsed below movement_floor).

If multiple steps contribute, record the FIRST step where collapse occurs (root-cause-tracing rule). If no single step zeroes alone but the cumulative effect is collapse, that's a SECOND-CLASS finding requiring multi-step sub-fix and a Cycle-15B-extension scope discussion.

### Sub-fix acceptance (Claude Task 6 verdict)

A sub-fix passes Cycle-15B Lane B verification when:
- ≥ 6 of 10 cycle14_synthetic_evidence.json fixtures produce `|delta| > 0.05` after extraction.
- Of those 6+, ≥ 90% have direction matching `expected_direction` (NEUTRAL fixtures excluded from direction-correctness denominator; F8/F9 with `expected_direction="NEUTRAL"` excluded; F10 repetition-damping fixture must show `|delta_F10| < 0.5 × |delta_F1|` per BSR-5).
- Sub-fix touches the SINGLE extraction step identified in Task 2; multi-step sub-fixes require explicit operator scope-extension authorization.

### Cycle-15B IC §16 acceptance (final gate)

- Post-fix re-ingestion produces new `dossier_updates` rows for the 16-day evidence window.
- Post-fix Cycle-13 replay (24 resolved markets, 255+ rows) shows ≥ 1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.
- If 0 positive-EV slices despite Lane B post-fix pass: Cycle-15B verdict = `extraction_fixed_but_information_frontier_holds` → escalate to skeleton §C source-onboarding OR §F redesign per operator decision.

Operator does NOT change these criteria post-hoc.

## Codex 10 tasks (implementation)

| # | Task | Output | Acceptance |
|---|---|---|---|
| C1 | **Per-step extraction trace harness.** Instrument `analysis/signal_analyzer._analyze_news` to emit ordered step records: `{step_name, input_signal_magnitude, output_signal_magnitude, intermediate_state}`. Cover LLM-path, keyword-path, magnitude-shift mapping, geo-coherence suppression. Read-only — do not modify extraction behavior. | `scripts/edge_replay/per_step_extraction_trace.py` + `analysis/signal_analyzer` instrumentation hooks gated by env flag. | Harness emits ≥ 4 step records per Lane B fixture without changing `estimated_probability` output for unflagged runs. |
| C2 | **Run trace over Lane B fixtures.** Execute C1 harness against all 10 fixtures in `tests/fixtures/cycle14_synthetic_evidence.json`. Identify which step satisfies the zero-collapse-step criterion (locked above). | `logs/edge_replay/cycle15b/per_step_trace.json` + `logs/edge_replay/cycle15b/zero_collapse_step.json` (single step name). | Per-fixture trace records present; zero-collapse step named (or multi-step finding flagged). |
| C3 | **LLM prompt convention audit.** Read `_LLM_SYSTEM_PROMPT`. Run qwen3:14b against the 10 Lane B fixtures using current prompt with same `think=False` setting as production. Log raw LLM JSON output per fixture: `{direction, magnitude, ...}`. | `logs/edge_replay/cycle15b/llm_prompt_audit.json`. | Raw LLM output for each fixture captured. Flag if `magnitude="none"` or `direction="neutral"` returned on a fixture with `expected_direction != "NEUTRAL"`. |
| C4 | **Per-keyword direction map dump.** Export current `_KEYWORDS` (or equivalent) direction assignments. Cross-reference against vocabulary in Lane B fixtures (FISA reauthorize/expire/lapse/signed; pardons issued/not; Iran nuclear deal; Vance Pakistan visit/cancel). Identify missing entries OR wrong-direction entries. | `logs/edge_replay/cycle15b/keyword_audit.json`. | Per-fixture keyword-coverage summary; missing-vocabulary list per fixture. |
| C5 | **Suppression-logic trace.** Instrument geo-coherence + magnitude-shift mapping decisions. For each Lane B fixture record `{suppression_triggered, suppression_reason, pre_suppression_magnitude, post_suppression_magnitude}`. | `logs/edge_replay/cycle15b/suppression_trace.json`. | Per-fixture suppression decision logged; fraction of Lane B fixtures suppressed reported. |
| C6 | **Sub-fix selection.** Synthesize C2-C5 outputs into a single recommendation: `{chosen_sub_fix, file:line, before_pseudocode, after_pseudocode, rationale_referencing_trace_evidence}`. Single-step fix only; multi-step requires operator scope-extension. | `docs/governance/edge-replay-cycle15b-sub-fix-proposal.md`. | Doc proposes ONE sub-fix; cites C2 zero-collapse step; references C3/C4/C5 evidence; before/after pseudocode reviewable. |
| C7 | **Implement sub-fix.** Apply the C6-named change at the named file:line. No "while I'm here" cleanup. | Single commit modifying ≤ 2 files in `analysis/`. | Tests pass; ruff clean; sub-fix matches C6 proposal exactly. |
| C8 | **Lane B post-fix verification.** Re-run Lane B harness against 10 fixtures with C7 code in place. Report direction + magnitude per fixture; compute pass rate. | `logs/edge_replay/cycle15b/lane_b_post_fix.json`. | ≥ 6/10 fixtures meet acceptance criteria above; F10 BSR-5 damping holds. |
| C9 | **Re-ingestion pipeline.** Re-run dossier-update logic over the 16-day evidence_store window with C7 code. Rebuild `dossier_updates`. Idempotent + deterministic (same input → same output across runs). Pre-fix `dossier_updates` rows preserved in a separate table or backup file for audit. | `data/dossier_updates_post_fix.db` (or equivalent) + `logs/edge_replay/cycle15b/reingestion_audit.json`. | Re-run twice produces byte-identical output; audit log shows row counts pre vs post; pre-fix rows recoverable. |
| C10 | **Cycle-15B replay run.** Execute Cycle-13 scoring against C9 post-fix `dossier_updates`. Report (source × market_family × signal_type) slice table with `ev_ci_95_lo` + `trades` columns. | `logs/edge_replay/cycle15b/counterfactual_scores.json` + `docs/governance/edge-replay-cycle15b-report.md`. | Report names whether IC §16 acceptance criterion (≥ 1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`) is met; if not, report cleanly states "extraction fixed but no positive-EV slice surfaced — escalate." |

## Claude 10 tasks (governance + review + scaffolding)

| # | Task | Output | Acceptance |
|---|---|---|---|
| L1 | **Cycle-15B charter document.** Mirror cycle-14 charter pattern: scope, locked acceptance criteria, sub-fix decision criteria, IC §16 evidence gate. Locked BEFORE C1 harness runs. | `docs/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md`. | Charter authored; criteria above replicated; cross-links to skeleton §B + diagnosis doc. |
| L2 | **Pre-execution sub-fix-criteria-lock verification.** When C2 lands, verify Codex's zero-collapse-step identification matches the locked criterion (input `|magnitude| > 0.05` AND output `|magnitude| < 0.01`). If criterion drifts, flag to Codex; trace re-runs do not proceed until aligned. | `docs/governance/2026-05-06-cycle-15b-pre-execution-criteria-verification.md` (post-C2). | Verification doc landed; criterion drift flagged or absence-of-drift confirmed. |
| L3 | **Codex per-step trace code review.** Review C1 harness for: read-only behavior under unflagged runs, instrumentation completeness across all 4+ extraction steps, JSON output schema stability. | Code review feedback comment or PR review on Codex commit. | Findings filed before C2 trace runs OR confirmed clean. |
| L4 | **Independent read of Lane B trace results.** Read C2/C3/C4/C5 outputs without consulting Codex's C6 proposal. Identify zero-collapse-step independently. If Claude's identification differs from Codex's, both perspectives recorded; operator picks. | Section in cycle-15B diagnosis doc (`edge-replay-cycle15b-report.md` Claude appendix) OR standalone `2026-05-06-cycle-15b-claude-independent-trace-read.md`. | Independent identification recorded; matches or differs from Codex with both rationales. |
| L5 | **Cross-check vs PROFIT-GOV-001 / PROFIT-GOV-002 same-class pathologies.** PROFIT-GOV-001 (qwen3 thinking-consumed-by-JSON-grammar; fixed via `think=False`) and PROFIT-GOV-002 (rubber-stamp bias) are LLM-layer pathologies. If C3 flags Lane B fixtures returning `magnitude="none"` on directional input, audit whether the same root cause (thinking-consumed-by-grammar in qwen3 OR low-confidence-default rubber-stamp at signal-analyzer) is at play. Recommend whether the sub-fix needs to address signal-analyzer prompt convention specifically OR a broader LLM-layer pattern. | Section in L4 doc or standalone `2026-05-06-cycle-15b-llm-pathology-cross-check.md`. | Cross-check landed; sub-fix scope recommendation matches pathology class. |
| L6 | **Sub-fix verdict appendix to Cycle-15B diagnosis doc.** Mirror cycle-14 appendix pattern. Independent voice on Codex's chosen sub-fix (C6); verify it matches trace evidence (C2-C5) and locked criteria (L2). If sub-fix path differs from Claude's L4 read, flag; operator picks. | Claude appendix in `docs/governance/edge-replay-cycle15b-sub-fix-proposal.md` OR `edge-replay-cycle15b-report.md`. | Appendix landed; matches/disagreement recorded; operator-decision-only items flagged. |
| L7 | **Re-ingestion atomicity review.** Review C9 re-ingestion pipeline for: idempotence (same input → same output), determinism (no wall-clock-dependent ordering), atomicity (re-run failure does not corrupt mid-state), pre-fix preservation (rollback path exists). Reference CLAUDE.md "DB transaction atomicity in `resolve_market()`" gotcha — analogous risk class. | Code review on C9 commit. | Atomicity findings filed; rollback path verified executable. |
| L8 | **Pre-fix `paper_trades` cohort note.** Per skeleton §A.5 (transferable to §B): pre-fix paper-traded data is no longer ground truth for calibration. Append cohort note to `data/paper_trades.db` schema doc OR `docs/IMPLEMENTATION_CONTRACT.md` §16. Future replay runs must distinguish pre-Cycle-15B vs post-Cycle-15B trade cohorts. | Schema-or-doc note + reference from PROFIT-EDGE-008 entry. | Note landed; future replay tooling guidance is clear. |
| L9 | **Cycle-15B post-verdict action checklist.** Mirror `cycle-14-post-verdict-action-checklist.md` pattern: pre-staged ROADMAP wording per Cycle-15B verdict (positive-EV slice surfaces / fixed-but-frontier-holds / extraction-rebuild-failed), EDGE_STATUS refresh template, debt-log close-and-file-successor template. Pre-stage BEFORE C10 lands. | `docs/governance/cycle-15b-post-verdict-action-checklist.md`. | Checklist landed pre-C10; verdict-to-wording maps cover the 3 outcome cases. |
| L10 | **Conditional Cycle-16 skeletons.** If Cycle-15B C10 produces ≥1 positive-EV slice → Cycle-16 = Wave-2 candidate authoring (slice-specific feed onboarding with replay-gated acceptance). If extraction fixed but no slice → Cycle-16 = §C source-onboarding OR §F redesign per operator decision. Pre-stage skeletons analogous to `cycle-15-conditional-charter-skeletons.md`. | `docs/_archive/governance/cycle-16-conditional-charter-skeletons.md`. | Skeleton set covers 3 outcome branches; verdict-to-skeleton map present; pre-stages BEFORE Cycle-15B C10 verdict landing. |

## Sequencing

Strict: L1 + L9 + L10 land BEFORE Codex C1 harness runs (criteria-lock pattern).
Parallel: C1-C5 trace runs can interleave with L3 code review.
Strict: L2 (criteria verification) lands BETWEEN C2 trace and C6 sub-fix selection.
Parallel: L4 + L5 + L6 land alongside or after C6.
Strict: L7 re-ingestion review lands BEFORE C10 replay run consumes C9 output.
Strict: L8 cohort note lands BEFORE C10 reporting.

## What Cycle-15B does NOT do

- No live-trading flag flip. Capital posture remains PAPER-ONLY.
- No Wave-2 / Wave-3 / Branch-D unblock. Those wait for IC §16 acceptance from C10.
- No multi-step sub-fix without explicit operator scope-extension authorization (locked above).
- No deploy hope. Each gate has replay evidence or it doesn't pass.
- No cycle-13 paper-trade re-use as ground truth post-fix. Cohort note (L8) enforces this.

## Cross-links

- `docs/_archive/governance/edge-replay-cycle14-diagnosis.md` — Cycle-14 verdict source.
- `docs/_archive/governance/cycle-15-conditional-charter-skeletons.md` §B — extraction rebuild skeleton.
- `docs/_archive/governance/cycle-14-post-verdict-action-checklist.md` — analogous post-verdict pattern.
- `docs/_archive/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` — sites 2/3/6/7 prime trace targets.
- `tests/fixtures/cycle14_synthetic_evidence.json` — 10 Lane B fixtures.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs C10 acceptance).
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-008` — debt entry tracking this cycle.
