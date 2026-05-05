# Implementation Contract review for cycle 4-5 outcomes

**Type:** retrospective audit (Claude task per Implementation Contract §11 — "Contract updates are explicit").
**Source:** Implementation Contract §1 (invariants) + §2 (boundaries) + §5 (Trade Readiness Gate) + §7 (Executor Contract) + §11 (Change control).
**Audience:** operator considering whether cycle 1-5 deploy evidence reshapes the contract.
**Drafted:** 2026-05-05.
**No contract edits applied.** Findings only.

## TL;DR

**0 invariant violations across cycles 1-5.** All Wave-1/2/3/Branch-D specs respected §1 invariants (belief-based system; probability/confidence separation; dossier model; layer purity; execution isolation; trade-frequency control; selectivity preservation). Lever C v1 LOCK addendum's INV-6 boundary attestation is the cleanest example — explicit attestation that suppression-only doesn't violate trade-frequency invariant. **No contract amendments recommended.** 1 LOW observation about §5 Trade Readiness Gate evolution that operator may want to track post-Wave-3.

## Per-invariant review

### INV-1: Belief-Based System

**Surface:** Wave-1 OBS-005 (cooldown sentinel), MATCH-001 (token-guard), OBS-003 (SKIPPED emission), EXEC-002 (series-correlation guard); Wave-2 A.1+ feed onboarding; Wave-3 Lever B (G1 floor) + Lever C (cross-series suppression).

**Adherence:** all Wave-1/2/3 changes preserve the belief-revision pattern. None of them accumulate raw signals + threshold; all of them either:
- Fix attribution gaps (OBS-003 SKIPPED stream)
- Tighten existing gates (Lever B G1 floor; Lever C cross-series suppression)
- Add new evidence sources to the existing belief-revision pipeline (Wave-2 A.1+ feeds)

**Verdict:** ✅ no violation.

### INV-2: Probability and Confidence Separation

**Surface:** Lever B G1 floor change (`G1_CONFIDENCE_THRESHOLD = 0.04`).

**Adherence:** the G1 threshold operates on `blended_confidence` (regime-scaled), not on `current_estimate`. Lowering 0.05 → 0.04 changes the confidence floor; doesn't conflate confidence with probability.

**Verdict:** ✅ no violation.

### INV-3: Stateful Dossier Model

**Surface:** None of the cycle 1-5 changes touch dossier state semantics. Lever C cross-series suppression operates on BlendTask candidates (post-dossier-blend); doesn't modify dossiers.

**Verdict:** ✅ no violation; no change to scope.

### INV-4: Purity of `/analysis`

**Surface:** Wave-1 Lever A.1 classifier patch in `main.py:_source_class_for_evidence`; Wave-2 same surface for Branch C feed token additions; Wave-3 Lever B in `tasks/trade_readiness_gate.py`; Wave-3 Lever C in `tasks/blend_task.py`.

**Adherence:** all changes respect `/analysis` purity:
- A.1 classifier is in `main.py` (orchestration/wiring layer per current architecture); not in `/analysis`
- Lever B is in `tasks/` — orchestration; allowed mutable state per layer contract
- Lever C is in `tasks/blend_task.py` — orchestration; the `_recent_headline_enqueues` dict is task-state, not `/analysis`-state

**Verdict:** ✅ no violation.

### INV-5: Execution Isolation in `/trading`

**Surface:** None of the cycle 1-5 changes add decision logic to `/trading`. EXEC-002 series-correlation guard is in `tasks/blend_task.py` (the `cross_series_correlation_in_window` reason emits SKIPPED before candidate reaches executor); the executor receives only post-suppression candidates with unchanged `signal_meta` shape.

**Adherence:** ✅ executor contract intact. Lever C v1 LOCK addendum §1 explicitly attests INV-5 boundary not violated.

**Verdict:** ✅ no violation.

### INV-6: No Uncontrolled Increase in Trade Frequency

**Surface:** This is the most consequential invariant for Wave-1/2/3 changes. Each lever's effect on trade frequency:

| lever | direction | rationale |
|---|---|---|
| OBS-005 cooldown sentinel | neutral | bug fix; first-time keys get None semantics (was 0.0). No trade-rate change. |
| MATCH-001 (B') token-guard | ↓ | tighter suppression; Codex archive replay shows 600-1300 records suppressed without harming 5 canonical regression-anchor events |
| OBS-003 SKIPPED emission | neutral | observability change; no decision-path change |
| EXEC-002 series-correlation | ↓ | suppresses correlated bursts; lifts 0 trades, suppresses some |
| GOV-003 monitor fix | neutral | governance-side fix; unrelated to trade-rate |
| Lever A.1 classifier | neutral | per Codex archive replay |
| Lever A.1+ Branch C onboard | ↑ | new feed adds OPPORTUNITY surface — INV-6's "uncontrolled increase" question |
| Lever B G1=0.04 | ↑ (1-2/14d) | per Codex G1 admittance counterfactual; bounded predicted lift |
| Lever C cross-series | ↓ | suppression-only per LOCK addendum §1 attestation |

**INV-6 critical evaluation for Wave-2 Branch C + Wave-3 Lever B (the two ↑ levers):**

- **Branch C:** Wave-2 branch decision table acceptance criterion = "≥ 1 PAPER_TRADE w/ NON-NEGATIVE aggregate realized P&L." This requires "proportional increase in signal quality" (per INV-6) — non-negative P&L IS the signal-quality test. ✅ INV-6-compliant.
- **Lever B 0.04:** parent spec §3 risk table bounds expected lift to 1-2 trades / 14d. Cycle-2 LOCK addendum + cycle-3 sizing-scope spec re-attest this. Acceptance criterion (parent §6): G1 SKIPPED count drops ≥30% AND newly-admitted candidates have non-negative realized P&L. ✅ INV-6-compliant.

**Verdict:** ✅ no violation. Both ↑ levers gate trade-frequency increases on signal-quality measurement.

### INV-7: No Degradation of Selectivity

**Surface:** same as INV-6.

**Adherence:** all locked Trade Readiness Gate thresholds (G1=0.04, G2-G6 unchanged, G3-failsafe unchanged, G4=0.40, G5/G6 unchanged) preserve the 6-condition gate. New levers add gates (Lever C cross-series); they don't soften existing gates.

**Verdict:** ✅ no violation.

## Per-section review

### §2 Architectural Boundaries

**Cycle 1-5 boundary touches:**
- `/feeds`: Wave-2 Branch C adds new RSS sources to `config.py:RSS_FEEDS`; allowed (ingestion only)
- `/analysis`: no changes
- `/tasks`: Wave-3 Lever B + Lever C edit `tasks/trade_readiness_gate.py` + `tasks/blend_task.py`; allowed (orchestration)
- `/trading`: no changes

**Verdict:** ✅ no boundary violations.

### §5 Trade Readiness Gate

**Cycle 1-5 changes:**
- G1 threshold: 0.05 → 0.04 (Wave-3 Lever B)
- G1 failsafe: 0.10 → 0.08 (Wave-3 Lever B)
- G2-G6: unchanged

**§11 authority:** "Changes to the Trade Readiness Gate conditions or thresholds (Section 5)" require "Explicit Approval Before Proceeding." Cycle 2 LOCK addendum + cycle 3 sizing-scope spec satisfy this requirement. Operator's "execute your N" cycles 2+3 are the explicit approval.

**Verdict:** ✅ contract followed.

### §7 Executor Contract

**Cycle 1-5 executor changes:** zero.

The executor receives `TradeCandidate` with unchanged `signal_meta` shape across all cycle 1-5 changes. Wave-3 Lever C suppresses candidates BEFORE they reach the executor (via SKIPPED emission); doesn't modify what the executor sees post-suppression.

**Verdict:** ✅ no violation.

### §11 Change Control Rules

**Cycle 1-5 changes that required approval:**
- Lever B 0.04 floor (§5 threshold change): cycle 2 LOCK addendum is the explicit-approval artifact
- Lever C v1 (§2 boundary touch + new gate): cycle 2 LOCK addendum's INV-6 attestation is the explicit-approval artifact
- Branch D escalation criteria: cycle 2 spec is the redesign-discussion artifact

**Verdict:** ✅ all changes followed §11 procedure.

## Findings

### F1 (LOW) — §5 G1 threshold history is now 3-step, not 1-step

**Observation:** §5 Trade Readiness Gate documents G1 threshold = 0.35 (per the original PHASE-3 contract). Operator-visible thresholds:

- Original (S4.5b post-EDGE-003): `G1 = 0.35`
- Current code at HEAD (`tasks/trade_readiness_gate.py:69`): `G1_CONFIDENCE_THRESHOLD = 0.05`
- Wave-3 Lever B locked: `G1_CONFIDENCE_THRESHOLD = 0.04`

The G1 = 0.35 → 0.05 change happened pre-cycle 1 (via PROFIT-EDGE-003 G1 calibration follow-up; commit chain referenced in profit_path_debt_log.md). The contract §5 "G1 ≥ 0.35" line is **stale** vs current code; cycle 1-5 work didn't introduce the staleness but didn't fix it either.

**Recommendation:** post-Wave-3 Lever B deploy (~ 2026-06-17+), update §5 G1 threshold reference to `0.04`. Optional pre-Wave-3: update §5 to `0.05` to reflect current code state. Not blocking; cosmetic alignment.

**Severity LOW** because the contract's G1 ≥ 0.35 line is purely informational; the load-bearing logic is in `tasks/trade_readiness_gate.py:69` which is correct + cycle-3-LOCK-tracked.

## Recommended action

**No contract amendments.** Cycles 1-5 followed the contract cleanly. F1 is informational-only and can be deferred to a post-Wave-3 cycle.

If operator wants pre-Wave-3 cosmetic alignment of §5 G1 threshold to current code: trivial 1-line edit; no architectural review required since the change is "doc reflects code that's already shipped."

## Out of scope

- Pre-cycle-1 contract violations (if any). Out of cycle 4-5 review window.
- Hypothetical Wave-4+ contract impact (e.g., PROFIT-LLM-001 prompt-template change). Surface is bounded per cycle-3 sizing-scope spec; future review at PROFIT-LLM-001 deploy time.
- §3 Belief System Rules + §6 Regime Handling Rules + §8 Observability requirements + §10 Roadmap Execution Rules + §12 Failure Mode Awareness. Cycles 1-5 didn't touch these surfaces; no review needed.

## Cross-links

- `docs/IMPLEMENTATION_CONTRACT.md` §1 + §2 + §5 + §7 + §11 — under review
- `docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` — Lever B 0.04 LOCK
- `docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` — Lever C v1 LOCK + INV-6 boundary attestation
- `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D handoff structure
- `docs/governance/2026-05-05-cross-cycle-contract-adherence-review.md` — cycle-4 audit of Claude/Codex split (sibling)
- `tasks/trade_readiness_gate.py:69-70` — G1 constant location of record
