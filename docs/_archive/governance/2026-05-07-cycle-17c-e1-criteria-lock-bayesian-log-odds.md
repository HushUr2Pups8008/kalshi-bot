# Cycle-17C E1 criteria lock — Bayesian log-odds update rule

**Type:** criteria-lock commit artifact. Must land BEFORE any E1 implementation commit.
**Drafted:** 2026-05-07 by Codex.
**Authority:** `2026-05-07-cycle-17c-charter-single-variable-redesign.md`, `2026-05-07-cycle-17c-first-axis-pick-rationale.md`, `2026-05-07-cycle-17c-experiment-ledger-schema.md`.
**Capital posture:** PAPER-ONLY. No live-trading flip is authorized by this experiment.

## Locked Semantic Check

Verdict: `EvidenceScore.implied_probability` is **dossier-state independent** for the purpose of E1. It is not a posterior conditioned on `Dossier.current_estimate`.

Source evidence:

- `analysis/evidence_types.py:26` defines `Evidence.implied_probability` as "what this evidence implies for market resolution."
- `analysis/evidence_types.py:44` carries that value through to `EvidenceScore` for belief update.
- `analysis/signal_analyzer.py:1-11` describes the analyzer as estimating a probability shift implied by news relative to current market price.
- `analysis/signal_analyzer.py:395-482` keyword path computes `estimated_prob` from market price plus news-derived shift; it does not read dossier state.
- `analysis/dossier_builder.py:95-163` is the first point where `current_estimate` enters the state update rule.

Important nuance: `implied_probability` is market-price anchored, not prior-free. It may incorporate current Kalshi market price (`market.yes_prob`) in the extraction layer. It does not incorporate the dossier's accumulated posterior state. Therefore E1 treats it as an evidence-only likelihood-ratio input relative to neutral 0.5 for the dossier update experiment.

Formula consequence:

- Use `evidence_log_lr = original_weight * logit(implied_probability)`.
- Do **not** use `logit(implied_probability) - logit(current_estimate)`.
- The subtraction form would treat `implied_probability` as a posterior already conditioned on dossier state. That contradicts the semantic check above and would erase repeated evidence when evidence agrees with the current dossier estimate.

If later source review proves `implied_probability` is posterior-like with respect to dossier state, E1 implementation must halt and this criteria-lock must be superseded before code changes land.

## E1 Hypothesis

Replacing the current additive weighted-delta update in `analysis/dossier_builder.py:update_dossier` with a Bayesian log-odds update will convert repaired extraction signal into a differentiated probability distribution, producing at least one IC §16-eligible slice on the frozen Cycle-16E corpus.

## Locked Formula

Constants:

```python
EPSILON = 0.001
PER_EVIDENCE_LOG_LR_CAP = 2.0
NEUTRAL_PRIOR = 0.5
```

State-update pseudocode:

```python
current = dossier.current_estimate if dossier.current_estimate is not None else NEUTRAL_PRIOR
current_p = clamp(current, EPSILON, 1.0 - EPSILON)
evidence_p = clamp(evidence_score.implied_probability, EPSILON, 1.0 - EPSILON)

current_log_odds = logit(current_p)
raw_log_lr = evidence_score.original_weight * logit(evidence_p)
capped_log_lr = clamp(raw_log_lr, -PER_EVIDENCE_LOG_LR_CAP, PER_EVIDENCE_LOG_LR_CAP)

new_estimate = sigmoid(current_log_odds + capped_log_lr)
```

First state update policy: if `current_estimate is None`, seed from neutral 0.5 through the same log-odds update. Do not seed directly to `implied_probability`.

Cap policy: per-evidence log-odds delta cap `±2.0`. There is no total posterior cap beyond probability clamp to `(0.001, 0.999)`.

Order semantics: capped per-evidence log-odds updates are order-sensitive. That matches the current implementation's order-sensitive additive cap.

## Implementation Surface

Allowed production behavior change:

- `analysis/dossier_builder.py:update_dossier`, state-update estimate math only.

Must remain unchanged:

- `classify_update` behavior.
- `confidence` growth and contradiction penalty.
- BSR-3 drift, recovery, freeze, and anchor timestamp state machine.
- `EvidenceScore` schema.
- extraction, keyword maps, LLM prompt, readiness gates, scorer, replay corpus, and capital settings.

Test strategy:

- Add a new fixture/test set that locks the Bayesian log-odds expected outputs.
- Keep legacy additive fixtures available as the revert anchor; do not delete the historical expectations from the evidence trail.
- Add at least one test proving the LR direction is `original_weight * logit(implied_probability)`, not subtracting `current_estimate`.

No feature flag. Revert by `git revert <E1-implementation-commit>`.

## Replay Command

The frozen scorer and corpus remain unchanged. E1 implementation changes dossier update behavior, then replay uses the Cycle-16E scorer path with E1-specific output files:

```bash
.venv/bin/python scripts/edge_replay/scorer_forensics_audit.py \
  --dataset logs/edge_replay/cycle16d/replay_dataset.jsonl \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --endpoint-diagnosis logs/edge_replay/cycle16d/endpoint_diagnosis.json \
  --output logs/edge_replay/cycle17c/e1/scorer_forensics.json \
  --corrected-scores logs/edge_replay/cycle17c/e1/counterfactual_scores_production_proxy.json \
  --report docs/_archive/governance/edge-replay-cycle17c-e1-report.md
```

If the replay wrapper evolves before implementation, the E1 result report must quote the exact command actually run and explain any command-only difference from this lock.

## Acceptance Bar

E1 is a candidate-fix experiment, not diagnostic-only upfront.

Keep condition:

- At least one `(source × market_family × signal_type)` slice has `ev_ci_95_lo > 0`, AND
- that same slice has `trades >= 10`.

Revert condition:

- `0` IC §16 slices, OR
- replay cannot execute against the frozen scorer/corpus, OR
- implementation touches behavior outside the locked surface, OR
- post-replay `trades < 10` for every slice, making IC §16 unreachable.

If E1 passes IC §16, it does not automatically update the baseline or authorize live trading. It instantiates Cycle-17 §A deploy-candidate review for the proven slice: slice-specific risk review, capital allocation, kill-switch plan, and an operator commit citing the replay report. Baseline pointer updates only after §A acceptance.

## Sample Size + MDE Disclosure

Frozen baseline: `12` production-proxy trades, `0` wins, `1.005` market-implied expected wins, `-$1.005` P&L, `0` IC §16 slices.

Pre-replay projection: E1 is expected to materially change admission by changing probability distribution. The first-axis rationale projected a plausible range of `5-50` production-proxy trades. Because that range crosses the IC §16 `trades >= 10` threshold, E1 remains candidate-fix eligible at criteria-lock time.

MDE plan:

- Report observed trade count `n`.
- Report market-implied expected wins, not a 50% coin-flip baseline.
- Report whether any slice reaches `trades >= 10`.
- If `n=12`, the rough detectable win-rate shift remains about `25pp`.
- If `n=30`, rough detectable shift is about `10pp`.
- If `n=50`, rough detectable shift is about `6pp`.

Any post-replay claim weaker than IC §16 is diagnostic-only and does not justify keeping the code change.

## Cross-links

- `docs/_archive/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md`
- `docs/_archive/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md`
- `docs/_archive/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md`
- `docs/_archive/governance/edge-replay-cycle16e-scorer-forensics.md`
- `analysis/evidence_types.py`
- `analysis/signal_analyzer.py`
- `analysis/dossier_builder.py`
