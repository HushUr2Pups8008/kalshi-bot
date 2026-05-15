# Pre-Cycle-14 Codex / operator queue

**Type:** brief queue note. Two action items must clear before Cycle-14 calibration diagnostic launches; both are quick (~5 min each).
**Drafted:** 2026-05-06 cycle 13.5.
**Authority:** Claude post-Cycle-13 outstanding-questions revisit + operator approval.

## Item 1 (Codex) — Cycle-13 replay re-run with production readiness thresholds

**Why:** Cycle-13's `score_counterfactual_pnl.py` ran with `_readiness_admitted` defaults `confidence ≥ 0.85` + thresholds ±0.40 from 0.50. Production G1 is `confidence ≥ 0.05` (per `tasks/trade_readiness_gate.py:69`). Replay's gate was 17× tighter than production. Result: `would_have_traded` count under-counted; `0 left_on_table_would_have_won` finding may be partly because the harness rejected too many evidence rows.

The 3 actual paper trades had all-wrong-direction regardless, so the headline calibration verdict stands. But the "left on table = 0" claim is weakened.

**Action:** Codex re-runs Cycle-13 replay with production thresholds:

```bash
.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py \
  --dataset logs/edge_replay/cycle13_live/replay_dataset.jsonl \
  --output logs/edge_replay/cycle13_live/counterfactual_scores_production_thresholds.json \
  --readiness-confidence 0.05 \
  --readiness-yes-threshold 0.50 \
  --readiness-no-threshold 0.50
```

(yes/no thresholds 0.50/0.50 = no asymmetric admission requirement; production gate uses signed-edge ≥ min_edge as primary gate, not yes/no probability bracketing.)

**Output:** append a §"Production-thresholds rerun" block to `docs/_archive/governance/edge-replay-cycle13-report.md`. Report:
- moved-to-traded counter-fraction under production thresholds
- left_on_table_would_have_won under production thresholds (vs Cycle-13's 0)
- whether the verdict (`positive_ev_slices = 0`, `no_positive_ev_slice = True`) holds

If verdict still 0 positive-EV slices, note it. If verdict changes (positive-EV slice surfaces under production thresholds), that's a major finding — operator + Claude must re-evaluate Cycle-14 scope.

**Estimated time:** ~5 min (single python invocation + ~20 lines doc append).

## Item 2 (Operator) — Capacity audit decision: Path 1 or Path 3

**Status quo:** Cycle-13 capacity refresh shows 0.663 reviewable fraction at 80/day budget. Path 3 (re-eval at close-time) failed — trend went up not down.

**Operator decision required by close-day (2026-05-08T19:01Z target under §8.5.1):**

- **Path 1:** raise daily review budget to ≥ 169 (covers Day-6 peak). Operator commits to ~169-decision review on the heaviest day, ~2× cycle-9's 67-decision review effort. Gate 6 PASSES under Path 1; §8.5.1 close criteria met; soak closes 2026-05-08.
- **Path 1-soft:** raise budget to 80 → 100/day (smaller commitment). Reviewable fraction improves but may still fall short of 0.85. Gate 6 likely still fails.
- **Continue to default 14-day floor (2026-05-15):** if neither variant of Path 1 is acceptable. §8.5.1 not invoked; soak runs an additional 7 days (2026-05-08 → 2026-05-15) under default §8.5 criteria. Risk: per-day decision count likely continues climbing; capacity still fails at default close.
- **§8.5.1 amendment (Path 2):** sample-based review redesign per `PROFIT-GOV-004`. NOT viable for THIS soak (mid-soak amendment is process violation per cycle-11 capacity resolution plan). Phase-3 spec work only.

**Recommendation:** Path 1 (full 169/day budget). Operator commits ~1-2 hours of focused review on close-day; gate 6 passes; soak closes per §8.5.1 path; no precedent of mid-soak gate amendment.

**Action:** operator picks a path; updates `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` gate-6 row to lock the choice + budget number.

**Estimated time:** decision is 5 min; execution at fire-time is ~1-2 hours operator review.

## Item 5 (queued informational) — Historical price endpoint gap

**Not a blocker; documenting for future replay refinement.**

Cycle-13's `fetch_historical_prices.py` probed `/markets/{ticker}/trades` and got 404 for sampled tickers. Per-decision-time historical price not available via that endpoint.

**Impact:**
- Cycle-13 replay used settlement-only proxy (counterfactual P&L based on resolved outcome × cost basis = $2.50/contract default).
- Cycle-14 calibration diagnostics: NO impact (operate on dossier_updates / paper_trades / resolved outcomes only; no Kalshi historical price needed).
- Cycle-15+ replay refinements (intra-market-lifetime EV reconstruction) MIGHT need this — at that point, alternative sources to evaluate:
  1. Kalshi may expose price history under a different endpoint name (search `/trade-api/v2/markets/{ticker}/...` for `prices`, `history`, `candlesticks`).
  2. Bot's own `paper_trades.market_snapshot` JSON column may carry per-trade snapshots; aggregate-by-ticker if so.
  3. Backfill from external source if available (Polymarket, etc.).
  4. Build an in-house price-history collector that polls `get_market(ticker)` periodically + persists snapshots.

**Action (Cycle-15+ scope, NOT now):** if/when intra-market EV becomes Cycle-15+ scope, audit the 4 candidates above. For now: documented + deferred.

## What this queue does NOT include

- Wave-1 deploy work — separate track; ships per existing plan 2026-05-08.
- Cycle-14 calibration diagnostic — separate cycle; charter at `2026-05-06-cycle-14-charter-calibration-diagnosis.md` runs after items 1+2 clear.
- IC §16 amendments — none required pre-Cycle-14.
- Memory hygiene — none new to capture.

## Sequencing

```
1. Codex runs Cycle-13 production-thresholds rerun         → ~5 min
2. Operator commits to Path 1 (or alternative)             → 5 min decision
3. Cycle-14 charter executes (Codex 7 deliverables)        → multiple hours
4. Wave-1 deploy day 2026-05-08T19:01Z                    → operator-day
```

Items 1 + 2 should clear today (2026-05-06) or tomorrow morning (2026-05-07) so Cycle-14 audit lands before Wave-1 commit 1.

## Cross-links

- `docs/_archive/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md` — Cycle-14 charter (downstream of this queue)
- `docs/_archive/governance/2026-05-06-cycle-13-replay-harness-code-review.md` — code review surfaced item 1
- `docs/_archive/governance/2026-05-06-gate-6-capacity-resolution-plan.md` — capacity resolution plan (item 2 background)
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` — gate-6 row updated by operator at item-2 decision
- `docs/_archive/governance/edge-replay-cycle13-report.md` — Codex appends production-thresholds block per item 1
- `scripts/edge_replay/fetch_historical_prices.py` + `kalshi/rest_client.py` — item-5 future-refinement candidates
