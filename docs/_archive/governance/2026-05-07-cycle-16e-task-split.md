# Cycle-16E task split — scorer forensics + corrections + D6 re-run

**Type:** 10-task split per agent (Codex + Claude). Mirrors prior cycle prep pattern (cycle-12/13/14/15B/16D).
**Drafted:** 2026-05-07 cycle-16D operator override.
**Authority:** Cycle-16D operator override (`docs/governance/edge-replay-cycle16d-report.md` "Operator override (2026-05-07)" section); PROFIT-EDGE-010 amended scope.
**Tracker:** PROFIT-EDGE-010 (Cycle-16E scorer forensics audit).

## TL;DR

Cycle-16D D6 produced 237 counterfactual trades / 2 wins / 0.84% win rate / -$7.46 P&L. Operator override 2026-05-07 identified three load-bearing scorer concerns that invalidate the operational reading until forensic audit completes:

1. `would_have_traded` does NOT gate on production G1-G6 readiness (only `abs(edge) >= min_edge`).
2. Replay is massively YES-biased (231 YES / 6 NO; 0/231 YES wins).
3. Price-unit cents-vs-dollars consistency unaudited end-to-end.

**Cycle-16E answers ONE question:** with scorer corrections in place (price-unit invariant, readiness gating, dedupe, by-cut breakdown), does the re-run D6 confirm the cycle-16D operational reading OR produce a different verdict?

**Pure replay-harness scope.** Bot extraction code untouched (C7 stays). POST_FIX_REBUILT cohort (`data/dossier_updates_post_fix.db`) intact. No re-ingestion needed.

10 tasks per agent, 20 total. Codex implementation-heavy (E1-E10). Claude governance + review + scaffolding (N1-N10).

## Pre-stated decision criteria (LOCK BEFORE E1)

These criteria are LOCKED. Operator does NOT change them post-hoc. Mirrors cycle-16D charter pattern.

### Price-unit acceptance (E1 + E2 + N4)

A scorer "passes price-unit invariant" iff:
- 100% of `market_yes_price` values in `historical_prices_cycle16d.json` are stored in CENTS (Kalshi convention).
- End-to-end trace fetch (Kalshi REST) → store (JSON + DB) → score (`score_counterfactual_pnl.py`) preserves the unit invariant at every step. Each step's assumed unit is documented in code + JSON schema + test.
- Any step where unit conversion happens (e.g., dollars → cents) is explicit, tested, and annotated in JSON output (e.g., field `_unit: "cents"` or per-row tag).

### `would_have_traded` acceptance (E3 + N5)

A scorer "passes readiness gating" iff:
- `would_have_traded` requires production G1 confidence floor + G6 sample-size floor + cooldown sentinel + same-side-guard, NOT just `abs(edge) >= min_edge`.
- Single source of truth: `tasks/trade_readiness_gate.py` (or equivalent). Scorer ports the production thresholds, does not invent new ones.
- Test fixture: synthetic dossier_update with edge=0.10 + confidence below G1 → scorer returns `would_have_traded=False`. Same fixture with confidence above G1 → `True`.

### Dedupe / episode-gate acceptance (E4 + N5)

A scorer "passes dedupe / episode-gate" iff:
- Per-market trade emission matches production `paper_trader` cooldown semantics (≥X seconds between same-ticker trades; OBS-005 unblock for never-traded; same-side-guard across open positions per CLAUDE.md gotcha "Same-signal guard must query *all* open trades").
- Test: 20 dossier_updates on one ticker within 60 seconds → at most N trades per cooldown window. N matches production.

### By-cut breakdown acceptance (E5 + N3)

Scorer output JSON contains explicit cuts:
- per-side (YES / NO)
- per-series (KXFISAEXTEND, KXTRUMPIRAN, etc.)
- per-price-bucket (`<5¢`, `5-25¢`, `25-50¢`, `50-75¢`, `75-95¢`, `>95¢`)
- per-admission-reason (G1-floor / G6-floor / cooldown / dedupe / same-side-guard / passed-all-gates)

Each cut reports: count, win count, win rate, P&L, ev_ci_95_lo.

### D6 re-run verdict (locked outcomes for E10 + N6)

Cycle-16E re-run produces a verdict in one of 4 categories:

| verdict | trigger | Cycle-17 path |
|---|---|---|
| `scorer_fixed_with_positive_ev_slice` | ≥1 IC §16 slice surfaces (`ev_ci_95_lo > 0` AND `trades ≥ 10`) post-correction | Cycle-17A — Wave-2 candidate slice authoring |
| `scorer_fixed_no_signal_confirmed` | 0 IC §16 slices AND win rate normalizes (between ~30% and ~70% on corrected sample) AND no extreme YES-bias | Cycle-17 §B / §C operator decision returns to the table |
| `scorer_fixed_but_anomalous_persists` | 0 IC §16 slices AND win rate still extreme (<20% or >80%) OR persistent YES/NO bias post-correction | Cycle-16F additional forensics: extraction-overfit audit + LLM path audit + per-source pathology trace; do NOT route to §B/§C |
| `scorer_corrections_incomplete` | Charter checks not all satisfied (E1-E5 has unresolved unit / gating / dedupe / breakdown gaps) | Cycle-16E-extension to complete corrections; do NOT re-run D6 |

Operator does NOT change these criteria post-hoc.

## Codex 10 tasks (implementation)

| # | Task | Output | Acceptance |
|---|---|---|---|
| E1 | **Price unit forensics.** Audit `historical_prices_cycle16d.json` per-endpoint provenance. Sample N=10 rows from each endpoint (`/markets/trades`, `/historical/trades`); cross-reference Kalshi API docs for canonical units. Confirm all stored values are in cents. Document the unit invariant. | `logs/edge_replay/cycle16e/price_unit_forensics.json` per-endpoint sample + classification. | Per-endpoint unit reported; canonical unit (cents) confirmed OR mismatch flagged. |
| E2 | **End-to-end price-unit trace.** Add explicit unit annotations to: fetch (`fetch_historical_prices.py`), store (JSON output schema), DB load (`build_replay_dataset.py`), score (`score_counterfactual_pnl.py`). At each step, verify the value matches the canonical unit. Add a test that fails if any step's unit annotation drifts. | Code annotations + test `tests/test_edge_replay_price_unit_invariant.py`. | Test passes; ALL pipeline steps annotate unit explicitly. |
| E3 | **`would_have_traded` semantics fix.** Modify `score_candidate` in `score_counterfactual_pnl.py` to gate on production G1-G6 readiness. Reference `tasks/trade_readiness_gate.py` (or current canonical readiness gate) for thresholds. Land behind a `--strict-readiness` flag (default ON for Cycle-16E re-run; legacy `abs(edge)`-only mode preserved for ablation comparison). | Code change + test fixture covering G1-floor / G6-floor pass + fail cases. | Test fixture: edge=0.10 + confidence below G1 → `would_have_traded=False`; same edge + confidence above G1 → `True`. |
| E4 | **Production-runtime dedupe / episode-gate replay.** Investigate `paper_trader` cooldown logic + OBS-005 sentinel + same-side-guard (per CLAUDE.md). Replicate semantics in scorer: when N evidence rows arrive for the same market in a cooldown window, emit at most M trades. M matches production. | Code change in scorer + test fixture: 20 same-ticker dossier_updates in 60 seconds → ≤N trades. | Production cooldown semantics replicated; test passes. |
| E5 | **Win-rate diagnostic breakdown.** Extend scorer output to include per-side / per-series / per-price-bucket / per-admission-reason cuts. Each cut: count, wins, win_rate, P&L, ev_ci_95_lo. | Extended `counterfactual_scores.json` schema + test that asserts cuts present. | Cuts emitted; test asserts schema. |
| E6 | **D6 re-run with corrected scorer.** Run corrected `score_counterfactual_pnl.py` against unchanged `data/dossier_updates_post_fix.db` + `historical_prices_cycle16d.json`. Emit revised counterfactual_scores.json. | `logs/edge_replay/cycle16e/counterfactual_scores.json` + `replay_dataset.jsonl`. | Re-run produces verdict-eligible output; reproducibility commands documented. |
| E7 | **Pre-correction vs post-correction diff report.** Side-by-side: original cycle-16D D6 (231 YES / 6 NO / 2 wins / 237 trades / 0.84% win rate) vs corrected cycle-16E D6. How many trades drop due to readiness-gate? Due to dedupe? What's the new win rate / by-side breakdown? | `docs/governance/edge-replay-cycle16e-pre-vs-post-correction-diff.md`. | Per-correction-type trade-count delta reported; new win rate by side reported. |
| E8 | **Kalshi orderbook midpoint sanity check.** For 2-3 test tickers from D6 traded rows, compare scorer's chosen `market_yes_price` at decision time against orderbook midpoint at same timestamp. Sanity-check scorer reads correct price field. | `logs/edge_replay/cycle16e/orderbook_midpoint_sanity.json` per-ticker comparison. | Scorer's chosen price within ±5¢ of orderbook midpoint OR discrepancy flagged. |
| E9 | **Scorer regression test suite.** Add tests locking corrected scorer behavior: synthetic prices in cents → expected EV; readiness-gate triggered → fewer trades; dedupe triggered → fewer trades; YES-NO balance preserved on a balanced fixture. | `tests/test_edge_replay_cycle16e_regression.py`. | All regression tests pass; cover the 3 cycle-16D scorer concerns explicitly. |
| E10 | **Cycle-16E replay report.** Per (source × market_family × signal_type) slice table with corrected ev_ci_95_lo + trades + win_rate. IC §16 acceptance check. Verdict against amended charter criteria (4 outcomes). Comparison vs cycle-16D D6 narrative. | `docs/governance/edge-replay-cycle16e-report.md`. | Report names verdict (one of the 4 locked outcomes); reproducibility commands present; pre-vs-post comparison narrated. |

## Claude 10 tasks (governance + review + scaffolding)

| # | Task | Output | Acceptance |
|---|---|---|---|
| N1 | **Cycle-16E charter document.** Mirror cycle-16D charter pattern. Lock the 5 acceptance criteria above. Locked BEFORE E1 runs. | `docs/governance/2026-05-07-cycle-16e-charter-scorer-forensics.md`. | Charter authored; criteria replicated; cross-links to operator-override section + task split. |
| N2 | **Pre-execution criteria-lock verification (post-E1+E2).** Verify Codex E1 + E2 outputs match locked price-unit invariant criterion. If unit drift detected, flag; E3 does NOT proceed until aligned. | `docs/governance/2026-05-07-cycle-16e-pre-execution-criteria-verification.md`. | Verification doc landed; drift flagged or absence confirmed. |
| N3 | **Codex scorer-correction code review (post-E3+E4+E5).** Review for correctness of: G1-G6 admission logic ported from production, cooldown / dedupe semantics matching `paper_trader`, breakdown cut computation, no inadvertent production-runtime modification. Reference `tasks/trade_readiness_gate.py` + `trading/paper_trader.py`. | Code review feedback / PR comment on Codex commit. | Findings filed before E6 re-run consumes corrections. |
| N4 | **Independent read of price-unit forensics output (post-E1).** Read E1 raw probes WITHOUT consulting Codex's classification. Identify unit pattern independently. Cross-check against cycle-16D D1 endpoint diagnosis raw `body_snippet` price strings (where `yes_price_dollars` and `yes_price_cents` semantics are visible). | `docs/governance/2026-05-07-cycle-16e-claude-independent-price-unit-read.md` (or section in N2 doc). | Independent classification recorded; matches or differs from Codex with rationale. |
| N5 | **Production-vs-scorer semantics cross-check (post-E3+E4).** Find single source of truth in `tasks/trade_readiness_gate.py` (G1-G6) + `trading/paper_trader.py` (cooldown / OBS-005 / same-side-guard). Verify Codex's port to scorer is faithful. Flag any drift between scorer behavior and production runtime. | Section in N3 doc or standalone `2026-05-07-cycle-16e-production-vs-scorer-semantics.md`. | Per-gate / per-cooldown comparison table; drift flagged. |
| N6 | **Sub-cycle verdict appendix to Cycle-16E report (post-E10).** Mirror cycle-15B L6 + cycle-16D M6 pattern. Independent voice on whether E10 verdict supports / contradicts cycle-16D operational reading. Verdict-vs-criteria check against locked 4 outcomes. Cycle-17 routing recommendation. | Claude appendix in `docs/governance/edge-replay-cycle16e-report.md`. | Appendix landed; matches/disagreement recorded; Cycle-17 routing authored. |
| N7 | **Anti-regression test review (post-E9).** Verify E9 regression tests cover the 3 cycle-16D scorer concerns originally found by operator (readiness gating, YES-bias mechanism, price-unit invariant). If a future cycle reverts a correction, would the test catch it? | Section in N3 doc or standalone `2026-05-07-cycle-16e-anti-regression-test-review.md`. | Per-concern coverage table; gap flagged or absence confirmed. |
| N8 | **Cycle-16E post-verdict action checklist.** Mirror cycle-15b/16d post-verdict pattern. Pre-stage 4 outcome paths + ROADMAP wording per outcome + EDGE_STATUS refresh + debt-log close + file successor. Pre-stage BEFORE E10 lands. | `docs/governance/cycle-16e-post-verdict-action-checklist.md`. | Checklist landed pre-E10; verdict-to-wording maps cover the 4 outcomes. |
| N9 | **Conditional Cycle-17 skeleton refresh.** Update `cycle-17-conditional-charter-skeletons.md` to reflect Cycle-16E gating. Add §F = "Cycle-16F additional forensics" if Cycle-16E verdict = `scorer_fixed_but_anomalous_persists`. Update verdict-to-skeleton map authority section. | Edit `docs/_archive/governance/cycle-17-conditional-charter-skeletons.md` + commit reference. | §F added; verdict-to-skeleton map references Cycle-16E re-run output as gate. |
| N10 | **PROFIT-EDGE-010 closure + PROFIT-EDGE-011 file (post-E10 + N6 verdict).** Mirror prior closure pattern. Status: COMPLETE regardless of verdict. PROFIT-EDGE-011 title matches verdict (Cycle-17A / Cycle-17 §B/§C operator decision returned / Cycle-16F additional forensics / Cycle-16E-extension). | Append to `docs/profit_path_debt_log.md`. | Both entries landed in same commit as ROADMAP/EDGE_STATUS refresh. |

## Sequencing

Strict: **N1 + N8 + N9 land BEFORE Codex E1.**
Strict: **N2 + N4 land AFTER E1+E2, BEFORE E3 starts.**
Parallel: N3 + N5 land alongside or after E3-E5; N3 must complete BEFORE E6 re-run.
Parallel: N7 anti-regression review fires AFTER E9, BEFORE E10 report.
Strict: **N6 verdict appendix fires AFTER E10 lands, BEFORE N10 closes PROFIT-EDGE-010.**

## What Cycle-16E does NOT do

- Bot extraction code changes. Cycle-15B C7 keyword-map fix stays in place.
- Re-ingestion of `data/dossier_updates_post_fix.db`. POST_FIX_REBUILT cohort intact per L8 cohort note.
- New keyword-map sub-fixes. Cycle-15B is closed.
- Source onboarding. Cycle-17 §B scope, deferred until Cycle-16E + Cycle-17 routing.
- LLM-path audit. L7.2 deferral; revisit only if Cycle-16E verdict = `scorer_fixed_but_anomalous_persists` AND operator authorizes Cycle-16F.
- Live-trading flag flip. PAPER-ONLY remains locked.
- Wave-1 deploy interference. Wave-1 ships 2026-05-08T19:01Z on independent track.
- Re-fetching prices via Kalshi REST. Existing `historical_prices_cycle16d.json` is the input; Cycle-16E corrects how it is consumed, not the data itself.

## Sole exception to "no fixes outside scorer"

Trivial measurement bug in the scorer's diagnostic output (e.g., off-by-one in a breakdown bucket count, JSON field mistyped) is in scope. Bot-side or production-runtime changes are NOT.

## Capital posture

PAPER-ONLY. NO LIVE CAPITAL. Wave-2 + Wave-3 + Branch-D HALTED until Cycle-16E + post-Cycle-17 cycle delivers ≥1 IC §16-eligible slice under audited scorer. Live-trading flip requires explicit operator override per `tests/test_paper_mode_lock_post_wave1.py`.

## Cycle-16E success criterion

E10 report produced + signed by both Codex and Claude. Verdict matches one of the 4 locked outcomes. Cycle-17 scope routing derived FROM verdict via `cycle-17-conditional-charter-skeletons.md` (refreshed in N9), not invented to fit a preferred path.

## Cross-links

- `docs/governance/edge-replay-cycle16d-report.md` "Operator override (2026-05-07)" — origin of Cycle-16E.
- `docs/_archive/governance/cycle-17-conditional-charter-skeletons.md` — Cycle-17 routing (refreshed in N9).
- `docs/governance/cycle-16d-post-verdict-action-checklist.md` — analogous post-verdict pattern (N8 mirrors).
- `docs/_archive/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` — L8 cohort note (POST_FIX_REBUILT intact) (ARCHIVED Stream G R18).
- `data/dossier_updates_post_fix.db` — POST_FIX_REBUILT cohort (E6 input, unchanged).
- `logs/edge_replay/cycle16d/historical_prices_cycle16d.json` — restored prices (E6 input, unchanged).
- `scripts/edge_replay/score_counterfactual_pnl.py` — scorer subject of E1-E5 corrections.
- `tasks/trade_readiness_gate.py` — production G1-G6 single source of truth (E3 reference).
- `trading/paper_trader.py` — production cooldown / OBS-005 / same-side-guard (E4 reference).
- CLAUDE.md "Same-signal guard must query *all* open trades" — load-bearing for E4 dedupe semantics.
- CLAUDE.md "Bet size is dynamic via `cfg.dynamic_max_bet(notional)`" — relevant to scorer trade sizing if E3 ports sizing logic.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs E10 acceptance).
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-010` — debt entry tracking this cycle (amended scope).
