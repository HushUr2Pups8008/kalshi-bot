# Cycle-16 conditional charter skeletons (L10)

**Type:** pre-staged charter scaffolds. ONE per Cycle-15B verdict outcome. Operator instantiates the matching skeleton the day Cycle-15B verdict lands.
**Drafted:** 2026-05-06 cycle-14 verdict landing (filed pre-Codex C1 per locked sequencing).
**Authority:** Cycle-15B charter §"Cycle-15B success criterion" — Cycle-16 scope derives FROM the verdict, not invented to fit a preferred fix. Mirrors `cycle-15-conditional-charter-skeletons.md` pattern.

## TL;DR

Cycle-15B produces a verdict in one of 3 categories. Pre-staging skeletons for each prevents the failure mode where Cycle-16 scope is improvised + drifts toward operator's preferred path.

The verdict comes first. The skeleton that matches the verdict instantiates. NO substitution; NO blending.

## Verdict-to-skeleton map

| Cycle-15B verdict | Cycle-16 skeleton |
|---|---|
| `extraction_fixed_with_positive_ev_slice` | §A — Wave-2 candidate slice deploy + replay validation |
| `extraction_fixed_but_information_frontier_holds` | §B — Source onboarding (transferable from cycle-15-skeletons §C) OR §C — strategic-pivot per operator decision |
| `extraction_rebuild_failed` | §B-extension — second sub-fix attempt at Cycle-15B re-open OR §C — strategic-pivot per operator decision |
| `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap` (added 2026-05-07 post-Cycle-15B C10) | §D — price-reconstruction prerequisite BEFORE §B / §C routing |

## §A — Cycle-16A: Wave-2 candidate slice deploy + replay validation

**Trigger:** Cycle-15B verdict = `extraction_fixed_with_positive_ev_slice`. Lane B post-fix ≥ 6/10 AND ≥ 1 IC §16 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.

**Goal:** ship the Cycle-15B sub-fix to production + Wave-2 deploy the identified positive-EV slice with slice-specific replay validation per IC §16.

**Codex deliverables (Cycle-16A):**

1. **Wave-2 candidate slice identification.** From Cycle-15B C10 output, name the (source × market_family × signal_type) slice. Example: "Reuters × KXTRUMPCHINA × news." This becomes the Wave-2 candidate; pre-staged Wave-2 specs (legal/geopolitics speculation) are NOT the candidate.
2. **Slice-specific deploy plan.** Define the source / classifier / blender configuration that activates the slice. Confirm changes do NOT broaden beyond the named slice.
3. **Pre-deploy replay validation.** Re-run Cycle-15B C10 replay with operator-stated 95% CI threshold confirmed. Demonstrate the slice still passes IC §16 with current evidence.
4. **Post-deploy ongoing replay cadence.** Schedule Cycle-N replay re-runs at every +30 trades milestone OR every 30 days, whichever first, on the deployed slice. Replay regression triggers automatic kill-switch on the slice (operator-confirmable, not auto-deployed).
5. **Cycle-15B sub-fix to production.** The C7 sub-fix (already replay-validated) ships as a normal commit; this is now the new ground-truth extraction layer.
6. **PRE_FIX cohort archival.** Per IC §16 Rule 6 + cohort note, ensure PRE_FIX paper_trades + dossier_updates are archived (not deleted).

**Acceptance:** Wave-2 slice deployed with active replay cadence + operator-explicit live-trading-enabled flip authorization (separate commit, replay-report citation in commit message). Wave-3 / Branch-D remain HALTED until Wave-2 produces ≥ 30 live trades AND replay re-run confirms slice still positive-EV.

**Capital posture:** PAPER-ONLY UNTIL operator explicit live-trading flip + post-flip first-30-trades soak. Then live capital may engage on the named slice ONLY, subject to ongoing replay cadence.

**Estimated scope:** 1-2 weeks Codex implementation + operator-decision-time on live-trading flip authorization. Cycle-16A success closes EDGE-008 + EDGE-009; Cycle-17+ may unblock Wave-3 candidate slices.

## §B — Cycle-16B: Source-onboarding-with-replay-evidence scope

**Trigger:** Cycle-15B verdict = `extraction_fixed_but_information_frontier_holds`. Lane B post-fix passes (≥ 6/10) but Cycle-13 replay over re-ingested evidence still shows 0 IC §16 slices. Conclusion: extraction now works; bot's source mix doesn't carry decisive signal.

**Goal:** identify alternative sources that DO carry decisive signal + onboard one. Mirrors `cycle-15-conditional-charter-skeletons.md` §C with the difference that extraction is now fixed.

**Codex deliverables (Cycle-16B):**

1. **Source-discovery research:** identify 3-5 candidate source classes. Possibilities: government Twitter/social, primary-source regulatory feeds (SEC EDGAR, Federal Register, court filings), specialist insider blogs, RSS from official agency channels. Cross-link to `2026-05-06-strategic-redirect-edge-replay-priority.md` diagnosis #3.
2. **Backfill experiment:** if any candidate source has accessible historical content (e.g., Federal Register API archives), backfill the bot's `evidence_store` with synthetic ingestion against the past 16-day window. Re-extract through Cycle-15B-fixed extraction.
3. **Replay against backfill:** re-run Cycle-13 scoring with the new evidence rows included. Determine whether any slice from new sources produces positive replayed EV.
4. **If yes:** that slice becomes the Wave-2 candidate (analogous to Cycle-16A path).
5. **If no:** information frontier confirmed at trader's data access. Cycle-17 = strategic redesign or pause (Cycle-16C path).

**Acceptance:** at least one source-class slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.

**Estimated scope:** 2-4 weeks. Source onboarding includes legal review (some sources have ToS), API integration, ingestion pipeline extension.

**Important:** Cycle-16B does NOT onboard sources speculatively. Each candidate must clear backfill replay before deploy. The pre-cycle-12 "deploy hope" pattern stays prohibited.

## §C — Cycle-16C: Strategic-pivot scope (potentially pause bot)

**Trigger:** Cycle-15B verdict = `extraction_rebuild_failed` OR `extraction_fixed_but_information_frontier_holds` with operator picking redesign over §B source onboarding. Mirrors `cycle-15-conditional-charter-skeletons.md` §F.

**Goal:** halt active development; redirect resources OR fundamentally redesign the bot's information-set / model / market selection.

**Operator-decision-doc deliverables (Cycle-16C):**

1. **Honest write-up:** "The bot in current form does not have edge against Kalshi at our information set." Explicit, no hedging. References Cycle-13 + Cycle-14 + Cycle-15B verdict trail.
2. **Three-options menu for operator** (mirrors skeleton §F):
   - **(a) Pause:** stop bot, archive code/data, redirect time/resources elsewhere.
   - **(b) Fundamental redesign:** different sources (insider, regulatory, primary-source), different model (different LLM, different update math), different markets (niche / low-volume / novel-event over efficient sports/elections), different sizing.
   - **(c) Continuation as data-collection:** keep paper-mode running indefinitely; never live-trade; treat as research project.
3. **Operator picks.** No technical Cycle-16C deliverables until operator decides.

**Acceptance:** operator decision documented + filed in `docs/profit_path_debt_log.md` + ROADMAP refreshed.

**Capital posture:** PAPER-ONLY until operator picks. (a) → no further work. (b) → Cycle-16C-redesign starts; no live-trading until full Wave-1/2/3-equivalent replay validation. (c) → indefinite paper-only.

**Estimated scope:** Cycle-16C itself is operator-decision-only (~1 hour). If operator picks (b), the redesign cycle is multi-month.

## §D — Cycle-16D: Price-reconstruction prerequisite

**Trigger:** Cycle-15B C10 verdict = `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap`. Added 2026-05-07 post-Cycle-15B C10 (`e5cfb8e`). Lane B post-fix passes ≥6/10 AND IC §16 slices = 0 BUT 0/N replay rows have decision-time executable price → scorer cannot compute counterfactual P&L → IC §16 failure is scorer-blocked, not "negative EV proven."

**Goal:** restore per-decision-time price reconstruction so the IC §16 acceptance gate can be evaluated. Pure replay-harness scope; bot extraction code untouched.

**Cycle-16D Codex deliverables:**

1. **Diagnose `/markets/{ticker}/trades` 404.** Endpoint changed? Auth requirement added? Historical data window contracted? Per cycle-13 finding `fetch_historical_prices.py` probe returned 404 — root cause unidentified at that point.
2. **If endpoint solvable:** restore the original fetch path; backfill `historical_prices.json` for the 24-market replay window.
3. **If endpoint dead permanently:** identify alternative price source. Candidates:
   - Kalshi orderbook-snapshot persistence (if archived).
   - Third-party prediction-market data archive (e.g., Polymarket/Kalshi historical data resellers, subject to licensing review).
   - Computed approximation: `market_yes_price` reconstructed from settlement outcome + volume curve + time-decay assumptions. Approximation introduces error bars — must be quantified and reported alongside replay output.
4. **Backfill historical prices for 24-market replay window.** Output: refreshed `logs/edge_replay/cycle13_live/historical_prices.json` with per-decision-time `market_yes_price` populated.
5. **Re-run Cycle-15B C10 against unchanged `data/dossier_updates_post_fix.db`.** POST_FIX_REBUILT cohort intact per L8 cohort note. No re-ingestion needed.
6. **Land verdict** that either:
   - Unblocks Cycle-16 §A (positive-EV slice surfaces post-price-restoration) → Wave-2 candidate authoring proceeds.
   - Confirms `extraction_fixed_but_information_frontier_holds` with prices verified → routes to §B source onboarding or §C strategic redesign per operator decision.
   - Surfaces a third unforeseen finding (e.g., extraction emits signal but signal is noise-distributed) → operator picks between §B / §C / fresh-charter Cycle-17 scope.

**Acceptance:** Per-decision-time `market_yes_price` populated for ≥ 90% of post-fix dossier-update rows in the 24-market replay window. Cycle-15B C10 re-run produces a verdict that distinguishes "no signal" from "scorer-blocked." Verdict drives Cycle-16 §A / §B / §C routing.

**Estimated scope:** 1-2 weeks if `/markets/{ticker}/trades` is solvable (auth fix, alternate endpoint, query-parameter adjustment). 2-4 weeks if alternative price source needed (orderbook archival recovery or third-party data integration). 4+ weeks if computed approximation path is the only viable route (requires independent error-quantification design).

**Out of scope for Cycle-16D:**
- Bot extraction code. C7 keyword-map extension stays in place.
- Source onboarding. That is §B scope, blocked until §D lands.
- Live-trading flip. PAPER-ONLY remains locked.
- New Cycle-15B sub-fixes. Cycle-15B is closed.
- LLM-path audit. L7.2 deferral; revisit in Cycle-16+ post-§D if needed.

**Important:** §D does NOT bypass IC §16. It restores the harness's ability to evaluate IC §16. Post-§D Cycle-16 path still requires `ev_ci_95_lo > 0` AND `trades ≥ 10` for any Wave-2/3/D unblock.

## §B-extension — Cycle-15B-extension: Second sub-fix attempt

**Trigger:** Cycle-15B verdict = `extraction_rebuild_failed` AND operator picks "try second sub-fix" over redesign.

**Goal:** apply a second single-step sub-fix at a different extraction step, gated by Lane B post-fix verification.

**Constraints (load-bearing):**

- Counts as fix attempt #2 for the `superpowers:systematic-debugging` "3+ fixes failed → architectural conversation" trigger. After 3 failed sub-fix attempts (Cycle-15B + Cycle-15B-extension + 1 more), operator MUST escalate to Cycle-16C redesign rather than try a 4th sub-fix.
- Same charter, task split, and acceptance criteria apply (re-run Codex C1-C10 with the second-step trace + sub-fix). L1-L10 already-landed scaffolding does not re-run; new pre-execution criteria-lock verification (L2-equivalent) re-runs against the second trace.
- Pre-fix-vs-post-fix cohort accumulates: PRE_FIX_V1 (pre-Cycle-15B), POST_FIX_V1 (Cycle-15B sub-fix), POST_FIX_V2 (Cycle-15B-extension sub-fix). Replay tooling MUST distinguish all three.

**Acceptance:** same as Cycle-15B charter — Lane B post-fix ≥ 6/10 AND ≥ 1 IC §16 slice.

## What this skeleton-set does NOT include

- Substantive content for any branch. Each skeleton is a 1-page outline. Cycle-16<X> charter authors the substantive scope when verdict lands.
- Any pre-judgment on which verdict will land. All 3 are equally weighted prior to Cycle-15B C10 running.
- Auto-instantiation triggers. Operator manually picks the matching skeleton the day verdict lands.
- Wave-3 or Branch-D scope. Those wait until Cycle-16A produces a deployed Wave-2 slice with sufficient live-trade history.

## Cross-links

- `docs/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` — Cycle-15B charter (verdict source)
- `docs/governance/cycle-15-conditional-charter-skeletons.md` — Cycle-15 skeletons (origin pattern; §C/§F transferable to Cycle-16 §B/§C)
- `docs/governance/cycle-15b-post-verdict-action-checklist.md` — Cycle-15B post-verdict checklist (filed this skeleton-set)
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — strategic redirect authority
- `docs/governance/edge-replay-pivot-playbook.md` — IC §16 Rule 5 diagnostic playbook
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs all Cycle-16 deploys)
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-008` — Cycle-15B debt entry (PROFIT-EDGE-009+ filed by matching Cycle-16<X>)
