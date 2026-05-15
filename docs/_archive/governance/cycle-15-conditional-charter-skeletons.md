# Cycle-15 conditional charter skeletons

**Type:** pre-staged charter scaffolds. ONE per Cycle-14 verdict outcome. Operator instantiates the matching skeleton the day Cycle-14 verdict lands.
**Drafted:** 2026-05-06 cycle 14 prep.
**Authority:** Cycle-14 charter §"Pre-stated decision criteria" — Cycle-15 scope derives FROM the verdict, not invented to fit a preferred fix.

## TL;DR

Cycle-14 produces a verdict in one of 6 categories. Pre-staging skeletons for each prevents the failure mode where Cycle-15 scope is improvised in the heat of the moment + drifts toward operator's preferred fix.

The verdict comes first. The skeleton that matches the verdict instantiates. NO substitution; NO blending.

## Verdict-to-skeleton map

| verdict | skeleton |
|---|---|
| `sign_error` | §A — Sign-error fix scope |
| `extraction_broken` | §B — Extraction-layer rebuild scope |
| `information_frontier` | §C — Source-onboarding-with-replay-evidence scope |
| `model_fine` | §D — Continuation paper-only data-collection scope |
| `sample_noise` | §E — Extend evidence-store window scope |
| `redesign` | §F — Strategic-pivot scope (potentially pause bot) |

## §A — Cycle-15A: Sign-error fix scope

**Trigger:** Cycle-14 verdict = `sign_error`. Direction-correctness when moved < 50%; specific file:line + before/after pseudocode identified per Cycle-14 charter §"1-line fix verification gate."

**Goal:** ship the sign-inversion fix with replayed-EV evidence per IC §16.

**Codex deliverables (Cycle-15A):**
1. Apply the named 1-line fix at the named file:line. Confirm Codex + Claude + operator agreement on location + before/after.
2. Run synthetic Lane A + Lane B against post-fix code. Both lanes must produce direction-correct movement (≥ 90% on synthetic fixtures).
3. Re-run full Cycle-13 replay against post-fix dossier_updates (need re-ingestion of the 16-day evidence window OR re-run of dossier update logic on existing evidence rows). Report new (source × market_family × signal_type) slice table.
4. Compare pre-fix vs post-fix replayed EV. Demonstrate at least one slice has `ev_ci_95_lo > 0` AND `trades ≥ 10` per IC §16.
5. Update `paper_trades` schema-or-doc note: pre-fix paper-traded data is no longer ground truth for calibration; post-fix re-ingestion required for any future replay validation.

**Acceptance:** post-fix replay shows ≥ 1 positive-EV slice; Cycle-15A diagnosis-doc proves IC §16 evidence gate cleared.

**Capital posture:** stays paper-only until acceptance criteria met AND operator explicitly authorizes live-trading-enabled flip with replay report citation in commit message.

**Estimated scope:** 1-3 days Codex implementation + 1 day Claude review + 1 day re-ingestion run. Cycle-15A success closes EDGE-007 + EDGE-008; Cycle-16+ unblocks Wave-2 candidate-slice deploy.

## §B — Cycle-15B: Extraction-layer rebuild scope

**Trigger:** Cycle-14 verdict = `extraction_broken`. Synthetic Lane A passes (downstream of extraction) but Lane B fails (real extraction in loop). Conclusion: dossier update math works on well-shaped input; extraction layer corrupts signal en route.

**Goal:** rebuild OR replace extraction layer (`signal_analyzer` + classifier path).

**Codex deliverables (Cycle-15B):**
1. Diagnose specific extraction failure mode. Compare fixture-input to fixture-after-extraction; identify what changes that shouldn't.
2. Author or refactor extraction logic. Likely candidates: keyword direction map, LLM prompt convention, geo-coherence suppression, magnitude-shift mapping.
3. Re-run synthetic Lane B against new extraction. Must match Lane A direction-correctness (≥ 90%).
4. Re-ingest 16-day evidence window through new extraction. Re-build dossier_updates. Re-run replay scoring.
5. IC §16 evidence: post-fix replay shows ≥ 1 positive-EV slice with `trades ≥ 10`.

**Acceptance:** same as §A but with extraction-rebuild root cause documented.

**Estimated scope:** 1-2 weeks. Extraction-layer changes touch `analysis/` substantially; review burden higher.

## §C — Cycle-15C: Source-onboarding-with-replay-evidence scope

**Trigger:** Cycle-14 verdict = `information_frontier`. Both synthetic lanes pass (dossier update + extraction work on well-shaped input). Real production data fails. Conclusion: bot's source mix doesn't carry decisive signal; mainstream news (81 % of evidence) is downstream of Kalshi market-makers + crowd.

**Goal:** identify alternative sources that DO carry decisive signal + onboard one.

**Codex deliverables (Cycle-15C):**
1. **Source-discovery research:** identify 3-5 candidate source classes. Possibilities: government Twitter/social, primary-source regulatory feeds (SEC EDGAR, Federal Register, court filings), specialist insider blogs, RSS from official agency channels. Per the strategic-redirect doc's diagnosis #3.
2. **Backfill experiment:** if any candidate source has accessible historical content (e.g., Federal Register has API archives), backfill the bot's evidence_store with synthetic ingestion against the past 16-day window.
3. **Replay against backfill:** re-run Cycle-13 scoring with the new evidence rows included. Determine whether ANY slice from the new sources produces positive replayed EV.
4. **If yes:** that slice becomes the Wave-2 candidate. NOT the speculative legal/geopolitics feeds from the original Lever A.1+ menu.
5. **If no:** information frontier confirmed at this trader's data access. Cycle-16 = strategic redesign or pause.

**Acceptance:** at least one source-class slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.

**Estimated scope:** 2-4 weeks. Source onboarding includes legal review (some sources have ToS), API integration, ingestion pipeline extension. Higher scope than §A or §B.

**Important:** Cycle-15C does NOT onboard sources speculatively. Each candidate must clear backfill replay before deploy. The pre-cycle-12 "deploy hope" pattern stays prohibited.

## §D — Cycle-15D: Continuation paper-only data-collection scope

**Trigger:** Cycle-14 verdict = `model_fine`. Movement_rate ≥ 10% AND direction-correctness ≥ 60%. The model IS calibrated; the 3-trade 0/3 loss is genuinely sample noise.

**Goal:** continue paper-only operation; gather more sized-bet evidence; reassess at larger n.

**Codex deliverables (Cycle-15D):**
1. **Continuation runbook:** document the bot continues paper-only at current configuration. Capital posture remains paper-only.
2. **Sample-size projection:** at current ingestion rate (~10 evidence/day, ~1 trade/100 evidence), how many days/weeks until the bot accumulates n=30 trades for power-adequate replay? Realistic answer probably 3-6 months.
3. **Periodic replay-verdict cadence:** schedule Cycle-N replay re-runs at every +30 trades milestone OR every 30 days, whichever first.
4. **No deploy:** Wave-2 / Wave-3 STAY HALTED until Cycle-N+1 replay shows positive-EV slice at adequate sample.

**Acceptance:** docs stating "bot continues paper-only; replay-verdict re-runs scheduled" + cycle-N+1 milestone defined.

**Capital posture:** paper-only INDEFINITELY in this branch. Live-trading-enabled flip remains gated on positive-EV replay.

**Estimated scope:** ~1 hour doc work. The "fix" here is operator patience, not code.

## §E — Cycle-15E: Extend evidence-store window scope

**Trigger:** Cycle-14 verdict = `sample_noise` (rare; usually not standalone). Movement_rate is high, direction-correctness is high on synthetic, but full corpus produces too-few moved decisions for power. Replay window is genuinely too short.

**Goal:** extend evidence-store window via backfill (similar to §C but for historical-evidence collection rather than new-source onboarding).

**Codex deliverables (Cycle-15E):**
1. Identify backfill source: bot's historical RSS-feed cache, third-party news archive, or extended ingestion period.
2. Backfill evidence_store to N=60+ resolved markets (target: 6+ months of corpus or 2× current 16-day window).
3. Re-run Cycle-13 replay at extended scope.
4. Verdict: positive-EV slice surfaces or not.

**Acceptance:** scope extended; verdict reported.

**Estimated scope:** 1-2 weeks if backfill source is straightforward; 4+ weeks if requires building a historical-ingestion pipeline.

## §F — Cycle-15F: Strategic-pivot scope (potentially pause bot)

**Trigger:** Cycle-14 verdict = `redesign` OR §B/§C/§E exhausted without finding edge.

**Goal:** halt active development; redirect resources OR fundamentally redesign the bot's information-set / model / market selection.

**Operator-decision-doc deliverables (Cycle-15F):**
1. **Honest write-up:** "The bot in current form does not have edge against Kalshi at our information set." Explicit, no hedging.
2. **Three-options menu for operator:**
   - **(a) Pause:** stop bot, archive code/data, redirect time/resources elsewhere.
   - **(b) Fundamental redesign:** different sources (insider, regulatory, primary-source), different model (different LLM, different update math), different markets (niche / low-volume / novel-event over efficient sports/elections), different sizing (smaller sized bets to extend bankroll runway).
   - **(c) Continuation as data-collection:** keep paper-mode running indefinitely; never live-trade; treat as research project.
3. **Operator picks.** No technical Cycle-15F deliverables until operator decides.

**Acceptance:** operator decision documented + filed in `docs/profit_path_debt_log.md` + ROADMAP refreshed.

**Capital posture:** PAPER-ONLY until operator picks. (a) → no further work. (b) → Cycle-15F-redesign starts; no live-trading until full Wave-1/2/3-equivalent replay validation. (c) → indefinite paper-only.

**Estimated scope:** Cycle-15F itself is operator-decision-only (~1 hour). If operator picks (b), the redesign cycle is multi-month.

## What this skeleton-set does NOT include

- Substantive content for any branch. Each skeleton is a 1-page outline. Cycle-15X charter authors the substantive scope when verdict lands.
- Any pre-judgment on which verdict will land. All 6 are equally weighted prior to Cycle-14 audit running.
- Auto-instantiation triggers. Operator manually picks the matching skeleton the day verdict lands.

## Cross-links

- `docs/_archive/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md` — Cycle-14 charter (verdict source)
- `docs/_archive/governance/edge-replay-pivot-playbook.md` — IC §16 Rule 5 diagnostic playbook
- `docs/_archive/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — strategic redirect authority
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs all Cycle-15 deploys)
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-007` — Cycle-14 debt entry (PROFIT-EDGE-008+ filed by matching Cycle-15X)
