# Cycle-17C E3 hypothesis sketch — side inference flip-sign ablation

**Type:** E3 hypothesis sketch. Drafted by Claude per operator vote. Codex finalizes at criteria-lock; this doc is decision-support, not the criteria-lock itself.
**Drafted:** 2026-05-08 post-E2 axis-abandonment (`89c6f4e`).
**Authority:** `2026-05-07-cycle-17c-charter-single-variable-redesign.md`, `2026-05-07-cycle-17c-first-axis-pick-rationale.md` (rank-3), `2026-05-07-cycle-17c-experiment-ledger-schema.md`.
**Capital posture:** PAPER-ONLY. No live-trading flip is authorized by this experiment. Even a `keep` outcome triggers Cycle-17 §A deploy-candidate review before any production code change.

## TL;DR

E3 axis = side inference (rank-3). Sub-axis menu A/B/C; criteria-lock target is **A (flip-sign)**. Pre-lock verifications below. Structural caveat: cycle-17C replay infrastructure does NOT re-run signal extraction, so flip-sign cannot be tested by editing `analysis/signal_analyzer.py:458` alone. Implementation path must be picked at criteria-lock from three options below.

## Hypothesis (locked at sketch level; refined at criteria-lock)

> Flipping the side-inference convention at the trade-side decision point — `side = "yes" if edge > 0 else "no"` → `side = "no" if edge > 0 else "yes"` — will produce ≥1 IC §16-eligible slice on the frozen Cycle-16E corpus. If true, the bot's edge calculation is directionally informative but the side-mapping is inverted relative to actual market outcome. If false, side inference is not the binding axis; revert and proceed to next axis.

## Source verification (pre-lock requirement #1)

### Side-inference source line

Verified 2026-05-08 against HEAD (`89c6f4e`):

- File: `analysis/signal_analyzer.py`
- Line: `458` (rationale doc cited line 431; file has shifted since)
- Code: `side = "yes" if edge > 0 else "no"`
- Context: inside `keyword_estimate()`. `edge = estimated_prob - market.yes_prob`.
- Only side-assignment in the module (grep confirmed: 1 of 1 `side =` matches).

Side flows: `keyword_estimate` → `NewsMarketAnalysis.side` → `executor.py` + `scorer_forensics_audit.py`. No parallel LLM-side path; LLM contributes only to `estimated_probability`, side is derived at line 458 from final edge sign.

### Side distribution on frozen production-proxy cohort

Pre-lock requirement: verify current side distribution before flip-sign experiment.

- Cycle-16E production-proxy: 12/12 YES (per `edge-replay-cycle16e-scorer-forensics.md` and `executor.py:200-244` faithful port).
- Cycle-17C E1 (Bayesian log-odds, reverted): 2/2 YES (per `edge-replay-cycle17c-e1-report.md`).
- E2 G1 sweep: production_proxy=12 at every threshold; side distribution unchanged from baseline (sweep is admission-count only, not side-redistributing).

**No production-proxy NO trades observed across cycle-16E, E1, or E2.** The bot's side decision is functionally `side = "yes"` for the entire production-proxy cohort on this corpus.

This means flip-sign produces 12/12 NO trades on the same 12 candidates. Mechanical inversion. Implications below.

## Sub-axis menu

### A — Flip-sign (criteria-lock target)

Change: `side = "yes" if edge > 0 else "no"` → `side = "no" if edge > 0 else "yes"`.
Tests: pure sign-convention error. Hypothesis: edge calculation is correct in magnitude but inverted in direction.
Touch surface: see "Implementation path" below — choice deferred to criteria-lock.
Risk: minor. Single-line conceptual change. Reversible.

### B — Force-NO (optional pre-check, NOT bundled with A)

Change: `side = "no"` unconditionally for all admitted candidates.
Tests: whether YES bias of the keyword/LLM extraction caused the 12-trade loss cohort.
Distinction from A: A respects edge sign and inverts; B ignores edge sign entirely.
Status per operator: "Diagnostic-only unless criteria-lock defines slice behavior carefully." Recommend keeping outcome-blind pre-check (admission count only, no win evaluation), and only escalating to criteria-lock if A's results are ambiguous.

### C — Force-YES (control, not a fix candidate)

Change: `side = "yes"` unconditionally.
Tests: harness behavior under uniform-side. Production-proxy already appears 12/12 YES, so C should be a no-op.
Use: confirm harness/scorer pipeline produces same 12 YES results when forced — sanity check on instrumentation. Not a candidate-fix.

## Implementation path (Codex picks one at criteria-lock)

The cycle-17C replay pipeline does NOT re-run `signal_analyzer.py` during replay. Evidence rows in `evidence_store.db` already have `side` baked in from original ingestion. Replay rebuilds dossiers (via `reingest_dossier_updates_post_fix.py`) and dataset, but does NOT re-extract from raw news. **Editing line 458 alone produces zero replay-output change.**

Three viable paths:

### Path 1 — Production code change + corpus regeneration

- Edit `analysis/signal_analyzer.py:458`.
- Re-extract entire 272-row corpus through `signal_analyzer` (LLM + keyword paths).
- Regenerate `evidence_store.db` (or new sibling DB).
- Rebuild replay dataset.
- Run frozen scorer.

Pros: truest behavioral test; tests production code as it would deploy.
Cons: LLM-availability dependent (Ollama qwen3 must be available); non-determinism (LLM outputs vary); time cost (272 LLM calls); potentially violates "fixed corpus" rule depending on charter interpretation.

### Path 2 — Scorer counterfactual mode

- Add `--flip-side` flag to `scripts/edge_replay/scorer_forensics_audit.py`.
- Flag inverts side at win-evaluation step only; gates and admission counts unchanged.

Pros: lightweight; same corpus; same admission cohort; just inverts win/loss outcome per row.
Cons: modifies the "fixed audited scorer" — possible charter violation depending on interpretation. Mitigation: frame the flag as a counterfactual mode, not a behavioral change.

### Path 3 — New post-processing diagnostic script

- Pattern: same as `scripts/edge_replay/g1_admission_sweep.py`.
- New script: `scripts/edge_replay/side_flip_counterfactual.py`.
- Reads scorer output + replay dataset + historical prices.
- Computes flip-side wins/PnL/EV/IC §16 from existing artifacts.

Pros: zero scorer touch; zero signal_analyzer touch; cleanest charter compliance; lightweight.
Cons: not a behavioral implementation — operator may consider this insufficient for `keep` verdict if charter intent was production-code-grade test.

### Recommendation

**Path 3 for the experiment, Path 1 deferred to §A deploy-candidate review if E3 passes IC §16.**

Rationale: Path 3 is the closest pattern to the E2 G1 sweep that operator + Codex have already approved. It produces full IC §16 metrics on the existing frozen artifacts without touching scorer or signal_analyzer. If Path 3 finds IC §16-positive evidence, the §A deploy-candidate review process is the correct gate to require Path 1 confirmation before any production code change ships.

Codex may pick differently at criteria-lock. Operator overrides any of these.

## Pre-lock requirements (operator-specified)

1. ✓ Verify current side-inference source line (`analysis/signal_analyzer.py:458`).
2. ✓ Verify side distribution on frozen production-proxy cohort (12/12 YES across cycle-16E, E1, E2).
3. Acceptance = IC §16 only.
4. Revert default.
5. If flip-sign reduces trades below 10 (e.g., due to other downstream gate sensitivity), mark diagnostic-only and revert.
6. Explicitly state: this tests side-sign convention, NOT extraction-prompt logic, NOT update-rule, NOT readiness gates. Single-variable.

## Acceptance bar

E3 is candidate-fix per operator override of original rationale-doc framing ("not expected to be a `keep`").

Keep condition (IC §16):
- ≥1 (source × market_family × signal_type) slice with `ev_ci_95_lo > 0`, AND
- that same slice has `trades >= 10`.

Revert condition:
- 0 IC §16 slices, OR
- replay/counterfactual cannot execute, OR
- implementation touches behavior outside the locked surface, OR
- post-flip `trades < 10` for every slice (production-proxy gates may behave differently with NO sides — e.g., paper_price_sanity inverts price meaning).

Diagnostic-only marker:
- If flip-sign produces `trades < 10` purely because downstream gates filter NO sides differently, this is diagnostic information about gate-side asymmetry but does not constitute a candidate-fix outcome.

## Trivial-inversion concern (must address at verdict)

The 12/12 YES baseline means flip-sign produces 12/12 NO automatically. **0 wins on YES → 12 wins on NO is mathematical**, not signal evidence. The IC §16 acceptance bar (`ev_ci_95_lo > 0`, `trades >= 10`) passes mechanically with 12/12 wins on n=12.

This is the cycle-16D anti-correlation framing trap revisited (per `feedback_market_implied_baseline.md` memory). Verdict appendix MUST evaluate against:
1. **Market-implied expected wins under flip-sign:** `Σ (1 - p_yes_at_decision_time) ≈ 12 - 1.005 = 10.995`. If actual flip-sign wins ≈ 11, that is INDISTINGUISHABLE from no-signal. Only wins meaningfully above 11 (e.g., 12/12) are weak signal evidence on n=12.
2. **Sub-slice consistency:** does flip-sign win uniformly across (a) keyword-only vs LLM-blended trades, (b) market_family slices, (c) signal_type slices? Uniform = signal-true. Mixed = sampling artifact on small n.
3. **Effect-size vs MDE:** at n=12 with 12/12 wins on flipped NO, the binomial CI is wide. Even 12/12 may have `ev_ci_95_lo` close to zero given variance on small n.

**Per IC §16 anti-pattern guidance in charter:** A `keep` verdict requires evidence beyond mechanical inversion. Operator + Claude verdict appendix must explicitly disqualify trivial-inversion pass and require a deploy-candidate §A review with cross-corpus validation before any production change.

## Sample size + MDE

Frozen production-proxy: n=12. MDE for win-rate detection at α=0.05 ≈ 25pp.

Flip-sign produces deterministic 12/12 wins on this cohort (mechanical). For meaningful signal evidence:
- Per-trade EV would need to be > market-implied expected EV on flipped side.
- Or: cross-corpus replication on a non-cycle-16D market mix (outside fixed-corpus scope; defer to §A review).

## Why NOT same-axis sub-experiments bundled

Per single-variable rule: A only as criteria-lock implementation. B (force-NO) is structurally different — it ignores edge sign. C (force-YES) is sanity. Bundling A + B + C violates single-variable. If A's results are ambiguous, B can be filed as E4 separately.

## Why this is the right E3

Per first-axis-pick rationale rank-3:
- Single-line conceptual change.
- Cleanly orthogonal to E1 (update rule) and E2-failed (readiness).
- Cycle-14 ruled out at synthetic Lane B, but cycle-16E + E1 + E2 all show 12/12 YES production-proxy — the bot is, in production, only ever producing YES-side bets. If signal direction is genuinely inverted, flip-sign reveals it.
- Effect-if-real: large. Effect-if-null: cleanly rules out side as cycle-17C remaining axis.
- Diagnostic value high either direction.

## Alternative axes if E3 reverts

Per first-axis ranking, post-E3-revert candidates:
- Rank-5: extraction prompt re-test (LLM-availability dependent, higher effort).
- Sub-readiness G3 / G4 sweeps (5 separate sub-axes per single-variable rule; heavy).
- Charter-level: reconsider fixed-corpus constraint given 12-row admission ceiling.

## Cross-links

- `docs/_archive/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md`
- `docs/_archive/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` (E3 row pending)
- `docs/_archive/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md` (rank-3)
- `docs/_archive/governance/2026-05-07-cycle-17c-e1-claude-verdict-appendix.md` (E2 axis-abandoned + E3 menu)
- `docs/_archive/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md` (E2 sweep evidence)
- `analysis/signal_analyzer.py:458` (touch surface, Path 1)
- `scripts/edge_replay/scorer_forensics_audit.py` (touch surface, Path 2)
- `scripts/edge_replay/g1_admission_sweep.py` (Path 3 reference pattern)
- Memory: `feedback_market_implied_baseline.md` (trivial-inversion concern is the same trap)
- Memory: `feedback_audit_scorer_before_verdict.md` (verdict appendix must audit against trivial inversion)

## Capital posture

PAPER-ONLY. Locked. E3 result does not change posture. Even if E3 produces IC §16-eligible slice, §A deploy-candidate review applies before any production code change.
