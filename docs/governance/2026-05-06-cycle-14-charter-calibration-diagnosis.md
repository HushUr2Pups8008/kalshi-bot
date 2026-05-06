# Cycle-14 charter — calibration kill-or-fix diagnosis

**Type:** focused single-deliverable cycle. Diagnosis-only — NO behavioral fixes ship in Cycle-14.
**Drafted:** 2026-05-06 (cycle 14 prep) post-cycle-13 IC §16 Rule 5 trigger.
**Authority:** operator + Codex + Claude alignment 2026-05-06; final direction "execute Cycle-14 as diagnosis-only with locked criteria."
**Owner:** Codex (implementation); Claude (review + diagnosis-doc co-authoring).
**Status:** ACTIVE.

## TL;DR

Cycle-13 replay returned 0 positive-EV slices at 24-market scope. Cycle-13 readiness inventory found 21/24 dossiers stuck at `current_estimate=0.5000`. Cycle-13 paper-trades audit found 3/3 wrong-direction trades (all `model_prob=0.432` NO-leaning, all resolved YES). Combined: bot may be ingesting evidence but failing to convert it into differentiated probabilities, AND when it does move, may move the wrong direction.

**Cycle-14 answers ONE question:** can the model move belief correctly when evidence should obviously move belief?

**Diagnosis-only.** Behavioral fixes are Cycle-15+ scope, replay-gated per IC §16. Sole exception: trivial measurement bug **in the diagnostic tooling itself** (NOT in the bot's calibration code).

## Pre-stated decision criteria (LOCK BEFORE INSPECTING RESULTS)

| metric | threshold | implication if hit |
|---|---|---|
| `movement_rate` (fraction of evidence-row processings where dossier delta > 0.01) | < 10% | calibration inertness confirmed → fix is in extraction or update logic |
| `direction-correctness` when moved (excluding 0.5000/no-direction rows) | < 50% | **sign-error confirmed** → fix is in dossier update math (1-line candidate but still IC §16 + replay-gated, NOT in Cycle-14) |
| `direction-correctness` when moved | ≥ 50% but `EV/P&L of moved > unmoved` AND `movement_rate < 10%` | bot has latent edge but undertrades (volume/sensitivity issue) → different fix scope |
| `LLM-called` cluster vs `non-LLM-called` cluster | LLM-called movement_rate > 10× non-LLM-called | LLM path is the only signal carrier → non-LLM ingestion contributes noise; rebalance OR drop |
| `source_class` cluster of wrong-direction movements | one class disproportionately wrong | source-quality / information-frontier signal |
| Synthetic high-info evidence injection (downstream-of-extraction) moves model materially | yes | dossier update logic works on well-shaped input |
| Synthetic + real-extraction-in-loop moves model materially | no | extraction layer corrupts signal |
| Both synthetic lanes succeed AND production data fails | n/a | information-frontier confirmed → real sources don't carry decisive signal |
| Both synthetic lanes fail | n/a | dossier update model fundamentally broken → fix scope is large (Cycle-15+) |
| Movement_rate ≥ 10% AND direction-correctness ≥ 60% AND sized-bet subset direction-correctness < 50% | n/a | overall calibration OK but SIZED-bet subset has different distribution → Kelly/sizing/G1 admission selects wrong rows |

These criteria are LOCKED. Operator does not change them after inspecting Cycle-14 output. If a result doesn't fit a stated criterion, that's a finding to document, not a reason to redefine the criteria.

## Cycle-14 deliverables (Codex authors)

### 1. Existing-outcome calibration audit script

`scripts/edge_replay/calibration_audit.py` (or sibling under existing `scripts/edge_replay/`):

Inputs: `data/evidence_store.db` + `data/paper_trades.db` + 24 finalized markets (already in `logs/edge_replay/cycle13_live/resolved_markets_full.json`).

Outputs:
- `movement_rate`: count(dossier_updates with |delta| > 0.01) / total
- `direction-correctness when moved`: per moved-dossier_update, did `new_estimate > 0.5` correlate with `resolved_yes=true` (and `< 0.5` with false)?
  - **Denominator EXCLUDES rows where `new_estimate ∈ [0.499, 0.501]`** (no-direction)
  - Reports `excluded_count` separately
- `EV/P&L of moved vs unmoved` decisions (rough proxy: applies `would_have_traded` from existing scorer)
- `Brier score` and `log-loss` against resolution outcomes — supporting only at n=24, with explicit "n=24, CI bands wide; do not use as primary verdict" caveat in output
- `direction-correctness on SIZED-bet subset` separately (the 3 paper trades) — flagged separately so unlucky-sample-vs-systematic can be distinguished from full-corpus result

### 2. Per-source belief movement audit

Extension of `scripts/edge_replay/score_counterfactual_pnl.py` OR sibling script. Per (`source`, `source_class`, `series_ticker`) tuple:

- count of dossier_updates
- mean / max absolute movement
- `llm_called=true` vs `false` split
- direction-correctness for moved rows in this slice (with same denominator exclusion)

Output: ranked table; flags inert sources (`mean_abs_move < 0.005`); flags wrong-direction-clustered sources.

### 3. LLM-vs-no-LLM split

From `dossier_updates.llm_called`:
- movement_rate per branch
- direction-correctness per branch
- per-source LLM-called fraction

Determine whether inertness comes from non-LLM structural path, LLM path, or both.

### 4. Synthetic high-info evidence injection — TWO LANES

**Lane A (downstream of extraction):** construct synthetic `evidence` rows with crystal-clear directional content (e.g., "FISA Section 702 reauthorization signed into law on April 30, 2026" against `KXFISAEXTEND-MAY01`). Insert directly into the dossier update path, BYPASSING the news-classifier / signal-analyzer extraction layer. Verify:
- model exits prior (movement_rate = 100%)
- model moves the correct direction (direction-correctness = 100%)
- magnitude is meaningful (|delta| > 0.05)

**Lane B (real extraction-in-loop):** same synthetic evidence text, fed through the actual `signal_analyzer` extraction pipeline. Assert same 3 conditions.

Discriminator:
- A passes, B passes → extraction + update both work; production-data failure is information-frontier
- A passes, B fails → extraction layer corrupts signal
- A fails → dossier update model fundamentally broken

### 5. Hard paper-mode-lock check (post-Wave-1 deploy)

Verify: after Wave-1 deploy commits land, `config.cfg.live_trading_enabled` (or equivalent) remains `False`. Pre-commit hook OR test that fails CI if Wave-1 deploys flip the flag without explicit operator override + replay-EV-evidence per IC §16.

This is **a separate test, not a diagnostic.** It's a guardrail that Wave-1's OBS-005 unblock doesn't accidentally widen exposure on a broken model.

### 6. ROADMAP update

`docs/ROADMAP.md` Wave-2/3/Branch-D rows: change "HALTED PER IC §16" → "HALTED AND POTENTIALLY OBSOLETE PENDING CYCLE-14 DIAGNOSIS." If Cycle-14 returns "redesign," these rows close entirely.

### 7. Written diagnosis doc

`docs/governance/edge-replay-cycle14-diagnosis.md`. Structured per the locked decision criteria above:

```
movement_rate: X.XX% (n=Y)
direction-correctness when moved: A/B (Z%; excluded N rows in [0.499, 0.501])
moved vs unmoved EV: ...
LLM-called split: ...
synthetic Lane A: pass/fail
synthetic Lane B: pass/fail
sized-bet subset direction-correctness: ...
verdict: [calibration_inert | sign_error | information_frontier | extraction_broken | sample_noise | model_fine]
recommended Cycle-15 scope: <one of: fix calibration math | rebuild extraction | onboard sources | strategic redesign | continue paper-only data collection>
```

NO behavioral fix ships in this doc. Recommendation only. Cycle-15 ships fix per IC §16.

## Out of scope for Cycle-14

- Behavioral fixes to the dossier update model. (Cycle-15+ replay-gated.)
- Behavioral fixes to extraction logic. (Cycle-15+ replay-gated.)
- Source onboarding. (Cycle-15+ replay-gated.)
- More HALT markers, doc cleanup, governance work. (Cycle-12 + cycle-13 covered those.)
- Wave-1 deploy work (independent track; ships 2026-05-08 per existing plan).

## Sole exception to "no fixes in Cycle-14"

Trivial measurement bug **in the diagnostic tooling itself** (e.g., off-by-one in a SQL aggregation, denominator mistake, JSON parse bug). NOT in the bot's calibration code. NOT in the dossier update logic. NOT in extraction. NOT in any prod path.

If a 1-line sign-inversion fix in the dossier update math is identified, it's still a behavioral fix → Cycle-15 + replay-gated.

## Capital posture

PAPER-ONLY. NO LIVE CAPITAL. Wave-2 + Wave-3 remain HALTED. Wave-1 ships as cleanup/observability hygiene only — does NOT claim edge.

## Cycle-14 success criterion

Diagnosis doc produced + signed by both Codex and Claude. Verdict matches one of the pre-stated decision criteria. Cycle-15 scope recommendation derived FROM the verdict, not invented to fit a preferred fix.

## Cross-links

- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — strategic redirect authority
- `docs/governance/edge-replay-pivot-playbook.md` — IC §16 Rule 5 diagnostic playbook (Cycle-14 instantiates it)
- `docs/governance/edge-replay-cycle13-report.md` — replay verdict that triggered Cycle-14
- `docs/governance/2026-05-06-cycle-13-replay-harness-code-review.md` — readiness-threshold finding (relevant to LLM-called split)
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-15+ fixes)
- `docs/EDGE_STATUS.md` — operator-facing dashboard (refreshed at end of Cycle-14)
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-007` (FUTURE) — Cycle-14 calibration diagnostic debt entry
