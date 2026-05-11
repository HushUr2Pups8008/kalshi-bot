# Cycle-17D Charter Amendment — Broader-Corpus Replay Authorized

**Date:** 2026-05-10  
**Author:** Claude  
**Predecessor Charter:** `2026-05-07-cycle-17c-charter-single-variable-redesign.md`  
**Trigger:** Operator directive 2026-05-10 (charter-level fixed-corpus reconsideration following structural findings E2 + E3)  
**Authority:** PROFIT-EDGE-012  
**Status:** HALTED 2026-05-10 before wrapper/sweep; see `2026-05-10-cycle-17d-halt-on-historical-corpus-degeneracy.md`

---

## §1 — Problem Statement

Cycle-17C experiments E2 (G1 admission sweep) and E3 (side-flip counterfactual) surfaced the same structural blocker: the frozen Cycle-16D corpus (272 dossier rows) admits exactly 12 production-proxy trades across the entire design-space of G1 threshold choices and side-flip counterfactuals.

**E2 finding (2026-05-08):** outcome-blind G1 confidence-threshold sweep over `{0.05, 0.04, 0.03, 0.02, 0.01, 0.00}` produced identical counts at every threshold. Production-proxy admission ceiling stays at 12 regardless of any G1 threshold change. Root-cause analysis: `paper_price_sanity = 119` is the dominant filter (45.8% of all skips), a corpus-mix property — the cycle-16D market sample contains many extreme-priced longshots (price ≤ 2¢ or ≥ 98¢) that fail this gate.

**E3 finding (2026-05-10):** flip-sign counterfactual on the same 12 admitted rows distributed across `8×5×2×2 = 160` possible 4-axis intersected bins (`signal_source × market_family × signal_type × news_class`), max actual bin = 9 rows. No slice clears the IC §16 `trades >= 10` gate. Verdict: `revert_required_no_ic16_slice`.

**Implication:** The within-corpus redesign-axis search space is exhausted. Lifting the 12-trade ceiling under Cycle-17C requires either loosening `paper_price_sanity` (risk-mode change, not signal experiment), a different corpus (violates fixed-corpus rule), or a charter-level reconsideration. Operator picks the third option.

---

## §2 — Operator-Locked Invariants (Preserved Verbatim)

The following five invariants from Cycle-17C remain locked and non-negotiable for Cycle-17D:

1. **IC §16 acceptance criteria.** ≥1 (`signal_source × market_family × signal_type × news_class`) slice with `ev_ci_95_lo > 0` AND `trades >= 10`. Per `docs/IMPLEMENTATION_CONTRACT.md` §16. Verbatim from Cycle-17C charter §"Acceptance bar = IC §16."

2. **Market-implied baseline as null model.** NOT 50% coin-flip. Per memory `feedback_market_implied_baseline.md`. Each experiment reports actual wins vs `Σ p_yes_at_decision_time` for the trades it produces.

3. **PAPER-ONLY capital posture.** Hard guardrail per Cycle-14 charter §5. NO experiment in Cycle-17D flips this. Live trading authorization remains gated behind Cycle-17 §A deploy-candidate review (slice-specific risk review + capital allocation per IC §16 Rule 4 + kill-switch plan + operator commit).

4. **Fixed pre-registered corpus discipline.** Corpus locked at build-script-land time (before each experiment), not amendable mid-experiment. Each experiment uses the same locked corpus without drift.

5. **Single-variable rule, revert default, criteria-lock-before-replay.** One hypothesis per experiment; only pre-declared changes with locked acceptance threshold; revert is the default outcome; criteria-lock commit lands before replay runs. Mirrors Cycle-17C charter §"Pre-stated decision criteria (LOCKED)."

---

## §3 — Operator Decisions (Verbatim)

### (a) — Cycle-13 source merging

Merge **both** `logs/edge_replay/cycle13_live/replay_dataset.jsonl` AND `logs/edge_replay/cycle13_local/replay_dataset.jsonl`. Dedup by tuple `(ticker, decision_ts, signal_source, headline)`. On dedup conflict: prefer `cycle13_live` over `cycle13_local`. Record the dropped duplicate count in the build artifact. This recovers any rows that exist in only one cohort (live-prod-extracted vs local-reconstruction paths).

### (b) — E4 axis pick deferred

**DEFER** until post-sweep. No E4 axis is locked at amendment time. The amendment landing does NOT name a next experiment. Axis pick follows the GO/NO-GO admission-count sweep result (§6 below).

### (c) — Revert budget reset

**RESET** to 0/3 for Cycle-17D. Cycle-17C's E1 + E3 reverts (2/3) are recorded as historical evidence in the amendment rationale (§1 above), but the new search space starts fresh. Operator rationale: "this is a charter amendment with a changed corpus surface, so the old revert budget should not mechanically punish the new search space."

### (d) — Clause E (cohort-drift disqualification)

**ADD** to the locked acceptance bar. 4-axis (`signal_source × market_family × signal_type × news_class`) is the IC §16 acceptance metric (preserves charter intent — single bar, no new degree of freedom). 5-axis (4 + `cohort` flag per IC §16 Rule 6) is diagnostic AND a disqualifier: any IC §16-eligible 4-axis slice whose `cohort_breakdown` is >75% concentrated in a single cohort (PRE_FIX / POST_FIX_REBUILT / POST_FIX_NEW) verdicts as `revert_cohort_drift_driven`. Justification: a slice drawing all statistical power from one cohort is testing extraction-regime difference, not signal quality.

### (e) — Operator tweak: GO/NO-GO admission-count sweep (first post-amendment step)

**CRITICAL** — this supersedes the architect's draft E4 launch sequence. The first post-amendment step is a **GO/NO-GO admission-count sweep**, NOT an E4 experiment launch.

**Threshold for GO:** at least one 4-axis intersected bin (`signal_source × market_family × signal_type × news_class`) has ≥10 admitted rows in the merged Cycle-17D corpus.

**If NO-GO:** Cycle-17D **STOPS before any redesign-axis experiment burns time or revert budget**. Operator decides next step: Option B or Option C corpus scope (TBD by operator), or pause Cycle-17 entirely.

**Operator-stated framing (verbatim):** "If the sweep does not produce enough eligible slice mass, Cycle-17D should stop before any redesign-axis experiment burns time or budget."

---

## §4 — Charter Deltas (Architect-Designed + Operator Overrides)

### Delta 1 — Corpus-binding rule (verbatim, SHA placeholder)

Replace the §"Hard constraints" first bullet of Cycle-17C charter ("Fixed corpus. Cycle-16E replay dataset...") with:

> **Fixed corpus (Cycle-17D broader-corpus replay).** Locked merged corpus at `logs/edge_replay/cycle17d/replay_dataset_merged.jsonl` — a deduplicated concatenation of `logs/edge_replay/cycle13_live/replay_dataset.jsonl`, `logs/edge_replay/cycle13_local/replay_dataset.jsonl`, `logs/edge_replay/cycle15b/replay_dataset.jsonl`, and `logs/edge_replay/cycle16d/replay_dataset.jsonl`. Dedup key: `(ticker, decision_ts, signal_source, headline)`. On dedup conflict between cycle13_live and cycle13_local, prefer cycle13_live and record the dropped row count in the build artifact. Each row carries a `cohort` flag (`PRE_FIX` / `POST_FIX_REBUILT` / `POST_FIX_NEW`) per IC §16 Rule 6. **SHA-256 of merged file (pinned 2026-05-10):** `a0f5401b65acd9592e2dcc1c34bb0b9d0c76fe4718a2d714a9bc29160244f913` — post-normalization SHA from schema-audit commit `6e626ea` (Codex added `market_family` field per the locked 4-axis sweep grouping). Supersedes the pre-normalization SHA `ab9ae8e9cf8f23349d7c96206c443ed1db52ebee0180a1c51f507690d958236c` from initial build commit `1336fe2`. Build manifest at `logs/edge_replay/cycle17d/build_manifest.json` records: 513 merged rows / 292 dropped duplicates / cohort breakdown `{POST_FIX_REBUILT: 272, PRE_FIX: 241, POST_FIX_NEW: 0}` / blank-headline normalizations `{cycle13_live: 2, cycle15b: 2, cycle16d: 2}`.

**⚠️ Schema-audit blocker (step 3 finding, 2026-05-10):** Per `logs/edge_replay/cycle17d/schema_compatibility_audit.json` (`production_proxy_ready: false`), the merged corpus is missing production-proxy fields on a structurally significant fraction of rows:

- `market_yes_price` missing on 239 rows (POST_FIX_REBUILT: 1, **PRE_FIX: 238 of 241**)
- `confidence` missing on 37 rows (POST_FIX_REBUILT: 34, PRE_FIX: 3)
- `edge` missing on 33 rows (POST_FIX_REBUILT: 33, PRE_FIX: 0)
- `model_prob` missing on 34 rows (POST_FIX_REBUILT: 34, PRE_FIX: 0)

Rows with ALL four production-proxy fields present: **POST_FIX_REBUILT 237/272; PRE_FIX 0/241; total 237.**

PRE_FIX price backfill from Kalshi API is **infeasible**: `logs/edge_replay/cycle13_live/historical_prices.json` is empty (`{}`); `historical_prices.errors.json` records 404 responses on all cycle-13 tickers (Kalshi retired the trades endpoint for these settled markets). No alternate source available within the repo.

**Effective admissible cohort = POST_FIX_REBUILT only (237 rows; equivalent to or slightly smaller than the original 272-row cycle-16D corpus that produced the 12-row admission ceiling in E2 + E3).** The merged-corpus approach did NOT lift the ceiling. Decision routed to operator before step 5 (GO/NO-GO sweep) — sequence HALTED at step 3.
>
> Cycle-16D-only baseline (12 trades / 0 wins / -$1.005 P&L / 0 IC §16 slices) remains the **frozen-baseline-on-cycle-16d-subset** reference. The broader-corpus baseline (E0') was not produced because Cycle-17D halted at step 3 before wrapper/sweep work.

### Delta 2 — Replay command (verbatim)

Replace the §"Hard constraints" replay-command bullet with:

> **Fixed replay command.** Planned wrapper `scripts/edge_replay/run_cycle17d_replay.sh --skip-price-backfill` was authorized but not produced. Cycle-17D halted at step 3 before wrapper-script work. See `2026-05-10-cycle-17d-halt-on-historical-corpus-degeneracy.md`.

### Delta 3 — Cohort discipline new clause (verbatim)

Add new bullet to §"Hard constraints" (after the corpus and replay-command bullets):

> **Cohort flag retention.** Every row in the merged corpus carries a `cohort` flag per IC §16 Rule 6. Slice grouping for IC §16 evaluation MUST be reported BOTH as 4-axis (`signal_source × market_family × signal_type × news_class`) AND as 5-axis (4 above + `cohort`). The 4-axis result is the IC §16 acceptance metric (preserves charter intent — single bar, no new degree of freedom). The 5-axis result is a diagnostic-only stratification that flags cohort-driven false positives. **Clause E (cohort-drift disqualification)** in the criteria-lock template formalizes the disqualifier: any IC §16-eligible 4-axis slice whose `cohort_breakdown` is >75% concentrated in a single cohort verdicts as `revert_cohort_drift_driven`.

### Delta 4 — E0' broader-corpus baseline subsection (verbatim)

Add new subsection inside §"Frozen baseline" (after the existing cycle-16E paragraph):

> **Cycle-17D broader-corpus baseline (E0').** Authorized at filing time but not produced. The merged-corpus path halted at step 3 before GO/NO-GO sweep, wrapper, or baseline work. The cycle-16E E0 row remains intact as the last executed historical reference.

### Delta 5 — Amendment dated entry (verbatim)

Add new dated entry to §"Amendments and structural findings" (in `2026-05-07-cycle-17c-charter-single-variable-redesign.md`):

> ### 2026-05-10 — Cycle-17D amendment: broader-corpus replay authorized
>
> **Source.** E2 + E3 structural finding. E2 (`docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md`) showed `production_proxy=12` admission count is identical at every tested G1 threshold — `paper_price_sanity=119` is the dominant filter, a corpus property of the cycle-16D market mix. E3 (`docs/governance/2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md`) reverted with `revert_required_no_ic16_slice` because the same 12 admitted rows distribute across 8×5×2×2 = 160 possible 4-axis intersected bins, max actual bin = 9 rows, no slice clears trades≥10. The redesign-axis space WITHIN cycle-16D is exhausted.
>
> **Operator pick (2026-05-10).** Charter-level fixed-corpus reconsideration. Do NOT run E4 inside cycle-16D. Draft Cycle-17D amendment for broader-corpus replay with all locked invariants preserved.
>
> **Amendment surface.** Deltas 1–4 above. The amendment binds: (1) the corpus identity (merged cycle-13_live + cycle-13_local + cycle-15B + cycle-16D, dedup tuple `(ticker, decision_ts, signal_source, headline)`, cycle13_live preferred on conflict); (2) the replay-command wrapper; (3) cohort-flag retention as 4-axis-acceptance + 5-axis-diagnostic; (4) the E0' broader-corpus baseline rule.
>
> **Operator-locked invariants preserved.** IC §16 acceptance verbatim, market-implied baseline retained, PAPER-ONLY hard guardrail, fixed pre-registered corpus discipline (corpus locked at build-script-land, not amendable mid-experiment), single-variable rule, revert default, criteria-lock-before-replay, 3-revert architectural-rethink rule.
>
> **Revert budget reset.** Cycle-17C carried E1+E3 reverts (2/3). Cycle-17D resets to 0/3. Operator rationale: "this is a charter amendment with a changed corpus surface, so the old revert budget should not mechanically punish the new search space." E1 and E3 retained as historical evidence (cited verbatim above as the amendment trigger).
>
> **First post-amendment step.** Authorized plan at filing time: GO/NO-GO admission-count sweep on the merged corpus. Superseded by step-3 halt after schema audit found PRE_FIX rows structurally unusable. No sweep ran and no E4 axis was picked.

### Delta 6 — Sequencing (verbatim, with operator-tweak GO/NO-GO)

Replace the Cycle-17C §"Sequencing" with:

> Strict sequence for Cycle-17D as authorized at filing time. Steps 4-10 were not executed because the cycle halted at step 3; see `2026-05-10-cycle-17d-halt-on-historical-corpus-degeneracy.md`.
>
> 1. **Charter amendment lands** (this commit).
> 2. **Build script lands** — `scripts/edge_replay/build_cycle17d_corpus.py` constructs the merged corpus, applies dedup, records dropped-duplicate count, computes SHA-256, writes the merged JSONL. Lands in a separate commit. Charter is updated to pin the SHA.
> 3. **Schema-compatibility audit lands** — single-commit scorer-tooling-fix per the charter exception. Confirms all four vintages (cycle13_live, cycle13_local, cycle15B, cycle16D) carry compatible row schemas for `scorer_forensics_audit.py` production-proxy mode. If incompatibilities surface, audit lands the minimum schema-normalization patch.
> 4. **Wrapper script lands** — `scripts/edge_replay/run_cycle17d_replay.sh`. Lands in a separate commit.
> 5. **GO/NO-GO admission-count sweep** — outcome-blind sweep on merged corpus using `g1_admission_sweep.py` reference pattern (new script `scripts/edge_replay/cycle17d_admission_sweep.py` if needed; otherwise extend the existing one with a corpus-path argument). **Threshold for GO: at least one 4-axis intersected bin (`signal_source × market_family × signal_type × news_class`) has ≥10 admitted rows.** Result lands as a governance ledger doc (`docs/governance/2026-05-10-cycle-17d-admission-sweep.md` or similar). If NO-GO, Cycle-17D HALTS at this step; operator decides escalation path.
> 6. **(GO only)** E0' broader-corpus baseline runs; recorded as ledger row.
> 7. **(GO only)** E4 axis pick — operator decides post-sweep, given the cohort-breakdown + admission-count evidence the sweep produces. No default; deferred until step 5 result is in hand.
> 8. **(GO only)** E4 criteria-lock commits — mirrors the structure of `2026-05-10-cycle-17c-e3-criteria-lock.md` with Cycle-17D corpus paths + Clause E (cohort-drift disqualification).
> 9. **(GO only)** E4 replay runs; verdict appendix lands.
> 10. **(GO only)** Ledger row updated post-verdict.
> 11. The 3-revert architectural-rethink rule applies fresh from 0/3 for Cycle-17D.

---

## §5 — Revert-Budget Tracker

| Experiment | Status | Counts toward 3-revert budget? |
|--|--|--|
| E1 — Bayesian log-odds update rule | REVERTED 2026-05-07 | YES — 1/3 |
| E2 — G1 readiness admission sweep | AXIS_ABANDONED_BEFORE_CRITERIA_LOCK 2026-05-08 | NO (per ledger exception — no implementation commit) |
| E3 — Side-inference flip-sign | REVERTED 2026-05-10 (`revert_required_no_ic16_slice`) | YES — 2/3 |
| **Cycle-17D revert budget** | **RESET 2026-05-10** | **0/3 fresh start** |

---

## §6 — First Post-Amendment Step: GO/NO-GO Admission-Count Sweep Specification

**Input:**
- Merged corpus at `logs/edge_replay/cycle17d/replay_dataset_merged.jsonl` (locked per Delta 1).
- Audited scorer + production-proxy gates per Delta 2.

**Output:**
- Governance ledger doc at `docs/governance/2026-05-10-cycle-17d-admission-sweep.md` (or similar timestamp).

**Outcome-blind sweep logic:**
- Apply production-proxy gate set (identical to cycle-16E reference pattern: price floor/ceiling 2¢/98¢, ticker cooldown 14400s, paper-duplicate prob/price 0.07/5.0, same-signal prob/price 0.02/2.0, PAPER_MIN_EDGE 0.02).
- Count admitted rows.
- Stratify admissions by 4-axis bins (`signal_source × market_family × signal_type × news_class`).
- Record cohort-breakdown for each bin (PRE_FIX / POST_FIX_REBUILT / POST_FIX_NEW counts).

**Threshold for GO:**
- At least one 4-axis intersected bin has ≥10 admitted rows.

**Threshold for NO-GO:**
- Max 4-axis bin admission < 10.

**If NO-GO:**
- Cycle-17D STOPS. Operator decides next step:
  - Option B or Option C corpus scope (separate operator decision, not part of this amendment).
  - Pause Cycle-17 entirely.
  - No redesign-axis experiment (E4+) lands until corpus scope is resolved.

**If GO:**
- Proceed to §4 Delta 6 step 6 (E0' baseline).

---

## §7 — Sequencing

See §4 Delta 6 above (verbatim). Strict sequence: amendment → build → schema audit → wrapper → sweep → (GO-dependent) E0' → (GO-dependent) E4 pick → (GO-dependent) E4 lock → (GO-dependent) E4 replay → (GO-dependent) ledger update.

---

## §8 — Capital Posture (Re-Affirmed)

PAPER-ONLY. Hard guardrail per Cycle-14 charter §5. NO experiment in Cycle-17D — including a hypothetical E4 `keep` — flips this. Live trading authorization requires Cycle-17 §A deploy-candidate review (slice-specific risk review + capital allocation per IC §16 Rule 4 + kill-switch plan + operator commit citing replay report), independent and out of scope for Cycle-17D.

---

## §9 — Cross-References

- **Cycle-17C predecessor charter:** `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` (this amendment amends §"Hard constraints" + §"Amendments and structural findings" + §"Sequencing").
- **E2 structural finding:** `docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md` (G1 sweep outcome-blind; production_proxy=12 across all thresholds).
- **E3 verdict:** `docs/governance/2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md` (side-flip reverted; 0 IC §16 slices).
- **Experiment ledger schema:** `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` (updated in separate commit to add corpus_id, corpus_sha256, cohort_breakdown, ic16_slices_4axis, ic16_slices_5axis_diagnostic fields).
- **Criteria-lock template:** `docs/governance/2026-05-10-cycle-17d-criteria-lock-template.md` (template for all Cycle-17D E4+ experiments; includes Clause E cohort-drift disqualification).
- **IC §16 authority:** `docs/IMPLEMENTATION_CONTRACT.md` §16 (replayed-EV gate; acceptance criteria cited verbatim).
- **Debt log:** `docs/profit_path_debt_log.md` PROFIT-EDGE-012 (updated with amendment status).
- **Memory references:** `feedback_market_implied_baseline.md` (null model), `feedback_audit_scorer_before_verdict.md` (verdict discipline).

---

**Operator sign-off (if required for this document's commitment):** TBD by operator workflow.
