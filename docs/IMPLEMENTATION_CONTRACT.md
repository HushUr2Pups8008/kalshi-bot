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
| G1 | Blended confidence | ≥ 0.05 (cycle-3 LOCK targets 0.04 at Wave-3 deploy) | All candidates | Confidence is regime-scaled at evaluation (see G6). G1 history: original PHASE-3 = 0.35; PROFIT-EDGE-003 G1 calibration follow-up moved to 0.05; cycle-3 Lever B 0.04 LOCK addendum (`docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md`) targets 0.04 / failsafe 0.08 / 2× ratio invariant at Wave-3 deploy ≥ 2026-06-17. Implementation source-of-truth: `tasks/trade_readiness_gate.py:G1_CONFIDENCE_THRESHOLD`. |
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

**Evaluation model.** The gate evaluates all conditions without short-circuiting and collects all failure reasons. When `regime_confidence < 0.40`, G4 fails AND the tighter G1/G3 thresholds are applied. The tighter thresholds are not dead code — they appear in `failure_reasons` when they also fail, providing diagnostic distinction between "blocked only by regime uncertainty" versus "blocked by regime uncertainty AND weak signal." This distinction is used by the S4.2 observability review and S3.6 calibration monitoring.

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
| `BLEND_DECISION` | `blend_task.py` | `market_ticker`, `fast_lane_p`, `fast_lane_confidence`, `accumulation_p`, `accumulation_confidence`, `structural_p`, `structural_confidence`, `regime_weights`, `regime_confidence`, `blended_p`, `blended_confidence`, `disagreement_score`, `blend_mode`, `trade_considered`, `trade_blocked_reason`, `evidence_ids_contributing` |
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
5. **"Capture" is byte-faithful.** If a task says "capture" production configuration into a repo template (e.g., installed launchd plist → `*.plist.template`), the only legal transformation is allowlisted token substitution (per §15). Do NOT infer behavioral content from spec docs, README, or intent. If the source artifact cannot be read (sandbox blocked, file absent), the task is blocked — escalate per §9 Claude rule 4. Authoring a template "from intent" is a contract violation, not a workaround.

### Claude Rules

1. **Resolve before handoff.** Any ambiguity in task specifications must be resolved before Codex begins implementation.
2. **Contract updates are explicit.** If architectural understanding evolves, this contract is updated in writing before affected tasks begin. Verbal or inline clarifications do not override the contract.
3. **Invariants are not negotiable during implementation.** If an implementation requires an invariant to be relaxed, that is a design discussion, not an implementation decision.
4. **Sandbox-blocked source reads escalate, not infer.** If the source artifact required for a capture task (production config, installed plist, live `.env`) cannot be read in the current execution sandbox, the correct response is "I cannot safely read X; approve read access or provide contents. Items dependent on this source are blocked." Do NOT proceed by inferring content from sibling docs.

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
6. **Runtime-observation tasks** are tasks whose completion depends on accumulated evidence from live or paper-mode bot runs rather than on code or test artifacts. For these tasks, the roadmap Notes column is the authoritative record of observation state, window dates, and pass/fail verdicts. Status transitions for runtime-observation tasks follow the same four allowed values, but IN_PROGRESS may carry a sub-state in the Notes column (e.g., "window open since [date]", "window closed [date]; checklist under review"). A runtime-observation task may not move to COMPLETE until a written pass/fail verdict has been recorded in the Notes column. S4.5b and S4.5c are the current runtime-observation tasks.

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
- **Behavioral deploys (intake / classifier / blender / gates / sizing) without replayed-EV evidence — see §16 (added cycle 11.5 post-strategic-redirect).**

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

---

## 14. Phase 3 Implementation Clarifications

This section resolves all ambiguities identified in the Phase Gate Review (2026-04-19) before Stage 3 implementation begins. These clarifications carry the same authority as the rest of the contract. They are binding on both Claude and Codex.

---

### CL-1: Lane Meeting Point — How blend_task is Triggered

The fast lane is the trigger for blend evaluation. When `signal_analyzer` produces a `SignalAnalysis` for a market, the result is routed to `blend_task` instead of directly to the executor. `blend_task` reads the current dossier (from `evidence_store`) and the current structural prior (from the `structural_priors` table) for the same `market_ticker`, calls `decision_blender.blend()`, evaluates the readiness gate, and submits the resulting `TradeCandidate` to the executor.

**Fast-lane candidates without slow-lane data:** If no dossier exists or no structural prior exists for a market, those lane inputs are passed as `None` to the blender. The blender excludes `None` lanes per DER-1. The readiness gate applies fast-lane exemptions (G2, G5, G6 not evaluated). This preserves the existing fast-lane behavior as the baseline.

**Slow-lane-only candidates are not generated.** Neither the accumulation lane nor the structural lane generates trade candidates independently. All trade candidates originate from a fast-lane signal. The slow lanes provide context only.

---

### CL-2: Structural Prior Persistence Schema

Structural priors are persisted in the `structural_priors` table in `evidence_store.db` (the same database used by the evidence store, not a new file). Schema:

```sql
CREATE TABLE IF NOT EXISTS structural_priors (
    market_ticker       TEXT PRIMARY KEY,
    prior_estimate      REAL,
    confidence          REAL NOT NULL DEFAULT 0.0,
    computed_ts         TEXT NOT NULL,
    recompute_trigger   TEXT,
    input_source_count  INTEGER NOT NULL DEFAULT 0,
    llm_called          INTEGER NOT NULL DEFAULT 0
);
```

One row per market. Upserted (`INSERT OR REPLACE`) on each recompute. `recompute_trigger` is a short string identifying why the recompute was triggered (`"dossier_update"` or `"scheduled"`). `llm_called` is 0 or 1 (SQLite boolean convention).

`evidence_store.py` must expose `get_structural_prior(market_ticker)` and `update_structural_prior(prior)` operations using the same per-market async locking contract as dossier operations.

---

### CL-3: PriorEstimate Type

`PriorEstimate` is defined in `analysis/evidence_types.py` alongside `Evidence`, `EvidenceScore`, and `Dossier`:

```python
@dataclass(frozen=True)
class PriorEstimate:
    market_ticker: str
    estimate: float           # probability, 0–1
    confidence: float         # 0–0.95
    input_source_count: int   # evidence records consumed in synthesis
    llm_called: bool
    computed_ts: str          # ISO 8601 UTC
```

`compute_structural_prior(market, context) -> PriorEstimate` is the pure function in `structural_prior.py`. The `context` parameter is a `dict[str, Any]` containing evidence records and dossier state passed by `structural_task.py`. The exact keys are defined by `structural_task.py` at call time — `structural_prior.py` reads from it without mutating it.

---

### CL-4: `default_min_edge` Source

`blend_task.py` determines `default_min_edge` at runtime:

```python
from config import cfg, PAPER_MIN_EDGE
default_min_edge = PAPER_MIN_EDGE if is_paper_mode else cfg.min_edge
```

This value is included in the blend_result dict passed to `evaluate_readiness()` as `"default_min_edge"`. It is not a per-market value; it is the same system-level threshold used by the executor's EV gate.

---

### CL-5: `recency_score` Computation in blend_task

`blend_task.py` computes `recency_score` as follows:

1. Call `evidence_store.get_recent_evidence(market_ticker, limit=100)` to obtain recent evidence records.
2. Extract `(record.original_weight, record.ingested_ts)` pairs.
3. Determine `dominant_regime`: the key with the highest value in `market.regime_weights`, defaulting to `"interpretation"` if `regime_weights` is empty.
4. Call `dossier_builder.half_life_for_regime(dominant_regime)` to get `half_life_days`.
5. Call `dossier_builder.recency_score(pairs, datetime.now(UTC), half_life_days)`.

The result is passed to `evaluate_readiness()` as `blend_result["recency_score"]`.

---

### CL-6: `evidence_source_classes` Derivation in blend_task

`blend_task.py` derives `evidence_source_classes` from the same `get_recent_evidence()` result used for recency_score:

```python
evidence_source_classes = [record.source_class for record in recent_records]
```

This is passed to `evaluate_readiness()` as `blend_result["evidence_source_classes"]`. The readiness gate calls `set(evidence_source_classes)` internally for the G2 diversity check.

---

### CL-7: Structural Recompute Trigger Condition

`structural_task.py` triggers a recompute for a given market when either:

1. No structural prior exists for this market (`get_structural_prior()` returns `None`), OR
2. The market's dossier has been updated since the last structural recompute: `dossier.updated_ts > structural_prior.computed_ts`.

If neither condition holds, the existing prior is returned as-is and no `STRUCTURAL_PRIOR_RECOMPUTE` event is emitted.

---

### CL-8: `input_sources` and `token_count` Field Semantics

**`input_sources`** in `STRUCTURAL_PRIOR_RECOMPUTE`: A `list[str]` of source identifiers consumed in the synthesis. Each entry is `"{source_class}:{source}"` from the evidence records used (e.g., `"official:kalshi_resolution_history"`, `"news:reuters"`). Derived from the evidence records passed in the `context` dict.

**`token_count`** in `STRUCTURAL_PRIOR_RECOMPUTE`: The number of LLM prompt tokens consumed during synthesis, as returned by the LLM response. Zero if `llm_called = False`.

---

### CL-9: G3 `disagreement_score` Formula (Full Expansion)

The contract states `confidence_weighted_std_dev(lane_p values)`. The full formula:

```
lanes = lanes with active estimates (lane_p is not None)
weights = [effective_confidence[i] for i in lanes]   # from DER-1, post regime-weighting
mean = sum(weights[i] * lane_p[i] for i in lanes) / sum(weights)
variance = sum(weights[i] * (lane_p[i] - mean)^2 for i in lanes) / sum(weights)
disagreement_score = sqrt(variance)
```

When only one lane has an active estimate, `disagreement_score = 0.0`. When all lanes agree exactly, `disagreement_score = 0.0`.

---

### CL-10: Phase Gate Review Resolution Summary

The Phase Gate Review (2026-04-19) issued PROCEED WITH CONDITIONS. All conditions are resolved by this section:

| Condition | Resolution |
|-----------|-----------|
| G4/failsafe interaction ambiguity | CL-1 clarification; Option A (hard block) confirmed; no code change |
| `implied_probability` persistence gap | Deferred to S4.1 + S4.2; no runtime impact confirmed |
| `prior_estimate: float` telemetry | Deferred to S4.2 observability review; no invariant violation |
| Budget manager backlog semantics | Fire-and-forget confirmed as intended; no change |
| Lane meeting point (S3.4) | Resolved: fast lane triggers blend; see CL-1 |
| Structural prior schema (S3.2) | Resolved: see CL-2 |
| `PriorEstimate` type (S3.1/S3.2) | Resolved: see CL-3 |
| `default_min_edge` source (S3.4) | Resolved: see CL-4 |
| `recency_score` pathway (S3.4) | Resolved: see CL-5 |
| `evidence_source_classes` pathway (S3.4) | Resolved: see CL-6 |
| Structural recompute trigger (S3.2) | Resolved: see CL-7 |
| `input_sources` + `token_count` semantics (S3.2) | Resolved: see CL-8 |
| `disagreement_score` full formula (S3.3) | Resolved: see CL-9 |

---

## 15. Production Configuration Capture Invariants

This section codifies the capture-discipline rules surfaced by the cycle-8 launchd plist consolidation incident (2026-05-05). When a task requires capturing production-machine configuration (installed launchd plists, live `.env` files, runtime config, etc.) into repo source-of-truth (templates, fixtures), the rules below are invariants — not guidelines.

The incident in one sentence: Codex authored 4 launchd plist templates from spec/intent rather than from the installed plists, introducing material behavioral drift (PYTHONUNBUFFERED removed, 96× cadence increase, one-shot → recurring, etc.). The drift was caught before install via diff against installed state. Root cause: "capture" was interpreted as "create templates matching expected purpose" instead of "preserve installed behavior exactly." Rewrite landed at commit `96e2995`. Background: `docs/governance/2026-05-05-launchd-plist-consolidation-decision.md` (the cycle-7 consolidation directive that the cycle-8 incident bypassed).

### Rule 1 — No source, no template

If the source artifact required for capture cannot be read (sandbox blocked, file absent, permissions denied), the task is **blocked**. Do not proceed by inferring content from:

- Sibling documentation (README, comments, design docs)
- Adjacent scripts that "should" describe the artifact
- Memory of prior conversations
- Similarity to publicly-available examples

Surface the blocker per §9 Claude rule 4. The escalation channel is asking the operator for direct read access or for the artifact's contents pasted into the conversation.

### Rule 2 — Capture is behavior-preserving; only allowlisted substitutions

The only legal transformations a capture task may apply are token substitutions from this finite allowlist:

| token | substitutes for |
|---|---|
| `@REPO_ROOT@` | absolute repo-root path (machine-local) |
| `@VENV_PYTHON@` | absolute path to `.venv/bin/python` |
| `@GOVERNANCE_LLM_MODEL@` | governance LLM model identifier |

Future allowlist additions require an IC update in the same commit that introduces them. Anything not on the allowlist must be preserved byte-faithfully — including comments, whitespace, ordering of dict keys, environment variable presence, and operational scheduling parameters.

### Rule 3 — Equivalence test required

Each capture artifact must have a sibling test that:

1. Renders the template with concrete token substitutions for the local machine.
2. Reverse-substitutes the rendered output back to canonical form.
3. Diffs against either:
   - the live source artifact (if reachable), or
   - a captured fixture stored under `tests/fixtures/<artifact-class>/`.

A non-empty diff is a test failure. The fixture, not the live artifact, is the long-term oracle — operator hand-edits to the live artifact must be re-captured into the fixture or reverted before the test re-passes.

### Rule 4 — Separate capture from improvement

A capture task lands one commit: byte-faithful capture only. Any intentional behavioral changes (e.g., changing `bothealth` cadence, renaming log paths, adding env vars) land in **separate, reviewed commits** with:

- Explicit operator approval per change
- A drift table documenting before / after / rationale
- The equivalence test refreshed in the same commit

Bundling capture with improvement is a contract violation — it conflates two semantically different operations and defeats the equivalence-test oracle.

### Rule 5 — Drift audit is a gate, not advisory

Any installer / deployer (e.g., `ops/launchd/install.sh`) that writes captured artifacts to production must refuse to proceed when the equivalence test reports drift. The legal escape hatches are:

- An explicit `--allow-drift` flag (non-default; logged)
- An interactive operator confirmation prompt in TTY mode

Silent drift-tolerant installation is forbidden.

### Rule 6 — Sandbox-blocked source reads escalate

When the agent's execution sandbox blocks reading the live source artifact, the correct response is to say so — not to fall back on inference. This is a process answer to the root cause of the cycle-8 incident; see §9 Claude rule 4 for the escalation specification.

### Scope

These rules apply to capture tasks for: launchd / systemd unit files, environment files, runtime config (e.g., `config.py` snapshots), database schema captures, and any other artifact where the production-machine state is the source of truth and the repo template is a derivative.

These rules do NOT apply to: code modules under `/analysis`, `/tasks`, `/feeds`, `/trading`, `/governance` — those follow §1 invariants + §2 architectural boundaries. The distinction is "is the production machine state authoritative?" — for production config, yes; for application code, the repo is authoritative.

### Cross-references

- §9 Codex rule 5 — capture-is-byte-faithful enforcement
- §9 Claude rule 4 — sandbox-blocked-read escalation
- Commit `96e2995` — byte-faithful rewrite landing the post-incident captures
- `docs/governance/2026-05-05-launchd-plist-consolidation-decision.md` — cycle-7 consolidation directive (the source-of-truth-policy decision that the cycle-8 incident bypassed)
- `ops/launchd/*.template` — current canonical captures (post-`96e2995`)
- `tests/test_launchd_plist_template_render.py` — current rendering test (Rule 3 oracle is queued for cycle-10 Codex item 1; current test is structural-only)

---

## 16. Replayed-EV Gate for Behavioral Deploys

**Added cycle 11.5 (2026-05-06)** in response to the strategic redirect documented in `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md`. Codifies the principle that deployment safety ≠ profit progress, and that behavioral changes deploy only when there is replayed-EV evidence to support them.

The incident in one sentence: 11 cycles of work shipped deployment safety, observability, and operator control while the bot accumulated 3 lifetime paper trades, lost all 3, and showed 89 % `edge +0.0000` on its OBS-003 SKIPPED stream — i.e., zero-edge production. Continuing to pre-stage Wave-2 / Wave-3 deploys without replay evidence was deploying hope, not edge.

### Rule 1 — Behavioral deploys require replayed-EV evidence

Behavioral changes deploy only after a replayed-EV harness shows positive expected value on the relevant feature, with operator-stated confidence threshold (default: 95 % CI on per-trade EV across at least the last 30 resolved markets in the evidence window).

**Behavioral changes covered:**
- Intake (`/feeds`): new RSS sources, classifier additions, source-class additions, per-source weight changes.
- Classifier / signal-extraction: any change that re-routes evidence into a different blender lane or alters its weight.
- Blender (`tasks/blend_task.py`): any change to the blending formula or the lane-meeting-point logic.
- Trade Readiness Gate (`tasks/trade_readiness_gate.py`): any threshold change (G1 confidence floor, G6 sample size, etc.) — including LOOSENING. Loosening absent replay evidence is COUNTERINDICATED because it converts "no edge" into "more low-quality trades."
- Sizing (`trading/executor.py`): Kelly fraction adjustments, dynamic-cap formula changes.

### Rule 2 — Safety / observability / governance fixes are exempt

Exempt categories deploy on their own (mechanical) merits without replay evidence:

- Safety fixes (kill switch logic, rollback runbook updates, env-driven revert paths).
- Observability additions (SKIPPED-emission, logging fields, attribution surfaces).
- Governance fixes (decision pipeline integrity, soak invariant audits, kill-switch guards).
- Bug fixes with mechanical hypotheses (e.g., OBS-005 cooldown sentinel — pre-fix `0.0` default for never-traded tickers indistinguishable from "cooldown just expired" is a clear mechanical issue; post-deploy production data tests the hypothesis without needing replay).
- Production config capture / launchd / install / pre-commit gates (per IC §15).

The exempt path's value is mechanical (correctness of operation), not edge-based. Deploying these without replay is fine because they don't depend on edge for value.

### Rule 3 — "May increase trades" is NOT enough; "would have produced positive replayed EV" is required

A common rationalization: "this lever may increase trade rate." Trade rate without positive EV is faster loss. Lever B G1 0.05 → 0.04 is the canonical example: it loosens admission. Without replay evidence that the additional admitted trades would have been profitable, loosening converts the existing zero-edge floor into a thinner zero-edge floor — same expected return, more variance, faster bankroll erosion.

The replayed-EV evidence requirement rejects this category of "may help" speculation.

### Rule 4 — Replay evidence must be concrete + reproducible

Replayed-EV evidence is a per-(source × market_family × signal_type) table with at least:

- trade count (n)
- win rate
- per-trade EV (with 95 % CI)
- realized P&L over the replay window
- Sharpe (or similar risk-adjusted measure)
- explicit replay-window definition (date range, market-resolution criteria)

A spec-side narrative claim ("this feed should help because X") is NOT replayed-EV evidence. The harness output (`docs/governance/edge-replay-cycle12-report.md` + sibling) IS.

### Rule 5 — Negative replayed-EV evidence is also evidence

If the replay harness finds NO feature slice with positive replayed EV at the operator-stated CI threshold, that is also a valid output. It triggers strategic-pivot conversation (calibration / sample-size / information-frontier diagnosis), not "ship anyway."

This is the same principle as IC §15 Rule 1 ("no source, no template"): no replay evidence, no behavioral deploy.

### Scope

Applies to all behavioral changes deployed to the running bot. Does NOT apply to:
- Pre-staged harnesses (xfail-strict tests for unshipped behavior land freely; they're contract pins, not deploys).
- Spec authoring (specs that describe a future deploy are fine; the deploy itself is gated).
- Replay-harness construction (Cycle-12 work) — meta-level: replay infrastructure is the prerequisite, not subject to the gate.

### Cross-references

- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — incident origin
- §11 Change Control — extended to require this evidence for behavioral changes
- §1 INV-7 (Selectivity) — strengthens its enforcement (no degradation of selectivity = no loosening without replay)
- `docs/governance/edge-replay-cycle12-report.md` (FUTURE) — Cycle-12 deliverable; replay harness output
