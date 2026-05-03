# PROFIT-EDGE-004 — lever menu (post-Jaccard-bisection)

**Status:** design (lever-menu, not implementation. Pre-loaded for post-soak decision-making once Codex's source-class diversification empirics return)
**Tracker:** `PROFIT-EDGE-004` in `docs/profit_path_debt_log.md`
**Owner:** Claude (sequencing) + Codex (empirics for individual levers)
**Severity:** HIGH (zero-edge / no-trade pattern is the dominant operator pain)
**Drafted:** 2026-05-03

## 1. Why this spec exists

`PROFIT-EDGE-004` is the umbrella debt entry for the matcher signal-quality / market-mix root cause behind the persistent zero-edge pattern. The original entry implicitly assumed simple matcher-threshold tuning would be the primary lever. Codex's 2026-05-03 Jaccard threshold bisection (`docs/governance/2026-05-03-matcher-jaccard-threshold-bisection.md`, commit `4f98943`) **disproves that assumption**:

- threshold 0.06 (current): 260/260 OPPORTUNITY records survive
- threshold 0.08: 134/260 survive (-48%)
- threshold 0.10: 72/260 survive (-72%)

A 50%+ OPPORTUNITY-volume cut to chase signal quality would re-introduce the no-trade pattern through the back door — the cure is worse than the disease at every threshold tested.

We need a different lever. This spec enumerates the candidate levers with cost / impact / risk shapes so the post-soak EDGE-004 implementation decision is data-driven rather than instinctive. Final lever selection happens after Codex's source-class diversification audit returns — this spec is the *menu* to choose from.

## 2. What we already know

- **MATCH-001 (B')** is the right narrow matcher lever. It fixes a *specific* asymmetry (the binary token-guard predicate) that suppresses ~600–1,300 archive records the matcher should have suppressed on volume, without harming the 5 canonical regression-anchor events. **MATCH-001 is part of EDGE-004's solution set, not all of it.** Closing EDGE-004 by waiting on MATCH-001 alone would leave 50%+ of the no-edge mass unaddressed.
- **PROFIT-OBS-003** addresses the *attribution* gap (240/260 OPPORTUNITY → silent exit), not the *production* gap (260/260 OPPORTUNITY → 0 edge upstream). OBS-003 closure makes EDGE-004 audits cleaner but does not move the trade rate.
- **PROFIT-EDGE-001 / -002 / -003** closed prior matcher / no-keywords / sport-prefix issues. Their cumulative lift was real but the ~1% conversion rate (3/260) sits at a lower bound that EDGE-004 must move.
- **G1 dominates the silent-exit kill mass.** 197/240 silent exits (82%) are `G1_blended_confidence`. The G1 floor of 0.05 in `decision_blender` is a calibration question explicitly *deferred* by the OBS-003 spec until post-fix data is available.
- **2 OPP events at +0.06 / +0.064 positive edge produced no PAPER_TRADE.** Per the post-cutover audit, these are silent exits (OBS-003 territory), not no-edge events. They confirm that *some* OPPORTUNITY signal does have positive edge; the matcher pipeline isn't pure noise.

## 3. The lever menu

Each lever lists: target mechanism, expected impact direction, blast radius, sizing cost (how cheaply we can size impact pre-deploy), risk shape, dependencies.

### Lever A — Source-class diversification audit (Codex, in flight 2026-05-03)

- **Mechanism:** identify whether OPPORTUNITY events at zero edge are concentrated in particular `source_class` values (news / court / etc.). If so, the fix is source-class weighting in `evidence_scorer`, not the matcher.
- **Expected impact:** depends on empirics. If a single source-class accounts for >60% of zero-edge events, weighting that class down lifts conversion materially.
- **Blast radius:** `analysis/evidence_scorer.py` weight tuning (config knob; no schema change).
- **Sizing cost:** LOW — Codex's audit is producing the answer this week. No deploy needed for sizing.
- **Risk shape:** LOW. Source-class weights are a calibration knob; rollback is a config diff.
- **Dependencies:** none. Independent of MATCH-001 / OBS-003 / EXEC-002.
- **Verdict:** **first lever to evaluate** because the empirics are arriving in days, not weeks, and the fix surface is small.

### Lever B — G1 calibration tightening (post-OBS-003)

- **Mechanism:** the G1 confidence floor of 0.05 in `analysis/decision_blender.py` blocks 197/240 silent exits. Lowering it to 0.04 / 0.03 admits a fraction of those candidates to the executor.
- **Expected impact:** sizable on trade-rate (197 candidates currently stuck behind the gate); unknown on EV (the gate exists for a reason — admitting low-confidence candidates may surface losses).
- **Blast radius:** one constant in `decision_blender`; widely-dependent file.
- **Sizing cost:** MED — requires post-OBS-003 SKIPPED stream to attribute counter-factual edges to admitted candidates. Cannot size meaningfully until OBS-003 lands.
- **Risk shape:** HIGH. This is a calibration change to a load-bearing gate. Wrong direction = trade-rate explosion + Kelly-fidelity loss. Right direction = right-sized signal admission. Empirically uncertain at draft time.
- **Dependencies:** **OBS-003 must land first** (per OBS-003 spec §11 explicit deferral). MATCH-001 (B') landing first is also recommended since the post-MATCH-001 OPPORTUNITY mix is materially different from the current mix.
- **Verdict:** evaluate after MATCH-001 + OBS-003 land and produce a clean 14-day post-fix attribution dataset. Earliest evaluation: 2026-05-23. Earliest implementation: 2026-06-06 (post-evaluation soak).

### Lever C — Cross-series headline correlation (EXEC-002 Approach 2)

- **Mechanism:** EXEC-002 (Approach 1, landing post-soak) handles same-series-prefix burst suppression. Approach 2 extends to cross-series correlation: one Trump headline firing on `KXTRUMPIRAN`, `KXMOCTRUMP25`, `KXPARDONSTRUMP` simultaneously is over-sized risk relative to underlying conviction.
- **Expected impact:** moderate; reduces correlated trade volume on hot news (similar shape to EXEC-002 Approach 1 but at a wider scope).
- **Blast radius:** BlendTask + a new headline-hash dedupe layer. Larger refactor than EXEC-002 Approach 1.
- **Sizing cost:** MED — empirically size cross-series-single-headline overlap in the 13-day archive (Codex strength). If overlap rate is < 5% of OPPORTUNITY events the lever isn't worth landing.
- **Risk shape:** MED. New decision-path gate; same risk shape as EXEC-002 Approach 1 but wider blast radius.
- **Dependencies:** EXEC-002 Approach 1 must land first (foundation). Codex archive audit must show overlap rate ≥ ~5%.
- **Verdict:** defer until post-EXEC-002 audit confirms overlap rate. Filed in EXEC-002 spec §11 as future-followup; this lever menu surfaces it as a candidate, not a decision.

### Lever D — Pre-LLM gate re-enablement

- **Mechanism:** the pre-LLM gate (`config.PRE_LLM_MIN_MATCH_SCORE` and `config.ENABLE_PRE_LLM_GATE`) was double-disabled per the production audit cited in `docs/ROADMAP.md` Stage 5 Context. Re-enabling it filters candidates *before* the LLM call, lifting average match quality going into the LLM and saving budget.
- **Expected impact:** moderate on signal quality (drops minimal-overlap matches before LLM sees them); small on edge directly (LLM is the rate-limiter on edge production, not the gate).
- **Blast radius:** two config flags; no code change.
- **Sizing cost:** LOW — re-run match diagnostics on the 13-day archive with the gate enabled at various score floors. Codex's existing match_score_audit harness can do this without code change.
- **Risk shape:** LOW-MED. The gate exists; re-enabling is restoring intended behavior. Risk: the floor at which the gate was disabled (likely the 0.06 Jaccard threshold or similar) may drop too many candidates, recreating the symptom Codex's bisection just disproved.
- **Dependencies:** sized post-MATCH-001 (B') because B' changes the upstream OPPORTUNITY mix; gate effects compose.
- **Verdict:** sized after MATCH-001 lands. Cheap to evaluate; same risk pattern as the Jaccard sweep — easily over-tuned.

### Lever E — Source-quality weighting (multi-source corroboration requirement)

- **Mechanism:** require N≥2 distinct sources to corroborate before a candidate clears the readiness gate. Today single-source candidates can produce trades; this changes the gate to require corroboration.
- **Expected impact:** large on trade *rate* (down) and *quality* (up). Cuts trade rate further before lifting edge.
- **Blast radius:** new gate in `trade_readiness_gate` or `decision_blender`. Decision-path edit.
- **Sizing cost:** MED — count single-source vs multi-source OPPORTUNITY events in the 13-day archive; size the cut.
- **Risk shape:** HIGH. Combined with MATCH-001's expected suppression lift, this lever could halve trade rate again. Premature absent compelling evidence.
- **Dependencies:** post-MATCH-001 + post-OBS-003 attribution data.
- **Verdict:** defer. Land only if Levers A + B + D fail to move trade rate above 5/260 baseline.

### Lever F — Market-mix specificity (P4-GATE territory)

- **Mechanism:** the 13-day archive's broad-scope geopolitical market mix is the underlying reason the LLM correctly anchors to 0.5. Narrower-scope markets (Appendix A integration per ROADMAP P4) shift the input mix toward markets where the LLM is willing to take a directional view.
- **Expected impact:** large but uncertain. The whole P4 thesis is that Appendix A integration unlocks edge production.
- **Blast radius:** intake / matcher / market-cache layers. Largest refactor of any lever.
- **Sizing cost:** HIGH — requires a parallel market-cache feed and side-by-side comparison.
- **Risk shape:** HIGH. Strategic-scope work; can't be evaluated without a separate dev stream.
- **Dependencies:** P4-GATE explicit. Outside the EDGE-004 closure scope.
- **Verdict:** **out of scope for EDGE-004.** ROADMAP-tracked, separate decision. Surfacing here only to clarify it's *not* the EDGE-004 lever.

## 4. Decision criteria

EDGE-004 closes OPEN → COMPLETE when:

- a measurable lift in the OPPORTUNITY → PAPER_TRADE conversion rate (currently 3/260 ≈ 1.2%) is observed *and attributed* to a specific lever from §3
- the lift is sustained over a 14-day post-deploy window
- per-lane attribution (via the post-OBS-003 SKIPPED stream + EVIDENCE_INGESTION counts) confirms the lever is doing the work the spec claimed

Closure does **not** require all levers to land. EDGE-004 closes when *one* lever produces the lift; remaining levers either re-open as their own debt entries or get filed as future follow-ups.

## 5. Sequencing recommendation (subject to Codex empirics)

1. **Wait for Codex's source-class diversification audit (Lever A).** Earliest verdict: this week.
2. **If Lever A finds a concentrated source-class:** land a source-class weight tweak as the EDGE-004 primary fix in Wave 2 of the post-soak landing order (after the 4-item stack: OBS-005 / MATCH-001 / OBS-003 / EXEC-002). Closes EDGE-004.
3. **If Lever A finds no concentration:** evaluate Lever D (pre-LLM gate) using Codex's match_score_audit harness. Land if the size-vs-trade-rate tradeoff is favorable.
4. **If neither A nor D moves the rate:** evaluate Lever B (G1 calibration) post-OBS-003 landing, with the 14-day post-fix attribution dataset.
5. **If A + D + B all stall:** Lever E (multi-source corroboration) becomes the next candidate. Lever C (cross-series headline correlation) becomes a parallel investigation. Lever F (P4-GATE) is a strategic decision outside EDGE-004 scope.

This is a probabilistic sequence. Each step's verdict modifies the prior on the next.

## 6. Risk

- **Lever soak after lever soak.** EDGE-004 may need 2–3 lever attempts before closure, each requiring its own attribution window. Total wall-clock from 2026-05-09 to closure could be 30–60 days. That's the *honest* timeline; collapsing it requires accepting under-attributed lifts.
- **Lever interaction.** B + E together may produce a non-linear interaction (admitting more candidates *and* requiring corroboration could either cancel out or compound). Land levers serially with attribution windows between them.
- **EDGE-004 closure ambiguity.** "Measurable lift" requires a definition. Recommend: 7+ PAPER_TRADE events in a 14-day window (roughly 2.3× the 13-day baseline of 3 trades). Calibrate against Codex's source-class diversification audit results before locking the threshold.

## 7. Soak-window contract

This spec is documentation only and lands during the active `PROFIT-PHASE2-001` soak. No code changes. The lever menu becomes operationally active alongside the post-soak landing-order spec (`2026-05-03-post-soak-landing-order-design.md`); EDGE-004 work begins after the 4-item stack lands and stabilizes (~2026-05-22 earliest).

## 8. Out of scope

- **Specific lever-implementation specs.** Once a lever is selected from this menu, that lever gets its own pre-load spec. This document is the menu; the chosen lever gets a separate design doc.
- **`PROFIT-LLM-001` signal-analyzer LLM unification.** Gated behind GOV.P4. Independent of EDGE-004's closure path.
- **Calibration framework changes.** The G1 floor in §3-B is a single-constant tweak. Larger calibration framework redesign (e.g., Bayesian posterior tracking per lane) is out of scope.
- **Restart-driven re-attribution.** All sizing data should come from the 13-day MacBook archive + Mac Studio post-cutover Phase 2 soak window. Replays are cheap; re-running the bot under different configurations is expensive and error-prone.
