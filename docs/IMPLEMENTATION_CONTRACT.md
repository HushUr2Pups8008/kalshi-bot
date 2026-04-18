# Implementation Contract — Multi-Lane Trading Architecture

**Version:** 1.0
**Status:** LOCKED
**Authority:** This document governs all implementation work on the Type 1 / Type 2 / Type 3 integrated architecture. It is binding on both Claude and Codex. No section may be overridden during implementation without an explicit architectural approval recorded in writing.

---

## 1. System Invariants

These are non-negotiable truths. Any implementation that violates them is incorrect regardless of whether it passes tests.

---

### INV-1: Belief-Based System

**Description.** The system maintains a belief state per market — a probability estimate and confidence score — that is updated through structured evidence revision. The system does not accumulate raw signals and threshold them. It revises beliefs.

**Why it exists.** Signal counting produces additive noise. Belief revision produces calibrated estimates. The distinction determines whether the system degrades gracefully under noisy inputs or amplifies noise into trades.

**What breaks if violated.** Counterfeit confidence from signal count. Inability to distinguish "five confirming articles" from "one fact reported five times." Drift without detection. Loss of the deduplication and decay mechanisms that bound risk.

---

### INV-2: Probability and Confidence Are Separate Dimensions

**Description.** `current_estimate` (probability, 0–1) and `confidence` (0–0.95) are independent fields. They evolve through separate rules. A high probability estimate does not imply high confidence. A high confidence score does not imply a high or low probability.

**Why it exists.** Blending, readiness gating, and regime handling all require independent access to each dimension. Collapsing them produces a single scalar that encodes neither correctly.

**What breaks if violated.** The Trade Readiness Gate becomes unenforceable. The blending formula produces wrong effective weights. High-probability low-confidence beliefs drive execution without gate protection.

---

### INV-3: Stateful Dossier Model

**Description.** Each active market has exactly one dossier. The dossier is the authoritative belief state for that market. All slow-path trade candidates are derived from it. The dossier is not a cache of the latest signal — it is the accumulated, decayed, deduplicated belief.

**Why it exists.** Without a persistent belief state, each evaluation starts from scratch. The accumulation and structural lanes exist specifically to build belief over time. Statelessness collapses those lanes to the behavior of the fast lane.

**What breaks if violated.** Evidence diversity requirements become unenforceable. Drift detection cannot function. Recency scoring has no history to score against. The slow path loses all its value.

---

### INV-4: Purity of `/analysis`

**Description.** Every function in `/analysis` is a pure function: deterministic, no I/O, no database access, no network calls, no shared mutable state, no side effects. Functions in `/analysis` receive data and return data.

**Why it exists.** Purity makes `/analysis` functions testable in isolation, composable, and safe to call from any async context without race conditions or resource contention.

**What breaks if violated.** Test isolation is lost. Database calls inside pure functions introduce latency, failure modes, and coupling that cannot be mocked cleanly. The architecture's layer contract collapses.

---

### INV-5: Execution Isolation in `/trading`

**Description.** All trade decision logic — EV gating, position sizing, paper/live mode selection, risk guards — lives in `/trading` and only in `/trading`. No other layer makes execution decisions. `/trading` does not contain signal interpretation, belief update logic, or market analysis.

**Why it exists.** Execution is the highest-risk layer. Concentrating it allows consistent safety gate audits. Diffusing it makes safety guarantees impossible to verify.

**What breaks if violated.** Safety gates may be bypassed by logic in `/tasks` or `/analysis` that routes around them. Live trading may occur through unreviewed code paths.

---

### INV-6: No Uncontrolled Increase in Trade Frequency

**Description.** Adding new signal lanes must not increase raw trade frequency without proportional increase in signal quality. The Trade Readiness Gate is the primary control. The executor's existing EV gate is the secondary control. Both must remain active and unsoftened.

**Why it exists.** Frequency without selectivity destroys edge. The system's value comes from trading less often with more conviction, not more often with weaker conviction.

**What breaks if violated.** Negative expected value trades enter the queue. Paper trading performance degrades. The transition to live trading is unsafe.

---

### INV-7: No Degradation of Selectivity

**Description.** The addition of the accumulation and structural lanes must not lower the bar for what constitutes a trade candidate compared to the current fast-lane-only system. New lanes add candidates only when their belief state meets the full readiness gate. They do not lower existing fast-lane standards.

**Why it exists.** The current system has calibrated selectivity from paper trading history. Regression from that baseline is a loss, not a feature.

**What breaks if violated.** Win rate degrades. The accumulated calibration data from paper trading is invalidated. Edge erodes.

---

## 2. Architectural Boundaries

### `/feeds`

**Allowed.**
- Ingest external data from APIs and news sources.
- Normalize raw data into typed Python objects.
- Emit data to queues or task handlers.
- Handle connection errors, retry logic, and rate limiting.

**Never allowed.**
- Interpret market direction from ingested data.
- Make probability estimates.
- Write to the evidence store or any database.
- Call analysis functions.
- Execute trades or modify positions.

**Violation examples.**
- A feed function that filters headlines by relevance. (Interpretation.)
- A feed that writes evidence records directly. (Storage side effect.)
- A feed that calls `signal_analyzer.estimate_probability`. (Interpretation.)

---

### `/analysis`

**Allowed.**
- Compute scores, weights, estimates, and classifications from input data.
- Return structured results to callers.
- Import from `utils` for logging schemas only.
- Define domain types used across layers.

**Never allowed.**
- I/O of any kind: no database reads or writes, no network calls, no file operations.
- Maintaining or mutating module-level state.
- Calling functions in `/tasks`, `/feeds`, or `/trading`.
- Triggering side effects.

**Violation examples.**
- `dossier_builder.py` reading from `evidence_store.db` to get the current dossier before updating it. (I/O inside pure layer.)
- `decision_blender.py` logging a `BLEND_DECISION` event directly. (Side effect inside pure layer.)
- `regime_classifier.py` calling the Kalshi API for market metadata. (Network call inside pure layer.)

---

### `/tasks`

**Allowed.**
- Orchestrate calls to `/analysis` functions with data sourced from `/feeds` or the evidence store.
- Read from and write to the evidence store.
- Emit log events via `utils/logger.py`.
- Manage async execution, scheduling, and resource budgets.
- Pass trade candidates to the executor in `/trading`.

**Never allowed.**
- Contain trading logic: EV calculation, position sizing, mode decisions.
- Contain market analysis logic that belongs in `/analysis`.
- Modify the executor's safety gates or mode configuration.
- Bypass the Trade Readiness Gate.

**Violation examples.**
- `accumulation_task.py` computing a confidence-weighted blend inline instead of calling `decision_blender.py`. (Analysis logic in orchestration.)
- `blend_task.py` checking `paper_mode` and skipping execution accordingly. (Execution logic in orchestration.)
- `budget_manager.py` calling the Kalshi API directly. (Feed responsibility in orchestration.)

---

### `/trading`

**Allowed.**
- Apply EV gating to trade candidates.
- Enforce position limits and risk guards.
- Execute trades against the Kalshi API in live mode.
- Simulate execution in paper mode.
- Log trade outcomes.
- Read `signal_meta` from candidates for logging only.

**Never allowed.**
- Interpret `signal_meta` fields to make routing or sizing decisions beyond `readiness_gate_min_edge_override`.
- Update the evidence store or any dossier.
- Call `/analysis` functions.
- Modify blending weights or regime classifications.

**Violation examples.**
- The executor checking `signal_meta["disagreement_score"]` and halving position size. (New decision logic in `/trading`.)
- The executor calling `regime_classifier.compute_regime_weights`. (Analysis in execution layer.)
- The executor writing a dossier update after trade execution. (Storage side effect in execution layer.)

---

## 3. Belief System Rules

### BSR-1: State Update vs Confidence Update

A **state update** changes `current_estimate`. A **confidence update** changes `confidence` without changing `current_estimate`. These are mutually exclusive operations per evidence item.

**State update is triggered when ALL of the following hold:**
- The evidence source class differs from all evidence ingested in the current half-life window for this market.
- Headline n-gram overlap with any evidence in the current half-life window is < 0.35.
- The `drift_suspect` flag is False, OR the dossier is in recovery mode (halved displacement cap applies).

**Confidence update is triggered when ANY state-update condition fails.**

No evidence item triggers both operations. The classification is determined before the update is applied.

### BSR-2: Per-Update Displacement Cap

A single state update may not move `current_estimate` by more than **0.10 probability units**, regardless of evidence quality score or confidence weight. This cap is enforced inside `dossier_builder.update_dossier` and is not configurable per-call.

### BSR-3: Drift Detection and Freeze

**Drift detection condition:**
`abs(current_estimate - prior_estimate) > 0.25` AND the dossier has not received a cross-class state update since `prior_estimate` was set.

`prior_estimate` is snapshotted each time a cross-class state update occurs. It is not the initial estimate — it is the estimate at the last cross-class anchor.

**On drift detection:** Set `drift_suspect = True`. Freeze state updates. Confidence updates continue.

**Escape condition (either):**
1. One full market half-life elapses since freeze.
2. A cross-class signal arrives that meets state-update criteria.

**Recovery mode:** On escape, clear `drift_suspect`. Enter recovery for one additional half-life. During recovery: confidence updates unrestricted; state updates permitted with per-update displacement cap of **0.05** (half of normal). Trigger a forced LLM re-synthesis if budget is available. After recovery period, normal rules resume.

### BSR-4: Evidence Decay

Evidence weight decays continuously using the market's half-life:

```
effective_weight(evidence, now) = original_weight * exp(-ln(2) * age / half_life)
```

The decay function is applied at read time — stored weights are undecayed. Decayed effective weights are used in: dossier update weighting, recency score computation, and confidence evolution.

### BSR-5: Same-Class Diminishing Returns

For same-class signals within a rolling window equal to one market half-life, the nth signal's update weight is divided by n. Probability state updates from same-class signals alone are not permitted (they become confidence-only updates under BSR-1).

### BSR-6: Confidence Evolution

- Each cross-class state update: `confidence += quality_score * (1 - confidence) * 0.3`. This is bounded growth — confidence asymptotes toward 1.0 but is capped at 0.95.
- Each same-class confidence update: apply diminishing returns factor first, then same formula.
- On contradiction (opposite-direction cross-class signal with quality ≥ 0.6): `confidence -= 0.20`, floored at 0.05.
- Confidence is not increased by time passage. It is only increased by evidence and decreased by contradiction or regime uncertainty scaling at evaluation time.

### BSR-7: Evidence Identity Approximation

Two evidence items are treated as independent if and only if both conditions hold:
1. Different source classes.
2. Headline n-gram overlap < 0.35 against all evidence in the current half-life window for this market.

Failing either condition: evidence is classified as a cluster member of the nearest matching item. Cluster members trigger confidence-only updates subject to same-class diminishing returns.

**Accepted limitation.** Paraphrase deduplication is not implemented. Cross-class correlated sources that use different language are treated as independent. This is a known approximation. The per-update displacement cap (BSR-2) and drift detection (BSR-3) bound the risk from this limitation.

---

## 4. Decision and Execution Rules

### DER-1: Blending Formula

```
effective_confidence[i] = lane_confidence[i] * regime_weight[i]
p_blend = sum(effective_confidence[i] * lane_p[i]) / sum(effective_confidence[i])
```

Applied only to lanes that have a current estimate. Lanes without estimates are excluded from the sum.

### DER-2: Dominance Rule

When `effective_confidence[i] > 2 * sum(effective_confidence[j] for j != i)`, adopt `lane_p[i]` directly as `p_blend`. Do not average. Record `blend_mode = "dominant_lane"` with the winning lane ID.

### DER-3: Structural Fail-Safe — Tier 1

**Activation conditions (all must hold):**
- Structural prior confidence ≥ 0.70.
- `abs(structural_p - p_blend) > 0.30`.
- Fast-lane signal exists within 2 × fast-lane deduplication window.

**Behavior:** Candidate passes readiness gate. `readiness_gate_min_edge_override = 2 × default_min_edge`. Record `blend_mode = "structural_tier1_override"`.

### DER-4: Structural Fail-Safe — Tier 2

**Activation conditions (all must hold):**
- Structural prior confidence ≥ 0.70.
- `abs(structural_p - p_blend) > 0.30`.
- Structural prior has been stable (no recompute movement > 0.05) for at least one full recompute cycle.
- No fast-lane signal within 2 × fast-lane deduplication window.

**Behavior:** Candidate is blocked. Record `blend_mode = "structural_tier2_veto"`.

If fast-lane signal exists within the window, Tier 2 degrades to Tier 1 regardless of structural stability. The fast-lane signal is the regime-change escape valve.

### DER-5: Valid Trade Candidate

A candidate is valid for submission to the executor if and only if it passes the full Trade Readiness Gate (Section 5). A candidate that fails any gate condition must be dropped and its failure condition logged in the `BLEND_DECISION` event under `trade_blocked_reason`.

---

## 5. Trade Readiness Gate — Formal Specification

The gate is a stateless predicate. It is evaluated in `blend_task.py` before a candidate is submitted to the executor. All conditions must be satisfied. There are no exceptions except where explicitly noted.

### Gate Conditions

| # | Condition | Threshold | Applies To | Notes |
|---|---|---|---|---|
| G1 | Blended confidence | ≥ 0.35 | All candidates | Confidence is regime-scaled at evaluation (see G6) |
| G2 | Evidence source class diversity | ≥ 2 distinct source classes | Dossier-sourced only | Exempt: fast-lane candidates |
| G3 | Disagreement score | ≤ 0.20 | All candidates | 0.15–0.20: pass with `readiness_gate_min_edge_override = 1.5 × default` |
| G4 | Regime confidence | ≥ 0.40 | All candidates | Below 0.40: fail-safe mode active; thresholds tighten (see Section 6) |
| G5 | Dossier not `drift_suspect` | True | Dossier-sourced only | Exempt: fast-lane candidates; recovery mode dossiers are NOT drift_suspect |
| G6 | Recency score | ≥ 0.30 | Dossier-sourced only | Exempt: fast-lane candidates |

### G1 Detail — Regime-Scaled Confidence

At gate evaluation time:
```
scaled_confidence = blended_confidence * regime_confidence
```
G1 applies to `scaled_confidence`, not `blended_confidence`. The dossier's stored confidence is unmodified.

### G3 Detail — Disagreement Scoring

```
disagreement_score = confidence_weighted_std_dev(lane_p values)
```
Computed over lanes with active estimates only. At 0.15–0.20: candidate passes but `readiness_gate_min_edge_override` is set to 1.5 × default. Above 0.20: candidate blocked.

### G4 Detail — Fail-Safe Threshold Tightening

When regime confidence < 0.40:
- G1 threshold raises from 0.35 to 0.50.
- G3 threshold lowers from 0.20 to 0.15 (zero tolerance for disagreement under regime uncertainty).

### G6 Detail — Recency Score

```
recency_score = sum(effective_weight[i] for all evidence[i]) / sum(original_weight[i] for all evidence[i])
```
Where `effective_weight[i]` uses the decay formula from BSR-4. A fully fresh dossier scores 1.0. A dossier with all evidence fully half-life-decayed scores approximately 0.50. A dossier untouched for two half-lives scores near 0.0.

### Fast-Lane Candidate Exemptions

Fast-lane candidates are subject to G1 (using fast-lane signal confidence as blended confidence), G3, and G4 only. G2, G5, and G6 do not apply.

---

## 6. Regime Handling Rules

### RHR-1: Regime Weight Vector

Each market carries a regime weight vector `{fast: float, interpretation: float, structural: float}` summing to 1.0. Computed by `regime_classifier.compute_regime_weights(market)` at discovery time. Recomputed after each dossier update.

### RHR-2: Regime Confidence

```
H = -sum(w * log(w) for w in regime_weights if w > 0)
H_max = log(3)  # log of number of lanes
regime_confidence = 1 - (H / H_max)
```

Range: 0.0 (uniform, maximum uncertainty) to 1.0 (fully peaked, maximum confidence).

### RHR-3: Blend Weight Interpolation Under Uncertainty

```
effective_regime_weight[i] = (1 - regime_confidence) * (1/3) + regime_confidence * regime_weight[i]
```

This is applied before computing effective lane confidence in DER-1. At `regime_confidence = 0`, all lanes receive equal weight. At `regime_confidence = 1`, full regime weights apply.

### RHR-4: Confidence Scaling

At gate evaluation time: `scaled_confidence = blended_confidence * regime_confidence`. This is an evaluation-time transform. Stored confidence values are not modified.

### RHR-5: Fail-Safe Mode

Active when `regime_confidence < 0.40`. Effects:
- G1 threshold: 0.35 → 0.50.
- G3 threshold: 0.20 → 0.15.
- Regime weight interpolation (RHR-3) is already in effect at all times; no additional flattening is applied in fail-safe mode specifically.

### RHR-6: Regime Confidence Recovery

Regime confidence is recomputed by calling `regime_classifier.compute_regime_weights` after each dossier update. No separate recovery mechanic is needed — the regime weight vector evolves as evidence accumulates and the entropy of that vector naturally decreases as the market's behavior becomes clearer.

---

## 7. Executor Contract

### What the Executor Receives

The executor receives a `TradeCandidate` object. For blend-path candidates, `signal_meta` is populated as follows:

```python
signal_meta = {
    "source_lane": "blend",          # or "fast"
    "fast_lane_p": float | None,
    "fast_lane_confidence": float | None,
    "accumulation_p": float | None,
    "accumulation_confidence": float | None,
    "structural_p": float | None,
    "structural_confidence": float | None,
    "blended_p": float,
    "blended_confidence": float,
    "disagreement_score": float,
    "regime_weights": dict,
    "regime_confidence": float,
    "blend_mode": str,               # "weighted_blend" | "dominant_lane" | "structural_tier1_override" | "structural_tier2_veto"
    "readiness_gate_min_edge_override": float | None,
}
```

### What the Executor May Use

- `readiness_gate_min_edge_override`: When non-None, use as `min_edge` for this candidate only.
- All other `signal_meta` fields: pass through to trade log and observability events. No branching.

### What the Executor Must Ignore

All `signal_meta` fields except `readiness_gate_min_edge_override` must not influence execution routing, position sizing, EV calculation, or mode selection. The executor reads one field and logs the rest.

### Strict Boundary Statement

`/trading` must not gain new decision logic. All intelligence — regime classification, blending, belief revision, readiness gating — is resolved before the candidate reaches the executor. The executor enforces its pre-existing EV gate and risk guards. It does not add to them based on `signal_meta`.

---

## 8. Observability and Traceability Requirements

### Required Event Types

| Event | Emitted By | Required Fields |
|---|---|---|
| `MATCH_DIAGNOSTIC` | `market_matcher.py` | `market_ticker`, `headline_id`, `token_overlap_count`, `overlap_ratio`, `low_match_quality`, `pre_llm_semantic_overlap`, `pre_llm_overlap_ratio`, `pre_llm_would_block`, `pre_llm_headline_token_count`, `pre_llm_market_token_count` |
| `SIGNAL_ANALYSIS_DETAIL` | `signal_analyzer.py` | `market_ticker`, `method`, `base_probability`, `final_probability`, `llm_probability_movement`, `llm_useful`, `pre_llm_would_block_and_useful`, `pre_llm_headline_token_count`, `pre_llm_market_token_count` |
| `EVIDENCE_INGESTION` | `accumulation_task.py` | `market_ticker`, `evidence_id`, `source_class`, `is_duplicate`, `correlation_discount_applied`, `update_type` (`state`\|`confidence`), `dossier_version_before`, `dossier_version_after` |
| `DOSSIER_UPDATE` | `accumulation_task.py` | `market_ticker`, `dossier_version`, `prior_estimate`, `new_estimate`, `update_delta`, `confidence_before`, `confidence_after`, `evidence_ids_contributing`, `llm_called`, `drift_suspect`, `in_recovery` |
| `STRUCTURAL_PRIOR_RECOMPUTE` | `structural_task.py` | `market_ticker`, `prior_estimate`, `new_estimate`, `input_sources`, `llm_called`, `token_count` |
| `BLEND_DECISION` | `blend_task.py` | `market_ticker`, `fast_lane_p`, `fast_lane_confidence`, `accumulation_p`, `accumulation_confidence`, `structural_p`, `structural_confidence`, `regime_weights`, `regime_confidence`, `blended_p`, `blended_confidence`, `disagreement_score`, `blend_mode`, `trade_considered`, `trade_blocked_reason` |
| `CALIBRATION_CHECK` | resolution handler | `market_ticker`, `lane`, `lane_estimate`, `final_resolution`, `error` |

### Traceability Requirement

Every trade decision must be reconstructible post-hoc from the event log. Specifically:
- A trade execution event must be joinable to a `BLEND_DECISION` event via `market_ticker` + timestamp.
- A `BLEND_DECISION` event must be joinable to the contributing `DOSSIER_UPDATE` events via `evidence_ids_contributing`.
- A `DOSSIER_UPDATE` event must be joinable to its `EVIDENCE_INGESTION` events via `evidence_id`.

This chain — trade → blend → dossier → evidence — must be unbroken. Any event that breaks this chain is a traceability failure.

### No Silent Failures

If a candidate is blocked by the Trade Readiness Gate, `BLEND_DECISION` must be emitted with `trade_considered = True`, `trade_blocked_reason = <condition that failed>`. Blocked candidates are not silently dropped.

---

## 9. Collaboration Protocol

### Responsibilities

**Claude is responsible for:**
- Architectural integrity across all sessions.
- Resolving ambiguity in contract requirements before implementation proceeds.
- Reviewing implementations that touch belief system logic, blending rules, or regime handling.
- Identifying when an implementation decision could violate an invariant.
- Updating this contract if a refinement is approved.

**Codex is responsible for:**
- Precise implementation of specified behavior.
- Adherence to this contract without interpretation.
- Reporting blockers, ambiguities, or apparent contract conflicts before proceeding.
- Not inventing behavior in unspecified areas.

### Codex Rules

1. **No invented behavior.** If a behavior is not specified in this contract or in the specific task description, do not implement it. Stop and request clarification.
2. **No reinterpretation.** If an instruction appears ambiguous, do not resolve the ambiguity by choosing an interpretation. Surface it.
3. **No scope expansion.** Implementing a specified task does not authorize touching adjacent code unless explicitly included in the task.
4. **Contract conflicts are blockers.** If a task instruction appears to conflict with this contract, the task is blocked. Do not resolve the conflict by choosing which to follow.

### Claude Rules

1. **Resolve before handoff.** Any ambiguity in task specifications must be resolved before Codex begins implementation.
2. **Contract updates are explicit.** If architectural understanding evolves, this contract is updated in writing before affected tasks begin. Verbal or inline clarifications do not override the contract.
3. **Invariants are not negotiable during implementation.** If an implementation requires an invariant to be relaxed, that is a design discussion, not an implementation decision.

---

## 10. Roadmap Execution Rules

### Allowed Status Values

| Status | Meaning |
|---|---|
| `NOT_STARTED` | Task has not begun |
| `IN_PROGRESS` | Task is actively being worked |
| `COMPLETE` | Task is done and validated |
| `BLOCKED` | Task cannot proceed; reason must be documented |

### Rules

1. Status must be updated to `IN_PROGRESS` before work begins and `COMPLETE` immediately when done.
2. No task may be skipped. If a task is determined to be unnecessary, document the reason and mark it `COMPLETE` with a note, not deleted.
3. All dependency constraints from the roadmap must be respected. A task whose dependencies are not `COMPLETE` may not move to `IN_PROGRESS`.
4. `BLOCKED` tasks must document the specific blocker. A blocked task is not abandoned — it is held until the blocker is resolved.
5. Roadmap tasks are the unit of work. Implementing more than one task in a single change set is permitted only when the tasks are explicitly marked as a group. Unannounced bundling is a violation.

---

## 11. Change Control Rules

### What Requires Clarification (Claude resolves, no approval needed)

- Ambiguous behavior within a specified component.
- Unspecified edge cases within a specified behavior.
- Threshold or parameter values not defined in the contract.

### What Requires Redesign Discussion (must pause implementation)

- A new component not present in the roadmap.
- A change to the responsibility boundary of any layer.
- A new trigger or execution path not in the decision flow.
- Any change that touches INV-5 (execution isolation) or INV-6 (trade frequency).

### What Requires Explicit Approval Before Proceeding

- Changes to system invariants (Section 1).
- Changes to architectural boundaries (Section 2).
- Changes to the Trade Readiness Gate conditions or thresholds (Section 5).
- Changes to the executor contract (Section 7).
- Any change to how live trading is activated or gated.

### Non-Negotiable Statement

No architectural changes during implementation unless explicitly approved through this change control process. An implementation that silently changes architecture by adding logic to the wrong layer, softening a gate condition, or creating hidden coupling between layers is not an approved change — it is a contract violation, regardless of whether it passes tests.

---

## 12. Failure Mode Awareness

### FM-1: Belief Drift

**Description.** Dossier estimate moves directionally over time from accumulated weak same-class signals without a genuine underlying event.

**Mitigations.** Per-update displacement cap (0.10). Cumulative displacement audit at 0.25 against last cross-class anchor. Drift-suspect freeze with half-life escape.

**Failure signals.** `drift_suspect = True` appearing frequently across many markets simultaneously. `DOSSIER_UPDATE` events with `update_delta` consistently near 0.10 in one direction over many updates.

---

### FM-2: Evidence Correlation Errors

**Description.** Multiple correlated sources (different outlets, same fact) are treated as independent, artificially inflating confidence.

**Mitigations.** N-gram overlap deduplication within the half-life window. Source-class same-class diminishing returns. Per-update displacement cap bounds maximum single-event exposure.

**Failure signals.** High `evidence_ids_contributing` count on a dossier with low actual event volume. `EVIDENCE_INGESTION` events with `is_duplicate = False` but very similar content.

---

### FM-3: Regime Misclassification

**Description.** A market is classified with high regime confidence in the wrong regime. A structural market is treated as fast-reaction, or vice versa.

**Mitigations.** Probabilistic regime model degrades gracefully under uncertainty. Low regime confidence triggers fail-safe mode that flattens blend weights and tightens gate thresholds. Regime weights are recomputed as evidence accumulates.

**Failure signals.** `regime_confidence` staying high on markets that subsequently show behavior inconsistent with their regime weights. `CALIBRATION_CHECK` events showing systematic per-regime error divergence.

---

### FM-4: Overtrading on Weak Signals

**Description.** Low-confidence beliefs clear the Trade Readiness Gate and produce trades.

**Mitigations.** G1 requires blended confidence ≥ 0.35 (regime-scaled). G2 requires evidence diversity for dossier candidates. The executor's EV gate applies independently.

**Failure signals.** Increase in trade frequency without proportional increase in win rate. `BLEND_DECISION` events with `blended_confidence` consistently near 0.35.

---

### FM-5: Stale Dossier Decisions

**Description.** A dossier that was well-supported at build time is used to generate trades after its evidence has decayed below meaningful threshold.

**Mitigations.** G6 recency score ≥ 0.30 blocks stale dossiers at the readiness gate. Decay is continuous and automatic.

**Failure signals.** `BLEND_DECISION` events with high `blended_p` but low recency score being blocked by G6. Indicates a dossier that would have been tradable but has gone stale — normal behavior, but worth monitoring for frequency.

---

### FM-6: LLM Budget Exhaustion

**Description.** High market volume exhausts the global LLM call budget, leaving dossiers without synthesis updates and degrading slow-path signal quality.

**Mitigations.** Priority queue with expiry-weighted and disagreement-weighted prioritization. Circuit breaker at 3× budget depth pausing new enqueues. Fast lane is budget-isolated.

**Failure signals.** `BUDGET_PRESSURE` log events. Queue depth metrics growing monotonically. `DOSSIER_UPDATE` events with `llm_called = False` disproportionately frequent.

---

## 13. Definition of Done

### Correctly Implemented

The system is correctly implemented when:

1. All 140 existing tests pass without modification to test logic.
2. Each new component in the roadmap has at least one unit test covering its primary behavior and its primary failure mode.
3. Every `BLEND_DECISION` event contains all required fields (Section 8) with non-null values for at least 90% of events in paper trading.
4. Every trade execution event is joinable to a `BLEND_DECISION` event.
5. No function in `/analysis` has I/O, imports from `/tasks` or `/trading`, or contains module-level mutable state.
6. The Trade Readiness Gate is called on every blend-path candidate before executor submission. No bypass code path exists.
7. `drift_suspect` freeze and recovery are exercised by a test with a synthetic evidence sequence that triggers drift detection.
8. Budget manager circuit breaker fires correctly under synthetic high-volume load (covered by S4.3).

### Safe to Run in Paper Mode

The system is safe to run in paper mode when:

1. All "Correctly Implemented" criteria are met.
2. The executor's paper/live mode gate is intact and the system defaults to paper mode.
3. A full run (all three lanes active) completes without unhandled exceptions for a minimum of one hour in paper mode.
4. `BLEND_DECISION`, `DOSSIER_UPDATE`, and `EVIDENCE_INGESTION` events are appearing in the log at expected rates.
5. Trade frequency has not increased beyond 2× baseline fast-lane frequency (a proxy for selectivity preservation — if the slow path is generating more candidates than the fast lane alone historically did, the gate thresholds must be reviewed before proceeding).
6. No `CALIBRATION_CHECK` events show per-lane error exceeding 1.5× historical fast-lane baseline within the first 24 hours.

### Not Done Until

- A component exists but has no corresponding log events being emitted.
- A component passes unit tests but has never been exercised in an integrated paper trading run.
- The Trade Readiness Gate is implemented but `trade_blocked_reason` is not populated on blocked candidates.
- The `BLEND_DECISION` event is emitted but `blend_mode` is missing or always `"weighted_blend"` (indicating the dominance and fail-safe branches are untested).
