# Cycle-16D replay report

Generated: 2026-05-07

## Verdict

**IC §16 acceptance: FAIL.**

Verdict label: `extraction_fixed_but_information_frontier_holds`.

No source x market-family x signal-type slice met both locked criteria:

- `ev_ci_95_lo > 0`
- `trades >= 10`

One raw positive-EV scorer group exists, but it has `trades = 1`; it is not an IC §16-accepted slice.

## Inputs

- Post-fix DB: `data/dossier_updates_post_fix.db`
- Resolved markets: `logs/edge_replay/cycle13_live/resolved_markets_full.json`
- Historical prices: `logs/edge_replay/cycle16d/historical_prices_cycle16d.json`
- Dataset: `logs/edge_replay/cycle16d/replay_dataset.jsonl`
- Scores: `logs/edge_replay/cycle16d/counterfactual_scores.json`
- Coverage audit: `logs/edge_replay/cycle16d/coverage_audit.json`

## D6 summary

| Metric | Value |
|---|---:|
| Replay rows | 272 |
| Rows with decision-time executable price | 271 |
| Coverage | 99.63% |
| Counterfactual trades | 237 |
| Counterfactual wins | 2 |
| Counterfactual P&L | -7.4630 |
| Overall ev_ci_95_lo | -0.0382 |
| Raw positive-EV scorer groups | 1 |
| IC §16 accepted slices | 0 |
| Brier score (latest model per market, n=24) | 0.2517 |

Brier is supporting evidence only: n=24 markets gives wide uncertainty. IC §16 routing remains governed by slice EV and trade-count criteria.

## Slice table

| slice | candidates | trades | wins | win_rate | pnl | ev_ci_95_lo |
|---|---:|---:|---:|---:|---:|---:|
| `paper-trade-roundtrip` / `KXTRUMPIRAN` / `SKIPPED` / `unknown` | 1 | 1 | 1 | 1.0000 | 0.0010 | 0.0010 |
| `unknown` / `KXTRUMPIRAN` / `PAPER_RESOLUTION` / `unknown` | 2 | 0 | 0 | n/a | 0 | 0.0000 |
| `NYT > World News` / `KXVANCEPAKISTAN` / `MATCH_DIAGNOSTIC` / `unknown` | 4 | 0 | 0 | n/a | 0 | 0.0000 |
| `NYT > World News` / `KXVANCEPAKISTAN` / `ANALYSIS_REJECTED` / `unknown` | 4 | 0 | 0 | n/a | 0 | 0.0000 |
| `Al Jazeera – Breaking News, World News and Video from Al Jazeera` / `KXVANCEPAKISTAN` / `MATCH_DIAGNOSTIC` / `unknown` | 1 | 0 | 0 | n/a | 0 | 0.0000 |
| `Al Jazeera – Breaking News, World News and Video from Al Jazeera` / `KXVANCEPAKISTAN` / `ANALYSIS_REJECTED` / `unknown` | 1 | 0 | 0 | n/a | 0 | 0.0000 |
| `NYT > World News` / `KXVANCEPAKISTAN` / `SIGNAL_ANALYSIS_DETAIL` / `unknown` | 1 | 0 | 0 | n/a | 0 | 0.0000 |
| `AP News` / `KXFISAEXTEND` / `SIGNAL_ANALYSIS_DETAIL` / `unknown` | 4 | 0 | 0 | n/a | 0 | 0.0000 |

## Coverage and exclusions

Coverage passed at 271/272 rows.

The only missing executable-price row is explicitly excluded from P&L scoring:

| ticker | rows | covered | reason |
|---|---:|---:|---|
| `KXPARDONSTRUMP-26APR-22` | 1 | 0 | `no_price_series_for_ticker=1` |

## D9 POST_FIX_REBUILT sentinel

- Sentinel status: `pass`
- Cohort flag: `post_fix_rebuilt`
- `cycle_15b_c7_deploy_commit`: `2222227`
- `cycle_15b_c7_deploy_ts`: `2026-05-07T00:00:00+00:00`

D6 used `data/dossier_updates_post_fix.db`; pre-fix paper-trade DB and trade-log inputs were pointed at empty `/private/tmp` paths.

## Reproducibility

```bash
bash scripts/edge_replay/run_cycle16d_replay.sh --skip-price-backfill
```

Live price refresh path:

```bash
bash scripts/edge_replay/run_cycle16d_replay.sh --live-price-backfill
```

Equivalent command sequence:

```bash
.venv/bin/python scripts/edge_replay/price_coverage_audit.py \
  --dataset logs/edge_replay/cycle15b/replay_dataset.jsonl \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --post-fix-db data/dossier_updates_post_fix.db \
  --output logs/edge_replay/cycle16d/coverage_audit.json

.venv/bin/python scripts/edge_replay/build_replay_dataset.py \
  --markets logs/edge_replay/cycle13_live/resolved_markets_full.json \
  --paper-trades-db /private/tmp/kalshi_cycle16d_no_paper_trades.db \
  --trade-log '/private/tmp/kalshi_cycle16d_no_trade_logs/*.jsonl' \
  --evidence-store-db data/dossier_updates_post_fix.db \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --output logs/edge_replay/cycle16d/replay_dataset.jsonl

.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py \
  --dataset logs/edge_replay/cycle16d/replay_dataset.jsonl \
  --readiness-confidence 0.05 \
  --output logs/edge_replay/cycle16d/counterfactual_scores.json
```

## Interpretation

Cycle-16D removes the prior scorer-blocked state: prices are now available for 99.6324% of rows. With prices verified, the post-fix replay still has no deployable IC §16 slice. Overall counterfactual P&L is negative, and the only raw positive group is a one-trade artifact below the locked trade-count floor.

Routing: no Wave-2/3/D behavioral deploy. Capital posture remains PAPER-ONLY pending Claude M6/M10 verdict consumption.
