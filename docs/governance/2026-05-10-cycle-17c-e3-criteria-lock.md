# Cycle-17C E3 Criteria Lock — Side-Inference Flip-Sign

**Date locked:** 2026-05-10
**Author:** Claude (per operator-confirmed coordination path (b) — Claude lands criteria-lock + verdict framework; Codex implements + replays after this commit)
**Hypothesis sketch:** [`2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md`](2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md) (`6081475`)
**Charter:** [`2026-05-07-cycle-17c-charter-single-variable-redesign.md`](2026-05-07-cycle-17c-charter-single-variable-redesign.md)
**Tracking:** PROFIT-EDGE-012

This is the pre-registered, charter-binding lock for Cycle-17C experiment E3. Once committed, no element below may be amended without an explicit operator-approved unlock commit. Codex implements + replays against this lock; Claude authors the verdict appendix (template embedded at the bottom of this doc) post-replay.

## 1 — Locked hypothesis

```
Sub-axis A only — flip-sign at the trade-side decision point.

If the bot's edge calculation is directionally informative but its
side-mapping is inverted relative to market outcome, then on the frozen
Cycle-16E production-proxy cohort (n=12, 12/12 YES baseline) the flip-
sign counterfactual produces a result that EXCEEDS the market-implied
expected-wins baseline AND clears IC §16 with sub-slice consistency.
```

This is **not** a test of "do mechanical inversions of a 12/12-YES cohort produce 12/12 wins" (they do; trivially). The hypothesis is that the bot's edge magnitude carries side-correct information that current side-mapping inverts. Verdict acceptance is calibrated against that distinction (§4 below).

Sub-axes B (Force-NO) and C (Force-YES) from the sketch are explicitly **NOT** part of this lock. Per single-variable rule, those are separate experiments (E4 candidate or sanity check) and may NOT be bundled.

## 2 — Locked implementation path: Path 3

Per sketch §"Implementation path":

- **Path 3 — New post-processing diagnostic script.**
- Touch surface: ONE NEW FILE — `scripts/edge_replay/side_flip_counterfactual.py`.
- **Forbidden touch surfaces** (charter compliance — any modification reverts E3 automatically):
  - `analysis/signal_analyzer.py` (no production code mutation).
  - `scripts/edge_replay/scorer_forensics_audit.py` (no scorer mutation).
  - `evidence_store.db` or any frozen artifact under `logs/edge_replay/cycle16d/`.
  - `tasks/blend_task.py`, `executor.py`, `tasks/trade_readiness_gate.py`, or any production decision path.
  - `governance/prompts.py` (anchor_rate polarity block lines 27–31 must remain untouched per CLAUDE.md).
- **Permitted reads:**
  - `logs/edge_replay/cycle16d/replay_dataset.jsonl` (frozen replay dataset)
  - `logs/edge_replay/cycle16d/counterfactual_scores.json` (scorer output)
  - `logs/edge_replay/cycle16d/historical_prices_cycle16d.json` (resolved prices)
  - `logs/edge_replay/cycle16d/coverage_audit.json`
- **Implementation pattern:** model on `scripts/edge_replay/g1_admission_sweep.py` (Codex + operator-approved E2 reference pattern). Same import structure (`PAPER_MIN_EDGE` from `config`), same outcome-blind admission logic, same per-slice IC §16 metric computation, same Markdown report style.

### 2.1 — Codex implementation spec

Required contract for `scripts/edge_replay/side_flip_counterfactual.py`:

| Aspect | Spec |
|--------|------|
| CLI entry | `argparse`-driven `main(argv)`. Args: `--dataset`, `--scores`, `--prices`, `--coverage`, `--output-dir`, `--json` (stdout JSON for machine reads), `--write-report` (path; default to companion governance ledger doc — see §3). |
| Deterministic | YES. No randomness. No LLM call. No network. No subprocess. Pure-Python over the frozen artifacts. |
| Side flip definition | For every row admitted by the production-proxy gate set in `g1_admission_sweep.py::_production_proxy_count`, derive `original_side ∈ {yes, no}` per the same `_infer_side` helper, then compute `flip_side = "no" if original_side == "yes" else "yes"`. Outcome evaluation uses `flip_side`. |
| Outcome evaluation | For each admitted row, compute `flip_win = (flip_side == "yes" and resolved_yes is True) or (flip_side == "no" and resolved_yes is False)`. PnL per row via the same convention `score_counterfactual_pnl.py` uses, but with `flip_side` substituted for `side`. |
| Slice grouping | Match scorer-forensics convention: (`signal_source` × `series_ticker` × `signal_type` × `news_class`). Compute per-slice `n`, `wins`, `losses`, `win_rate`, `pnl`, `ev_mean`, `ev_ci_95_lo`, `ev_ci_95_hi`. Use the same Wilson interval / bootstrap math as scorer_forensics if available; otherwise replicate. |
| IC §16 evaluation | For each slice, flag `ic16_eligible = (ev_ci_95_lo > 0) AND (trades >= 10)`. Top-line counts: total slices, IC §16 slices, raw positive-EV slices. |
| **Trivial-inversion disqualification** (HARD GATE — operator addition 2026-05-10) | Compute `market_implied_expected_wins_flip = sum(1 - market_yes_price/100 for each admitted row)`. Compute `actual_flip_wins = sum(flip_win for each admitted row)`. Emit `excess_wins_vs_market = actual_flip_wins - market_implied_expected_wins_flip`. If `excess_wins_vs_market <= 0.5` (within sampling noise of market-implied baseline on n≤30), the script must mark the verdict `revert_trivial_inversion` regardless of IC §16 outcome. This is the load-bearing anti-pattern guard per memory `feedback_market_implied_baseline.md`. |
| **Sub-slice consistency** (HARD GATE — operator addition 2026-05-10) | Decompose admitted-row flip-wins by each independent slice axis (signal_source, market_family, signal_type, news_class). For each axis, compute Fisher exact test (or simple proportion difference where n is too small) between flip-win rates across that axis's bins. If ANY axis shows non-uniform flip-win rates with p < 0.20 (loose threshold given n≤30) AND the dominant bin contributes >75% of wins, the script must mark the verdict `revert_uniform_consistency_failed` — a uniform across-axis pattern is required for a `keep` candidate. |
| Output | (a) Markdown report at `--write-report` path; (b) JSON summary on stdout when `--json`; (c) per-row JSONL artifact at `<output-dir>/side_flip_per_row.jsonl` for any post-hoc audit; (d) `<output-dir>/side_flip_summary.json` with the structured report payload. |
| Forbidden output labels | Reuse `FORBIDDEN_OUTPUT_PATTERN` guard from `g1_admission_sweep.py`. The script must NOT emit per-row `flip_win` labels in any admission-count-only artifact; outcome-sensitive fields are confined to the per-row JSONL and the slice-table block of the markdown report. |
| Tests | At minimum: a unit test in `tests/test_side_flip_counterfactual.py` covering (i) trivial-inversion disqualification fires when `excess_wins_vs_market <= 0.5`, (ii) IC §16-positive slice WITHOUT consistency = `revert_uniform_consistency_failed`, (iii) IC §16-positive AND consistent AND excess > 0.5 = `keep_candidate` (synthetic fixture). |

### 2.2 — Locked replay command

```
.venv/bin/python scripts/edge_replay/side_flip_counterfactual.py \
  --dataset    logs/edge_replay/cycle16d/replay_dataset.jsonl \
  --scores     logs/edge_replay/cycle16d/counterfactual_scores.json \
  --prices     logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --coverage   logs/edge_replay/cycle16d/coverage_audit.json \
  --output-dir logs/edge_replay/cycle17c-e3 \
  --write-report docs/governance/2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md
```

Codex must run this command verbatim post-implementation. Any flag deviation requires unlock.

## 3 — Locked acceptance bar (HARD)

A `keep` verdict on E3 requires **all four** of the following clauses to hold simultaneously. Failure of any one clause forces a `revert` outcome.

### Clause A — IC §16 baseline

- ≥1 (`signal_source` × `series_ticker` × `signal_type` × `news_class`) slice with `ev_ci_95_lo > 0` AND `trades >= 10`.

### Clause B — Trivial-inversion disqualification (operator-tightened 2026-05-10)

- `excess_wins_vs_market_flip > 0.5` on the full admitted cohort, where:
  ```
  excess_wins_vs_market_flip = actual_flip_wins - sum(1 - p_yes_at_decision_time)
  ```
- This guards against the cycle-16D anti-correlation framing trap: on a 12/12-YES baseline, a flip-sign mechanical pass produces 12 wins but the market-implied baseline is ≈10.995, giving an excess of ≈1.005. Mathematical inversion is therefore **near-zero excess** — indistinguishable from no-signal.
- Per memory `feedback_market_implied_baseline.md`: market-implied baseline (`Σ market_yes_price/100` for the original side; equivalent to `Σ (1 - p_yes)` for the flipped side) is the correct null, NOT 50% coin-flip.

### Clause C — Sub-slice consistency (operator-tightened 2026-05-10)

- Flip-sign win-rate must be uniform across the four slice axes (signal_source, market_family / series_ticker, signal_type, news_class) within sampling noise.
- "Uniform" means: for every axis with ≥2 bins each containing ≥3 admitted rows, the proportion test or Fisher exact comparison between bins yields p ≥ 0.20 (loose threshold for small-n).
- "Dominant-bin > 75%" failure mode: if the wins are concentrated in a single bin of any axis (e.g., all 12/12 wins from one source_class), the result is a sampling artifact, NOT signal evidence. The `revert_uniform_consistency_failed` verdict fires.

### Clause D — Single-variable charter compliance

- Only the new `scripts/edge_replay/side_flip_counterfactual.py` file is created. No production code, scorer, frozen artifact, or governance prompt is touched. (Tests for the new script are permitted under `tests/test_side_flip_counterfactual.py`.)
- Codex's commit log on the implementation must reflect ONLY this file + its test + the verdict-companion markdown that the script generates. Any drift = automatic revert.

### Revert conditions (any one fires `revert_required_*`)

- Clause A fails (no IC §16-eligible slice) → `revert_required_no_ic16_slice`.
- Clause B fails (`excess_wins_vs_market_flip <= 0.5`) → `revert_trivial_inversion`. **This is the most likely failure mode given the 12/12 baseline and small n.**
- Clause C fails (sub-slice non-uniformity) → `revert_uniform_consistency_failed`.
- Clause D fails (touch-surface drift) → `revert_charter_violation`.
- Replay cannot execute (artifact missing, parse failure, etc.) → `revert_replay_failed`.
- `trades < 10` post-flip across all slices (downstream gate asymmetry like `paper_price_sanity` inverting on NO sides) → `diagnostic_only_revert` per sketch §"Diagnostic-only marker". This counts as a revert against the 3-revert architectural-rethink budget.

### What a `keep` does NOT authorize

A `keep` verdict on E3 does **NOT** authorize:
- Any production code change.
- Any scorer change.
- Any live-trading flip.
- Cherry-picking the backup `5828ad2` Lever A.1 commit or any other deferred Wave-2/3 code.

A `keep` verdict authorizes ONLY:
- Filing PROFIT-EDGE-013 = "Cycle-17 §A deploy-candidate review for slice X" (where X is the IC §16-eligible slice the post-processing diagnostic surfaces).
- Cycle-17 §A reviewing whether Path 1 (production code change + corpus regen) should now be greenlit, with cross-corpus validation as a prerequisite.

The §A review is independent and out of scope for E3.

## 4 — Revert-budget tracker (charter)

| Experiment | Status | Counts toward 3-revert budget? |
|------------|--------|--------------------------------|
| E1 — Bayesian log-odds update rule | REVERTED 2026-05-07 (`edge-replay-cycle17c-e1-report.md`) | YES — 1/3 |
| E2 — G1 readiness admission sweep | AXIS_ABANDONED_BEFORE_CRITERIA_LOCK 2026-05-08 | NO (per ledger row exception — no implementation commit landed) |
| E3 — Side-inference flip-sign | LOCKED 2026-05-10 (THIS DOC); replay pending Codex | TBD on verdict |

If E3 reverts → 2/3. Room for E4 before architectural-rethink rule fires.

## 5 — Capital posture (re-affirmed for the lock)

PAPER-ONLY. Hard guardrail per Cycle-14 charter §5. NO experiment in cycle-17C — including a hypothetical E3 `keep` — flips this. Live trading authorization requires Cycle-17 §A review + cross-corpus validation + operator commit, none of which is in E3 scope.

## 6 — Verdict appendix framework (template — Claude fills in post-replay)

The following section is the verdict-template skeleton. After Codex runs the locked replay command (§2.2) and the script writes its companion report at `docs/governance/2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md`, Claude appends a verdict appendix to that companion report (NOT this criteria-lock doc) using this skeleton.

```markdown
# Cycle-17C E3 Verdict Appendix — Side-Inference Flip-Sign

**Verdict author:** Claude
**Date:** TBD (post-replay)
**Replay command:** Per criteria-lock §2.2 (verbatim).
**Replay artifacts:** logs/edge_replay/cycle17c-e3/{side_flip_summary.json, side_flip_per_row.jsonl}
**Companion report:** This document (Codex-generated; verdict appended below).

## Verdict label

One of: `keep_candidate` | `revert_required_no_ic16_slice` |
        `revert_trivial_inversion` | `revert_uniform_consistency_failed` |
        `revert_charter_violation` | `revert_replay_failed` |
        `diagnostic_only_revert`.

## Clause-by-clause evaluation

### Clause A — IC §16 baseline
- IC §16 slices found: <N>
- Top slice: <slice spec> with ev_ci_95_lo = <X>, trades = <T>
- PASS / FAIL: <verdict>

### Clause B — Trivial-inversion disqualification
- actual_flip_wins: <X>
- market_implied_expected_wins_flip: <Y>
- excess_wins_vs_market_flip: <X - Y>
- Threshold: > 0.5
- PASS / FAIL: <verdict>
- Interpretation: <how to read the excess against n=<N>; cite memory feedback_market_implied_baseline.md if relevant>

### Clause C — Sub-slice consistency
- Per-axis analysis:
  - signal_source: bins = <list>; flip-win-rates = <list>; min-comparison p-value = <P>; dominant-bin share = <S%>
  - market_family: <same>
  - signal_type: <same>
  - news_class: <same>
- Threshold: every axis bin-comparison p ≥ 0.20 AND no dominant-bin > 75%
- PASS / FAIL: <verdict>

### Clause D — Single-variable charter compliance
- Files added: <list> (must be exactly: scripts/edge_replay/side_flip_counterfactual.py, tests/test_side_flip_counterfactual.py, this verdict markdown)
- Files modified: <list> (must be empty)
- Forbidden touch surfaces verified clean: <yes/no>
- PASS / FAIL: <verdict>

## Final decision
<keep_candidate / revert_*> — <one-paragraph operator-facing rationale>.

## Routing
- IF keep_candidate: file PROFIT-EDGE-013 = "Cycle-17 §A deploy-candidate review for slice <X>"; do NOT alter production code; do NOT flip capital posture.
- IF revert_*: increment revert-budget tracker (E1 = 1/3 → E3 = 2/3 if revert here); pick E4 axis per sketch §165-178 alternatives (extraction prompt | keyword map already-explored | market mix structurally deferred); update PROFIT-EDGE-012 ledger row with verdict + decision_rationale.
- IF diagnostic_only_revert: also note structural finding about gate-side asymmetry; consider charter amendment for cycle-17D.

## Memory references applied
- feedback_market_implied_baseline.md — applied to Clause B threshold derivation.
- feedback_audit_scorer_before_verdict.md — applied to script-output sanity check before consuming verdict.

## Cross-references
- Criteria-lock: docs/governance/2026-05-10-cycle-17c-e3-criteria-lock.md
- Hypothesis sketch: docs/governance/2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md
- Charter: docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md
- Predecessor verdicts: edge-replay-cycle17c-e1-report.md, 2026-05-08-cycle-17c-e2-g1-admission-sweep.md
```

## 7 — Cross-references

- Hypothesis sketch: [`2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md`](2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md)
- Charter: [`2026-05-07-cycle-17c-charter-single-variable-redesign.md`](2026-05-07-cycle-17c-charter-single-variable-redesign.md)
- Experiment ledger schema: [`2026-05-07-cycle-17c-experiment-ledger-schema.md`](2026-05-07-cycle-17c-experiment-ledger-schema.md)
- E1 criteria-lock + verdict: [`2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md`](2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md), [`edge-replay-cycle17c-e1-report.md`](edge-replay-cycle17c-e1-report.md), [`2026-05-07-cycle-17c-e1-claude-verdict-appendix.md`](2026-05-07-cycle-17c-e1-claude-verdict-appendix.md)
- E2 axis-abandoned: [`2026-05-08-cycle-17c-e2-g1-admission-sweep.md`](2026-05-08-cycle-17c-e2-g1-admission-sweep.md)
- IC §16 authority: [`docs/IMPLEMENTATION_CONTRACT.md`](../IMPLEMENTATION_CONTRACT.md) §16
- Reference pattern for Path 3 implementation: [`scripts/edge_replay/g1_admission_sweep.py`](../../scripts/edge_replay/g1_admission_sweep.py)
- Memory: `feedback_market_implied_baseline.md` (load-bearing for Clause B threshold)
- Memory: `feedback_audit_scorer_before_verdict.md` (verdict discipline)
- Tracking: `PROFIT-EDGE-012`

---

**Operator note:** This criteria-lock formalizes the operator hard requirement (2026-05-10): "IC §16 alone is insufficient for E3 keep. The criteria-lock must include trivial-inversion disqualification against market-implied expected wins and per-slice consistency. If flip-side performance is indistinguishable from the market-implied baseline, E3 reverts/abandons even if the headline win count or IC §16 check looks good." Clauses B + C above are the formalization of that requirement.
