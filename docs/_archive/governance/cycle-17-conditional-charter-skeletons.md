# Cycle-17 conditional charter skeletons (M9)

**Type:** pre-staged charter scaffolds. ONE per Cycle-16D verdict outcome. Operator instantiates the matching skeleton the day Cycle-16D verdict lands.
**Drafted:** 2026-05-07 cycle-15B verdict landing (filed pre-D1 per locked sequencing).
**Authority:** Cycle-16D charter §"Cycle-16D success criterion" — Cycle-17 scope derives FROM the verdict, not invented to fit a preferred fix. Mirrors `cycle-16-conditional-charter-skeletons.md` pattern.

## TL;DR

Cycle-16D produces a verdict in one of 4 categories. Pre-staging skeletons for each prevents the failure mode where Cycle-17 scope is improvised + drifts toward operator's preferred path.

The verdict comes first. The skeleton that matches the verdict instantiates. NO substitution; NO blending.

## Verdict-to-skeleton map

| Cycle-16D verdict | Cycle-17 skeleton |
|---|---|
| `extraction_fixed_with_positive_ev_slice` | §A — Wave-2 candidate slice deploy + replay validation |
| `extraction_fixed_but_information_frontier_holds` | §B — Source onboarding (transferable from cycle-16-skeletons §B) OR §C — strategic-pivot per operator decision |
| `cycle_16d_extension_needed` | §D-extension — second backfill attempt at Cycle-16D re-open OR §C per operator decision |
| `escalation_required` | §E — operator scope-extension fresh-charter OR §C strategic redesign per operator decision |

### 2026-05-07 amendment: Cycle-16E scorer forensics

Cycle-16D operational reading was withdrawn 2026-05-07 pending Cycle-16E scorer forensics. Cycle-16E delivered (Codex commit `c913ffd`) and Cycle-16E verdict = `scorer_fixed_no_signal_confirmed` (production-proxy 12 trades / 0 wins / 0 IC §16 slices; matches market-implied baseline).

**Routing impact:** Cycle-17 §B/§C operator decision is **RESTORED** (un-deferred). Cycle-16E confirmed the cycle-16D `extraction_fixed_but_information_frontier_holds` label maps to outcome 2 in this map → §B / §C.

**Cycle-16F additional forensics not triggered.** The "anti-correlated signal" / "extraction overfit" hypotheses raised in cycle-16D M6 appendix are withdrawn — they were artifacts of (a) wrong baseline assumption (50% coin-flip vs market-implied 9.463 expected wins), and (b) scorer overadmission. Both addressed by Cycle-16E.

If operator picks §B, the "mandatory pre-onboarding re-trace of 235 losers" requirement from cycle-16D M6 appendix is **RELAXED** — it was driven by anti-correlation hypothesis. §B can proceed against current evidence, gated only on candidate-source replay validation per IC §16 Rule 4.

## §A — Cycle-17A: Wave-2 candidate slice deploy + replay validation

**Trigger:** Cycle-16D D8 verdict = `extraction_fixed_with_positive_ev_slice`. D5 coverage ≥ 90% AND D8 ≥ 1 IC §16 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.

**Goal:** ship the Cycle-15B C7 keyword fix (already deployed in production code) + Wave-2 deploy the identified positive-EV slice with slice-specific replay validation per IC §16. Mirrors `cycle-16-conditional-charter-skeletons.md` §A.

**Codex deliverables (Cycle-17A):**

1. **Wave-2 candidate slice identification.** From Cycle-16D D8 output, name the (source × market_family × signal_type) slice. Pre-staged Wave-2 specs (legal/geopolitics speculation) are NOT the candidate.
2. **Slice-specific deploy plan.** Define the source / classifier / blender configuration that activates the slice. Confirm changes do NOT broaden beyond the named slice.
3. **Pre-deploy replay validation.** Re-run Cycle-16D D6 replay with operator-stated 95% CI threshold confirmed. Demonstrate the slice still passes IC §16 with current evidence.
4. **Post-deploy ongoing replay cadence.** Schedule Cycle-N replay re-runs at every +30 trades milestone OR every 30 days, whichever first, on the deployed slice. Replay regression triggers automatic kill-switch on the slice (operator-confirmable).
5. **PRE_FIX cohort archival confirmation.** Per IC §16 Rule 6 + cohort note: PRE_FIX paper_trades + dossier_updates archived; POST_FIX_REBUILT and POST_FIX_NEW cohorts continue tracking.

**Acceptance:** Wave-2 slice deployed with active replay cadence + operator-explicit live-trading-enabled flip authorization (separate commit, replay-report citation in commit message). Wave-3 / Branch-D remain HALTED until Wave-2 produces ≥ 30 live trades AND replay re-run confirms slice still positive-EV.

**Capital posture:** PAPER-ONLY UNTIL operator explicit live-trading flip + post-flip first-30-trades soak. Then live capital may engage on the named slice ONLY, subject to ongoing replay cadence.

**Estimated scope:** 1-2 weeks Codex implementation + operator-decision-time on live-trading flip authorization. Cycle-17A success closes EDGE-009 + EDGE-010; Cycle-18+ may unblock Wave-3 candidate slices.

## §B — Cycle-17B: Source-onboarding-with-replay-evidence scope

**Trigger:** Cycle-16D D8 verdict = `extraction_fixed_but_information_frontier_holds`. D5 coverage ≥ 90% AND D8 0 IC §16 slices. Conclusion: extraction now works (Cycle-15B); prices now reconstructable (Cycle-16D); bot's source mix doesn't carry decisive signal.

**Goal:** identify alternative sources that DO carry decisive signal + onboard one. Mirrors `cycle-16-conditional-charter-skeletons.md` §B with the difference that price reconstruction is now confirmed.

**Codex deliverables (Cycle-17B):**

1. **Source-discovery research:** identify 3-5 candidate source classes. Possibilities: government Twitter/social, primary-source regulatory feeds (SEC EDGAR, Federal Register, court filings), specialist insider blogs, RSS from official agency channels.
2. **Backfill experiment:** if any candidate source has accessible historical content, backfill the bot's `evidence_store` with synthetic ingestion against the past 16-day window. Re-extract through Cycle-15B-fixed extraction.
3. **Replay against backfill:** re-run Cycle-16D D6 replay with the new evidence rows included. Determine whether any slice from new sources produces positive replayed EV.
4. **If yes:** that slice becomes the Wave-2 candidate (analogous to Cycle-17A path).
5. **If no:** information frontier confirmed at trader's data access. Cycle-18 = strategic redesign or pause (Cycle-17C path).

**Acceptance:** at least one source-class slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.

**Estimated scope:** 2-4 weeks. Source onboarding includes legal review (some sources have ToS), API integration, ingestion pipeline extension.

**Important:** Cycle-17B does NOT onboard sources speculatively. Each candidate must clear backfill replay before deploy.

## §C — Cycle-17C: Strategic-pivot scope (potentially pause bot)

**Trigger:** Cycle-16D D8 verdict = `extraction_fixed_but_information_frontier_holds` OR `escalation_required` OR `cycle_16d_extension_needed`, with operator picking redesign over §B / §D-extension. Mirrors `cycle-16-conditional-charter-skeletons.md` §C.

**Goal:** halt active development; redirect resources OR fundamentally redesign the bot's information-set / model / market selection.

**Operator-decision-doc deliverables (Cycle-17C):**

1. **Honest write-up:** "The bot in current form does not have edge against Kalshi at our information set." Explicit, no hedging. References Cycle-13 + Cycle-14 + Cycle-15B + Cycle-16D verdict trail.
2. **Three-options menu for operator** (mirrors prior skeletons):
   - **(a) Pause:** stop bot, archive code/data, redirect time/resources elsewhere.
   - **(b) Fundamental redesign:** different sources, different model, different markets, different sizing.
   - **(c) Continuation as data-collection:** keep paper-mode running indefinitely; never live-trade; treat as research project.
3. **Operator picks.** No technical Cycle-17C deliverables until operator decides.

**Acceptance:** operator decision documented + filed in `docs/profit_path_debt_log.md` + ROADMAP refreshed.

**Capital posture:** PAPER-ONLY until operator picks. (a) → no further work. (b) → Cycle-17C-redesign starts; no live-trading until full Wave-1/2/3-equivalent replay validation. (c) → indefinite paper-only.

**Estimated scope:** Cycle-17C itself is operator-decision-only (~1 hour). If operator picks (b), the redesign cycle is multi-month.

## §D-extension — Cycle-16D-extension: Second backfill attempt

**Trigger:** Cycle-16D D5 coverage 70-89% (`cycle_16d_extension_needed` verdict) AND operator picks "try second backfill" over redesign.

**Goal:** apply a second backfill approach (multi-source merge OR different endpoint OR refined approximation) to lift coverage to ≥ 90%.

**Constraints (load-bearing):**

- Counts as fix attempt #2 for the `superpowers:systematic-debugging` "3+ fixes failed → architectural conversation" trigger. After 3 failed backfill attempts, operator MUST escalate to Cycle-17C redesign rather than try a 4th.
- Same charter, task split, and acceptance criteria apply (re-run Codex D3-D10 with the second-backfill source). M1-M10 already-landed scaffolding does not re-run; new pre-execution criteria-lock verification (M2-equivalent) re-runs against the second backfill path.
- POST_FIX_REBUILT cohort still intact; no re-ingestion needed.

**Acceptance:** same as Cycle-16D charter — D5 coverage ≥ 90% AND D8 ≥ 1 IC §16 slice.

## §E — Operator scope-extension fresh charter

**Trigger:** Cycle-16D D5 coverage < 70% (`escalation_required` verdict) OR consecutive backfill failures across Cycle-16D + extension exhaust the 3-attempt rule.

**Goal:** operator-decision-only meta-cycle. Operator decides whether the price-reconstruction path is salvageable at all OR whether the bot's IC §16 evaluability is fundamentally blocked at this trader's data access.

**Operator-decision-doc deliverables (Cycle-17E):**

1. **Coverage-failure write-up.** What was tried; why each attempt fell short.
2. **Three options:**
   - **(a) Fresh charter for a different price-reconstruction approach** (e.g., direct Kalshi data partnership, on-chain settlement records if Kalshi exposes any, paid third-party historical aggregator).
   - **(b) Accept IC §16 unevaluable at current data access; route to Cycle-17C redesign.**
   - **(c) Pause bot indefinitely.**
3. **Operator picks.**

**Acceptance:** operator decision documented + ROADMAP refreshed.

**Estimated scope:** ~1 hour operator decision; option (a) launches multi-week scope.

## What this skeleton-set does NOT include

- Substantive content for any branch. Each skeleton is a 1-page outline. Cycle-17<X> charter authors the substantive scope when verdict lands.
- Any pre-judgment on which verdict will land. All 4 are equally weighted prior to Cycle-16D D8 running.
- Auto-instantiation triggers. Operator manually picks the matching skeleton the day verdict lands.
- Wave-3 or Branch-D scope. Those wait until Cycle-17A produces a deployed Wave-2 slice with sufficient live-trade history.

## Cross-links

- `docs/_archive/governance/2026-05-07-cycle-16d-charter-price-reconstruction.md` — Cycle-16D charter (verdict source) (ARCHIVED Stream G R16)
- `docs/_archive/governance/cycle-16-conditional-charter-skeletons.md` — Cycle-16 skeletons (origin pattern; §A/§B/§C transferable to Cycle-17 §A/§B/§C)
- `docs/_archive/governance/cycle-16d-post-verdict-action-checklist.md` — Cycle-16D post-verdict checklist (filed this skeleton-set) (ARCHIVED Stream G R21)
- `docs/_archive/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` — cohort definitions (ARCHIVED Stream G R18)
- `docs/_archive/governance/edge-replay-pivot-playbook.md` — IC §16 Rule 5 diagnostic playbook
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs all Cycle-17 deploys)
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-009` — Cycle-16D debt entry (PROFIT-EDGE-010+ filed by matching Cycle-17<X>)
