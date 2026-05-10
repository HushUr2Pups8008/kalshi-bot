# Cycle-17D Criteria-Lock Template — All Experiments (E4+)

**Date template created:** 2026-05-10  
**Author:** Claude  
**Predecessor template:** `docs/governance/2026-05-10-cycle-17c-e3-criteria-lock.md`  
**Charter:** `docs/governance/2026-05-10-cycle-17d-charter-amendment.md` (Cycle-17D amendment)  
**Status:** TEMPLATE (not a concrete criteria-lock for any specific experiment; each E4, E5, ... clones this template and fills in the placeholder fields)  
**Tracking:** PROFIT-EDGE-012 (rolling per-experiment entries)

---

## Preamble

This is the **TEMPLATE** for all Cycle-17D experiment criteria-locks (E4, E5, E6, ...). Each concrete experiment:
1. Clones this template into a dated doc (e.g., `2026-05-10-cycle-17d-e4-criteria-lock-[hypothesis].md`).
2. Fills in the placeholder sections (§1 locked hypothesis, §2 implementation path, etc.) before replay runs.
3. Lands as a criteria-lock commit before the replay command executes.
4. Remains immutable post-lock (per Cycle-17C charter rule: "Acceptance criteria + hypothesis LOCKED pre-replay").

The hard clauses in §3 (Clauses A–E) apply to **every** Cycle-17D experiment. Clause E (cohort-drift disqualification) is new to Cycle-17D and is load-bearing per the charter amendment.

---

## 1 — Locked Hypothesis (Placeholder; Experiment Fills in)

```
[EXPERIMENT FILLS IN: single sentence, falsifiable hypothesis]

Example (do NOT use this for a real experiment):
"Lowering the EVIDENCE_CONFIDENCE_THRESHOLD from 0.30 to 0.20 in 
tasks/trade_readiness_gate.py will admit >10 production-proxy trades 
on the merged Cycle-17D corpus and produce ≥1 IC §16 slice."
```

**Charter rule:** hypothesis must be single-sentence, falsifiable, and testable on the locked corpus.

---

## 2 — Locked Implementation Path (Placeholder; Experiment Picks One)

**Options (mirrors E3 template structure):**

### Path 1 — Production code change

**Touch surface:** ONE file only. (Multi-file changes require operator scope-extension or redesign-as-separate-experiment.)

**Forbidden touch surfaces** (any modification reverts E automatically):
- Other production files outside the declared one.
- `analysis/signal_analyzer.py` (unless explicitly named as the hypothesis).
- `scripts/edge_replay/scorer_forensics_audit.py` (no scorer mutation; schema-normalization per Delta 2 exception is separate).
- `evidence_store.db` or any frozen artifact under `logs/edge_replay/cycle17d/`.
- `governance/prompts.py` (anchor_rate polarity block lines 27–31 must remain untouched per CLAUDE.md).
- Any file outside `analysis/`, `tasks/`, or `trading/` without explicit charter amendment.

**Permitted reads:**
- `logs/edge_replay/cycle17d/replay_dataset_merged.jsonl` (locked merged corpus).
- `logs/edge_replay/cycle17d/counterfactual_scores.json` (scorer output).
- `logs/edge_replay/cycle17d/historical_prices_cycle17d.json` (resolved prices).
- `logs/edge_replay/cycle17d/coverage_audit.json`.

**Implementation pattern:** code change lands in a single commit; tests added or updated in the same commit or a follow-up.

### Path 2 — Counterfactual scorer mode

**Touch surface:** ONE NEW FILE in `scripts/edge_replay/` (e.g., `scripts/edge_replay/[hypothesis]_counterfactual.py`).

**Forbidden touch surfaces:**
- Production code (analysis/tasks/trading).
- `scorer_forensics_audit.py` itself.
- Frozen artifacts.

**Implementation pattern:** mirrors E3 Path 3 (`side_flip_counterfactual.py`). Deterministic, no randomness, no LLM calls, no network, pure-Python over frozen artifacts.

### Path 3 — Post-processing diagnostic script

**Touch surface:** ONE NEW FILE in `scripts/edge_replay/`.

**Forbidden touch surfaces:** same as Path 2.

**Implementation pattern:** same as Path 2. Outcome-blind or outcome-sensitive as needed by hypothesis.

---

### 2.1 — Codex Implementation Spec (Placeholder; Experiment Fills in)

| Aspect | Spec |
|--------|------|
| CLI entry | [EXPERIMENT: argparse signature; required args, output formats] |
| Deterministic | [YES / NO — describe any non-determinism] |
| Hypothesis-specific logic | [EXPERIMENT: description of the single-variable change under test] |
| Slice grouping | [EXPERIMENT: 4-axis (`signal_source × market_family × signal_type × news_class`) required; 5-axis diagnostic optional] |
| IC §16 evaluation | [EXPERIMENT: gate formula matching the 4-axis grouping; must preserve charter definition] |
| Output | [EXPERIMENT: Markdown report path; JSON summary; per-row JSONL; structured summary JSON] |
| Tests | [EXPERIMENT: minimum unit-test coverage required per Path choice] |

**Charter rule:** implementation contract must be explicit and verifiable before replay runs.

---

### 2.2 — Locked Replay Command (Placeholder; Experiment Fills in)

```
[EXPERIMENT: exact shell command including corpus path, scorer flags, output paths]

Example (do NOT use this for a real experiment):
.venv/bin/python scripts/edge_replay/[hypothesis]_counterfactual.py \
  --dataset    logs/edge_replay/cycle17d/replay_dataset_merged.jsonl \
  --scores     logs/edge_replay/cycle17d/counterfactual_scores.json \
  --prices     logs/edge_replay/cycle17d/historical_prices_cycle17d.json \
  --coverage   logs/edge_replay/cycle17d/coverage_audit.json \
  --output-dir logs/edge_replay/cycle17d-e4 \
  --write-report docs/governance/2026-05-10-cycle-17d-e4-[hypothesis].md
```

**Charter rule:** command must be verbatim-executable post-commit. Any flag deviation requires unlock.

---

## 3 — Locked Acceptance Bar (HARD)

A `keep` verdict on any Cycle-17D experiment requires **all five** of the following clauses to hold simultaneously. Failure of any one clause forces a `revert` outcome.

---

### Clause A — IC §16 baseline (4-axis)

**Requirement:**
- ≥1 (`signal_source × market_family × signal_type × news_class`) slice with `ev_ci_95_lo > 0` AND `trades >= 10`.

**Rationale:** per `docs/IMPLEMENTATION_CONTRACT.md` §16 (replayed-EV gate) and Cycle-17C charter §"Acceptance bar = IC §16." This is the single bar for all Cycle-17 experiments; the 4-axis grouping preserves charter intent (no new degree of freedom).

**Verdict label if fails:** `revert_required_no_ic16_slice`.

---

### Clause B — Trivial-inversion / trivial-pass disqualification

**Requirement (experiment-specific; template provides reference wording):**

For **flip-style experiments** (analogous to E3):
- `excess_wins_vs_market = actual_wins - sum(market_yes_price/100 for the cohort)` MUST be > 0.5.
- Per memory `feedback_market_implied_baseline.md`: market-implied baseline is the correct null, NOT 50% coin-flip.
- Justification: a flip-sign mechanical pass on a >50% baseline produces near-zero excess — indistinguishable from no-signal.

For **non-flip experiments** (e.g., new update rule, new threshold):
- Define an experiment-specific anti-trivial-pass gate that prevents a change from appearing positive purely because of selection effects or mechanical inversion.
- Reference memory `feedback_market_implied_baseline.md` for the baseline philosophy; do NOT use 50% coin-flip.
- Example placeholder: "Win-rate improvement must exceed the market-implied expected-wins improvement by ≥ 5 percentage points."

**Verdict label if fails:** `revert_trivial_pass` or `revert_trivial_inversion` (experiment-specific term).

---

### Clause C — Sub-slice consistency

**Requirement:**
- Decompose admitted-row outcomes by each independent slice axis (signal_source, market_family, signal_type, news_class).
- For each axis with ≥2 bins each containing ≥3 admitted rows, compute proportion test or Fisher exact test between bins.
- Result must yield p ≥ 0.20 (loose threshold given small-n) OR no axis has >75% wins concentrated in a single bin.
- If ANY axis shows non-uniform win-rates at p < 0.20 AND the dominant bin contributes >75% of wins, the result is a sampling artifact, NOT signal evidence.

**Rationale:** a uniform across-axis pattern is required for a `keep` candidate. Monoculture bins are diagnostic only.

**Verdict label if fails:** `revert_uniform_consistency_failed`.

---

### Clause D — Single-variable charter compliance

**Requirement:**
- Only the declared file(s) per §2 are touched.
- No production code mutation, scorer mutation, frozen artifact mutation, or governance prompt mutation (except experiment-specific artifacts like the diagnostic script output).
- Commit log reflects ONLY the declared change + tests + companion markdown. Any drift = automatic revert.

**Rationale:** Cycle-17D charter rule: "One active experiment at a time. No parallel changes. No 'while we're here' cleanup. No bundled commits mixing experiment + unrelated work."

**Verdict label if fails:** `revert_charter_violation`.

---

### Clause E — Cohort-drift disqualification (NEW in Cycle-17D)

**Requirement:**
- If the IC §16-eligible 4-axis slice's `cohort_breakdown` is >75% concentrated in a single cohort flag (PRE_FIX / POST_FIX_REBUILT / POST_FIX_NEW), verdict = `revert_cohort_drift_driven` regardless of Clauses A/B/C/D outcomes.

**Rationale:** per Cycle-17D charter amendment §3(d). A slice drawing all statistical power from one extraction-regime is testing extraction-regime difference, not signal quality. Cohort-drift-driven results cannot generalize to production.

**Example:** if all 12 wins on a slice come from POST_FIX_REBUILT rows (100% concentration), and POST_FIX_REBUILT is 84% of the slice, the result is cohort-drift-driven even if Clauses A–D pass.

**Verdict label if fails:** `revert_cohort_drift_driven`.

---

## 4 — Revert Conditions (Any One Fires `revert_*`)

- Clause A fails (no IC §16-eligible slice) → `revert_required_no_ic16_slice`.
- Clause B fails (trivial pass) → `revert_trivial_pass` or `revert_trivial_inversion`.
- Clause C fails (sub-slice non-uniformity) → `revert_uniform_consistency_failed`.
- Clause D fails (touch-surface drift) → `revert_charter_violation`.
- Clause E fires (cohort >75% concentration) → `revert_cohort_drift_driven`.
- Replay cannot execute (artifact missing, parse failure, etc.) → `revert_replay_failed`.
- `trades < 10` post-experiment across all slices (downstream gate asymmetry) → `diagnostic_only_revert` per sketch, counting as a revert against the 3-revert architectural-rethink budget.

---

## 5 — Revert-Budget Tracker (Cycle-17D Fresh)

| Experiment | Status | Counts toward 3-revert budget? |
|--|--|--|
| Cycle-17C E1 | REVERTED 2026-05-07 | Historical; not carried forward |
| Cycle-17C E2 | AXIS_ABANDONED 2026-05-08 | Historical; not carried forward |
| Cycle-17C E3 | REVERTED 2026-05-10 | Historical; not carried forward |
| **Cycle-17D revert budget** | **RESET 2026-05-10** | **0/3 fresh start** |
| E4 (this slot) | TBD | TBD on verdict |

If E4 reverts → 1/3. If E5 reverts → 2/3. If E6 reverts → 3/3 → architectural-rethink rule fires → Cycle-17D halts; operator picks next cycle or axis.

---

## 6 — Capital Posture (Re-Affirmed for Cycle-17D)

PAPER-ONLY. Hard guardrail per Cycle-14 charter §5. NO experiment in Cycle-17D — including a hypothetical `keep` — flips this. Live trading authorization requires Cycle-17 §A deploy-candidate review (slice-specific risk review + capital allocation per IC §16 Rule 4 + kill-switch plan + operator commit citing replay report), independent and out of scope for Cycle-17D.

---

## 7 — Verdict Appendix Template Skeleton (Claude Fills in Post-Replay)

After Codex runs the locked replay command (§2.2) and the script/code change writes its companion report (Markdown at the path in §2.2), Claude appends a verdict appendix using this skeleton.

```markdown
# Cycle-17D E[N] Verdict Appendix — [Hypothesis Name]

**Verdict author:** Claude
**Date:** [TBD post-replay]
**Replay command:** Per criteria-lock §2.2 (verbatim).
**Replay artifacts:** logs/edge_replay/cycle17d-e[N]/{summary.json, per_row.jsonl, ...}
**Companion report:** This document (experiment-generated; verdict appended below).

## Verdict Label

One of: `keep_candidate` | `revert_required_no_ic16_slice` |
        `revert_trivial_pass` | `revert_uniform_consistency_failed` |
        `revert_charter_violation` | `revert_cohort_drift_driven` |
        `revert_replay_failed` | `diagnostic_only_revert`.

## Clause-by-Clause Evaluation

### Clause A — IC §16 baseline (4-axis)
- IC §16 slices found: <N>
- Top slice: <4-axis spec> with ev_ci_95_lo = <X>, trades = <T>
- Cohort breakdown for top slice: {PRE_FIX: <A>, POST_FIX_REBUILT: <B>, POST_FIX_NEW: <C>}
- PASS / FAIL: <verdict>

### Clause B — Trivial-pass disqualification
- [EXPERIMENT-SPECIFIC METRICS: e.g., excess_wins_vs_market for flip, or win-rate delta for threshold change]
- Threshold: [EXPERIMENT-SPECIFIC]
- PASS / FAIL: <verdict>
- Interpretation: <how to read the result>

### Clause C — Sub-slice consistency
- Per-axis analysis:
  - signal_source: bins = <list>; win-rates = <list>; min p-value = <P>; dominant-bin share = <S%>
  - market_family: <same>
  - signal_type: <same>
  - news_class: <same>
- Threshold: every axis bin-comparison p ≥ 0.20 AND no dominant-bin > 75%
- PASS / FAIL: <verdict>

### Clause D — Single-variable charter compliance
- Files added: <list>
- Files modified: <list>
- Forbidden touch surfaces verified clean: <yes/no>
- PASS / FAIL: <verdict>

### Clause E — Cohort-drift disqualification
- Top IC §16 slice cohort breakdown: {PRE_FIX: <A>, POST_FIX_REBUILT: <B>, POST_FIX_NEW: <C>} = <%> dominant
- Threshold: max concentration ≤ 75%
- PASS / FAIL: <verdict>
- Note: if any slice is >75% concentrated, the verdict is `revert_cohort_drift_driven` regardless of A/B/C/D.

## Final Decision

<keep_candidate / revert_*> — <one-paragraph operator-facing rationale>.

## Routing

- IF keep_candidate: file PROFIT-EDGE-[N+1] = "Cycle-17 §A deploy-candidate review for slice <X>"; do NOT alter production code; do NOT flip capital posture.
- IF revert_*: increment revert-budget tracker (0/3 → 1/3 if first revert); pick E[N+1] axis per charter §4 Delta 6 sequence; update PROFIT-EDGE-012 ledger row with verdict + decision_rationale.
- IF diagnostic_only_revert: also note structural finding; consider charter amendment for Cycle-18 if pattern repeats.

## Memory References Applied
- feedback_market_implied_baseline.md (applied to Clause B)
- feedback_audit_scorer_before_verdict.md (verdict discipline)

## Cross-References
- Criteria-lock: [THIS DOCUMENT DATE]
- Charter: docs/governance/2026-05-10-cycle-17d-charter-amendment.md
- Experiment ledger schema: docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md
- Predecessor E3 lock (reference pattern): docs/governance/2026-05-10-cycle-17c-e3-criteria-lock.md
```

---

## 8 — Cross-References

- **Cycle-17D charter amendment:** `docs/governance/2026-05-10-cycle-17d-charter-amendment.md` (governs all Cycle-17D experiments).
- **Cycle-17C charter (predecessor):** `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` (reference for rules, locked patterns).
- **Experiment ledger schema:** `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` (updated to include corpus_id, corpus_sha256, cohort_breakdown, ic16_slices_4axis, ic16_slices_5axis_diagnostic).
- **E3 criteria-lock (reference pattern):** `docs/governance/2026-05-10-cycle-17c-e3-criteria-lock.md` (structure mirror; Clause E is new to Cycle-17D).
- **IC §16 authority:** `docs/IMPLEMENTATION_CONTRACT.md` §16 (replayed-EV gate).
- **Memory references:**
  - `feedback_market_implied_baseline.md` (Clause B: null model is market-implied, not coin-flip).
  - `feedback_audit_scorer_before_verdict.md` (verdict discipline).
  - `feedback_cohort_drift_driven.md` (if it exists; otherwise Clause E formalization is new).

---

**Template version:** 1.0  
**Status:** ACTIVE (each concrete E4, E5, ... clones and fills in).

