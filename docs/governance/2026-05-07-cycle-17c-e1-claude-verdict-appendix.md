# Cycle-17C E1 Claude verdict appendix + E2 axis recommendation

**Type:** Claude N6-equivalent verdict appendix to `edge-replay-cycle17c-e1-report.md` + E2 axis recommendation per cycle-17C charter no-overlap rule.
**Drafted:** 2026-05-08 post-Codex E1 revert (`2312f17`).
**Authority:** `2026-05-07-cycle-17c-charter-single-variable-redesign.md` §"Sequencing" + §"Cycle-17C failure modes".
**Concur with Codex verdict:** `revert_required_no_ic16_slice`.

## TL;DR

E1 reverted cleanly (commit `2312f17`). HEAD restored to baseline-equivalent state (verified: `git diff HEAD c913ffd -- analysis/dossier_builder.py` empty). E1 ledger row populated correctly. **Fix attempt #1 of 3** toward the architectural-conversation rule. 2 more reverts → halt and rethink axis.

E2 recommendation: **readiness admission (axis #4 in original ranking)** with a single-threshold loosening change. Rationale below.

## Independent voice on E1 result

Codex's report is accurate. The verdict matches charter rules cleanly. Adding two non-blocking observations:

### Observation 1 — what E1 actually changed

Variant comparison vs cycle-16E baseline:

| variant | cycle-16E | E1 | delta |
|---|---:|---:|---:|
| `baseline_abs_edge` | 237 | 237 | 0 |
| `readiness_only` | 182 | 90 | -92 |
| `paper_price_sanity` | 110 | 110 | 0 |
| `readiness_plus_price_sanity` | 63 | 24 | -39 |
| `readiness_price_signed_edge` | 63 | 24 | -39 |
| `production_proxy` | 12 | 2 | -10 |

**Key finding:** `baseline_abs_edge` is unchanged (237). `paper_price_sanity` is unchanged (110). The Bayesian log-odds change did NOT alter `abs(edge) >= min_edge` admission count. **Readiness gate became the binding constraint** — readiness-only dropped 50% (182 → 90), and downstream gates compounded from there.

This means: log-odds didn't change *which trades* hit the edge threshold; it changed how *confident* the dossier was about each estimate. Since readiness gates filter on confidence-floor (G1) and sample-size (G6), log-odds-derived estimates fail those thresholds at twice the cycle-16E rate.

### Observation 2 — "less negative P&L" is not edge

Production-proxy P&L improved from -$1.005 (baseline) to -$0.150 (E1). On a naive read this looks better. **It's not.**

E1 produced 2 trades vs baseline's 12. Smaller P&L magnitude is the mechanical consequence of fewer trades. Per-trade economics are identical (both 0 wins). If you scaled cycle-16E baseline down to 2 trades, you'd expect approximately -$0.17 P&L. E1 produced -$0.15. Within sampling noise of "same per-trade economics."

This is exactly the "directionally better but not deploy-positive" trap charter §"Anti-patterns" warns against. Codex correctly rejected it via `revert`.

## Revert correctness check

| check | result |
|---|---|
| Implementation commit identifiable | ✓ `fa8e15a` |
| Revert commit references implementation | ✓ `git revert fa8e15a` per ledger row |
| HEAD diff vs `c913ffd` baseline empty for `analysis/dossier_builder.py` | ✓ verified |
| Test changes reverted | ✓ `tests/test_dossier_builder.py` not present in HEAD diff |
| New fixture set retained per charter test strategy | per Codex report — legacy preserved as revert anchor |
| 51 tests passed post-revert | ✓ per Codex report |
| ruff clean | ✓ |
| No `update_dossier` behavior drift | ✓ |

**Revert clean.** No follow-up needed.

## Lessons learned (for E2 axis pick)

E1 reverted, but produced testable evidence:

1. **Probability distribution is not the binding constraint** for edge admission on this corpus. `baseline_abs_edge` count is unchanged across cycle-16E and E1 — meaning the SAME 237 dossier rows produce `abs(edge) >= min_edge`. Whether the bot uses additive or log-odds, those 237 are the candidate pool.

2. **Readiness gate is now the load-bearing filter.** Cycle-16E confirmed audited scorer; E1 confirmed update-rule changes cascade primarily through readiness, not edge. Readiness drop from 182 → 90 under log-odds suggests confidence/G6 calibration is more sensitive to estimate-spread than edge calibration.

3. **Bayesian log-odds + ±2.0 cap + low `original_weight` is intrinsically conservative.** Most evidence rows have `original_weight < 1`. Multiplied by `logit(implied_probability)` (typically `[-2.2, +2.2]` after eps clamp), then capped at ±2.0, the per-evidence log-odds delta is small. Over the corpus, posteriors don't drift far from neutral. This explains the 50% readiness-confidence drop.

4. **Same-axis re-test with a different formula (Kalman, uncapped Bayesian, exponential decay) likely produces similar conservatism.** All three are intrinsically anchor-pulling forms vs additive's "step-toward-evidence" form. Lower expected info gain than switching axis.

## E2 axis recommendation: readiness admission

Per first-axis-pick info-gain ranking, readiness admission was rank #4. Promote to #1 candidate for E2 because E1 surfaced readiness as the load-bearing filter.

### Recommended hypothesis sketch

E2 hypothesis (operator finalizes after sweep; Codex locks at criteria-lock):

> Lowering `G1_CONFIDENCE_THRESHOLD` (`tasks/trade_readiness_gate.py:69`, currently `0.05`) by an explicit calibrated step will admit additional production-proxy candidates, allowing IC §16 `trades >= 10` to be reached on at least one slice.

This is a **diagnostic-only** experiment by construction: loosening readiness reveals whether readiness was masking a positive slice. It cannot create signal where none exists. Criteria-lock revert-default is mandatory regardless of P&L direction.

### G6 verification note (correction)

V1 of this appendix proposed a "G6 sample-size floor" sub-axis. **Incorrect.** Source verification:

- `tasks/trade_readiness_gate.py:118` defines `G6_RECENCY_THRESHOLD = 0.30`.
- G6 predicate at lines 200-202: `recency_score < 0.30 → fail`.
- G6 is a **recency floor**, not a sample-size floor. No sample-size gate exists in the readiness predicates (G1-G6 are: scaled-confidence, source-class diversity, disagreement, regime-confidence, dossier-drift, recency).

G6 sub-axis struck. E2 reduces to G1 only.

### Sub-axis: G1 explicit-threshold sweep

Outcome-blind admission-count sweep over candidate G1 values **before** criteria-lock:

| G1 threshold | meaning |
|---|---|
| 0.05 | current production value |
| 0.04 | smallest loosening below current |
| 0.03 | mid-loosening |
| 0.02 | larger loosening |
| 0.01 | near-disabled |
| 0.00 | effectively disabled (G1 always passes) |

Sweep computes admission counts only — not wins, not P&L, not market-implied expected wins, not EV, not IC §16 slices. The sweep is **not an IC §16 replay** and cannot justify keep/deploy on its own.

**Outcome-blind sweep contract (Codex implements):**

- Sweep script must fail if any result field, output column, intermediate variable, log line, or imported scorer module path matches `win|pnl|profit|resolution|settlement|ev|ic16` (case-insensitive substring check at startup + per-row guard).
- Sweep stops at admission-count-per-variant. No win evaluation, no P&L computation, no slice reporting.
- Sweep report must explicitly disclaim: "This is not an IC §16 replay. Admission counts are projection-only and cannot justify keep/deploy."

**Operator picks the smallest loosening that projects production-proxy `n >= 10`.**

If only `0.00` (effectively disabled) crosses `n >= 10`, readiness axis is underpowered. Abandon. Pick rank-3 (side inference) or rank-5 (extraction prompt) per first-axis info-gain ranking.

### Why NOT a single-step "G1 -0.05" edit

V1 proposed "lower G1 by 0.05." Arithmetic: `0.05 - 0.05 = 0.00` = functional disabling, not a calibrated step. Replaced with explicit-threshold sweep so operator picks a defensible value with admission-count evidence rather than arithmetic luck.

### Acceptance vs charter

E2 G1 sweep produces an admission-count projection. Operator either:

1. **Picks threshold X** where projected production-proxy `n >= 10`. Codex commits E2 criteria-lock at G1 = X. Then E2 implementation = single-line config change. Then replay. Then verdict (full IC §16 acceptance bar applies — `>= 1 slice with ev_ci_95_lo > 0` AND `trades >= 10`).
2. **Abandons readiness axis.** Documents axis-exhausted rationale. Picks alternate axis from rank-3 / rank-5.

Either path honors single-variable + revert-default + no-overlap rules.

### Why NOT same-axis Kalman / uncapped Bayesian / exponential decay

- All three are anchor-pulling forms; expected to produce similar admission-tightening.
- Re-testing the update-rule axis with a third formula = "tweaking the same knob" anti-pattern.
- Diminishing info gain.

If operator strongly prefers another update-rule formula, file as E2-alternative; recommend rank-2 priority.

### Why NOT side inference / extraction prompt / market-family / keyword map

- Side inference: still diagnostic-only per cycle-14 trace. Cycle-16E showed 12/12 YES production-proxy; flipping side wouldn't help unless bot's signal is genuinely NO-direction-correct (no evidence for that).
- Extraction prompt: still LLM-availability-dependent + high effort + cycle-15B + cycle-16E both confirmed extraction is repaired at synthetic level.
- Market-family selection: still violates fixed-corpus constraint.
- Keyword map: still already-explored.

## Operator action required

Pick E2 path:
- **(A) G1 explicit-threshold sweep** (recommended). Codex runs outcome-blind admission-count sweep across `{0.05, 0.04, 0.03, 0.02, 0.01, 0.00}`. Operator picks smallest threshold projecting production-proxy `n >= 10`, OR abandons readiness axis if only `0.00` reaches `n >= 10`.
- **(B) Update rule re-test** (Kalman / uncapped Bayesian / exponential decay). Carries "tweaking same knob" anti-pattern risk per E1 evidence — baseline_abs_edge unchanged at 237 across both update rules.
- **(C) Different axis** (rank-3 side inference, rank-5 extraction prompt, or operator-documented rationale).

If (A): Codex bundles this appendix amendment + sweep script (`scripts/edge_replay/g1_admission_sweep.py` or operator-final path) + sweep report (`docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md`) in **one** commit. Operator then picks threshold or abandons.

If (B) or (C): Codex commits E2 criteria-lock under chosen axis. Same locked workflow as E1.

## Cross-links

- `docs/governance/edge-replay-cycle17c-e1-report.md` — Codex E1 report (this appendix companions).
- `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` — charter (operating rule + 3-revert architectural rule).
- `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` — E1 row populated; E2 row populated (`axis_abandoned_before_criteria_lock`); E3 row pending.
- `docs/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md` — original info-gain ranking.
- `docs/governance/2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md` — E1 criteria-lock.
- `tasks/trade_readiness_gate.py` — production G1-G6 thresholds (E2 candidate touch surface for sub-axes (a) and (b)).
- `analysis/dossier_builder.py` — E1 surface (now reverted to baseline).
- Memory: `feedback_market_implied_baseline.md` — relevant to E2 verdict reading.
- Memory: `feedback_audit_scorer_before_verdict.md` — relevant to E2 verdict reading.

## Capital posture

PAPER-ONLY. Locked. E1 revert does not change posture. E2 will not change posture.
