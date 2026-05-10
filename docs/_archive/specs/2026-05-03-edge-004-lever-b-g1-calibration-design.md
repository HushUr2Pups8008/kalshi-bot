# PROFIT-EDGE-004 Lever B — G1 calibration tightening

> **🛑 BLOCKED PER IC §16 (cycle-11.5 strategic redirect, 2026-05-06) — COUNTERINDICATED.** Loosening G1 (0.05 → 0.04) absent replay evidence converts the existing 89 %-zero-edge floor into MORE low-quality trades. Per IC §16 Rule 3: "may increase trade rate" is NOT enough; "would have produced positive replayed EV" is required. Wave-3 deploy of this lever is HALTED pending Cycle-12 replay harness output that explicitly shows the additional admitted trades have positive EV. See `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` and IC §16.

**Status:** BLOCKED PER IC §16 (was: design; Wave 2 of post-soak landing — earliest deploy ≥ 2026-06-06; gated on Lever A's verdict + ≥ 14 d post-OBS-003 attribution dataset)
**Tracker:** `PROFIT-EDGE-004` (Lever B entry from `2026-05-03-edge-004-lever-menu-design.md`)
**Owner:** Claude (design) + Codex (sizing — see §4)
**Severity:** HIGH (parent EDGE-004 closure path; secondary lever in the revised A → B → E → C → D sequence)
**Drafted:** 2026-05-03
**Empirical basis:** `docs/governance/2026-05-03-obs003-kill-attribution.md` (Codex 2026-05-03) — 197/240 silent exits attributed to `G1_blended_confidence` (82.1 %).

## 1. Why this lever (revised post-Codex 2026-05-03 G1 counterfactual)

The EDGE-004 lever menu's revised sequencing puts B as the secondary lever after A. The data-anchor for the *kill mass*: per Codex's per-gate kill attribution, **G1 dominates 197/240 silent exits (82.1 %)**. The G1 floor of 0.05 in `analysis/decision_blender.py` (`G1_CONFIDENCE_THRESHOLD`) is a hand-set constant from the original BSR landing; whether 0.05 is the right floor is empirically unknown.

**Codex's 2026-05-03 G1 admittance counterfactual** (`docs/governance/2026-05-03-g1-admittance-counterfactual.md`, commit `e630e28`) sizes the trade-rate impact of relaxing the floor against archived `BLEND_DECISION` records:

| floor | admitted | admission rate | edge ≥ 0.02 | edge ≥ 0.05 | mean predicted edge | p50 | p90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.04 | 32/197 | 16.2 % | 1 | 1 | 0.0020 | 0.0 | 0.0 |
| 0.03 | 65/197 | 33.0 % | 2 | 1 | 0.0014 | 0.0 | 0.0 |

**Surprise finding:** loosening G1 admits a meaningful number of candidates (32 at 0.04, 65 at 0.03), but their mean predicted edge is 0.001-0.002 and only 1-2 of the 32-65 admitted candidates clear the `paper_min_edge=0.02` floor. **Lever B is not a trade-rate lever in expectation.** Most G1-killed candidates were correctly killed — they have effectively zero edge.

**Revised Lever B value proposition:** B is an **attribution / calibration lever**, not an edge-production lever. Its value is:

1. Making the previously-killed candidates visible in the post-OBS-003 SKIPPED stream + (when admitted) potentially the paper-trade stream — exposing whether the *G1 floor itself* is calibrated correctly against actual realized outcomes.
2. Producing post-deploy data to evaluate whether G1 should move *up* (the floor is too lenient) rather than down (the floor is too tight). Codex's data argues the floor is approximately correct as an EV gate; the question is whether the constant should be removed in favor of a calibration-driven dynamic floor.
3. Serving as a *final* check that EDGE-004 isn't closable through gate-loosening, before resorting to Lever E (multi-source corroboration, HIGH-risk).

If MATCH-001 (B') + Lever A together lift conversion ≥ 5 %, EDGE-004 closes and **Lever B doesn't land.** If they fall short, Lever B lands as an attribution-data-gathering exercise with the explicit prior that paper-trade lift ≤ 1 trade per 14 days. Honest sizing matters here — operator should not expect Lever B to "produce edge."

## 2. The fix

`analysis/decision_blender.py`:

```python
# Before (current)
G1_CONFIDENCE_THRESHOLD = 0.05
G1_FAILSAFE_CONFIDENCE_THRESHOLD = 0.10

# After (Lever B candidate floors)
G1_CONFIDENCE_THRESHOLD = <CHOSEN_FLOOR>     # candidates: 0.04, 0.03, 0.025
G1_FAILSAFE_CONFIDENCE_THRESHOLD = <CHOSEN_FAILSAFE>  # scale proportionally
```

Two single-line constant edits. The failsafe floor (which fires under low regime confidence per `tasks/trade_readiness_gate.py:159`) scales with the primary floor — recommend keeping the 2× ratio (failsafe = primary × 2) unless the sizing data argues otherwise.

**No behavior-shape change.** The G1 gate continues to fire on the same condition (`blended_confidence < threshold`); only the threshold value changes.

## 3. Floor candidates and decision space

| floor | failsafe | rationale | expected admit-rate increase | risk |
|---|---|---|---|---|
| 0.05 (current) | 0.10 | baseline | — | — |
| 0.04 | 0.08 | conservative loosening (-20 %) | small lift; targeted; low blowout risk | LOW |
| 0.03 | 0.06 | moderate loosening (-40 %) | moderate lift; admits a long tail | MED |
| 0.025 | 0.05 | aggressive loosening (-50 %) | larger lift but the tail dominates | HIGH |
| 0.00 | 0.00 | gate disabled | trade-rate explosion | DO NOT LAND |

Recommendation: **start at 0.04**. Half-step landings limit blast radius; if the post-deploy data shows a clean lift, follow up with a separate spec for 0.03. Skipping straight to 0.03 risks losing the attribution on which step did the work.

## 4. Sizing methodology

**Codex's 2026-05-03 G1 admittance counterfactual** (`docs/governance/2026-05-03-g1-admittance-counterfactual.md` + `scripts/simulations/g1_admittance_counterfactual.py`, commit `e630e28`) already sizes the floor candidates against the 13-day archive. Headline numbers in §1 above. **No further pre-deploy sizing needed.**

The audit's stated limitation: archive `BLEND_DECISION` records store only the *first* readiness failure. Candidates admitted by a lower G1 floor may still fail G2/G3/G4/G5/G6 once the full gate re-evaluates. The 32/65 admittance numbers are upper bounds; actual paper-trade lift will be smaller.

**Post-deploy attribution methodology** (this is the value Lever B actually delivers):

1. **OBS-003 SKIPPED stream attribution:** post-deploy, candidates that previously hit G1 at the old floor now either (a) admit and produce PAPER_TRADE/PAPER_RESOLUTION events, or (b) fail at a downstream gate (G2/G3/G4/G5/G6) and emit SKIPPED with the post-G1 reason.
2. **Per-candidate edge realization:** for the 1-2 candidates per 14 days that clear `paper_min_edge=0.02`, track realized P&L. If cumulative is positive, the floor was too tight; if negative, the floor was at or above the right level.
3. **Calibration framework hook:** the existing `PROFIT-CAL-001` `CALIBRATION_CHECK` consumer (`analysis/calibration_task.py`) already tracks per-lane calibration drift. Lever B's marginal candidates feed into this stream automatically — the operator can read the calibration deltas from existing dashboards.

Sizing-cost: ZERO additional pre-deploy work. Codex's harness covered it.

## 5. Components touched

Single file: `analysis/decision_blender.py`. Two single-line constant edits.

Plus tests: `tests/test_decision_blender.py` — pin both new threshold constants and refresh any cases that hardcoded 0.05 / 0.10 expectations.

**No changes to** `tasks/`, `trading/`, `feeds/`, or `config.py`. The gate continues to fire on the same condition; only the constants change.

**Soak invariant:** Lever B is a decision-path edit on the load-bearing readiness gate. Cannot land mid-soak under any circumstances. Cannot land in Wave 1 of post-soak (must follow ≥ 14 d post-OBS-003 dataset). Wave 2 candidate.

## 6. Acceptance criteria

- `G1_CONFIDENCE_THRESHOLD` and `G1_FAILSAFE_CONFIDENCE_THRESHOLD` updated per the chosen floor (recommend 0.04 / 0.08).
- All `tests/test_decision_blender.py` cases that hardcoded the 0.05 floor refreshed to the new value or parametrized.
- 14 d post-deploy attribution: G1 SKIPPED count drops by ≥ 30 % at 0.04 floor (admitted candidates are no longer in the SKIPPED stream); admitted candidates either produce PAPER_TRADE events or fail at downstream gates with attribution visible.
- OPPORTUNITY → PAPER_TRADE conversion rate **may not lift materially** per Codex's counterfactual sizing (predicted lift: 1-2 trades per 14 days, well within stochastic noise). Acceptance criterion is therefore *attribution clarity*, not raw conversion lift: the post-OBS-003 SKIPPED stream now distinguishes "killed at G1" vs "killed at G2-G6 after passing G1."
- Newly-admitted candidates that *do* produce PAPER_TRADE events have non-negative aggregate realized P&L over the 14 d window. Negative aggregate is a rollback trigger.
- No regression in existing test fixtures.

## 7. Rollback

Two-line constant revert. Trivial. Operator-side fast revert: no env-var; revert + redeploy is the only path. (Could add an env-var override if operator wants kill-switch capability — recommend deferring unless specifically requested.)

**Trigger to revert:** post-deploy 7 d realized P&L on newly-admitted candidates is *negative* — the floor exists for a reason and we found it. Different from "trade rate stays flat" (which is a non-result, not a regression).

## 8. Dependencies

Hard:
- **OBS-003 must have landed and produced ≥ 14 d of SKIPPED-stream attribution.** Without OBS-003 the G1 silent-exit count is unobservable from the trade-log alone.
- **MATCH-001 (B') must have landed.** B' changes the upstream OPPORTUNITY mix; sizing G1 against pre-B' data is sizing the wrong distribution.

Soft:
- **Lever A first-feed should have landed and stabilised.** A's source-class diversification interacts with G1 — broader source mixes lift G2 (passes a different gate) and may indirectly lift blended confidence past the existing 0.05 floor for some candidates, reducing Lever B's marginal value. If Lever A's first feed lifts conversion ≥ 5 %, **EDGE-004 closes against Lever A and Lever B never lands.**

## 9. Risks

- **Calibration sensitivity.** The G1 floor is the load-bearing gate that controls trade rate. Wrong direction (too aggressive a loosening) produces a trade-rate explosion that could chew through paper bankroll fast. Mitigation: the recommended 0.04 step is half the loosening of 0.03; rollback trigger is realized P&L not trade rate.
- **Over-admittance feeds Kelly fidelity loss.** Kelly-sized bets compound: one bad admission compounds into more bad admissions if the calibration framework lags. The post-deploy 7 d window must include calibration drift detection (PROFIT-CAL-001's existing `CALIBRATION_CHECK` event stream covers this).
- **G1 dominance may be artifact, not signal.** 197/240 silent exits at G1 might be downstream of G6 / G2 / G3 also failing — i.e., the candidates are weak across the board and G1 is just the first gate they fail. If true, lowering G1 admits weak candidates that G2 / G3 will then re-block. Sizing methodology §4 step 2 catches this: if newly-admitted candidates predominantly fail G2 or G3 in the counterfactual, Lever B isn't the right lever.
- **Failsafe-mode admittance.** The failsafe threshold (10 % currently) fires under `regime_confidence < 0.20`. Halving the failsafe to 0.05 in the aggressive scenario is functionally close to disabling the gate during regime-uncertainty periods. Avoid 0.025 / 0.05 floor pair without explicit operator sign-off.

## 10. Soak-window contract

This spec is documentation only and lands during the active `PROFIT-PHASE2-001` soak. No code changes. Lever B becomes operationally active only after OBS-003 + Lever A's first attempt complete; earliest deploy ≥ 2026-06-06.

## 11. xfail harness pre-load decision

Per the project's pre-load convention (cf. OBS-003 / OBS-005 / MATCH-001 (B') / EXEC-002 harnesses), Lever B *would* warrant an xfail-strict harness pinning the new constants. **Defer the harness pre-load** because:

1. The constants depend on Lever A's outcome — if A closes EDGE-004, Lever B never lands and the harness is dead code.
2. The exact floor value is sized post-OBS-003 (per §4) and may be 0.04, 0.03, or 0.025; pre-loading the harness against the wrong value creates a false-positive xpass at landing time.

If after Lever A's first-feed verdict (~2026-05-29) Lever B becomes the chosen path, *then* draft the harness pre-load with the sized floor value. Same pattern as the other Wave-2 specs: design first, harness once empirics narrow.

## 12. Out of scope

- **Other readiness-gate floors (G2 / G3 / G4 / G5 / G6).** G1 dominates the kill mass; the others are tail. If G1 calibration lands cleanly and EDGE-004 closes, the others stay at their current values.
- **Bayesian posterior tracking per lane.** Larger calibration framework redesign — separate spec, not this one.
- **`PROFIT-CAL-001` calibration drift detection.** Already wired; Lever B benefits from the existing `CALIBRATION_CHECK` stream rather than extending it.
- **Dynamic / regime-conditional G1 floors.** A single static constant is enough; per-regime floors are over-engineering until single-floor data argues otherwise.
- **`PROFIT-LLM-001` signal-analyzer LLM unification.** Out of EDGE-004 scope entirely.
