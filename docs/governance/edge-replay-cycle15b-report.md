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
