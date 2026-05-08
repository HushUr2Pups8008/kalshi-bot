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

E2 hypothesis (Codex + operator finalize at criteria-lock):

> Loosening readiness admission (G1 confidence floor and/or G6 sample-size floor) by a single calibrated step will increase production-proxy trade count materially (target n ≥ 30) so the IC §16 `trades ≥ 10` gate becomes reachable across multiple slices, producing direct evidence of whether the bot has signal at higher trade volumes.

### Specific candidate changes (Codex picks one)

| sub-axis | change | expected n shift | risk |
|---|---|---|---|
| (a) G1 confidence floor | lower by 0.05 | +20-50% trades | minor — established loosening direction |
| (b) G6 sample-size floor | lower from N to N-1 | +10-30% trades | minor — also loosening |
| (c) Combine (a) + (b) | both | +30-100% trades | violates single-variable; pick (a) OR (b) only |

Single-variable rule mandates (a) OR (b), not both. Recommend **(a) G1 confidence floor** as the first sub-experiment because:
- E1 evidence directly implicates confidence calibration (50% readiness drop at no other change).
- G1 is a single config value; touch is < 5 LoC.
- Effect is predictable and bounded.

### Acceptance vs charter

E2 (a) hypothesis projection: 12 → 18-30 production-proxy trades. Crosses IC §16 `trades ≥ 10` floor → candidate-fix eligible. If projection lands near 18-25, MDE for win-rate detection at α=0.05 ≈ 13-15pp. Detectable effect size is demanding but not impossible.

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

Pick E2 axis:
- **(A) Readiness admission, G1 floor -0.05** (Claude recommendation; rank-1 info gain post-E1)
- **(B) Readiness admission, G6 floor -1** (Claude rank-2; same axis, alternate sub-axis)
- **(C) Update rule, Kalman or uncapped Bayesian** (Codex choice; carries "tweaking same knob" risk)
- **(D) Different axis entirely** (operator picks; document rationale)

Once picked, Codex commits E2 criteria-lock, then implementation, then replay, then verdict. Same locked workflow as E1.

## Cross-links

- `docs/governance/edge-replay-cycle17c-e1-report.md` — Codex E1 report (this appendix companions).
- `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` — charter (operating rule + 3-revert architectural rule).
- `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` — E1 row populated; E2 row pending.
- `docs/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md` — original info-gain ranking.
- `docs/governance/2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md` — E1 criteria-lock.
- `tasks/trade_readiness_gate.py` — production G1-G6 thresholds (E2 candidate touch surface for sub-axes (a) and (b)).
- `analysis/dossier_builder.py` — E1 surface (now reverted to baseline).
- Memory: `feedback_market_implied_baseline.md` — relevant to E2 verdict reading.
- Memory: `feedback_audit_scorer_before_verdict.md` — relevant to E2 verdict reading.

## Capital posture

PAPER-ONLY. Locked. E1 revert does not change posture. E2 will not change posture.
