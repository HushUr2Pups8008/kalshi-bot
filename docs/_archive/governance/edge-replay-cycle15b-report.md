# Cycle-15B replay report

Generated: 2026-05-07

## Verdict

**IC §16 acceptance: FAIL.**

No source x market-family x signal-type slice satisfies:

- `ev_ci_95_lo > 0`
- `trades >= 10`

Cycle-15B fixed the Lane B extraction collapse, and C9 rebuilt the post-fix dossier-update corpus, but C10 does **not** produce a deployable positive-EV slice.

## Inputs

- Post-fix DB: `data/dossier_updates_post_fix.db`
- Resolved markets: `logs/edge_replay/cycle13_live/resolved_markets_full.json`
- Historical prices: `logs/edge_replay/cycle13_live/historical_prices.json`
- Dataset: `logs/edge_replay/cycle15b/replay_dataset.jsonl`
- Scores: `logs/edge_replay/cycle15b/counterfactual_scores.json`

Build command:

```bash
.venv/bin/python scripts/edge_replay/build_replay_dataset.py \
  --markets logs/edge_replay/cycle13_live/resolved_markets_full.json \
  --paper-trades-db /private/tmp/kalshi_cycle15b_no_paper_trades.db \
  --trade-log '/private/tmp/kalshi_cycle15b_no_trade_logs/*.jsonl' \
  --evidence-store-db data/dossier_updates_post_fix.db \
  --historical-prices logs/edge_replay/cycle13_live/historical_prices.json \
  --output logs/edge_replay/cycle15b/replay_dataset.jsonl
```

Score command:

```bash
.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py \
  --dataset logs/edge_replay/cycle15b/replay_dataset.jsonl \
  --readiness-confidence 0.05 \
  --output logs/edge_replay/cycle15b/counterfactual_scores.json
```

## C10 summary

| Metric | Value |
|---|---:|
| Replay rows | 272 |
| Rows with nonzero post-fix model delta | 7 |
| Rows with decision-time executable price | 0 |
| Readiness-admitted rows | 183 |
| Counterfactual trades | 0 |
| Positive-EV slices | 0 |
| IC §16 accepted slices | 0 |

Important caveat: `left_on_table` rows are not executable P&L rows in this C10 run because `market_yes_price` is absent for all post-fix dossier rows. They show readiness admission under the replay gate, not tradable expected value.

## Series distribution

| Series | Rows | Nonzero model-delta rows | Readiness-admitted rows |
|---|---:|---:|---:|
| `KXTRUMPIRAN` | 112 | 1 | 107 |
| `KXMOCTRUMP25` | 70 | 2 | 61 |
| `KXVANCEPAKISTAN` | 49 | 1 | 4 |
| `KXFISAEXTEND` | 17 | 0 | 0 |
| `KXPARDONSTRUMP` | 15 | 2 | 5 |
| `KXTRUMPCHINA` | 4 | 0 | 3 |
| `KXELECTIONEMERGENCY` | 2 | 0 | 0 |
| `KXNEWDEAL` | 2 | 1 | 2 |
| `KXVOTESAVEAMERICA` | 1 | 0 | 1 |

## Interpretation

Cycle-15B passes the extraction-layer synthetic acceptance from C8, but post-fix historical replay still does not meet the deploy evidence bar. This is not a green-light result for Wave-2/3 or live capital.

C10 is also not a clean "negative EV" result, because the replay scorer cannot create executable P&L without decision-time prices. The correct conclusion is narrower:

1. Extraction collapse is repaired on Lane B fixtures.
2. Post-fix corpus replay produces no IC §16-eligible slice.
3. The replay price-data gap remains load-bearing for any future claim about executable P&L.

## Next routing

Per Cycle-16 skeletons, this result routes to the non-acceptance branch: no behavioral deploy, capital posture remains **PAPER-ONLY**, and any next cycle must either solve executable price reconstruction or pursue the post-verdict path Claude selects from the Cycle-16 conditional skeletons.

---

## Claude appendix — verdict consumption + Cycle-16 routing

**Drafted:** 2026-05-07 post-Codex C10 commit `e5cfb8e`.
**Authority:** Cycle-15B charter §"Cycle-15B success criterion" (`docs/_archive/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` ARCHIVED Stream G R15); post-verdict checklist (`docs/_archive/governance/cycle-15b-post-verdict-action-checklist.md` ARCHIVED Stream G R21); Cycle-16 skeletons (`cycle-16-conditional-charter-skeletons.md`).

### Verdict

Per Cycle-15B charter: **`extraction_fixed_but_information_frontier_holds`** (Lane B post-fix ≥6/10 ✓ AND 0 IC §16 slices ✗).

C8 Lane B verification: 8/8 directional + 2/2 NEUTRAL pass. Extraction repair confirmed.

C10 IC §16 acceptance gate: 0 slices with `ev_ci_95_lo > 0` AND `trades ≥ 10`. Gate fails.

### Independent voice — "frontier" qualifier

The charter verdict label `information_frontier_holds` typically implies the bot's source mix doesn't carry decisive signal. **C10 does NOT support that read.** Concur with Codex's narrower conclusion:

- 7/272 rows (~2.6%) have nonzero post-fix model delta — extraction is now emitting signal where cycle-14 measured 1.57%.
- 183/272 rows (~67%) are readiness-admitted under the replay gate.
- **0/272 rows have decision-time executable price.** Scorer cannot compute counterfactual P&L without `market_yes_price` at the decision instant.

The IC §16 failure is **scorer-blocked**, not "negative EV proven." Distinguishing is load-bearing for Cycle-16 routing.

This is the same `/markets/{ticker}/trades` 404 issue surfaced in cycle-13 and noted in cycle-14 charter §"Historical price endpoint gap." Cycle-14 deemed it not a blocker for diagnosis-only Cycle-14. Cycle-15B inherited the gap — it did not create it. For Cycle-15B IC §16 acceptance, the gap IS load-bearing.

### Cycle-16 routing recommendation

Pre-staged Cycle-16 skeletons (§A / §B / §C / §B-extension in `cycle-16-conditional-charter-skeletons.md`) all assume the IC §16 gate can be evaluated. None addresses the case where the harness lacks executable prices.

**Recommended new branch: Cycle-16D — price-reconstruction prerequisite.** Solve per-decision-time price reconstruction BEFORE choosing between §B source onboarding or §C strategic redesign. Otherwise the operator chooses between paths neither of which can produce IC §16 evidence under current harness state.

§D scope (high-level):
1. Diagnose `/markets/{ticker}/trades` 404 (endpoint changed? auth required? historical data not retained?).
2. If endpoint dead: identify alternative price source (Kalshi orderbook snapshots, third-party archives, computed from settlement + volume curve).
3. Backfill `historical_prices.json` with per-decision-time prices for the 24-market replay window.
4. Re-run C10 against unchanged `data/dossier_updates_post_fix.db` (POST_FIX_REBUILT cohort intact per L8 cohort note).
5. Land verdict that EITHER unblocks Cycle-16 §A (positive-EV slice) OR confirms `extraction_fixed_but_information_frontier_holds` with prices verified (then route to §B or §C as originally designed).

§D does NOT touch bot extraction code. Pure replay-harness scope. Estimated 1-2 weeks if endpoint is solvable; 2-4 weeks if alternative price source needed.

Operator picks: §D first OR direct to §B/§C accepting that IC §16 gate cannot be evaluated until §D lands. Claude recommends §D first — without prices, §B source onboarding's "did the new source produce a positive-EV slice?" question is unanswerable.

### What's ruled out

- **`extraction_rebuild_failed`** — C8 8/8 directional + 2/2 NEUTRAL pass conclusively. Sub-fix at `GEOPOLITICAL_SIGNALS` (path b.i / b.ii operator-authorized in `61bf4c1`) succeeded.
- **`extraction_fixed_with_positive_ev_slice`** — 0 IC §16 slices. Wave-2 candidate slice authoring NOT authorized.
- **Sign-error candidate trace sites 1-7** — all confirmed RULED OUT or REPAIRED:
  - Sites 1, 4, 5: ruled out by Lane A pass (cycle-14).
  - Sites 2, 6: ruled out by C2 first-step-collapse rule (cycle-15B).
  - Site 7: REPAIRED by C7 keyword extension.
  - Site 3: REPAIRED by C7 keyword extension.

### What this verdict does NOT settle

- Whether the bot's source mix carries decisive signal at the post-fix extraction layer. C10 cannot answer because prices aren't reconstructable. Cycle-16 §D needed to reach the question.
- Whether LLM path produces additional differentiation beyond keyword path. C9 used keyword-only path per L7.2 finding. Cycle-16+ may revisit.
- Whether Wave-2/3/Branch-D will ever unblock. Capital posture stays PAPER-ONLY until §D + (subsequent §A/§B/§C) deliver a slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.

### Capital posture

PAPER-ONLY. Locked until Cycle-16 §D + replay-validated positive-EV slice. No operator-explicit live-trading flip authorized.
